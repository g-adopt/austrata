# gadata — design

A Python package for accessing Geoscience Australia borehole and hydrogeology
data through their open OGC/ArcGIS web services, with a local provenance-aware
cache. Pure data access and modelling; meshing lives in **omega**, which
consumes this package. Interpolation sits as a parallel layer on top of the same
domain objects.

## Scope (agreed)

In scope:
- Boreholes modelled as first-class `Borehole` objects (header + downhole logs).
- Region-level collections (all bores inside an arbitrary polygon / bbox).
- Hydrogeology of Australia as a polygon layer to overlay.
- Auto-pagination to completion against both backends.
- Local cache with hybrid freshness checking and a provenance manifest.
- A parallel (not nested) interpolation layer that *consumes* domain objects.

Out of scope:
- Mesh generation (omega).
- Reprojection / map projections — everything is returned as lon/lat
  (EPSG:4326/GDA94 geographic); omega owns projection.
- Construction logs, directional survey, samples (deferred to a later cut).

First-cut downhole coverage: **stratigraphy logs** and **earth-material logs**.

## The two backends

| Concern        | Boreholes                          | Hydrogeology                         |
|----------------|-------------------------------------|--------------------------------------|
| Server         | GeoServer (OGC)                     | ArcGIS MapServer (Esri REST)         |
| Base URL       | `https://services.ga.gov.au/gis/boreholes/wfs` | `https://services.ga.gov.au/gis/rest/services/Hydrogeology_of_Australia/MapServer` |
| Protocol       | WFS 2.0.0 GetFeature                | REST `/{layer}/query`                |
| Geometry       | Points + relational log tables      | Polygons                             |
| Filter         | CQL / OGC filter + BBOX             | `where` + `geometry` intersect       |
| Paginate       | `startIndex` + `count`, `resultType=hits` for total | `resultOffset` + `resultRecordCount`, `exceededTransferLimit` |
| Output         | `application/json` (GeoJSON)        | `f=geojson`                          |

These differ enough (filter syntax, pagination, field naming) that they hide
behind one port with two adapters.

## Architecture (clean architecture / DDD layering)

```
gadata/
  domain/                # no I/O, no HTTP, no geopandas-IO — pure model
    borehole.py          # Borehole entity, BoreholeCollection aggregate
    stratigraphy.py      # StratigraphyInterval, EarthMaterialInterval value objects
    hydrogeology.py      # HydrogeologyUnit value object
    region.py            # Region value object (wraps a shapely polygon / bbox)
  ports/                 # interfaces the domain/use-cases depend on
    data_source.py       # BoreholeSource, HydrogeologySource protocols
    cache.py             # DatasetCache protocol
  application/           # use cases — orchestrate ports, no vendor detail
    fetch_boreholes.py   # boreholes-in-region, single borehole, load logs
    fetch_hydrogeology.py
  infrastructure/        # the messy edges
    ogc_wfs_client.py    # GeoServer adapter (boreholes)
    arcgis_rest_client.py# ArcGIS adapter (hydrogeology)
    http.py              # requests session + tenacity retry + conditional GET
    dataset_cache.py     # hash-named files + manifest + freshness logic
    feature_mapper.py    # GeoJSON feature -> domain object mappers
  # interpolation/       # DEFERRED — not built now; will be a parallel layer
                         # consuming domain objects once its consumer exists.
  client.py              # GADataClient facade — wires everything
  __init__.py            # public exports
```

Dependency rule: `domain` depends on nothing; `application` depends on `domain`
+ `ports`; `infrastructure` implements `ports`; `client` wires concrete
infrastructure into the use cases. `interpolation` depends only on `domain`.

## Domain model

- `Borehole`: identifier, name, lon/lat point, total depth, drill metadata, and
  lazy accessors `.stratigraphy` / `.earth_material` returning lists of interval
  value objects (or a GeoDataFrame view). Logs are fetched on first access via
  an injected source, then memoised.
- `StratigraphyInterval`: `top_depth`, `bottom_depth`, `unit`, `age`, `contact`.
- `EarthMaterialInterval`: `top_depth`, `bottom_depth`, `material`, description.
- `BoreholeCollection`: the set of boreholes for a `Region`; iterable; carries
  the region geometry; `.to_geodataframe()` for the headers; can bulk-load logs.
- `HydrogeologyUnit`: polygon + `feature`, `type`, `distribution`, `productivity`,
  `aquifer_type`.
- `Region`: a shapely polygon (bbox is just a rectangular polygon); knows how to
  emit a BBOX tuple and a WKT/geometry payload for each backend.

## Public API (facade)

