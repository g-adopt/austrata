# Examples

Runnable scripts that exercise `austrata` against the live Geoscience Australia
services and the local NGIS state cores. They are scripts, not tests: each one
fetches real data and writes its outputs (GeoPackage, CSV, PNG, rasters,
ParaView files) next to itself. Those outputs are deliberately **git-ignored** —
only the scripts and the small `ABSUC_sample.csv` reference layout are tracked.

Run them with the project interpreter, from the repo root, e.g.

```bash
python examples/quickstart.py
```

The first NGIS run downloads and optimises a state core once (several hundred MB
to a few GB under `AUSTRATA_NGIS_DIR`); every run after that filters the cached
fast DB in memory, offline.

## Tour

- `quickstart.py` — a short tour of the whole API against the live GA services
  (boreholes in a bbox and a polygon, a single bore by ENO, hydrogeology
  polygons, provenance). Start here.

## State cores (NGIS) and stratigraphy maps

- `nsw-example/nsw-example.py` — pulls all NSW groundwater data from the NGIS
  state core and exports the stratigraphy, earth-material and construction logs.
- `nsw-example/stratigraphy_map.py` — maps bores carrying stratigraphy for any
  source over an Australia basemap; takes `NSW | VIC | QLD | GA` (default NSW).
- `nsw-example/nsw_stratigraphy_map.py` — the NSW-specific variant, plotting NGIS
  bores over the backdrop of all NSW bores.
- `state-strata/state-strata.py` — exports per-source stratigraphy in GA's ABSUC
  table format; `ABSUC_sample.csv` is the reference column layout it reproduces.
