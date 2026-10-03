import os
import sys
import math
import traci


# ============================================================
# SENTINEL — Simulation Core
# SUMO + TraCI
# ============================================================

SUMO_HOME = os.environ.get("SUMO_HOME")

if not SUMO_HOME:
    raise RuntimeError(
        "SUMO_HOME is not set. Make sure SUMO was installed correctly."
    )

SUMO_BINARY = os.path.join(SUMO_HOME, "bin", "sumo-gui.exe")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUMO_CONFIG = os.path.join(BASE_DIR, "osm.sumocfg")


# ============================================================
# CONFIGURATION
# ============================================================

AMBULANCE_ID = "sentinel_ambulance"
NORMAL_TRAFFIC_SPEED = 3.0
AMBULANCE_SPEED = 6.0
# Wait a little so normal traffic is visible first
AMBULANCE_DEPLOY_TIME = 20

# Trigger incident after ambulance has travelled
INCIDENT_TRIGGER_TIME = 45

# Large artificial travel time = simulated blockage
BLOCKED_EDGE_TRAVEL_TIME = 9999

# CLEARPATH activation distance
CLEARPATH_DISTANCE = 100

# Maximum time CLEARPATH may hold a signal
CLEARPATH_MAX_HOLD = 20


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def edge_distance(edge_id):
    """
    Return the approximate length of an edge.
    """
    try:
        return traci.edge.getLastStepMeanSpeed(edge_id)
    except:
        return 0


def get_edge_length(edge_id):
    """
    Get actual SUMO edge length.
    """
    try:
        lane_id = traci.edge.getLaneNumber(edge_id)

        if lane_id > 0:
            lane = f"{edge_id}_0"
            return traci.lane.getLength(lane)

    except:
        pass

    return 0


def find_long_route():
    """
    Find a long route through the actual OSM network.

    We look at existing vehicles, but only accept routes
    that are sufficiently long for the emergency demo.
    """

    best_route = None
    best_distance = 0

    vehicles = traci.vehicle.getIDList()

    for veh_id in vehicles:

        try:

            route = traci.vehicle.getRoute(veh_id)

            # We need enough edges for a meaningful route
            if len(route) < 8:
                continue

            distance = get_route_distance(route)

            # Ignore very short routes
            if distance < 500:
                continue

            if distance > best_distance:

                best_distance = distance
                best_route = route

        except Exception:
            continue

    return best_route, best_distance

def get_route_distance(route):
    """
    Calculate route distance.
    """

    total = 0

    for edge in route:

        try:
            lanes = traci.edge.getLaneNumber(edge)

            if lanes > 0:
                total += traci.lane.getLength(f"{edge}_0")

        except:
            pass

    return total


def find_signal_for_vehicle(vehicle_id):
    """
    Find the traffic light immediately ahead of the ambulance.
    """

    try:
        lane_id = traci.vehicle.getLaneID(vehicle_id)

        links = traci.lane.getLinks(lane_id)

        for link in links:

            next_lane = link[0]

            if next_lane.startswith(":"):
                continue

            tls_list = traci.trafficlight.getIDList()

            for tls_id in tls_list:

                controlled_links = traci.trafficlight.getControlledLinks(
                    tls_id
                )

                for index, group in enumerate(controlled_links):

                    for connection in group:

                        if not connection:
                            continue

                        incoming_lane = connection[0]
                        outgoing_lane = connection[1]

                        if incoming_lane == lane_id:

                            return tls_id

    except:
        pass

    return None