```python
from gadata import GADataClient
from shapely.geometry import box

ga = GADataClient(cache_dir=None, offline=False)   # cache_dir defaults to user cache

region = box(149.0, -36.0, 150.0, -35.0)           # or any shapely polygon

bores = ga.boreholes(region=region)                # BoreholeCollection (paginated, cached)
bores = ga.boreholes(bbox=(149, -36, 150, -35))    # bbox shortcut
bores = ga.boreholes(region=region, filter="drillingMethod='Diamond'")  # backend filter pass-through

one = ga.borehole("ga.boreholes.123456")           # single Borehole by id
strat = one.stratigraphy                            # lazy, cached list of intervals

hydro = ga.hydrogeology(region=region)             # GeoDataFrame of polygons
```

Returns: `Borehole` / `BoreholeCollection` objects for boreholes;
`GeoDataFrame` for hydrogeology. Everything in lon/lat. `BoreholeCollection`
and the objects expose `.to_geodataframe()`.

## Caching & freshness

A single `DatasetCache` owns a cache directory and a `manifest.json`. One entry
per logical query.

- **Cache key**: sha256 of canonical `(service, layer, bbox/region-wkt, filter,
  output params)`. The hash is the on-disk filename.
- **Stored artifact**: GeoParquet (`<hash>.parquet`) — compact, typed, fast via
  pyogrio/pyarrow.
- **Manifest entry**: `{query, filename, content_sha256, etag, last_modified,
  server_fingerprint, fetched_at, feature_count}`. This is the provenance record.
- **Freshness (hybrid, agreed)**:
  1. `offline=True` or no network → use cached file if present, else raise.
  2. Online, not `force_refresh`:
     a. Conditional revalidation: send `If-None-Match` (stored etag) /
        `If-Modified-Since` (stored last_modified). `304` → cache is fresh.
     b. No validators from server → fall back to a cheap fingerprint:
        ArcGIS `lastEditDate` / service `modified`; WFS `resultType=hits`
        `numberMatched` (+ extent). Compare to stored `server_fingerprint`.
     c. Changed / unknown → re-download (paginate), recompute `content_sha256`,
        rewrite file, update manifest.
  3. `force_refresh=True` → always re-download.
- `content_sha256` is stored for integrity/provenance even though revalidation
  uses validators/fingerprints (a content hash alone can't decide staleness
  before download).

**Cache location (decided):** follow g-drift's intended approach — use an
OS-appropriate user cache directory, `pooch.os_cache("gadata")` /
`platformdirs.user_cache_dir("gadata")` (e.g. `~/Library/Caches/gadata` on
macOS), overridable via a `GADATA_DATA_DIR` env var and a `cache_dir=`
constructor argument. Never write inside the installed package
(`site-packages` is read-only). Only the cache-path convention is borrowed from
g-drift; gadata keeps its own cache/freshness machinery (g-drift's pooch
registry model assumes static known-hash files and does not fit live queries).

## Dependencies (library-first)

- `requests` + `tenacity` — HTTP with retry/backoff (tenacity is the Python
  cockatiel). Conditional-GET handled in our cache layer.
- `geopandas`, `shapely`, `pyproj`, `pyogrio`, `pyarrow` — geospatial IO + model.
- `platformdirs` — cache location.
- Dev/test: `pytest`, `vcrpy` (record real GA responses once → deterministic
  offline tests).

**Considered and rejected:** `requests-cache` (transparent HTTP cache) — it
fights the hash-named provenance store you want and models a different concept
(HTTP cache, not a reproducible dataset artifact store). `owslib` /
`arcgis`/`restapi` — heavyweight, and we only need two narrow request shapes;
plain requests + geopandas is leaner and fully under our control.

## Verified facts (probed live 2026-06-17; re-verified by tests/test_ga_server_live.py)

Dataset sizes (national totals) — **pagination is mandatory, never optional**:
- `gsmlp:BoreholeView` headers: **52,338**
- `bh:BoreholeStratigraphyLogs`: **190,016**
- `bh:BoreholeEarthMaterialLogs`: **551,852**

**Join key:** borehole `ENO` (numeric, e.g. `35147`). The header layer
`gsmlp:BoreholeView` exposes it lowercase as `eno`; the log tables expose it
UPPERCASE as `ENO`. Logs also carry `BOREHOLE_PID`
(`http://pid.geoscience.gov.au/samplingFeature/au/BH<ENO>`) and `BOREHOLE_NAME`.
**Field-name case differs between layers — the mapper must normalise.**

**Freshness mechanism differs per backend (validates the hybrid design):**
- Boreholes WFS emits **no** `ETag`/`Last-Modified`. → use the
  `resultType=hits` `numberMatched` count as the cheap fingerprint.
- Hydrogeology ArcGIS emits an **`ETag`** (`Cache-Control: must-revalidate`),
  but `editingInfo`/`modified` are null. → use conditional `If-None-Match`.

**Pagination params:** ArcGIS `maxRecordCount` = 2000, `supportsPagination` =
true (`resultOffset`/`resultRecordCount`, `exceededTransferLimit` flag). WFS
uses `count`+`startIndex`; get the total up front with `resultType=hits`.

