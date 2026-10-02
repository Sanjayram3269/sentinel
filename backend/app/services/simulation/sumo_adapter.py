"""Optional SUMO/TraCI adapter; all blocking calls stay inside the runner thread.

This adapter is the only module that knows SUMO exists. It contains no CLEARPATH
policy and no SENTINEL business logic: it starts a configured SUMO instance,
mirrors an already-validated scenario into it, and reports measured values.

Nothing here is exercised unless the server is configured with a SUMO
installation (``SUMO_BINARY``/``SUMO_CONFIG_PATH``/``SUMO_NETWORK_ID``) and the
``traci`` module is importable. The standard test suite never requires SUMO.
"""

import logging
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

logger = logging.getLogger(__name__)
STOPPED_SPEED_THRESHOLD = 0.1


class SumoTraCIAdapter:
    """Drive a configured SUMO network through the TraCI socket."""

    name = "sumo_traci_adapter"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.metadata: dict[str, Any] = {}
        self._connection: Any = None
        self._traci: Any = None
        self._connection_label: str | None = None
        self._scenario: SimulationScenario | None = None
        self._started_at = 0.0
        self._previous_time = 0.0
        self._seen_vehicle = False
        self._vehicle_arrived = False
        self._arrival_time: float | None = None
        self._speed_samples: list[float] = []
        self._stopped_seconds = 0.0
        self._stop_count = 0
        self._was_moving = False
        self._time_loss: float | None = None
        self._background_delay_samples: list[float] = []
        self._counted_background_arrivals: set[str] = set()
        self._background_arrivals = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self, scenario: SimulationScenario) -> None:
        config_path = self._validate_sumo_configuration(scenario)
        binary = shutil.which(self.settings.sumo_binary)
        if binary is None:
            raise SimulationUnavailableError(
                f"SUMO executable {self.settings.sumo_binary!r} is unavailable"
            )
        try:
            import traci
        except ImportError as error:
            raise SimulationUnavailableError(
                "TraCI module is unavailable; install SUMO's Python tools"
            ) from error

        self._traci = traci
        label = f"sentinel-{scenario.correlation_id}"
        try:
            traci.start(
                [
                    binary,
                    "-c",
                    config_path,
                    "--seed",
                    str(scenario.seed),
                    "--no-step-log",
                    "true",
                ],
                label=label,
            )
            self._connection_label = label
            self._connection = traci.getConnection(label)
            version, build = self._connection.getVersion()
            self.metadata = {
                "sumo_version": str(version),
                "sumo_build": str(build),
                "sumo_binary": binary,
                "sumo_seed": scenario.seed,
            }
            self._started_at = float(self._connection.simulation.getTime())
            self._previous_time = self._started_at
            self._scenario = scenario
            self._install_scenario_vehicles(scenario)
        except SimulationAdapterError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise SimulationConnectionError(
                "Could not establish a TraCI connection to the configured SUMO network"
            ) from error

    def _validate_sumo_configuration(self, scenario: SimulationScenario) -> str:
        config_path = self.settings.sumo_config_path
        if not config_path:
            raise SimulationUnavailableError("SUMO_CONFIG_PATH is not configured")
        if (
            self.settings.sumo_network_id
            and scenario.network_id != self.settings.sumo_network_id
        ):
            raise SimulationUnavailableError(
                "Scenario network_id is not the configured SUMO network"
            )
        if not Path(config_path).is_file():
            raise SimulationUnavailableError(
                "Configured SUMO configuration file is unavailable"
            )
        return config_path

    def _install_scenario_vehicles(self, scenario: SimulationScenario) -> None:
        """Mirror the validated scenario into SUMO, failing loudly on any mismatch."""
        connection = self._connection
        edge_ids = set(connection.edge.getIDList())
        required_edges = set(scenario.route_edge_ids)
        for flow in scenario.traffic_flows:
            required_edges.update(flow.edge_ids)
        if required_edges - edge_ids:
            raise SimulationUnavailableError(
                "Scenario edge mapping contains IDs absent from the configured SUMO network"
            )

        vehicle_types = set(connection.vehicletype.getIDList())
        emergency_type = str(
            scenario.emergency_vehicle_configuration.get(
                "sumo_vehicle_type_id", "emergency"
            )
        )
        if emergency_type not in vehicle_types:
            raise SimulationVehicleError(
                "Configured emergency SUMO vehicle type is missing from the network"
            )
        if any(flow.vehicle_type_id not in vehicle_types for flow in scenario.traffic_flows):
            raise SimulationVehicleError(
                "A configured background SUMO vehicle type is missing from the network"
            )

        signal_ids = set(connection.trafficlight.getIDList())
        if any(signal.signal_id not in signal_ids for signal in scenario.traffic_signals):
            raise SimulationSignalError(
                "A configured traffic signal is missing from the SUMO network"
            )

        emergency_route_id = f"sentinel-route-{scenario.correlation_id}"
        if emergency_route_id not in set(connection.route.getIDList()):
            connection.route.add(emergency_route_id, scenario.route_edge_ids)
        if scenario.emergency_vehicle_id not in set(connection.vehicle.getIDList()):
            connection.vehicle.add(
                scenario.emergency_vehicle_id,
                emergency_route_id,
                typeID=emergency_type,
                depart="0",
            )

        for flow_index, flow in enumerate(scenario.traffic_flows):
            flow_route_id = f"sentinel-flow-route-{scenario.correlation_id}-{flow_index}"
            connection.route.add(flow_route_id, flow.edge_ids)
            for vehicle_index in range(flow.vehicle_count):
                depart = vehicle_index * flow.depart_period_seconds
                if depart > scenario.max_simulation_seconds:
                    break
                connection.vehicle.add(
                    f"sentinel-flow-{scenario.correlation_id}-{flow_index}-{vehicle_index}",
                    flow_route_id,
                    typeID=flow.vehicle_type_id,
                    depart=str(depart),
                )

    def close(self) -> None:
        """Release the TraCI connection and any SUMO process it started."""
        connection, self._connection = self._connection, None
        self._scenario = None
        if connection is not None:
            try:
                connection.close(wait=False)
            except Exception:
                logger.exception("TraCI connection did not close cleanly")
            return
        if self._traci is not None and self._connection_label is not None:
            try:
                self._traci.switchConnection(self._connection_label)
                self._traci.close(False)
            except Exception:
                logger.exception("SUMO TraCI session did not shut down cleanly")
        self._traci = None
        self._connection_label = None

    # -- adapter contract --------------------------------------------------

    def step(self) -> None:
        connection = self._require_connection()
        if self._scenario is None:
            return
        try:
            connection.simulationStep()
        except Exception as error:
            raise SimulationConnectionError("TraCI simulation step failed") from error

        time_now = float(connection.simulation.getTime())
        elapsed = max(0.0, time_now - self._previous_time)
        self._previous_time = time_now
        vehicle_id = self._scenario.emergency_vehicle_id
        try:
            vehicle_ids = set(connection.vehicle.getIDList())
        except Exception as error:
            raise SimulationConnectionError("Cannot query SUMO vehicle list") from error

        if vehicle_id in vehicle_ids:
            self._seen_vehicle = True
            try:
                speed = float(connection.vehicle.getSpeed(vehicle_id))
            except Exception as error:
                raise SimulationVehicleError(
                    "Cannot read emergency vehicle speed"
                ) from error
            self._speed_samples.append(speed)
            stopped = speed <= STOPPED_SPEED_THRESHOLD
            if stopped:
                self._stopped_seconds += elapsed
                if self._was_moving:
                    self._stop_count += 1
            self._was_moving = not stopped
            self._time_loss = self._optional_time_loss(connection, vehicle_id)
        elif self._seen_vehicle:
            try:
                arrived_ids = set(connection.simulation.getArrivedIDList())
            except Exception as error:
                raise SimulationConnectionError(
                    "Cannot query SUMO arrival list"
                ) from error
            if vehicle_id in arrived_ids:
                self._vehicle_arrived = True
                if self._arrival_time is None:
                    self._arrival_time = time_now - self._started_at
        self._sample_background(connection, vehicle_id)

    def _sample_background(self, connection: Any, vehicle_id: str) -> None:
        """Collect optional background-traffic metrics where SUMO provides them."""
        try:
            arrived_ids = set(connection.simulation.getArrivedIDList())
        except Exception:
            logger.warning("Background arrival list unavailable; throughput omitted")
            return
        for arrived_id in arrived_ids - self._counted_background_arrivals:
            if arrived_id != vehicle_id:
                self._background_arrivals += 1
        self._counted_background_arrivals |= arrived_ids
        try:
            for background_id in connection.vehicle.getIDList():
                if background_id == vehicle_id:
                    continue
                time_loss = self._optional_time_loss(connection, background_id)
                if time_loss is not None:
                    self._background_delay_samples.append(time_loss)
        except Exception:
            logger.warning("Background time loss unavailable; delay metric omitted")

    @staticmethod
    def _optional_time_loss(connection: Any, vehicle_id: str) -> float | None:
        """Read an optional TraCI value, reporting it as unavailable on failure."""
        try:
            return float(connection.vehicle.getTimeLoss(vehicle_id))
        except Exception:
            logger.debug("TraCI time loss unavailable for %s", vehicle_id)
            return None

    def _require_connection(self) -> Any:
        if self._connection is None:
            raise SimulationConnectionError("TraCI connection is not active")
        return self._connection

    def simulation_time_seconds(self) -> int:
        if self._connection is None:
            return 0
        try:
            now = float(self._connection.simulation.getTime())
        except Exception as error:
            raise SimulationConnectionError("Cannot read SUMO simulation time") from error
        return max(0, int(now - self._started_at))

    def is_finished(self) -> bool:
        connection = self._require_connection()
        try:
            return int(connection.simulation.getMinExpectedNumber()) <= 0
        except Exception as error:
            raise SimulationConnectionError("Cannot query SUMO completion state") from error

    def get_vehicle_state(self, vehicle_id: str) -> EmergencyVehicleState | None:
        connection = self._require_connection()
        if self._scenario is None or vehicle_id != self._scenario.emergency_vehicle_id:
            raise SimulationVehicleError("Emergency vehicle is not configured")
        if self._vehicle_arrived:
            return EmergencyVehicleState(None, 0.0, True, False, {})
        try:
            if vehicle_id not in set(connection.vehicle.getIDList()):
                return None
            self._seen_vehicle = True
            road_id = str(connection.vehicle.getRoadID(vehicle_id))
            speed = float(connection.vehicle.getSpeed(vehicle_id))
            distances = {
                str(item[0]): float(item[2])
                for item in connection.vehicle.getNextTLS(vehicle_id)
            }
        except Exception as error:
            raise SimulationVehicleError(
                "Cannot read emergency vehicle state from SUMO"
            ) from error
        return EmergencyVehicleState(
            edge_id=road_id,
            speed_meters_per_second=speed,
            arrived=False,
            waiting=speed <= STOPPED_SPEED_THRESHOLD,
            distances_to_signals_meters=distances,
        )

    def get_signal_state(self, signal_id: str) -> str | None:
        connection = self._require_connection()
        try:
            if signal_id not in set(connection.trafficlight.getIDList()):
                raise SimulationSignalError("Traffic signal is missing from SUMO network")
            return str(connection.trafficlight.getPhase(signal_id))
        except SimulationSignalError:
            raise
        except Exception as error:
            raise SimulationSignalError("Cannot read SUMO signal phase") from error

    def set_signal_state(self, signal_id: str, phase: str) -> None:
        connection = self._require_connection()
        try:
            if signal_id not in set(connection.trafficlight.getIDList()):
                raise SimulationSignalError("Traffic signal is missing from SUMO network")
        except SimulationSignalError:
            raise
        except Exception as error:
            raise SimulationSignalError("Cannot read SUMO signal list") from error
        try:
            connection.trafficlight.setPhase(signal_id, int(phase))
        except (TypeError, ValueError) as error:
            raise SimulationSignalError(
                "SUMO signal phase must be a numeric phase index"
            ) from error
        except Exception as error:
            raise SimulationSignalError("Cannot set SUMO signal phase") from error

    def get_metrics(self, vehicle_id: str) -> SimulationMetrics:
        """Report only values actually measured during this run."""
        if self._scenario is None or vehicle_id != self._scenario.emergency_vehicle_id:
            raise SimulationVehicleError("Emergency vehicle is not configured")
        if not self._seen_vehicle:
            raise SimulationVehicleError(
                "Emergency vehicle never entered the SUMO simulation"
            )
        return SimulationMetrics(
            emergency_vehicle_travel_time_seconds=self._arrival_time,
            total_delay_seconds=self._time_loss,
            stopped_time_seconds=self._stopped_seconds,
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
            background_vehicle_throughput=(
                self._background_arrivals if self._background_arrivals else None
            ),
        )
