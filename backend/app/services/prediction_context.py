"""Deterministic prediction feature extraction from persisted mission state."""

from uuid import UUID

from geoalchemy2 import Geography
from fastapi import HTTPException
from sqlalchemy import and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Event,
    Hazard,
    Mission,
    Route,
    RouteCandidate,
    Vehicle,
    VehicleTelemetry,
)
from app.models.enums import HazardStatus, RouteStatus
from app.models.road import RoadEdge, RoadNetwork
from app.config import get_settings
from app.services.predictors import (
    EventFeature,
    HazardFeature,
    MissionPredictionContext,
    RouteFeature,
    VehicleFeature,
)

MAX_CONTEXT_ROUTES = 200
MAX_CONTEXT_HAZARDS = 200
MAX_RECENT_EVENTS = 20
#: Upper bound on road edges matched to one route, so a pathological geometry
#: cannot make a single prediction unbounded.
MAX_ROUTE_ROAD_EDGES = 400
#: Search radius, in metres, when snapping a route to the nearest road edge.
ROAD_MATCH_TOLERANCE_METERS = 50.0


async def _canonical_road_edge_keys(
    db: AsyncSession, route_ids: list[UUID]
) -> dict[UUID, tuple[str, ...]]:
    """Resolve each route's stored canonical ``RoadEdge.id`` list.

    Routes generated from the road network already know exactly which edges they
    traverse, so re-deriving that geometrically would be slower and less
    accurate. The stored order is the travel order and is preserved. Only the
    canonical id is translated into the facade's ``"u->v"`` graph key; the SUMO
    ``external_id`` is never used here.
    """
    if not route_ids:
        return {}
    rows = (
        await db.execute(
            select(
                RouteCandidate.route_id,
                RouteCandidate.road_segment_ids,
            )
            .where(RouteCandidate.route_id.in_(route_ids))
            .order_by(RouteCandidate.created_at.desc(), RouteCandidate.id.desc())
        )
    ).all()

    by_route: dict[UUID, list[str]] = {}
    for row in rows:
        if row.route_id is None or row.route_id in by_route:
            continue
        ids = [str(value) for value in (row.road_segment_ids or []) if value]
        if ids:
            by_route[row.route_id] = ids
    if not by_route:
        return {}

    edge_rows = (
        await db.execute(
            select(RoadEdge.id, RoadEdge.from_node, RoadEdge.to_node).where(
                RoadEdge.id.in_(
                    [UUID(value) for ids in by_route.values() for value in ids]
                )
            )
        )
    ).all()
    key_by_id = {
        str(edge_id): f"{from_node}->{to_node}"
        for edge_id, from_node, to_node in edge_rows
    }
    resolved: dict[UUID, tuple[str, ...]] = {}
    for route_id, ids in by_route.items():
        keys = tuple(key_by_id[value] for value in ids if value in key_by_id)
        if keys:
            resolved[route_id] = keys[:MAX_ROUTE_ROAD_EDGES]
    return resolved


async def _failed_route_ids(db: AsyncSession, mission_id: UUID) -> set[UUID]:
    """Routes recorded as failed or aborted on this mission."""
    rows = await db.execute(
        select(Route.id).where(
            Route.mission_id == mission_id,
            Route.status.in_([RouteStatus.FAILED, RouteStatus.ABORTED]),
        )
    )
    return {row.id for row in rows.all()}


async def _match_routes_to_road_edges(
    db: AsyncSession, route_ids: list[UUID], network_key: str
) -> dict[UUID, tuple[str, ...]]:
    """Match each route's real WGS84 geometry to ordered road-network edges.

    This is a spatial join, not a coordinate guess: the route geometry is
    compared against stored road geometry in PostGIS and the matching edges are
    ordered by where they sit along the route. A route that shares no road with
    the imported network simply returns no edges, which the AI adapter reports
    as a missing input rather than substituting something else.
    """
    if not route_ids:
        return {}

    # Fraction of the route length at which each road edge is first reached.
    reach = func.ST_LineLocatePoint(
        Route.geometry, func.ST_LineInterpolatePoint(RoadEdge.geometry, 0.5)
    )
    # Treat a near miss as a match: stored routes are a discretised trace and
    # rarely coincide exactly with individual OSM edges.
    matches = func.ST_DWithin(
        cast(Route.geometry, Geography(srid=4326)),
        cast(RoadEdge.geometry, Geography(srid=4326)),
        ROAD_MATCH_TOLERANCE_METERS,
    )
    # Selecting the network id as a scalar subquery keeps Route as the single
    # left-hand FROM; joining RoadNetwork directly would leave two unrelated
    # FROMs for SQLAlchemy to disambiguate.
    network_ids = (
        select(RoadNetwork.id)
        .where(RoadNetwork.network_key == network_key)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(
                Route.id.label("route_id"),
                RoadEdge.from_node,
                RoadEdge.to_node,
                reach.label("reach"),
            )
            .select_from(Route)
            .join(RoadEdge, and_(RoadEdge.network_id == network_ids, matches))
            .where(Route.id.in_(route_ids))
            .order_by(Route.id, reach, RoadEdge.external_id)
        )
    ).all()

    grouped: dict[UUID, list[tuple[float, str]]] = {}
    for row in rows:
        bucket = grouped.setdefault(row.route_id, [])
        if len(bucket) >= MAX_ROUTE_ROAD_EDGES:
            continue
        bucket.append((float(row.reach or 0.0), f"{row.from_node}->{row.to_node}"))

    return {
        route_id: tuple(edge_id for _reach, edge_id in bucket)
        for route_id, bucket in grouped.items()
    }


