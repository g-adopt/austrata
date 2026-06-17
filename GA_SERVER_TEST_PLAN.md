# GA Server Test Plan

A contract / health / data-availability test plan for the **live** Geoscience
Australia web services that `gadata` depends on. These checks do **not** test our
own code — they verify that the external GA servers are reachable and healthy,
that their advertised capabilities and schema are intact, and that real data and
samples still download correctly. When one of these fails it means the upstream
service has drifted, degraded, or gone down — not that gadata has a bug.

## Endpoints under test

| Name | Base URL |
|------|----------|
| Boreholes WFS | `https://services.ga.gov.au/gis/boreholes/wfs` (also `/ows`) |
| Hydrogeology ArcGIS | `https://services.ga.gov.au/gis/rest/services/Hydrogeology_of_Australia/MapServer` |

Reference region used throughout (ACT/Canberra, lon/lat GDA94):
`bbox = 148.9,-35.6,149.3,-35.1`. Known boreholes carrying logs: **ENO 35147,
35151**. Native CRS for both backends is **EPSG:4283** (GDA94 geographic,
lon/lat). WFS bbox values must carry the explicit `EPSG:4283` short-form suffix.

Severity legend: **critical** = a failure means gadata cannot fetch real data or
will silently return wrong data; **important** = degraded confidence / a contract
has drifted; **nice-to-have** = good signal, not blocking.

---

## 1. Connectivity & server health

| ID | Description | Request | Pass criteria | Severity |
|----|-------------|---------|---------------|----------|
| C1 | WFS host DNS + TLS resolves | TLS handshake to `services.ga.gov.au:443` | Resolves, valid non-expired cert, chain verifies | critical |
| C2 | WFS base responds | `GET .../boreholes/wfs?service=WFS&version=2.0.0&request=GetCapabilities` | HTTP 200 | critical |
| C3 | ArcGIS service responds | `GET .../Hydrogeology_of_Australia/MapServer?f=json` | HTTP 200, JSON body | critical |
| C4 | WFS response time within bound | time C2 | First byte < 10 s (warn), < 30 s (fail) | important |
| C5 | ArcGIS response time within bound | time C3 | First byte < 10 s (warn), < 30 s (fail) | important |
| C6 | No maintenance / HTML error page | inspect C2/C3 bodies | Content-Type is XML (WFS) / JSON (ArcGIS); body is not an HTML "service unavailable" page | critical |
| C7 | No unexpected redirect off ga.gov.au | follow redirects on C2/C3 | Final URL still on `services.ga.gov.au` | important |
| C8 | HTTP status sanity on a trivial query | `GET .../MapServer/0?f=json` | HTTP 200 (not 403/500/503) | critical |

---

## 2. WFS capability / contract

| ID | Description | Request | Pass criteria | Severity |
|----|-------------|---------|---------------|----------|
| W1 | GetCapabilities parses as valid XML | C2 body | Parses as WFS 2.0.0 capabilities document, no XML error | critical |
| W2 | All expected layers advertised | parse `FeatureType/Name` from C2 | Present: `gsmlp:BoreholeView`, `bh:BoreholeStratigraphyLogs`, `bh:BoreholeEarthMaterialLogs`, `bh:Boreholes`, `bh:BoreholeConstructionLogs`, `bh:BoreholeDirectionalSurveyStations`, `bh:BoreholeSamples`, `gsmlbh:Borehole` | critical |
| W3 | `application/json` output supported | parse capabilities `OutputFormat` / probe | `application/json` listed as a GetFeature output format | critical |
| W4 | `resultType=hits` returns numberMatched | `GetFeature&typeNames=gsmlp:BoreholeView&resultType=hits` | XML root carries a parseable integer `numberMatched` attribute | critical |
| W5 | Header schema (property names) unchanged | `GetFeature&typeNames=gsmlp:BoreholeView&count=1&outputFormat=application/json` | Properties include lowercase `eno` plus the header fields gadata maps (name, depth, drill metadata). Flag any added/removed/renamed key | critical |
| W6 | Stratigraphy log schema unchanged | `GetFeature&typeNames=bh:BoreholeStratigraphyLogs&count=1&outputFormat=application/json` | Properties include UPPERCASE `ENO`, `BOREHOLE_PID`, `BOREHOLE_NAME`, and top/bottom depth + stratigraphic unit fields | critical |
| W7 | Earth-material log schema unchanged | `GetFeature&typeNames=bh:BoreholeEarthMaterialLogs&count=1&outputFormat=application/json` | Properties include UPPERCASE `ENO`, `BOREHOLE_PID`, depth fields + material/description fields | critical |
| W8 | Native CRS is EPSG:4283 | inspect `crs` of W5 GeoJSON / capabilities `DefaultCRS` | Declares GDA94 / EPSG:4283 (lon/lat) | important |
| W9 | bbox **requires** explicit CRS suffix | `...&bbox=148.9,-35.6,149.3,-35.1` (no CRS) | Returns HTTP 400 (regression guard: confirms our mandatory suffix is still required) | important |
| W10 | bbox with `EPSG:4283` short form = lon/lat order | `...&bbox=148.9,-35.6,149.3,-35.1,EPSG:4283` | HTTP 200, features fall inside the ACT box | critical |
| W11 | Axis-order trap: `urn:` form flips to lat/lon | `...&bbox=...,urn:ogc:def:crs:EPSG::4283` | Behaves as lat/lon order (documented quirk) — assert it still flips so the Region value object's choice stays correct | important |
| W12 | WFS sends NO ETag/Last-Modified | inspect headers on W5 | No `ETag`, no `Last-Modified` (confirms hits-fingerprint freshness path is still needed) | important |
| W13 | POST GetFeature accepted | POST a GetFeature with a small `ENO IN (...)` CQL body | HTTP 200, returns features (POST path for long ENO lists still works) | critical |

