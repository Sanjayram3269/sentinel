"""Static validation for Alembic when PostgreSQL is not available."""

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config


BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_initial_migration_renders_postgresql_schema_offline() -> None:
    output = StringIO()
    config = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in sql
    assert "CREATE TYPE mission_status AS ENUM" in sql
    assert "CREATE TABLE missions" in sql
    assert "CREATE TABLE predictions" in sql
    assert "USING gist (location)" in sql
    assert "geometry(LINESTRING,4326)" in sql