**BBOX axis-order trap (WFS 2.0):** the bbox value **must** carry an explicit
CRS suffix. Use `bbox=minLon,minLat,maxLon,maxLat,EPSG:4283` (short EPSG form =
lon/lat order). Omitting the CRS returns HTTP 400; the `urn:ogc:def:crs:EPSG::4283`
form flips to lat/lon order. The Region value object owns this so callers never
see it.

**Native CRS** is EPSG:4283 (GDA94 geographic, lon/lat) for both — already the
lon/lat contract we return; no reprojection needed.

**Log-fetch strategy (RESOLVED by probe — this is the make-or-break decision):**
- A BBOX filter on `bh:BoreholeStratigraphyLogs` returns **0 features** — the log
  layers are **not spatially queryable**. Logs cannot be fetched by region directly.
- `CQL ENO IN (...)` works on both header (`eno`) and log (`ENO`) layers, and
  **POST GetFeature** works (dodges GET URL-length limits).
- Therefore the canonical region→logs workflow is: (1) spatial header query →
  collect the ENO set, (2) chunk the ENO list (configurable batch size) into
  `ENO IN (...)` filters sent via **POST**, (3) paginate each chunk. Per-borehole
  lazy loading is only for the single-`Borehole` path, never for a region.
- 2 boreholes returned 31 stratigraphy rows (~15× fan-out) — bulk log pulls are
  large; pagination + chunking are mandatory.

**ArcGIS output SR (RESOLVED):** `f=geojson` returns **no `crs` field** (GeoJSON
defaults to WGS84) and does not guarantee GDA94. Always pass **`outSR=4283`**
explicitly; `f=json&outSR=4283` correctly returns `wkid 4283`. Without it we'd
silently mix 4283/4326 (~1–1.8 m in Australia) and break the single lon/lat
contract.

## Design revisions from architecture review (2026-06-17)

A senior review (Opus 4.8) stress-tested the above. Adopted changes:

**Critical:**
- *Log fetch*: as resolved above — ENO-chunked POST GetFeature, not spatial
  filter, not per-borehole loops for regions.
- *WFS freshness is best-effort*: `numberMatched` alone can't detect same-count
  content edits. Combine `numberMatched` **+ result extent + a max-age TTL**
  (revalidate-by-refetch after N days regardless). Document `force_refresh` as
  the only correctness guarantee.
- *Cache-key stability*: canonicalise region geometry before hashing — round
  coords to fixed precision (~6 dp), `shapely.normalize`, stable WKT. Document
  that subset/superset regions are not reused (each distinct region = new entry).
- *Cache atomicity*: temp-write-then-rename for parquet **and** manifest, a file
  lock around manifest mutation, and `.partial` quarantine for in-progress pulls
  so an interrupted/failed pagination is never committed as a complete entry.
  Define the KeyboardInterrupt / partial-failure contract (interrupt leaves cache
  uncorrupted).

**Should-fix:**
- HTTP policy section: retryable status set (429/502/503/504; never 400/404),
  attempt + backoff caps, honour `Retry-After`, split connect vs read timeouts,
  a politeness inter-request delay + concurrency cap, and a descriptive
  `User-Agent` with contact info (courtesy to a government server).
- Partial-failure mid-pagination: fail cleanly, never cache a truncated dataset.
- Provenance manifest gains `citation`, `license`, `source_url`,
  `service_version`, `cache_format_version`; `BoreholeCollection.citation()`.
- Pin the log schema: depth datum + units (m, and relative to what), dtypes,
  and an invalid-interval / null policy (e.g. top > bottom, missing depths) so
  downstream interpolation isn't poisoned.
- Structured logging under the `gadata` logger (cache hit/miss/revalidate,
  page N/M, retries), off by default.
- Cache ops + dry-run: `count_only=True` (uses `resultType=hits` /
  `returnCountOnly`), request-inspection, and `cache.list()/info()/clear()`,
  plus a documented region prefetch idiom for offline/field use.

**Nice-to-have / deferred:**
- Demote `interpolation/` from a package directory to a `port` (protocol) seam
  until its consumer contract exists — avoid empty architectural decoration.
- Cache-format version + migration path.
- VCR fixtures recorded on small bboxes; a weekly live contract test (skipped by
  default) to catch GA schema/endpoint drift.

## Build phases

1. Skeleton: package scaffold, pyproject, domain value objects, ports.
2. HTTP + pagination adapters (WFS, ArcGIS) returning raw GeoJSON.
3. DatasetCache + manifest + hybrid freshness.
4. Mappers + `Borehole`/`BoreholeCollection` + `GADataClient` facade.
5. Hydrogeology path.
6. Tests with recorded fixtures; docs/usage example.
7. Define the `interpolation` seam (signatures only) for you to grow.
