"""Travel advice collectors: what five foreign ministries advise their citizens about each destination.

One collector per ministry, each read as a whole (one answer, or a few, for every destination) behind the cache of
``BaseCollector``; the formats were read on 2026-10-28 and recorded in ``tests/fixtures/travel`` (its README says what each
file is). Licences, credits and what could not be verified: ``SOURCES.md``. Cache lifetimes and request counts:
``docs/data-sources.md``.

- **United Kingdom**, Foreign, Commonwealth & Development Office (FCDO), through the GOV.UK Content API: the index
  ``/api/content/foreign-travel-advice`` lists the 226 pages with the date of each one's latest version, and **each page**
  (``/api/content/foreign-travel-advice/<page>``, about 66 kB) holds the alert statuses, the text and the FCDO's own history
  of changes. A page is read only when the index says it has a newer version than the one kept (about 6 a day on
  2026-10-28); the pages kept survive a restart (``TravelPageStore``), so the 226 pages (15 MB) are read once, not at each start.
  The Content API allows 10 requests a second per client: the pages are read one after the other, ``GB_PAGE_SPACING_S`` apart.
- **United States**, Department of State: ``cadataapi.state.gov/api/TravelAdvisories``, every advice with its text in one
  answer (490 kB, no validator: read whole each time).
- **Canada**, Global Affairs Canada: ``index-updated.json`` (every destination, its level in English and French, 195 kB,
  answered ``304`` to a conditional request); the text of one destination is in ``cta-cap-<code>.json`` (283 kB for Mexico, with
  the addresses of the embassies), read **only** when that destination is asked for (``fetch_detail``).
- **Australia**, Smartraveller (Department of Foreign Affairs and Trade): ``destinations-export``, every destination with
  its texts in one answer (298 kB, gzipped on the wire, regenerated every 15 minutes, with ``ETag``).
- **The Netherlands**, Ministerie van Buitenlandse Zaken (NederlandWereldwijd): ``/v2/.../infotypes/traveladvice``, 200
  destinations an answer (``rows=200``, two answers for the 226), each with its summary "In het kort". The feed has **no
  field for the colour** (``classification`` says "Dit veld wordt niet meer ondersteund"): the colour is read from the
  sentence of the summary that states it, and only when the summary names a single colour (see ``nl_level``).

A destination whose code or page no table of ``travel_codes.py`` knows is not placed: it is counted in ``unplaced``, never
guessed. An item that cannot be read is skipped and counted; an answer none of whose items can be read raises (the cache
or the stale value is served).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional, Protocol

import httpx

from app import clock
from app.attribution import credit_text
from app.collectors import travel_codes as codes
from app.collectors.base import BaseCollector, CacheEntry, Collected, parse_iso_utc
from app.models.travel import TravelAdvice, TravelSourceChange, TravelText

log = logging.getLogger(__name__)

# How long a feed is served before it is asked again. The ministries change a few advices a day (the recorded answers of
# 2026-10-28, versions dated in the last 24 hours / 7 days: the FCDO 6 / 35, Canada 6 / 27, Smartraveller about 7 / 20, the
# Netherlands 1 / 25, the Department of State 0 / 13; docs/data-sources.md): an hour of delay on an advice that stays for weeks
# is the trade-off.
TTL_S = 3600
# The text of one Canadian destination (read on request): kept six hours, and asked again conditionally.
CA_DETAIL_TTL_S = 6 * 3600

MAX_ITEMS = 1000  # every feed has about 230 destinations; thousands is a mistake

US_URL = "https://cadataapi.state.gov/api/TravelAdvisories"
CA_INDEX_URL = "https://data.international.gc.ca/travel-voyage/index-updated.json"
CA_DETAIL_URL = "https://data.international.gc.ca/travel-voyage/cta-cap-{code}.json"
CA_PAGE_EN = "https://travel.gc.ca/destinations/{slug}"
AU_URL = "https://www.smartraveller.gov.au/destinations-export"
NL_URL = "https://opendata.nederlandwereldwijd.nl/v2/sources/nederlandwereldwijd/infotypes/traveladvice"
NL_PAGE_SIZE = 200  # the most the feed gives in one answer (rows=200)
NL_MAX_PAGES = 5
GB_INDEX_URL = "https://www.gov.uk/api/content/foreign-travel-advice"
GB_PAGE_URL = "https://www.gov.uk/api/content/foreign-travel-advice/{page}"
GB_WEB_URL = "https://www.gov.uk/foreign-travel-advice/{page}"
# The GOV.UK Content API allows 10 requests a second per client: one page every 0.2 s stays at half of it.
GB_PAGE_SPACING_S = 0.2
GB_WARNINGS_PART = "warnings-and-insurance"

_PAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")

# The levels of Smartraveller, in its own words (the four of its scale, seen in the export on 2026-10-28) -> its step.
AU_LEVELS: dict[str, int] = {
    "Exercise normal safety precautions": 1,
    "Exercise a high degree of caution": 2,
    "Reconsider your need to travel": 3,
    "Do not travel": 4,
}

# The Dutch colours -> their step ("Groen: u kunt erheen reizen", "Geel: let op, er zijn risico's", "Oranje: alleen
# noodzakelijke reizen", "Rood: niet reizen": https://www.nederlandwereldwijd.nl/reisadvies/reisadviezen-kleurcodes).
NL_COLOURS: dict[str, int] = {"groen": 1, "geel": 2, "oranje": 3, "rood": 4}
_NL_COLOUR = re.compile(r"\b(groen|geel|oranje|rood)\b", re.IGNORECASE)
# The sentence the summaries state the colour with: "De kleurcode van het reisadvies voor Réunion is groen." (also
# "voor het reisadvies", "van het reisadvies van India is rood voor ...").
_NL_STATEMENT = re.compile(
    r"kleurcode\s+(?:van|voor)\s+het\s+reisadvies\s+(?:(?:van|voor)\s+[^.]*?\s+)?is\s+(groen|geel|oranje|rood)\b",
    re.IGNORECASE,
)

# The FCDO's alert statuses (the four seen on its 226 pages on 2026-10-28).
GB_WHOLE_AVOID_ALL = "avoid_all_travel_to_whole_country"
GB_WHOLE_ESSENTIAL = "avoid_all_but_essential_travel_to_whole_country"
GB_PARTS_AVOID_ALL = "avoid_all_travel_to_parts"
GB_PARTS_ESSENTIAL = "avoid_all_but_essential_travel_to_parts"
GB_STATUSES = frozenset({GB_WHOLE_AVOID_ALL, GB_WHOLE_ESSENTIAL, GB_PARTS_AVOID_ALL, GB_PARTS_ESSENTIAL})

_US_TITLE = re.compile(r"^(?P<name>.*?)\s+-\s+(?P<level>Level\s+(?P<step>[1-4])\s*:.*\S)\s*$", re.DOTALL)
_US_PAGE = re.compile(r"/([^/]+)\.html$")
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class TravelFeed:
    """Every advice of one ministry, as read.

    ``unplaced``: the destinations of the feed no table places in a country (their titles). ``skipped``: the items that could
    not be read. ``pending`` (the FCDO): the pages the index says have a newer version that could not be read yet; the
    version kept is served meanwhile, with the date it was last confirmed.
    """

    ministry: str
    advices: tuple[TravelAdvice, ...]
    unplaced: tuple[str, ...] = ()
    skipped: int = 0
    pending: tuple[str, ...] = ()


def _text(value: Any) -> Optional[str]:
    """A non-empty string, stripped of the spaces around it (non-breaking ones included); ``None`` otherwise."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _obj(value: Any) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _derived(*extra: str, common: bool = True) -> list[str]:
    return ["country", *extra, *(["common_min", "common_max"] if common else [])]