def find_green_phase(tls_id, vehicle_id):
    """
    Determine which signal phase gives green to the
    ambulance's actual movement.

    This is better than permanently assuming phase 0.
    """

    try:

        lane_id = traci.vehicle.getLaneID(vehicle_id)

        vehicle_route = traci.vehicle.getRoute(vehicle_id)
        route_index = traci.vehicle.getRouteIndex(vehicle_id)

        if route_index >= len(vehicle_route) - 1:
            return None

        current_edge = vehicle_route[route_index]
        next_edge = vehicle_route[route_index + 1]

        controlled_links = traci.trafficlight.getControlledLinks(
            tls_id
        )

        program = traci.trafficlight.getAllProgramLogics(tls_id)

        if not program:
            return None

        logic = program[0]

        for phase_index, phase in enumerate(logic.phases):

            state = phase.state

            for link_index, group in enumerate(controlled_links):

                if link_index >= len(state):
                    continue

                signal = state[link_index]

                if signal not in ("G", "g"):
                    continue

                for connection in group:

                    if not connection:
                        continue

                    incoming_lane = connection[0]
                    outgoing_lane = connection[1]

                    if incoming_lane != lane_id:
                        continue

                    if not outgoing_lane:
                        continue

                    outgoing_edge = outgoing_lane.split("_")[0]

                    if outgoing_edge == next_edge:

                        return phase_index

    except Exception as e:
        print("Phase detection error:", e)

    return None


def distance_to_next_signal(vehicle_id):
    """
    Estimate distance from ambulance to the next signal.
    """

    try:

        route = traci.vehicle.getRoute(vehicle_id)
        index = traci.vehicle.getRouteIndex(vehicle_id)

        if index < 0:
            return 9999

        current_edge = route[index]

        lane_id = traci.vehicle.getLaneID(vehicle_id)

        lane_length = traci.lane.getLength(lane_id)

        lane_position = traci.vehicle.getLanePosition(vehicle_id)

        remaining = lane_length - lane_position

        # If we're already inside a junction, distance is zero-ish
        if current_edge.startswith(":"):
            return 0

        return remaining

    except:
        return 9999


def activate_clearpath(vehicle_id):
    """
    Give the ambulance signal priority at the next
    relevant traffic light.
    """

    tls_id = find_signal_for_vehicle(vehicle_id)

    if not tls_id:
        return None, None

    phase = find_green_phase(tls_id, vehicle_id)

    if phase is None:
        return tls_id, None

    current_phase = traci.trafficlight.getPhase(tls_id)

    print()
    print("=" * 60)
    print("CLEARPATH ACTIVATED")
    print("=" * 60)
    print("Emergency vehicle:", vehicle_id)
    print("Traffic light:", tls_id)
    print("Current phase:", current_phase)
    print("Required green phase:", phase)
    print("Signal pre-emption ACTIVE")
    print("=" * 60)

    traci.trafficlight.setPhase(tls_id, phase)

    # Hold this phase long enough for ambulance to pass
    traci.trafficlight.setPhaseDuration(
        tls_id,
        CLEARPATH_MAX_HOLD
    )

    return tls_id, phase


def restore_signal(tls_id):
    """
    Return traffic light to its normal SUMO program.
    """

    if not tls_id:
        return

    try:

        traci.trafficlight.setProgram(
            tls_id,
            "0"
        )

        print()
        print("CLEARPATH RELEASED")
        print("Traffic signal returned to normal operation.")
        print()

    except Exception as e:

        print(
            "Could not restore signal:",
            e
        )


# ============================================================
# START SUMO
# ============================================================

print()
print("=" * 60)
print("SENTINEL EMERGENCY RESPONSE SIMULATION")
print("=" * 60)
print()

sumo_cmd = [
    SUMO_BINARY,
    "-c",
    SUMO_CONFIG,
    "--start",
    "--quit-on-end",
]

traci.start(sumo_cmd)

print("SUMO connected.")
print()


# ============================================================
# SIMULATION VARIABLES
# ============================================================

ambulance_deployed = False
incident_created = False
rerouted = False
clearpath_active = False

primary_route = []
backup_route = []

incident_edge = None
blocked_edge = None

active_tls = None

start_time = None
incident_time = None
reroute_time = None
arrival_time = None


# ============================================================
# MAIN SIMULATION LOOP
# ============================================================

