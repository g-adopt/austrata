"""ArcGIS REST adapter for the GA Hydrogeology service (``HydrogeologySource``).

Backend facts this adapter encodes (verified live, see DESIGN):

* Single polygon layer (layer 0) on an Esri MapServer.
* ``f=geojson`` omits the ``crs`` field and does **not** guarantee GDA94 — the
  default is WGS84/4326. We therefore force ``outSR=4283`` on *every* query so
  the lon/lat (GDA94) contract holds end to end. The ~1–1.8 m difference between
  4283 and 4326 in Australia would otherwise silently corrupt positions.
* ``maxRecordCount`` is 2000 and ``supportsPagination`` is true. We page with
  ``resultOffset`` + ``resultRecordCount`` and loop while ``exceededTransferLimit``
  is true (and as a backstop while a full page comes back).
* Spatial filter is ``geometry`` + ``geometryType`` + ``inSR=4283`` +
  ``spatialRel=esriSpatialRelIntersects``. Polygon ``geometry`` JSON can be
  large, so polygon queries go via POST; envelope/bbox queries via GET.

Returns raw GeoJSON feature dicts; mapping is the mappers' job (a later task).
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from gadata.domain.region import Region
from gadata.infrastructure.http import HttpClient

logger = logging.getLogger("gadata.arcgis")

HYDRO_BASE_URL = (
    "https://services.ga.gov.au/gis/rest/services/"
    "Hydrogeology_of_Australia/MapServer"
)
DEFAULT_LAYER = 0
GDA94 = 4283


class ArcGisRestClient:
    """Adapter implementing :class:`~gadata.ports.data_source.HydrogeologySource`."""

    def __init__(
        self,
        http: Optional[HttpClient] = None,
        *,
        base_url: str = HYDRO_BASE_URL,
        layer: int = DEFAULT_LAYER,
        page_size: int = 2000,
        max_pages: int = 10000,
    ) -> None:
        self.http = http or HttpClient()
        self.base_url = base_url
        self.layer = layer
        self.page_size = page_size
        self.max_pages = max_pages

    @property
    def _query_url(self) -> str:
        return f"{self.base_url}/{self.layer}/query"

    # -- counting --------------------------------------------------------

    def count_units(self, region: Region, where: Optional[str] = None) -> int:
        """Cheap count of units intersecting ``region`` (``returnCountOnly``)."""
        params = self._base_params(region, where)
        params.update({"returnCountOnly": "true", "f": "json"})
        resp = self._send(region, params)
        return int(resp.json().get("count", 0))

    # -- features --------------------------------------------------------

    def fetch_units(self, region: Region, where: Optional[str] = None) -> List[dict]:
        """All polygon features intersecting ``region``, paginated to completion."""
        features: List[dict] = []
        offset = 0
        page_num = 0
        while True:
            params = self._base_params(region, where)
            params.update(
                {
                    "f": "geojson",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "resultOffset": offset,
                    "resultRecordCount": self.page_size,
                }
            )
            resp = self._send(region, params)
            body = resp.json()
            page = body.get("features", [])
            features.extend(page)
            page_num += 1
            exceeded = bool(body.get("exceededTransferLimit"))
            logger.info(
                "ArcGIS hydrogeology: page %d offset=%d -> %d (cum %d, exceeded=%s)",
                page_num, offset, len(page), len(features), exceeded,
            )
            # exceededTransferLimit is ArcGIS's authoritative "more records?"
            # signal — it is False even when the final page is exactly
            # page_size long, so it (not the page length) decides termination.
            if not exceeded:
                break
            if not page:
                break
            offset += len(page)
            if page_num >= self.max_pages:
                raise RuntimeError(
                    f"ArcGIS pagination exceeded max_pages={self.max_pages}; "
                    "aborting to avoid an infinite loop."
                )
        return features

    # -- request assembly ------------------------------------------------

    def _base_params(self, region: Region, where: Optional[str]) -> dict:
        """Shared query params: spatial filter, SR, and the where clause.

        ``outSR=4283`` is non-negotiable (see module docstring). ``inSR=4283``
        tells the server the supplied geometry is GDA94.
        """
        params = {
            "where": where or "1=1",
            "geometry": json.dumps(region.arcgis_geometry()),
            "geometryType": region.arcgis_geometry_type(),
            "inSR": GDA94,
            "outSR": GDA94,
            "spatialRel": "esriSpatialRelIntersects",
        }
        return params

    def _send(self, region: Region, params: dict, headers: Optional[dict] = None):
        """GET for envelope (bbox) queries, POST for polygon (large geometry)."""
        if region.is_rectangular():
            return self.http.get(self._query_url, params=params, headers=headers)
        # Polygon geometry JSON can be large -> POST to dodge URL-length limits.
        return self.http.post(self._query_url, data=params, headers=headers)

    # -- conditional probe (for the cache freshness plan) ----------------

    def probe_etag(self, region: Region, where: Optional[str] = None,
                   etag: Optional[str] = None) -> dict:
        """Cheap conditional probe: returns the current ETag and a 304 flag.

        Sends a ``returnCountOnly`` query carrying ``If-None-Match`` (when an
        ``etag`` is known). A 304 means the cached copy is current. Keeping the
        HTTP shape here (not in the cache plan) preserves the layering: the cache
        stays backend-agnostic and the adapter owns the ArcGIS request details.
        """
        params = self._base_params(region, where)
        params.update({"returnCountOnly": "true", "f": "json"})
        req_headers = {"If-None-Match": etag} if etag else None
        resp = self._send(region, params, headers=req_headers)
        return {
            "not_modified": resp.status_code == 304,
            "etag": resp.headers.get("ETag"),
        }
