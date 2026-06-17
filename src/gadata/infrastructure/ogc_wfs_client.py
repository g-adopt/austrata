"""GeoServer WFS 2.0 adapter for the GA boreholes service (``BoreholeSource``).

Backend facts this adapter encodes (verified live, see DESIGN):

* Headers live in ``gsmlp:BoreholeView`` (join key ``eno``, lowercase).
* Logs live in ``bh:BoreholeStratigraphyLogs`` / ``bh:BoreholeEarthMaterialLogs``
  (join key ``ENO``, UPPERCASE) and are **not spatially queryable** — a BBOX on
  them returns zero features. The only path is ``ENO IN (...)``.
* Long ``ENO IN (...)`` filters are sent via **POST** to dodge GET URL limits.
* Totals are obtained up front with ``resultType=hits`` and the result is
  paginated to completion with ``count`` + ``startIndex``.
* The bbox value must carry the explicit ``EPSG:4283`` suffix (``Region`` owns
  this); omitting it 400s and the urn form flips axis order.

The adapter returns raw GeoJSON feature dicts; mapping to domain objects is the
mappers' job (a later task).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

from gadata.domain.region import Region
from gadata.infrastructure.http import HttpClient

logger = logging.getLogger("gadata.wfs")

WFS_BASE_URL = "https://services.ga.gov.au/gis/boreholes/wfs"

HEADER_TYPE = "gsmlp:BoreholeView"
STRAT_TYPE = "bh:BoreholeStratigraphyLogs"
EARTH_TYPE = "bh:BoreholeEarthMaterialLogs"

# numberMatched appears as a root-element attribute in the hits XML response.
_NUMBER_MATCHED_RE = re.compile(r'numberMatched=["\'](\d+)["\']')


class OgcWfsClient:
    """Adapter implementing :class:`~gadata.ports.data_source.BoreholeSource`."""

    def __init__(
        self,
        http: Optional[HttpClient] = None,
        *,
        base_url: str = WFS_BASE_URL,
        page_size: int = 1000,
        eno_chunk_size: int = 200,
        max_pages: int = 10000,
    ) -> None:
        self.http = http or HttpClient()
        self.base_url = base_url
        self.page_size = page_size
        self.eno_chunk_size = eno_chunk_size
        self.max_pages = max_pages

    # -- headers ---------------------------------------------------------

    def count_headers(self, region: Region, cql_filter: Optional[str] = None) -> int:
        """Total header count in ``region`` via ``resultType=hits``."""
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": HEADER_TYPE,
            "resultType": "hits",
            "srsName": "EPSG:4283",
        }
        self._apply_spatial_filter(params, region, cql_filter)
        resp = self.http.get(self.base_url, params=params)
        return self._parse_number_matched(resp.text)

    def fetch_headers(self, region: Region, cql_filter: Optional[str] = None) -> List[dict]:
        """All header features intersecting ``region``, paginated to completion."""
        total = self.count_headers(region, cql_filter)
        logger.info("WFS headers: %d total in region", total)
        features: List[dict] = []
        start = 0
        page_num = 0
        while True:
            params = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": HEADER_TYPE,
                "outputFormat": "application/json",
                "srsName": "EPSG:4283",
                "count": self.page_size,
                "startIndex": start,
            }
            self._apply_spatial_filter(params, region, cql_filter)
            resp = self.http.get(self.base_url, params=params)
            page = resp.json().get("features", [])
            features.extend(page)
            page_num += 1
            logger.info("WFS headers: page at startIndex=%d -> %d (cum %d/%s)",
                        start, len(page), len(features), total or "?")
            if len(page) < self.page_size:
                break
            start += self.page_size
            if total and start >= total:
                break
            self._guard_max_pages(page_num)
        return features

    def fetch_header(self, identifier: str) -> Optional[dict]:
        """A single header by ENO (CQL ``eno=<n>``); ``None`` if not found."""
        eno = self._identifier_to_eno(identifier)
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": HEADER_TYPE,
            "outputFormat": "application/json",
            "srsName": "EPSG:4283",
            "count": 1,
            "cql_filter": f"eno={eno}",
        }
        resp = self.http.get(self.base_url, params=params)
        feats = resp.json().get("features", [])
        return feats[0] if feats else None

    # -- logs (ENO-chunked POST; not spatially queryable) ----------------

    def fetch_stratigraphy(self, enos: Iterable[int]) -> List[dict]:
        """Stratigraphy-log features for ``enos`` (chunked, paginated POST)."""
        return self._fetch_logs(STRAT_TYPE, enos)

    def fetch_earth_material(self, enos: Iterable[int]) -> List[dict]:
        """Earth-material-log features for ``enos`` (chunked, paginated POST)."""
        return self._fetch_logs(EARTH_TYPE, enos)

    def _fetch_logs(self, type_name: str, enos: Iterable[int]) -> List[dict]:
        unique = sorted({int(e) for e in enos})
        if not unique:
            return []
        features: List[dict] = []
        for chunk in self._chunked(unique, self.eno_chunk_size):
            in_list = ",".join(str(e) for e in chunk)
            cql = f"ENO IN ({in_list})"
            features.extend(self._post_paginated(type_name, cql))
        logger.info("WFS logs %s: %d features for %d ENOs", type_name, len(features), len(unique))
        return features

    def _post_paginated(self, type_name: str, cql_filter: str) -> List[dict]:
        """Paginate one POST GetFeature chunk to completion."""
        features: List[dict] = []
        start = 0
        page_num = 0
        while True:
            data = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": type_name,
                "outputFormat": "application/json",
                "srsName": "EPSG:4283",
                "count": self.page_size,
                "startIndex": start,
                "cql_filter": cql_filter,
            }
            resp = self.http.post(self.base_url, data=data)
            page = resp.json().get("features", [])
            features.extend(page)
            page_num += 1
            if len(page) < self.page_size:
                break
            start += self.page_size
            self._guard_max_pages(page_num)
        return features

    def _guard_max_pages(self, page_num: int) -> None:
        """Raise if pagination overruns ``max_pages`` (never infinite-loop)."""
        if page_num >= self.max_pages:
            raise RuntimeError(
                f"WFS pagination exceeded max_pages={self.max_pages}; "
                "aborting to avoid an infinite loop."
            )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _apply_spatial_filter(params: dict, region: Region, cql_filter: Optional[str]) -> None:
        """Attach the BBOX (and optional CQL) to a header request.

        BBOX and a custom ``cql_filter`` cannot both ride the WFS ``bbox`` param,
        so when a CQL filter is present the spatial constraint is expressed as a
        ``BBOX()`` predicate ANDed inside ``cql_filter`` instead.
        """
        min_lon, min_lat, max_lon, max_lat = region.bounds
        if cql_filter:
            bbox_pred = f"BBOX(shape,{min_lon},{min_lat},{max_lon},{max_lat},'EPSG:4283')"
            params["cql_filter"] = f"({cql_filter}) AND {bbox_pred}"
        else:
            params["bbox"] = region.wfs_bbox()

    @staticmethod
    def _parse_number_matched(text: str) -> int:
        match = _NUMBER_MATCHED_RE.search(text)
        if not match:
            raise ValueError("Could not parse numberMatched from WFS hits response.")
        return int(match.group(1))

    @staticmethod
    def _identifier_to_eno(identifier: str) -> int:
        """Accept a bare ENO, an int, or a PID URL ending in BH<ENO>."""
        text = str(identifier).strip()
        if text.isdigit():
            return int(text)
        match = re.search(r"BH(\d+)$", text)
        if match:
            return int(match.group(1))
        raise ValueError(f"Cannot derive ENO from identifier {identifier!r}.")

    @staticmethod
    def _chunked(items: List[int], size: int):
        for i in range(0, len(items), size):
            yield items[i:i + size]
