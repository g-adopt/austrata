"""Offline tests for the NGIS→domain mapper and the NGISClient facade.

A real synthetic .gdb (fiona-written) + tmp_path cache/ngis_dir + a monkeypatched
'TAS' registry entry drive the whole stack: load_ngis_frames → mapper → client.
"""
import fiona
import geopandas as gpd
import pytest
from fiona.crs import CRS
from shapely.geometry import Point

from austrata.domain.region import Region
from austrata.infrastructure import ngis_download, ngis_sources
from austrata.infrastructure.ngis_mapper import (
    ngis_bores_to_collection,
    ngis_construction,
    ngis_earth_material,
    ngis_stratigraphy,
)
from austrata.ngis_client import NGISClient

_ALBERS = CRS.from_epsg(3577)
# Two bores: GW1 inside the test box, GW2 outside it.
_BOX = (143.0, -36.0, 144.0, -34.0)


def _write_gdb(path):
    # StateTerritory is a coded int in real NGIS data, not a state name.
    bore_schema = {"geometry": "Point", "properties": {
        "HydroCode": "str", "Longitude": "float", "Latitude": "float",
        "RefElev": "float", "RefElevDesc": "str", "Status": "str", "FType": "str",
        "StateBoreID": "str", "BoreDepth": "float", "DrilledDepth": "float",
        "DrilledDate": "str", "Agency": "str", "StateTerritory": "int"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_Bore",
                    schema=bore_schema, crs=_ALBERS) as c:
        c.write({"geometry": {"type": "Point", "coordinates": (1e6, -3e6)},
                 "properties": {"HydroCode": "GW1", "Longitude": 143.5, "Latitude": -35.0,
                                "RefElev": 100.0, "RefElevDesc": "natural surface",
                                "Status": "Operating", "FType": "Water Bore",
                                "StateBoreID": "B1", "BoreDepth": 200.0,
                                "DrilledDepth": 195.0, "DrilledDate": "1998-04-01",
                                "Agency": "NSW Water", "StateTerritory": 1}})
        c.write({"geometry": {"type": "Point", "coordinates": (2e6, -2e6)},
                 "properties": {"HydroCode": "GW2", "Longitude": 148.0, "Latitude": -33.0,
                                "RefElev": 80.0, "RefElevDesc": "natural surface",
                                "Status": "Operating", "FType": "Water Bore",
                                "StateBoreID": "B2", "BoreDepth": 50.0,
                                "DrilledDepth": 48.0, "DrilledDate": "2001-01-01",
                                "Agency": "NSW Water", "StateTerritory": 1}})
        # GW3 sits exactly on the box's east edge (lon == max_lon) — it must be
        # INCLUDED (edge-inclusive, matching GA's WFS bbox).
        c.write({"geometry": {"type": "Point", "coordinates": (1.5e6, -3e6)},
                 "properties": {"HydroCode": "GW3", "Longitude": 144.0, "Latitude": -35.0,
                                "RefElev": 70.0, "RefElevDesc": "natural surface",
                                "Status": "Operating", "FType": "Water Bore",
                                "StateBoreID": "B3", "BoreDepth": 60.0,
                                "DrilledDepth": 58.0, "DrilledDate": "2005-01-01",
                                "Agency": "NSW Water", "StateTerritory": 1}})

    strat_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "Description": "str", "FromDepth": "float",
        "ToDepth": "float", "TopElev": "float", "BottomElev": "float",
        "RefElev": "float", "Comment": "str"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_BoreholeLog",
                    schema=strat_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "Description": "Shepparton Formation",
            "FromDepth": 0.0, "ToDepth": 5.0, "TopElev": 100.0,
            "BottomElev": 95.0, "RefElev": 100.0, "Comment": "top unit"}})
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "Description": "Unknown",
            "FromDepth": 5.0, "ToDepth": 9.0, "TopElev": 95.0,
            "BottomElev": 91.0, "RefElev": 100.0, "Comment": None}})

    lith_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "MajorLithCode": "str", "MinorLithCode": "str",
        "Description": "str", "FromDepth": "float", "ToDepth": "float", "RefElev": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_LithologyLog",
                    schema=lith_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "MajorLithCode": "CLAY", "MinorLithCode": "SAND",
            "Description": "sandy clay", "FromDepth": 0.0, "ToDepth": 2.0, "RefElev": 100.0}})

    cons_schema = {"geometry": "None", "properties": {
        "HydroCode": "str", "ConstructionType": "str", "Material": "str",
        "InnerDiameter": "float", "OuterDiameter": "float", "Property": "str",
        "PropertySize": "float", "DrillMethod": "str", "FromDepth": "float",
        "ToDepth": "float", "RefElev": "float"}}
    with fiona.open(path, "w", driver="OpenFileGDB", layer="NGIS_ConstructionLog",
                    schema=cons_schema) as c:
        c.write({"geometry": None, "properties": {
            "HydroCode": "GW1", "ConstructionType": "Screen", "Material": "PVC",
            "InnerDiameter": 100.0, "OuterDiameter": 110.0, "Property": "Slotted",
            "PropertySize": 0.5, "DrillMethod": "Rotary", "FromDepth": 30.0,
            "ToDepth": 42.0, "RefElev": 100.0}})


