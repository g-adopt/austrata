# austrata — design

`austrata` is a pure **data-access + caching** layer over **Geoscience Australia
(GA)** open data (boreholes via GeoServer OGC WFS, Hydrogeology of Australia via
ArcGIS REST) and the Bureau of Meteorology **NGIS** state cores. It returns
geopandas GeoDataFrames and domain objects, always in **lon/lat EPSG:4283 (GDA94
geographic)**. Map projection and meshing are deliberately out of scope — they
live in the companion `omega` package, which consumes this one.

## Public API

`from austrata import GADataClient` is the one object most callers use.

```python
ga = GADataClient(cache_dir=None, *, offline=False, max_age=None,
                  http=None, wfs=None, arcgis=None, cache=None)
```

All collaborators are injectable for testing; defaults are constructed otherwise.
`cache_dir` defaults to the OS user cache (`platformdirs`), overridable via the
`AUSTRATA_DATA_DIR` env var. `offline=True` serves only from cache (raises if
absent). `max_age` is the cache TTL backstop.

- `ga.boreholes(region=None, *, bbox=None, filter=None, force_refresh=False, count_only=False)`
  → `BoreholeCollection` (or `int` when `count_only`). Pass a shapely geometry
  `region=` **or** a `bbox=(min_lon, min_lat, max_lon, max_lat)` tuple, not both.
  `filter` is a WFS CQL predicate ANDed with the spatial filter.
- `ga.borehole(identifier)` → `Borehole | None`. `identifier` is an ENO (int/str)
  or a `…/BH<ENO>` PID URL.
- `ga.hydrogeology(region=None, *, bbox=None, where=None, force_refresh=False, count_only=False)`
  → `GeoDataFrame` of polygons (or `int` when `count_only`). `where` is an ArcGIS
  SQL predicate.

Module helpers for hydrogeology provenance (no method on a bare GeoDataFrame):
`austrata.client.hydrogeology_provenance(gdf)` / `hydrogeology_citation(gdf)`.

### NGIS clients (BoM state cores)

`from austrata import NGISClient, GroundwaterClient` add a second borehole source —
the Bureau of Meteorology National Groundwater Information System (NGIS) state
cores (NSW/VIC/QLD) — and a federated query across GA + NGIS.

```python
ngis = NGISClient(ngis_dir=None, cache_dir=None, *, offline=False, http=None, cache=None)
gw   = GroundwaterClient(cache_dir=None, ngis_dir=None, *, offline=False, http=None,
                         ga=None, ngis=None)
```

- `ngis.boreholes(state, *, bbox=None, region=None, force_refresh=False)` →
  `BoreholeCollection` tagged `source="NGIS:<STATE>"`, filtered to the box. The
  NGIS payload is the prize: dense stratigraphy with per-interval AHD top/base
  elevations the GA WFS lacks regionally. `load_logs("stratigraphy"|"earth_material"|
  "construction")` joins the cached fast-DB log frame by `HydroCode` and
  distributes onto each bore; the three export frames carry a `source` column.
- `gw.boreholes(*, bbox=None, region=None, sources=None, force_refresh=False)` →
  one `BoreholeCollection` federating GA **and** every NGIS state whose extent
  intersects the box (a NSW box never opens the VIC gdb). **No GA↔NGIS dedup** —
  an overlapping bore appears once per source, each with its `.source`. `sources=`
  restricts the backends: `"GA"`, `"NGIS"` (all intersecting), `"NGIS:NSW"`, or a
  bare `"NSW"`. `load_logs` dispatches per source (GA via WFS/ENO, NGIS via
  gdb/HydroCode); `construction` is NGIS-only and silently skips GA bores.
  `provenance()` is per-source; `citation()` concatenates every source's citation.

`ngis_dir` (raw gdbs, bulky + disposable) is separate from `cache_dir` (the fast
DB), overridable via the `AUSTRATA_NGIS_DIR` env var.

### Domain objects (`austrata.domain`)

