"""Shared HTTP client: ``requests.Session`` + ``tenacity`` retry policy.

Implements the HTTP policy from DESIGN as a courtesy to the GA government
servers and for robustness:

* Retry only on transient failures — status 429/502/503/504 and connection/
  read errors. Never retry 400/404 (a 400 from a bad WFS filter would never
  succeed on retry; retrying just hammers the server). A ``Retry-After`` header
  on a 429 is honoured.
* Capped exponential backoff, configurable attempt count.
* Split connect vs read timeouts.
* A descriptive ``User-Agent`` with a contact placeholder.
* A small inter-request politeness delay so we never burst the server.
* Conditional-GET pass-through: callers may pass ``If-None-Match`` /
  ``If-Modified-Since`` and a 304 is surfaced (not raised) so the cache layer
  can act on it. Response headers (ETag/Last-Modified) are returned to callers.

Returns raw :class:`requests.Response`; parsing belongs to the adapters.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("gadata.http")

#: Status codes worth retrying — all transient. 400/404 are deliberately absent.
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})

DEFAULT_USER_AGENT = (
    "gadata/0.1 (Geoscience Australia data client; "
    "+https://github.com/g-adopt/gadata; contact: gadata-maintainers)"
)


class RetryableHTTPError(requests.HTTPError):
    """A response whose status is in :data:`RETRYABLE_STATUS`."""


def _is_retryable(exc: BaseException) -> bool:
    """tenacity predicate: retry transient HTTP and connection/read errors."""
    if isinstance(exc, RetryableHTTPError):
        return True
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


class HttpClient:
    """A polite, retrying HTTP client around a single :class:`requests.Session`."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        max_attempts: int = 4,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
        politeness_delay: float = 0.15,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.politeness_delay = politeness_delay
        self._last_request_at = 0.0
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    # -- public API ------------------------------------------------------

    def get(self, url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None) -> requests.Response:
        """GET ``url`` with the retry/backoff/politeness policy applied."""
        return self._request("GET", url, params=params, headers=headers)

    def post(
        self,
        url: str,
        *,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> requests.Response:
        """POST ``url`` (form-encoded ``data``) with the same policy.

        POST is needed for ENO-chunked GetFeature, which dodges GET URL-length
        limits on long ``ENO IN (...)`` filters.
        """
        return self._request("POST", url, params=params, headers=headers, data=data)

    # -- internals -------------------------------------------------------

    def _sleep_for_politeness(self) -> None:
        if self.politeness_delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self.politeness_delay - elapsed
        if wait > 0:
            time.sleep(wait)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        # tenacity wraps the inner send so backoff state is per-call.
        retryer = retry(
            reraise=True,
            stop=stop_after_attempt(self.max_attempts),
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential(multiplier=self.backoff_base, max=self.backoff_max),
            before_sleep=self._log_retry,
        )
        return retryer(self._send)(method, url, **kwargs)

    def _send(self, method: str, url: str, **kwargs) -> requests.Response:
        self._sleep_for_politeness()
        try:
            resp = self.session.request(
                method,
                url,
                timeout=(self.connect_timeout, self.read_timeout),
                **kwargs,
            )
        finally:
            self._last_request_at = time.monotonic()

        status = resp.status_code
        # 304 (conditional GET) and 2xx are returned as-is for the caller/cache.
        if status == 304 or status < 400:
            return resp
        if status in RETRYABLE_STATUS:
            self._honour_retry_after(resp)
            logger.warning("Retryable HTTP %s from %s", status, url)
            raise RetryableHTTPError(f"HTTP {status} for {url}", response=resp)
        # Non-retryable client/server error (400/404/...): surface immediately.
        resp.raise_for_status()
        return resp

    def _honour_retry_after(self, resp: requests.Response) -> None:
        """Sleep for a ``Retry-After`` delay (seconds form) if the server set one."""
        value = resp.headers.get("Retry-After")
        if not value:
            return
        try:
            delay = float(value)
        except ValueError:
            return  # HTTP-date form not handled; backoff covers it anyway.
        delay = min(delay, self.backoff_max)
        if delay > 0:
            logger.info("Honouring Retry-After: sleeping %.1fs", delay)
            time.sleep(delay)

    @staticmethod
    def _log_retry(retry_state) -> None:  # pragma: no cover - logging only
        logger.info("Retrying HTTP request (attempt %s)", retry_state.attempt_number)