---

## 3. ArcGIS capability / contract

| ID | Description | Request | Pass criteria | Severity |
|----|-------------|---------|---------------|----------|
| A1 | Service JSON reachable & parses | `MapServer?f=json` | HTTP 200, JSON, lists layers | critical |
| A2 | Layer 0 JSON reachable | `MapServer/0?f=json` | HTTP 200, JSON | critical |
| A3 | Layer 0 geometry type is polygon | A2 `geometryType` | `esriGeometryPolygon` | critical |
| A4 | maxRecordCount unchanged | A2 `maxRecordCount` | Equals 2000 (warn if changed — affects pagination math) | important |
| A5 | supportsPagination true | A2 `advancedQueryCapabilities.supportsPagination` | `true` (paged pulls depend on this) | critical |
| A6 | Field list unchanged | A2 `fields[].name` | Includes `aquif_ty`, `distbn`, `prodty`, `type`, `feature`, `ufi`. Flag any drift | critical |
| A7 | ArcGIS sends an ETag | inspect headers on a `/query` response | `ETag` present, `Cache-Control: must-revalidate` (conditional-GET freshness path) | important |
| A8 | `editingInfo`/`modified` still null | A1/A2 | `modified`/`editingInfo` null — confirms ETag (not lastEditDate) is the freshness signal | nice-to-have |
| A9 | `outSR=4283` returns wkid 4283 | `MapServer/0/query?where=1=1&resultRecordCount=1&f=json&outSR=4283&returnGeometry=true` | `spatialReference.wkid == 4283` | critical |
| A10 | `f=geojson` omits crs (must pass outSR) | `.../query?...&f=geojson` | No `crs` field present (regression guard for the silent-4326 trap) | important |
| A11 | exceededTransferLimit flag exposed | unbounded `/query?where=1=1&f=geojson` | Response carries `exceededTransferLimit` boolean | important |
| A12 | WMS/WFS off the MapServer reachable | `MapServer/WFSServer?request=GetCapabilities` / `WMSServer?...` | HTTP 200 (nice-to-have; we use REST primarily) | nice-to-have |

---

## 4. Data download / sampling