- **`Region`** (`region.py`) — immutable query footprint (shapely geometry in
  EPSG:4283). `Region.from_bbox(min_lon,min_lat,max_lon,max_lat)`,
  `from_geometry(geom)`. `bounds`, `is_rectangular()`. Owns backend quirks:
  `wfs_bbox()` (returns `"…,EPSG:4283"` — the suffix is mandatory),
  `arcgis_geometry()` / `arcgis_geometry_type()` (envelope for bbox, polygon ring
  otherwise), and `cache_key()` (sha256 of canonicalised geometry — stable across
  numerically-equal geometries).
- **`Borehole`** (`borehole.py`) — header entity: `eno`, `name`, `longitude`,
  `latitude`, `identifier`, `elevation_m`, `state`, `province`, `purpose`,
  `status`, drill/observation metadata, plus a **`source`** tag (`"GA"` /
  `"NGIS:<STATE>"`), the promoted NGIS fields `bore_depth_m` / `drilled_depth_m` /
  `drilled_date`, and a **`source_attributes`** dict holding the entire original
  record verbatim (miss-nothing raw bag, on every source). `Borehole.from_feature`
  sets `source="GA"`. `point` → shapely Point. Lazy log accessors `.stratigraphy` /
  `.earth_material` / `.construction` (raise until loaded); `set_stratigraphy(...)` /
  `set_earth_material(...)` / `set_construction(...)` inject.
- **`BoreholeCollection`** (`borehole.py`) — iterable aggregate of `Borehole` +
  its `Region`. `__len__`, `__iter__`, `__getitem__`, `enos`, `to_geodataframe()`
  (headers as points in EPSG:4283, with a `source` column). `load_logs(kind=
  "stratigraphy"|"earth_material"|"construction", force_refresh=False)` bulk-loads
  logs onto each borehole (wired by the client). `stratigraphy_geodataframe()` /
  `earth_material_geodataframe()` / `construction_geodataframe()` export the loaded
  logs as a tidy GeoDataFrame (one row per interval, borehole-point geometry,
  EPSG:4283, a `source` column; raise if not loaded, empty frame if loaded-but-zero).
  `provenance()` / `citation()` surface the cache entry's source/license/access date.
- **`StratigraphyInterval`, `EarthMaterialInterval`** (`stratigraphy.py`) — frozen
  value objects, one log interval each. `top_depth`/`bottom_depth` in **metres
  below the depth reference point**; `ref_elevation_m_ahd` in m AHD; per-interval
  `top_elev_m_ahd`/`bottom_elev_m_ahd` (m AHD — null on GA, populated by NGIS).
  `StratigraphyInterval` also carries a free-text `comment`. Both carry a
  `source_attributes` raw bag (`compare=False`, so equality/hash stay on the real
  fields). `from_feature` normalises the UPPERCASE log keys. Carry `valid`/
  `invalid_reason` (never raise on bad data — filter on `valid` before interpolation).
- **`ConstructionInterval`** (`construction.py`) — frozen value object for NGIS
  screen/casing intervals (NGIS-only; GA has no equivalent). Same depth/AHD/`valid`
  contract + raw bag, plus `construction_type`, `material`, `inner_diameter`,
  `outer_diameter`, `property`, `property_size`, `drill_method`.
- **`HydrogeologyUnit`** (`hydrogeology.py`) — polygon value object: `feature`,
  `type`, `distribution`, `productivity`, `aquifer_type`, `ufi`.

## Architecture (clean architecture / DDD)

Dependency rule: `domain` depends on nothing → `ports` define interfaces →
`application` orchestrates ports → `infrastructure` implements ports → `client`
wires concrete infrastructure. Nothing below `client` mentions a specific backend.

```
austrata/
  domain/           region, borehole, stratigraphy, construction, hydrogeology,
                    coercion (pure)
  ports/            data_source.py (BoreholeSource, HydrogeologySource),
                    cache.py (DatasetCache protocol)
  application/      fetch_boreholes.py, fetch_hydrogeology.py, fetch_ngis.py
  infrastructure/   ogc_wfs_client, arcgis_rest_client, http, dataset_cache,
                    feature_mapper; ngis_sources, ngis_download, ngis_optimiser,
                    ngis_mapper
  client.py             GADataClient facade
  ngis_client.py        NGISClient facade
  groundwater_client.py GroundwaterClient (federated GA + NGIS)
```

