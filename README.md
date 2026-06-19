# austrata

[![Tests](https://github.com/g-adopt/austrata/actions/workflows/tests.yml/badge.svg)](https://github.com/g-adopt/austrata/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/austrata.svg)](https://pypi.org/project/austrata/)
[![Python](https://img.shields.io/pypi/pyversions/austrata.svg)](https://pypi.org/project/austrata/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20756406.svg)](https://doi.org/10.5281/zenodo.20756406)

Access Australian borehole, stratigraphy, and hydrogeology data through open
government web services and state groundwater cores, with a provenance-aware
local cache.

`austrata` models boreholes as first-class objects (a header plus downhole
stratigraphy, earth-material, and construction logs), lets you pull every bore
inside an arbitrary polygon or bounding box, and exposes the Hydrogeology of
Australia polygon layer to overlay.

It draws on two complementary borehole sources behind one API. The national
Geoscience Australia services — the boreholes GeoServer (WFS) and the
Hydrogeology of Australia ArcGIS MapServer — give continent-wide coverage. The
Bureau of Meteorology NGIS state cores (NSW, VIC, QLD) add a much denser local
source, with the per-interval AHD elevations and screen/casing construction logs
the national WFS lacks. A federated `GroundwaterClient` queries Geoscience
Australia and the relevant state cores together.

Results are cached locally as GeoParquet with a provenance manifest, and
revalidated before refetching so repeated queries are cheap and reproducible.

Everything is returned in lon/lat (EPSG:4283, GDA94 geographic). Map projection
and mesh generation are deliberately out of scope — those live in the companion
`omega` package, which consumes this one.

## Installation

```bash
pip install austrata
```

Requires Python 3.11+. Runtime dependencies are geopandas, shapely, pyproj,
pyogrio, fiona, pyarrow, requests, tenacity, platformdirs, filelock, and tqdm.
For a development checkout, `pip install -e ".[dev]"` adds the test, lint, and
type-check tooling.

## Quickstart

```python
from austrata import GADataClient
from shapely.geometry import box

ga = GADataClient()                      # cache defaults to the OS user cache dir

# Boreholes inside a bounding box (lon/lat). Paginated and cached automatically.
bores = ga.boreholes(bbox=(148.9, -35.6, 149.3, -35.1))
print(len(bores), "boreholes")
gdf = bores.to_geodataframe()            # headers as a GeoDataFrame (EPSG:4283)

# Or pass any shapely geometry as the region.
bores = ga.boreholes(region=box(148.9, -35.6, 149.3, -35.1))

# Load downhole logs for the whole collection in one shot (ENO-batched, cached).
bores.load_logs("stratigraphy")
for b in bores:
    for interval in b.stratigraphy:      # list of StratigraphyInterval
        if interval.valid:
            print(b.name, interval.top_depth, interval.bottom_depth, interval.unit)

bores.load_logs("earth_material")        # b.earth_material is then populated

# Export the loaded logs as a tidy GeoDataFrame (one row per interval, borehole
# point geometry, EPSG:4283). Save with geopandas: .to_file('x.gpkg') / .to_csv(...).
strat = bores.stratigraphy_geodataframe()
earth = bores.earth_material_geodataframe()

# A single borehole by ENO or PID.
one = ga.borehole("35147")

# Hydrogeology polygons to overlay, as a GeoDataFrame.
hydro = ga.hydrogeology(bbox=(148.9, -35.6, 149.3, -35.1))

# A backend filter passes straight through.
diamond = ga.boreholes(bbox=(148.9, -35.6, 149.3, -35.1), filter="drillingMethod='Diamond'")
```

### State cores (NGIS) and federated queries

For NSW, VIC, and QLD the Bureau of Meteorology NGIS state cores are a much
denser source. `GroundwaterClient` federates Geoscience Australia and every state
core whose extent intersects your query, returning one collection where each bore
is tagged with its `.source`:

```python
from austrata import GroundwaterClient

gw = GroundwaterClient()
bores = gw.boreholes(bbox=(146.0, -35.5, 146.5, -35.0))   # a NSW box
print(len(bores), "boreholes from", {b.source for b in bores})

# NGIS carries dense stratigraphy plus screen/casing construction logs (NGIS-only).
bores.load_logs("stratigraphy")
bores.load_logs("construction")
strat = bores.stratigraphy_geodataframe()   # one row per interval, with a 'source' column

# Restrict the backends: "GA", "NGIS" (all intersecting), "NGIS:NSW", or a bare "NSW".
nsw_only = gw.boreholes(bbox=(146.0, -35.5, 146.5, -35.0), sources="NGIS:NSW")
```

The first query against a state core downloads and optimises its geodatabase
once (several hundred MB to a few GB, with a progress bar) into a local fast
store; every query after that filters it in memory, offline. The raw cores live
under `AUSTRATA_NGIS_DIR`, separate from the query cache. Use `NGISClient`
directly if you want a single state core without the GA federation.

### Dry-run counts

Pass `count_only=True` to get the number of features without downloading them
(uses the cheap `resultType=hits` / `returnCountOnly` paths):

```python
n_bores = ga.boreholes(bbox=(148.9, -35.6, 149.3, -35.1), count_only=True)
n_units = ga.hydrogeology(bbox=(148.9, -35.6, 149.3, -35.1), count_only=True)
```

### Caching, freshness, and offline use

Each logical query is cached as a `<hash>.parquet` file plus an entry in a
`manifest.json`, in an OS-appropriate user cache directory
(e.g. `~/Library/Caches/austrata` on macOS). Override the location with the
`cache_dir=` argument or the `AUSTRATA_DATA_DIR` environment variable.

On a repeat query `austrata` revalidates rather than blindly refetching: the
ArcGIS path uses the service `ETag` (conditional `If-None-Match`), and the WFS
path — which exposes no ETag — compares the `numberMatched` count as a cheap
fingerprint. Both fall back to a max-age TTL (30 days by default), so a
same-count content edit is eventually picked up. `force_refresh=True` is the
only hard guarantee of a fresh pull.

```python
ga = GADataClient(offline=True)          # never touch the network; serve cache or raise
ga = GADataClient(max_age=7 * 24 * 3600) # revalidate-by-refetch weekly
bores = ga.boreholes(bbox=..., force_refresh=True)

# Inspect or clear the cache.
ga.cache.info()                          # dir, entry count, total bytes, per-entry detail
ga.cache.list()                          # cached keys
ga.cache.clear()                         # wipe everything (or clear(key) for one)
```

To prefetch for offline/field use, run the queries you need once while online;
they land in the cache and an `offline=True` client serves them thereafter.

### Citing the data

Each source publishes its data under CC BY 4.0. `austrata` records the provenance
of every cached query — per source for a federated collection — so you can cite
it with its access date:

```python
print(bores.citation())                  # citation string incl. "Accessed YYYY-MM-DD"
bores.provenance()                        # dict: source_url, license, fetched_at, ...

from austrata import hydrogeology_citation
print(hydrogeology_citation(hydro))
```

## The lon/lat (GDA94) contract

Both services are native EPSG:4283 (GDA94 geographic). `austrata` pins this end to
end: the WFS bbox carries an explicit `EPSG:4283` suffix, and ArcGIS queries
force `outSR=4283` (its GeoJSON otherwise silently defaults to WGS84). Every
geometry you get back is lon/lat in GDA94 — no reprojection happens here.

## Architecture

The package follows clean-architecture / DDD layering:

- `domain/` — pure value objects and entities (`Region`, `Borehole`,
  `BoreholeCollection`, `StratigraphyInterval`, `EarthMaterialInterval`,
  `ConstructionInterval`, `HydrogeologyUnit`). No I/O.
- `ports/` — the interfaces the application layer depends on (`BoreholeSource`,
  `HydrogeologySource`, `DatasetCache`).
- `application/` — use cases that build per-backend cache fetch-plans.
- `infrastructure/` — the HTTP client, the WFS and ArcGIS adapters, the feature
  mappers, the dataset cache, and the NGIS pipeline (download, optimise,
  fast-store, mapper).
- `client.py` / `ngis_client.py` / `groundwater_client.py` — the `GADataClient`,
  `NGISClient`, and federated `GroundwaterClient` facades that wire it together.

## License

MIT (the code). The Geoscience Australia borehole and hydrogeology data is
© Geoscience Australia (CC BY 4.0); the NGIS state cores are © the Bureau of
Meteorology and the contributing state agencies (CC BY 4.0). `austrata` records
each source's provenance and citation on every query.
