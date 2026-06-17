"""GeoJSON feature <-> domain / GeoDataFrame mappers.

The cache stores GeoParquet, so the round trip is:
``features (GeoJSON dicts) -> GeoDataFrame (cached) -> domain objects``.
Mapping lives here so the adapters stay thin (raw features) and the cache stays
domain-agnostic (it only sees GeoDataFrames).

Headers and hydrogeology keep their geometry on the GeoDataFrame; log tables are
attribute-only (their point geometry duplicates the header location and is not
needed downhole), so they map to a plain table the domain reads as intervals.
"""
from __future__ import annotations

from typing import List, Optional

import geopandas as gpd
from shapely.geometry import Point, shape

from gadata.domain.borehole import Borehole, BoreholeCollection
from gadata.domain.region import Region
from gadata.domain.stratigraphy import EarthMaterialInterval, StratigraphyInterval

GDA94 = "EPSG:4283"


# -- boreholes ----------------------------------------------------------

def borehole_features_to_gdf(features: List[dict]) -> gpd.GeoDataFrame:
    """GeoJSON header features -> a GeoDataFrame of header attributes + points.

    The full property set is preserved as columns so the cached artifact is a
    faithful, queryable record; geometry comes from the feature point (falling
    back to the GDA94 property fields when absent).
    """
    records = []
    geoms = []
    for feat in features:
        props = dict(feat.get("properties") or {})
        records.append(props)
        geoms.append(_feature_point(feat, props))
    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=GDA94)
    return gdf


def _feature_point(feat: dict, props: dict) -> Optional[Point]:
    geom = feat.get("geometry")
    if geom and geom.get("type") == "Point":
        x, y = geom["coordinates"][0], geom["coordinates"][1]
        return Point(x, y)
    lon, lat = props.get("GDA94_dlong"), props.get("GDA94_dlat")
    if lon is not None and lat is not None:
        return Point(float(lon), float(lat))
    return None


def gdf_to_borehole_collection(gdf: gpd.GeoDataFrame, region: Region) -> BoreholeCollection:
    """A cached headers GeoDataFrame -> a domain ``BoreholeCollection``."""
    boreholes: List[Borehole] = []
    for _, row in gdf.iterrows():
        props = {k: row[k] for k in gdf.columns if k != "geometry"}
        geom = row.geometry
        geometry = None
        if geom is not None and not geom.is_empty:
            geometry = {"type": "Point", "coordinates": [geom.x, geom.y]}
        boreholes.append(Borehole.from_feature(props, geometry))
    return BoreholeCollection(boreholes, region)


# -- hydrogeology -------------------------------------------------------

def hydrogeology_features_to_gdf(features: List[dict]) -> gpd.GeoDataFrame:
    """GeoJSON polygon features -> a GeoDataFrame of attributes + polygons."""
    records = []
    geoms = []
    for feat in features:
        records.append(dict(feat.get("properties") or {}))
        geom = feat.get("geometry")
        geoms.append(shape(geom) if geom else None)
    return gpd.GeoDataFrame(records, geometry=geoms, crs=GDA94)


# -- logs ---------------------------------------------------------------

def log_features_to_dataframe(features: List[dict]) -> gpd.GeoDataFrame:
    """Log GeoJSON features -> a GeoDataFrame of attributes (geometry kept).

    Logs are attribute-only downhole, but the cache stores GeoParquet, so the
    result is a GeoDataFrame carrying the feature point (the header location,
    unused downhole) to keep the cache round-trip uniform across layers.
    """
    records = []
    geoms = []
    for feat in features:
        props = dict(feat.get("properties") or {})
        records.append(props)
        geoms.append(_feature_point(feat, props))
    return gpd.GeoDataFrame(records, geometry=geoms, crs=GDA94)


def gdf_to_records(gdf: gpd.GeoDataFrame) -> List[dict]:
    """A cached log GeoDataFrame -> a list of property dicts (drop geometry)."""
    cols = [c for c in gdf.columns if c != "geometry"]
    return [dict(row) for row in gdf[cols].to_dict(orient="records")]


def stratigraphy_from_records(records: List[dict]) -> List[StratigraphyInterval]:
    return [StratigraphyInterval.from_feature(r) for r in records]


def earth_material_from_records(records: List[dict]) -> List[EarthMaterialInterval]:
    return [EarthMaterialInterval.from_feature(r) for r in records]


def distribute_stratigraphy(collection: BoreholeCollection, records: List[dict]) -> None:
    """Group stratigraphy records by ENO and set them on each borehole."""
    by_eno = _group_by_eno(records)
    for bh in collection:
        rows = by_eno.get(bh.eno, [])
        bh.set_stratigraphy(stratigraphy_from_records(rows))


def distribute_earth_material(collection: BoreholeCollection, records: List[dict]) -> None:
    """Group earth-material records by ENO and set them on each borehole."""
    by_eno = _group_by_eno(records)
    for bh in collection:
        rows = by_eno.get(bh.eno, [])
        bh.set_earth_material(earth_material_from_records(rows))


def _group_by_eno(records: List[dict]) -> dict:
    grouped: dict = {}
    for r in records:
        raw = r.get("ENO")
        if raw is None:
            continue
        try:
            eno = int(float(raw))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(eno, []).append(r)
    return grouped
