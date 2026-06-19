# NGIS geodatabase structure — extraction notes

These are the Bureau of Meteorology **National Groundwater Information System
(NGIS)** state extracts, downloaded from data.gov.au. They are ESRI File
Geodatabases (`.gdb` directories) built on the ArcHydro for Groundwater data
model. This note describes their structure for someone writing extraction code,
with a focus on getting, per bore: the **identified formation name** and the
**top/base of each formation**.

Short answer to the main question: yes. The hydrostratigraphy log table
(`NGIS_BoreholeLog`) gives one row per logged interval with a formation name and
both the depth and the AHD elevation of the top and base. See
[The formation top/base data](#the-formation-topbase-data-the-important-part).

## What's on disk

Under `data/ngis/` (each zip extracts to a folder containing one `.gdb`):

| State | Geodatabase | Bores | Lithology rows | Hydrostrat rows |
|-------|-------------|------:|---------------:|----------------:|
| NSW | `nsw_extract/.../NSW_NGIS_Core_v2pt3.gdb` | 141,076 | 835,411 | 126,992 |
| VIC | `vic_extract/.../DEPI_NGIS_Core_v2pt3_20140321.gdb` | 206,233 | 1,130,313 | 119,450 |
| QLD | `qld_core/.../NGIS_QLD_Core.gdb` | 134,989 | 359,435 | 101,817 |

Two extra files in the folder are not full cores and can be ignored for log
extraction: `qld_gsq_petroleum.zip` (bore locations only, no logs) and
`nsw_ngis_20140701.zip` (older NSW; contains only a licence-metadata gdb,
superseded by the v2pt3 core above).

There is no SA / WA / NT / TAS extract on data.gov.au. They exist only inside the
national compilation, whose data.gov.au download is currently truncated/corrupt
(reported to the custodian). Those states need a fresh national file from BoM.

## Coordinate reference systems

All three cores use the same CRS, a compound CRS:

- Horizontal: **GDA94 / Australian Albers, EPSG:3577** (metres). This is the
  `geometry`/`SHAPE` of `NGIS_Bore`.
- Vertical: **AHD height, EPSG:5711** (metres). All the elevation fields
  (`TopElev`, `BottomElev`, `RefElev`, ...) are metres AHD.

Each bore *also* carries plain `Latitude` / `Longitude` columns in **GDA94
geographic, EPSG:4283** (degrees), so you can skip the projected geometry
entirely and read lon/lat straight from the attributes if that's easier. The two
agree; use whichever you prefer.

## How the tables join

Everything keys off the bore. Two equivalent join keys exist:

- `HydroCode` — string, e.g. `"GW029313.1.1"`. Present on the bore **and** on
  every log row. This is the simplest join key (string equality).
- `HydroID` (on `NGIS_Bore`) == `BoreID` (on every log table) — integer, same
  thing under two names.

The log tables (`NGIS_*Log`) are **non-spatial tables** (no geometry of their
own). You get a log row's location by joining back to `NGIS_Bore` on
`HydroCode` / `BoreID` and taking the bore point.

```
NGIS_Bore (point)  1 ──< many  NGIS_BoreholeLog     (hydrostratigraphy: formations)
                   1 ──< many  NGIS_LithologyLog    (driller/geologist lithology)
                   1 ──< many  NGIS_ConstructionLog (screens / casing)
        join on HydroCode  (or NGIS_Bore.HydroID == log.BoreID)
```

## The formation top/base data (the important part)

Table: **`NGIS_BoreholeLog`** — one row per hydrostratigraphic interval. This is
where the named formations and their tops/bases live.

| Field | Type | Meaning |
|-------|------|---------|
| `BoreID` | int | join key → `NGIS_Bore.HydroID` |
| `HydroCode` | str | join key → `NGIS_Bore.HydroCode` |
| `Description` | str(255) | **the formation / hydrostratigraphic unit name** (free text, e.g. `"Shepparton Formation"`, `"Calivil Formation"`, `"Upper Renmark Group"`) |
| `FromDepth` | float | **depth to top** of the interval, metres below `RefElev` |
| `ToDepth` | float | **depth to base** of the interval, metres below `RefElev` |
| `TopElev` | float | **elevation of top**, metres AHD |
| `BottomElev` | float | **elevation of base**, metres AHD |
| `RefElev` | float | reference elevation the depths are measured from, metres AHD |
| `RefElevDesc` | str | what `RefElev` is (natural surface, etc.) |
| `HGUID` / `HGUNumber` | int | coded hydrogeologic-unit id — **often "Unknown" (`-19999999` / `-9999`); do not rely on it. Use `Description`.** |
| `Author`, `Source`, `Comment` | str | provenance |
| `LogType` | int | coded log type |

Depth vs elevation: the two are consistent, related by the reference elevation,
roughly `TopElev ≈ RefElev − FromDepth` and `BottomElev ≈ RefElev − ToDepth`.
Use depths if you want metres-below-surface; use the `*Elev` fields if you want
absolute AHD surfaces (what you'd want for building layer geometry). Both are
provided so you don't have to compute them.

Population (how complete these fields are):

| Field | NSW | VIC | QLD |
|-------|----:|----:|----:|
| `Description` (formation name) | 100% | 100% | 100% |
| `FromDepth` | 100% | 83% | 100% |
| `ToDepth` | 100% | 99% | 100% |
| `TopElev` | 99% | 83% | 100% |
| `BottomElev` | 99% | 99% | 100% |

So the formation name is always present. Depth/elevation is essentially complete
for NSW and QLD; for VIC a minority of rows are missing the *top* depth/elevation
(but keep the base), so handle nulls.

The `Description` vocabulary is state-specific free text (NSW ~166 distinct
values, VIC ~1,700, QLD ~1,500), and includes an `"Unknown"` bucket
(NSW ~7,400 rows, VIC ~9,300). Top values per state, to give the flavour:

- **NSW:** Shepparton Formation, Calivil Formation, Upper Renmark Group, Parilla
  Sands, Cowra Formation, Lachlan Formation, Coonambidgal Formation.
- **VIC:** Loxton Sand, Morwell Formation, Thorpdale Volcanics, plus many
  "Undifferentiated ..." buckets.
- **QLD:** Main Range Volcanics, Elliott Formation, Marburg Subgroup, Walloon
  Coal Measures, Hutton Sandstone.

Because it's free text, matching to a target set of model layers should be
case-insensitive and tolerant (e.g. group `"Upper/Middle/Lower Renmark Group"`
under `Renmark`). Filter out `"Unknown"` before use.

## Supporting tables (for reference)

`NGIS_LithologyLog` — driller's/geologist's lithology, finer-grained than the
formations. Same key fields (`BoreID`, `HydroCode`, `FromDepth`, `ToDepth`,
`TopElev`, `BottomElev`) plus `MajorLithCode` / `MinorLithCode` (coded, e.g.
`CLAY`, `SAND`, `GRVL`) and a free-text `Description`. Useful if you want the raw
material column rather than the interpreted formation. Note these intervals can
**overlap** (multiple logging schemes recorded against the same bore at the same
depths) — dedupe/select a `LogType` before stacking them.

`NGIS_ConstructionLog` — screen/casing intervals (same depth/elevation fields).
Not needed for formation geometry.

`NGIS_Bore` — the header. Useful fields beyond the keys and `Latitude`/
`Longitude`: `BoreDepth`, `DrilledDepth`, `RefElev` (m AHD), `LandElev`,
`Status`, `DrilledDate`, `FType`, `StateBoreID`.

`NGIS_HydrogeologicUnit` — a tiny lookup table (≈13 rows in NSW) mapping the
coded `HGUID` to unit names. It is sparse and does not cover most bores, which is
why the per-interval `Description` text is the reliable source of the formation
name, not this lookup.

## Reading gotchas

- **Driver:** read with **`fiona`** or **GDAL/OGR** (`ogrinfo`). `pyogrio`
  (and therefore `geopandas.read_file(engine="pyogrio")`, the default) raises
  `GeometryError: Geometry type is not supported: 2147483648` on these gdbs
  because of an exotic geometry-type flag on some layers. Use
  `geopandas.read_file(path, layer=..., engine="fiona")` or read attributes with
  `fiona.open(...)` directly. The log tables are non-spatial so they read fine
  either way once you avoid the geometry layers.
- **Field name aliases:** some integer key fields carry an OGR "alternative
  name" (e.g. `HydroID` aliased as `HGUID` on the unit table). Address fields by
  their primary name as listed above.
- **Sentinels:** treat `-9999` / `-999999` / `-19999999` as null/Unknown in the
  numeric and HGU fields.
- **Vintage:** these are 2014 extracts (NSW 2014-11, VIC 2014-03, QLD core). Fine
  for stratigraphy, but not current for time-series things like water levels.

## Minimal extraction recipe (formation tops/bases per bore)

```python
import fiona

GDB = "nsw_extract/.../NSW_NGIS_Core_v2pt3.gdb"

# 1) bore lon/lat (and ref elevation) keyed by HydroCode
bores = {}
with fiona.open(GDB, layer="NGIS_Bore") as src:
    for f in src:
        p = f["properties"]
        bores[p["HydroCode"]] = (p["Longitude"], p["Latitude"], p["RefElev"])

# 2) formation intervals: name + top/base depth + top/base AHD elevation
rows = []
with fiona.open(GDB, layer="NGIS_BoreholeLog") as src:
    for f in src:
        p = f["properties"]
        name = (p.get("Description") or "").strip()
        if not name or name == "Unknown":
            continue
        lon, lat, refelev = bores.get(p["HydroCode"], (None, None, None))
        rows.append({
            "hydrocode": p["HydroCode"],
            "lon": lon, "lat": lat,
            "formation": name,
            "from_depth_m":  p.get("FromDepth"),   # top, m below RefElev
            "to_depth_m":    p.get("ToDepth"),     # base, m below RefElev
            "top_elev_m_ahd":    p.get("TopElev"),
            "bottom_elev_m_ahd": p.get("BottomElev"),
        })
```

`lower_murrumbidgee_hydrostrat.csv` in this folder was produced exactly this way
(clipped to a lon/lat box and grouped Shepparton/Calivil/Renmark) and can serve
as a worked example of the output shape.

## What the `austrata` normaliser maps (inclusive — nothing dropped)

The package normalises NGIS into the **same** domain objects as the GA WFS
(`Borehole`, `StratigraphyInterval`, `EarthMaterialInterval`, plus an NGIS-only
`ConstructionInterval`). **Policy: miss nothing.** Each object has a *curated*
surface of named fields (the comparable + high-value ones, listed below) **and** a
`source_attributes` dict carrying the **entire** original record verbatim. So a
field that isn't promoted to a named attribute is still present — it lives in the
raw bag, never discarded. The tables below say which original fields are *promoted*
to named attributes; everything else is in `source_attributes`.

One caveat on faithfulness: the fast DB is GeoParquet, so `source_attributes`
values are **value**-faithful but may be **dtype**-normalised by the parquet layer
— e.g. an integer sentinel like `HGUID = -9999` can come back as `-9999.0`. The
value is intact; don't be surprised if an int round-trips as a float.

### Layers used

Four of the 14 layers are read: `NGIS_Bore` (headers), `NGIS_BoreholeLog`
(→ stratigraphy), `NGIS_LithologyLog` (→ earth material) and `NGIS_ConstructionLog`
(→ construction; screens/casing — NGIS-only).

**Layers not read** (not borehole data): `NGIS_HydrogeologicUnit` (sparse coded
lookup; we use the per-interval `Description` instead), `NGIS_BoreLine`,
`NGIS_ConstructionLine` (line geometries), `NGIS_ManagementZone` (polygons),
`NGIS_Geovolume`, `NGIS_GeoRasters` and the four `fras_*_NGIS_GeoRasters` raster
layers.

**Files ignored:** `qld_gsq_petroleum.zip` (bore locations only, no logs) and
`nsw_ngis_20140701.zip` (older NSW; licence-metadata only, superseded). Also the
projected `SHAPE` geometry (EPSG:3577 Albers) — we read the `Longitude`/`Latitude`
attribute columns (EPSG:4283) and discard the Albers point.

### `NGIS_Bore` → `Borehole`

| NGIS field | maps to | note |
|------------|---------|------|
| `HydroCode` | `identifier` (+ join key) | NGIS has no ENO, so `eno` is `None` |
| `Longitude` / `Latitude` | `longitude` / `latitude` | EPSG:4283 |
| `RefElev` | `elevation_m` | m AHD |
| `RefElevDesc` | `depth_reference` | |
| `Status` | `status` | |
| `FType` | `purpose` | bore type |
| `StateBoreID` | `name` | |
| `BoreDepth` | `bore_depth_m` | promoted (total depth) |
| `DrilledDepth` | `drilled_depth_m` | promoted |
| `DrilledDate` | `drilled_date` | promoted |
| `Agency` | `data_custodian` | coded int |
| `StateTerritory` | `state` | resolved to the state code being read |

**`NGIS_Bore` fields in `source_attributes`** (kept, not promoted): `HydroID`
(also used internally as the join key), `StatePipeID`, `WCode`, `HGUID`,
`HGUNumber`, `NafHGUNumber`, `Easting`, `Northing`, `Projection`, `ProjectionZone`,
`CoordMethod`, `HeightDatum`, `RefElevMethod`, `TsRefElev`, `TsRefElevDesc`,
`TsRefElevMethod`, `LandElev`, `LandElevMethod`, `IsMultiPipe`, `BoreLineCode`,
`WorksID`, `LicenceExtractID`, `LicenceExtractVolume`, `LicenceUseID`.

### `NGIS_BoreholeLog` → `StratigraphyInterval`

| NGIS field | maps to |
|------------|---------|
| `Description` | `unit` (the formation name) |
| `FromDepth` / `ToDepth` | `top_depth` / `bottom_depth` (m below RefElev) |
| `TopElev` / `BottomElev` | `top_elev_m_ahd` / `bottom_elev_m_ahd` |
| `RefElev` | `ref_elevation_m_ahd` |
| `Comment` | `comment` (promoted free-text note, e.g. *"Bottom of renmark at 186 m"*) |
| `HydroCode` | join key → borehole point |

GA-only stratigraphy fields with **no NGIS equivalent** are left `None`:
`unit_pid`, `older_age`, `younger_age`, `older_age_ma`, `younger_age_ma`,
`top_contact`, `base_contact`, `geological_province`, `stratigraphy_id`.

**`NGIS_BoreholeLog` fields in `source_attributes`** (kept, not promoted):
`BoreID` (redundant with `HydroCode`), `RefElevDesc`, `HGUID`, `HGUNumber`,
`NafHGUNumber` (unreliable coded HGU — do not rely on it), `Author`, `Source`,
`Scenario`, `LogType`.

### `NGIS_LithologyLog` → `EarthMaterialInterval`

| NGIS field | maps to |
|------------|---------|
| `MajorLithCode` | `lithology_group` |
| `MinorLithCode` | `lithology_qualifier` |
| `Description` | `description` |
| `FromDepth` / `ToDepth` | `top_depth` / `bottom_depth` |
| `TopElev` / `BottomElev` | `top_elev_m_ahd` / `bottom_elev_m_ahd` |
| `RefElev` | `ref_elevation_m_ahd` |
| `HydroCode` | join key → borehole point |

GA-only `lithology` is left `None` (NGIS gives coded major/minor, not a single
lithology string). **`NGIS_LithologyLog` fields in `source_attributes`:** `BoreID`,
`RefElevDesc`, `Source`, `LogType`. Recall lithology intervals can **overlap**
(multiple `LogType` schemes at the same depths) — dedupe/select before stacking.

### `NGIS_ConstructionLog` → `ConstructionInterval` (NGIS-only)

Screen/casing intervals. No GA equivalent, so this is an NGIS-only domain object
exposed via `load_logs("construction")`.

| NGIS field | maps to |
|------------|---------|
| `ConstructionType` | `construction_type` |
| `Material` | `material` |
| `InnerDiameter` / `OuterDiameter` | `inner_diameter` / `outer_diameter` |
| `Property` / `PropertySize` | `property` / `property_size` |
| `DrillMethod` | `drill_method` |
| `FromDepth` / `ToDepth` | `top_depth` / `bottom_depth` |
| `TopElev` / `BottomElev` | `top_elev_m_ahd` / `bottom_elev_m_ahd` |
| `RefElev` | `ref_elevation_m_ahd` |
| `HydroCode` | join key → borehole point |

**`NGIS_ConstructionLog` fields in `source_attributes`:** `BoreID`, `RefElevDesc`,
`LogType`.

## Download sources

The package never hardcodes these in scattered places: the authoritative pinned
copy lives in `austrata/infrastructure/ngis_sources.py`, which records for each
state the data.gov.au resource URL, our S3 mirror, the expected zip size and md5,
the `.gdb` path inside the archive, the vintage, and the state extent. The
downloader (`ngis_download.ensure_gdb`) tries the data.gov.au resource first and
falls back to the mirror, and it verifies the downloaded archive against the
pinned size and md5 before trusting it — a corrupt or reissued file is rejected,
not used. Update the registry, not this table, when a custodian reissues a core;
the URLs below are reproduced from it for convenience.

### New South Wales (vintage 2014-11, zip 98,390,403 bytes)

- Landing page: https://data.gov.au/data/dataset/b0f37930-a810-4819-8fb6-8008538fa53b
- Direct download (data.gov.au): https://data.gov.au/data/dataset/b0f37930-a810-4819-8fb6-8008538fa53b/resource/0cbae516-efef-4ab5-8685-b0570ac9ab4c/download/6c364d09-fc3b-47c3-aa98-6c702d3d8137.zip
- S3 mirror (fallback): https://gadopt.syd1.digitaloceanspaces.com/austrata/ngis/nsw_ngis_core_v2pt3.zip
- md5: `8c5006f6e136eec8700464f8d1783373`

### Victoria (vintage 2014-03, zip 1,105,719,595 bytes)

- Landing page: https://data.gov.au/data/dataset/2eb2630f-313e-4542-9137-c6d7d82171b0
- Direct download (data.gov.au): https://data.gov.au/data/dataset/2eb2630f-313e-4542-9137-c6d7d82171b0/resource/54963b10-0515-43f5-a7ec-285f5fecacb9/download/fc6dbf39-d786-4412-b631-04695db4cc90.zip
- S3 mirror (fallback): https://gadopt.syd1.digitaloceanspaces.com/austrata/ngis/vic_ngis_core_2014.zip
- md5: `1138ee6a47e3c37cef174c20b9f6c76f`

### Queensland (vintage 2014, zip 48,203,959 bytes)

- Landing page: https://data.gov.au/data/dataset/d2c06543-7389-4ac2-86c0-74b077bcdaa5
- Direct download (data.gov.au): https://data.gov.au/data/dataset/d2c06543-7389-4ac2-86c0-74b077bcdaa5/resource/02424659-834a-4a60-bc83-a1b4ddd23ee8/download/9f7573c0-e238-4a3c-a62b-a0326eaa9254.zip
- S3 mirror (fallback): https://gadopt.syd1.digitaloceanspaces.com/austrata/ngis/qld_ngis_core.zip
- md5: `0e3b845c35c8e37a867ee5f9e7132a32`

All three are licensed CC BY 4.0. SA/WA/NT/TAS have no standalone core on
data.gov.au (they live only inside the national compilation, whose download is
currently truncated), so they are deliberately absent from the registry. The
nightly smoke health check (`tests/test_ngis_live.py`, markers `live and smoke`)
HEADs both the data.gov.au resource and the mirror for each state and compares
the Content-Length against the pinned size, so a custodian reissue/truncation or
a broken mirror surfaces early — and tells you which of the two is down.

