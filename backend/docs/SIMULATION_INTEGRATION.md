# SENTINEL Simulation Integration Contract

This contract connects a teammate-provided SUMO network to the existing simulation API. Simulation and CLEARPATH are digital-twin-only; no real traffic signal control is implemented.

## Backend Request Mapping

The client supplies a stable `network_id`, an ordered `route_edge_ids` mapping for the selected active SENTINEL route, simulation seed/duration, and optional traffic-flow descriptions. The route-to-edge mapping is explicit because SENTINEL geographic geometry is not a road graph and must never be converted into invented edge IDs.

Configure the server with:

- `SUMO_NETWORK_ID`: the exact network identifier accepted by that backend deployment.
- `SUMO_CONFIG_PATH`: server-local SUMO `.sumocfg`; it is not accepted from request bodies or returned in API responses.
- `SUMO_BINARY`: optional executable override, default `sumo`.

TraCI must be importable from the configured SUMO installation. The `SumoTraCIAdapter` owns connection lifecycle, simulation stepping, vehicle/signal reads, simulated phase writes, metrics reads, and shutdown.

## Traffic Signal Mapping

Each participating `TrafficSignal` row must be enabled and have `signal_metadata` containing:

```json
{
  "sumo_signal_id": "junction-42",
  "edge_id": "edge-42-in",
  "valid_phases": ["0", "1", "2"],
  "initial_phase": "0",
  "preemption_phase": "1",
  "release_phase": "0",
  "maximum_duration_seconds": 20,
  "safe_transitions": {
    "0": ["1"],
    "1": ["0"],
    "2": ["0"]
  }
}
```

Phase values are SUMO phase indices represented as strings for TraCI. `edge_id` must be present in the supplied route mapping. Safety metadata must include both the current-to-preemption and preemption-to-release transitions; missing mappings reject the action rather than guessing.

## Network Inputs

The teammate supplies:

- `.net.xml` and its referenced `.rou.xml`/`.add.xml` assets via a `.sumocfg`.
- Stable SUMO network ID and edge IDs corresponding to route candidates.
- Mission route-to-edge mappings in route order.
- Traffic demand/vehicle-type configuration.
- Signal IDs, corridor edge mapping, phases, safe transitions, and max pre-emption duration.

Keep network assets outside PostgreSQL and avoid committing large generated output. The bundled fixture uses synthetic edge names and phase traces only for development/tests; it is not a representation of a real geography.

## Metrics

The adapter returns measured emergency travel time, time loss, stopped time/count, average speed, route completion, duration, and optional background delay/throughput. Any unsupported metric is `null`. Comparison runs the same request/seed/network twice and reports only deltas available from both runs; no improvement is guaranteed.
