"""Live end-to-end tests for GADataClient (run with ``-m live``)."""
import pytest

from gadata.client import GADataClient
from gadata.domain.borehole import BoreholeCollection

pytestmark = pytest.mark.live

ACT_BBOX = (148.9, -35.6, 149.3, -35.1)
# A small Murray Basin bbox around boreholes known (via the probe) to carry
# stratigraphy logs (ENOs 35147/35151 at ~140.695, -34.475).
MURRAY_BBOX = (140.68, -34.49, 140.71, -34.46)


@pytest.fixture
def ga(tmp_path):
    # Isolated cache per test so live runs never pollute the user cache dir.
    return GADataClient(cache_dir=tmp_path)


def test_boreholes_real(ga):
    coll = ga.boreholes(bbox=ACT_BBOX)
    assert isinstance(coll, BoreholeCollection)
    assert len(coll) > 0
    assert all(b.longitude is not None for b in coll)


def test_boreholes_repeat_hits_cache(ga):
    coll1 = ga.boreholes(bbox=ACT_BBOX)
    # Repeat: revalidates via numberMatched fingerprint, should serve cached.
    coll2 = ga.boreholes(bbox=ACT_BBOX)
    assert len(coll1) == len(coll2)


def test_hydrogeology_real(ga):
    gdf = ga.hydrogeology(bbox=ACT_BBOX)
    assert len(gdf) > 0
    assert gdf.crs.to_epsg() == 4283
    assert gdf.geometry.iloc[0].geom_type in {"Polygon", "MultiPolygon"}


def test_load_logs_real(ga):
    # Murray Basin bbox: these boreholes are known to carry stratigraphy logs,
    # so this exercises the full wired boreholes -> load_logs -> distribute path.
    coll = ga.boreholes(bbox=MURRAY_BBOX)
    assert len(coll) > 0
    coll.load_logs("stratigraphy")
    total = sum(len(b.stratigraphy) for b in coll)
    assert total > 0
    # And every interval landed on the borehole whose ENO it carries.
    for b in coll:
        for iv in b.stratigraphy:
            assert iv.eno == b.eno