# --- United States -----------------------------------------------------------------------------------------------------


def parse_us(payload: Any, checked_utc: int) -> TravelFeed:
    """The advices of the Department of State. The level is the one its title states (``Iraq - Level 4: Do Not Travel``)."""
    if not isinstance(payload, list) or len(payload) > MAX_ITEMS:
        raise ValueError("Department of State answer is not a list of advisories")
    credit = credit_text("travel_us")
    advices: list[TravelAdvice] = []
    unplaced: list[str] = []
    skipped = 0
    for item in payload:
        title = _text(item.get("Title")) if isinstance(item, dict) else None
        link = _text(item.get("Link")) if isinstance(item, dict) else None
        match = _US_TITLE.match(title) if title else None
        page = _US_PAGE.search(link) if link else None
        if title is None or link is None or match is None or page is None:
            skipped += 1
            continue
        countries = codes.us_countries(page.group(1))
        name = match.group("name").strip()
        if not countries:
            unplaced.append(name)
            continue
        step = int(match.group("step"))
        summary = _text(item.get("Summary"))
        for country in countries:
            advices.append(
                TravelAdvice(
                    ministry="us",
                    country=country,
                    source_code=page.group(1),
                    title=name,
                    level=step,
                    level_texts={"en": match.group("level")},
                    common_min=step,
                    common_max=step,
                    published_utc=parse_iso_utc(item.get("Updated")) or parse_iso_utc(item.get("Published")),
                    checked_utc=checked_utc,
                    url=link,
                    summary=[TravelText(language="en", format="html", content=summary)] if summary else [],
                    derived=_derived(),
                    attribution=credit,
                )
            )
    if payload and not advices and not unplaced:
        raise ValueError(f"Department of State answer: {skipped} advisories, none could be read")
    # Two pages for the same destination under the same title (Tonga, on 2026-10-28: ``destination.ton`` and an older
    # ``tonga-travel-advisory``): the newest is kept. Two titles for one country (Gaza, The West Bank) are two advices.
    newest: dict[tuple[str, str], TravelAdvice] = {}
    for advice in advices:
        key = (advice.country, advice.title)
        kept = newest.get(key)
        if kept is None or (advice.published_utc or 0) > (kept.published_utc or 0):
            newest[key] = advice
    return TravelFeed("us", tuple(newest.values()), tuple(unplaced), skipped)


