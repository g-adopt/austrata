"""Live NGIS health tests — knowing when the NGIS data goes unavailable.

These hit the real **upstream** NGIS artifacts, not austrata: for each state core
they check that the data.gov.au primary download AND our S3 mirror are reachable
and the right size, so we learn early when a custodian reissues/truncates the file
or when our fallback mirror breaks. Like the GA live suite they are gated by the
``live`` marker and split into ``smoke`` (fast, HEAD-only, joins the nightly run)
and ``heavy`` (downloads the smallest real core end to end; on-demand only).

The size check is **best-effort** (a fast Content-Length sanity signal, and the
server may omit it); the true integrity guard is the md5 verification inside
``ensure_gdb``, which is what actually gates a download from being trusted. Don't
over-trust a green smoke run — it means "reachable and right size", not "verified".

Run::

    pytest -m "live and smoke" -q       # nightly (HEAD checks only)
    pytest -m "live and heavy" -q       # on demand (downloads QLD core)
"""
from __future__ import annotations

import pytest

from austrata.infrastructure.http import HttpClient
from austrata.infrastructure.ngis_sources import NGIS_SOURCES, NGIS_STATES, get_source

pytestmark = pytest.mark.live


def _http() -> HttpClient:
    """A polite shared client (matches the GA live suite's politeness delay)."""
    return HttpClient(politeness_delay=0.15)


def _content_length(url: str) -> "int | None":
    """Content-Length for ``url`` via a HEAD (no body download).

    Falls back to a 1-byte Range GET when HEAD omits the length (some CDNs do),
    reading the total from a 206 ``Content-Range`` — still no full download.
    """
    sess = _http().session
    head = sess.head(url, allow_redirects=True, timeout=(10, 60))
    head.raise_for_status()
    cl = head.headers.get("Content-Length")
    if cl is not None:
        return int(cl)
    rng = sess.get(url, headers={"Range": "bytes=0-0"}, stream=True,
                   allow_redirects=True, timeout=(10, 60))
    rng.raise_for_status()
    rng.close()
    cr = rng.headers.get("Content-Range")  # e.g. "bytes 0-0/48203959"
    if cr and "/" in cr:
        total = cr.rsplit("/", 1)[-1]
        return int(total) if total.isdigit() else None
    return None


# ======================================================================
# Smoke: primary + mirror reachability and size (nightly)
# ======================================================================


@pytest.mark.smoke
@pytest.mark.parametrize("state", NGIS_STATES)
def test_ngis_mirror_reachable_and_sized(state):
    """The S3 mirror (our fallback) is reachable and the expected size.

    Run first/independently so a failure pins the blame on the MIRROR — the
    fallback we rely on when data.gov.au is down.
    """
    src = get_source(state)
    try:
        length = _content_length(src.mirror_url)
    except Exception as exc:  # noqa: BLE001 - want the url+state in the message
        pytest.fail(f"NGIS {state} MIRROR unreachable: {src.mirror_url} ({exc})")
    if length is not None:
        assert length == src.zip_bytes, (
            f"NGIS {state} MIRROR size {length} != expected {src.zip_bytes} "
            f"({src.mirror_url}) — mirror artifact changed/truncated"
        )


@pytest.mark.smoke
@pytest.mark.parametrize("state", NGIS_STATES)
def test_ngis_primary_reachable_and_sized(state):
    """The data.gov.au primary download is reachable and the expected size.

    A failure here while the mirror test passes means "primary down, mirror still
    OK" — we are running on the fallback. The message says which url/state.
    """
    src = get_source(state)
    try:
        length = _content_length(src.resource_url)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"NGIS {state} PRIMARY (data.gov.au) unreachable: {src.resource_url} "
            f"({exc}) — if the mirror test passes we are on the fallback"
        )
    if length is not None:
        assert length == src.zip_bytes, (
            f"NGIS {state} PRIMARY size {length} != expected {src.zip_bytes} "
            f"({src.resource_url}) — custodian may have reissued/truncated; "
            f"re-pin the registry"
        )


@pytest.mark.smoke
def test_ngis_registry_has_three_cores():
    """The registry still pins exactly the three standalone state cores."""
    assert set(NGIS_SOURCES) == {"NSW", "VIC", "QLD"}


# ======================================================================
# Heavy: real end-to-end pipeline against the smallest core (on demand)
# ======================================================================


@pytest.mark.heavy
def test_ngis_qld_end_to_end(tmp_path):
    """Download the smallest real core (QLD ~48 MB) and run a real box query.

    Proves the whole pipeline against real data: ensure_gdb -> optimise ->
    NGISClient.boreholes(bbox) -> load_logs, asserting EPSG:4283 bores and at
    least one stratigraphy interval with a populated AHD elevation.
    """
    from austrata.ngis_client import NGISClient

    ngis = NGISClient(ngis_dir=tmp_path / "ngis", cache_dir=tmp_path / "cache")
    # A small box over SE Queensland (Brisbane/Lockyer) known to hold bores.
    bbox = (152.0, -28.0, 152.6, -27.4)
    bc = ngis.boreholes("QLD", bbox=bbox)
    assert len(bc) > 0
    assert all(b.source == "NGIS:QLD" for b in bc)
    gdf = bc.to_geodataframe()
    assert gdf.crs.to_epsg() == 4283

    bc.load_logs("stratigraphy")
    sgdf = bc.stratigraphy_geodataframe()
    assert len(sgdf) > 0
    assert sgdf.crs.to_epsg() == 4283
    assert set(sgdf["source"]) == {"NGIS:QLD"}
    # NGIS fills the per-interval AHD elevations the GA WFS lacks.
    assert sgdf["top_elev_m_ahd"].notna().any()


@pytest.mark.heavy
def test_ngis_federated_qld_end_to_end(tmp_path):
    """The federated GroundwaterClient returns GA + NGIS:QLD over a real box."""
    from austrata.groundwater_client import GroundwaterClient

    gw = GroundwaterClient(cache_dir=tmp_path / "cache", ngis_dir=tmp_path / "ngis")
    bbox = (152.0, -28.0, 152.6, -27.4)
    bc = gw.boreholes(bbox=bbox)
    sources = {b.source for b in bc}
    assert "NGIS:QLD" in sources  # routed in (QLD extent covers the box)
    assert bc.to_geodataframe().crs.to_epsg() == 4283
