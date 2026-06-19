# NGIS integration — implementation plan

Status: **implemented (Tasks 1–8), all gates green.** Everything below shipped in
reviewed increments — the domain `source`/raw-bag changes and `ConstructionInterval`,
the two-stage `ensure_gdb` → `optimise_state` pipeline, the fast-DB cache with its
stamp gate, the NGIS mapper, the `NGISClient` and federated `GroundwaterClient`,
the live primary+mirror smoke checks, and these docs. The notes that follow are
kept as the design record (settled decisions marked ✅; the ❓ forks were all
resolved during the build). This plan extends the package with a second borehole
source (the BoM NGIS state cores) and a federated "one box, all sources" query.

## 1. Goal

Let a user get bore data **from a lon/lat box**, the same way they load boreholes
from GA today, but drawing on the local NGIS state geodatabases as well — ideally
**checking every source that covers the box** in a single call:

```python
# today (GA only)
ga = GADataClient()
bc = ga.boreholes(bbox=(143.0, -35.8, 146.3, -33.9))

# target: federated across GA + NGIS state cores that intersect the box
gw = GroundwaterClient()                     # name ❓
bc = gw.boreholes(bbox=(143.0, -35.8, 146.3, -33.9))   # GA + NSW(+VIC) NGIS
bc.load_logs("stratigraphy")
gdf = bc.stratigraphy_geodataframe()         # one tidy frame, 'source' column
```

The NGIS payload is the prize: dense stratigraphy with complete per-interval AHD
top/base elevations, which the GA WFS lacks regionally.

## 2. Already done (foundation, all gates green)

- ✅ Per-interval AHD elevations modelled source-agnostically on
  `StratigraphyInterval` / `EarthMaterialInterval` (`top_elev_m_ahd`,
  `bottom_elev_m_ahd`), read from `INTERVAL_BEGIN/END_ELEV_M_AHD`; exports gained
  the columns. GA leaves them null; NGIS will fill them.
- ✅ `austrata/infrastructure/ngis_sources.py` — pinned registry: per state, the
  data.gov.au resource URL + landing page, the public mirror URL, expected zip
  size + md5, the `.gdb` path inside the archive, vintage, and citation.
- ✅ All three cores mirrored to `s3://gadopt/austrata/ngis/` (public-read; verified
  the credential-free HTTPS URL serves byte-for-byte).
- ✅ Confirmed fiona reads the gdbs and `TopElev`/`BottomElev` are populated.

## 3. How NGIS plugs into the architecture

Respecting the clean-architecture dependency rule (`domain` → `ports` →
`application` → `infrastructure` → `client`):

- **domain** — unchanged except a source tag (see §7). NGIS bores/logs become the
  *same* `Borehole` / `StratigraphyInterval` / `EarthMaterialInterval` objects
  ("comparable under the hood").