for step in range(1500):

    traci.simulationStep()

    sim_time = traci.simulation.getTime()
    # ------------------------------------------------------------
    # SENTINEL TRAFFIC SPEED CONTROL
    # ------------------------------------------------------------

    for veh_id in traci.vehicle.getIDList():

        if veh_id == AMBULANCE_ID:
            continue

        try:
            traci.vehicle.setMaxSpeed(
                veh_id,
                NORMAL_TRAFFIC_SPEED
            )
        except:
            pass


    # --------------------------------------------------------
    # SHOW NORMAL TRAFFIC
    # --------------------------------------------------------

    if step % 20 == 0:

        vehicles = traci.vehicle.getIDList()

        print(
            f"Time: {sim_time:6.1f}s"
            f" | Vehicles: {len(vehicles):3d}"
        )


    # ========================================================
    # FIND A LONG EXISTING ROUTE
    # ========================================================

    if not ambulance_deployed and sim_time >= AMBULANCE_DEPLOY_TIME:

        route, distance = find_long_route()

        if route and len(route) >= 3:

            primary_route = list(route)

            print()
            print("=" * 60)
            print("PRIMARY EMERGENCY ROUTE SELECTED")
            print("=" * 60)

            print("Number of edges:", len(primary_route))
            print(
                "Route distance:",
                round(distance, 1),
                "meters"
            )

            print()
            print("Route:")

            for edge in primary_route:
                print("  →", edge)

            print("=" * 60)


            # ------------------------------------------------
            # ADD AMBULANCE
            # ------------------------------------------------

            route_id = "sentinel_primary_route"

            try:
                traci.route.add(
                    route_id,
                    primary_route
                )
            except:
                pass


            try:

                traci.vehicle.add(
                    AMBULANCE_ID,
                    route_id,
                    typeID="SENTINEL_AMBULANCE",
                    depart=sim_time
                )

                # --------------------------------------------------------
                # MAKE SUMO-GUI FOLLOW THE AMBULANCE
                # --------------------------------------------------------

                try:
                    traci.gui.trackVehicle(
                        "View #0",
                        AMBULANCE_ID
                    )

                    traci.gui.setZoom(
                        "View #0",
                        250
                    )

                except Exception as e:
                    print("Could not configure SUMO camera:", e)

                # --------------------------------------------------------
                # AMBULANCE DEPLOYMENT COMPLETE
                # --------------------------------------------------------

                ambulance_deployed = True
                start_time = sim_time

                print()
                print("=" * 60)
                print("AMBULANCE DEPLOYED")
                print("=" * 60)
                print("Vehicle:", AMBULANCE_ID)
                print("Mission status: EN ROUTE")
                print("SUMO camera: FOLLOWING AMBULANCE")
                print("=" * 60)

            except Exception as e:

                print(
                    "Could not deploy ambulance:",
                    e
                )


    # ========================================================
    # AMBULANCE STATUS
    # ========================================================

    if ambulance_deployed:

        vehicles = traci.vehicle.getIDList()

        if AMBULANCE_ID in vehicles:

            current_edge = traci.vehicle.getRoadID(
                AMBULANCE_ID
            )

            speed = traci.vehicle.getSpeed(
                AMBULANCE_ID
            )

            route_index = traci.vehicle.getRouteIndex(
                AMBULANCE_ID
            )

            remaining_edges = (
                len(traci.vehicle.getRoute(AMBULANCE_ID))
                - route_index
            )

            if step % 10 == 0:

                print(
                    f"AMBULANCE | "
                    f"Time {sim_time:5.1f}s | "
                    f"Road {current_edge} | "
                    f"Speed {speed:4.1f} m/s | "
                    f"Remaining edges {remaining_edges}"
                )


            # =================================================
            # INCIDENT
            # =================================================

            if (
                not incident_created
                and sim_time >= INCIDENT_TRIGGER_TIME
                and len(primary_route) >= 4
            ):

                current_index = traci.vehicle.getRouteIndex(
                    AMBULANCE_ID
                )

                # Choose an edge ahead of ambulance
                incident_index = min(
                    current_index + 2,
                    len(primary_route) - 2
                )

                incident_edge = primary_route[
                    incident_index
                ]

                blocked_edge = incident_edge

                print()
                print("=" * 60)
                print("INCIDENT DETECTED")
                print("=" * 60)
                print("Incident type: ROAD BLOCKAGE")
                print("Blocked edge:", blocked_edge)
                print("Primary route compromised.")
                print("SENTINEL evaluating alternatives...")
                print("=" * 60)


                # ------------------------------------------------
                # Increase the travel time of the blocked edge
                # specifically for the ambulance.
                # ------------------------------------------------

                traci.vehicle.setAdaptedTraveltime(
                    AMBULANCE_ID,
                    blocked_edge,
                    BLOCKED_EDGE_TRAVEL_TIME
                )

                incident_created = True
                incident_time = sim_time


            # =================================================
            # REPLAN
            # =================================================

            if (
                incident_created
                and not rerouted
                and sim_time >= incident_time + 2
            ):

                before_route = list(
                    traci.vehicle.getRoute(
                        AMBULANCE_ID
                    )
                )

                try:

                    traci.vehicle.rerouteTraveltime(
                        AMBULANCE_ID,
                        currentTravelTimes=True
                    )

                    after_route = list(
                        traci.vehicle.getRoute(
                            AMBULANCE_ID
                        )
                    )

                    if after_route != before_route:

                        backup_route = after_route

                        rerouted = True
                        reroute_time = sim_time

                        print()
                        print("=" * 60)
                        print("AUTOMATIC REPLAN COMPLETE")
                        print("=" * 60)

                        print("OLD ROUTE:")
                        print(
                            " → ".join(
                                before_route
                            )
                        )

                        print()
                        print("BACKUP ROUTE:")

                        print(
                            " → ".join(
                                backup_route
                            )
                        )

                        print("=" * 60)

                    else:

                        print(
                            "No alternate route found yet."
                        )

                except Exception as e:

                    print(
                        "Rerouting error:",
                        e
                    )


            # =================================================
            # CLEARPATH
            # =================================================

            distance = distance_to_next_signal(
                AMBULANCE_ID
            )

            if (
                not clearpath_active
                and distance <= CLEARPATH_DISTANCE
            ):

                tls, phase = activate_clearpath(
                    AMBULANCE_ID
                )

                if tls and phase is not None:

                    active_tls = tls
                    clearpath_active = True


            # =================================================
            # RELEASE CLEARPATH AFTER JUNCTION
            # =================================================
            if clearpath_active:

                route_index = traci.vehicle.getRouteIndex(
                    AMBULANCE_ID
                )

                route = traci.vehicle.getRoute(
                    AMBULANCE_ID
                )

                if route_index >= len(route) - 1:

                    restore_signal(
                        active_tls
                    )

                    clearpath_active = False
                    active_tls = None


        else:

            # --------------------------------------------------------
            # AMBULANCE HAS LEFT THE SIMULATION
            # This means the ambulance has completed its route.
            # --------------------------------------------------------

            if (
                ambulance_deployed
                and start_time is not None
                and arrival_time is None
                and sim_time > start_time + 5
            ):

                arrival_time = sim_time

                print()
                print("=" * 60)
                print("AMBULANCE MISSION COMPLETE")
                print("=" * 60)

                print(
                    "Total mission time:",
                    round(
                        arrival_time - start_time,
                        2
                    ),
                    "seconds"
                )

                if incident_time:

                    print(
                        "Incident detected at:",
                        round(
                            incident_time,
                            2
                        ),
                        "seconds"
                    )

                if reroute_time:

                    print(
                        "Replanned at:",
                        round(
                            reroute_time,
                            2
                        ),
                        "seconds"
                    )

                print("=" * 60)
                print()

                break
# ============================================================
# CLEAN SHUTDOWN
# ============================================================

try:
    traci.close()
except:
    pass

print()
print("=" * 60)
print("SENTINEL SIMULATION FINISHED")
print("=" * 60)