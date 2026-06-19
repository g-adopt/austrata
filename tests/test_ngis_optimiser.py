"""Offline tests for optimise_state, exercised against a REAL synthetic .gdb.

fiona's OpenFileGDB driver is write-capable here, so the fixture writes a genuine
multi-layer File Geodatabase in tmp_path (bores + the three log tables, including
a non-spatial table, an "Unknown" formation row, and an orphan log row with no
matching bore). This exercises the actual fiona reader, not a mock.
"""
import fiona
import pandas as pd
import pytest
from fiona.crs import CRS

from austrata.infrastructure import ngis_optimiser, ngis_sources
from austrata.infrastructure.ngis_optimiser import (
    OPTIMISER_VERSION,
    build_stamp,
    optimise_state,
)

# Albers (3577) CRS on the bores layer, as the real cores carry — proves we read
# lon/lat from the attribute columns, not this projected geometry.
_ALBERS = CRS.from_epsg(3577)


def _write_gdb(path):
    bore_schema = {
        "geometry": "Point",
        "properties": {
            "HydroCode": "str", "Longitude": "float", "Latitude": "float",
            "RefElev": "float", "StateBoreID": "str",
        },
    }
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_Bore",
                    schema=bore_schema, crs=_ALBERS) as c:
        c.write({"geometry": {"type": "Point", "coordinates": (1.0e6, -3.0e6)},
                 "properties": {"HydroCode": "GW1", "Longitude": 143.5,
                                "Latitude": -35.0, "RefElev": 100.0, "StateBoreID": "B1"}})
        c.write({"geometry": {"type": "Point", "coordinates": (1.1e6, -3.1e6)},
                 "properties": {"HydroCode": "GW2", "Longitude": 144.0,
                                "Latitude": -34.5, "RefElev": 80.0, "StateBoreID": "B2"}})

    strat_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "Description": "str", "FromDepth": "float",
        "ToDepth": "float", "TopElev": "float", "BottomElev": "float", "Comment": "str"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_BoreholeLog",
                    schema=strat_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "Description": "Shepparton Formation",
            "FromDepth": 0.0, "ToDepth": 5.0, "TopElev": 100.0,
            "BottomElev": 95.0, "Comment": "top unit"}})
        # "Unknown" formation row — MUST survive (flagged invalid downstream).
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "Description": "Unknown",
            "FromDepth": 5.0, "ToDepth": 9.0, "TopElev": 95.0,
            "BottomElev": 91.0, "Comment": None}})
        # Orphan row — HydroCode has no matching bore; MUST survive (null geom).
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW_ORPHAN", "Description": "Calivil Formation",
            "FromDepth": 0.0, "ToDepth": 3.0, "TopElev": None,
            "BottomElev": None, "Comment": None}})

    lith_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "MajorLithCode": "str", "FromDepth": "float", "ToDepth": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_LithologyLog",
                    schema=lith_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW2", "MajorLithCode": "CLAY",
            "FromDepth": 0.0, "ToDepth": 2.0}})

    cons_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "ConstructionType": "str", "FromDepth": "float", "ToDepth": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_ConstructionLog",
                    schema=cons_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "ConstructionType": "Screen",
            "FromDepth": 30.0, "ToDepth": 42.0}})


@pytest.fixture
def synthetic_gdb(tmp_path):
    path = tmp_path / "synthetic_ngis.gdb"
    _write_gdb(str(path))
    return str(path)


@pytest.fixture
def fake_tas(monkeypatch):
    src = ngis_sources.NgisSource(
        state="TAS", vintage="2014-test", dataset_url="https://data.gov.au/test",
        resource_url="https://x/y.zip", mirror_url="https://m/y.zip",
        zip_bytes=1, zip_md5="deadbeefcafe", gdb_relpath="x.gdb")
    monkeypatch.setitem(ngis_sources.NGIS_SOURCES, "TAS", src)
    return src


def test_optimise_returns_all_four_layers(synthetic_gdb, fake_tas):
    frames = optimise_state(synthetic_gdb, "TAS")
    assert set(frames) == {"bores", "stratigraphy", "earth_material", "construction"}