| ID | Description | Request | Pass criteria | Severity |
|----|-------------|---------|---------------|----------|
| D1 | Header sample in ACT bbox returns features | `gsmlp:BoreholeView&bbox=...,EPSG:4283&count=50&outputFormat=application/json` | `> 0` features, each with Point geometry | critical |
| D2 | Header geometry valid & in-bounds | inspect D1 geometries | All coords within Australian bounds (lon 112–154, lat -44 to -10) and inside the ACT box | critical |
| D3 | hits count matches paginated pull | bbox `resultType=hits` vs full paginated `count`/`startIndex` sweep over same bbox | Sum of pages == numberMatched (end-to-end pagination correctness) | critical |
| D4 | Stratigraphy for ENO 35147,35151 returns rows | POST `bh:BoreholeStratigraphyLogs` CQL `ENO IN (35147,35151)` | `> 0` rows (expect ~31 across the two, per probe) | critical |
| D5 | Earth-material for ENO 35147,35151 returns rows | POST `bh:BoreholeEarthMaterialLogs` CQL `ENO IN (35147,35151)` | `> 0` rows | important |
| D6 | Log depth fields sane | inspect D4/D5 rows | `top_depth <= bottom_depth`, both non-negative, numeric/parseable | critical |
| D7 | Join key present on logs | D4/D5 rows | Every row carries `ENO` matching the requested set | critical |
| D8 | Join key present on headers | D1 features | Every feature carries `eno` | important |
| D9 | Hydrogeology polygons for ACT bbox | `MapServer/0/query` with `geometry=<bbox>&geometryType=esriGeometryEnvelope&inSR=4283&outSR=4283&f=geojson&where=1=1` | `> 0` features with valid (closed, ≥4-vertex) Polygon/MultiPolygon geometry | critical |
| D10 | Hydro coords in Australian bounds | inspect D9 geometries | All vertices within lon 112–154, lat -44 to -10 | important |
| D11 | Hydro attribute payload populated | D9 properties | Non-null values present for the mapped fields (`aquif_ty`, `type`, `feature`, etc.) | important |
| D12 | PID URL format intact | D4/D5 `BOREHOLE_PID` | Matches `http://pid.geoscience.gov.au/samplingFeature/au/BH<ENO>` | nice-to-have |

---

## 5. Data integrity / drift detection

| ID | Description | Request | Pass criteria | Severity |
|----|-------------|---------|---------------|----------|
| I1 | BoreholeView national count order-of-magnitude | `gsmlp:BoreholeView&resultType=hits` | numberMatched within ±20% of 52,338 (catches collapse-to-0 or schema rename) | critical |
| I2 | Stratigraphy national count order-of-magnitude | `bh:BoreholeStratigraphyLogs&resultType=hits` | Within ±20% of 190,016 | important |
| I3 | Earth-material national count order-of-magnitude | `bh:BoreholeEarthMaterialLogs&resultType=hits` | Within ±20% of 551,852 | important |
| I4 | Logs are NOT spatially queryable (regression guard) | `bh:BoreholeStratigraphyLogs&bbox=...,EPSG:4283&resultType=hits` | numberMatched == 0 (if this ever returns >0, the ENO-chunk workflow can be simplified — flag it) | important |
| I5 | Header/log case asymmetry holds | compare W5 vs W6/W7 keys | Header exposes lowercase `eno`, logs expose uppercase `ENO` (mapper normalisation still required) | important |
| I6 | Hydro layer count present & non-zero | `MapServer/0/query?where=1=1&returnCountOnly=true&f=json` | `count > 0` and within expected order of magnitude | important |
| I7 | ENO fan-out ratio sane | D4 rows ÷ 2 boreholes | Roughly the ~15× seen at probe time (gross drift flagged, not hard-failed) | nice-to-have |

---

## 6. Freshness / caching signals

| ID | Description | Request | Pass criteria | Severity |
|----|-------------|---------|---------------|----------|
| F1 | ArcGIS ETag stable across identical requests | two identical `/query` calls | Same `ETag` both times | important |
| F2 | Conditional GET honoured | repeat F1 request with `If-None-Match: <etag>` | HTTP 304, empty body | important |
| F3 | ETag changes when request differs | `/query` for two different bboxes | Different `ETag` (or absent) — ETag is request-scoped, not global | nice-to-have |
| F4 | WFS numberMatched usable as fingerprint | `resultType=hits` for the same bbox twice | Identical integer both times (deterministic; safe to fingerprint) | important |
| F5 | WFS still emits no validators (revisit) | inspect WFS response headers | Still no ETag/Last-Modified; if one appears, the cheaper conditional path becomes available — flag it | nice-to-have |

---

## 7. Robustness / failure modes

