import traci
import os
import time

sumo = os.path.join(
    os.environ["SUMO_HOME"],
    "bin",
    "sumo-gui.exe"
)

config = os.path.join(
    os.getcwd(),
    "osm.sumocfg"
)

print("Starting SUMO...")

traci.start([
    sumo,
    "-c", config,
    "--start"
])

print("SUMO connected!")

for step in range(100):
    traci.simulationStep()

    if step % 10 == 0:
        vehicles = traci.vehicle.getIDList()

        print(
            "Step:",
            step,
            "Time:",
            traci.simulation.getTime(),
            "Vehicles:",
            len(vehicles)
        )

        if vehicles:
            print("Example:", vehicles[:5])

traci.close()

print("Done.")