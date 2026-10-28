"""The travel advice, put together: every ministry's level for each country, one country's texts, the comparison and the archive.

The ministries are read by the collectors of ``app/collectors/travel.py``; this service asks them (each behind its own cache:
nothing here sends a request of its own), compares their advices on the common scale and archives each new version of an advice
(``TravelStore``).

**The comparison is ours** (``TravelComparison``, every field in its ``derived``): only the advices for a whole country that have a
common step are compared (an advice for "Gaza" or the "Azores" is not the advice of the country). Each one is a range on the common
scale (the FCDO's "no advice against travel" is 1 to 2); two ministries **disagree** when their ranges cannot meet, and the gap is
the number of steps between them. It never changes or replaces a ministry's level, and it is never called official.

A ministry whose first read takes long (the FCDO's 226 pages, once in the life of a database) does not hold the answer: after
``WAIT_S`` the answer is given without it, the ministry says ``loading``, and the read goes on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from app import clock
from app.attribution import CREDITS, merge_credits
from app.collectors import travel as collectors
from app.collectors.base import BaseCollector, CollectorUnavailableError
from app.commercial import COMMERCIAL_USE
from app.models.travel import (
    TravelAdvice,
    TravelChange,
    TravelComparison,
    TravelCountry,
    TravelCountryDetail,
    TravelFeedState,
    TravelLevel,
    TravelMinistry,
    TravelOverview,
    TravelScaleStep,
)
from app.services.geoindex import CountryIndex
from app.services.travel_store import TravelStore

log = logging.getLogger(__name__)

# How long an answer waits for a ministry that has not answered yet (its read goes on after).
WAIT_S = 8.0

NOTICE = (
    "Travel advice of foreign ministries, relayed as they publish it. Each ministry's level is in its own words; the common scale "
    "and the comparison are Vigie's, not an official level. The areas a ministry advises against inside a country are in its text "
    "only. Read the ministry's own page before you travel."
)


@dataclass(frozen=True)
class MinistryInfo:
    """What does not change about a ministry."""

    id: str
    name: str
    country: str
    url: str
    read: bool
    why_not_read: Optional[str] = None
    levels: tuple[tuple[int, dict[str, str]], ...] = ()
    statuses: tuple[str, ...] = ()
    licence: Optional[str] = None


# The scales are the ministries' own words, as their feeds write them (2026-10-28; docs/data-sources.md).
MINISTRIES: tuple[MinistryInfo, ...] = (
    MinistryInfo(
        "gb",
        "Foreign, Commonwealth & Development Office (United Kingdom)",
        "GB",
        "https://www.gov.uk/foreign-travel-advice",
        True,
        statuses=(
            collectors.GB_PARTS_ESSENTIAL,
            collectors.GB_PARTS_AVOID_ALL,
            collectors.GB_WHOLE_ESSENTIAL,
            collectors.GB_WHOLE_AVOID_ALL,
        ),
        licence="Open Government Licence v3.0",
    ),
    MinistryInfo(
        "us",
        "U.S. Department of State, Bureau of Consular Affairs (United States)",
        "US",
        "https://travel.state.gov",
        True,
        levels=(
            (1, {"en": "Level 1: Exercise Normal Precautions"}),
            (2, {"en": "Level 2: Exercise Increased Caution"}),
            (3, {"en": "Level 3: Reconsider Travel"}),
            (4, {"en": "Level 4: Do Not Travel"}),
        ),
        licence="Public domain",
    ),
    MinistryInfo(
        "ca",
        "Global Affairs Canada (Canada)",
        "CA",
        "https://travel.gc.ca/travelling/advisories",
        True,
        levels=(
            (1, {"en": "Exercise normal security precautions", "fr": "Prendre des mesures de sécurité normales"}),
            (2, {"en": "Exercise a high degree of caution", "fr": "Faire preuve d’une grande prudence"}),
            (3, {"en": "Avoid non-essential travel", "fr": "Éviter tout voyage non-essentiel"}),
            (4, {"en": "Avoid all travel", "fr": "Éviter tout voyage"}),
        ),
        licence="Open Government Licence – Canada",
    ),
    MinistryInfo(
        "au",
        "Smartraveller, Department of Foreign Affairs and Trade (Australia)",
        "AU",
        "https://www.smartraveller.gov.au/",
        True,
        levels=tuple((step, {"en": words}) for words, step in collectors.AU_LEVELS.items()),
        licence="Creative Commons Attribution (latest version)",
    ),
    MinistryInfo(
        "nl",
        "Ministerie van Buitenlandse Zaken (Netherlands)",
        "NL",
        "https://www.nederlandwereldwijd.nl/reisadvies",
        True,
        levels=tuple((step, {"nl": colour}) for colour, step in collectors.NL_COLOURS.items()),
        licence="CC0 1.0 Universal",
    ),
    MinistryInfo(
        "de",
        "Auswärtiges Amt (Germany)",
        "DE",
        "https://www.auswaertiges-amt.de/de/ReiseUndSicherheit/reise-und-sicherheitshinweise",
        False,
        why_not_read="Its open data interface is given on request; the request has not been answered yet.",
    ),
    MinistryInfo(
        "fr",
        "Ministère de l'Europe et des Affaires étrangères (France)",
        "FR",
        "https://www.diplomatie.gouv.fr/fr/conseils-aux-voyageurs/",
        False,
        why_not_read="No live feed of its advice was found; its pages are linked, not read.",
    ),
)


@dataclass
class _Read:
    """What one ministry gave for one answer."""

    state: TravelFeedState
    feed: Optional[collectors.TravelFeed] = None


def _overlap_gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """How many steps apart two ranges of the common scale are (0 when they meet)."""
    return max(0, a[0] - b[1], b[0] - a[1])


def compare(advices: list[TravelAdvice] | list[TravelLevel]) -> TravelComparison:
    """How the ministries' advices for one country compare (ours). Only the advices for the whole country with a common step count."""
    ranges: dict[str, tuple[int, int]] = {}
    for advice in advices:
        if advice.part or advice.common_min is None or advice.common_max is None:
            continue
        if advice.ministry in ranges:  # two advices of one ministry for the whole country: not compared, it cannot be told which
            ranges[advice.ministry] = (-1, -1)
            continue
        ranges[advice.ministry] = (advice.common_min, advice.common_max)
    ranges = {ministry: value for ministry, value in ranges.items() if value != (-1, -1)}
    compared = sorted(ranges)
    if len(compared) < 2:
        return TravelComparison(compared=compared)
    gap = max(_overlap_gap(ranges[a], ranges[b]) for a in compared for b in compared if a < b)
    if gap == 0:
        return TravelComparison(compared=compared, disagree=False, gap=0)
    top = max(low for low, _ in ranges.values())
    bottom = min(high for _, high in ranges.values())
    return TravelComparison(
        compared=compared,
        disagree=True,
        gap=gap,
        strictest=[m for m in compared if ranges[m][0] == top],
        laxest=[m for m in compared if ranges[m][1] == bottom],
    )


