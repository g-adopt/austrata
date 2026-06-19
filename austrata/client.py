"""``GADataClient`` — the public facade wiring adapters + cache + domain.

This is the only object most callers touch. It hides the two backends, the
cache, and the freshness machinery behind a small, lon/lat-only API::

    ga = GADataClient()
    bores = ga.boreholes(bbox=(149, -36, 150, -35))
    bores.load_logs("stratigraphy")
    hydro = ga.hydrogeology(region=some_polygon)

Everything is returned in EPSG:4283 (GDA94 geographic). Boreholes come back as a
:class:`BoreholeCollection` of domain objects; hydrogeology as a GeoDataFrame.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple, Union

import geopandas as gpd
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from austrata.application import fetch_boreholes as bh_uc
from austrata.application import fetch_hydrogeology as hy_uc
from austrata.domain.borehole import Borehole, BoreholeCollection
from austrata.domain.region import Region
from austrata.infrastructure.arcgis_rest_client import ArcGisRestClient
from austrata.infrastructure.dataset_cache import DatasetCache
from austrata.infrastructure.feature_mapper import (
    distribute_earth_material,
    distribute_stratigraphy,
    gdf_to_borehole_collection,
    gdf_to_records,
)
from austrata.infrastructure.http import HttpClient
from austrata.infrastructure.ogc_wfs_client import OgcWfsClient

logger = logging.getLogger("austrata.client")

BBox = Tuple[float, float, float, float]
_PROVENANCE_ATTR = "gadata_provenance"


def hydrogeology_provenance(gdf: gpd.GeoDataFrame) -> dict:
    """Provenance of a hydrogeology GeoDataFrame returned by the client."""
    return dict(gdf.attrs.get(_PROVENANCE_ATTR, {}))


def hydrogeology_citation(gdf: gpd.GeoDataFrame) -> str:
    """A citation string (incl. access date) for a hydrogeology GeoDataFrame."""
    prov = gdf.attrs.get(_PROVENANCE_ATTR, {})
    base = prov.get("citation") or "Hydrogeology of Australia, Geoscience Australia."
    parts = [base]
    if prov.get("fetched_at"):
        import datetime as _dt

        date = _dt.datetime.fromtimestamp(float(prov["fetched_at"]), _dt.timezone.utc).date()
        parts.append(f"Accessed {date.isoformat()}.")
    if prov.get("license"):
        parts.append(f"Licensed {prov['license']}.")
    if prov.get("source_url"):
        parts.append(f"Source: {prov['source_url']}")
    return " ".join(parts)


class GADataClient:
    """Facade over the GA boreholes (WFS) and hydrogeology (ArcGIS) services."""

    def __init__(
        self,
        cache_dir=None,
        *,
        offline: bool = False,
        max_age: Optional[float] = None,
        http: Optional[HttpClient] = None,
        wfs: Optional[OgcWfsClient] = None,
        arcgis: Optional[ArcGisRestClient] = None,
        cache: Optional[DatasetCache] = None,
    ) -> None:
        http = http or HttpClient()
        self.wfs = wfs or OgcWfsClient(http)
        self.arcgis = arcgis or ArcGisRestClient(http)
        if cache is not None:
            self.cache = cache
        elif max_age is not None:
            self.cache = DatasetCache(cache_dir, offline=offline, max_age=max_age)
        else:
            self.cache = DatasetCache(cache_dir, offline=offline)

    # -- boreholes -------------------------------------------------------

    def boreholes(
        self,
        region: Optional[BaseGeometry] = None,
        *,
        bbox: Optional[BBox] = None,
        filter: Optional[str] = None,
        force_refresh: bool = False,
        count_only: bool = False,
    ) -> Union[BoreholeCollection, int]:
        """Boreholes whose headers intersect ``region``/``bbox`` (cached).

        ``count_only`` returns the integer count without a full pull.
        """
        reg = self._resolve_region(region, bbox)
        if count_only:
            return self.wfs.count_headers(reg, filter)
        key = bh_uc.header_cache_key(reg, filter)
        plan = bh_uc.build_header_plan(self.wfs, reg, filter)
        gdf = self.cache.get_or_fetch(key, plan, force_refresh=force_refresh)
        collection = gdf_to_borehole_collection(gdf, reg)
        collection._loader = self._make_log_loader(collection)  # wire load_logs
        collection._provenance = self.cache.provenance(key)
        return collection

    def borehole(self, identifier: str) -> Optional[Borehole]:
        """A single borehole header by ENO/identifier, or ``None``."""
        feat = self.wfs.fetch_header(identifier)
        if feat is None:
            return None
        return Borehole.from_feature(feat.get("properties") or {}, feat.get("geometry"))

    # -- hydrogeology ----------------------------------------------------

    def hydrogeology(
        self,
        region: Optional[BaseGeometry] = None,
        *,
        bbox: Optional[BBox] = None,
        where: Optional[str] = None,
        force_refresh: bool = False,
        count_only: bool = False,
    ) -> Union[gpd.GeoDataFrame, int]:
        """Hydrogeology polygons intersecting ``region``/``bbox`` (cached)."""
        reg = self._resolve_region(region, bbox)
        if count_only:
            return self.arcgis.count_units(reg, where)
        key = hy_uc.hydrogeology_cache_key(reg, where)
        plan = hy_uc.build_hydrogeology_plan(self.arcgis, reg, where)
        gdf = self.cache.get_or_fetch(key, plan, force_refresh=force_refresh)
        # Stamp provenance onto the frame's metadata so hydrogeology_provenance()
        # / hydrogeology_citation() can surface it (GeoDataFrame.attrs survives
        # in-memory; it is not persisted to parquet, which is fine here).
        gdf.attrs[_PROVENANCE_ATTR] = self.cache.provenance(key)
        return gdf

    # -- log loading (wires BoreholeCollection.load_logs) ----------------

    def _make_log_loader(self, collection: BoreholeCollection):
        def load(kind: str = "stratigraphy", *, force_refresh: bool = False) -> None:
            enos = collection.enos
            if not enos:
                return
            key = bh_uc.log_cache_key(kind, enos)
            plan = bh_uc.build_log_plan(self.wfs, kind, enos)
            gdf = self.cache.get_or_fetch(key, plan, force_refresh=force_refresh)
            records = gdf_to_records(gdf)
            if kind == "stratigraphy":
                distribute_stratigraphy(collection, records)
            elif kind == "earth_material":
                distribute_earth_material(collection, records)
            else:
                raise ValueError(f"Unknown log kind {kind!r}.")
        return load

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _resolve_region(region: Optional[BaseGeometry], bbox: Optional[BBox]) -> Region:
        if region is not None and bbox is not None:
            raise ValueError("Pass either region= or bbox=, not both.")
        if bbox is not None:
            return Region.from_bbox(*bbox)
        if region is not None:
            if isinstance(region, Region):
                return region
            return Region(region if isinstance(region, BaseGeometry) else box(*region))
        raise ValueError("A region= geometry or bbox= tuple is required.")
