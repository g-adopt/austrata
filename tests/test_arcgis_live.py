"""Live integration tests against the real GA Hydrogeology ArcGIS service.

Run with ``-m live``; skipped by default. Prove the adapter against reality:
the ACT bbox returns polygons, the count is positive, and the geometry
coordinates land inside the Australian GDA94 lon/lat range (confirming
``outSR=4283`` took effect — not some other CRS).
"""
import pytest

from austrata.domain.region import Region
from austrata.infrastructure.arcgis_rest_client import ArcGisRestClient

pytestmark = pytest.mark.live

ACT_REGION = Region.from_bbox(148.9, -35.6, 149.3, -35.1)

# Generous continental bounds for a plausibility check on returned coordinates.
AUS_LON = (112.0, 154.0)
AUS_LAT = (-44.0, -10.0)


@pytest.fixture(scope="module")
def client():
    return ArcGisRestClient()


def test_count_units_positive(client):
    assert client.count_units(ACT_REGION) > 0


def test_fetch_units_returns_polygons(client):
    feats = client.fetch_units(ACT_REGION)
    assert len(feats) > 0
    geom = feats[0]["geometry"]
    assert geom["type"] in {"Polygon", "MultiPolygon"}
    assert "feature" in feats[0]["properties"]


def test_coordinates_are_plausible_gda94_lonlat(client):
    feats = client.fetch_units(ACT_REGION)
    geom = feats[0]["geometry"]
    # Dig out the first coordinate pair regardless of Polygon/MultiPolygon nesting.
    coords = geom["coordinates"]
    while isinstance(coords[0][0], (list, tuple)):
        coords = coords[0]
    lon, lat = coords[0][0], coords[0][1]
    assert AUS_LON[0] <= lon <= AUS_LON[1], f"lon {lon} outside Australia"
    assert AUS_LAT[0] <= lat <= AUS_LAT[1], f"lat {lat} outside Australia"