def _signature(advice: TravelAdvice) -> tuple[str, dict[str, Any]]:
    """What makes a version of an advice another one, and what is archived of it."""
    payload: dict[str, Any] = {
        "level": advice.level,
        "level_texts": advice.level_texts,
        "statuses": advice.statuses,
        "common_min": advice.common_min,
        "common_max": advice.common_max,
        "published_utc": advice.published_utc,
        "published_text": advice.published_text,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False), payload


def _light(advice: TravelAdvice) -> TravelLevel:
    return TravelLevel(
        ministry=advice.ministry,
        title=advice.title,
        part=advice.part,
        level=advice.level,
        statuses=advice.statuses,
        regional=advice.regional,
        common_min=advice.common_min,
        common_max=advice.common_max,
        published_utc=advice.published_utc,
        published_text=advice.published_text,
        checked_utc=advice.checked_utc,
    )


class TravelService:
    """The travel advice of every ministry read by this instance."""

    def __init__(self, sources: dict[str, BaseCollector[Any]], countries: CountryIndex, store: Optional[TravelStore]) -> None:
        self.sources = sources
        self.countries = countries
        self.store = store
        self._recorded: dict[str, int] = {}  # ministry -> id() of the feed last archived (a cached feed is archived once)
        self._signatures: Optional[dict[tuple[str, str, str], str]] = None

    # --- reading the ministries --------------------------------------------------------------------------------------

    async def _read(self, ministry: str, wait_s: Optional[float]) -> _Read:
        collector = self.sources[ministry]
        try:
            pending = collector.fetch_ex("feed")
            fetched = await (asyncio.wait_for(pending, wait_s) if wait_s is not None else pending)
        except asyncio.TimeoutError:
            return self._kept(collector, "loading")
        except CollectorUnavailableError:
            return self._kept(collector, "unavailable")
        feed = fetched.value
        if isinstance(feed, collectors.TravelFeed):
            self._archive(feed)
            return _Read("stale" if fetched.stale else "ok", feed)
        return _Read("unavailable")

    @staticmethod
    def _kept(collector: BaseCollector[Any], state: TravelFeedState) -> _Read:
        """The FCDO's pages kept by an earlier run, when the FCDO cannot be read now (never archived: not a read)."""
        kept = collector.kept_feed() if isinstance(collector, collectors.FCDOTravelCollector) else None
        return _Read("kept", kept) if kept is not None and state == "unavailable" else _Read(state, kept)

    async def read_all(self, wait_s: Optional[float] = WAIT_S) -> dict[str, _Read]:
        """Every ministry, at once; ``wait_s=None`` waits for all of them (the scheduler)."""
        names = list(self.sources)
        results = await asyncio.gather(*(self._read(name, wait_s) for name in names))
        return dict(zip(names, results, strict=True))

    async def refresh(self) -> None:
        """Keep every ministry fresh and archive what changed (the scheduler calls it at each cycle; a cached feed costs nothing)."""
        await self.read_all(wait_s=None)

    # --- the archive -------------------------------------------------------------------------------------------------

    def _archive(self, feed: collectors.TravelFeed) -> None:
        """Write the versions of ``feed`` that the archive does not have yet (once per feed read)."""
        if self.store is None or self._recorded.get(feed.ministry) == id(feed):
            return
        self._recorded[feed.ministry] = id(feed)
        signatures = self._signatures if self._signatures is not None else self.store.last_signatures()
        self._signatures = signatures
        now = int(clock.wall())
        rows = []
        for advice in feed.advices:
            key = (advice.ministry, advice.country, advice.title)
            signature, payload = _signature(advice)
            if signatures.get(key) == signature:
                continue
            payload["first"] = key not in signatures
            signatures[key] = signature
            rows.append((advice.ministry, advice.country, advice.title, advice.checked_utc or now, signature, payload))
        try:
            self.store.add_versions(rows)
        except Exception as exc:  # the archive never makes the answer fail
            log.warning("travel archive: %s", exc)

    def history(self, country: str) -> list[TravelChange]:
        """The versions archived for ``country``, newest first, each said to be the first or to change the level or not."""
        if self.store is None:
            return []
        rows = list(reversed(self.store.versions(country)))  # oldest first, to compare each with the one before
        previous: dict[tuple[str, str], dict[str, Any]] = {}
        changes: list[TravelChange] = []
        for row in rows:
            key = (row["ministry"], row["title"])
            before = previous.get(key)
            first = bool(row.get("first")) or before is None
            changed = None
            if not first and before is not None:
                changed = row.get("level") != before.get("level") or row.get("statuses") != before.get("statuses")
            changes.append(
                TravelChange(
                    ministry=row["ministry"],
                    title=row["title"],
                    observed_utc=row["observed_utc"],
                    first=first,
                    level_changed=changed,
                    level=row.get("level"),
                    level_texts=row.get("level_texts") or {},
                    statuses=row.get("statuses") or [],
                    common_min=row.get("common_min"),
                    common_max=row.get("common_max"),
                    published_utc=row.get("published_utc"),
                    published_text=row.get("published_text"),
                )
            )
            previous[key] = row
        return list(reversed(changes))

    # --- the answers -------------------------------------------------------------------------------------------------

    def _ministries(self, reads: dict[str, _Read]) -> list[TravelMinistry]:
        listed: list[TravelMinistry] = []
        for info in MINISTRIES:
            read = reads.get(info.id)
            credit = CREDITS.get(f"travel_{info.id}")
            use = COMMERCIAL_USE.get(f"travel_{info.id}")
            feed = read.feed if read else None
            listed.append(
                TravelMinistry(
                    id=info.id,
                    name=info.name,
                    country=info.country,
                    url=info.url,
                    read=info.read,
                    why_not_read=info.why_not_read,
                    levels=[TravelScaleStep(level=step, words=words) for step, words in info.levels],
                    statuses=list(info.statuses),
                    licence=info.licence,
                    commercial_use=use.status if use else None,
                    state=read.state if read else None,
                    advices=len(feed.advices) if feed else (0 if read else None),
                    unplaced=list(feed.unplaced) if feed else [],
                    pending=list(feed.pending) if feed else [],
                    attribution=credit.text if credit else None,
                )
            )
        return listed

    @staticmethod
    def _attribution(reads: dict[str, _Read]) -> Optional[str]:
        return merge_credits(*(CREDITS[f"travel_{m}"].text for m, read in reads.items() if read.feed is not None))

    def _name(self, country: str) -> Optional[str]:
        name = self.countries.name(country)
        return None if name == country else name

    async def overview(self) -> TravelOverview:
        """``GET /v1/travel``: every ministry's level for each country."""
        reads = await self.read_all()
        by_country: dict[str, list[TravelAdvice]] = {}
        for read in reads.values():
            for advice in read.feed.advices if read.feed else ():
                by_country.setdefault(advice.country, []).append(advice)
        countries = [
            TravelCountry(
                country=country,
                name=self._name(country),
                advices=[_light(advice) for advice in advices],
                comparison=compare(advices),
            )
            for country, advices in sorted(by_country.items())
        ]
        return TravelOverview(
            generated_utc=int(clock.wall()),
            ministries=self._ministries(reads),
            countries=countries,
            notice=NOTICE,
            attribution=self._attribution(reads),
        )

    async def country(self, country: str) -> TravelCountryDetail:
        """``GET /v1/travel/{cc}``: every ministry's advice for ``country``, its texts, the comparison and the archive."""
        reads = await self.read_all()
        advices: list[TravelAdvice] = []
        not_covered: list[str] = []
        for ministry, read in reads.items():
            found = [advice for advice in (read.feed.advices if read.feed else ()) if advice.country == country]
            if read.feed is not None and not found:
                not_covered.append(ministry)
            advices.extend(found)
        advices = [await self._with_text(advice) for advice in advices]
        store_start = self.store.first_version_utc() if self.store is not None else None
        return TravelCountryDetail(
            generated_utc=int(clock.wall()),
            country=country,
            name=self._name(country),
            advices=advices,
            comparison=compare(advices),
            history=self.history(country),
            history_since_utc=store_start,
            not_covered=sorted(not_covered),
            ministries=self._ministries(reads),
            notice=NOTICE,
            attribution=merge_credits(*(advice.attribution for advice in advices)),
        )

    async def _with_text(self, advice: TravelAdvice) -> TravelAdvice:
        """Canada keeps the text of a destination in a file of its own: read it now (cached six hours). Without it, no text."""
        collector = self.sources.get("ca")
        if advice.ministry != "ca" or advice.summary or not isinstance(collector, collectors.CanadaTravelCollector):
            return advice
        try:
            texts = await asyncio.wait_for(collector.fetch_detail(advice.source_code), WAIT_S)
        except (asyncio.TimeoutError, CollectorUnavailableError, ValueError):
            return advice
        return advice.model_copy(update={"summary": texts})