async def _nearest_road_node(
    db: AsyncSession, hazard_id: UUID, network_key: str
) -> str | None:
    """Nearest road-graph node to a hazard's real geometry."""
    row = (
        await db.execute(
            select(RoadEdge.from_node)
            .join(RoadNetwork, RoadNetwork.network_key == network_key)
            .where(
                RoadEdge.network_id == RoadNetwork.id,
                func.ST_DWithin(
                    cast(Hazard.geometry, Geography(srid=4326)),
                    cast(RoadEdge.geometry, Geography(srid=4326)),
                    ROAD_MATCH_TOLERANCE_METERS,
                ),
            )
            .order_by(RoadEdge.external_id)
            .limit(1)
        )
    ).first()
    return row[0] if row is not None else None


async def build_prediction_context(
    db: AsyncSession, mission_id: UUID
) -> MissionPredictionContext:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    route_rows = (
        await db.execute(
            select(
                Route.id,
                Route.vehicle_id,
                Route.distance_meters,
                Route.estimated_duration_seconds,
                Route.risk_score,
            )
            .where(
                Route.mission_id == mission_id,
                Route.status == RouteStatus.ACTIVE,
            )
            .order_by(Route.created_at.desc(), Route.id.desc())
            .limit(MAX_CONTEXT_ROUTES)
        )
    ).all()

    network_key = get_settings().road_network_key
    route_ids = [row.id for row in route_rows]
    # Canonical ids first; the geometric join is only the fallback for routes
    # that were not generated from the network.
    road_edges_by_route = await _canonical_road_edge_keys(db, route_ids)
    unmatched = [route_id for route_id in route_ids if route_id not in road_edges_by_route]
    road_edges_by_route.update(
        await _match_routes_to_road_edges(db, unmatched, network_key)
    )
    closed_ids = await _failed_route_ids(db, mission_id)
    routes = tuple(
        RouteFeature(
            id=row.id,
            vehicle_id=row.vehicle_id,
            distance_meters=row.distance_meters,
            estimated_duration_seconds=row.estimated_duration_seconds,
            risk_score=row.risk_score,
            road_edge_ids=road_edges_by_route.get(row.id, ()),
            closed=row.id in closed_ids,
        )
        for row in route_rows
    )

    latest_speed = (
        select(VehicleTelemetry.speed)
        .where(VehicleTelemetry.vehicle_id == Vehicle.id)
        .order_by(VehicleTelemetry.observed_at.desc(), VehicleTelemetry.id.desc())
        .limit(1)
        .correlate(Vehicle)
        .scalar_subquery()
    )
    vehicle_rows = (
        await db.execute(
            select(Vehicle.id, func.coalesce(latest_speed, Vehicle.speed).label("speed"))
            .where(Vehicle.mission_id == mission_id)
            .order_by(Vehicle.id)
            .limit(MAX_CONTEXT_ROUTES)
        )
    ).all()
    vehicles = tuple(
        VehicleFeature(id=row.id, speed_meters_per_second=row.speed)
        for row in vehicle_rows
    )
    hazard_rows = list(
        (
            await db.execute(
                select(Hazard.id, Hazard.hazard_type, Hazard.severity)
                .where(
                    Hazard.mission_id == mission_id,
                    Hazard.status == HazardStatus.ACTIVE,
                )
                .order_by(Hazard.start_time.desc(), Hazard.id.desc())
                .limit(MAX_CONTEXT_HAZARDS)
            )
        ).all()
    )
    event_rows = (
        await db.execute(
            select(Event.event_type, Event.payload)
            .where(Event.mission_id == mission_id)
            .order_by(Event.occurred_at.desc(), Event.created_at.desc(), Event.id.desc())
            .limit(MAX_RECENT_EVENTS)
        )
    ).all()
    hazard_distances: dict[UUID, float] = {}
    if hazard_rows and routes:
        hazard_ids = [row.id for row in hazard_rows]
        route_ids = [route.id for route in routes]
        route_geography = cast(Route.geometry, Geography(srid=4326))
        hazard_geography = cast(Hazard.geometry, Geography(srid=4326))
        distance = func.ST_Distance(hazard_geography, route_geography)
        rows = (
            await db.execute(
                select(Hazard.id, func.min(distance).label("distance_meters"))
                .join(
                    Route,
                    Route.mission_id == Hazard.mission_id,
                )
                .where(
                    Hazard.id.in_(hazard_ids),
                    Route.id.in_(route_ids),
                )
                .group_by(Hazard.id)
                .limit(MAX_CONTEXT_HAZARDS)
            )
        ).all()
        hazard_distances = {row.id: float(row.distance_meters) for row in rows}

    hazard_nodes: dict[UUID, str | None] = {}
    if hazard_rows:
        for hazard in hazard_rows:
            hazard_nodes[hazard.id] = await _nearest_road_node(
                db, hazard.id, network_key
            )

    hazards = tuple(
        HazardFeature(
            id=row.id,
            hazard_type=row.hazard_type.value,
            severity=row.severity,
            distance_meters=hazard_distances.get(row.id),
            road_node_id=hazard_nodes.get(row.id),
        )
        for row in hazard_rows
    )
    recent_events = tuple(
        EventFeature(event_type=row.event_type, payload=row.payload)
        for row in event_rows
    )
    hazard_event_seen = any(
        event.event_type in {"HAZARD_UPDATED", "ROAD_CLOSURE", "ACCIDENT_DETECTED"}
        for event in recent_events
    )
    return MissionPredictionContext(
        mission_id=mission_id,
        mission_status=mission.status.value,
        routes=routes,
        vehicles=vehicles,
        hazards=hazards,
        recent_events=recent_events,
        active_hazard_event_seen=hazard_event_seen,
    )