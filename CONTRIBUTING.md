# Contributing to austrata

Thanks for helping improve austrata. It is a pure data-access + caching layer
over Geoscience Australia open data and the NGIS state cores; the architecture
and the verified service quirks it works around are documented in `DESIGN.md`,
which is worth a read before changing anything below `client.py`.

## Environment & commands

Use any Python ≥3.11. An editable install with the dev extras gets you the test,
lint, and type-check tooling:

```bash
pip install -e ".[dev]"          # editable install with dev tooling

python -m pytest -q                        # offline unit tests (no network)
python -m pytest -q -m "live and smoke"    # fast GA + NGIS server health
python -m pytest -q -m "live and contract" # fuller GA schema/data contract
python -m pytest -q -m live                # all live tests (hit real servers)

python -m flake8 austrata tests            # lint (line length 120)
python -m mypy austrata                    # type check (must stay clean)
```

`pytest` excludes anything marked `live` by default (those hit the real GA
servers). Markers: `live`, `smoke`, `contract`, `heavy` (heavy = on-demand only).

## Testing & monitoring

- Offline unit tests mock HTTP with `responses` and use `tmp_path` caches. The
  NGIS offline tests write a real synthetic `.gdb` with fiona (no network, no
  multi-GB download) so the reader, optimiser, mapper, and federation are
  exercised for real.
- Live tests (`tests/test_*_live.py`, `tests/test_ga_server_live.py`,
  `tests/test_ngis_live.py`) hit the real servers and are gated by the `live`
  marker.
- CI: `tests.yml` runs the offline suite, flake8, and mypy on every push/PR.
  `nightly-ga-health.yml` runs the smoke subset nightly and the contract subset
  weekly, uploading reports. The nightly smoke also HEADs each NGIS state's
  data.gov.au primary **and** S3 mirror and checks the advertised size, so a
  custodian reissue/truncation or a broken mirror surfaces early (the md5 verify
  in `ensure_gdb` is the real integrity guard). The NGIS heavy end-to-end (which
  downloads the QLD core) is on-demand only.

## Conventions

- Python ≥3.11; flat `austrata/` package layout; flake8/black line length 120;
  keep `mypy austrata` clean.
- Keep files focused (roughly under 200 lines) and respect the clean-architecture
  dependency rule: `domain` → `ports` → `application` → `infrastructure` →
  `client`. Nothing below `client` should mention a specific backend.
- Run the three gates (offline `pytest`, `mypy austrata`, `flake8`) before
  opening a pull request.

## Deferred / out of scope

Please ask before building these — they are intentionally not here yet:

- The interpolation layer that would consume these domain objects to build 3D
  layer surfaces (it belongs alongside the data layer, parallel to it).
- Spatially-sorted fast-DB parquet for predicate pushdown — a future NGIS
  optimisation not needed at current state sizes.
- Map projection and meshing, which live in the companion `omega` package.
