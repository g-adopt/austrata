"""Downhole log interval value objects.

Two log layers are modelled: stratigraphy (``bh:BoreholeStratigraphyLogs``) and
earth material (``bh:BoreholeEarthMaterialLogs``). Both expose the same depth
contract and the same join keys; they differ in the geological payload.

Depth datum (pinned, per DESIGN's schema-pinning requirement)
-------------------------------------------------------------
* ``top_depth`` / ``bottom_depth`` are in **metres below the depth reference
  point** (``INTERVAL_BEGIN_M`` / ``INTERVAL_END_M`` from the service), with
  ``top`` shallower (smaller) than ``bottom``. The reference point elevation is
  carried as ``ref_elevation_m_ahd`` (``DEPTH_REF_POINT_ELEV_M_AHD``, metres AHD)
  so a consumer can convert to elevation if needed.
* ``top_elev_m_ahd`` / ``bottom_elev_m_ahd`` carry the per-interval top/base
  elevation in **metres AHD** (``INTERVAL_BEGIN_ELEV_M_AHD`` /
  ``INTERVAL_END_ELEV_M_AHD``). These are routinely null on the GA WFS but are
  populated by sources that provide them (e.g. the NGIS state cores, whose
  ``TopElev`` / ``BottomElev`` map onto these keys). They are what a consumer
  wants for building absolute layer geometry, so they are modelled here
  source-agnostically; fall back to ``ref_elevation_m_ahd - depth`` when null.

Null / invalid-interval policy (pinned)
---------------------------------------
``from_feature`` never raises on bad data; instead it sets the boolean ``valid``
flag and records ``invalid_reason``. An interval is flagged invalid when:
* either depth is missing/unparseable, or
* ``top_depth > bottom_depth`` (inverted).
A zero-thickness interval (``top == bottom``) is permitted and stays valid; some
logs legitimately record point observations. Callers that feed interpolation
should filter on ``valid`` so bad rows never poison downstream models.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from austrata.domain.coercion import to_float as _to_float
from austrata.domain.coercion import to_str as _to_str


def _check_interval(top: Optional[float], bottom: Optional[float]) -> Optional[str]:
    """Return an invalid-interval reason string, or ``None`` if the depths are ok."""
    if top is None or bottom is None:
        return "missing depth"
    if top > bottom:
        return "inverted interval (top > bottom)"
    return None


@dataclass(frozen=True)
class StratigraphyInterval:
    """One stratigraphy log interval for a borehole.

    Fields map from the UPPERCASE ``bh:BoreholeStratigraphyLogs`` properties.
    """

    eno: Optional[int]
    borehole_name: Optional[str]
    borehole_pid: Optional[str]
    top_depth: Optional[float]
    bottom_depth: Optional[float]
    unit: Optional[str]
    unit_pid: Optional[str]
    older_age: Optional[str]
    younger_age: Optional[str]
    older_age_ma: Optional[float]
    younger_age_ma: Optional[float]
    top_contact: Optional[str]
    base_contact: Optional[str]
    geological_province: Optional[str]
    ref_elevation_m_ahd: Optional[float]
    top_elev_m_ahd: Optional[float]
    bottom_elev_m_ahd: Optional[float]
    stratigraphy_id: Optional[int]
    valid: bool
    invalid_reason: Optional[str] = None
    comment: Optional[str] = None
    source_attributes: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_feature(cls, properties: dict) -> "StratigraphyInterval":
        """Build from a GeoJSON feature's ``properties`` dict (UPPERCASE keys)."""
        p = properties or {}
        top = _to_float(p.get("INTERVAL_BEGIN_M"))
        bottom = _to_float(p.get("INTERVAL_END_M"))
        reason = _check_interval(top, bottom)
        eno = _to_float(p.get("ENO"))
        sid = _to_float(p.get("STRATIGRAPHY_ID"))
        return cls(
            eno=int(eno) if eno is not None else None,
            borehole_name=_to_str(p.get("BOREHOLE_NAME")),
            borehole_pid=_to_str(p.get("BOREHOLE_PID")),
            top_depth=top,
            bottom_depth=bottom,
            unit=_to_str(p.get("STRAT_UNIT_NAME")),
            unit_pid=_to_str(p.get("STRAT_UNIT_PID")),
            older_age=_to_str(p.get("OLDER_AGE")),
            younger_age=_to_str(p.get("YOUNGER_AGE")),
            older_age_ma=_to_float(p.get("OLDER_AGE_MA")),
            younger_age_ma=_to_float(p.get("YOUNGER_AGE_MA")),
            top_contact=_to_str(p.get("TOP_CONTACT_NAME")),
            base_contact=_to_str(p.get("BASE_CONTACT_NAME")),
            geological_province=_to_str(p.get("GEOLOGICAL_PROVINCE")),
            ref_elevation_m_ahd=_to_float(p.get("DEPTH_REF_POINT_ELEV_M_AHD")),
            top_elev_m_ahd=_to_float(p.get("INTERVAL_BEGIN_ELEV_M_AHD")),
            bottom_elev_m_ahd=_to_float(p.get("INTERVAL_END_ELEV_M_AHD")),
            stratigraphy_id=int(sid) if sid is not None else None,
            valid=reason is None,
            invalid_reason=reason,
            comment=_to_str(p.get("COMMENT")),
            source_attributes=dict(p),
        )