| ID | Description | Request | Pass criteria | Severity |
|----|-------------|---------|---------------|----------|
| R1 | Bad WFS layer name fails fast | `GetFeature&typeNames=bh:DoesNotExist` | HTTP 400 / ExceptionReport quickly (no hang), not 200-with-garbage | important |
| R2 | Bad ArcGIS layer fails fast | `MapServer/999?f=json` | Error JSON / 400, returned promptly | important |
| R3 | Multi-page bbox pagination over a large region | sweep a wide bbox with `startIndex`/`count` across many pages | Every page HTTP 200; pages concatenate to the hits total with no gaps/dupes | important |
| R4 | Long ENO IN list via POST | POST GetFeature with a ~200-ENO `ENO IN (...)` body | HTTP 200 (confirms POST dodges GET URL-length limit at realistic chunk sizes) | important |
| R5 | Retry-After / 429 expectation | (observational) note whether 429s carry `Retry-After` | Document observed behaviour; gadata must honour `Retry-After` if present | nice-to-have |
| R6 | Read-timeout behaviour | issue a heavy query with a short read timeout | Client times out cleanly (server doesn't hang the socket indefinitely) | nice-to-have |
| R7 | ArcGIS resultOffset beyond end | `/query?...&resultOffset=<count+1>` | Returns 0 features cleanly, no 500 | nice-to-have |
| R8 | Oversized GET ENO list rejected gracefully | GET (not POST) GetFeature with a very long `ENO IN (...)` | 414/400 (not a hang) — justifies the POST strategy | nice-to-have |

---

## 8. Monitoring cadence

**Fast smoke check (CI nightly / pre-run, ~10 calls, seconds):**
C1, C2, C3, C6, C8, W4, A1, A2, A3, A5, A9, D1, I1, I6. These confirm the
servers are up, the two key layers exist with the right geometry/CRS, and a small
real fetch returns data. Cheap and safe to run before any production pull.

**Fuller weekly contract test (~30–40 calls, a minute or two):**
All of sections 2 and 3 (full schema/field/CRS contract), D2–D12 (real samples
incl. logs for ENO 35147/35151 and hydro polygons), I2–I5/I7 (count drift,
case-asymmetry, the not-spatially-queryable guard), and section 6 (freshness
signals). This is the schema/endpoint-drift early-warning net.

**Heavy / on-demand only (do NOT run routinely):**
D3 full paginated reconciliation of a bbox, R3 wide-region multi-page sweep, R4
long POST list, R8 oversized GET. These pull many pages or large payloads;
schedule monthly or run manually when investigating a suspected drift, out of
courtesy to a government server (single-threaded, polite inter-request delay,
descriptive User-Agent with contact info).

---

## 9. Unverified unknowns worth a probe

These behaviours are assumed or never measured; the plan above establishes them
but they are flagged as genuinely unverified:

1. **WFS 429 / rate-limit behaviour and whether `Retry-After` is sent** — we have
   never been throttled, so the retry contract (R5) is unconfirmed.
2. **Actual GET URL-length limit on the WFS host** — we adopt POST defensively
   (R8) but have not measured where GET breaks, so the safe chunk size for the
   GET fallback is unknown.
3. **Full header schema of `gsmlp:BoreholeView`** — the probe only printed keys
   from a single feature; the complete, authoritative field list and dtypes
   (especially the depth/drill-metadata fields gadata maps) are not pinned (W5).
4. ~~Exact stratigraphy/earth-material depth field names, units, and datum.~~
   **RESOLVED (probed 2026-06-17 via DescribeFeatureType + samples):** depth
   fields are `INTERVAL_BEGIN_M` / `INTERVAL_END_M` / `INTERVAL_LENGTH_M`, all
   `xsd:decimal`, in **metres measured DOWN from the depth reference point**
   (begin <= end). `DEPTH_REF_POINT_ELEV_M_AHD` is the reference elevation in
   **metres AHD**; `INTERVAL_BEGIN_ELEV_M_AHD` / `INTERVAL_END_ELEV_M_AHD` give
   per-interval elevation in m AHD (present but usually null — else compute as
   `ref_elev - depth`). Vertical datum for elevations is AHD. Note: gadata's
   `StratigraphyInterval` does not yet model the per-interval elevation fields;
   consider adding them for the interpolation work.
5. **ArcGIS server-side max page size vs advertised maxRecordCount=2000** — we
   trust the advertised value; whether the server silently caps lower under load
   (A4/A11) is unverified.
6. **The `/ows` vs `/wfs` WFS endpoint equivalence** — DESIGN notes both exist;
   we have not confirmed they return identical capabilities/data.
7. **Behaviour of `gsmlbh:Borehole` and the other deferred bh: layers** — listed
   in capabilities (W2) but their schema/geometry/queryability are unprobed.
8. **Whether ArcGIS `f=geojson` ever silently emits 4326 vs 4283 geometry** — we
   assert no `crs` field (A10) and always pass `outSR=4283`, but have not done a
   numeric cross-check of `f=json&outSR=4283` vs `f=geojson` coordinates to prove
   the ~1–1.8 m datum shift is actually neutralised.
9. **TLS cert expiry / chain for `services.ga.gov.au`** — C1 establishes it; the
   current expiry date and renewal cadence are unknown.
10. **Stability of `numberMatched` as a fingerprint against same-count content
    edits** — by design `numberMatched` cannot detect an edit that preserves the
    row count; the extent+TTL fallback (per DESIGN) is the mitigation but its
    sensitivity has not been measured.