@pytest.fixture
def synthetic_gdb(tmp_path):
    path = tmp_path / "synthetic.gdb"
    _write_gdb(str(path))
    return str(path)


@pytest.fixture
def fake_tas(monkeypatch):
    src = ngis_sources.NgisSource(
        state="TAS", vintage="2014-test", dataset_url="https://data.gov.au/test",
        resource_url="https://x/y.zip", mirror_url="https://m/y.zip",
        zip_bytes=1, zip_md5="md5aaa", gdb_relpath="x.gdb")
    monkeypatch.setitem(ngis_sources.NGIS_SOURCES, "TAS", src)
    return src


@pytest.fixture
def client(tmp_path, synthetic_gdb, fake_tas, monkeypatch):
    """An NGISClient whose ensure_gdb returns the synthetic gdb (no download)."""
    monkeypatch.setattr(ngis_download, "ensure_gdb",
                        lambda state, **kw: synthetic_gdb)
    return NGISClient(ngis_dir=tmp_path / "ngis", cache_dir=tmp_path / "cache")


# -- mapper field fidelity ----------------------------------------------


def _bores_frame():
    df = gpd.GeoDataFrame(
        [{"HydroCode": "GW1", "Longitude": 143.5, "Latitude": -35.0,
          "RefElev": 100.0, "RefElevDesc": "natural surface", "Status": "Operating",
          "FType": "Water Bore", "StateBoreID": "B1", "BoreDepth": 200.0,
          "DrilledDepth": 195.0, "DrilledDate": "1998-04-01", "Agency": "NSW Water",
          # StateTerritory is a CODED INT in real NGIS data, not a state name; a
          # non-"NSW" value here would expose any regression that reads it.
          "StateTerritory": 1}],
        geometry=[Point(143.5, -35.0)], crs="EPSG:4283")
    return df


def test_bore_curated_mapping_and_raw_bag():
    region = Region.from_bbox(*_BOX)
    coll = ngis_bores_to_collection(_bores_frame(), region, "NGIS:NSW")
    b = coll[0]
    assert b.eno is None
    assert b.identifier == "GW1"
    assert b.name == "B1"
    assert b.longitude == 143.5 and b.latitude == -35.0
    assert b.elevation_m == 100.0
    assert b.depth_reference == "natural surface"
    assert b.status == "Operating"
    assert b.purpose == "Water Bore"
    assert b.bore_depth_m == 200.0 and b.drilled_depth_m == 195.0
    assert b.drilled_date == "1998-04-01"
    assert b.data_custodian == "NSW Water"
    # b.state comes from the queried source tag, NOT the coded StateTerritory int.
    assert b.state == "NSW"
    assert b.source == "NGIS:NSW"
    # Raw bag verbatim, minus the optimiser-added geometry; native lon/lat KEPT.
    assert "geometry" not in b.source_attributes
    assert b.source_attributes["Longitude"] == 143.5
    assert b.source_attributes["HydroCode"] == "GW1"
    # The coded StateTerritory survives verbatim in the raw bag.
    assert b.source_attributes["StateTerritory"] == 1


def test_stratigraphy_mapping_unknown_kept_flagged():
    strat = gpd.GeoDataFrame(
        [{"HydroCode": "GW1", "Description": "Shepparton Formation", "FromDepth": 0.0,
          "ToDepth": 5.0, "TopElev": 100.0, "BottomElev": 95.0, "RefElev": 100.0,
          "Comment": "note", "Longitude": 143.5, "Latitude": -35.0},
         {"HydroCode": "GW1", "Description": "Unknown", "FromDepth": 5.0, "ToDepth": 9.0,
          "TopElev": 95.0, "BottomElev": 91.0, "RefElev": 100.0, "Comment": None,
          "Longitude": 143.5, "Latitude": -35.0}],
        geometry=[Point(143.5, -35.0)] * 2, crs="EPSG:4283")
    by_code = ngis_stratigraphy(strat)
    ivs = by_code["GW1"]
    assert len(ivs) == 2
    a, u = ivs
    assert a.unit == "Shepparton Formation" and a.eno is None
    assert a.top_depth == 0.0 and a.bottom_depth == 5.0
    assert a.top_elev_m_ahd == 100.0 and a.bottom_elev_m_ahd == 95.0
    assert a.ref_elevation_m_ahd == 100.0 and a.comment == "note"
    assert a.valid is True
    # Unknown row kept but flagged.
    assert u.unit == "Unknown"
    assert u.valid is False and u.invalid_reason == "unknown formation"
    # Log raw bag strips optimiser-added geometry + denormalised lon/lat.
    for k in ("geometry", "Longitude", "Latitude"):
        assert k not in a.source_attributes
    assert a.source_attributes["RefElev"] == 100.0


