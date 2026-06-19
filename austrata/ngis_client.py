"""``NGISClient`` — NGIS-only facade, sibling to :class:`GADataClient`.

Serves boreholes (+ stratigraphy / earth-material / construction logs) from the
BoM NGIS state cores, in the same lon/lat EPSG:4283 shape and with the same
``BoreholeCollection`` API as :class:`GADataClient`::

    ngis = NGISClient()
    bc = ngis.boreholes("NSW", bbox=(143.0, -35.8, 146.3, -33.9))
    bc.load_logs("stratigraphy")
    gdf = bc.stratigraphy_geodataframe()      # tidy frame, 'source' column

This is the wiring seam: the concrete infrastructure (``ensure_gdb`` /
``optimise_state`` / ``build_stamp`` / ``OPTIMISER_VERSION``) is imported here and
injected into the clean application use-case, which stays backend-agnostic.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from shapely.geometry.base import BaseGeometry

from austrata.application import fetch_ngis as ngis_uc
from austrata.domain.borehole import BoreholeCollection
from austrata.domain.region import Region
from austrata.infrastructure import ngis_download, ngis_optimiser
from austrata.infrastructure.dataset_cache import DatasetCache
from austrata.infrastructure.http import HttpClient
from austrata.infrastructure.ngis_mapper import (
    ngis_bores_to_collection,
    ngis_construction,
    ngis_earth_material,
    ngis_stratigraphy,
)

logger = logging.getLogger("austrata.ngis")

BBox = Tuple[float, float, float, float]


class NGISClient:
    """Facade over the local NGIS state cores (boreholes + downhole logs)."""

    def __init__(
        self,
        ngis_dir=None,
        cache_dir=None,
        *,
        offline: bool = False,
        http: Optional[HttpClient] = None,
        cache: Optional[DatasetCache] = None,
    ) -> None:
        self.ngis_dir = ngis_dir
        self.offline = offline
        self.http = http or HttpClient()
        self.cache = cache or DatasetCache(cache_dir, offline=offline)

    # -- boreholes -------------------------------------------------------

    def boreholes(
        self,
        state: str,
        *,
        bbox: Optional[BBox] = None,
        region: Optional[BaseGeometry] = None,
        force_refresh: bool = False,
    ) -> BoreholeCollection:
        """NGIS bores for ``state`` whose location falls in ``bbox``/``region``."""
        reg = self._resolve_region(region, bbox)
        frames = self._load_frames(state, force_refresh=force_refresh)
        in_box = _filter_to_region(frames["bores"], reg)
        source = f"NGIS:{state.strip().upper()}"
        collection = ngis_bores_to_collection(in_box, reg, source)
        collection._loader = self._make_log_loader(state, collection, force_refresh)
        collection._provenance = self._provenance(state)
        return collection

    # -- log loading -----------------------------------------------------

    def _make_log_loader(self, state: str, collection: BoreholeCollection, force_refresh: bool):
        def load(kind: str = "stratigraphy", *, force_refresh: bool = force_refresh) -> None:
            frames = self._load_frames(state, force_refresh=force_refresh)
            codes = {b.identifier for b in collection if b.identifier is not None}
            if kind == "stratigraphy":
                by_code: dict = ngis_stratigraphy(_subset(frames["stratigraphy"], codes))
                setter = "set_stratigraphy"
            elif kind == "earth_material":
                by_code = ngis_earth_material(_subset(frames["earth_material"], codes))
                setter = "set_earth_material"
            elif kind == "construction":
                by_code = ngis_construction(_subset(frames["construction"], codes))
                setter = "set_construction"
            else:
                raise ValueError(f"Unknown log kind {kind!r}.")
            for b in collection:
                getattr(b, setter)(by_code.get(b.identifier, []) if b.identifier else [])
        return load

    # -- internals -------------------------------------------------------

    def _load_frames(self, state: str, *, force_refresh: bool):
        return ngis_uc.load_ngis_frames(
            self.cache, state,
            ensure_gdb=ngis_download.ensure_gdb,
            optimise_state=ngis_optimiser.optimise_state,
            optimiser_version=ngis_optimiser.OPTIMISER_VERSION,
            build_stamp=ngis_optimiser.build_stamp,
            ngis_dir=self.ngis_dir, http=self.http,
            force_refresh=force_refresh, offline=self.offline,
        )

    def _provenance(self, state: str) -> dict:
        key = ngis_uc.ngis_cache_key(state, "bores", ngis_optimiser.OPTIMISER_VERSION)
        return self.cache.provenance(key)

    @staticmethod
    def _resolve_region(region: Optional[BaseGeometry], bbox: Optional[BBox]) -> Region:
        if region is not None and bbox is not None:
            raise ValueError("Pass either region= or bbox=, not both.")
        if bbox is not None:
            return Region.from_bbox(*bbox)
        if region is not None:
            return region if isinstance(region, Region) else Region(region)
        raise ValueError("A region= geometry or bbox= tuple is required.")


# -- frame filtering ----------------------------------------------------


def _filter_to_region(bores, region: Region):
    """Rows whose bore point intersects the region geometry (EPSG:4283).

    Uses ``intersects`` (not ``within``) so a point exactly on the box edge is
    included — matching GADataClient's edge-inclusive WFS bbox semantics, so the
    same edge bore appears from both sources in the federated client.
    """
    if len(bores) == 0:
        return bores
    geom = region.geometry
    mask = bores.geometry.notna() & bores.geometry.intersects(geom)
    return bores[mask]


def _subset(log_frame, codes):
    """Log rows whose HydroCode is in ``codes`` (the in-box bores' identifiers)."""
    if len(log_frame) == 0 or "HydroCode" not in log_frame.columns:
        return log_frame
    return log_frame[log_frame["HydroCode"].isin(codes)]