@dataclass(frozen=True)
class EarthMaterialInterval:
    """One earth-material log interval for a borehole.

    Fields map from the UPPERCASE ``bh:BoreholeEarthMaterialLogs`` properties.
    """

    eno: Optional[int]
    borehole_name: Optional[str]
    borehole_pid: Optional[str]
    top_depth: Optional[float]
    bottom_depth: Optional[float]
    lithology: Optional[str]
    lithology_group: Optional[str]
    lithology_qualifier: Optional[str]
    description: Optional[str]
    geological_province: Optional[str]
    ref_elevation_m_ahd: Optional[float]
    top_elev_m_ahd: Optional[float]
    bottom_elev_m_ahd: Optional[float]
    earth_material_id: Optional[int]
    valid: bool
    invalid_reason: Optional[str] = None
    source_attributes: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_feature(cls, properties: dict) -> "EarthMaterialInterval":
        """Build from a GeoJSON feature's ``properties`` dict (UPPERCASE keys)."""
        p = properties or {}
        top = _to_float(p.get("INTERVAL_BEGIN_M"))
        bottom = _to_float(p.get("INTERVAL_END_M"))
        reason = _check_interval(top, bottom)
        eno = _to_float(p.get("ENO"))
        emid = _to_float(p.get("EARTH_MATERIAL_ID"))
        return cls(
            eno=int(eno) if eno is not None else None,
            borehole_name=_to_str(p.get("BOREHOLE_NAME")),
            borehole_pid=_to_str(p.get("BOREHOLE_PID")),
            top_depth=top,
            bottom_depth=bottom,
            lithology=_to_str(p.get("LITHOLOGY")),
            lithology_group=_to_str(p.get("LITHOLOGY_GROUP")),
            lithology_qualifier=_to_str(p.get("LITHOLOGY_QUALIFIER")),
            description=_to_str(p.get("EARTH_MATERIAL_DESC")),
            geological_province=_to_str(p.get("GEOLOGICAL_PROVINCE")),
            ref_elevation_m_ahd=_to_float(p.get("DEPTH_REF_POINT_ELEV_M_AHD")),
            top_elev_m_ahd=_to_float(p.get("INTERVAL_BEGIN_ELEV_M_AHD")),
            bottom_elev_m_ahd=_to_float(p.get("INTERVAL_END_ELEV_M_AHD")),
            earth_material_id=int(emid) if emid is not None else None,
            valid=reason is None,
            invalid_reason=reason,
            source_attributes=dict(p),
        )
