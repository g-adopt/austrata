"""The :class:`HydrogeologyUnit` value object.

Maps one polygon feature from the ArcGIS ``Hydrogeology_of_Australia`` layer.
The service uses terse, lowercase, truncated field names (``distbn``,
``prodty``, ``aquif_ty``); ``from_feature`` normalises them to readable names.
The geometry itself is kept on the GeoDataFrame the application layer builds,
not on this value object, which carries only the attribute payload plus the
stable ``ufi`` identifier.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from gadata.domain.coercion import to_str as _to_str


@dataclass(frozen=True)
class HydrogeologyUnit:
    """Attributes of one hydrogeology polygon (geometry held separately)."""

    feature: Optional[str]
    type: Optional[str]
    distribution: Optional[str]
    productivity: Optional[str]
    aquifer_type: Optional[str]
    ufi: Optional[str]

    @classmethod
    def from_feature(cls, properties: dict) -> "HydrogeologyUnit":
        """Build from a GeoJSON feature's ``properties`` dict (terse ArcGIS keys)."""
        p = properties or {}
        return cls(
            feature=_to_str(p.get("feature")),
            type=_to_str(p.get("type")),
            distribution=_to_str(p.get("distbn")),
            productivity=_to_str(p.get("prodty")),
            aquifer_type=_to_str(p.get("aquif_ty")),
            ufi=_to_str(p.get("ufi")),
        )
