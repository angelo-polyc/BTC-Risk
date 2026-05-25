"""HTTP helpers with retries, optional rate-limit gating, and adaptive backoff.

Retries on:
- 429 (with Retry-After)
- 403 (CloudFront/WAF throttling sometimes surfaces this for Coinglass)
- 5xx
- Any `TransportError` (covers TCP drops: RemoteProtocolError, ReadError,
  ReadTimeout, ConnectError, WriteError, PoolTimeout)

Backoff is `THROTTLE_BACKOFF_BASE^attempt + jitter` for throttle-class errors
(429/403) and `RETRY_BASE^attempt + jitter` for transport errors, so a
sustained edge throttle clears (5-30s+) instead of hammering through the
same wall in 1-2s.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional

import httpx

from ratelimit import TokenBucket

LOG = logging.getLogger("divergence.http")

REQUEST_TIMEOUT_S = 15.0
MAX_RETRIES = 4
RETRY_BASE = 1.5              # transport / 5xx
THROTTLE_BACKOFF_BASE = 3.0   # 429 / 403 — assume window is multi-second
THROTTLE_MAX_SLEEP_S = 30.0   # cap any single sleep


async def request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    sem: Optional[asyncio.Semaphore] = None,
    bucket: Optional[TokenBucket] = None,
) -> Any:
    """Best-effort JSON fetch. Returns parsed JSON on success, None on permanent
    failure. Both `sem` (concurrency cap) and `bucket` (rate cap) gate
    independently — pass both for sources with a documented rate limit."""
    last_err: Optional[str] = None
    for attempt in range(MAX_RETRIES):
        if bucket is not None:
            await bucket.acquire()
        try:
            if sem is not None:
                await sem.acquire()
            try:
                resp = await client.request(
                    method, url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_S
                )
            finally:
                if sem is not None:
                    sem.release()

            if resp.status_code in (429, 403):
                # Throttle-class. Honor Retry-After if present, else exponential
                # backoff scaled for sliding-window limits.
                ra = resp.headers.get("retry-after")
                if ra and ra.replace(".", "", 1).isdigit():
                    wait = min(float(ra), THROTTLE_MAX_SLEEP_S)
                else:
                    wait = min(
                        THROTTLE_BACKOFF_BASE ** attempt + random.uniform(0, 1.0),
                        THROTTLE_MAX_SLEEP_S,
                    )
                LOG.info("throttle url=%s status=%s waiting=%.2fs", url, resp.status_code, wait)
                await asyncio.sleep(wait)
                last_err = f"http_{resp.status_code}"
                continue

            if resp.status_code >= 500:
                await asyncio.sleep(RETRY_BASE ** attempt + random.uniform(0, 0.3))
                last_err = f"http_{resp.status_code}"
                continue

            if resp.status_code >= 400:
                # Genuine 4xx (404, 422, unknown symbol). Not retryable.
                LOG.debug("4xx url=%s status=%s body=%s", url, resp.status_code, resp.text[:200])
                return None

            try:
                return resp.json()
            except ValueError:
                LOG.warning("json decode failed url=%s body=%s", url, resp.text[:200])
                return None

        except httpx.TransportError as exc:
            # Covers TCP drops (RemoteProtocolError, ReadError), timeouts
            # (ReadTimeout, ConnectTimeout, PoolTimeout), and connect failures.
            # If TLS/TCP is being closed by an edge throttle (no HTTP response
            # at all), treat retries >=1 with throttle-class backoff so we
            # don't hammer a closed door at 1s intervals.
            last_err = type(exc).__name__
            if attempt >= 1:
                wait = min(
                    THROTTLE_BACKOFF_BASE ** attempt + random.uniform(0, 0.5),
                    THROTTLE_MAX_SLEEP_S,
                )
            else:
                wait = RETRY_BASE ** attempt + random.uniform(0, 0.3)
            LOG.debug("transport err url=%s err=%s waiting=%.2fs", url, last_err, wait)
            await asyncio.sleep(wait)

    LOG.warning("request gave up url=%s last_err=%s", url, last_err)
    return None