class USTravelCollector(BaseCollector[TravelFeed]):
    """The travel advisories of the U.S. Department of State: one answer for every destination, cached an hour."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="travel_us",
            name="U.S. Department of State travel advisories",
            domain="travel",
            ttl_seconds=TTL_S,
            timeout_seconds=20.0,
            http_client=http_client,
        )

    async def fetch_feed(self) -> TravelFeed:
        return await self.fetch("feed")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> TravelFeed:
        resp = await self._get(client, US_URL)
        resp.raise_for_status()
        return await clock.run_blocking(lambda: parse_us(resp.json(), int(clock.wall())))


# --- Canada ------------------------------------------------------------------------------------------------------------


def parse_ca_index(payload: Any, checked_utc: int) -> TravelFeed:
    """The advices of Global Affairs Canada (``index-updated.json``): the level is ``advisory-state`` 0 to 3, worded in both
    languages. The text is not in the index (``parse_ca_detail``)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or len(data) > MAX_ITEMS:
        raise ValueError("Global Affairs Canada answer has no 'data' object")
    credit = credit_text("travel_ca")
    advices: list[TravelAdvice] = []
    unplaced: list[str] = []
    skipped = 0
    for key, item in data.items():
        if not isinstance(item, dict):
            skipped += 1
            continue
        eng = _obj(item.get("eng"))
        fra = _obj(item.get("fra"))
        name = _text(eng.get("name")) or _text(item.get("country-eng"))
        slug = _text(eng.get("url-slug"))
        state = item.get("advisory-state")
        if name is None or slug is None or not _PAGE_NAME.match(slug) or isinstance(state, bool) or state not in (0, 1, 2, 3):
            skipped += 1
            continue
        code = _text(item.get("country-iso")) or str(key)
        countries = codes.ca_countries(code)
        if not countries:
            unplaced.append(name)
            continue
        step = int(state) + 1
        levels = {lang: text for lang, text in (("en", _text(eng.get("advisory-text"))), ("fr", _text(fra.get("advisory-text")))) if text}
        notes = [
            TravelText(language=lang, format="text", content=text)
            for lang, text in (("en", _text(eng.get("recent-updates"))), ("fr", _text(fra.get("recent-updates"))))
            if text
        ]
        published = _obj(item.get("date-published"))
        regional = item.get("has-regional-advisory")
        advices.append(
            TravelAdvice(
                ministry="ca",
                country=countries[0],
                source_code=code,
                title=name,
                level=step,
                level_texts=levels,
                regional=bool(regional) if regional in (0, 1) else None,
                common_min=step,
                common_max=step,
                published_utc=parse_iso_utc(published.get("asp")),
                checked_utc=checked_utc,
                url=CA_PAGE_EN.format(slug=slug),
                change_note=notes,
                derived=_derived(),
                attribution=credit,
            )
        )
    if data and not advices and not unplaced:
        raise ValueError(f"Global Affairs Canada answer: {skipped} destinations, none could be read")
    return TravelFeed("ca", tuple(advices), tuple(unplaced), skipped)


