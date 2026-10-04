"""Read-only road network geometry endpoints.

These endpoints expose imported reference data only. They never generate,
mutate, or score a route, and they never translate a route into simulator
identifiers; that translation belongs to the simulation request boundary.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import RoadEdge, RoadNetwork
from app.schemas.road import RoadEdgeProperties, RoadNetworkSummary
from app.services.road_graph import RoadNetworkNotFound, load_network

router = APIRouter(prefix="/road-networks", tags=["road networks"])

# Edges are declared LINESTRING in the model and NOT NULL in the migration, so
# only that one shape is accepted. Accepting MULTILINESTRING here would mean
# either silently flattening it or raising a parse error deep in the loop.
_GEOJSON_TYPES = {
    "LINESTRING": "LineString",
}


def _geojson_geometry(wkt: str) -> dict[str, Any]:
    """Convert a PostGIS WKT linestring into a GeoJSON geometry.

    Geometry is requested from PostGIS as ``ST_AsText`` rather than read as a
    geometry column. GeoAlchemy2 converts a value read back from the database
    into a ``WKBElement`` whose ``str()`` is hex-encoded WKB, not WKT, and this
    project does not depend on Shapely to convert it. Stringifying the element
    would therefore parse as garbage on every real request, so the conversion
    is pushed into the database where the text already exists.
    """
    text = (wkt or "").strip()
    if not text:
        raise HTTPException(status_code=500, detail="road edge geometry is empty")
    # "SRID=4326;LINESTRING(...)" is the EWKT form some drivers emit.
    prefix, separator, remainder = text.partition(";")
    if separator and prefix.upper().startswith("SRID"):
        text = remainder.strip()
    geometry_type, separator, remainder = text.partition("(")
    geometry_type = geometry_type.strip().upper()
    if not separator or geometry_type not in _GEOJSON_TYPES:
        raise HTTPException(
            status_code=500,
            detail=f"road edge geometry is not a linestring: {geometry_type!r}",
        )
    try:
        coordinates = [
            [float(value) for value in pair.split()]
            for pair in remainder.rstrip(")").split(",")
            if pair.strip()
        ]
    except ValueError as error:
        # Unparseable coordinates are a data fault; report it rather than
        # letting a bare ValueError escape as an opaque 500.
        raise HTTPException(
            status_code=500, detail="road edge geometry has malformed coordinates"
        ) from error
    if len(coordinates) < 2:
        raise HTTPException(
            status_code=500, detail="road edge geometry has fewer than two vertices"
        )
    return {"type": _GEOJSON_TYPES[geometry_type], "coordinates": coordinates}


async def _network_or_404(
    db: AsyncSession, network_key: str
) -> RoadNetwork:
    try:
        return await load_network(db, network_key)
    except RoadNetworkNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/{network_key}",
    summary="Describe an imported road network",
    response_model=RoadNetworkSummary,
)
async def describe_road_network(
    network_key: str, db: AsyncSession = Depends(get_db)
) -> RoadNetworkSummary:
    """Return counts and declared extent for one imported network."""
    network = await _network_or_404(db, network_key)
    total = await db.scalar(
        select(func.count())
        .select_from(RoadEdge)
        .where(RoadEdge.network_id == network.id)
    )
    signalised = await db.scalar(
        select(func.count())
        .select_from(RoadEdge)
        .where(RoadEdge.network_id == network.id, RoadEdge.has_signal.is_(True))
    )
    ways = await db.scalar(
        select(func.count(func.distinct(RoadEdge.osm_way_id))).where(
            RoadEdge.network_id == network.id
        )
    )
    declared = network.orig_boundary.split(",") if network.orig_boundary else []
    bounding_box = [float(value) for value in declared] if len(declared) == 4 else None
    return RoadNetworkSummary(
        network_key=network.network_key,
        edge_count=int(total or 0),
        signalised_edge_count=int(signalised or 0),
        distinct_osm_way_ids=int(ways or 0),
        bounding_box=bounding_box,
    )


@router.get(
    "/{network_key}/edges",
    summary="Return road network edges as GeoJSON",
)
async def list_road_network_edges(
    network_key: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Return every edge of one network as a GeoJSON FeatureCollection."""
    network = await _network_or_404(db, network_key)
    rows = (
        await db.execute(
            select(
                RoadEdge.id,
                RoadEdge.external_id,
                RoadEdge.osm_way_id,
                RoadEdge.road_class,
                RoadEdge.speed_limit_kmh,
                RoadEdge.length_m,
                RoadEdge.lanes,
                RoadEdge.has_signal,
                func.ST_AsText(RoadEdge.geometry).label("geometry_wkt"),
            )
            .where(RoadEdge.network_id == network.id)
            .order_by(RoadEdge.external_id)
        )
    ).all()
    features = []
    for row in rows:
        # The property list lives in the schema so the GeoJSON contract has one
        # definition instead of being restated per endpoint.
        properties = RoadEdgeProperties(
            road_edge_id=str(row.id),
            external_id=row.external_id,
            osm_way_id=row.osm_way_id,
            road_class=row.road_class,
            speed_limit_kmh=float(row.speed_limit_kmh),
            length_m=float(row.length_m),
            lanes=int(row.lanes),
            has_signal=bool(row.has_signal),
        )
        features.append(
            {
                "type": "Feature",
                "id": properties.road_edge_id,
                "geometry": _geojson_geometry(row.geometry_wkt),
                "properties": properties.model_dump(),
            }
        )
    return {"type": "FeatureCollection", "features": features}