- **infrastructure** — the **two-stage pipeline**: raw gdb (source) → optimiser →
  fast DB (what every query reads). A consistency check gates each boundary.
  - `ngis_sources.py` ✅ — the registry (pins each state's url, mirror, md5, vintage).
  - `ngis_download.py` — `ensure_gdb(state)`: return a trusted `.gdb`. **Local-first**
    — if a usable `.gdb` is already on disk (your `data/ngis/` or a configured NGIS
    dir), use it; else download the zip (primary → mirror fallback), **verify size +
    md5 against the registry** (the remote↔local consistency check), extract.
  - `ngis_optimiser.py` — `optimise_state(gdb, state)`: the fiona pass that converts
    a whole state once. Reads `NGIS_Bore`, `NGIS_BoreholeLog`, `NGIS_LithologyLog`,
    `NGIS_ConstructionLog`; builds a bores GeoDataFrame (Point from Longitude/
    Latitude, EPSG:4283) and denormalises the bore lon/lat + geometry onto each log
    row by `HydroCode`. Output is the **fast DB**: per-state, per-layer GeoParquet
    carrying **every original NGIS column verbatim** (no GA-key renaming — this is
    what guarantees miss-nothing and removes any silent-`None` key-mismatch risk),
    stamped with `{state, vintage, gdb_md5, optimiser_version}`. Carries an
    `OPTIMISER_VERSION` constant, bumped whenever the mapping changes. The curated
    NGIS-column → domain-field mapping lives in a dedicated NGIS mapper (with the
    client), the way GA has `feature_mapper`; domain stays pure and shared.
- **application**
  - `fetch_ngis.py` — one `DatasetCache` entry per `(state, layer, optimiser_version)`
    holding the fast DB. Its `FetchPlan.fetch()` runs `ensure_gdb` → `optimise_state`
    → returns `(GeoDataFrame, provenance)`. Freshness (the gdb↔fast-DB consistency
    check): the stamped `gdb_md5` + `optimiser_version` are the fingerprint; a fast
    DB whose stamp still matches the registry is trusted and the gdb is never
    touched. A version bump (or `force_refresh`) auto-rebuilds — re-downloading the
    gdb only if it is no longer on disk. So once built, the 3 GB of raw gdb is
    disposable.
- **client**
  - `NGISClient` — NGIS-only facade, same method names/return types as
    `GADataClient` (`boreholes`, `borehole`, log loading/export).
  - Federation layer (`GroundwaterClient` ❓) — fans a box out to GA + the NGIS
    states whose extent intersects it, returns one combined `BoreholeCollection`
    tagged by `source`.

## 4. The federated box query

```
boreholes(bbox) ─┬─ GADataClient.boreholes(bbox)        → GA bores  (source='GA')
                 ├─ NGISClient('NSW').boreholes(bbox)    → NSW bores (source='NGIS:NSW')
                 └─ NGISClient('VIC').boreholes(bbox)    → VIC bores (source='NGIS:VIC')
                              (only states whose extent intersects the box)
        → merge into one BoreholeCollection, each Borehole carries .source
```

- **State routing:** each NGIS core has a known geographic extent; intersect the
  box with hardcoded state extents so a NSW box never opens the 1 GB VIC gdb ❓.
- **No record-level dedup by default** (GA uses ENO, NGIS uses HydroCode — no shared
  key; merging would be fuzzy). Return everything, tagged by source ❓.
- **`load_logs` dispatch:** the combined collection routes each bore's log fetch
  by its source (GA → WFS by ENO; NGIS → gdb by HydroCode), then exports one tidy
  frame with a `source` column.

## 5. Component breakdown (files, ~budgets)

| File | Responsibility | ~lines |
|------|----------------|-------:|
| `infrastructure/ngis_sources.py` ✅ | pinned registry | 150 |
| `infrastructure/ngis_download.py` | download + verify + extract | ~140 |
| `infrastructure/ngis_optimiser.py` | whole-state fiona read + join + GA-shape + raw bag | ~200 |
| `infrastructure/ngis_extents.py` *(maybe)* | state bbox routing | ~40 |
| `domain/construction.py` | `ConstructionInterval` value object | ~60 |
| `application/fetch_ngis.py` | fast-DB cache keys + FetchPlan | ~120 |
| `client.py` (or `ngis_client.py`) | `NGISClient` + `GroundwaterClient` | ~180 |
| `tests/test_ngis_*` | offline (fixture gdb) + live smoke | — |

Each file stays under the repo's ~200-line guide.

## 6. Caching & performance model

✅ **Whole state once → fast DB; queries filter the fast DB.** Decided.

- A state's first touch pays a one-time cost: ensure the gdb (download if neither
  the gdb nor a valid fast DB is present) + optimise the whole state. VIC is the
  worst case (~1 GB / 206k bores / 1.1M log rows) — order of a minute or two; a
  `log()` line makes the wait visible rather than a silent hang.
- After that, **every** box query (this region and all future ones) reads the
  cached fast DB and filters by lon/lat in memory — milliseconds, fully offline.
- The fast DB is GeoParquet `DatasetCache` entries, one per `(state, layer)` at the
  current `optimiser_version`. The raw gdb is needed only to *build* the fast DB,
  so it can be discarded afterwards; a stamp mismatch (version bump) triggers a
  rebuild, re-downloading the gdb only if it is gone.
- Later optimisation (not v1): spatially sort the parquet so pyarrow row-group
  statistics enable predicate pushdown, avoiding a full in-memory load per query.
  For the current state sizes a load+filter is already fast enough.

## 7. Domain changes (inclusive — miss nothing)

Two layers per object: a **curated comparable surface** + a **complete raw bag**.

