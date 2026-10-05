"""Fresh-database and reversible migration checks for Task 5 revision 0004."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def _migration(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _head_revision() -> str:
    """Return the single head revision this tree declares.

    ``upgrade head`` must land on whatever the newest revision is, so the
    expectation is derived instead of pinned to one revision. Pinning it meant
    adding any later migration broke a test that is about migration safety, not
    about which revision happens to be newest.
    """
    completed = _migration("", "heads")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    revisions = [
        line.split()[0] for line in completed.stdout.splitlines() if line.strip()
    ]
    assert len(revisions) == 1, f"expected one head, found {revisions}"
    return revisions[0]


def test_fresh_database_upgrade_downgrade_safety_and_reupgrade() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run fresh database migration tests")

    head_revision = _head_revision()

    source_url = make_url(TEST_DATABASE_URL)
    database_name = f"sentinel_task5_{uuid4().hex[:16]}"
    database_url = source_url.set(database=database_name).render_as_string(
        hide_password=False
    )
    role_name = (source_url.username or "").replace('"', '""')

    async def create_database() -> None:
        connection = await asyncpg.connect(
            user=source_url.username,
            password=source_url.password,
            host=source_url.host,
            port=source_url.port,
            database="postgres",
        )
        try:
            await connection.execute(f'CREATE DATABASE "{database_name}" OWNER "{role_name}"')
        finally:
            await connection.close()

    async def seed_legacy_candidate_and_check_downgrade() -> None:
        connection = await asyncpg.connect(
            user=source_url.username,
            password=source_url.password,
            host=source_url.host,
            port=source_url.port,
            database=database_name,
        )
        mission_id, vehicle_id, candidate_id, route_id = (uuid4() for _ in range(4))
        try:
            await connection.execute(
                "INSERT INTO missions (id, status, priority, objective) "
                "VALUES ($1, 'CREATED', 3, 'migration-test')",
                mission_id,
            )
            await connection.execute(
                "INSERT INTO vehicles "
                "(id, mission_id, vehicle_type, status, call_sign, capability) "
                "VALUES ($1, $2, 'AMBULANCE', 'AVAILABLE', $3, '{}'::jsonb)",
                vehicle_id,
                mission_id,
                f"MIGRATION-{vehicle_id}",
            )
            await connection.execute(
                "INSERT INTO route_candidates "
                "(id, mission_id, vehicle_id, route_rank, estimated_duration_seconds, "
                "distance_meters, risk_score, confidence, backup_viable) "
                "VALUES ($1, $2, $3, 1, 60, 100, 0.2, 0.8, true)",
                candidate_id,
                mission_id,
                vehicle_id,
            )
        finally:
            await connection.close()

        upgrade = _migration(database_url, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

        connection = await asyncpg.connect(
            user=source_url.username,
            password=source_url.password,
            host=source_url.host,
            port=source_url.port,
            database=database_name,
        )
        try:
            await connection.execute(
                "UPDATE route_candidates SET risk_score = NULL WHERE id = $1",
                candidate_id,
            )
            await connection.execute(
                "INSERT INTO routes "
                "(id, mission_id, vehicle_id, status, name, geometry, distance_meters, "
                "estimated_duration_seconds) "
                "VALUES ($1, $2, $3, 'FAILED', 'migration-test', "
                "ST_GeomFromText('LINESTRING(0 0, 1 1)', 4326), 100, 60)",
                route_id,
                mission_id,
                vehicle_id,
            )
        finally:
            await connection.close()

        downgrade = _migration(database_url, "downgrade", "0003_prediction_intelligence")
        assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
        current = _migration(database_url, "current")
        assert current.returncode == 0
        assert "0003_prediction_intelligence" in current.stdout

        connection = await asyncpg.connect(
            user=source_url.username,
            password=source_url.password,
            host=source_url.host,
            port=source_url.port,
            database=database_name,
        )
        try:
            risk_score = await connection.fetchval(
                "SELECT risk_score FROM route_candidates WHERE id = $1", candidate_id
            )
            route_status = await connection.fetchval(
                "SELECT status::text FROM routes WHERE id = $1", route_id
            )
            assert risk_score == 1.0
            assert route_status == "BLOCKED"
        finally:
            await connection.close()

        upgrade = _migration(database_url, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
        connection = await asyncpg.connect(
            user=source_url.username,
            password=source_url.password,
            host=source_url.host,
            port=source_url.port,
            database=database_name,
        )
        try:
            await connection.execute(
                "UPDATE route_candidates SET score = 0.5 WHERE id = $1", candidate_id
            )
        finally:
            await connection.close()

        refused_downgrade = _migration(
            database_url, "downgrade", "0003_prediction_intelligence"
        )
        assert refused_downgrade.returncode != 0
        assert "Cannot downgrade 0004 while Task 5 candidate data exists" in (
            refused_downgrade.stdout + refused_downgrade.stderr
        )
        current = _migration(database_url, "current")
        assert current.returncode == 0
        assert head_revision in current.stdout

        connection = await asyncpg.connect(
            user=source_url.username,
            password=source_url.password,
            host=source_url.host,
            port=source_url.port,
            database=database_name,
        )
        try:
            await connection.execute(
                "UPDATE route_candidates SET score = NULL WHERE id = $1", candidate_id
            )
        finally:
            await connection.close()
        downgrade = _migration(database_url, "downgrade", "0003_prediction_intelligence")
        assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
        current = _migration(database_url, "current")
        assert current.returncode == 0
        assert "0003_prediction_intelligence" in current.stdout
        upgrade = _migration(database_url, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    try:
        asyncio.run(create_database())
        fresh_upgrade = _migration(database_url, "upgrade", "head")
        assert fresh_upgrade.returncode == 0, fresh_upgrade.stdout + fresh_upgrade.stderr
        current = _migration(database_url, "current")
        assert current.returncode == 0
        assert head_revision in current.stdout
        parity = _migration(database_url, "check")
        assert parity.returncode == 0, parity.stdout + parity.stderr

        fresh_downgrade = _migration(
            database_url, "downgrade", "0003_prediction_intelligence"
        )
        assert fresh_downgrade.returncode == 0, (
            fresh_downgrade.stdout + fresh_downgrade.stderr
        )
        current = _migration(database_url, "current")
        assert current.returncode == 0
        assert "0003_prediction_intelligence" in current.stdout
        asyncio.run(seed_legacy_candidate_and_check_downgrade())

        current = _migration(database_url, "current")
        assert current.returncode == 0
        assert head_revision in current.stdout
        parity = _migration(database_url, "check")
        assert parity.returncode == 0, parity.stdout + parity.stderr
    finally:
        async def drop_database() -> None:
            connection = await asyncpg.connect(
                user=source_url.username,
                password=source_url.password,
                host=source_url.host,
                port=source_url.port,
                database="postgres",
            )
            try:
                await connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = $1 AND pid <> pg_backend_pid()",
                    database_name,
                )
                await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
            finally:
                await connection.close()

        asyncio.run(drop_database())
