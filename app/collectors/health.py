"""The communicable disease threats the ECDC publishes, for the travel pages.

The European Centre for Disease Prevention and Control (ECDC) lists its publications in RSS feeds
(https://www.ecdc.europa.eu/en/rss-feeds). Three of them say what threatens a traveller, each the ten latest items:

- the **epidemiological updates** (``taxonomy/term/1310``): an outbreak, a worldwide overview of a disease;
- the **threat and risk assessments** (``taxonomy/term/1295``);
- the weekly **Communicable disease threats report** (CDTR, ``taxonomy/term/1505``).

Each item keeps the ECDC's words: its title, its summary (HTML, as given), its date and its link. The feeds name no country
(no ``category``, read on 2026-10-28): the countries an item names are matched by Vigie on their names
(``app/services/travel.py``), never stated by the ECDC. Each feed answers ``ETag`` and ``Last-Modified``: the three are asked
conditionally once an hour. Licence: CC BY 4.0, commercial use allowed (``SOURCES.md``). The answers of 2026-10-28 are recorded
in ``tests/fixtures/health``.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app import clock
from app.attribution import credit_text
from app.collectors.base import BaseCollector, CacheEntry, Collected, http_date_to_epoch
from app.models.travel import HealthItem, HealthKind, TravelText

ECDC_FEEDS: dict[HealthKind, str] = {
    "epidemiological_update": "https://www.ecdc.europa.eu/en/taxonomy/term/1310/feed",
    "risk_assessment": "https://www.ecdc.europa.eu/en/taxonomy/term/1295/feed",
    "threats_report": "https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed",
}
# An item stays in a feed until ten newer ones push it out: a weekly report, a few updates and assessments a month (the feeds of
# 2026-10-28: the ten latest updates span seven months, the ten latest reports ten weeks). An hour of delay is the trade-off.
TTL_S = 3600
MAX_ITEMS = 100  # a feed holds ten items; more is a mistake


@dataclass(frozen=True)
class HealthFeed:
    """The items of the three feeds, newest first, and each feed's own items (to reuse one answered ``304``)."""

    items: tuple[HealthItem, ...]
    by_kind: dict[str, tuple[HealthItem, ...]]


def _text(element: Optional[ET.Element]) -> Optional[str]:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def parse_ecdc(content: bytes, kind: HealthKind) -> tuple[HealthItem, ...]:
    """The items of one ECDC feed. An item with no title or no link is skipped; a feed with no channel raises."""
    root = ET.fromstring(content)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("the ECDC feed has no channel")
    attribution = credit_text("health_ecdc")
    items: list[HealthItem] = []
    for element in channel.findall("item")[:MAX_ITEMS]:
        title = _text(element.find("title"))
        url = _text(element.find("link"))
        if title is None or url is None:
            continue
        summary = _text(element.find("description"))
        published = http_date_to_epoch(_text(element.find("pubDate")))
        items.append(
            HealthItem(
                source="ecdc",
                kind=kind,
                title=html.unescape(title),
                url=url,
                summary=TravelText(language="en", format="html", content=summary) if summary else None,
                published_utc=int(published) if published is not None else None,
                attribution=attribution,
            )
        )
    return tuple(items)


def plain(text: str) -> str:
    """The words of an HTML summary, without its tags, to look for the names of countries in it."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


class ECDCHealthCollector(BaseCollector[HealthFeed]):
    """The ECDC's epidemiological updates, risk assessments and weekly threats reports: three conditional requests an hour."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="health_ecdc",
            name="ECDC communicable disease threats",
            domain="health",
            ttl_seconds=TTL_S,
            timeout_seconds=20.0,
            http_client=http_client,
        )

    async def fetch_feed(self) -> HealthFeed:
        return await self.fetch("feed")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[HealthFeed]:
        previous: Optional[CacheEntry[HealthFeed]] = kwargs.get("previous")
        old = (previous.validators or {}) if previous is not None else {}
        validators: dict[str, str] = {}
        by_kind: dict[str, tuple[HealthItem, ...]] = {}
        for kind, url in ECDC_FEEDS.items():
            headers = {}
            if old.get(f"{kind} ETag"):
                headers["If-None-Match"] = old[f"{kind} ETag"]
            if old.get(f"{kind} Last-Modified"):
                headers["If-Modified-Since"] = old[f"{kind} Last-Modified"]
            resp = await self._get(client, url, headers=headers)
            if resp.status_code == 304:
                if previous is None or kind not in previous.value.by_kind:
                    raise ValueError(f"the ECDC answered 304 Not Modified to a request that was not conditional ({kind})")
                by_kind[kind] = previous.value.by_kind[kind]
                validators.update({key: value for key, value in old.items() if key.startswith(f"{kind} ")})
                continue
            resp.raise_for_status()
            content = resp.content
            by_kind[kind] = await clock.run_blocking(lambda content=content, kind=kind: parse_ecdc(content, kind))
            for name in ("ETag", "Last-Modified"):
                if resp.headers.get(name):
                    validators[f"{kind} {name}"] = resp.headers[name]
        if not any(by_kind.values()):
            raise ValueError("no ECDC feed has an item that can be read")
        items = sorted(
            (item for kind_items in by_kind.values() for item in kind_items),
            key=lambda item: -(item.published_utc or 0),
        )
        return Collected(HealthFeed(tuple(items), by_kind), validators=validators or None)
