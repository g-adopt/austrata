"""Pure/offline unit tests for the Borehole entity and collection aggregate."""
import pytest

from austrata.domain.borehole import Borehole, BoreholeCollection
from austrata.domain.region import Region

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


def test_borehole_from_feature_tags_source_and_captures_raw_bag():
    bh = Borehole.from_feature(HEADER_FEATURE)
    assert bh.source == "GA"
    assert bh.source_attributes == HEADER_FEATURE
    # GA leaves the promoted NGIS header fields null.
    assert bh.bore_depth_m is None
    assert bh.drilled_depth_m is None
    assert bh.drilled_date is None


def test_unloaded_construction_raises_then_returns_when_injected():
    bh = Borehole.from_feature(HEADER_FEATURE)
    with pytest.raises(NotImplementedError):
        _ = bh.construction
    bh.set_construction([])
    assert bh.construction == []


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
    assert "source" in gdf.columns
    assert gdf["source"].iloc[0] == "GA"
    for col in ("bore_depth_m", "drilled_depth_m", "drilled_date"):
        assert col in gdf.columns


def test_collection_load_logs_not_implemented():
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    coll = BoreholeCollection([Borehole.from_feature(HEADER_FEATURE)], region)
    with pytest.raises(NotImplementedError):
        coll.load_logs()


# -- log-export GeoDataFrames -------------------------------------------

from austrata.domain.stratigraphy import (  # noqa: E402
    EarthMaterialInterval,
    StratigraphyInterval,
)

STRAT_A = {"ENO": 1, "BOREHOLE_NAME": "BH1", "INTERVAL_BEGIN_M": 0,
           "INTERVAL_END_M": 5, "STRAT_UNIT_NAME": "Sand",
           "DEPTH_REF_POINT_ELEV_M_AHD": 100}
STRAT_B = {"ENO": 1, "BOREHOLE_NAME": "BH1", "INTERVAL_BEGIN_M": 5,
           "INTERVAL_END_M": 10, "STRAT_UNIT_NAME": "Clay"}
EARTH_A = {"ENO": 2, "BOREHOLE_NAME": "BH2", "INTERVAL_BEGIN_M": 0,
           "INTERVAL_END_M": 3, "LITHOLOGY": "gravel", "LITHOLOGY_GROUP": "rock"}


def _two_bore_collection():
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    a = Borehole(eno=1, name="BH1", longitude=149.1, latitude=-35.5)
    b = Borehole(eno=2, name="BH2", longitude=149.3, latitude=-35.2)
    return BoreholeCollection([a, b], region), a, b


def test_stratigraphy_geodataframe_rows_and_columns():
    coll, a, b = _two_bore_collection()
    a.set_stratigraphy([StratigraphyInterval.from_feature(STRAT_A),
                        StratigraphyInterval.from_feature(STRAT_B)])
    b.set_stratigraphy([])  # loaded, no intervals
    gdf = coll.stratigraphy_geodataframe()
    assert len(gdf) == 2  # only the two intervals on BH1
    assert gdf.crs.to_epsg() == 4283
    for col in ("source", "eno", "top_depth_m", "bottom_depth_m", "ref_elev_m_ahd",
                "unit", "comment", "valid", "invalid_reason", "geometry"):
        assert col in gdf.columns
    assert gdf.geometry.iloc[0].geom_type == "Point"
    assert set(gdf["unit"]) == {"Sand", "Clay"}
    assert gdf["ref_elev_m_ahd"].iloc[0] == 100.0


def test_earth_material_geodataframe_rows_and_columns():
    coll, a, b = _two_bore_collection()
    a.set_earth_material([])
    b.set_earth_material([EarthMaterialInterval.from_feature(EARTH_A)])
    gdf = coll.earth_material_geodataframe()
    assert len(gdf) == 1
    assert gdf.crs.to_epsg() == 4283
    for col in ("lithology", "lithology_group", "description",
                "earth_material_id", "geometry"):
        assert col in gdf.columns
    assert gdf["lithology"].iloc[0] == "gravel"


def test_stratigraphy_geodataframe_not_loaded_raises():
    coll, _, _ = _two_bore_collection()
    with pytest.raises(RuntimeError, match="stratigraphy not loaded"):
        coll.stratigraphy_geodataframe()


def test_earth_material_geodataframe_not_loaded_raises():
    coll, _, _ = _two_bore_collection()
    with pytest.raises(RuntimeError, match="earth_material not loaded"):
        coll.earth_material_geodataframe()


def test_stratigraphy_geodataframe_loaded_but_empty():
    coll, a, b = _two_bore_collection()
    a.set_stratigraphy([])
    b.set_stratigraphy([])
    gdf = coll.stratigraphy_geodataframe()
    assert len(gdf) == 0
    assert gdf.crs.to_epsg() == 4283
    # Documented columns still present on the empty frame.
    for col in ("source", "eno", "top_depth_m", "unit", "comment", "valid"):
        assert col in gdf.columns


# -- construction export (NGIS-only) ------------------------------------

from austrata.domain.construction import ConstructionInterval  # noqa: E402

CONSTRUCTION_A = {"BOREHOLE_NAME": "GW1", "INTERVAL_BEGIN_M": 30,
                  "INTERVAL_END_M": 42, "CONSTRUCTION_TYPE": "Screen",
                  "MATERIAL": "PVC", "DEPTH_REF_POINT_ELEV_M_AHD": 120}


def _ngis_two_bore_collection():
    region = Region.from_bbox(143.0, -36.0, 146.0, -34.0)
    a = Borehole(eno=None, name="GW1", longitude=143.5, latitude=-35.0,
                 source="NGIS:NSW")
    b = Borehole(eno=None, name="GW2", longitude=144.0, latitude=-34.5,
                 source="NGIS:NSW")
    return BoreholeCollection([a, b], region), a, b


def test_construction_geodataframe_rows_and_source_column():
    coll, a, b = _ngis_two_bore_collection()
    a.set_construction([ConstructionInterval.from_feature(CONSTRUCTION_A)])
    b.set_construction([])
    gdf = coll.construction_geodataframe()
    assert len(gdf) == 1
    assert gdf.crs.to_epsg() == 4283
    for col in ("source", "construction_type", "material", "inner_diameter",
                "property", "drill_method", "geometry"):
        assert col in gdf.columns
    assert gdf["source"].iloc[0] == "NGIS:NSW"
    assert gdf["construction_type"].iloc[0] == "Screen"


def test_construction_geodataframe_not_loaded_raises():
    coll, _, _ = _ngis_two_bore_collection()
    with pytest.raises(RuntimeError, match="construction not loaded"):
        coll.construction_geodataframe()


def test_construction_geodataframe_loaded_but_empty():
    coll, a, b = _ngis_two_bore_collection()
    a.set_construction([])
    b.set_construction([])
    gdf = coll.construction_geodataframe()
    assert len(gdf) == 0
    assert gdf.crs.to_epsg() == 4283
    for col in ("source", "construction_type", "material", "valid"):
        assert col in gdf.columns
