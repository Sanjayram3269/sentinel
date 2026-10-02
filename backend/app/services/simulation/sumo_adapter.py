"""Optional SUMO/TraCI adapter; all blocking calls stay inside the runner thread."""

import shutil
from pathlib import Path
from statistics import mean
from typing import Any

from app.config import Settings, get_settings
from app.schemas.simulation import SimulationMetrics, SimulationScenario
from app.services.simulation.adapter import (
    EmergencyVehicleState,
    SimulationAdapterError,
    SimulationConnectionError,
    SimulationSignalError,
    SimulationUnavailableError,
    SimulationVehicleError,
)


class SumoTraCIAdapter:
    name = "sumo_traci_adapter"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._connection: Any = None
        self._traci: Any = None
        self._scenario: SimulationScenario | None = None
        self._started_at = 0.0
        self._seen_vehicle = False
        self._vehicle_arrived = False
        self._speed_samples: list[float] = []
        self._stopped_seconds = 0
        self._stop_count = 0
        self._was_moving = False
        self._time_loss: float | None = None
        self._background_arrivals = 0
        self._background_delay_samples: list[float] = []
        self._previous_time = 0.0
        self.metadata: dict[str, Any] = {}

    def start(self, scenario: SimulationScenario) -> None:
        config_path = self.settings.sumo_config_path
        if not config_path:
            raise SimulationUnavailableError("SUMO_CONFIG_PATH is not configured")
        if self.settings.sumo_network_id and scenario.network_id != self.settings.sumo_network_id:
            raise SimulationUnavailableError("Scenario network_id is not configured for SUMO")
        if not Path(config_path).is_file():
            raise SimulationUnavailableError("Configured SUMO configuration file is unavailable")
        binary = shutil.which(self.settings.sumo_binary)
        if binary is None:
            raise SimulationUnavailableError("SUMO executable is unavailable")
        try:
            import traci

            self._traci = traci
            label = f"sentinel-{scenario.correlation_id}"
            traci.start(
                [binary, "-c", config_path, "--seed", str(scenario.seed), "--no-step-log", "true"],
                label=label,
            )
            self._connection = traci.getConnection(label)
            self._scenario = scenario
            self._started_at = float(self._connection.simulation.getTime())
            version, build = self._connection.getVersion()
            self.metadata = {"sumo_version": str(version), "sumo_build": str(build)}
            self._previous_time = self._started_at
            self._install_scenario_vehicles(scenario)
        except ImportError as error:
            self.close()
            raise SimulationUnavailableError("TraCI module is unavailable") from error
        except SimulationAdapterError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise SimulationConnectionError("Could not establish TraCI connection") from error

    def _install_scenario_vehicles(self, scenario: SimulationScenario) -> None:
        edge_ids = set(self._connection.edge.getIDList())
        required_edges = set(scenario.route_edge_ids)
        for flow in scenario.traffic_flows:
            required_edges.update(flow.edge_ids)
        missing_edges = required_edges - edge_ids
        if missing_edges:
            raise SimulationUnavailableError(
                "Scenario edge mapping contains IDs absent from the configured SUMO network"
            )

        vehicle_types = set(self._connection.vehicletype.getIDList())
        emergency_type = str(
            scenario.emergency_vehicle_configuration.get(
                "sumo_vehicle_type_id", "emergency"
            )
        )
        if emergency_type not in vehicle_types:
            raise SimulationVehicleError("Configured emergency SUMO vehicle type is missing")
        if any(flow.vehicle_type_id not in vehicle_types for flow in scenario.traffic_flows):
            raise SimulationVehicleError("Configured background SUMO vehicle type is missing")

        configured_signals = {
            signal_id for signal_id in self._connection.trafficlight.getIDList()
        }
        if any(signal.signal_id not in configured_signals for signal in scenario.traffic_signals):
            raise SimulationSignalError("Configured traffic signal is missing from SUMO network")

        emergency_route_id = f"sentinel-route-{scenario.route_id}"
        emergency_route_ids = set(self._connection.route.getIDList())
        if emergency_route_id not in emergency_route_ids:
            self._connection.route.add(emergency_route_id, scenario.route_edge_ids)
        vehicle_ids = set(self._connection.vehicle.getIDList())
        if scenario.emergency_vehicle_id not in vehicle_ids:
            self._connection.vehicle.add(
                scenario.emergency_vehicle_id,
                emergency_route_id,
                typeID=emergency_type,
                depart="0",
            )

        for flow_index, flow in enumerate(scenario.traffic_flows):
            flow_route_id = f"sentinel-flow-route-{scenario.correlation_id}-{flow_index}"
            self._connection.route.add(flow_route_id, flow.edge_ids)
            for vehicle_index in range(flow.vehicle_count):
                depart = vehicle_index * flow.depart_period_seconds
                if depart > scenario.max_simulation_seconds:
                    break
                self._connection.vehicle.add(
                    f"sentinel-flow-{scenario.correlation_id}-{flow_index}-{vehicle_index}",
                    flow_route_id,
                    typeID=flow.vehicle_type_id,
                    depart=str(depart),
                )

    def step(self) -> None:
        if self._connection is None:
            raise SimulationConnectionError("TraCI connection is not active")
        try:
            self._connection.simulationStep()
            time_now = float(self._connection.simulation.getTime())
            elapsed = max(0.0, time_now - self._previous_time)
            self._previous_time = time_now
            if self._scenario is None:
                return
            vehicle_id = self._scenario.emergency_vehicle_id
            vehicle_ids = set(self._connection.vehicle.getIDList())
            if vehicle_id in vehicle_ids:
                self._seen_vehicle = True
                speed = float(self._connection.vehicle.getSpeed(vehicle_id))
                self._speed_samples.append(speed)
                waiting = speed <= 0.1
                if waiting:
                    self._stopped_seconds += elapsed
                    if self._was_moving:
                        self._stop_count += 1
                self._was_moving = not waiting
                try:
                    self._time_loss = float(self._connection.vehicle.getTimeLoss(vehicle_id))
                except Exception:
                    self._time_loss = None
            elif self._seen_vehicle and vehicle_id in set(
                self._connection.simulation.getArrivedIDList()
            ):
                self._vehicle_arrived = True

            for background_id in self._connection.vehicle.getIDList():
                if background_id == vehicle_id:
                    continue
                try:
                    self._background_delay_samples.append(
                        float(self._connection.vehicle.getTimeLoss(background_id))
                    )
                except Exception:
                    continue
            self._background_arrivals += sum(
                1
                for arrived_id in self._connection.simulation.getArrivedIDList()
                if arrived_id != vehicle_id
            )
        except Exception as error:
            raise SimulationConnectionError("TraCI simulation step failed") from error

    def simulation_time_seconds(self) -> int:
        if self._connection is None:
            return 0
        return max(0, int(float(self._connection.simulation.getTime()) - self._started_at))

    def is_finished(self) -> bool:
        if self._connection is None:
            return True
        try:
            return int(self._connection.simulation.getMinExpectedNumber()) <= 0
        except Exception as error:
            raise SimulationConnectionError("Cannot query SUMO completion state") from error

    def get_vehicle_state(self, vehicle_id: str) -> EmergencyVehicleState | None:
        if self._connection is None:
            raise SimulationConnectionError("TraCI connection is not active")
        try:
            if vehicle_id not in set(self._connection.vehicle.getIDList()):
                if self._vehicle_arrived:
                    return EmergencyVehicleState(None, 0.0, True, False)
                if not self._seen_vehicle:
                    return None
                return EmergencyVehicleState(None, None, False, False)
            self._seen_vehicle = True
            road_id = str(self._connection.vehicle.getRoadID(vehicle_id))
            speed = float(self._connection.vehicle.getSpeed(vehicle_id))
            next_tls = self._connection.vehicle.getNextTLS(vehicle_id)
            distances = {str(item[0]): float(item[2]) for item in next_tls}
            return EmergencyVehicleState(
                edge_id=road_id,
                speed_meters_per_second=speed,
                arrived=False,
                waiting=speed <= 0.1,
                distances_to_signals_meters=distances,
            )
        except Exception as error:
            raise SimulationVehicleError("Cannot read emergency vehicle state") from error

    def get_signal_state(self, signal_id: str) -> str | None:
        if self._connection is None:
            raise SimulationConnectionError("TraCI connection is not active")
        try:
            signal_ids = set(self._connection.trafficlight.getIDList())
            if signal_id not in signal_ids:
                raise SimulationSignalError("Traffic signal is missing from SUMO network")
            return str(self._connection.trafficlight.getPhase(signal_id))
        except SimulationSignalError:
            raise
        except Exception as error:
            raise SimulationSignalError("Cannot read SUMO signal state") from error

    def set_signal_state(self, signal_id: str, phase: str) -> None:
        if self._connection is None:
            raise SimulationConnectionError("TraCI connection is not active")
        try:
            if signal_id not in set(self._connection.trafficlight.getIDList()):
                raise SimulationSignalError("Traffic signal is missing from SUMO network")
            self._connection.trafficlight.setPhase(signal_id, int(phase))
        except SimulationSignalError:
            raise
        except (TypeError, ValueError) as error:
            raise SimulationSignalError("SUMO phase must be a valid numeric phase index") from error
        except Exception as error:
            raise SimulationSignalError("Cannot set SUMO signal phase") from error

    def get_metrics(self, vehicle_id: str) -> SimulationMetrics:
        if not self._seen_vehicle:
            raise SimulationVehicleError("Emergency vehicle never entered the SUMO simulation")
        return SimulationMetrics(
            emergency_vehicle_travel_time_seconds=(
                float(self.simulation_time_seconds()) if self._vehicle_arrived else None
            ),
            total_delay_seconds=self._time_loss,
            stopped_time_seconds=float(self._stopped_seconds),
            number_of_stops=self._stop_count,
            average_speed_meters_per_second=(
                mean(self._speed_samples) if self._speed_samples else None
            ),
            route_completed=self._vehicle_arrived,
            simulation_duration_seconds=float(self.simulation_time_seconds()),
            background_average_delay_seconds=(
                mean(self._background_delay_samples)
                if self._background_delay_samples
                else None
            ),
            background_vehicle_throughput=self._background_arrivals or None,
        )

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close(wait=False)
            except Exception:
                pass
        elif self._traci is not None:
            try:
                self._traci.close(False)
            except Exception:
                pass