# gadata — project guide

Python package for accessing **Geoscience Australia (GA)** open data: boreholes
(GeoServer OGC WFS) and Hydrogeology of Australia (ArcGIS REST). It is a pure
**data-access + caching** layer. It returns geopandas GeoDataFrames and domain
objects, always in **lon/lat EPSG:4283 (GDA94 geographic)**. Downstream packages
do the rest: **omega** owns meshing/projection; an **interpolation** layer (not
built yet) will consume these domain objects in parallel.

Repo: https://github.com/g-adopt/gadata (public). Design rationale lives in
`DESIGN.md`; the live-server test plan in `GA_SERVER_TEST_PLAN.md`.

## Environment & commands

Always use the project Python at `~/Workplace/python3.12`.

```bash
~/Workplace/python3.12/bin/pip install -e ".[dev]"      # editable install
~/Workplace/python3.12/bin/python3.12 -m pytest -q                       # offline unit tests (no network)
~/Workplace/python3.12/bin/python3.12 -m pytest -q -m "live and smoke"   # fast GA-server health (~15 checks)
~/Workplace/python3.12/bin/python3.12 -m pytest -q -m "live and contract"# fuller GA schema/data contract
~/Workplace/python3.12/bin/python3.12 -m pytest -q -m live               # all live tests
~/Workplace/python3.12/bin/python3.12 -m flake8 gadata tests             # lint (line-length 120)
~/Workplace/python3.12/bin/python3.12 -m mypy gadata                     # type check (must stay clean)
```

Default `pytest` excludes anything marked `live` (those hit the real GA
servers). Markers: `live`, `smoke`, `contract`, `heavy` (heavy = on-demand only).

## Public API

`from gadata import GADataClient` is the one object most callers use.

```python
ga = GADataClient(cache_dir=None, *, offline=False, max_age=None,
                  http=None, wfs=None, arcgis=None, cache=None)
```
All collaborators are injectable for testing; defaults are constructed otherwise.
`cache_dir` defaults to the OS user cache (`platformdirs`), overridable via the
`GADATA_DATA_DIR` env var. `offline=True` serves only from cache (raises if
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
`gadata.client.hydrogeology_provenance(gdf)` / `hydrogeology_citation(gdf)`.

### Domain objects (`gadata.domain`)

- **`Region`** (`region.py`) — immutable query footprint (shapely geometry in
  EPSG:4283). `Region.from_bbox(min_lon,min_lat,max_lon,max_lat)`,
  `from_geometry(geom)`. `bounds`, `is_rectangular()`. Owns backend quirks:
  `wfs_bbox()` (returns `"…,EPSG:4283"` — the suffix is mandatory),
  `arcgis_geometry()` / `arcgis_geometry_type()` (envelope for bbox, polygon ring
  otherwise), and `cache_key()` (sha256 of canonicalised geometry — stable across
  numerically-equal geometries).
- **`Borehole`** (`borehole.py`) — header entity: `eno`, `name`, `longitude`,
  `latitude`, `identifier`, `elevation_m`, `state`, `province`, `purpose`,
  `status`, drill/observation metadata. `Borehole.from_feature(properties, geometry)`.
  `point` → shapely Point. Lazy log accessors `.stratigraphy` / `.earth_material`
  (raise until loaded); `set_stratigraphy(...)` / `set_earth_material(...)` inject.
- **`BoreholeCollection`** (`borehole.py`) — iterable aggregate of `Borehole` +
  its `Region`. `__len__`, `__iter__`, `__getitem__`, `enos`, `to_geodataframe()`
  (headers as points in EPSG:4283). `load_logs(kind="stratigraphy"|"earth_material",
  force_refresh=False)` bulk-loads logs onto each borehole (wired by the client).
  `provenance()` / `citation()` surface the cache entry's source/license/access date.
- **`StratigraphyInterval`, `EarthMaterialInterval`** (`stratigraphy.py`) — frozen
  value objects, one log interval each. `top_depth`/`bottom_depth` in **metres
  below the depth reference point**; `ref_elevation_m_ahd` in m AHD. `from_feature`
  normalises the UPPERCASE log keys. Carry `valid`/`invalid_reason` (never raise on
  bad data — filter on `valid` before interpolation).
- **`HydrogeologyUnit`** (`hydrogeology.py`) — polygon value object: `feature`,
  `type`, `distribution`, `productivity`, `aquifer_type`, `ufi`.

## Architecture (clean architecture / DDD)

Dependency rule: `domain` depends on nothing → `ports` define interfaces →
`application` orchestrates ports → `infrastructure` implements ports → `client`
wires concrete infrastructure. Nothing below `client` mentions a specific backend.

```
gadata/
  domain/           region, borehole, stratigraphy, hydrogeology, coercion (pure)
  ports/            data_source.py (BoreholeSource, HydrogeologySource),
                    cache.py (DatasetCache protocol)
  application/      fetch_boreholes.py, fetch_hydrogeology.py — build FetchPlans
  infrastructure/   ogc_wfs_client, arcgis_rest_client, http, dataset_cache,
                    feature_mapper
  client.py         GADataClient facade
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
  `INTERVAL_BEGIN/END_ELEV_M_AHD` exist but are usually null (not yet modelled).

## Testing & monitoring

- Offline unit tests mock HTTP with `responses` and use `tmp_path` caches.
- Live tests (`tests/test_*_live.py`, `tests/test_ga_server_live.py`) hit the real
  servers; each maps to a `GA_SERVER_TEST_PLAN.md` ID in its name.
- `.github/workflows/nightly-ga-health.yml` runs the **smoke** subset nightly
  (~02:00 AEST) and the **contract** subset weekly, uploading reports. The run
  steps use `set -o pipefail` so a failing check fails the job.

## Conventions & deferred work

- Python ≥3.11; flat `gadata/` package layout; flake8/black line-length 120; keep `mypy gadata` clean.
- Files under ~200 lines, functions focused; clean-architecture dependency rule.
- **Deferred (do not build without asking):** the interpolation layer; per-interval
  elevation fields on `StratigraphyInterval`; the lower-priority unknowns flagged
  at the end of `GA_SERVER_TEST_PLAN.md`.
- Commits: author as Sia Ghelichkhan, no AI attribution.
