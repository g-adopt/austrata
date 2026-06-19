"""Offline unit tests for ensure_gdb (no real network, no multi-GB cores).

Real-core downloads are exercised only by the heavy/live test at the bottom,
which is deselected by default.
"""
import hashlib
import io
import zipfile

import pytest
import responses

from austrata.infrastructure import ngis_sources
from austrata.infrastructure.http import HttpClient
from austrata.infrastructure.ngis_download import ensure_gdb

GDB_RELPATH = "extract_dir/TEST_NGIS_Core.gdb"
PRIMARY_URL = "https://data.gov.au/test/ngis-tas.zip"
MIRROR_URL = "https://mirror.test/ngis-tas.zip"


def _make_zip() -> bytes:
    """A tiny zip carrying a fake .gdb directory at the registry's gdb_relpath."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # A .gdb is a directory; ship a file inside it so extractall makes it.
        zf.writestr(f"{GDB_RELPATH}/gdb", b"\x00")
        zf.writestr(f"{GDB_RELPATH}/a00000001.gdbtable", b"fake")
    return buf.getvalue()


@pytest.fixture
def fake_source(monkeypatch):
    """Register a synthetic 'TAS' core whose size/md5 match our tiny zip."""
    payload = _make_zip()
    src = ngis_sources.NgisSource(
        state="TAS",
        vintage="test",
        dataset_url="https://data.gov.au/test",
        resource_url=PRIMARY_URL,
        mirror_url=MIRROR_URL,
        zip_bytes=len(payload),
        zip_md5=hashlib.md5(payload).hexdigest(),
        gdb_relpath=GDB_RELPATH,
    )
    monkeypatch.setitem(ngis_sources.NGIS_SOURCES, "TAS", src)
    return src, payload


def _client():
    return HttpClient(politeness_delay=0.0, max_attempts=1)


def test_local_first_returns_without_http(tmp_path, fake_source):
    """A pre-placed .gdb is returned with zero HTTP calls."""
    src, _ = fake_source
    gdb = tmp_path / src.state / src.gdb_relpath
    gdb.mkdir(parents=True)
    with responses.RequestsMock() as rsps:  # any HTTP would raise here
        out = ensure_gdb("TAS", ngis_dir=tmp_path, http=_client())
        assert len(rsps.calls) == 0
    assert out == gdb.resolve()


def test_offline_without_local_raises(tmp_path, fake_source):
    with pytest.raises(RuntimeError, match="Offline and no local NGIS gdb"):
        ensure_gdb("TAS", ngis_dir=tmp_path, http=_client(), offline=True)


@responses.activate
def test_download_verifies_and_extracts(tmp_path, fake_source):
    src, payload = fake_source
    responses.add(responses.GET, PRIMARY_URL, body=payload, status=200)
    out = ensure_gdb("TAS", ngis_dir=tmp_path, http=_client())
    assert out.is_dir()
    assert out == (tmp_path / src.state / src.gdb_relpath).resolve()
    # The disposable archive is cleaned up after extraction.
    assert not (tmp_path / src.state / f"{src.state}.zip").exists()


@responses.activate
def test_checksum_mismatch_is_rejected(tmp_path, fake_source):
    """Both sources serve a corrupt body → no gdb is ever returned."""
    responses.add(responses.GET, PRIMARY_URL, body=b"corrupt-not-the-zip", status=200)
    responses.add(responses.GET, MIRROR_URL, body=b"also-corrupt", status=200)
    with pytest.raises(RuntimeError, match="could not obtain a verified archive"):
        ensure_gdb("TAS", ngis_dir=tmp_path, http=_client())


@responses.activate
def test_primary_fails_mirror_serves_good_zip(tmp_path, fake_source):
    """Primary errors; mirror serves a good zip that verifies and extracts."""
    src, payload = fake_source
    responses.add(responses.GET, PRIMARY_URL, status=503)  # data.gov.au down
    responses.add(responses.GET, MIRROR_URL, body=payload, status=200)
    out = ensure_gdb("TAS", ngis_dir=tmp_path, http=_client())
    assert out.is_dir()
    assert {c.request.url for c in responses.calls} == {PRIMARY_URL, MIRROR_URL}


@responses.activate
def test_primary_bad_checksum_falls_back_to_mirror(tmp_path, fake_source):
    """Primary verifies-false (wrong bytes); mirror's good zip is used."""
    src, payload = fake_source
    responses.add(responses.GET, PRIMARY_URL, body=b"wrong-size-and-md5", status=200)
    responses.add(responses.GET, MIRROR_URL, body=payload, status=200)
    out = ensure_gdb("TAS", ngis_dir=tmp_path, http=_client())
    assert out.is_dir()


# -- real-core acquisition (heavy + live; deselected by default) --------


@pytest.mark.live
@pytest.mark.heavy
def test_ensure_gdb_real_qld_core(tmp_path):
    """Download + verify the smallest real core (QLD, ~48 MB)."""
    out = ensure_gdb("QLD", ngis_dir=tmp_path)
    assert out.is_dir()