def parse_ca_detail(payload: Any) -> list[TravelText]:
    """The risk levels of one Canadian destination (``advisories``: the national advisory and the regional ones), in English
    and in French, as given."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Global Affairs Canada destination has no 'data' object")
    texts: list[TravelText] = []
    for lang, key in (("en", "eng"), ("fr", "fra")):
        part = data.get(key)
        content = _text(part.get("advisories")) if isinstance(part, dict) else None
        if content:
            texts.append(TravelText(language=lang, format="html", title=None, content=content))
    return texts


class CanadaTravelCollector(BaseCollector[Any]):
    """The travel advice of Global Affairs Canada: the index (conditional, an hour) and, on request, one destination's text."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="travel_ca",
            name="Global Affairs Canada travel advice and advisories",
            domain="travel",
            ttl_seconds=TTL_S,
            timeout_seconds=20.0,
            http_client=http_client,
        )

    async def fetch_feed(self) -> TravelFeed:
        return await self.fetch("feed")

    async def fetch_detail(self, code: str) -> list[TravelText]:
        """The risk levels of the destination ``code`` (``MX``, ``PT-20``), in English and French: one conditional request."""
        if not re.fullmatch(r"[A-Z]{2}(-[0-9A-Z]{1,3})?", code):
            raise ValueError(f"not a Canadian destination code: {code!r}")
        return await self.fetch(f"detail:{code}", detail=code, ttl_seconds=CA_DETAIL_TTL_S)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[Any]:
        previous: Optional[CacheEntry[Any]] = kwargs.get("previous")
        detail: Optional[str] = kwargs.get("detail")
        url = CA_INDEX_URL if detail is None else CA_DETAIL_URL.format(code=detail.lower())
        resp = await self._get(client, url, headers=_conditional(previous))
        now = int(clock.wall())
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("Global Affairs Canada answered 304 Not Modified to a request that was not conditional")
            value = previous.value if detail is not None else _checked(previous.value, now)
            return Collected(value, validators=previous.validators)
        resp.raise_for_status()
        if detail is None:
            value = await clock.run_blocking(lambda: parse_ca_index(resp.json(), now))
        else:
            value = await clock.run_blocking(lambda: parse_ca_detail(resp.json()))
        return Collected(value, validators=_validators(resp))


# --- Australia ---------------------------------------------------------------------------------------------------------


def parse_au(payload: Any, checked_utc: int) -> TravelFeed:
    """The advices of Smartraveller. Its strings are HTML-escaped (``Côte d&#039;Ivoire``): the title is unescaped, the texts are
    kept as given and said to be HTML. Its dates (``Mon, 2026-06-29 10:08``) carry no time zone: kept as written."""
    if not isinstance(payload, list) or len(payload) > MAX_ITEMS:
        raise ValueError("Smartraveller answer is not a list of destinations")
    credit = credit_text("travel_au")
    advices: list[TravelAdvice] = []
    unplaced: list[str] = []
    skipped = 0
    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        title = _text(item.get("title"))
        url = _text(item.get("field_url"))
        if title is None or url is None or not url.startswith("https://www.smartraveller.gov.au/"):
            skipped += 1
            continue
        page = url.rstrip("/").rsplit("/", 1)[-1]
        if page in codes.AU_NOT_DESTINATIONS:
            continue
        name = html.unescape(title)
        countries = codes.au_countries(page)
        if not countries:
            unplaced.append(name)
            continue
        words = _text(item.get("field_overall_advice_level"))
        step = AU_LEVELS.get(words) if words else None
        levels_text = _text(item.get("field_advice_levels"))
        update = _text(item.get("field_last_update"))
        advices.append(
            TravelAdvice(
                ministry="au",
                country=countries[0],
                source_code=page,
                title=name,
                level=step,
                level_texts={"en": words} if words else {},
                common_min=step,
                common_max=step,
                published_text=_text(item.get("changed")),
                checked_utc=checked_utc,
                url=url,
                summary=[TravelText(language="en", format="html", content=levels_text)] if levels_text else [],
                change_note=[TravelText(language="en", format="html", content=update)] if update else [],
                derived=_derived(),
                attribution=credit,
            )
        )
    if payload and not advices and not unplaced:
        raise ValueError(f"Smartraveller answer: {skipped} destinations, none could be read")
    return TravelFeed("au", tuple(advices), tuple(unplaced), skipped)


