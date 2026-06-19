# austrata

Access Geoscience Australia borehole and hydrogeology data through their open
OGC/ArcGIS web services, with a provenance-aware local cache.

`austrata` models boreholes as first-class objects (a header plus downhole
stratigraphy and earth-material logs), lets you pull every bore inside an
arbitrary polygon or bounding box, and exposes the Hydrogeology of Australia
polygon layer to overlay. It talks to two backends behind one API: the GA
boreholes GeoServer (WFS) and the Hydrogeology of Australia ArcGIS MapServer.
Results are cached locally as GeoParquet with a provenance manifest, and
revalidated before refetching so repeated queries are cheap and reproducible.

Everything is returned in lon/lat (EPSG:4283, GDA94 geographic). Map projection
and mesh generation are deliberately out of scope — those live in the companion
`omega` package, which consumes this one.

## Installation

```bash
pip install -e ".[dev]"
```

Requires Python 3.11+. Runtime dependencies are geopandas, shapely, pyproj,
pyogrio, pyarrow, requests, tenacity, platformdirs, and filelock.

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

Geoscience Australia publishes this data under CC BY 4.0. `austrata` records the
provenance of every cached query so you can cite it with its access date:

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

The package follows clean-architecture / DDD layering (see `DESIGN.md`):

- `domain/` — pure value objects and entities (`Region`, `Borehole`,
  `BoreholeCollection`, `StratigraphyInterval`, `EarthMaterialInterval`,
  `HydrogeologyUnit`). No I/O.
- `ports/` — the interfaces the application layer depends on (`BoreholeSource`,
  `HydrogeologySource`, `DatasetCache`).
- `application/` — use cases that build per-backend cache fetch-plans.
- `infrastructure/` — the HTTP client, the WFS and ArcGIS adapters, the feature
  mappers, and the dataset cache.
- `client.py` — the `GADataClient` facade that wires it together.

## License

MIT (the code). The data accessed through it is © Geoscience Australia, CC BY 4.0.
