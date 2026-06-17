"""Live integration tests against the real GA WFS (run with ``-m live``).

These hit the network and are skipped by default. They prove the adapter
against reality: a tiny ACT bbox returns headers, the hits count is positive,
and two known ENOs return stratigraphy/earth-material features.
"""
import pytest

from gadata.domain.region import Region
from gadata.infrastructure.ogc_wfs_client import OgcWfsClient

pytestmark = pytest.mark.live

# Small ACT/Canberra bbox (lon/lat, GDA94) used in the probe.
ACT_REGION = Region.from_bbox(148.9, -35.6, 149.3, -35.1)
# ENOs verified to carry stratigraphy / earth-material logs in the probe.
KNOWN_ENOS = [35147, 35151]


@pytest.fixture(scope="module")
def client():
    return OgcWfsClient(page_size=500, eno_chunk_size=200)


def test_count_headers_positive(client):
    assert client.count_headers(ACT_REGION) > 0


def test_fetch_headers_returns_features(client):
    feats = client.fetch_headers(ACT_REGION)
    assert len(feats) > 0
    props = feats[0]["properties"]
    assert "eno" in props


def test_fetch_header_single(client):
    feat = client.fetch_header("35147")
    assert feat is not None
    assert int(feat["properties"]["eno"]) == 35147


def test_fetch_stratigraphy_for_known_enos(client):
    feats = client.fetch_stratigraphy(KNOWN_ENOS)
    assert len(feats) > 0
    assert "ENO" in feats[0]["properties"]


def test_fetch_earth_material_for_known_enos(client):
    feats = client.fetch_earth_material(KNOWN_ENOS)
    assert len(feats) > 0
    assert "LITHOLOGY" in feats[0]["properties"]
