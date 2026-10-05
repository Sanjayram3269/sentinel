"""Database-free checks for the road network GeoJSON conversion.

These matter because the persistence layer cannot run without PostGIS. The
endpoint reads geometry from the database, and what a database returns is not
what a test fixture is likely to construct: GeoAlchemy2 converts a value read
back into a ``WKBElement`` whose ``str()`` is hex-encoded WKB. Converting by
stringifying the element therefore parses garbage on every real request, and
the only in-suite coverage of the read path would otherwise be the
TEST_DATABASE_URL integration test.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from geoalchemy2 import WKBElement

from app.api.v1.road_networks import _geojson_geometry


def test_linestring_wkt_becomes_a_geojson_geometry() -> None:
    geometry = _geojson_geometry("LINESTRING(77.5 13.1, 77.6 13.2, 77.7 13.3)")

    assert geometry["type"] == "LineString"
    assert geometry["coordinates"] == [
        [77.5, 13.1],
        [77.6, 13.2],
        [77.7, 13.3],
    ]


def test_negative_coordinates_survive() -> None:
    """A Bengaluru extract is positive, but the parser must not assume that."""
    geometry = _geojson_geometry("LINESTRING(-77.5 -13.1, -77.6 -13.2)")

    assert geometry["coordinates"] == [[-77.5, -13.1], [-77.6, -13.2]]


def test_srid_prefix_is_tolerated() -> None:
    geometry = _geojson_geometry("SRID=4326;LINESTRING(77.5 13.1, 77.6 13.2)")

    assert geometry["type"] == "LineString"
    assert len(geometry["coordinates"]) == 2


def test_multilinestring_is_rejected_because_edges_are_linestrings() -> None:
    """An edge that is not a linestring is a data fault, not a mapping to guess."""
    with pytest.raises(HTTPException) as error:
        _geojson_geometry("MULTILINESTRING((77.5 13.1, 77.6 13.2))")

    assert error.value.status_code == 500


def test_single_vertex_geometry_is_rejected() -> None:
    with pytest.raises(HTTPException) as error:
        _geojson_geometry("LINESTRING(77.5 13.1)")

    assert error.value.status_code == 500


def test_hex_wkb_read_from_the_database_is_not_silently_accepted() -> None:
    """The failure this guards is silent: the row is fetched, then it is garbage.

    GeoAlchemy2 hands back a ``WKBElement`` on read, so ``str()`` is hex. If the
    endpoint ever goes back to stringifying the element, this shape is what it
    receives, and a parser that accepts it would emit nonsense coordinates.
    """
    wkb = WKBElement(b"\x01\x02\x00\x00\x00", srid=4326)

    assert not str(wkb).startswith("LINESTRING")
    with pytest.raises(HTTPException) as error:
        _geojson_geometry(str(wkb))

    assert error.value.status_code == 500


def test_empty_geometry_is_reported_rather_than_producing_an_empty_line() -> None:
    with pytest.raises(HTTPException) as error:
        _geojson_geometry("")

    assert error.value.status_code == 500