"""Pure/offline unit tests for the Borehole entity and collection aggregate."""
import pytest

from gadata.domain.borehole import Borehole, BoreholeCollection
from gadata.domain.region import Region

# Mirrors the live gsmlp:BoreholeView probe (lowercase keys, lowercase eno).
HEADER_FEATURE = {
    "eno": 433968,
    "name": "CSIRO Ginninderra survey Jan 2007",
    "identifier": "http://pid.geoscience.gov.au/samplingFeature/au/BH433968",
    "GDA94_dlong": 149.2,
    "GDA94_dlat": -35.1,
    "elevation_m": 607.7,
    "state": "NSW",
    "geologicalProvinces": "Lachlan Orogen",
    "purpose": "stratigraphic - research",
    "status": "unknown",
}


def test_borehole_from_feature_uses_geometry_point():
    geom = {"type": "Point", "coordinates": [149.2, -35.10000004]}
    bh = Borehole.from_feature(HEADER_FEATURE, geom)
    assert bh.eno == 433968
    assert bh.name.startswith("CSIRO")
    assert bh.longitude == 149.2
    assert bh.latitude == -35.10000004
    assert bh.state == "NSW"
    assert bh.province == "Lachlan Orogen"
    assert bh.point is not None


def test_borehole_falls_back_to_gda94_props():
    bh = Borehole.from_feature(HEADER_FEATURE, geometry=None)
    assert bh.longitude == 149.2 and bh.latitude == -35.1


def test_unloaded_logs_raise_not_implemented():
    bh = Borehole.from_feature(HEADER_FEATURE)
    with pytest.raises(NotImplementedError):
        _ = bh.stratigraphy
    with pytest.raises(NotImplementedError):
        _ = bh.earth_material


def test_injected_logs_are_returned():
    bh = Borehole.from_feature(HEADER_FEATURE)
    bh.set_stratigraphy([])
    assert bh.stratigraphy == []


def test_collection_is_iterable_and_sized():
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    bores = [Borehole.from_feature(HEADER_FEATURE) for _ in range(3)]
    coll = BoreholeCollection(bores, region)
    assert len(coll) == 3
    assert list(coll) == bores
    assert coll.enos == [433968, 433968, 433968]


def test_collection_to_geodataframe_is_lonlat():
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    coll = BoreholeCollection([Borehole.from_feature(HEADER_FEATURE)], region)
    gdf = coll.to_geodataframe()
    assert len(gdf) == 1
    assert gdf.crs.to_epsg() == 4283
    assert "eno" in gdf.columns


def test_collection_load_logs_not_implemented():
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    coll = BoreholeCollection([Borehole.from_feature(HEADER_FEATURE)], region)
    with pytest.raises(NotImplementedError):
        coll.load_logs()
