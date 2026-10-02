# SENTINEL Simulation Integration Contract

This document describes exactly what the Task 6 backend implements today, and
what a teammate must supply to replace the synthetic development fixture with a
real SUMO network. It is written to match the code, not an aspirational design.

## Simulation-Only Boundary

SENTINEL runs simulations against a **digital twin**. There is no code path in
this backend that connects to real traffic signal hardware, and none is planned
here.

- CLEARPATH signal actions are written only through a `SimulationAdapter`.
- `ClearPathSafetyGuard` exists to keep the digital twin internally consistent.
  It is **not** a real-world traffic-signal safety certification and must not be
  described as one.
- API descriptions state that results are simulation-only; server-local
  filesystem paths are never accepted from, or returned to, a client.

## Architecture

```
HTTP API  (app/api/v1/simulations.py)
    ↓
SimulationService      (app/services/simulation/service.py)   persistence + events
    ↓
ScenarioBuilder        (scenario_builder.py)                   explicit mappings only
    ↓
SimulationRunner       (runner.py)                             synchronous loop
    ↓
SimulationAdapter      (adapter.py)                            replaceable contract
    ├── FakeSimulationAdapter   deterministic fixture replay
    └── SumoTraCIAdapter        real SUMO over TraCI
```

CLEARPATH stays a separate strategy layered on top of the same runner:

```
Emergency vehicle state
    ↓
ClearPathStrategy   →  SignalAction (proposal only, never writes a phase)
    ↓
ClearPathSafetyGuard → SafetyVerdict (APPROVED / REJECTED + machine-readable code)
    ↓
SimulationAdapter.set_signal_state()   → SUMO
```

Rules that hold by construction:

- API routes never call TraCI, SUMO, or the runner loop directly.
- The TraCI adapter contains no CLEARPATH policy and no SENTINEL domain logic.
- Future agents and the mission optimizer call `SimulationService`, not TraCI.
- The blocking simulation loop runs in a worker thread via `asyncio.to_thread`,
  so the FastAPI event loop is never blocked.

## The Adapter Contract

`SimulationAdapter` is a `Protocol`; both implementations satisfy it and the
test suite asserts that with `isinstance`. A concrete adapter is selected per
run by `default_adapter_factory` from the scenario's `network_id`.

Lifecycle: `start()` → repeated `step()` → state/metric reads and
`set_signal_state()` → `close()`. `close()` runs in a `finally` block, so a
failed or timed-out run still releases every signal it pre-empted and every
resource the adapter acquired. Teardown failures are logged and never mask the
original simulation error.

Failures are explicit and carry a stable, API-safe `code`:

| Exception                       | `code`                            |
| ------------------------------- | --------------------------------- |
| `SimulationAdapterError`        | `SIMULATOR_FAILURE`               |
| `SimulationUnavailableError`    | `SIMULATOR_UNAVAILABLE`           |
| `SimulationConnectionError`     | `SIMULATOR_CONNECTION_FAILED`     |
| `SimulationVehicleError`        | `SIMULATION_VEHICLE_MISSING`      |
| `SimulationSignalError`         | `SIMULATION_SIGNAL_MISSING`       |
| `SimulationTimeoutError`        | `SIMULATION_TIMEOUT`              |
| `SimulationTerminatedError`     | `SIMULATION_TERMINATED`           |
| `SimulationStepError`           | `SIMULATION_STEP_FAILED`          |
| `SimulationConfigurationError`  | `SIMULATION_CONFIGURATION_INVALID`|

Only `SimulationAdapterError` messages are surfaced to clients; any other
exception is reported as a generic failure with the detail left in server logs.

## Explicit Route → Edge Mapping

SENTINEL route geometry is a `LINESTRING` in WGS84. It is **not** a road graph
and is never converted into simulator edge IDs. Every edge ID must be supplied
explicitly by the caller or come from a named, configured network.

A simulation request supplies:

- `network_id` — must equal `SIMULATION_DEVELOPMENT_NETWORK_ID` or the
  configured `SUMO_NETWORK_ID`. Any other value is rejected with `422`.
- `route_edge_ids` — ordered, duplicate-free, and validated against the named
  network.
- `traffic_signal_ids` — `TrafficSignal` rows owned by the caller's mission.
- `traffic_flows` — optional demand, rejected outright for the dev fixture.
- `seed` and `max_simulation_seconds` — determinism and horizon.

The `ScenarioBuilder` also validates mission existence, vehicle ownership,
route ownership, and that the route is `ACTIVE`. Missing or non-owning records
return `404`/`409`; invalid mappings return `422`.

