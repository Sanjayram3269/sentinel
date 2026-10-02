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

The backend currently covers domain persistence, event/telemetry streams, and
deterministic prediction baselines. Trained ML models, routing, optimization,
simulation execution, signal control, CLEARPATH, and workflow orchestration
remain out of scope.