### Adapters (`infrastructure`)

- **`OgcWfsClient`** (boreholes, WFS 2.0): `count_headers(region, cql_filter)` via
  `resultType=hits`; `fetch_headers(...)` auto-paginates (`count`+`startIndex`);
  `fetch_header(identifier)`; `fetch_stratigraphy(enos)` / `fetch_earth_material(enos)`
  via **ENO-chunked POST** `ENO IN (...)` (logs are not spatially queryable).
- **`ArcGisRestClient`** (hydrogeology, Esri REST): `count_units(region, where)`
  (`returnCountOnly`); `fetch_units(...)` paginated (`resultOffset`/
  `resultRecordCount`, stops on `exceededTransferLimit=false`); `probe_etag(...)`
  for conditional revalidation. Always sends `outSR=4283`.
- Both reach the network through **`HttpClient`** (`http.py`): `get`/`post`,
  tenacity retry on 429/502/503/504 only (never 400/404), honours `Retry-After`,
  split connect/read timeouts, polite `User-Agent` + inter-request delay,
  surfaces 304 for conditional GETs. Paginators have a `max_pages` guard so they
  can never infinite-loop.

### Cache & freshness (`dataset_cache.py`)

`DatasetCache` stores one query result as `<key>.parquet` + an entry in
`manifest.json`. Key = `Region.cache_key()` + query descriptor.

- **Atomicity:** parquet written to `<key>.partial` then `os.replace`d; manifest
  written via temp-file + `os.replace` under a `filelock`. An interrupted/failed
  fetch never commits.
- **Freshness** is injected per query as a **`FreshnessStrategy`** (concrete
  `FetchPlan`): `conditional_headers`, `fingerprint`, `is_unchanged`, `fetch`. The
  cache is backend-agnostic — WFS plans fingerprint `numberMatched` (no ETag);
  ArcGIS plans use `If-None-Match`/ETag (304). A `max_age` TTL is the backstop
  since `numberMatched` can't detect same-count content edits (best-effort,
  documented; `force_refresh` is the only hard guarantee).
- API: `get_or_fetch(key, plan, force_refresh=False)` (main entry), `get`, `put`,
  `has`, `is_fresh`, `provenance(key)`, `list()`, `info()`, `clear(key=None)`.

### Application use-cases (`fetch_boreholes.py`, `fetch_hydrogeology.py`)

Build the per-backend cache keys and `FetchPlan`s: `header_cache_key` /
`build_header_plan`, `log_cache_key` / `build_log_plan`,
`hydrogeology_cache_key` / `build_hydrogeology_plan`. `feature_mapper.py` maps raw
GeoJSON ↔ GeoDataFrames ↔ domain objects (`gdf_to_borehole_collection`,
`distribute_stratigraphy`, etc.).

### NGIS pipeline (two stages, two consistency contracts)

NGIS plugs in as a second borehole source via a **two-stage optimiser pipeline**:
the raw multi-GB `.gdb` is converted **once per state** into a "fast DB" (per-layer
GeoParquet) that every box query then filters in memory — milliseconds, offline.

- **`ngis_sources.py`** — the pinned registry: per state, the data.gov.au resource
  URL, our S3 mirror, expected zip size + md5, the `.gdb` path, vintage, citation,
  and the geographic `extent` (derived from the real gdb bore bounds) used for
  routing. `get_source(state)`, `NGIS_STATES`, `ngis_states_intersecting(box)`.
- **`ngis_download.ensure_gdb(state)`** — **local-first**, else streams the zip
  (data.gov.au primary → S3 mirror fallback) with a progress bar, **verifies size +
  md5** against the registry (the **remote↔gdb contract** — an unverified archive
  is never trusted), extracts. `AUSTRATA_NGIS_DIR` overrides where raw gdbs live;
  `AUSTRATA_NO_PROGRESS` silences the download bar.
