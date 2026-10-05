"""Road network API contracts.

The edge collection is emitted as GeoJSON because GeoJSON is WGS84, which is
exactly what the backend stores and exactly what MapLibre renders. No
reprojection step is needed on the client, and no second copy of the geometry
is introduced for the frontend.
"""

from pydantic import BaseModel, ConfigDict, Field


class RoadEdgeProperties(BaseModel):
    """Identifiers and attributes a client needs to style or correlate an edge."""

    model_config = ConfigDict(extra="forbid")

    road_edge_id: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=120)
    osm_way_id: int | None = None
    road_class: str = Field(min_length=1, max_length=40)
    speed_limit_kmh: float = Field(ge=0, allow_inf_nan=False)
    length_m: float = Field(ge=0, allow_inf_nan=False)
    lanes: int = Field(ge=1)
    has_signal: bool


class RoadNetworkSummary(BaseModel):
    """Identification and extent of one imported network."""

    model_config = ConfigDict(extra="forbid")

    network_key: str
    edge_count: int = Field(ge=0)
    signalised_edge_count: int = Field(ge=0)
    distinct_osm_way_ids: int = Field(ge=0)
    bounding_box: list[float] | None = Field(
        default=None,
        description="[min_lon, min_lat, max_lon, max_lat] when geometry is present.",
    )
