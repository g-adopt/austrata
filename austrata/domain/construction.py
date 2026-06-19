"""The NGIS-only :class:`ConstructionInterval` value object.

Construction logs (screens / casing) have no Geoscience Australia WFS equivalent,
so this object is populated only from the NGIS ``NGIS_ConstructionLog`` table. It
shares the depth contract and the null/invalid-interval policy of the
stratigraphy and earth-material intervals (see :mod:`austrata.domain.stratigraphy`):
depths are metres below the depth reference point, ``top`` shallower than
``bottom``, and ``from_feature`` never raises — it flags ``valid``/
``invalid_reason`` instead.

Like the other interval objects it carries a ``source_attributes`` dict holding
the entire original record verbatim (``compare=False`` so the frozen object stays
hashable/equal on its real fields).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from austrata.domain.coercion import to_float as _to_float
from austrata.domain.coercion import to_str as _to_str
from austrata.domain.stratigraphy import _check_interval


@dataclass(frozen=True)
class ConstructionInterval:
    """One construction (screen / casing) interval for a borehole.

    Fields map from the NGIS ``NGIS_ConstructionLog`` table.
    """

    eno: Optional[int]
    borehole_name: Optional[str]
    borehole_pid: Optional[str]
    top_depth: Optional[float]
    bottom_depth: Optional[float]
    construction_type: Optional[str]
    material: Optional[str]
    inner_diameter: Optional[float]
    outer_diameter: Optional[float]
    property: Optional[str]
    property_size: Optional[float]
    drill_method: Optional[str]
    ref_elevation_m_ahd: Optional[float]
    top_elev_m_ahd: Optional[float]
    bottom_elev_m_ahd: Optional[float]
    valid: bool
    invalid_reason: Optional[str] = None
    source_attributes: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_feature(cls, properties: dict) -> "ConstructionInterval":
        """Build from a feature's ``properties`` dict (NGIS construction keys)."""
        p = properties or {}
        top = _to_float(p.get("INTERVAL_BEGIN_M"))
        bottom = _to_float(p.get("INTERVAL_END_M"))
        reason = _check_interval(top, bottom)
        eno = _to_float(p.get("ENO"))
        return cls(
            eno=int(eno) if eno is not None else None,
            borehole_name=_to_str(p.get("BOREHOLE_NAME")),
            borehole_pid=_to_str(p.get("BOREHOLE_PID")),
            top_depth=top,
            bottom_depth=bottom,
            construction_type=_to_str(p.get("CONSTRUCTION_TYPE")),
            material=_to_str(p.get("MATERIAL")),
            inner_diameter=_to_float(p.get("INNER_DIAMETER")),
            outer_diameter=_to_float(p.get("OUTER_DIAMETER")),
            property=_to_str(p.get("PROPERTY")),
            property_size=_to_float(p.get("PROPERTY_SIZE")),
            drill_method=_to_str(p.get("DRILL_METHOD")),
            ref_elevation_m_ahd=_to_float(p.get("DEPTH_REF_POINT_ELEV_M_AHD")),
            top_elev_m_ahd=_to_float(p.get("INTERVAL_BEGIN_ELEV_M_AHD")),
            bottom_elev_m_ahd=_to_float(p.get("INTERVAL_END_ELEV_M_AHD")),
            valid=reason is None,
            invalid_reason=reason,
            source_attributes=dict(p),
        )
