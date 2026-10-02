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

Creation endpoints return `201`; lookups return `404` for unknown IDs. Point
inputs use `{ "latitude": number, "longitude": number }`.

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

This task establishes domain persistence and basic APIs only. Prediction
algorithms, routing, optimization, simulation execution, signal control,
CLEARPATH, and workflow orchestration are intentionally out of scope.