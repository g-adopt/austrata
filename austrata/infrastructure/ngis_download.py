"""``ensure_gdb`` — the gdb tier of the NGIS two-stage pipeline.

Turns a state code into a *trusted* local ``.gdb`` directory, honouring the
remote↔local consistency contract: a downloaded archive is verified by size and
md5 against :mod:`austrata.infrastructure.ngis_sources` before it is ever trusted,
and an unverified artifact is never returned.

This module only *acquires* the raw geodatabase. Normalising it into the fast DB
(the optimiser pass) is a separate stage. Behaviour:

1. Resolve the NGIS data dir (arg > ``AUSTRATA_NGIS_DIR`` env > platformdirs cache
   ``austrata/ngis``). The package manages ``<ngis_dir>/<STATE>/...`` under it.
2. **Local-first:** a usable ``.gdb`` already at the expected path is returned
   immediately — no network. ``offline`` + absent raises.
3. Otherwise stream the zip to disk, ``resource_url`` (data.gov.au) first then
   ``mirror_url`` (our S3) on failure, writing a ``.partial`` promoted atomically.
4. **Verify before trusting:** size *and* md5 must match the registry; on a
   mismatch the artifact is discarded and the other source tried. If neither
   verifies, raise — an unverified gdb is never returned.
5. Extract into ``<ngis_dir>/<STATE>/`` (idempotently) and return the ``.gdb``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from platformdirs import user_cache_dir

from austrata.infrastructure.http import HttpClient
from austrata.infrastructure.ngis_sources import NgisSource, get_source

logger = logging.getLogger("austrata.ngis")

#: Env var overriding where raw NGIS geodatabases are stored/extracted.
_ENV_DIR = "AUSTRATA_NGIS_DIR"

#: Streaming read block size for download + checksum (1 MiB).
_BLOCK = 1 << 20


def resolve_ngis_dir(ngis_dir: Optional[os.PathLike | str] = None) -> Path:
    """Resolve the NGIS data dir: arg > env > platformdirs cache ``austrata/ngis``."""
    chosen = ngis_dir or os.environ.get(_ENV_DIR) or os.path.join(user_cache_dir("austrata"), "ngis")
    return Path(chosen)


def ensure_gdb(
    state: str,
    *,
    ngis_dir: Optional[os.PathLike | str] = None,
    http: Optional[HttpClient] = None,
    force: bool = False,
    offline: bool = False,
) -> Path:
    """Return an absolute path to a trusted, extracted ``.gdb`` for ``state``."""
    source = get_source(state)
    base = resolve_ngis_dir(ngis_dir) / source.state
    gdb_path = base / source.gdb_relpath

    if gdb_path.is_dir() and not force:
        logger.info("NGIS %s: using local gdb %s", source.state, gdb_path)
        return gdb_path.resolve()

    if offline:
        raise RuntimeError(
            f"Offline and no local NGIS gdb for {source.state} at {gdb_path}; "
            f"run once online (or place the extracted .gdb there) first."
        )

    base.mkdir(parents=True, exist_ok=True)
    zip_path = base / f"{source.state}.zip"
    _download_verified(source, zip_path, http or HttpClient())
    _extract(zip_path, base)
    # The download is large and disposable once extracted.
    if zip_path.exists():
        zip_path.unlink()

    if not gdb_path.is_dir():
        raise RuntimeError(
            f"NGIS {source.state}: archive verified but expected gdb "
            f"{source.gdb_relpath!r} not found after extraction."
        )
    logger.info("NGIS %s: gdb ready at %s", source.state, gdb_path)
    return gdb_path.resolve()


# -- download + verify --------------------------------------------------


def _download_verified(source: NgisSource, dest: Path, http: HttpClient) -> None:
    """Stream the archive (primary→mirror) to ``dest``, verified or raise."""
    errors: List[str] = []
    for label, url in (("data.gov.au", source.resource_url), ("mirror", source.mirror_url)):
        try:
            size, md5 = _stream_to_file(http, url, dest)
        except Exception as exc:  # network/IO error: try the next source
            logger.warning("NGIS %s: %s download failed: %s", source.state, label, exc)
            errors.append(f"{label}: {exc}")
            continue
        ok, why = _verify(source, size, md5)
        if ok:
            logger.info("NGIS %s: %s download verified (%d bytes)", source.state, label, size)
            return
        logger.warning("NGIS %s: %s download rejected (%s)", source.state, label, why)
        errors.append(f"{label}: {why}")
        if dest.exists():
            dest.unlink()
    raise RuntimeError(
        f"NGIS {source.state}: could not obtain a verified archive; "
        f"tried " + "; ".join(errors)
    )


def _stream_to_file(http: HttpClient, url: str, dest: Path) -> Tuple[int, str]:
    """Stream ``url`` to ``<dest>.partial`` then atomically promote it.

    Goes through the :class:`HttpClient` session so the download inherits the
    polite ``User-Agent``; streams so a multi-GB core never lands in memory.
    Returns the written ``(size_bytes, md5_hex)``.
    """
    partial = dest.with_suffix(dest.suffix + ".partial")
    md5 = hashlib.md5()
    size = 0
    try:
        resp = http.session.get(url, stream=True, timeout=(http.connect_timeout, http.read_timeout))
        resp.raise_for_status()
        with open(partial, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_BLOCK):
                if not chunk:
                    continue
                fh.write(chunk)
                md5.update(chunk)
                size += len(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(partial, dest)
    finally:
        if partial.exists():
            partial.unlink()
    return size, md5.hexdigest()


def _verify(source: NgisSource, size: int, md5: str) -> Tuple[bool, Optional[str]]:
    """Check a downloaded archive against the pinned size + md5."""
    if size != source.zip_bytes:
        return False, f"size {size} != expected {source.zip_bytes}"
    if md5 != source.zip_md5:
        return False, f"md5 {md5} != expected {source.zip_md5}"
    return True, None


# -- extract ------------------------------------------------------------


def _extract(zip_path: Path, dest_dir: Path) -> None:
    """Extract ``zip_path`` into ``dest_dir``.

    Overwrite-idempotent: re-running overwrites the same members, but it does not
    prune files absent from a newer archive. Harmless here because the archive is
    md5-pinned, so "newer archive, same state" cannot occur without a registry bump.
    """
    logger.info("NGIS: extracting %s", zip_path.name)
    with zipfile.ZipFile(zip_path) as zf:
        # The size+md5 verify before this point (verify-before-extract) is what
        # makes extractall safe against zip-slip: the archive is a known, pinned
        # artifact, not arbitrary attacker-supplied input.
        zf.extractall(dest_dir)