def test_bores_frame_lonlat_geometry_and_all_columns(synthetic_gdb, fake_tas):
    bores = optimise_state(synthetic_gdb, "TAS")["bores"]
    assert bores.crs.to_epsg() == 4283
    # Every original NGIS_Bore column carried verbatim.
    for col in ("HydroCode", "Longitude", "Latitude", "RefElev", "StateBoreID"):
        assert col in bores.columns
    pt = bores.iloc[0].geometry
    # Geometry is lon/lat from the attributes, NOT the projected Albers SHAPE.
    assert pt.geom_type == "Point"
    assert abs(pt.x - 143.5) < 1e-9 and abs(pt.y + 35.0) < 1e-9


def test_log_frame_has_verbatim_cols_plus_denormalised_lonlat(synthetic_gdb, fake_tas):
    strat = optimise_state(synthetic_gdb, "TAS")["stratigraphy"]
    assert strat.crs.to_epsg() == 4283
    # Verbatim NGIS log columns preserved (not renamed to GA keys).
    for col in ("HydroCode", "Description", "FromDepth", "ToDepth",
                "TopElev", "BottomElev", "Comment"):
        assert col in strat.columns
    # Denormalised bore lon/lat present.
    assert "Longitude" in strat.columns and "Latitude" in strat.columns
    gw1 = strat[strat["HydroCode"] == "GW1"].iloc[0]
    assert abs(gw1["Longitude"] - 143.5) < 1e-9
    assert gw1.geometry.geom_type == "Point"


def test_unknown_formation_row_survives(synthetic_gdb, fake_tas):
    strat = optimise_state(synthetic_gdb, "TAS")["stratigraphy"]
    assert (strat["Description"] == "Unknown").sum() == 1


def test_orphan_log_row_survives_with_null_geometry(synthetic_gdb, fake_tas):
    strat = optimise_state(synthetic_gdb, "TAS")["stratigraphy"]
    orphan = strat[strat["HydroCode"] == "GW_ORPHAN"]
    assert len(orphan) == 1
    row = orphan.iloc[0]
    assert row.geometry is None
    # lon/lat null (pandas coerces None to NaN in a float column).
    assert pd.isna(row["Longitude"]) and pd.isna(row["Latitude"])


def test_stamp_fields(fake_tas):
    stamp = build_stamp("TAS")
    assert stamp["state"] == "TAS"
    assert stamp["vintage"] == "2014-test"
    assert stamp["gdb_md5"] == "deadbeefcafe"
    assert stamp["optimiser_version"] == OPTIMISER_VERSION


def test_missing_bore_layer_raises(tmp_path, fake_tas):
    path = tmp_path / "nobores.gdb"
    schema = {"geometry": "None", "properties": {"HydroCode": "str"}}
    with fiona.open(str(path), "w", driver="OpenFileGDB",
                    layer="NGIS_BoreholeLog", schema=schema) as c:
        c.write({"geometry": None, "properties": {"HydroCode": "GW1"}})
    with pytest.raises(RuntimeError, match="no NGIS_Bore layer"):
        optimise_state(str(path), "TAS")


def test_absent_optional_layer_omitted(tmp_path, fake_tas):
    """A gdb with only bores + strat yields just those keys (no crash)."""
    path = tmp_path / "partial.gdb"
    bore_schema = {"geometry": "Point", "properties": {
        "HydroCode": "str", "Longitude": "float", "Latitude": "float"}}
    with fiona.open(str(path), "w", driver="OpenFileGDB", layer="NGIS_Bore",
                    schema=bore_schema, crs=_ALBERS) as c:
        c.write({"geometry": {"type": "Point", "coordinates": (0.0, 0.0)},
                 "properties": {"HydroCode": "GW1", "Longitude": 145.0, "Latitude": -33.0}})
    frames = optimise_state(str(path), "TAS")
    assert set(frames) == {"bores"}
    assert ngis_optimiser  # module import reference (silences linters)
