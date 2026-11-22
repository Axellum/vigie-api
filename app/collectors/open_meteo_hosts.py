"""Open-Meteo asked to a self-hosted instance first, the public API as a fallback (ADR 0039).

When an Open-Meteo URL (``VIGIE_OPEN_METEO_FORECAST_URL``, ``VIGIE_OPEN_METEO_MARINE_URL``,
``VIGIE_OPEN_METEO_AIR_QUALITY_URL``) points to a self-hosted instance, a collector sends its request there first. That
request is not counted in the quota of the public API: the instance is ours, the data and the code are the same
(the public Open-Meteo bucket, CC BY 4.0; the Open-Meteo server, AGPLv3).

The same request goes to the public API, counted in its quota as before, when the instance:

- cannot be reached or does not answer in time (it is then left aside for ``SELF_HOSTED_PAUSE_S`` seconds, so a stopped
  instance does not add its timeout to every request);
- answers an error (4xx or 5xx), or something that is not JSON;
- answers without a single value (every series null): the model the instance syncs does not cover the place, or its
  data is not there yet.

``VIGIE_OPEN_METEO_PUBLIC_FALLBACK=0`` turns the fallback off: the answer of the instance is used as it is.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Optional

import httpx

from app import clock

if TYPE_CHECKING:
    from app.collectors.base import BaseCollector

log = logging.getLogger(__name__)

PUBLIC_FALLBACK_ENV = "VIGIE_OPEN_METEO_PUBLIC_FALLBACK"
# A self-hosted read takes well under a second (a few hundred milliseconds when the data is not in memory yet,
# measured on 2026-11-22); past this delay the public API answers sooner.
SELF_HOSTED_TIMEOUT_S = 5.0
# After a failed connection the instance is left aside this long before it is tried again.
SELF_HOSTED_PAUSE_S = 60.0
# The blocks of an Open-Meteo answer that hold series (``time`` aside).
DATA_BLOCKS = ("current", "minutely_15", "hourly", "daily")


def public_fallback_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """``False`` only when ``VIGIE_OPEN_METEO_PUBLIC_FALLBACK`` says so (``0``, ``false``, ``no``, ``off``)."""
    raw = ((env if env is not None else os.environ).get(PUBLIC_FALLBACK_ENV) or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def has_values(payload: Any) -> bool:
    """``True`` when an Open-Meteo answer (one place, or a list for several) holds at least one non-null value."""
    if isinstance(payload, list):
        return any(has_values(item) for item in payload)
    if not isinstance(payload, dict):
        return False
    for block_name in DATA_BLOCKS:
        block = payload.get(block_name)
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            if key == "time":
                continue
            if isinstance(value, list):
                if any(v is not None for v in value):
                    return True
            elif value is not None:
                return True
    return False


def _usable(response: httpx.Response) -> bool:
    if response.status_code >= 400:
        return False
    try:
        return has_values(response.json())
    except ValueError:
        return False


async def get_open_meteo(
    collector: "BaseCollector[Any]",
    client: httpx.AsyncClient,
    url: str,
    public_url: str,
    params: Mapping[str, str],
) -> httpx.Response:
    """GET an Open-Meteo endpoint: ``url`` first when it is a self-hosted instance, ``public_url`` as a fallback.

    When ``url`` is the public endpoint this is ``collector._get`` (counted in the quota, as before).
    """
    if url.rstrip("/") == public_url.rstrip("/"):
        return await collector._get(client, public_url, params=params)

    now = clock.monotonic()
    fallback = public_fallback_enabled()
    paused_until = collector.self_hosted_paused_until.get(url, 0.0)
    if now >= paused_until or not fallback:
        collector.self_hosted_calls += 1
        try:
            response = await client.get(url, params=params, timeout=SELF_HOSTED_TIMEOUT_S)
        except httpx.HTTPError as exc:
            if not fallback:
                raise
            collector.self_hosted_paused_until[url] = now + SELF_HOSTED_PAUSE_S
            log.warning("%s: self-hosted Open-Meteo unreachable (%s), public API used", collector.provider_id,
                        type(exc).__name__)
        else:
            if not fallback or _usable(response):
                return response
            log.info("%s: self-hosted Open-Meteo answered %s without data, public API used",
                     collector.provider_id, response.status_code)
    collector.public_fallbacks += 1
    return await collector._get(client, public_url, params=params)
