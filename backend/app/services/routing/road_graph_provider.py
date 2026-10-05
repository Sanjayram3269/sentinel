"""Route generation from the imported PostGIS road network.

This provider replaces the caller-supplied-geometry habit. Instead of trusting
arbitrary polylines, it snaps two real WGS84 points onto the ``osm_urban_v1``
graph and searches that graph, so every candidate is a walk over stored
``RoadEdge`` rows.

Three properties are deliberate and are relied on by the resilience engine:

* **Nothing is fabricated.** Candidate geometry is the concatenation of the
  stored ``RoadEdge`` geometries in travel order, taken from the cached graph
  (see :mod:`app.services.road_graph`). No straight line is ever drawn between
  two nodes that were not already joined by a real edge.
* **Identity is explicit.** ``road_segment_ids`` carries canonical
  ``RoadEdge.id`` values, because that is SENTINEL's canonical road identity.
  The SUMO ``external_id`` is a *different* identifier that belongs only to the
  simulation boundary, and the AI layer's ``"u->v"`` node-pair key is a third
  thing again; this module never mixes them.
* **Failures are loud.** A point that cannot be snapped within the configured
  bound, or an origin and destination with no path between them, raises rather
  than silently returning a stretched or invented route.

The graph is loaded through :func:`app.services.ai_road_graph.get_road_graph`,
which caches per network key and network stamp, so routing does not rebuild
NetworkX per request.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import networkx as nx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas.domain import GeoPoint
from app.schemas.predictions import PredictionKind
from app.schemas.routing import RouteProposal
from app.services.route_scoring import PredictionSignals
from app.services.routing.base import RoutingRequest

__all__ = [
    "MAX_ROUTE_GEOMETRY_POINTS",
    "RoadGraphRoutingProvider",
    "RouteSnappingError",
    "RoutingUnavailableError",
    "distance_weight",
    "edge_travel_time_s",
    "snap_point_to_graph_node",
    "travel_time_weight",
]

EARTH_RADIUS_METERS = 6_371_008.8

#: ``RouteProposal.geometry`` is capped at 5000 points by the schema. A route
#: longer than this is truncated rather than silently dropped.
MAX_ROUTE_GEOMETRY_POINTS = 4999


class RouteSnappingError(ValueError):
    """A coordinate has no graph node within the configured search bound."""


class RoutingUnavailableError(RuntimeError):
    """No imported network, or no path between the snapped endpoints."""


@dataclass(frozen=True)
class SnappedPoint:
    """Where a requested coordinate actually landed on the graph."""

    node: str
    longitude: float
    latitude: float
    snap_distance_meters: float

    @property
    def point(self) -> GeoPoint:
        return GeoPoint(latitude=self.latitude, longitude=self.longitude)


def haversine_m(
    lon_a: float, lat_a: float, lon_b: float, lat_b: float
) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    latitude_delta = math.radians(lat_b - lat_a)
    longitude_delta = math.radians(lon_b - lon_a)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(lat_a))
        * math.cos(math.radians(lat_b))
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(value))


def _validate(point: GeoPoint, label: str) -> None:
    if not (math.isfinite(point.longitude) and math.isfinite(point.latitude)):
        raise RouteSnappingError(f"{label} coordinates must be finite")
    if not -180.0 <= point.longitude <= 180.0:
        raise RouteSnappingError(f"{label} longitude is outside [-180, 180]")
    if not -90.0 <= point.latitude <= 90.0:
        raise RouteSnappingError(f"{label} latitude is outside [-90, 90]")


def snap_point_to_graph_node(
    graph: nx.DiGraph,
    point: GeoPoint,
    *,
    max_snap_meters: float,
    label: str = "point",
) -> SnappedPoint:
    """Map a WGS84 coordinate onto its nearest graph node, within a bound.

    Strategy (nearest-*node*, not nearest-edge, and deliberately documented as
    such): every node in the cached graph carries WGS84 ``x``/``y`` taken from
    the endpoints of stored ``RoadEdge`` rows. The nearest node is chosen by
    haversine distance, with ties broken by ascending node id so repeated calls
    are deterministic.

    The search is bounded by ``max_snap_meters``. A point further away than the
    bound raises instead of being snapped: silently attaching a vehicle to a
    road hundreds of metres away would be a wrong route presented as a right
    one. Callers that want edge-accurate snapping can pass the coordinate of the
    road itself.
    """
    _validate(point, label)
    if max_snap_meters <= 0:
        raise ValueError("max_snap_meters must be positive")

    best: Optional[tuple[float, str]] = None
    for node, data in graph.nodes(data=True):
        try:
            longitude = float(data["x"])
            latitude = float(data["y"])
        except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover
            raise RoutingUnavailableError(
                f"graph node {node!r} has no usable WGS84 coordinates"
            ) from exc
        distance = haversine_m(point.longitude, point.latitude, longitude, latitude)
        # Ties resolve on node id so the mapping never depends on dict order.
        if best is None or (distance, str(node)) < best:
            best = (distance, str(node))

    if best is None:
        raise RoutingUnavailableError("the road graph contains no nodes")

    distance, node = best
    if distance > max_snap_meters:
        raise RouteSnappingError(
            f"{label} is {distance:.1f} m from the nearest road node, which exceeds "
            f"the {max_snap_meters:.1f} m snapping bound; refusing to snap"
        )
    data = graph.nodes[node]
    return SnappedPoint(
        node=node,
        longitude=float(data["x"]),
        latitude=float(data["y"]),
        snap_distance_meters=distance,
    )


def edge_travel_time_s(data: dict[str, Any]) -> float:
    """Free-flow travel time for one edge, from stored length and speed limit.

    This is the routing cost. It uses only real imported attributes --
    ``length_m`` and ``speed_limit_kmh``. Signal count, road class and lane
    count are carried on the edge and consumed by the AI layer's feature
    extraction; they are deliberately *not* folded into the cost here, because
    doing so would require a delay model that has not been fitted or validated.
    """
    speed_kmh = float(data.get("speed_limit_kmh") or 0.0)
    if speed_kmh <= 0:
        raise RoutingUnavailableError("an edge has a non-positive speed limit")
    return float(data.get("length_m") or 0.0) / (speed_kmh * 1000.0 / 3600.0)


def edge_length_m(data: dict[str, Any]) -> float:
    return float(data.get("length_m") or 0.0)


def travel_time_weight(_from_node: Any, _to_node: Any, data: dict[str, Any]) -> float:
    """NetworkX weight callable: shortest *travel time* from real attributes."""
    return edge_travel_time_s(data)


def distance_weight(_from_node: Any, _to_node: Any, data: dict[str, Any]) -> float:
    """NetworkX weight callable: shortest *distance* from stored length."""
    return edge_length_m(data)


def route_geometry(graph: nx.DiGraph, node_path: list[str]) -> list[GeoPoint]:
    """Concatenate stored edge geometries along ``node_path``.

    Each hop contributes its own stored ``coords``; when an edge has none (only
    possible for a hand-built graph, never for an imported network) the two real
    stored endpoints are used. Either way the vertices are real edge endpoints,
    not interpolated positions.
    """
    points: list[GeoPoint] = []
    for from_node, to_node in zip(node_path, node_path[1:]):
        data = graph[from_node][to_node]
        coords = data.get("coords") or ()
        if coords:
            vertices = [(float(x), float(y)) for x, y in coords]
        else:
            vertices = [
                (float(graph.nodes[from_node]["x"]), float(graph.nodes[from_node]["y"])),
                (float(graph.nodes[to_node]["x"]), float(graph.nodes[to_node]["y"])),
            ]
        for longitude, latitude in vertices:
            candidate = GeoPoint(latitude=latitude, longitude=longitude)
            if not points or points[-1] != candidate:
                points.append(candidate)
    if len(points) < 2:
        raise RoutingUnavailableError("a route hop produced no geometry")
    if len(points) > MAX_ROUTE_GEOMETRY_POINTS:
        # Keep both endpoints; drop evenly spaced interior vertices.
        step = len(points) / float(MAX_ROUTE_GEOMETRY_POINTS)
        kept = [points[int(index * step)] for index in range(MAX_ROUTE_GEOMETRY_POINTS)]
        kept[0] = points[0]
        kept[-1] = points[-1]
        points = kept
    return points


@dataclass(frozen=True)
class GraphRoute:
    """One real path over the imported network, before it becomes a proposal."""

    node_path: tuple[str, ...]
    road_edge_ids: tuple[str, ...]
    geometry: tuple[GeoPoint, ...]
    distance_meters: float
    travel_time_seconds: float
    signal_count: int
    mean_speed_limit_kmh: float

    @property
    def duration_seconds(self) -> int:
        return int(math.ceil(self.travel_time_seconds))


class RoadGraphRoutingProvider:
    """A :class:`~app.services.routing.base.RoutingProvider` over the real graph."""

    name = "road_graph_routing_provider"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._graph: Optional[nx.DiGraph] = None
        self._network_key: Optional[str] = None

    # -- graph access ---------------------------------------------------
    async def prepare(self, db: AsyncSession) -> None:
        """Load (or reuse) the cached graph for the configured network.

        Called by :class:`~app.services.route_service.RouteService` before
        ``calculate_routes``. The graph itself comes from the shared cache, so
        this performs no query on a warm cache beyond the network stamp check.
        """
        from app.services.ai_road_graph import RoadNetworkUnavailable, get_road_graph

        try:
            road_graph = await get_road_graph(db, self.settings.road_network_key)
        except RoadNetworkUnavailable as exc:
            raise RoutingUnavailableError(str(exc)) from exc
        self._graph = road_graph.graph
        self._network_key = road_graph.network_key

    @property
    def network_key(self) -> str:
        return self._network_key or self.settings.road_network_key

    @property
    def graph(self) -> nx.DiGraph:
        if self._graph is None:
            raise RoutingUnavailableError(
                "the road graph was not loaded; call prepare() first"
            )
        return self._graph

    # -- candidate generation -------------------------------------------
    def calculate_routes(self, request: RoutingRequest) -> list[RouteProposal]:
        """Produce real candidate routes between the requested endpoints."""
        graph = self.graph
        origin = snap_point_to_graph_node(
            graph,
            request.origin,
            max_snap_meters=self.settings.route_max_snap_meters,
            label="origin",
        )
        destination = snap_point_to_graph_node(
            graph,
            request.destination,
            max_snap_meters=self.settings.route_max_snap_meters,
            label="destination",
        )
        if origin.node == destination.node:
            raise RoutingUnavailableError(
                "origin and destination snap to the same road node"
            )

        paths = self._candidate_paths(origin.node, destination.node)
        if not paths:
            raise RoutingUnavailableError(
                "the road network has no directed path between the snapped "
                "origin and destination"
            )

        proposals: list[RouteProposal] = []
        for index, node_path in enumerate(paths, start=1):
            route = self._describe(graph, node_path)
            proposals.append(
                RouteProposal(
                    name=(
                        f"{self.network_key} path {index} "
                        f"({len(route.road_edge_ids)} edges)"
                    )[:160],
                    geometry=list(route.geometry),
                    distance_meters=round(route.distance_meters, 3),
                    estimated_duration_seconds=route.duration_seconds,
                    # No ML or heuristic risk is invented here; prediction-driven
                    # signals arrive separately via prediction_signals().
                    risk_score=None,
                    predicted_failure_probability=None,
                    congestion_score=None,
                    hazard_exposure=None,
                    # Canonical SENTINEL identity (RoadEdge.id), in travel order.
                    road_segment_ids=list(route.road_edge_ids),
                    reachable=True,
                )
            )
        return proposals

    def _candidate_paths(
        self, origin_node: str, destination_node: str
    ) -> list[tuple[str, ...]]:
        """The exact shortest path plus genuinely distinct alternatives.

        *Candidate 1* is the true optimum: Dijkstra on the objective cost, which
        is cross-checked against Yen's ``shortest_simple_paths`` in the tests.

        *Candidates 2..n* come from deterministic **edge penalization**: each
        iteration multiplies the cost of every edge already used by an
        accepted candidate by ``route_alternative_penalty``, then re-runs
        Dijkstra. This is the standard alternative-routes construction and it is
        fully deterministic -- no sampling, no randomness. It is used because
        Yen's *k*-shortest paths on a dense street grid differ only locally:
        measured on ``osm_urban_v1`` the second path shares 85-96% of its edges
        with the first, which never clears the 0.30 diversity threshold, so the
        resilience engine could never appoint a backup. Penalization forces the
        alternatives onto different streets. The cost of that choice is explicit:
        alternatives are optimal under a *modified* cost, not under the raw
        objective, so candidate 1 remains the only strictly optimal route.

        **Documented DiGraph limitation.** The graph is a ``DiGraph``, so the
        eleven parallel ``(from_node, to_node)`` pairs in ``osm_urban_v1`` have
        already collapsed into one edge each (712 stored rows become 701 graph
        edges). Alternatives that differ *only* by which of those parallel edges
        they use are therefore not recoverable at routing time, and the
        attributes of the winning row are the ones used. Switching to
        ``MultiDiGraph`` would fix that but is out of scope here and would change
        the edge lookup the AI layer performs (``G[u][v]``).
        """
        graph = self.graph
        weight = self._weight_key()
        limit = max(1, self.settings.route_max_candidates)
        try:
            first = nx.shortest_path(
                graph, origin_node, destination_node, weight=weight
            )
        except nx.NetworkXNoPath as exc:
            # The imported network is directed, so an unreachable pair is a real
            # possibility rather than a bug to paper over.
            raise RoutingUnavailableError(
                "the road network has no directed path between the snapped "
                "origin and destination"
            ) from exc

        paths: list[tuple[str, ...]] = [tuple(first)]
        seen = {paths[0]}
        used: set[tuple[str, str]] = set(zip(first, first[1:]))
        factor = max(1.0, float(self.settings.route_alternative_penalty))

        while len(paths) < limit:
            def penalized(_u: Any, _v: Any, data: dict[str, Any], _used=used) -> float:
                base = weight(_u, _v, data)
                return base * factor if (_u, _v) in _used else base

            try:
                candidate = nx.shortest_path(
                    graph, origin_node, destination_node, weight=penalized
                )
            except nx.NetworkXNoPath:
                # Every remaining street is penalised into disconnection.
                break
            key = tuple(candidate)
            if key in seen:
                # No new route is reachable under this penalty; further
                # iterations would repeat it, so stop instead of looping.
                break
            seen.add(key)
            paths.append(key)
            used.update(zip(candidate, candidate[1:]))
        return paths

    def _weight_key(self):
        """Cost function used for shortest *travel time* by default."""
        if self.settings.route_objective == "MINIMIZE_DISTANCE":
            return distance_weight
        return travel_time_weight

    def _describe(self, graph: nx.DiGraph, node_path: tuple[str, ...]) -> GraphRoute:
        distance = 0.0
        travel_time = 0.0
        speed_sum = 0.0
        signals = 0
        road_edge_ids: list[str] = []
        geometry: list[GeoPoint] = []
        for from_node, to_node in zip(node_path, node_path[1:]):
            data = graph[from_node][to_node]
            distance += edge_length_m(data)
            travel_time += edge_travel_time_s(data)
            speed_sum += float(data.get("speed_limit_kmh") or 0.0)
            if data.get("has_signal"):
                signals += 1
            road_edge_id = str(data.get("road_edge_id") or "")
            if not road_edge_id:
                raise RoutingUnavailableError(
                    "a routed edge has no canonical RoadEdge.id; refusing to "
                    "publish an unidentified route"
                )
            road_edge_ids.append(road_edge_id)
            for point in route_geometry(graph, [from_node, to_node]):
                if not geometry or geometry[-1] != point:
                    geometry.append(point)
        if len(geometry) < 2:
            raise RoutingUnavailableError("generated route has no geometry")
        return GraphRoute(
            node_path=node_path,
            road_edge_ids=tuple(road_edge_ids),
            geometry=tuple(geometry),
            distance_meters=distance,
            travel_time_seconds=travel_time,
            signal_count=signals,
            mean_speed_limit_kmh=speed_sum / len(road_edge_ids),
        )

    # -- prediction-aware scoring signals -------------------------------
    async def prediction_signals(
        self,
        db: AsyncSession,
        mission_id: UUID,
        vehicle_id: UUID,
        proposals: list[tuple[UUID, RouteProposal]],
        graph: nx.DiGraph,
    ) -> dict[UUID, PredictionSignals]:
        """Per-candidate score inputs, from Phase 4 where AI is enabled.

        ETA and route-failure come from the AI facade (``sentinel_ai.api``) on
        the candidate's own edge list, so provenance travels with the numbers:
        ``baseline_fallback`` and ``confidence`` are recorded and the caller is
        told this is not a trained probability. Congestion and hazard exposure
        come from the existing deterministic baseline predictors, which are
        whole-mission estimates evaluated for the single-route context of this
        candidate; they are not ML predictions and are labelled as such.

        When AI is disabled the failure signal is simply absent, and the scoring
        engine reweights the remaining metrics and reports reduced coverage.
        """
        from app.services.predictors import (
            HazardFeature,
            MissionPredictionContext,
            RouteFeature,
            VehicleFeature,
            predictor_for,
        )

        settings = self.settings
        vehicles = {
            vehicle.id: vehicle
            for vehicle in await _load_vehicles(db, mission_id)
        }
        vehicle = vehicles.get(vehicle_id)
        hazards = await _load_hazards(db, mission_id)
        vehicle_feature = (
            VehicleFeature(id=vehicle.id, speed_meters_per_second=vehicle.speed)
            if vehicle
            else None
        )

        signals: dict[UUID, PredictionSignals] = {}
        ai_source = "disabled"
        reasons: list[str] = []
        confidence = 0.0
        for candidate_id, proposal in proposals:
            route = RouteFeature(
                id=candidate_id,
                vehicle_id=vehicle_id,
                distance_meters=proposal.distance_meters,
                estimated_duration_seconds=proposal.estimated_duration_seconds,
                risk_score=proposal.risk_score,
                road_edge_ids=tuple(proposal.road_segment_ids),
                closed=False,
            )
            context = MissionPredictionContext(
                mission_id=mission_id,
                mission_status="active",
                routes=(route,),
                vehicles=(vehicle_feature,) if vehicle_feature else (),
                hazards=hazards,
                recent_events=(),
                active_hazard_event_seen=bool(hazards),
            )
            congestion = _deterministic_score(
                predictor_for(PredictionKind.CONGESTION), context
            )
            hazard = _deterministic_score(
                predictor_for(PredictionKind.HAZARD_IMPACT), context
            )
            eta_seconds: Optional[float] = None
            failure: Optional[float] = None
            if settings.sentinel_ai_enabled:
                eta_seconds, failure, ai_source, reasons, confidence = self._ai_signals(
                    graph, proposal, vehicle
                )
            signals[candidate_id] = PredictionSignals(
                eta_seconds=eta_seconds,
                failure_probability=failure,
                congestion_score=congestion,
                hazard_exposure=hazard,
            )
        self._provenance = _provenance_text(
            ai_source=ai_source,
            confidence=confidence,
            reasons=reasons,
            network_key=self.network_key,
        )
        return signals

    def _ai_signals(
        self,
        graph: nx.DiGraph,
        proposal: RouteProposal,
        vehicle: Any,
    ) -> tuple[Optional[float], Optional[float], str, list[str], float]:
        """Call the AI facade once for one candidate's edge list.

        ``Vehicle.speed`` is metres per second. Telemetry-preferred speed
        belongs to the Phase 4 prediction context; here the vehicle's own
        recorded speed is used so a route request does not depend on telemetry
        having arrived yet.

        The facade is the AI layer's entire public surface; the adapter in
        :mod:`app.services.ai_predictor` uses these same functions, so routing
        and prediction cannot drift apart.
        """
        import time

        from sentinel_ai.api import predict_eta, predict_route_risk
        from sentinel_ai.contracts import (
            PredictEtaRequest,
            PredictRouteRiskRequest,
            UnitState,
            WorldState,
        )

        # The facade needs graph-local edge keys ("u->v"), which are a third
        # identity; they are derived from the canonical ids we already hold via
        # the graph's own edge attributes rather than re-matched geometrically.
        edge_keys = _edge_keys(graph, tuple(proposal.road_segment_ids))
        if not edge_keys:
            return None, None, "no_edge_keys", ["route edges are absent from the graph"], 0.0

        depart = time.time()
        world_state = WorldState(
            timestamp_utc=depart,
            demand_level=1.0,
            closed_edges=[],
            incidents=[],
            hazards=[],
            units=[
                UnitState(
                    unit_id=str(vehicle.id) if vehicle else "routing",
                    unit_type="emergency",
                    position=edge_keys[0].split("->")[0],
                    available=True,
                )
            ],
            hospitals=[],
        )
        eta_response = predict_eta(
            PredictEtaRequest(
                route=edge_keys,
                depart_time_utc=depart,
                world_state=world_state,
            ),
            G=graph,
        )
        risk_response = predict_route_risk(
            PredictRouteRiskRequest(
                route=edge_keys,
                depart_time_utc=depart,
                world_state=world_state,
            ),
            G=graph,
        )
        eta_seconds: Optional[float] = None
        if vehicle is not None and vehicle.speed:
            eta_seconds = round(proposal.distance_meters / float(vehicle.speed), 2)
        else:
            eta_seconds = round(eta_response.eta_p50_min * 60.0, 2)
        return (
            eta_seconds,
            float(risk_response.failure_probability),
            risk_response.source,
            list(risk_response.reasons) or list(eta_response.reasons),
            float(max(eta_response.confidence, risk_response.confidence)),
        )


def _edge_keys(graph: nx.DiGraph, road_edge_ids: tuple[str, ...]) -> list[str]:
    """Translate canonical RoadEdge ids into the facade's ``"u->v"`` keys.

    The graph is scanned once per call and the result is inverted, so a route
    never has to be matched back to edges geometrically.
    """
    if not road_edge_ids:
        return []
    wanted = set(road_edge_ids)
    found: dict[str, str] = {}
    for from_node, to_node, data in graph.edges(data=True):
        road_edge_id = str(data.get("road_edge_id") or "")
        if road_edge_id in wanted:
            found[road_edge_id] = f"{from_node}->{to_node}"
            if len(found) == len(wanted):
                break
    return [found[road_edge_id] for road_edge_id in road_edge_ids if road_edge_id in found]


def _deterministic_score(predictor: Any, context: Any) -> Optional[float]:
    """Score one whole-mission prediction for a single-route context."""
    result = predictor.predict(context, 300)
    if result.probability is not None:
        return float(result.probability)
    return None


def _provenance_text(
    *,
    ai_source: str,
    confidence: float,
    reasons: list[str],
    network_key: str,
) -> str:
    """A single line recording where candidate signals came from."""
    parts = [
        f"road_graph={network_key}",
        f"ai_source={ai_source}",
        f"ai_confidence={confidence:.2f}",
        f"not_a_trained_probability={'true' if ai_source != 'ml' else 'false'}",
    ]
    if reasons:
        parts.append(f"ai_reasons={'|'.join(reasons)}")
    return "; ".join(parts)


async def _load_vehicles(db: AsyncSession, mission_id: UUID) -> list[Any]:
    from sqlalchemy import select

    from app.models import Vehicle

    return list((await db.scalars(select(Vehicle).where(Vehicle.mission_id == mission_id))).all())


async def _load_hazards(db: AsyncSession, mission_id: UUID) -> tuple[Any, ...]:
    from sqlalchemy import select

    from app.models import Hazard
    from app.services.predictors import HazardFeature as _HazardFeature

    rows = (
        await db.execute(
            select(Hazard.id, Hazard.hazard_type, Hazard.severity, Hazard.geometry)
            .where(Hazard.mission_id == mission_id)
            .order_by(Hazard.id)
        )
    ).all()
    features: list[_HazardFeature] = []
    for row in rows:
        features.append(
            _HazardFeature(
                id=row.id,
                hazard_type=str(row.hazard_type),
                severity=int(row.severity or 0),
                distance_meters=None,
                road_node_id=None,
            )
        )
    return tuple(features)