def test_earth_material_and_construction_mapping():
    em = gpd.GeoDataFrame(
        [{"HydroCode": "GW1", "MajorLithCode": "CLAY", "MinorLithCode": "SAND",
          "Description": "sandy clay", "FromDepth": 0.0, "ToDepth": 2.0,
          "RefElev": 100.0, "Longitude": 143.5, "Latitude": -35.0}],
        geometry=[Point(143.5, -35.0)], crs="EPSG:4283")
    e = ngis_earth_material(em)["GW1"][0]
    assert e.lithology is None
    assert e.lithology_group == "CLAY" and e.lithology_qualifier == "SAND"
    assert e.description == "sandy clay"

    con = gpd.GeoDataFrame(
        [{"HydroCode": "GW1", "ConstructionType": "Screen", "Material": "PVC",
          "InnerDiameter": 100.0, "OuterDiameter": 110.0, "Property": "Slotted",
          "PropertySize": 0.5, "DrillMethod": "Rotary", "FromDepth": 30.0,
          "ToDepth": 42.0, "RefElev": 100.0, "Longitude": 143.5, "Latitude": -35.0}],
        geometry=[Point(143.5, -35.0)], crs="EPSG:4283")
    c = ngis_construction(con)["GW1"][0]
    assert c.construction_type == "Screen" and c.material == "PVC"
    assert c.inner_diameter == 100.0 and c.outer_diameter == 110.0
    assert c.property == "Slotted" and c.property_size == 0.5
    assert c.drill_method == "Rotary"


def test_unknown_with_bad_depth_prefers_depth_reason():
    strat = gpd.GeoDataFrame(
        [{"HydroCode": "GW1", "Description": "Unknown", "FromDepth": 9.0,
          "ToDepth": 5.0, "RefElev": 100.0}],
        geometry=[Point(143.5, -35.0)], crs="EPSG:4283")
    iv = ngis_stratigraphy(strat)["GW1"][0]
    assert iv.valid is False
    assert "inverted" in iv.invalid_reason  # depth precedence over "unknown"


# -- NGISClient end to end ----------------------------------------------


def _by_code(bc):
    return {b.identifier: b for b in bc}


def test_client_boreholes_filters_to_box_and_tags_source(client):
    bc = client.boreholes("TAS", bbox=_BOX)
    codes = {b.identifier for b in bc}
    # GW1 inside; GW3 sits exactly on the east edge (edge-INCLUSIVE, like GA's
    # WFS bbox); GW2 (148E/-33) is well outside.
    assert codes == {"GW1", "GW3"}
    for b in bc:
        assert b.source == "NGIS:TAS"
        assert b.state == "TAS"  # from the source tag, not coded StateTerritory


def test_client_load_logs_all_three_kinds(client):
    bc = client.boreholes("TAS", bbox=_BOX)
    bc.load_logs("stratigraphy")
    bc.load_logs("earth_material")
    bc.load_logs("construction")
    b = _by_code(bc)["GW1"]
    assert len(b.stratigraphy) == 2
    assert len(b.earth_material) == 1
    assert len(b.construction) == 1
    # GW3 is in-box but has no logs -> loaded-but-empty, not raising.
    assert _by_code(bc)["GW3"].stratigraphy == []


def test_client_export_frames_have_source_and_ahd(client):
    bc = client.boreholes("TAS", bbox=_BOX)
    bc.load_logs("stratigraphy")
    gdf = bc.stratigraphy_geodataframe()
    assert len(gdf) == 2  # GW1's two intervals; GW3 has none
    assert gdf.crs.to_epsg() == 4283
    for col in ("source", "unit", "comment", "top_elev_m_ahd", "bottom_elev_m_ahd"):
        assert col in gdf.columns
    assert set(gdf["source"]) == {"NGIS:TAS"}
    assert gdf["top_elev_m_ahd"].iloc[0] == 100.0

    bc.load_logs("construction")
    cgdf = bc.construction_geodataframe()
    assert cgdf["source"].iloc[0] == "NGIS:TAS"
    assert cgdf["construction_type"].iloc[0] == "Screen"


def test_client_provenance_surfaces_ngis_source(client):
    bc = client.boreholes("TAS", bbox=_BOX)
    prov = bc.provenance()
    assert "NGIS" in (prov.get("service_version") or "")
    assert prov.get("license") == "CC BY 4.0"
    cite = bc.citation()
    assert isinstance(cite, str) and len(cite) > 0
