"""The health threats for travellers: what the ECDC publishes, and the agencies that are linked only.

The ECDC is read by ``app/collectors/health.py`` (behind its cache: nothing here sends a request). Its feeds name no country,
so **the countries of an item are ours**: an item names a country when its title or its summary contains the country's English
name (``NameMatcher``: the names of ``app/data/countries.geojson.gz``, and the few other names of ``ALIASES``), which is listed in
the item's ``derived``. A country can be named for another reason than a threat there ("cases imported from Kenya"): an item is
shown with the country, never as a level of the country.

The WHO and the CDC are linked, not read (``AGENCIES``: why, in one sentence each).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from app import clock
from app.attribution import CREDITS
from app.collectors.base import CollectorUnavailableError
from app.collectors.health import ECDCHealthCollector, HealthFeed, plain
from app.commercial import COMMERCIAL_USE
from app.models.travel import HealthAgency, HealthItem, TravelFeedState, TravelHealth
from app.services.geoindex import CountryIndex

WAIT_S = 8.0

NOTICE = (
    "Publications of public health agencies about communicable disease threats, relayed as they publish them. The countries an "
    "item names are matched by Vigie on their names, not stated by the agency. Ask a doctor or a travel clinic before you travel."
)

# Other names of a country than the one of ``countries.geojson.gz`` (Natural Earth), as the agencies write them. Ours: a name is
# added here when a feed was seen to use it.
ALIASES: dict[str, str] = {
    "DRC": "CD",
    "Côte d'Ivoire": "CI",
    "Cabo Verde": "CV",
    "Czechia": "CZ",
    "Türkiye": "TR",
    "Timor-Leste": "TL",
    "United States": "US",
    "USA": "US",
    "UK": "GB",
    "Viet Nam": "VN",
    "Russian Federation": "RU",
    "Republic of Korea": "KR",
}


class NameMatcher:
    """Finds the countries named in a text: the longest name first, a whole word, with its capitals ("Niger" is not in "Nigeria")."""

    def __init__(self, names: dict[str, str]) -> None:
        self._codes = names
        ordered = sorted(names, key=len, reverse=True)
        self._pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(name) for name in ordered) + r")(?![\w-])") if ordered else None

    @classmethod
    def for_countries(cls, countries: CountryIndex) -> "NameMatcher":
        names = {countries.name(code): code for code in countries.countries() if countries.name(code) != code}
        names.update(ALIASES)
        return cls(names)

    def countries(self, text: str) -> list[str]:
        if self._pattern is None:
            return []
        return sorted({self._codes[match.group(1)] for match in self._pattern.finditer(text)})


@dataclass(frozen=True)
class AgencyInfo:
    id: str
    name: str
    url: str
    read: bool
    why_not_read: Optional[str] = None
    licence: Optional[str] = None


AGENCIES: tuple[AgencyInfo, ...] = (
    AgencyInfo(
        "ecdc",
        "European Centre for Disease Prevention and Control (ECDC)",
        "https://www.ecdc.europa.eu/en/publications-and-data/monitoring/weekly-threats-reports",
        True,
        licence="CC BY 4.0",
    ),
    AgencyInfo(
        "who",
        "World Health Organization (WHO), Disease Outbreak News",
        "https://www.who.int/emergencies/disease-outbreak-news",
        False,
        why_not_read=(
            "Its terms allow extracts of its website for research or private study, not for a commercial use; it is linked, not read."
        ),
    ),
    AgencyInfo(
        "cdc",
        "U.S. Centers for Disease Control and Prevention (CDC), Travel Health Notices",
        "https://wwwnc.cdc.gov/travel/notices",
        False,
        why_not_read=(
            "Most of its pages are in the public domain, but some are not and a notice does not say which; it is linked, not read."
        ),
    ),
)


class HealthService:
    """The ECDC's publications, with the countries they name (ours), and the agencies that are linked only."""

    def __init__(self, ecdc: ECDCHealthCollector, countries: CountryIndex) -> None:
        self.ecdc = ecdc
        self.matcher = NameMatcher.for_countries(countries)
        self._matched: tuple[int, tuple[HealthItem, ...]] = (0, ())

    async def _read(self, wait_s: Optional[float]) -> tuple[TravelFeedState, Optional[HealthFeed]]:
        try:
            pending = self.ecdc.fetch_ex("feed")
            fetched = await (asyncio.wait_for(pending, wait_s) if wait_s is not None else pending)
        except asyncio.TimeoutError:
            return "loading", None
        except CollectorUnavailableError:
            return "unavailable", None
        if not isinstance(fetched.value, HealthFeed):
            return "unavailable", None
        return ("stale" if fetched.stale else "ok"), fetched.value

    async def refresh(self) -> None:
        """Keep the ECDC fresh (the scheduler calls it with the ministries)."""
        await self._read(None)

    def _with_countries(self, feed: HealthFeed) -> tuple[HealthItem, ...]:
        """The items of ``feed`` with the countries they name (worked out once per feed read)."""
        if self._matched[0] == id(feed):
            return self._matched[1]
        items = tuple(
            item.model_copy(
                update={
                    "countries": self.matcher.countries(" ".join((item.title, plain(item.summary.content) if item.summary else ""))),
                    "derived": ["countries"],
                }
            )
            for item in feed.items
        )
        self._matched = (id(feed), items)
        return items

    def _agencies(self, state: TravelFeedState, feed: Optional[HealthFeed]) -> list[HealthAgency]:
        listed: list[HealthAgency] = []
        for info in AGENCIES:
            credit = CREDITS.get(f"health_{info.id}")
            use = COMMERCIAL_USE.get(f"health_{info.id}")
            listed.append(
                HealthAgency(
                    id=info.id,
                    name=info.name,
                    url=info.url,
                    read=info.read,
                    why_not_read=info.why_not_read,
                    licence=info.licence,
                    commercial_use=use.status if use else None,
                    state=state if info.read else None,
                    items=len(feed.items) if info.read and feed is not None else None,
                    attribution=credit.text if credit else None,
                )
            )
        return listed

    async def answer(self, country: Optional[str] = None, wait_s: Optional[float] = WAIT_S) -> TravelHealth:
        """``GET /v1/travel/health`` (every item), or the items that name ``country`` (in ``GET /v1/travel/{country}``)."""
        state, feed = await self._read(wait_s)
        items = list(self._with_countries(feed)) if feed is not None else []
        if country is not None:
            items = [item for item in items if country in (item.countries or [])]
        credit = CREDITS.get("health_ecdc")
        return TravelHealth(
            generated_utc=int(clock.wall()),
            country=country,
            agencies=self._agencies(state, feed),
            items=items,
            notice=NOTICE,
            attribution=credit.text if credit is not None and feed is not None else None,
        )
