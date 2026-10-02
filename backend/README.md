# SENTINEL Backend

The backend is the service foundation for SENTINEL, a predictive geoagentic
emergency-response system. This task provides the FastAPI application,
environment-driven settings, async PostgreSQL/PostGIS and Redis connectivity,
and a health endpoint. Domain models and response workflows are intentionally
not included yet.

## Requirements

- Python 3.11 or newer
- PostgreSQL with PostGIS (or Docker Compose from the repository root)
- Redis (or Docker Compose from the repository root)

## Local setup

From the repository root, create a virtual environment, install dependencies,
and copy the example settings:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Start PostgreSQL/PostGIS and Redis from the repository root with
`docker compose up -d`. The provided development database settings are ready
for those containers.

## Run the API

From the `backend` directory with the virtual environment active:

```powershell
python -m uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. Check `/` and `/health`.
Interactive API documentation is available at `/docs`.

## Run tests

From the `backend` directory:

```sh
pytest
python -m pytest
```

Both commands are supported from the backend directory. The health endpoint
test does not require live database or Redis services.

## Current scope

This foundation includes application configuration, process-lifetime async
Redis client setup, an async SQLAlchemy session dependency, and a health check.
AI agents, prediction models, routing, optimization, SUMO/TraCI, and CLEARPATH
will be implemented in later tasks.