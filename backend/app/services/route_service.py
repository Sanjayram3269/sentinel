"""Candidate generation, scoring, persistence, activation, and retrieval."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Event, Mission, Route, RouteCandidate, Vehicle
from app.models.enums import ResilienceRole, RouteStatus
from app.schemas.events import EventCreate, EventType
from app.schemas.routing import (
    ResilienceLevel,
    ResilienceRead,
    RouteActivationRead,
    RouteCandidateCollection,
    RouteCandidateRead,
    RouteCandidateRequest,
    RouteCollection,
    RouteRead,
)
from app.services.event_service import EventService
from app.services.route_resilience import (
    ResilienceResult,
    assign_route_roles,
    is_route_status_viable,
)
from app.services.route_scoring import (
    RouteScoreWeights,
    RouteScoringEngine,
    ScoredRoute,
)
from app.services.routing.base import RoutingProvider, RoutingRequest
from app.services.routing.road_graph_provider import (
    RouteSnappingError,
    RoutingUnavailableError,
)

MAX_ROUTE_LIST_LIMIT = 200


class RouteService:
    def __init__(
        self,
        event_service: EventService,
        provider: RoutingProvider,
        settings: Settings | None = None,
    ) -> None:
        self.event_service = event_service
        self.provider = provider
        self.settings = settings or get_settings()
        self.scorer = RouteScoringEngine(
            RouteScoreWeights.from_settings(self.settings),
            failure_threshold=self.settings.route_failure_threshold,
            hazard_threshold=self.settings.route_hazard_threshold,
        )

    async def generate_candidates(
        self,
        db: AsyncSession,
        mission_id: UUID,
        payload: RouteCandidateRequest,
    ) -> RouteCandidateCollection:
        cycle_id = uuid4()
        correlation_id = uuid4()
        # Real generation is the default when the caller supplies no geometry.
        # Caller-supplied candidates keep the previous provider and behaviour.
        provider = self.provider
        if not payload.candidates:
            from app.services.routing.road_graph_provider import (
                RoadGraphRoutingProvider,
            )

            provider = RoadGraphRoutingProvider(self.settings)
        prepare = getattr(provider, "prepare", None)
        if prepare is not None:
            try:
                await prepare(db)
            except (RoutingUnavailableError, RouteSnappingError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            # The graph stamp check opened an implicit transaction; close it so
            # the explicit block below is the only one.
            await db.commit()
        async with db.begin():
            mission = await db.get(Mission, mission_id)
            if mission is None:
                raise HTTPException(status_code=404, detail="Mission not found")
            vehicle = await db.scalar(
                select(Vehicle)
                .where(Vehicle.id == payload.vehicle_id)
                .with_for_update()
            )
            if vehicle is None:
                raise HTTPException(status_code=404, detail="Vehicle not found")
            if vehicle.mission_id != mission_id:
                raise HTTPException(
                    status_code=409,
                    detail="Vehicle does not belong to this mission",
                )

            provider_request = RoutingRequest(
                mission_id=mission_id,
                vehicle_id=payload.vehicle_id,
                origin=payload.origin,
                destination=payload.destination,
                proposals=tuple(payload.candidates),
                parameters=payload.routing_parameters,
            )
            try:
                proposals = provider.calculate_routes(provider_request)
            except (RoutingUnavailableError, RouteSnappingError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if not proposals:
                raise HTTPException(
                    status_code=422,
                    detail="Routing provider returned no route candidates",
                )
            scored_inputs = []
            route_ids = [uuid4() for _ in proposals]
            for route_id, proposal in zip(route_ids, proposals, strict=True):
                scored_inputs.append((route_id, proposal))
            signals = None
            provenance = None
            if hasattr(provider, "prediction_signals"):
                signals = await provider.prediction_signals(
                    db,
                    mission_id,
                    payload.vehicle_id,
                    scored_inputs,
                    provider.graph,
                )
                provenance = getattr(provider, "_provenance", None)
            scored = self.scorer.score(scored_inputs, predictions=signals)
            if provenance:
                scored = [
                    replace(item, provenance=provenance) for item in scored
                ]
            resilience = assign_route_roles(
                scored, minimum_diversity=self.settings.route_min_diversity
            )

            candidate_by_route: dict[UUID, RouteCandidate] = {}
            for rank, item in enumerate(scored, start=1):
                proposal = item.proposal
                line = _linestring(proposal)
                route = Route(
                    id=item.candidate_id,
                    mission_id=mission_id,
                    vehicle_id=payload.vehicle_id,
                    status=RouteStatus.CANDIDATE,
                    name=proposal.name,
                    geometry=line,
                    distance_meters=proposal.distance_meters,
                    estimated_duration_seconds=proposal.estimated_duration_seconds,
                    risk_score=proposal.risk_score,
                )
                candidate = RouteCandidate(
                    mission_id=mission_id,
                    vehicle_id=payload.vehicle_id,
                    route=route,
                    planning_cycle_id=cycle_id,
                    origin=WKTElement(
                        f"POINT({payload.origin.longitude} {payload.origin.latitude})",
                        srid=4326,
                    ),
                    destination=WKTElement(
                        f"POINT({payload.destination.longitude} {payload.destination.latitude})",
                        srid=4326,
                    ),
                    geometry=line,
                    status=RouteStatus.CANDIDATE,
                    resilience_role=resilience.roles.get(item.candidate_id),
                    route_rank=rank,
                    estimated_duration_seconds=proposal.estimated_duration_seconds,
                    distance_meters=proposal.distance_meters,
                    risk_score=proposal.risk_score,
                    predicted_failure_probability=item.metrics.get("failure"),
                    congestion_score=item.metrics.get("congestion"),
                    score=item.score,
                    confidence=item.score_coverage,
                    hazard_exposure=(
                        {"score": item.metrics["hazard"], "source": "candidate_or_prediction"}
                        if "hazard" in item.metrics
                        else {}
                    ),
                    road_segment_ids=proposal.road_segment_ids,
                    backup_viable=item.viable,
                    rationale=self._rationale(item),
                    provider=provider.name,
                )
                db.add(candidate)
                candidate_by_route[item.candidate_id] = candidate
            await db.flush()
            event = await self.event_service.persist(
                db,
                mission_id,
                EventCreate(
                    event_type=EventType.ROUTE_UPDATED,
                    timestamp=datetime.now(timezone.utc),
                    source=provider.name,
                    correlation_id=correlation_id,
                    payload={
                        "planning_cycle_id": str(cycle_id),
                        "vehicle_id": str(payload.vehicle_id),
                        "route_ids": [str(route_id) for route_id in route_ids],
                        "roles": {
                            str(route_id): resilience.roles[route_id].value
                            for route_id in resilience.roles
                        },
                    },
                ),
            )

        await self.event_service.publish_persisted(event)
        reads = [
            self._candidate_read(
                candidate_by_route[item.candidate_id],
                item.proposal,
                item,
            )
            for item in scored
        ]
        return RouteCandidateCollection(
            mission_id=mission_id,
            vehicle_id=payload.vehicle_id,
            planning_cycle_id=cycle_id,
            provider=provider.name,
            candidates=reads,
            resilience=self._resilience_read(
                mission_id, payload.vehicle_id, cycle_id, resilience
            ),
        )

    async def list_routes(
        self,
        db: AsyncSession,
        mission_id: UUID,
        *,
        vehicle_id: UUID | None,
        role: ResilienceRole | None,
        route_status: RouteStatus | None,
        limit: int,
    ) -> RouteCollection:
        await self._require_mission(db, mission_id)
        limit = min(max(limit, 1), MAX_ROUTE_LIST_LIMIT)
        statement = (
            select(
                Route,
                RouteCandidate.resilience_role,
                RouteCandidate.status,
                func.ST_AsGeoJSON(Route.geometry),
            )
            .outerjoin(RouteCandidate, RouteCandidate.route_id == Route.id)
            .where(Route.mission_id == mission_id)
        )
        if vehicle_id is not None:
            statement = statement.where(Route.vehicle_id == vehicle_id)
        if role is not None:
            statement = statement.where(RouteCandidate.resilience_role == role)
            statement = statement.where(
                Route.status.not_in(
                    [RouteStatus.FAILED, RouteStatus.ABORTED, RouteStatus.DEGRADED]
                )
            )
            statement = statement.where(
                RouteCandidate.status.not_in(
                    [RouteStatus.FAILED, RouteStatus.ABORTED, RouteStatus.DEGRADED]
                )
            )
        if route_status is not None:
            statement = statement.where(Route.status == route_status)
        rows = (
            await db.execute(
                statement.order_by(Route.created_at.desc(), Route.id.desc()).limit(limit)
            )
        ).all()
        return RouteCollection(
            items=[self._route_read(row.Route, row[1], row[3], row[2]) for row in rows],
            limit=limit,
        )

    async def get_route(
        self, db: AsyncSession, mission_id: UUID, route_id: UUID
    ) -> RouteRead:
        await self._require_mission(db, mission_id)
        row = (
            await db.execute(
                select(
                    Route,
                    RouteCandidate.resilience_role,
                    RouteCandidate.status,
                    func.ST_AsGeoJSON(Route.geometry),
                )
                .outerjoin(RouteCandidate, RouteCandidate.route_id == Route.id)
                .where(Route.mission_id == mission_id, Route.id == route_id)
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Route not found")
        return self._route_read(row.Route, row[1], row[3], row[2])

    async def activate(
        self,
        db: AsyncSession,
        mission_id: UUID,
        route_id: UUID,
        *,
        correlation_id: UUID | None = None,
    ) -> RouteActivationRead:
        correlation_id = correlation_id or uuid4()
        async with db.begin():
            if await db.get(Mission, mission_id) is None:
                raise HTTPException(status_code=404, detail="Mission not found")
            route = await db.scalar(
                select(Route)
                .where(Route.id == route_id, Route.mission_id == mission_id)
                .with_for_update()
            )
            if route is None:
                raise HTTPException(status_code=404, detail="Route not found")
            candidate = await db.scalar(
                select(RouteCandidate)
                .where(RouteCandidate.route_id == route_id)
                .with_for_update()
            )
            vehicle = await db.scalar(
                select(Vehicle)
                .where(Vehicle.id == route.vehicle_id)
                .with_for_update()
            )
            if vehicle is None or vehicle.mission_id != mission_id:
                raise HTTPException(
                    status_code=409, detail="Route vehicle does not belong to this mission"
                )
            if not is_route_status_viable(route.status):
                raise HTTPException(status_code=409, detail="Nonviable route cannot be activated")
            if (
                candidate is None
                or not candidate.backup_viable
                or not is_route_status_viable(candidate.status)
            ):
                raise HTTPException(status_code=409, detail="Route is not viable")
            if (
                candidate.predicted_failure_probability is not None
                and candidate.predicted_failure_probability
                > self.settings.route_failure_threshold
            ):
                raise HTTPException(status_code=409, detail="Route failure risk exceeds threshold")
            hazard = candidate.hazard_exposure.get("score")
            if hazard is not None and hazard > self.settings.route_hazard_threshold:
                raise HTTPException(status_code=409, detail="Route hazard exposure exceeds threshold")

            previous = list(
                (
                    await db.scalars(
                        select(Route)
                        .where(
                            Route.mission_id == mission_id,
                            Route.vehicle_id == route.vehicle_id,
                            Route.status == RouteStatus.ACTIVE,
                            Route.id != route_id,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            previous_route_ids = [old_route.id for old_route in previous]
            previous_candidates = list(
                (
                    await db.scalars(
                        select(RouteCandidate).where(
                            RouteCandidate.route_id.in_(previous_route_ids)
                        )
                    )
                ).all()
            ) if previous_route_ids else []
            previous_candidates_by_route = {
                old_candidate.route_id: old_candidate
                for old_candidate in previous_candidates
            }
            for old_route in previous:
                old_route.status = RouteStatus.ABORTED
                old_candidate = previous_candidates_by_route.get(old_route.id)
                if old_candidate is not None:
                    old_candidate.status = RouteStatus.ABORTED
            was_active = route.status is RouteStatus.ACTIVE
            route.status = RouteStatus.ACTIVE
            candidate.status = RouteStatus.ACTIVE
            event = await self.event_service.persist(
                db,
                mission_id,
                EventCreate(
                    event_type=(
                        EventType.ROUTE_UPDATED if was_active or previous else EventType.ROUTE_ASSIGNED
                    ),
                    timestamp=datetime.now(timezone.utc),
                    source="sentinel_route_engine",
                    correlation_id=correlation_id,
                    payload={
                        "route_id": str(route.id),
                        "vehicle_id": str(vehicle.id),
                        "resilience_role": candidate.resilience_role.value
                        if candidate.resilience_role
                        else None,
                        "previous_route_ids": [str(item.id) for item in previous],
                    },
                ),
            )
            await db.flush()
            await db.refresh(route)
            await db.refresh(candidate)
            await db.refresh(event)
            geojson = await db.scalar(
                select(func.ST_AsGeoJSON(Route.geometry)).where(Route.id == route_id)
            )
        await self.event_service.publish_persisted(event)
        route_read = self._route_read(
            route, candidate.resilience_role, geojson, candidate.status
        )
        return RouteActivationRead(
            route=route_read,
            event_id=event.id,
            correlation_id=correlation_id,
        )

    async def resilience(
        self,
        db: AsyncSession,
        mission_id: UUID,
        *,
        vehicle_id: UUID | None,
        planning_cycle_id: UUID | None,
    ) -> ResilienceRead:
        await self._require_mission(db, mission_id)
        filters = [RouteCandidate.mission_id == mission_id]
        if vehicle_id is not None:
            filters.append(RouteCandidate.vehicle_id == vehicle_id)
        if planning_cycle_id is not None:
            filters.append(RouteCandidate.planning_cycle_id == planning_cycle_id)
        else:
            cycle_filters = [RouteCandidate.mission_id == mission_id]
            if vehicle_id is not None:
                cycle_filters.append(RouteCandidate.vehicle_id == vehicle_id)
            latest_cycle = select(RouteCandidate.planning_cycle_id).where(*cycle_filters)
            latest_cycle = latest_cycle.order_by(
                RouteCandidate.created_at.desc(), RouteCandidate.id.desc()
            ).limit(1).scalar_subquery()
            filters.append(RouteCandidate.planning_cycle_id == latest_cycle)
        rows = (
            await db.execute(
                select(
                    RouteCandidate,
                    Route,
                    func.ST_AsGeoJSON(Route.geometry),
                )
                .join(Route, Route.id == RouteCandidate.route_id)
                .where(*filters)
                .order_by(RouteCandidate.route_rank)
                .limit(50)
            )
        ).all()
        if not rows:
            return ResilienceRead(
                mission_id=mission_id,
                vehicle_id=vehicle_id,
                planning_cycle_id=planning_cycle_id,
                primary_route_id=None,
                backup_route_id=None,
                contingency_route_id=None,
                resilience_level=ResilienceLevel.NONE,
                resilience_score=0.0,
                route_diversity=0.0,
                failure_exposure=None,
                primary_available=False,
                backup_available=False,
                contingency_available=False,
                explanation=["No route candidates exist for the requested planning cycle"],
            )
        scored = [self._stored_scored_route(row.RouteCandidate, row.Route, row[2]) for row in rows]
        result = assign_route_roles(
            scored, minimum_diversity=self.settings.route_min_diversity
        )
        return self._resilience_read(
            mission_id,
            rows[0].RouteCandidate.vehicle_id,
            rows[0].RouteCandidate.planning_cycle_id,
            result,
        )

    async def _require_mission(self, db: AsyncSession, mission_id: UUID) -> None:
        if await db.scalar(select(Mission.id).where(Mission.id == mission_id)) is None:
            raise HTTPException(status_code=404, detail="Mission not found")

    @staticmethod
    def _rationale(item: ScoredRoute) -> str:
        if item.rejection_reasons:
            return "; ".join(item.rejection_reasons)
        base = (
            f"baseline_routing_score_v1={item.score:.4f}; "
            f"metric_coverage={item.score_coverage:.2f}"
        )
        if item.provenance:
            return f"{base}; {item.provenance}"
        return base

    @staticmethod
    def _candidate_read(
        candidate: RouteCandidate, proposal: Any, scored: ScoredRoute
    ) -> RouteCandidateRead:
        hazard_exposure = candidate.hazard_exposure or {}
        return RouteCandidateRead(
            candidate_id=candidate.id,
            route_id=candidate.route_id,
            mission_id=candidate.mission_id,
            vehicle_id=candidate.vehicle_id,
            planning_cycle_id=candidate.planning_cycle_id,
            origin=proposal.geometry[0],
            destination=proposal.geometry[-1],
            geometry=proposal.geometry,
            road_segment_ids=proposal.road_segment_ids,
            distance_meters=candidate.distance_meters,
            estimated_duration_seconds=candidate.estimated_duration_seconds,
            risk_score=candidate.risk_score,
            predicted_failure_probability=candidate.predicted_failure_probability,
            hazard_exposure=hazard_exposure,
            congestion_score=candidate.congestion_score,
            resilience_role=candidate.resilience_role,
            status=candidate.status,
            score=candidate.score,
            score_coverage=candidate.confidence,
            viable=candidate.backup_viable,
            rationale=candidate.rationale,
            provider=candidate.provider,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
        )

    @staticmethod
    def _route_read(
        route: Route,
        role: ResilienceRole | None,
        geojson: str | None,
        candidate_status: RouteStatus | None,
    ) -> RouteRead:
        coordinates = json.loads(geojson)["coordinates"] if geojson else []
        from app.schemas.domain import GeoPoint

        points = [GeoPoint(latitude=latitude, longitude=longitude) for longitude, latitude in coordinates]
        return RouteRead(
            id=route.id,
            mission_id=route.mission_id,
            vehicle_id=route.vehicle_id,
            status=route.status,
            name=route.name,
            geometry=points,
            distance_meters=route.distance_meters,
            estimated_duration_seconds=route.estimated_duration_seconds,
            risk_score=route.risk_score,
            resilience_role=(
                role
                if is_route_status_viable(route.status)
                and candidate_status is not None
                and is_route_status_viable(candidate_status)
                else None
            ),
            created_at=route.created_at,
            updated_at=route.updated_at,
        )

    @staticmethod
    def _stored_scored_route(
        candidate: RouteCandidate, route: Route, geojson: str
    ) -> ScoredRoute:
        from app.schemas.domain import GeoPoint
        from app.schemas.routing import RouteProposal

        coordinates = json.loads(geojson)["coordinates"]
        geometry = [
            GeoPoint(latitude=latitude, longitude=longitude)
            for longitude, latitude in coordinates
        ]
        hazard = candidate.hazard_exposure.get("score")
        proposal = RouteProposal(
            name=route.name,
            geometry=geometry,
            distance_meters=route.distance_meters,
            estimated_duration_seconds=route.estimated_duration_seconds,
            risk_score=candidate.risk_score,
            predicted_failure_probability=candidate.predicted_failure_probability,
            congestion_score=candidate.congestion_score,
            hazard_exposure=hazard,
            road_segment_ids=candidate.road_segment_ids,
        )
        metrics = {
            key: value
            for key, value in {
                "failure": candidate.predicted_failure_probability,
                "risk": candidate.risk_score,
                "congestion": candidate.congestion_score,
                "hazard": hazard,
            }.items()
            if value is not None
        }
        return ScoredRoute(
            candidate_id=route.id,
            proposal=proposal,
            score=candidate.score if candidate.score is not None else 1.0,
            score_coverage=candidate.confidence,
            viable=(
                candidate.backup_viable
                and is_route_status_viable(candidate.status)
                and is_route_status_viable(route.status)
            ),
            rejection_reasons=(),
            metrics=metrics,
            provider_order=candidate.route_rank - 1,
        )

    @staticmethod
    def _resilience_read(
        mission_id: UUID,
        vehicle_id: UUID,
        cycle_id: UUID,
        result: ResilienceResult,
    ) -> ResilienceRead:
        return ResilienceRead(
            mission_id=mission_id,
            vehicle_id=vehicle_id,
            planning_cycle_id=cycle_id,
            primary_route_id=result.primary_id,
            backup_route_id=result.backup_id,
            contingency_route_id=result.contingency_id,
            resilience_level=result.level,
            resilience_score=result.score,
            route_diversity=result.diversity,
            failure_exposure=result.failure_exposure,
            primary_available=result.primary_id is not None,
            backup_available=result.backup_id is not None,
            contingency_available=result.contingency_id is not None,
            explanation=list(result.explanation),
        )


def _linestring(proposal: Any) -> WKTElement:
    coordinates = ", ".join(
        f"{point.longitude} {point.latitude}" for point in proposal.geometry
    )
    return WKTElement(f"LINESTRING({coordinates})", srid=4326)