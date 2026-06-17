"""The :class:`Region` value object.

A region is a query footprint: an arbitrary shapely polygon (a bounding box is
just a rectangular polygon). It owns the backend-specific quirks the live
services impose so callers and the application layer never see them:

* WFS 2.0 wants the bbox as ``minLon,minLat,maxLon,maxLat,EPSG:4283``. Omitting
  the CRS suffix returns HTTP 400; the long ``urn:ogc:def:crs:EPSG::4283`` form
  silently flips to lat/lon axis order. The short EPSG form keeps lon/lat order.
* ArcGIS wants a structured ``geometry`` + ``geometryType`` intersect payload.
* The local cache keys entries on the region, so two callers passing the
  *same* footprint must collapse to one entry. We canonicalise the geometry
  (round coordinates, normalise, deterministic WKT) before hashing so that
  numerically-equal-but-textually-different geometries hash identically.

Everything here is GDA94 geographic (EPSG:4283), the lon/lat contract gadata
returns end to end; there is no reprojection.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Tuple

import shapely
from shapely.geometry import box as shapely_box
from shapely.geometry.base import BaseGeometry

#: EPSG code of the native CRS for both backends (GDA94 geographic, lon/lat).
GDA94 = 4283

#: Coordinate precision (decimal degrees) used when canonicalising for hashing.
#: ~6 dp ≈ 0.1 m, finer than any borehole positional accuracy GA reports.
_CACHE_PRECISION_DP = 6
_CACHE_GRID = 10 ** (-_CACHE_PRECISION_DP)


@dataclass(frozen=True)
class Region:
    """An immutable query footprint wrapping a shapely geometry in EPSG:4283."""

    geometry: BaseGeometry

    def __post_init__(self) -> None:
        if self.geometry is None or self.geometry.is_empty:
            raise ValueError("Region geometry must be a non-empty shapely geometry.")
        if not self.geometry.is_valid:
            raise ValueError("Region geometry is invalid (self-intersecting?).")

    # -- constructors ----------------------------------------------------

    @classmethod
    def from_bbox(cls, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> "Region":
        """Build a region from a lon/lat bounding box (a rectangular polygon)."""
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ValueError(
                f"Degenerate bbox: ({min_lon}, {min_lat}, {max_lon}, {max_lat}); "
                "expected min_lon < max_lon and min_lat < max_lat."
            )
        return cls(shapely_box(min_lon, min_lat, max_lon, max_lat))

    @classmethod
    def from_geometry(cls, geometry: BaseGeometry) -> "Region":
        """Build a region from any shapely geometry (convenience alias)."""
        return cls(geometry)

    # -- spatial accessors ----------------------------------------------

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        """``(min_lon, min_lat, max_lon, max_lat)`` envelope of the geometry."""
        return tuple(self.geometry.bounds)  # type: ignore[return-value]

    def is_rectangular(self) -> bool:
        """True when the geometry equals its own bounding box (a plain bbox)."""
        min_lon, min_lat, max_lon, max_lat = self.bounds
        return self.geometry.equals(shapely_box(min_lon, min_lat, max_lon, max_lat))

    # -- WFS (GeoServer / boreholes) ------------------------------------

    def wfs_bbox(self) -> str:
        """WFS 2.0 ``bbox`` value with explicit lon/lat CRS suffix.

        Returns ``"minLon,minLat,maxLon,maxLat,EPSG:4283"``. The CRS suffix is
        mandatory: omitting it 400s on the GA WFS, and the urn form flips axis
        order. WFS BBOX is always the envelope, so non-rectangular regions are
        coarsely pre-filtered here and refined downstream against the polygon.
        """
        min_lon, min_lat, max_lon, max_lat = self.bounds
        return f"{min_lon},{min_lat},{max_lon},{max_lat},EPSG:{GDA94}"

    # -- ArcGIS (Esri REST / hydrogeology) ------------------------------

    def arcgis_geometry(self) -> dict:
        """Structured ``geometry`` payload for an ArcGIS intersect query.

        Returns an envelope for rectangular regions (cheap, and all the WFS can
        do anyway) and a ring polygon otherwise. Pair with
        :meth:`arcgis_geometry_type` and ``inSR=4283`` / ``spatialRel=
        esriSpatialRelIntersects`` in the adapter.
        """
        if self.is_rectangular():
            min_lon, min_lat, max_lon, max_lat = self.bounds
            return {
                "xmin": min_lon,
                "ymin": min_lat,
                "xmax": max_lon,
                "ymax": max_lat,
                "spatialReference": {"wkid": GDA94},
            }
        exterior = self.geometry.exterior  # type: ignore[attr-defined]
        ring = [[x, y] for x, y in exterior.coords]
        return {"rings": [ring], "spatialReference": {"wkid": GDA94}}

    def arcgis_geometry_type(self) -> str:
        """The Esri ``geometryType`` matching :meth:`arcgis_geometry`."""
        return "esriGeometryEnvelope" if self.is_rectangular() else "esriGeometryPolygon"

    # -- caching ---------------------------------------------------------

    def _canonical_wkt(self) -> str:
        """Deterministic WKT after rounding + normalising the geometry.

        Coordinates are snapped to a fixed grid then ``normalize``-d (canonical
        ring orientation and vertex ordering) so that two geometries describing
        the same footprint produce byte-identical WKT, and thus the same key.
        """
        snapped = shapely.set_precision(self.geometry, _CACHE_GRID)
        canonical = shapely.normalize(snapped)
        return shapely.to_wkt(canonical, rounding_precision=_CACHE_PRECISION_DP, trim=True)

    def cache_key(self) -> str:
        """Stable sha256 hex digest of the canonicalised geometry.

        Identical footprints yield identical keys; distinct footprints yield
        distinct keys. Subset/superset regions are *not* reused — each distinct
        footprint is its own cache entry (documented in DESIGN).
        """
        payload = self._canonical_wkt().encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        min_lon, min_lat, max_lon, max_lat = self.bounds
        kind = "bbox" if self.is_rectangular() else self.geometry.geom_type
        return f"Region({kind}: {min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f})"