class SmartravellerCollector(BaseCollector[TravelFeed]):
    """The travel advice of Smartraveller (Australia): one conditional request for every destination, cached an hour."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="travel_au",
            name="Smartraveller travel advice (Australia, DFAT)",
            domain="travel",
            ttl_seconds=TTL_S,
            timeout_seconds=20.0,
            http_client=http_client,
        )

    async def fetch_feed(self) -> TravelFeed:
        return await self.fetch("feed")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[TravelFeed]:
        previous: Optional[CacheEntry[TravelFeed]] = kwargs.get("previous")
        resp = await self._get(client, AU_URL, headers=_conditional(previous))
        now = int(clock.wall())
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("Smartraveller answered 304 Not Modified to a request that was not conditional")
            return Collected(_checked(previous.value, now), validators=previous.validators)
        resp.raise_for_status()
        feed = await clock.run_blocking(lambda: parse_au(resp.json(), now))
        return Collected(feed, validators=_validators(resp))


# --- The Netherlands ---------------------------------------------------------------------------------------------------


def nl_level(introduction: str) -> tuple[Optional[int], Optional[bool]]:
    """The colour of a Dutch advice, read from its summary "In het kort": ``(step, regional)``.

    The step is given only when the summary names **one** colour and states it in the usual sentence ("De kleurcode van het
    reisadvies voor Réunion is groen."); a summary that names several colours gives no step for the whole destination
    (``None``) and ``regional=True``; a summary that names none gives ``(None, None)``.
    """
    plain = re.sub(r"\s+", " ", _TAGS.sub(" ", introduction))
    named = {word.lower() for word in _NL_COLOUR.findall(plain)}
    if not named:
        return None, None
    if len(named) > 1:
        return None, True
    stated = {word.lower() for word in _NL_STATEMENT.findall(plain)}
    return (NL_COLOURS[next(iter(named))], False) if stated == named else (None, False)


def parse_nl(items: list[Any], checked_utc: int) -> TravelFeed:
    """The advices of the Dutch ministry (the items of every page of the feed)."""
    credit = credit_text("travel_nl")
    advices: list[TravelAdvice] = []
    unplaced: list[str] = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        code = _text(item.get("isocode")) or _text(item.get("id"))
        name = _text(item.get("location"))
        url = _text(item.get("canonical"))
        introduction = _text(item.get("introduction"))
        if code is None or name is None or url is None or not url.startswith("https://www.nederlandwereldwijd.nl/"):
            skipped += 1
            continue
        countries = codes.nl_countries(code)
        if not countries:
            unplaced.append(name)
            continue
        step, regional = nl_level(introduction or "")
        colour = next((word for word, value in NL_COLOURS.items() if value == step), None)
        advices.append(
            TravelAdvice(
                ministry="nl",
                country=countries[0],
                source_code=code,
                title=name,
                level=step,
                level_texts={"nl": colour} if colour else {},
                regional=regional,
                common_min=step,
                common_max=step,
                published_utc=parse_iso_utc(item.get("lastmodified")),
                checked_utc=checked_utc,
                url=url,
                summary=[TravelText(language="nl", format="html", content=introduction)] if introduction else [],
                derived=_derived("level", "level_texts", "regional"),
                attribution=credit,
            )
        )
    if items and not advices and not unplaced:
        raise ValueError(f"NederlandWereldwijd answer: {skipped} destinations, none could be read")
    return TravelFeed("nl", tuple(advices), tuple(unplaced), skipped)


class NetherlandsTravelCollector(BaseCollector[TravelFeed]):
    """The travel advice of the Dutch ministry: two answers of 200 destinations, cached an hour."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="travel_nl",
            name="NederlandWereldwijd travel advice (Netherlands, Ministerie van Buitenlandse Zaken)",
            domain="travel",
            ttl_seconds=TTL_S,
            timeout_seconds=20.0,
            http_client=http_client,
        )

    async def fetch_feed(self) -> TravelFeed:
        return await self.fetch("feed")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> TravelFeed:
        items: list[Any] = []
        for page in range(NL_MAX_PAGES):
            params = {"output": "json", "rows": str(NL_PAGE_SIZE), "offset": str(page * NL_PAGE_SIZE)}
            resp = await self._get(client, NL_URL, params=params)
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list):
                raise ValueError("NederlandWereldwijd answer is not a list")
            items.extend(batch)
            if len(batch) < NL_PAGE_SIZE:
                break
        return await clock.run_blocking(lambda: parse_nl(items, int(clock.wall())))


