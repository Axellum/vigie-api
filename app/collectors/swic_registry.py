"""The registry of the WMO Severe Weather Information Centre (SWIC): which national services publish CAP warnings, and where.

The WMO keeps one record per service at ``https://severeweather.wmo.int/v2/json/sources.json`` (the same list the SWIC
website is built from). Read on 2026-10-20, it listed 140 records, all ``capAlertFeedStatus: operating`` and all
``sourceIsOfficial: true``: the status says nothing about whether the feed answers (88 of the 107 services that are not
already covered answered with at least one feed that day, see ``docs/data-sources.md``), so it is not used as a filter.

A record names, in ``capAlertFeed``, the feed of the service itself: one address, or several separated by commas (one per
language: 51 of the 140). The registry changes rarely (its ``Last-Modified`` was 2024-03-18): it is read once a day, with
``If-Modified-Since``, and a snapshot of it is bundled (``app/data/swic_registry.json.gz``, written by
``tools/build_swic_snapshot.py`` without the contact details of the people in the records) so that the countries an
instance serves are known before the first read and when the WMO does not answer.

The licence of each service is another matter, kept in ``swic_terms.py``.
"""

from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, CacheEntry, Collected, Fetched
from app.collectors.swic_cap import CapError

REGISTRY_URL = "https://severeweather.wmo.int/v2/json/sources.json"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "data" / "swic_registry.json.gz"
MAX_REGISTRY_BYTES = 2_000_000  # the registry weighs 156 KB on 2026-10-20
REGISTRY_TTL_S = 24 * 3600

WMO_SOURCES_MODES = ("verified", "all", "off")
DEFAULT_WMO_SOURCES = "verified"


def wmo_sources_from_env() -> str:
    """Which registry services this instance reads: ``VIGIE_WMO_SOURCES`` = ``verified`` (default), ``all`` or ``off``.

    ``verified``: only the services whose reuse terms were read and allow relaying them (``SOURCES.md``). ``all``: every
    service of the registry that is not excluded, whatever its terms (for an instance only its owner uses). ``off``: none.
    ``0``, ``false``, ``no`` and ``none`` mean ``off``; an unknown value means the default, the cautious one.
    """
    raw = os.environ.get("VIGIE_WMO_SOURCES", "").strip().lower()
    if raw in ("0", "false", "no", "none"):
        return "off"
    return raw if raw in WMO_SOURCES_MODES else DEFAULT_WMO_SOURCES


@dataclass(frozen=True)
class SwicSource:
    """One service of the registry."""

    source_id: str  # ``mx-smn-es``
    country: Optional[str]  # ISO 3166-1 alpha-2, upper case (``authorityCountry``)
    country_name: str
    name: str  # the service's name in its first language
    feeds: tuple[str, ...]  # the addresses of ``capAlertFeed``
    web: Optional[str] = None  # ``metLink``
    official: bool = True
    meteoalarm: bool = False  # the feed is MeteoAlarm's own


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_registry(payload: Any) -> tuple[SwicSource, ...]:
    """The services of a registry answer (``{"sources": [{"source": {...}}, ...]}``). A record without an id is skipped.

    Raises ``CapError`` when the answer is not a registry at all (so that a changed format is an error, not "no service").
    """
    records = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise CapError("the answer is not a SWIC registry (no 'sources' list)")
    found: list[SwicSource] = []
    for entry in records:
        record = entry.get("source") if isinstance(entry, dict) else None
        if not isinstance(record, dict) or (source_id := _text(record.get("sourceId"))) is None:
            continue
        country = _text(record.get("authorityCountry"))
        feeds = tuple(
            url
            for url in (part.strip() for part in str(record.get("capAlertFeed") or "").split(","))
            if url.startswith(("http://", "https://"))
        )
        languages = record.get("byLanguage")
        first = languages[0] if isinstance(languages, list) and languages and isinstance(languages[0], dict) else {}
        found.append(
            SwicSource(
                source_id=source_id,
                country=country.upper() if country and len(country) == 2 else None,
                country_name=_text(record.get("countryName")) or "",
                name=_text(first.get("name")) or _text(record.get("authorityAbbrev")) or source_id,
                feeds=feeds,
                web=_text(record.get("metLink")),
                official=record.get("sourceIsOfficial") is True,
                meteoalarm=record.get("isMeteoalarm") is True,
            )
        )
    if not found:
        raise CapError("the SWIC registry lists no service")
    return tuple(found)


def snapshot_payload(sources_payload: Any) -> dict[str, Any]:
    """The registry answer reduced to what this API reads, without the names and e-mail addresses of the people in it."""
    kept = ("sourceId", "byLanguage", "capAlertFeed", "countryName", "authorityCountry", "authorityAbbrev", "metLink",
            "sourceIsOfficial", "isMeteoalarm", "isCountry", "capAlertFeedStatus")
    out = []
    for entry in sources_payload["sources"]:
        record = entry["source"]
        trimmed = {key: record[key] for key in kept if key in record}
        trimmed["byLanguage"] = [{"code": lang.get("code"), "name": lang.get("name")} for lang in record.get("byLanguage", [])[:1]]
        out.append({"source": trimmed})
    return {"sources": out}


def bundled_registry(path: Path = SNAPSHOT_PATH) -> tuple[SwicSource, ...]:
    """The registry as it was when the snapshot was written (the services this API knows without asking the WMO)."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return parse_registry(json.load(handle))


class SwicRegistryCollector(BaseCollector[tuple[SwicSource, ...]]):
    """The registry of the WMO, cached a day and asked with ``If-Modified-Since``."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="wmo_swic_registry",
            name="WMO Severe Weather Information Centre, registry of services",
            domain="warnings",
            ttl_seconds=REGISTRY_TTL_S,
            timeout_seconds=15.0,
            http_client=http_client,
        )

    async def fetch_registry(self) -> Fetched[tuple[SwicSource, ...]]:
        return await self.fetch_ex(cache_key="registry")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[tuple[SwicSource, ...]]:
        previous: Optional[CacheEntry[tuple[SwicSource, ...]]] = kwargs.get("previous")
        headers = {"Accept": "application/json"}
        if previous is not None and previous.validators:
            if previous.validators.get("ETag"):
                headers["If-None-Match"] = previous.validators["ETag"]
            if previous.validators.get("Last-Modified"):
                headers["If-Modified-Since"] = previous.validators["Last-Modified"]
        response = await self._get_capped(client, REGISTRY_URL, max_bytes=MAX_REGISTRY_BYTES, headers=headers)
        if response.status_code == 304 and previous is not None:
            return Collected(previous.value, validators=previous.validators)
        response.raise_for_status()
        try:
            sources = parse_registry(response.json())
        except ValueError as exc:  # not JSON (a CloudFront error page) or not a registry
            raise CapError(f"the registry could not be read: {exc}") from exc
        validators = {name: response.headers[name] for name in ("ETag", "Last-Modified") if response.headers.get(name)}
        return Collected(sources, validators=validators or None)