- ✅ **`source` tag** on `Borehole` (`'GA'`, `'NGIS:NSW'`, …) and a source-neutral
  `identifier` (`eno` stays for GA; NGIS uses `HydroCode`, `eno=None`). `source`
  flows into `to_geodataframe()` and every log export.
- ✅ **`source_attributes: dict`** on `Borehole`, `StratigraphyInterval`,
  `EarthMaterialInterval`, `ConstructionInterval` — the **entire** original record,
  verbatim, for whatever isn't promoted. Applied to **both** sources (GA keeps its
  full WFS properties too). On the frozen value objects it is `field(compare=False)`
  so equality/hash stay defined on the real fields. Exports gain an opt-in to
  explode it into namespaced columns.
- ✅ **Promoted NGIS fields** (named attributes, broadly useful): `bore_depth_m`,
  `drilled_depth_m`, `drilled_date` on `Borehole`; a free-text `comment` on
  `StratigraphyInterval` (the NGIS `Comment`). Everything else rides in the bag.
- ✅ **New `ConstructionInterval`** value object (screens/casing): depths + AHD +
  `construction_type`, `material`, `inner_diameter`, `outer_diameter`, `property`,
  `property_size`, `drill_method` + raw bag. NGIS-only (GA has no equivalent);
  add `kind="construction"` to `load_logs` and a `construction_geodataframe()`
  export. Lives in a new `domain/construction.py` (keeps files < 200 lines).
- ✅ Map NGIS → domain: `NGIS_Bore` → `Borehole`; `NGIS_BoreholeLog.Description` →
  stratigraphy `unit`, depths/AHD as above; `NGIS_LithologyLog` → earth-material;
  `NGIS_ConstructionLog` → construction. **"Unknown" formation rows are kept and
  flagged `valid=False`** (`invalid_reason='unknown formation'`), never dropped.

## 8. Provenance & reproducibility

Every NGIS cache entry records source string, data.gov.au URL, licence (CC BY),
vintage, and the artifact md5 — surfaced through the existing `provenance()` /
`citation()`. A result always traces back to an exact pinned download.

## 9. Testing

- Offline: a tiny synthetic `.gdb` fixture (a few bores + log rows) so the reader,
  normaliser, and federation are exercised with no network and no 3 GB.
- Live smoke: the data.gov.au resources and the mirror stay reachable (size/HEAD),
  wired into the nightly health workflow next to the GA checks.
- Mark heavy full-state reads `heavy` (on-demand only).

## 10. Decisions (resolved)

- ✅ Federation via a new **`GroundwaterClient`** wrapping GA + NGIS; `GADataClient`
  and `NGISClient` stay usable on their own.
- ✅ **No record-level dedup** — return every bore tagged by `source`.
- ✅ Box query returns **headers only**; logs attach via `load_logs(...)` (GA's flow).
- ✅ **Local-first, else download** to a configured NGIS dir (raw gdbs gitignored).
- ✅ **Whole-state-once → fast DB**, the two-stage optimiser pipeline (§3, §6).
- ✅ Map **hydrostratigraphy + lithology** (construction ignored).
- ✅ Keep **"Unknown"** formation rows, flagged `valid=False` (caller filters).
- ✅ **Boreholes only**; hydrogeology stays GA-only on `GADataClient`.
- ✅ Discarded NGIS fields documented in `data/ngis/NGIS_SCHEMA.md`.

Still soft (decide in code, low stakes): the configured NGIS dir name/env var;
state-extent routing source (hardcoded vs read once from the gdb); whether to add
a free-text `comment` field to `StratigraphyInterval` to keep NGIS `Comment`.

## 11. Build order

1. `ngis_download.py` — `ensure_gdb` (local-first, primary→mirror, md5-verify).
2. `ngis_optimiser.py` — whole-state fiona pass → GA-shaped fast-DB frames.
3. `fetch_ngis.py` + `NGISClient` — fast-DB cache entries + single-source box query
   working end to end (headers, then `load_logs`).
4. domain `source` tag on `Borehole` (+ `source` column in exports).
5. `GroundwaterClient` — federated box query across GA + intersecting NGIS states.
6. tests — offline (tiny synthetic gdb fixture) + live smoke (urls reachable).
7. docs — fold the data.gov.au URL list into `NGIS_SCHEMA.md`; update `DESIGN.md`.