# --- United Kingdom ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GbIndexEntry:
    """One page of the FCDO index."""

    page: str
    title: str
    updated_utc: int


@dataclass(frozen=True)
class GbPage:
    """What is kept of one FCDO page (it survives a restart in ``TravelPageStore``)."""

    page: str
    name: str
    statuses: tuple[str, ...]
    published_utc: Optional[int]
    warnings_html: Optional[str]
    warnings_title: Optional[str]
    change_description: Optional[str]
    history: tuple[tuple[Optional[int], str], ...] = ()
    checked_utc: Optional[int] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "GbPage":
        data = json.loads(text)
        return cls(
            page=data["page"],
            name=data["name"],
            statuses=tuple(data.get("statuses") or ()),
            published_utc=data.get("published_utc"),
            warnings_html=data.get("warnings_html"),
            warnings_title=data.get("warnings_title"),
            change_description=data.get("change_description"),
            history=tuple((when, note) for when, note in data.get("history") or ()),
            checked_utc=data.get("checked_utc"),
        )


class TravelPageStore(Protocol):
    """Where the pages of a ministry read one by one are kept between two starts (``app/services/travel_store.py``)."""

    def load_pages(self, ministry: str) -> dict[str, str]: ...

    def save_page(self, ministry: str, page: str, updated_utc: int, payload: str) -> None: ...


def parse_gb_index(payload: Any) -> list[GbIndexEntry]:
    """The pages of the FCDO index (``links.children``) with the date of each one's latest version."""
    links = payload.get("links") if isinstance(payload, dict) else None
    children = links.get("children") if isinstance(links, dict) else None
    if not isinstance(children, list) or not children or len(children) > MAX_ITEMS:
        raise ValueError("FCDO index has no 'links.children' list")
    entries: list[GbIndexEntry] = []
    for child in children:
        details = child.get("details") if isinstance(child, dict) else None
        country = details.get("country") if isinstance(details, dict) else None
        page = _text(country.get("slug")) if isinstance(country, dict) else None
        name = _text(country.get("name")) if isinstance(country, dict) else None
        updated = parse_iso_utc(child.get("public_updated_at"))
        if page is None or name is None or updated is None or not _PAGE_NAME.match(page):
            continue
        entries.append(GbIndexEntry(page, name, updated))
    if not entries:
        raise ValueError("FCDO index: no page could be read")
    return entries


def parse_gb_page(payload: Any, page: str, checked_utc: int) -> GbPage:
    """What is kept of one FCDO page: the alert statuses, the part "Warnings and insurance", the latest change and the history."""
    details = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(details, dict) or not isinstance(details.get("alert_status"), list):
        raise ValueError(f"FCDO page {page}: no 'details.alert_status'")
    country = _obj(details.get("country"))
    name = _text(country.get("name"))
    if name is None:
        raise ValueError(f"FCDO page {page}: no country name")
    statuses = tuple(status for status in details["alert_status"] if isinstance(status, str))
    parts: list[Any] = details["parts"] if isinstance(details.get("parts"), list) else []
    warnings = next((part for part in parts if isinstance(part, dict) and part.get("slug") == GB_WARNINGS_PART), None)
    history = tuple(
        (parse_iso_utc(entry.get("public_timestamp")), note)
        for entry in (details["change_history"] if isinstance(details.get("change_history"), list) else ())
        if isinstance(entry, dict) and (note := _text(entry.get("note")))
    )
    return GbPage(
        page=page,
        name=name,
        statuses=statuses,
        published_utc=parse_iso_utc(payload.get("public_updated_at")),
        warnings_html=_text(warnings.get("body")) if warnings else None,
        warnings_title=_text(warnings.get("title")) if warnings else None,
        change_description=_text(details.get("change_description")),
        history=history,
        checked_utc=checked_utc,
    )


