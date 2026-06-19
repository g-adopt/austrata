"""NGIS fast-DB GeoDataFrames → domain objects (the NGIS analogue of
``feature_mapper.py``).

The optimiser keeps every NGIS column verbatim; this mapper applies the *curated*
NGIS→domain field mapping (the tables in ``data/ngis/NGIS_SCHEMA.md``) by
constructing domain objects directly. The full original record rides along in
each object's ``source_attributes`` (the raw bag), so nothing is lost.

Two columns are optimiser-added, not native NGIS, so they are stripped from the
raw bag: the ``geometry`` column everywhere, and — on the *log* layers only — the
denormalised ``Longitude``/``Latitude`` (the bore carries those natively, the log
rows do not). The bores layer's ``Longitude``/``Latitude`` ARE native and stay.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import geopandas as gpd

from austrata.domain.borehole import Borehole, BoreholeCollection
from austrata.domain.construction import ConstructionInterval
from austrata.domain.region import Region
from austrata.domain.stratigraphy import EarthMaterialInterval, StratigraphyInterval
from austrata.domain.coercion import to_float as _f
from austrata.domain.coercion import to_str as _s

_JOIN = "HydroCode"
#: Optimiser-added columns to drop from the raw bag (logs strip lon/lat too).
_DROP_BORE = ("geometry",)
_DROP_LOG = ("geometry", "Longitude", "Latitude")


def ngis_bores_to_collection(
    bores_gdf: gpd.GeoDataFrame, region: Region, source: str
) -> BoreholeCollection:
    """Map the bores fast-DB frame to a :class:`BoreholeCollection` (one per row)."""
    # The state code comes from the queried source tag ("NGIS:NSW" -> "NSW"), not
    # from NGIS_Bore.StateTerritory — that field is a coded int, not a state name
    # (the raw value still rides in source_attributes).
    state = source.split(":")[-1] if ":" in source else None
    boreholes = [_bore(_row_dict(row), source, state) for _, row in bores_gdf.iterrows()]
    return BoreholeCollection(boreholes, region)


def ngis_stratigraphy(strat_gdf: gpd.GeoDataFrame) -> Dict[str, List[StratigraphyInterval]]:
    """``HydroCode`` -> stratigraphy intervals (Unknown formation kept, flagged)."""
    return _group(strat_gdf, _stratigraphy)


def ngis_earth_material(em_gdf: gpd.GeoDataFrame) -> Dict[str, List[EarthMaterialInterval]]:
    """``HydroCode`` -> earth-material intervals."""
    return _group(em_gdf, _earth_material)


def ngis_construction(con_gdf: gpd.GeoDataFrame) -> Dict[str, List[ConstructionInterval]]:
    """``HydroCode`` -> construction intervals."""
    return _group(con_gdf, _construction)


# -- per-row builders (curated mapping per NGIS_SCHEMA.md) --------------


def _bore(p: dict, source: str, state: Optional[str]) -> Borehole:
    return Borehole(
        eno=None,                                  # NGIS has no ENO
        name=_s(p.get("StateBoreID")),
        longitude=_f(p.get("Longitude")),
        latitude=_f(p.get("Latitude")),
        identifier=_s(p.get(_JOIN)),               # HydroCode is the identity
        elevation_m=_f(p.get("RefElev")),
        state=state,                               # from the queried source tag
        purpose=_s(p.get("FType")),
        status=_s(p.get("Status")),
        data_custodian=_s(p.get("Agency")),
        depth_reference=_s(p.get("RefElevDesc")),
        source=source,
        bore_depth_m=_f(p.get("BoreDepth")),
        drilled_depth_m=_f(p.get("DrilledDepth")),
        drilled_date=_s(p.get("DrilledDate")),
        source_attributes=_bag(p, _DROP_BORE),
    )


def _stratigraphy(p: dict) -> StratigraphyInterval:
    top, bottom = _f(p.get("FromDepth")), _f(p.get("ToDepth"))
    reason = _depth_or_unknown(top, bottom, _s(p.get("Description")))
    return StratigraphyInterval(
        eno=None,
        borehole_name=_s(p.get(_JOIN)),
        borehole_pid=None,
        top_depth=top,
        bottom_depth=bottom,
        unit=_s(p.get("Description")),
        unit_pid=None, older_age=None, younger_age=None,
        older_age_ma=None, younger_age_ma=None,
        top_contact=None, base_contact=None, geological_province=None,
        ref_elevation_m_ahd=_f(p.get("RefElev")),
        top_elev_m_ahd=_f(p.get("TopElev")),
        bottom_elev_m_ahd=_f(p.get("BottomElev")),
        stratigraphy_id=None,
        valid=reason is None,
        invalid_reason=reason,
        comment=_s(p.get("Comment")),
        source_attributes=_bag(p, _DROP_LOG),
    )


def _earth_material(p: dict) -> EarthMaterialInterval:
    top, bottom = _f(p.get("FromDepth")), _f(p.get("ToDepth"))
    reason = _check_depth(top, bottom)
    return EarthMaterialInterval(
        eno=None,
        borehole_name=_s(p.get(_JOIN)),
        borehole_pid=None,
        top_depth=top,
        bottom_depth=bottom,
        lithology=None,                            # NGIS gives coded major/minor
        lithology_group=_s(p.get("MajorLithCode")),
        lithology_qualifier=_s(p.get("MinorLithCode")),
        description=_s(p.get("Description")),
        geological_province=None,
        ref_elevation_m_ahd=_f(p.get("RefElev")),
        top_elev_m_ahd=_f(p.get("TopElev")),
        bottom_elev_m_ahd=_f(p.get("BottomElev")),
        earth_material_id=None,
        valid=reason is None,
        invalid_reason=reason,
        source_attributes=_bag(p, _DROP_LOG),
    )


def _construction(p: dict) -> ConstructionInterval:
    top, bottom = _f(p.get("FromDepth")), _f(p.get("ToDepth"))
    reason = _check_depth(top, bottom)
    return ConstructionInterval(
        eno=None,
        borehole_name=_s(p.get(_JOIN)),
        borehole_pid=None,
        top_depth=top,
        bottom_depth=bottom,
        construction_type=_s(p.get("ConstructionType")),
        material=_s(p.get("Material")),
        inner_diameter=_f(p.get("InnerDiameter")),
        outer_diameter=_f(p.get("OuterDiameter")),
        property=_s(p.get("Property")),
        property_size=_f(p.get("PropertySize")),
        drill_method=_s(p.get("DrillMethod")),
        ref_elevation_m_ahd=_f(p.get("RefElev")),
        top_elev_m_ahd=_f(p.get("TopElev")),
        bottom_elev_m_ahd=_f(p.get("BottomElev")),
        valid=reason is None,
        invalid_reason=reason,
        source_attributes=_bag(p, _DROP_LOG),
    )


# -- helpers ------------------------------------------------------------


def _group(gdf: gpd.GeoDataFrame, build) -> Dict[str, list]:
    """Group built intervals by ``HydroCode`` (skipping rows with no code)."""
    out: Dict[str, list] = {}
    for _, row in gdf.iterrows():
        p = _row_dict(row)
        code = _s(p.get(_JOIN))
        if code is None:
            continue
        out.setdefault(code, []).append(build(p))
    return out


def _row_dict(row) -> dict:
    """A plain dict for one frame row (geometry kept here; bag-stripping is later)."""
    return dict(row)


def _bag(p: dict, drop) -> dict:
    """The verbatim record minus the optimiser-added columns in ``drop``."""
    return {k: v for k, v in p.items() if k not in drop}


def _check_depth(top, bottom):
    """Depth-only invalid reason (same policy as the domain interval objects)."""
    if top is None or bottom is None:
        return "missing depth"
    if top > bottom:
        return "inverted interval (top > bottom)"
    return None


def _depth_or_unknown(top, bottom, unit):
    """Stratigraphy reason: depth invalidity takes precedence over Unknown unit."""
    reason = _check_depth(top, bottom)
    if reason is not None:
        return reason
    if unit is not None and unit.strip().lower() == "unknown":
        return "unknown formation"
    return None
