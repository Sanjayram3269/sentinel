import traci
import os

# --------------------------------------------------
# SUMO
# --------------------------------------------------

sumo = os.path.join(
    os.environ["SUMO_HOME"],
    "bin",
    "sumo-gui.exe"
)

config = os.path.join(
    os.getcwd(),
    "osm.sumocfg"
)

traci.start([
    sumo,
    "-c", config,
    "--start"
])

print("SUMO connected!")

# --------------------------------------------------
# TARGET TRAFFIC LIGHT
# --------------------------------------------------

TLS_ID = "cluster_10185134633_11878377533_17327420_2451331597_#4more"

# We want the ambulance to approach this junction
# from road 237186601.
START_EDGE = "237186601"

# Road after the junction
TARGET_EDGE = "1113248659"

# --------------------------------------------------
# FIND ROUTE
# --------------------------------------------------

route = traci.simulation.findRoute(
    START_EDGE,
    TARGET_EDGE
)

print("\nAmbulance route:")
print(route.edges)
print("Route distance:", round(route.length, 2), "meters")
print("Estimated travel time:", round(route.travelTime, 2), "seconds")

traci.route.add(
    "ambulance_route",
    route.edges
)

# --------------------------------------------------
# ADD AMBULANCE
# --------------------------------------------------

traci.vehicle.add(
    "ambulance",
    "ambulance_route",
    typeID="DEFAULT_VEHTYPE",
    depart=0
)

# Make ambulance slightly faster
traci.vehicle.setMaxSpeed(
    "ambulance",
    18
)

print("\nAMBULANCE DEPLOYED")
print("------------------")

# --------------------------------------------------
# CLEARPATH STATE
# --------------------------------------------------

clearpath_active = False
original_phase = None

# Trigger distance
TRIGGER_DISTANCE = 80

# --------------------------------------------------
# SIMULATION
# --------------------------------------------------

for step in range(300):

    traci.simulationStep()

    vehicles = traci.vehicle.getIDList()

    if "ambulance" not in vehicles:
        if step > 20:
            print("\nAMBULANCE COMPLETED ROUTE")
            break
        continue

    # Current ambulance road
    current_edge = traci.vehicle.getRoadID("ambulance")

    # Position
    x, y = traci.vehicle.getPosition("ambulance")

    # Speed
    speed = traci.vehicle.getSpeed("ambulance")

    # Distance to junction
    junction_x, junction_y = traci.junction.getPosition(TLS_ID)

    distance = (
        (x - junction_x) ** 2 +
        (y - junction_y) ** 2
    ) ** 0.5

    # Print every 5 seconds
    if step % 5 == 0:

        print(
            f"Time: {traci.simulation.getTime():.0f}s | "
            f"Road: {current_edge} | "
            f"Speed: {speed:.1f} m/s | "
            f"Distance to junction: {distance:.1f}m"
        )

    # --------------------------------------------------
    # CLEARPATH ACTIVATION
    # --------------------------------------------------

    if (
        not clearpath_active
        and current_edge == START_EDGE
        and distance <= TRIGGER_DISTANCE
    ):

        clearpath_active = True

        original_phase = traci.trafficlight.getPhase(TLS_ID)

        print("\n================================")
        print("CLEARPATH ACTIVATED")
        print("================================")
        print("Emergency vehicle detected.")
        print("Target junction:", TLS_ID)
        print("Distance:", round(distance, 1), "m")
        print("Current signal phase:", original_phase)

        # Change signal to phase 0
        # (one of the green phases from our earlier inspection)
        print("Requesting GREEN priority...")

        traci.trafficlight.setPhase(
            TLS_ID,
            0
        )

        print("SIGNAL PRE-EMPTION ACTIVE")
        print("================================\n")

    # --------------------------------------------------
    # RELEASE CLEARPATH AFTER AMBULANCE PASSES
    # --------------------------------------------------

    if (
        clearpath_active
        and current_edge == TARGET_EDGE
    ):

        print("\n================================")
        print("AMBULANCE PASSED JUNCTION")
        print("================================")

        print("CLEARPATH RELEASED")

        # Return to normal signal operation
        traci.trafficlight.setProgram(
            TLS_ID,
            "0"
        )

        clearpath_active = False

        print("Signal returned to normal operation.")
        print("================================\n")

traci.close()

print("\nSimulation finished.")