def gb_common(statuses: tuple[str, ...]) -> tuple[Optional[int], Optional[int], Optional[bool]]:
    """The common scale of an FCDO advice from its statuses: ``(common_min, common_max, regional)``.

    Against all travel to the whole country is 4, against all but essential travel to the whole country is 3. Without a status
    for the whole country, the FCDO advises against no travel to it as a whole: 1 or 2, which the common scale cannot split. A
    status for parts says nothing of the rest of the country (the parts can cover all of it, as in Ukraine on 2026-10-28, where
    the two statuses for parts are the whole map): the level for the whole country is then anywhere up to the strictest status
    for parts (3 or 4). A status no table knows makes the whole advice unknown.
    """
    if any(status not in GB_STATUSES for status in statuses):
        return None, None, None
    regional = any(status in (GB_PARTS_AVOID_ALL, GB_PARTS_ESSENTIAL) for status in statuses)
    if GB_WHOLE_AVOID_ALL in statuses:
        return 4, 4, regional
    if GB_WHOLE_ESSENTIAL in statuses:
        return 3, 3, regional
    if GB_PARTS_AVOID_ALL in statuses:
        return 1, 4, regional
    if GB_PARTS_ESSENTIAL in statuses:
        return 1, 3, regional
    return 1, 2, regional


def gb_advices(page: GbPage, checked_utc: Optional[int]) -> list[TravelAdvice]:
    """The advice of one FCDO page, once for each country it covers (``Cook Islands, Tokelau and Niue``)."""
    common_min, common_max, regional = gb_common(page.statuses)
    credit = credit_text("travel_gb")
    summary = (
        [TravelText(language="en", format="html", title=page.warnings_title, content=page.warnings_html)] if page.warnings_html else []
    )
    notes = [TravelText(language="en", format="text", content=page.change_description)] if page.change_description else []
    history = [TravelSourceChange(published_utc=when, note=note) for when, note in page.history]
    return [
        TravelAdvice(
            ministry="gb",
            country=country,
            source_code=page.page,
            title=page.name,
            statuses=list(page.statuses),
            regional=regional,
            common_min=common_min,
            common_max=common_max,
            published_utc=page.published_utc,
            checked_utc=checked_utc,
            url=GB_WEB_URL.format(page=page.page),
            summary=summary,
            change_note=notes,
            source_history=history,
            derived=_derived(common=common_min is not None),
            attribution=credit,
        )
        for country in codes.gb_countries(page.page)
    ]


@dataclass
class _GbState:
    pages: dict[str, GbPage] = field(default_factory=dict)
    entries: list[GbIndexEntry] = field(default_factory=list)  # the last index read (a ``304`` reuses it)
    loaded: bool = False