## Traffic Signal Metadata

Each participating `TrafficSignal` row must be `enabled` and carry
`signal_metadata` with:

```json
{
  "sumo_signal_id": "junction-42",
  "edge_id": "edge-42-in",
  "valid_phases": ["0", "1", "2"],
  "initial_phase": "0",
  "preemption_phase": "1",
  "release_phase": "0",
  "maximum_duration_seconds": 20,
  "safe_transitions": { "0": ["1"], "1": ["0"], "2": ["0"] }
}
```

Phase values are SUMO phase indices carried as strings and converted by the
TraCI adapter. `edge_id` must appear in the supplied route mapping.

`SignalDefinition` rejects metadata that is internally inconsistent: unknown
phases in `safe_transitions`, a `preemption_phase` equal to `release_phase`, an
`initial_phase` with no permitted outgoing transition, or missing metadata
permitting `preemption_phase → release_phase`. Such a request is refused with
`422` and **no** `SimulationRun` row is created.

## CLEARPATH Strategy And Safety Guard

`ClearPathStrategy` proposes an action only when all of these hold:

- the scenario mode is `CLEARPATH`,
- the emergency vehicle exists and has not arrived,
- the signal is configured in the scenario and lies on the route corridor,
- the vehicle is approaching (distance ≤ `SIMULATION_APPROACH_DISTANCE_METERS`),
- the signal has not already been requested in this run.

A corridor signal is requested **at most once per run** and is never re-armed.
This is a deliberate bound: pre-emption can never become an open-ended override.

Every proposal — approved or not — is recorded as a `SignalActionResult` and
validated by `ClearPathSafetyGuard`, which checks, in order: mode, action
well-formedness, signal present in the scenario, corridor membership, known
current phase, valid requested phase, the requested phase being the configured
pre-emption phase, bounded duration, permitted transition, and permitted
release. Rejections are recorded with a machine-readable `reason_code`:

`MODE_NOT_CLEARPATH`, `MALFORMED_ACTION`, `SIGNAL_OUTSIDE_SIMULATION`,
`SIGNAL_OUTSIDE_CORRIDOR`, `CURRENT_PHASE_UNKNOWN`, `INVALID_REQUESTED_PHASE`,
`NO_PREEMPTION_PHASE`, `INTERVENTION_DURATION_EXCEEDED`,
`UNSAFE_PHASE_TRANSITION`, `UNSAFE_RELEASE_TRANSITION`. An approved action gets
`APPROVED_SIMULATION_ONLY`.

An approved pre-emption is released to `release_phase` when the vehicle passes
the signal, when the duration expires, or during teardown — so no run can end
with a signal still pre-empted.

## Metrics

Metrics are values the adapter actually measured. Anything the adapter did not
measure is `null`; nothing is estimated or back-filled.

| Field                                  | Source                                        |
| -------------------------------------- | --------------------------------------------- |
| `emergency_vehicle_travel_time_seconds`| measured arrival time of the emergency vehicle |
| `stopped_time_seconds`                 | measured time below the stopped-speed threshold |
| `number_of_stops`                      | measured moving → stopped transitions          |
| `average_speed_meters_per_second`      | mean of measured per-step speeds               |
| `route_completed`                      | observed arrival                               |
| `simulation_duration_seconds`          | measured simulated horizon                     |
| `total_delay_seconds`                  | SUMO time loss, when TraCI provides it          |
| `background_average_delay_seconds`     | optional; SUMO time loss                        |
| `background_vehicle_throughput`        | optional; newly arrived background vehicles    |

## Comparison

`POST .../simulations/compare` runs the same scenario twice, changing only
`mode`. Network, route, edge mapping, traffic demand, seed, and horizon are
shared. The response reports only deltas derivable from both runs:

- `travel_time_delta_seconds` (CLEARPATH − baseline)
- `travel_time_improvement_percent` (`null` when the baseline is missing or zero)
- `stopped_time_delta_seconds`
- `stops_delta`

CLEARPATH may improve, be neutral, or degrade. The API never assumes an
improvement, and there is no code path that produces a percentage without two
measured values.

## FakeSimulationAdapter And The Development Fixture

[`simulation/scenarios/development_fixture.json`](../simulation/scenarios/development_fixture.json)
is labelled `DEVELOPMENT / TEST NETWORK ONLY; synthetic edges, not real
geography`. It contains two synthetic edges and one synthetic signal. It is not
a Bengaluru digital twin and no claim about a real city may be derived from it.

