# SENTINEL

SENTINEL is a predictive geoagentic emergency-response digital twin for the
Geoagentic Framework to Support Emergency Movement problem. It is intended to
coordinate emergency missions and resources using live geospatial data,
predictions, and human oversight.

## Current backend responsibility

The current backend is a foundation only: it exposes the FastAPI service and
health check, loads configuration from the environment, and provides
async-compatible PostgreSQL/PostGIS and Redis connection setup. It does not yet
implement emergency-response domain behavior.

## Technology stack

- Python 3.11+
- FastAPI and Uvicorn
- Pydantic v2 and Pydantic Settings
- SQLAlchemy 2.x with asyncpg
- PostgreSQL with PostGIS
- Redis
- pytest and httpx

## Repository structure

```text
sentinel/
├── backend/
│   ├── app/       # API application, settings, database, and service packages
│   ├── tests/     # Backend tests
│   ├── .env.example
│   ├── requirements.txt
│   └── README.md
├── docker-compose.yml
└── README.md
```

## Local setup

Use Python 3.11 or newer. In PowerShell from the repository root:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

The example configuration is for local development. Do not use its database
credentials outside a local development environment.

## Docker setup

From the repository root, start PostgreSQL/PostGIS and Redis:

```powershell
docker compose up -d
docker compose ps
```

PostgreSQL is exposed on port `5432` with persistent data in the
`postgres_data` volume. Redis is exposed on port `6379`.

## Start FastAPI

From the `backend` directory with its virtual environment active:

```powershell
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. Verify `GET /` and
`GET /health`; API documentation is at `/docs`.

## Run tests

From the `backend` directory:

```powershell
pytest
```

The health test runs without PostgreSQL or Redis.

## Current scope and later tasks

This task establishes the backend runtime, configuration, async database
session support, Redis client lifecycle, and basic health endpoint. AI agents,
XGBoost, routing and mission optimization, SUMO/TraCI simulation, CLEARPATH,
voice, and frontend modules will be implemented in later tasks. No domain
models, event bus, or emergency workflows are included yet.