class FCDOTravelCollector(BaseCollector[TravelFeed]):
    """The travel advice of the FCDO (GOV.UK): the index every hour, and the pages it says have changed, one after the other."""

    def __init__(
        self,
        store: Optional[TravelPageStore] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        page_spacing_s: float = GB_PAGE_SPACING_S,
    ) -> None:
        super().__init__(
            provider_id="travel_gb",
            name="FCDO foreign travel advice (GOV.UK Content API)",
            domain="travel",
            ttl_seconds=TTL_S,
            timeout_seconds=20.0,
            http_client=http_client,
        )
        self.store = store
        self.page_spacing_s = page_spacing_s
        self._state = _GbState()
        self.pages_read = 0  # pages read since the start (the 226 once, then the changed ones)

    async def fetch_feed(self) -> TravelFeed:
        return await self.fetch("feed")

    def _load(self) -> None:
        """The pages kept by an earlier run (once per start)."""
        if self._state.loaded:
            return
        self._state.loaded = True
        if self.store is None:
            return
        for page, text in self.store.load_pages("gb").items():
            try:
                self._state.pages[page] = GbPage.from_json(text)
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("FCDO page %s kept in the database cannot be read: %s", page, exc)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[TravelFeed]:
        self._load()
        previous: Optional[CacheEntry[TravelFeed]] = kwargs.get("previous")
        resp = await self._get(client, GB_INDEX_URL, headers=_conditional(previous) if self._state.entries else {})
        if resp.status_code == 304 and self._state.entries:
            entries = self._state.entries  # the index did not change: only the pages still pending are read again
        else:
            resp.raise_for_status()
            entries = await clock.run_blocking(lambda: parse_gb_index(resp.json()))
            self._state.entries = entries
        validators = _validators(resp) if resp.status_code != 304 else (previous.validators if previous else None)
        now = int(clock.wall())
        pending: list[str] = []
        first = True
        for entry in entries:
            kept = self._state.pages.get(entry.page)
            if kept is not None and kept.published_utc == entry.updated_utc:
                continue
            if not first and self.page_spacing_s > 0:
                await asyncio.sleep(self.page_spacing_s)
            first = False
            try:
                page = await self._read_page(client, entry.page)
            except Exception as exc:  # one page that fails keeps its kept version and is said to be pending
                log.warning("FCDO page %s could not be read: %s", entry.page, exc)
                pending.append(entry.title)
                continue
            self._state.pages[entry.page] = page
            if self.store is not None:
                self.store.save_page("gb", entry.page, entry.updated_utc, page.to_json())
            if page.published_utc != entry.updated_utc:  # the page served is older than the index says: asked again next time
                pending.append(entry.title)
        listed = {entry.page for entry in entries}
        advices: list[TravelAdvice] = []
        unplaced: list[str] = []
        for entry in entries:
            page = self._state.pages.get(entry.page)
            if page is None:
                continue
            confirmed = now if page.published_utc == entry.updated_utc else page.checked_utc
            placed = gb_advices(page, confirmed)
            if not placed:
                unplaced.append(page.name)
            advices.extend(placed)
        for gone in set(self._state.pages) - listed:  # a page the index no longer lists is no longer an advice
            del self._state.pages[gone]
        if not advices and not unplaced:
            raise ValueError(f"FCDO: none of the {len(entries)} pages could be read")
        return Collected(TravelFeed("gb", tuple(advices), tuple(unplaced), 0, tuple(pending)), validators=validators)

    def kept_feed(self) -> Optional[TravelFeed]:
        """The pages kept by an earlier run, each with the date it was last confirmed, for when the index cannot be read at
        all (``None`` when nothing is kept). Not a read of the FCDO: the caller says the advice was not confirmed now."""
        self._load()
        advices = [advice for page in self._state.pages.values() for advice in gb_advices(page, page.checked_utc)]
        return TravelFeed("gb", tuple(advices)) if advices else None

    async def _read_page(self, client: httpx.AsyncClient, page: str) -> GbPage:
        resp = await self._get(client, GB_PAGE_URL.format(page=page))
        resp.raise_for_status()
        self.pages_read += 1
        return await clock.run_blocking(lambda: parse_gb_page(resp.json(), page, int(clock.wall())))


# --- shared ------------------------------------------------------------------------------------------------------------


def _conditional(previous: Optional[CacheEntry[Any]]) -> dict[str, str]:
    headers: dict[str, str] = {}
    if previous is not None and previous.validators:
        if previous.validators.get("ETag"):
            headers["If-None-Match"] = previous.validators["ETag"]
        if previous.validators.get("Last-Modified"):
            headers["If-Modified-Since"] = previous.validators["Last-Modified"]
    return headers


def _validators(resp: httpx.Response) -> Optional[dict[str, str]]:
    found = {name: resp.headers[name] for name in ("ETag", "Last-Modified") if resp.headers.get(name)}
    return found or None


def _checked(feed: Any, now: int) -> Any:
    """The same feed, confirmed by the ministry at ``now`` (a ``304``)."""
    if not isinstance(feed, TravelFeed):
        return feed
    return TravelFeed(
        feed.ministry,
        tuple(advice.model_copy(update={"checked_utc": now}) for advice in feed.advices),
        feed.unplaced,
        feed.skipped,
        feed.pending,
    )


TRAVEL_COLLECTORS: dict[str, Callable[..., BaseCollector[Any]]] = {
    "gb": FCDOTravelCollector,
    "us": USTravelCollector,
    "ca": CanadaTravelCollector,
    "au": SmartravellerCollector,
    "nl": NetherlandsTravelCollector,
}