The adapter replays the declared trace and **measures** the outcome. The fixture
holds no per-mode metric table, so a run cannot report a number it did not
compute. Because the fixture models no response of traffic to signal control, a
baseline and a CLEARPATH run measure identically and the comparison reports a
neutral `0%` result. That is the honest outcome, and it is asserted by the test
suite.

`FakeSimulationAdapter` also supports `fail_on_step`, `metric_overrides`, and
`departure_step` for deterministic failure-injection tests. `metric_overrides`
is a test-only hook; no production code path sets it.

The fixture accepts no `traffic_flows`, and rejects any edge or signal ID it
does not declare.

## SumoTraCIAdapter

Configured entirely through the environment:

- `SUMO_BINARY` (default `sumo`) — resolved with `shutil.which`.
- `SUMO_CONFIG_PATH` — server-local `.sumocfg`, validated to be a readable file.
- `SUMO_NETWORK_ID` — the only network ID accepted for real SUMO runs.

`start()` validates configuration before importing TraCI, starts SUMO with the
scenario seed, records the SUMO version/build as adapter metadata, and mirrors
the already-validated scenario in: it checks every required edge exists, that
the emergency and background vehicle types exist, and that every configured
signal exists. Any mismatch raises an explicit error — the adapter never
silently substitutes an edge, a signal, or a vehicle.

`step()` advances the simulation and records speed samples, stopped time, stop
count, time loss, background delay, and newly arrived background vehicles.
Arrival time is captured when SUMO reports the vehicle in `getArrivedIDList()`.
Optional TraCI values that the installed SUMO does not provide are recorded as
unavailable and logged at debug level rather than swallowed or invented.

`close()` is safe to call twice and never raises.

## Persistence, Events And Statuses

Simulation runs use the existing `simulation_runs` table from migration
`0001_initial_domain`; **Task 6 required no schema migration**. Everything else
lives in the existing JSONB `configuration` and `metrics` columns: mode, network
ID, the full validated scenario, seed, simulator name, adapter metadata,
correlation ID, CLEARPATH action results, and any error code/message.

Statuses are `PENDING → RUNNING → COMPLETED | FAILED`. The `RUNNING` row is
committed *before* any simulation work starts, and every path afterwards reaches
a terminal state, so a failed simulation can never remain `RUNNING`.

Events use the existing `EventService` and Redis bus:

- `SIMULATION_STARTED`
- `CLEARPATH_REQUESTED` (CLEARPATH only)
- `CLEARPATH_UPDATED` (CLEARPATH only)
- `SIMULATION_COMPLETED` or `SIMULATION_FAILED`

Every event carries `mission_id`, `simulation_run_id` in its payload, and the
run's `correlation_id`. If the event bus is unreachable the event is already
durable in the `events` table; the failure is logged and the recorded simulation
result is left unchanged rather than being rewritten.

## Teammate Handoff Checklist

To run against a real network, supply:

- `.net.xml` plus referenced `.rou.xml` / `.add.xml`, wired through a `.sumocfg`
  kept outside PostgreSQL and outside version control if it is generated.
- A stable `SUMO_NETWORK_ID`.
- Ordered `route_edge_ids` for each mission route candidate.
- Signal IDs, corridor `edge_id` per signal, phase indices, safe transitions
  (including pre-emption → release), and a maximum pre-emption duration.
- Traffic demand and background vehicle types, plus the emergency vehicle type
  name to pass as `sumo_vehicle_type_id`.
- The matching server-side `SUMO_BINARY`, `SUMO_CONFIG_PATH`, and
  `SUMO_NETWORK_ID` environment values.

No API, service, runner, or CLEARPATH code needs to change to accept them.

## Verifying

```bash
pytest -q                                              # no SUMO required
alembic check                                          # Task 6 adds no migration
docker compose config
```

The real SUMO smoke test is opt-in and never fabricates output:

```bash
SENTINEL_SUMO_SMOKE=1 \
SUMO_BINARY=sumo \
SUMO_CONFIG_PATH=/abs/path/scenario.sumocfg \
SUMO_NETWORK_ID=team-network-v1 \
SENTINEL_SUMO_SMOKE_EDGES=edge-a,edge-b \
SENTINEL_SUMO_SMOKE_SIGNALS='[...]' \
pytest tests/integration/test_sumo_adapter_smoke.py
```

Without a working SUMO installation this file reports a skip. It is not evidence
of a passing SUMO run, and no result in this repository may be described as one.
