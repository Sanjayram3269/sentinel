"""Database-free checks for domain model imports and spatial contracts."""

from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import configure_mappers

from app.models import Base


def test_domain_models_import_and_relationships_configure() -> None:
    configure_mappers()

    expected_tables = {
        "missions",
        "incidents",
        "vehicles",
        "vehicle_telemetry",
        "routes",
        "route_candidates",
        "hospitals",
        "shelters",
        "hazards",
        "traffic_signals",
        "mission_plans",
        "plan_approvals",
        "events",
        "audit_logs",
        "simulation_runs",
        "predictions",
        "road_networks",
        "road_edges",
    }
    assert set(Base.metadata.tables) == expected_tables

    for table in Base.metadata.tables.values():
        assert isinstance(table.c.id.type, Uuid)


def test_spatial_columns_use_expected_geometry_and_srid() -> None:
    point_columns = (
        ("incidents", "location"),
        ("vehicles", "current_location"),
        ("vehicle_telemetry", "position"),
        ("hospitals", "location"),
        ("shelters", "location"),
        ("traffic_signals", "location"),
    )
    for table_name, column_name in point_columns:
        spatial_type = Base.metadata.tables[table_name].c[column_name].type
        assert spatial_type.geometry_type == "POINT"
        assert spatial_type.srid == 4326

    route_type = Base.metadata.tables["routes"].c.geometry.type
    assert route_type.geometry_type == "LINESTRING"
    assert route_type.srid == 4326

    hazard_type = Base.metadata.tables["hazards"].c.geometry.type
    assert hazard_type.geometry_type == "MULTIPOLYGON"
    assert hazard_type.srid == 4326

    road_edge_type = Base.metadata.tables["road_edges"].c.geometry.type
    assert road_edge_type.geometry_type == "LINESTRING"
    assert road_edge_type.srid == 4326


def test_domain_timestamps_are_timezone_aware() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone, f"{table.name}.{column.name} is naive"