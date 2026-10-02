# SENTINEL Backend

The backend provides the persistence contract for SENTINEL's emergency-response
workflow. It contains versioned missions, incidents, fleet and telemetry,
routes and route candidates, facilities, hazards, plans and approvals, events,
audit records, simulations, and predictions. Future AI, routing, optimization,
SUMO/TraCI, CLEARPATH, and frontend work should consume this domain layer rather
than define competing persistence models.

## Requirements

- Python 3.11 or newer
- PostgreSQL 16 with PostGIS (or Docker Compose from the repository root)
- Redis (or Docker Compose from the repository root)
- Dependencies from `requirements.txt`

## Local setup

From the repository root, create a virtual environment, install dependencies,
and copy the example settings. The activation command below is for PowerShell;
use the environment's platform-specific activation script on other shells.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Start PostgreSQL/PostGIS and Redis from the repository root:

```sh
docker compose up -d postgres redis
```

The development `DATABASE_URL` in `.env.example` is configured for these local
containers. The database role must be permitted to install the PostGIS extension
on first migration.

## Domain And Spatial Data

All 16 domain entities use UUID primary keys, timezone-aware timestamps, and
PostgreSQL JSONB for flexible capability, plan, event, audit, and simulation
payloads. Geographic points and route/hazard shapes use GeoAlchemy2 geometry
columns in SRID 4326, with GiST indexes for spatial lookup. API point coordinates
are expressed as latitude/longitude and converted to PostGIS `POINT` values;
route geometries are `LINESTRING` and hazard extents are `MULTIPOLYGON`.

## Migrations And Seed Data

Run these commands from the `backend` directory after PostgreSQL/PostGIS is
available:

```sh
alembic upgrade head
alembic current
python scripts/seed_dev.py
```

The seed is deterministic and idempotent, and every record is labeled as
development/simulation data. Coordinates and facility names are synthetic and
make no claims about real infrastructure. To validate migration SQL without a
database, run `alembic upgrade head --sql`.

## Run the API

From the `backend` directory with the virtual environment active:

```powershell
python -m uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. Existing `/` and `/health`
endpoints remain available; interactive API documentation is at `/docs`.

## Domain API

The initial JSON endpoints are:

- `POST /api/v1/missions` and `GET /api/v1/missions/{mission_id}`
- `GET /api/v1/missions/{mission_id}/state` for mission, incidents, vehicles,
  active routes, and the latest 20 events
- `POST /api/v1/incidents` and `GET /api/v1/incidents/{incident_id}`
- `POST /api/v1/vehicles` and `GET /api/v1/vehicles/{vehicle_id}`
- `POST` and `GET /api/v1/missions/{mission_id}/events`
- `POST /api/v1/missions/{mission_id}/telemetry`
- `WS /ws/missions/{mission_id}` for the mission's normalized event stream

Creation endpoints return `201`; lookups return `404` for unknown IDs. Point
inputs use `{ "latitude": number, "longitude": number }`.

## Prediction And Intelligence

Task 4 exposes explicit prediction execution and retrieval:

- `POST /api/v1/missions/{mission_id}/predictions` runs one requested baseline
- `GET /api/v1/missions/{mission_id}/predictions` returns bounded history, with
    optional `prediction_type`, `limit` (maximum 200), `before`, and `after`
- `GET /api/v1/missions/{mission_id}/predictions/{prediction_id}` retrieves one
    mission-scoped result

The API accepts `ETA`, `ROUTE_FAILURE`, `CONGESTION`, and `HAZARD_IMPACT`.
Predictors implement a replaceable `Predictor` interface and consume a bounded,
deterministic context built from the existing mission state and PostGIS data.
Execution is explicit; no background or event-triggered prediction loop runs.

All predictors currently use deterministic baseline rules named
`baseline_rule_v1`; they are not trained machine-learning models and make no
accuracy claim. For route-failure and hazard-impact results, `probability` is a
deterministic bounded risk score, not a calibrated statistical probability.
`confidence` instead represents the availability and quality of observed
signals. Every result includes structured factors with descriptions; results
without required data use `status: UNAVAILABLE`, a reason, and `missing_inputs`.

Baseline methods:

- ETA reports an estimate for each active route, using route distance in meters
    divided by current vehicle speed in meters per second, then the route's
    estimated duration as a fallback. It does not choose a route.
- Route-failure risk sums deterministic contributions for missing active routes,
    route risk (`0.4 × risk_score`), recent deviations (`0.15`), closures (`0.2`),
    severe congestion (`0.15`), and hazards within one kilometer
    (`0.2 × severity / 5`); a missing active route contributes `0.45`. The sum is
    capped at 1.0. Risk levels are LOW below 0.25, MODERATE below 0.65, otherwise
    HIGH.
- Congestion uses a recent observed congestion event when available; otherwise
    it compares current vehicle speed with route distance divided by estimated
    duration and reports the median relative-speed level.
- Hazard impact uses PostGIS route-to-hazard distance and stored hazard
    severity. The baseline impact score is severity divided by five, multiplied
    by a linear proximity weight within five kilometers. A missing spatial
    comparison is unavailable, not a low-impact result.

Congestion levels use the route-relative congestion score: HIGH at 0.65 or
above, MODERATE at 0.3 or above, otherwise LOW. An observed congestion event is
used directly when its level is LOW, MODERATE/MEDIUM, or HIGH/SEVERE. ETA reports
one result per active route instead of selecting among routes.

Predictions are persisted before a `PREDICTION_UPDATED` event is sent through
the Task 3 event service and Redis publisher. A publish failure returns a
controlled error; the prediction remains stored. Prediction is kept separate
from route selection, optimization, safety validation, approval, and action.

## Routing And Resilience

Task 5 adds explicit routing candidate evaluation and route monitoring:

- `POST /api/v1/missions/{mission_id}/routes/candidates` scores caller-supplied
    candidate `LINESTRING` geometry and persists a planning cycle
- `GET /api/v1/missions/{mission_id}/routes` lists at most 200 persisted routes;
    optional `vehicle_id`, `role`, and `status` filters are supported
- `GET /api/v1/missions/{mission_id}/routes/{route_id}` retrieves one route
- `GET /api/v1/missions/{mission_id}/routes/resilience` evaluates the latest
    cycle, or accepts `vehicle_id` and `planning_cycle_id`
- `POST /api/v1/missions/{mission_id}/routes/{route_id}/activate` activates a
    viable route and deactivates any previously active route for that vehicle

`RoutingProvider` is the replaceable provider contract. The current
`baseline_development_provider` only normalizes route proposals included in the
request; it creates no road network and makes no external routing calls.
Candidate records link to existing `Route` records for their stored PostGIS
geometry and operational lifecycle. `RouteCandidate` stores planning cycle,
origin/destination, provider, role, and evaluation metrics.

The `baseline_routing_score_v1` formula uses configurable weights, defaulting
to ETA 0.25, distance 0.15, risk 0.15, predicted failure 0.20, congestion
0.10, and hazard exposure 0.15. ETA and distance are min-max normalized across
the supplied candidates; equal values map to neutral 0.5. Available metrics
only are included and their weights are renormalized. `score_coverage` reports
the fraction of configured weight represented by available inputs. Scores are
lower-is-better and are prototype parameters, not scientifically validated
operational weights. Candidate duration is the provider's ETA input. Task 4's
mission-wide predictions are not applied to newly generated candidate routes.
Candidate/provider-supplied values are used when present; otherwise failure,
congestion, and hazard inputs remain unavailable and reduce score coverage.
Existing Task 4 ETA predictions are not reused for new geometries because they
describe different routes. Route monitoring can use a Task 4 prediction only
when its persisted `route_id` exactly matches the monitored route.

Viability defaults are `ROUTE_FAILURE_THRESHOLD=0.70`,
`ROUTE_HAZARD_THRESHOLD=0.80`, and
`ROUTE_DEVIATION_THRESHOLD_METERS=100`. A route above a failure or hazard
threshold or explicitly reported unreachable by a provider is excluded;
failure, aborted, and degraded routes are not activatable. Weights are
configured with `ROUTE_WEIGHT_ETA`, `ROUTE_WEIGHT_DISTANCE`,
`ROUTE_WEIGHT_RISK`, `ROUTE_WEIGHT_FAILURE`, `ROUTE_WEIGHT_CONGESTION`, and
`ROUTE_WEIGHT_HAZARD`. These are development defaults, not validated emergency
dispatch thresholds.

Primary is the lowest-scoring viable candidate. Backup and contingency must
meet `ROUTE_MIN_DIVERSITY` (default 0.30) relative to already selected routes.
`FAILED`, `ABORTED`, and `DEGRADED` routes are excluded from resilience roles;
degraded routes remain available in route history for diagnostics but cannot
be activated.
When provider road-segment IDs exist, diversity uses shared ID count; otherwise
it uses exact shared geometry segments and geodesic segment lengths from the
candidate vertices. This is only a geometry approximation, not proof of
road-network independence. The resilience score combines role coverage
(0.2/0.2/0.1), mean pairwise diversity (0.3), and inverse available failure
exposure (0.2); unknown failure exposure receives a neutral 0.5 contribution.
All three roles and score >= 0.75 are HIGH; a viable primary and backup are at
least MEDIUM; primary-only is LOW; no viable primary is NO_RESILIENCE.

`RouteMonitorService.evaluate_route_health(mission_id, route_id)` is explicit;
there is no background monitor. It checks latest telemetry against the route
using PostGIS geography distances in meters, stored/Task 4 failure probability,
and active hazard exposure. New failure/deviation/backup degradation emits the
matching Task 3 event and `REPLAN_TRIGGERED` with
`GENERATE_NEW_CANDIDATES`; it does not run a replanner. Candidate geometry and
route scoring are prototypes and are **not certified for real emergency
dispatch**.

Migration `0004_route_resilience` refuses downgrade while Task 5 candidate data
exists, rather than silently dropping it. When safe to downgrade, null legacy
candidate risks are restored to the old non-null contract using conservative
`1.0`; new route statuses are mapped to legacy `BLOCKED`/`REJECTED` values.
PostgreSQL enum labels cannot be removed directly, so the Task 5 labels remain
unused after downgrade.

## SUMO Digital Twin And CLEARPATH

Task 6 simulation is explicitly **digital-twin-only**. CLEARPATH signal actions
are applied only through a simulation adapter; this backend does not control
real traffic infrastructure.

The mission-scoped endpoints are:

- `POST /api/v1/missions/{mission_id}/simulations/baseline`
- `POST /api/v1/missions/{mission_id}/simulations/clearpath`
- `POST /api/v1/missions/{mission_id}/simulations/compare`
- `GET /api/v1/missions/{mission_id}/simulations`
- `GET /api/v1/missions/{mission_id}/simulations/{simulation_id}`

The request must provide a network identifier, an explicit ordered route-to-edge
mapping, vehicle/active route IDs, a deterministic seed, and bounded simulation
duration. Geographic route geometry is not converted into road topology. Signal
UUIDs must refer to enabled `TrafficSignal` records whose metadata explicitly
maps `sumo_signal_id`, `edge_id`, valid phases, safe transitions, pre-emption
phase, release phase, and maximum duration.

`SimulationAdapter` is the synchronous boundary. `SumoTraCIAdapter` loads
TraCI only when configured and runs inside a worker thread, never in API route
code. Configure `SUMO_BINARY`, `SUMO_CONFIG_PATH`, and `SUMO_NETWORK_ID` for a
teammate-supplied SUMO network; TraCI must be available from that SUMO
installation. Missing SUMO configuration or executable is reported as a
persisted FAILED simulation. SUMO is optional for the standard test suite.

`FakeSimulationAdapter` runs the bundled
[`development_fixture.json`](simulation/scenarios/development_fixture.json),
which is labeled `DEVELOPMENT / TEST NETWORK ONLY` and is synthetic edge/phase
data, not a map of any real city. It replays a declared trace and *measures* the
result (travel time, stopped time, stops, mean speed, completion); it stores no
per-mode metric table. The fixture models no traffic response to signal control,
so a baseline and a CLEARPATH run of it measure identically and the comparison
correctly reports a 0% change rather than an invented improvement. The fixture
has no configurable background traffic demand; dynamic `traffic_flows` are
rejected for that network. The fake adapter is used only for that named network
and tests; it is not represented as SUMO. Standard tests need no SUMO
installation.

Baseline never calls CLEARPATH. CLEARPATH uses a separate deterministic strategy;
every proposed action passes `ClearPathSafetyGuard`, which validates corridor,
phase, configured safe transition/release, and bounded duration. A corridor
signal is requested at most once per run and is never re-armed. Approved
pre-emption is released when the vehicle passes the signal, when the duration
ends, or at teardown, so a run can never end with a signal still pre-empted.
Comparison uses the same scenario, traffic configuration, and seed for both
modes; deltas come from adapter-returned metrics and may show improvement, no
change, or worse results. Unsupported metrics remain `null`.

Simulation state, configuration, metrics, action outcomes, and errors use the
existing `SimulationRun` JSONB fields. Lifecycle and CLEARPATH updates use the
existing `EventService`/Redis event path. No schema migration is required.

Optional SUMO setup example (run SUMO with a teammate-provided `.sumocfg`):

```powershell
$env:SUMO_BINARY = "sumo"
$env:SUMO_CONFIG_PATH = "D:\\traffic-data\\scenario.sumocfg"
$env:SUMO_NETWORK_ID = "team-network-v1"
```

Run simulation coverage with `pytest tests/integration/test_simulation_api.py`;
those tests use the fake adapter and do not require SUMO. A real SUMO smoke run
lives in `tests/integration/test_sumo_adapter_smoke.py` and is skipped unless
`SENTINEL_SUMO_SMOKE=1` plus `SUMO_BINARY`, `SUMO_CONFIG_PATH`,
`SUMO_NETWORK_ID`, and `SENTINEL_SUMO_SMOKE_EDGES` are set. It never fabricates
a result: an unavailable or unconfigured SUMO installation is reported as a
skip, and a simulator rejection is surfaced as an explicit skip, not a pass.

### Teammate Integration Contract

See [`docs/SIMULATION_INTEGRATION.md`](docs/SIMULATION_INTEGRATION.md) for the
network/edge/signal mapping contract to replace the development fixture without
changing API or service contracts.

## Run tests

From the `backend` directory, run the database-free suite with:

```sh
pytest
python -m pytest
```

Model, schema, route-registration, and offline migration checks do not need a
database. PostGIS persistence, spatial execution, migration application, and
database-backed API tests are in `tests/integration` and are skipped unless
`TEST_DATABASE_URL` points to a dedicated disposable PostGIS database. For
example, set it in the shell before running pytest; the integration tests apply
the Alembic migration to that database. Do not point it at production data.

## Scope

The backend currently covers domain persistence, event/telemetry streams,
deterministic prediction baselines, prototype route resilience, and simulation-
only SUMO/CLEARPATH integration. Trained ML models, real-world traffic signal
control, resource allocation, human approval, autonomous actions, and workflow
orchestration remain out of scope.

> SENTINEL CLEARPATH currently operates only inside the SUMO digital traffic
> twin. It does not directly control real-world traffic infrastructure.