- **`ngis_optimiser.optimise_state(gdb, state)`** — one fiona pass (pyogrio raises
  on these gdbs) reading `NGIS_Bore` + the three log layers **verbatim** (every
  original column; no GA-key renaming), building a lon/lat Point from the
  `Longitude`/`Latitude` attributes (the Albers `SHAPE` is discarded) and
  denormalising the bore lon/lat + point onto each log row by `HydroCode`. Carries
  `OPTIMISER_VERSION`; `build_stamp(state)` emits `{state, vintage, gdb_md5,
  optimiser_version}`.
- **`fetch_ngis.load_ngis_frames(...)`** — the **gdb↔fast-DB stamp gate**: one
  `DatasetCache` entry per `(state, layer, optimiser_version)`; each persists the
  stamp (`gdb_md5` + `optimiser_version`) as its `server_fingerprint`. Freshness =
  every layer present AND the stored stamp equals the expected one; a version bump
  or a future md5 change forces a rebuild (and sweeps the superseded entries),
  re-downloading the gdb only if it is gone. Build-once: a rebuild runs ensure_gdb
  + optimise_state **once** and caches all four frames. Infra is injected
  (dependency rule); the client wires it.
- **`ngis_mapper.py`** — the curated NGIS-column → domain-field mapping (the GA
  analogue of `feature_mapper.py`), per the tables in `data/ngis/NGIS_SCHEMA.md`.
  Builds domain objects directly; the full original record rides in
  `source_attributes` (logs strip only the optimiser-added geometry + denormalised
  lon/lat). `eno=None` for NGIS (`HydroCode` is the identifier); `"Unknown"`
  formations are kept but flagged `valid=False`.

## Verified GA-service facts (gotchas baked into the code)

- Endpoints: boreholes `https://services.ga.gov.au/gis/boreholes/wfs`;
  hydrogeology `https://services.ga.gov.au/gis/rest/services/Hydrogeology_of_Australia/MapServer` (layer 0).
- Sizes (national): 52,338 borehole headers; 190,016 stratigraphy logs; 551,852
  earth-material logs. **Pagination is mandatory.**
- Join key **ENO**: lowercase `eno` in `gsmlp:BoreholeView`, UPPERCASE `ENO` in
  the `bh:*` log layers. The mapper normalises case.
- **Log layers are NOT spatially queryable** (a BBOX returns 0) → fetch logs by
  `ENO IN (...)`, via POST for long lists.
- WFS `bbox` **requires** an explicit `EPSG:4283` suffix (else HTTP 400); the
  `urn:` CRS form flips to lat/lon axis order.
- WFS sends **no ETag** → freshness via `numberMatched` + TTL. ArcGIS sends an
  **ETag** → conditional GET. ArcGIS `f=geojson` omits the CRS, so always pass
  **`outSR=4283`** (else silent WGS84/4326). ArcGIS `maxRecordCount`=2000.
- Depths: `INTERVAL_BEGIN_M`/`INTERVAL_END_M` (metres, down from the depth ref
  point); `DEPTH_REF_POINT_ELEV_M_AHD` in m AHD. Per-interval
  `INTERVAL_BEGIN/END_ELEV_M_AHD` are modelled (`top/bottom_elev_m_ahd`) — usually
  null on GA, populated by NGIS.

## Verified NGIS facts (gotchas baked into the code)

- Read the `.gdb` with **fiona, NOT pyogrio** — pyogrio raises `GeometryError`
  (an exotic geometry-type flag); `geopandas.read_file(..., engine="fiona")` or
  `fiona.open(...)` work.
- Join key is **`HydroCode`** (a string, present on the bore and every log row);
  NGIS has no ENO, so `eno=None` on NGIS objects and `HydroCode` is the
  `identifier`. `StateTerritory`/`Agency` are coded ints — state comes from the
  queried code, not that field.
- **Miss-nothing:** the optimiser keeps every NGIS column verbatim; the curated
  mapping promotes a named surface and the rest rides in `source_attributes`.
- **`"Unknown"` formations are kept**, flagged `valid=False` (`invalid_reason=
  "unknown formation"`); a depth problem takes precedence in the reason. Read
  lon/lat from the `Longitude`/`Latitude` attributes; the projected Albers `SHAPE`
  (EPSG:3577) is discarded. Only NSW/VIC/QLD exist as standalone cores.
