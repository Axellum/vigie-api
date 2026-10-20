"""The official warnings of the national services for a point or a country, and the changes to push.

``VigilanceService`` joins the sources (MeteoAlarm feeds per country, the national services that have their own
collector, the NWS answer the hazards already use), finds the country of a point **offline** (``geoindex``), keeps the
warnings that cover the point, and adds the words of the issuer (the CAP documents) to the ones it returns. It asks the
providers as little as it can:

- A feed is asked only for the countries that are needed: the one(s) of the point or the country asked, and, for the
  scheduler, those of the watched zones, of the points a client follows, and of the countries asked for recently (at most
  ``RECENT_COUNTRIES_MAX`` of them, forgotten after ``RECENT_COUNTRIES_TTL_S``).
- **One source per country, the national one first.** Germany (DWD), Canada (ECCC) and New Zealand (MetService) have a
  collector of the national service: for such a country the national source is asked and MeteoAlarm's copies of the
  same warnings are not (so nothing is returned twice, and a request is saved). MeteoAlarm is asked for Germany only
  when the DWD gave no answer at all. Countries with no national collector keep MeteoAlarm. France has MeteoAlarm and
  Vigicrues (river floods, a different thing at a finer scale): see ``_without_covered_flooding``.
- A CAP document is asked only for a warning that an answer returns, once (it never changes), and at most
  ``CAP_DOCUMENTS_PER_REQUEST`` new ones per request: the others come with the next request.
- The NWS answer is the hazards' own: no request of its own.

An answer says what it could not do (``coverage``): a country whose warnings name their areas with codes that
have no shape here is not "without warnings", it is "not matched to a point".
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from app import clock
from app.attribution import METEOALARM_DISCLAIMER, credit_text, merge_credits
from app.collectors.base import CollectorUnavailableError
from app.collectors.disasters import NOAANWSCollector
from app.collectors.dwd import DWDCollector
from app.collectors.eccc import ECCCCollector
from app.collectors.meteoalarm import (
    COUNTRY_FEEDS,
    CapDocument,
    MeteoAlarmCapCollector,
    MeteoAlarmCollector,
    MeteoAlarmFeed,
    with_cap,
)
from app.collectors.metservice import MetServiceCollector
from app.collectors.vigicrues import VigicruesCollector
from app.geo import shape_to_geojson
from app.models.schemas import SEVERITY_RANK, SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceChange,
    VigilanceCoverage,
    VigilanceGeoJSONCollection,
    VigilanceGeoJSONFeature,
    VigilanceQuery,
    VigilancesResponse,
    VigilanceStatus,
    VigilanceText,
)
from app.services.geoindex import (
    DEFAULT_RIVER_RADIUS_KM,
    CountryIndex,
    GeometryIndex,
    PointLocator,
    geometry_of,
    localization_of,
)

RECENT_COUNTRIES_MAX = 8
RECENT_COUNTRIES_TTL_S = 3600
CAP_DOCUMENTS_PER_REQUEST = 40
CAP_CONCURRENCY = 4
FOLLOW_DECIMALS = 4  # a followed position is rounded to about 11 m

NWS_COUNTRY = "US"
FRANCE = "FR"
# The countries that have a national collector: the source is preferred to MeteoAlarm for them (Canada and New Zealand
# are not in MeteoAlarm at all).
NATIONAL_SOURCES: dict[str, str] = {"DE": "dwd", "CA": "eccc", "NZ": "metservice"}
SUPPORTED_COUNTRIES = sorted({*COUNTRY_FEEDS, NWS_COUNTRY, *NATIONAL_SOURCES})
# The sources whose warnings carry their words inline (no CAP document to fetch afterwards).
INLINE_TEXT_SOURCES = ("dwd", "eccc", "metservice")
# Source -> key of its credit in ``app/attribution.py``, in the order the credits are printed.
CREDIT_KEYS: dict[str, str] = {
    "meteoalarm": "meteoalarm",
    "dwd": "dwd_warnings",
    "eccc": "eccc_alerts",
    "metservice": "metservice_alerts",
    "vigicrues": "vigicrues",
    "noaa_nws": "noaa_nws",
}
FLOODING_WORD = "flooding"  # MeteoAlarm awareness type 12 (CAP profile v2.0): Météo-France's "inondation" (river floods)

NO_SOURCE_NOTE = (
    "No official warning source covers this point (MeteoAlarm: 39 European countries; DWD: Germany; ECCC: Canada; "
    "MetService: New Zealand; NWS: the United States)."
)
METSERVICE_OFF_NOTE = (
    "MetService (New Zealand) is turned off on this instance (VIGIE_ENABLE_METSERVICE=0): its terms of use are not settled "
    "for an instance that relays the warnings to others (SOURCES.md), so no source answers for this point."
)
ECCC_NOTE = (
    "Read from MSC GeoMet (the Datamart CAP files cannot be polled, see docs/data-sources.md): the CAP instruction and "
    "sender are not in that form, so `instruction` and `issuer` are empty."
)
NWS_NOTE = (
    "Only the Severe and Extreme alerts are read (the request the hazards already use). An alert that names "
    "zones (UGC codes) instead of a polygon is not matched to a point; ask by country to list it."
)


@dataclass(frozen=True)
class FollowedPoint:
    """A position the scheduler watches for changes: a watched zone (everyone is told) or a point a client follows."""

    key: str
    lat: float
    lon: float
    label: dict[str, Any]
    audience: str = "all"  # "all" (broadcast) or "followers" (the clients that follow this point)


@dataclass
class SourceData:
    """What one source gave for one country, and what the API knows of it."""

    source: str
    country: str
    vigilances: list[Vigilance]
    coverage: VigilanceCoverage
    note: Optional[str] = None  # what is worth saying about this source for this country, in every answer
    fallback: bool = False  # asked only because the national service did not answer: in the answer, not in the push tracking


def level_rank(level: Optional[SeverityLevel]) -> int:
    """Rank of a level; -1 for an unknown one (it is below every known level)."""
    return -1 if level is None else SEVERITY_RANK[level]


def is_active(vigilance: Vigilance, now_utc: float) -> bool:
    """A warning that is not cancelled and whose end (the later of ``ends`` and ``expires``) is not past."""
    if vigilance.status == VigilanceStatus.CANCEL:
        return False
    end = max((t for t in (vigilance.ends_utc, vigilance.expires_utc) if t is not None), default=None)
    return end is None or end >= now_utc


def track_key(vigilance: Vigilance) -> str:
    """What identifies "the same warning" from one update to the next: source, phenomenon and area.

    An update of a MeteoAlarm warning is a new document with a new identifier, so the identifier cannot follow a
    warning in time; its source, its phenomenon and its area can.
    """
    codes = ",".join(sorted(f"{code.scheme}:{code.value}" for code in vigilance.area.geocodes))
    if not codes:
        basis = vigilance.shape.wkb_hex[:4096] if vigilance.shape is not None else (vigilance.area.description or vigilance.id)
        codes = "area:" + hashlib.sha1(basis.encode()).hexdigest()[:12]
    what = vigilance.phenomenon.value if vigilance.phenomenon else (vigilance.phenomenon_original or "?")
    return f"{vigilance.source}|{vigilance.country}|{what}|{codes}"


def parse_languages(value: Optional[str]) -> Optional[tuple[str, ...]]:
    """``fr,en`` -> ``("fr", "en")``: the language tags to keep (a prefix of the tag of each text); ``None`` keeps all."""
    if not value:
        return None
    tags = tuple(tag.strip().lower() for tag in value.split(",") if tag.strip())
    return tags or None


def keep_the_strongest(vigilances: list[Vigilance]) -> dict[str, Vigilance]:
    """One warning per ``track_key``: the highest level (then the earliest onset) when several overlap."""
    kept: dict[str, Vigilance] = {}
    for vigilance in vigilances:
        key = track_key(vigilance)
        current = kept.get(key)
        if current is None or (level_rank(vigilance.level), -(vigilance.onset_utc or 0)) > (
            level_rank(current.level),
            -(current.onset_utc or 0),
        ):
            kept[key] = vigilance
    return kept


class VigilanceService:
    """The warnings for a point or a country, and the changes to push."""

    def __init__(
        self,
        meteoalarm: MeteoAlarmCollector,
        meteoalarm_cap: MeteoAlarmCapCollector,
        noaa_nws: NOAANWSCollector,
        countries: CountryIndex,
        geometries: GeometryIndex,
        watched_zones: Callable[[], list[dict[str, Any]]],
        *,
        dwd: Optional[DWDCollector] = None,
        eccc: Optional[ECCCCollector] = None,
        metservice: Optional[MetServiceCollector] = None,
        vigicrues: Optional[VigicruesCollector] = None,
    ) -> None:
        self.meteoalarm = meteoalarm
        self.meteoalarm_cap = meteoalarm_cap
        self.noaa_nws = noaa_nws
        self.dwd = dwd
        self.eccc = eccc
        self.metservice = metservice
        self.vigicrues = vigicrues
        self.countries = countries
        self.geometries = geometries
        self._watched_zones = watched_zones
        self._recent: OrderedDict[str, float] = OrderedDict()
        self._tracked: dict[str, dict[str, Vigilance]] = {}
        self._known: dict[str, set[str]] = {}  # per followed point, the sources that have answered at least once

    # ------------------------------------------------------------------ the countries that are needed

    def remember(self, country: str) -> None:
        """Note that a country was asked for: the scheduler keeps its feed fresh for a while."""
        self._recent.pop(country, None)
        self._recent[country] = clock.monotonic()
        while len(self._recent) > RECENT_COUNTRIES_MAX:
            self._recent.popitem(last=False)

    def recent_countries(self) -> list[str]:
        """The countries asked for within the last ``RECENT_COUNTRIES_TTL_S`` seconds."""
        now = clock.monotonic()
        for country in [c for c, asked in self._recent.items() if now - asked > RECENT_COUNTRIES_TTL_S]:
            del self._recent[country]
        return list(self._recent)

    def _national(self, country: str) -> Optional[str]:
        """The national source asked for a country (``dwd``...), or ``None`` when MeteoAlarm is the source (or none is wired)."""
        source = NATIONAL_SOURCES.get(country)
        return source if source is not None and getattr(self, source) is not None else None

    def _plan(self, country: str) -> list[str]:
        """The sources asked for a country, in order. The NWS is not here: it costs no request of its own."""
        national = self._national(country)
        if national is not None:
            return [national]
        plan = ["meteoalarm"] if country in COUNTRY_FEEDS else []
        if country == FRANCE and self.vigicrues is not None:
            plan.append("vigicrues")
        return plan

    def needed_countries(self, extra_points: Optional[list[tuple[float, float]]] = None) -> list[str]:
        """The countries worth keeping fresh (those with a feed of their own): watched zones, followed points, recent requests."""
        needed: set[str] = set()
        points = [(float(z["lat"]), float(z["lon"])) for z in self._watched_zones()] + list(extra_points or [])
        for lat, lon in points:
            needed.update(self.countries.countries_for_point(lat, lon))
        needed.update(self.recent_countries())
        return sorted(country for country in needed if self._plan(country))

    # ------------------------------------------------------------------ the sources

    async def _meteoalarm(self, country: str) -> SourceData:
        key = f"atom:{country}"
        try:
            fetched = await self.meteoalarm.fetch_country(country)
        except (CollectorUnavailableError, ValueError) as exc:
            return SourceData(
                "meteoalarm",
                country,
                [],
                VigilanceCoverage(source="meteoalarm", country=country, status="unavailable", note=f"The provider gave no answer: {exc}"),
            )
        feed: MeteoAlarmFeed = fetched.value
        now = clock.wall()
        vigilances = [v for v in feed.vigilances if is_active(v, now)]
        return SourceData(
            "meteoalarm",
            country,
            vigilances,
            VigilanceCoverage(
                source="meteoalarm",
                country=country,
                status="stale" if fetched.stale else "ok",
                feed_updated_utc=feed.updated_utc,
                fetched_age_s=self._age(self.meteoalarm, key),
            ),
        )

    async def _nws(self) -> SourceData:
        try:
            fetched = await self.noaa_nws.fetch_vigilances()
        except (CollectorUnavailableError, ValueError) as exc:
            return self._unavailable("noaa_nws", NWS_COUNTRY, exc)
        now = clock.wall()
        vigilances = [v for v in fetched.value if is_active(v, now)]
        return SourceData(
            "noaa_nws",
            NWS_COUNTRY,
            vigilances,
            VigilanceCoverage(
                source="noaa_nws",
                country=NWS_COUNTRY,
                status="stale" if fetched.stale else "ok",
                fetched_age_s=self._age(self.noaa_nws, "global"),
            ),
        )

    @staticmethod
    def _unavailable(source: str, country: str, exc: Exception) -> SourceData:
        return SourceData(
            source,
            country,
            [],
            VigilanceCoverage(source=source, country=country, status="unavailable", note=f"The provider gave no answer: {exc}"),
        )

    def _national_data(
        self,
        source: str,
        country: str,
        collector: Any,
        key: str,
        fetched: Any,
        vigilances: list[Vigilance],
        updated_utc: Optional[int] = None,
        note: Optional[str] = None,
    ) -> SourceData:
        now = clock.wall()
        active = [v for v in vigilances if is_active(v, now)]
        return SourceData(
            source,
            country,
            active,
            VigilanceCoverage(
                source=source,
                country=country,
                status="stale" if fetched.stale else "ok",
                feed_updated_utc=updated_utc,
                fetched_age_s=self._age(collector, key),
            ),
            note=note,
        )

    async def _dwd(self) -> SourceData:
        assert self.dwd is not None
        try:
            fetched = await self.dwd.fetch_status()
        except (CollectorUnavailableError, ValueError) as exc:
            return self._unavailable("dwd", "DE", exc)
        status = fetched.value
        return self._national_data("dwd", "DE", self.dwd, "status", fetched, list(status.vigilances), status.updated_utc)

    async def _eccc(self) -> SourceData:
        assert self.eccc is not None
        try:
            fetched = await self.eccc.fetch_alerts()
        except (CollectorUnavailableError, ValueError) as exc:
            return self._unavailable("eccc", "CA", exc)
        return self._national_data("eccc", "CA", self.eccc, "alerts", fetched, list(fetched.value.vigilances), note=ECCC_NOTE)

    async def _metservice(self) -> SourceData:
        assert self.metservice is not None
        try:
            fetched = await self.metservice.fetch_feed()
        except (CollectorUnavailableError, ValueError) as exc:
            return self._unavailable("metservice", "NZ", exc)
        feed = fetched.value
        note = (
            f"{feed.unreadable} of the {feed.items} items of the feed could not be read as a CAP alert (an address that is not "
            "MetService's, or a document the server no longer has): they are not in this answer."
            if feed.unreadable
            else None
        )
        return self._national_data("metservice", "NZ", self.metservice, "feed", fetched, list(feed.vigilances), note=note)

    async def _vigicrues(self) -> SourceData:
        assert self.vigicrues is not None
        try:
            fetched = await self.vigicrues.fetch_flow()
        except (CollectorUnavailableError, ValueError) as exc:
            return self._unavailable("vigicrues", FRANCE, exc)
        flow = fetched.value
        note = (
            f"{len(flow.vigilances)} of the {flow.sections} river sections are above green; a position gets those within "
            "its river radius (`river_radius_km`), with their distance."
        )
        if flow.skipped:
            note += f" {flow.skipped} sections above green could not be read."
        return self._national_data("vigicrues", FRANCE, self.vigicrues, "flow", fetched, list(flow.vigilances), flow.updated_utc, note)

    @staticmethod
    def _age(collector: Any, key: str) -> Optional[int]:
        entry = collector._cache.get(key)
        return None if entry is None else max(0, int(clock.monotonic() - entry.stored_at))

    async def _country_sources(self, country: str) -> list[SourceData]:
        """The sources of one country: the national one first; MeteoAlarm only if there is none, or it gave no answer."""
        if country == NWS_COUNTRY:
            return [await self._nws()]
        national = self._national(country)
        if national is not None:
            data = await getattr(self, f"_{national}")()
            sources = [data]
            if data.coverage.status == "unavailable" and country in COUNTRY_FEEDS:
                fallback = await self._meteoalarm(country)
                fallback.fallback = True
                fallback.note = (
                    "Used because the national service gave no answer; its copies of the warnings may not be matched to a point."
                )
                sources.append(fallback)
            return sources
        sources = []
        if country in COUNTRY_FEEDS:
            sources.append(await self._meteoalarm(country))
        if country == FRANCE and self.vigicrues is not None:
            sources.append(await self._vigicrues())
        return sources

    async def _sources(self, countries: list[str]) -> list[SourceData]:
        groups = await asyncio.gather(*(self._country_sources(country) for country in countries))
        return [data for group in groups for data in group]

    # ------------------------------------------------------------------ localization and the words of the issuer

    def _localized(self, vigilance: Vigilance, locator: Optional[PointLocator] = None) -> Vigilance:
        """The warning with how a point can be matched to its area on this instance (and, for a river section, how far it is)."""
        localization = localization_of(vigilance, self.geometries)
        update: dict[str, Any] = {"area": vigilance.area.model_copy(update={"localization": localization})}
        if locator is not None:
            update["distance_km"] = locator.distance_km(vigilance)
        return vigilance.model_copy(update=update)

    def _finish_coverage(self, data: SourceData) -> None:
        """Count the warnings of the feed and the ones whose area has no shape here."""
        not_matchable = [v for v in data.vigilances if localization_of(v, self.geometries) == "none"]
        data.coverage.warnings_in_feed = len(data.vigilances)
        data.coverage.warnings_not_matchable = len(not_matchable)
        schemes = sorted({code.scheme for v in not_matchable for code in v.area.geocodes})
        notes: list[str] = []
        if not_matchable:
            what = " and ".join(schemes) if schemes else "their area"
            notes.append(
                f"{len(not_matchable)} of {len(data.vigilances)} warnings name their area with {what} codes whose shape is not "
                "available on this instance: they cannot be matched to a point (ask by country to list them)."
            )
        if data.source == "noaa_nws":
            notes.append(NWS_NOTE)
        if data.note:
            notes.append(data.note)
        if notes:
            data.coverage.note = " ".join(notes)

    async def _with_texts(self, vigilances: list[Vigilance], languages: Optional[tuple[str, ...]]) -> tuple[list[Vigilance], int]:
        """Add the words of the issuer to MeteoAlarm warnings; returns them and how many are still without.

        The CAP documents already held cost nothing. Of the others, the first ``CAP_DOCUMENTS_PER_REQUEST`` are
        asked (the strongest warnings first, the list is sorted); the rest wait for the next request.
        """
        cap = self.meteoalarm_cap
        urls = list(dict.fromkeys(v.link for v in vigilances if v.source == "meteoalarm" and v.link))
        held = {url for url in urls if cap._get_cached_if_fresh(url) is not None}
        missing = [url for url in urls if url not in held][:CAP_DOCUMENTS_PER_REQUEST]
        semaphore = asyncio.Semaphore(CAP_CONCURRENCY)

        async def load(url: str) -> tuple[str, Optional[CapDocument]]:
            async with semaphore:
                try:
                    return url, (await cap.fetch_document(url)).value
                except (CollectorUnavailableError, ValueError):
                    return url, None

        documents = dict(await asyncio.gather(*(load(url) for url in [*held, *missing])))
        enriched: list[Vigilance] = []
        without = 0
        for vigilance in vigilances:
            if vigilance.source in INLINE_TEXT_SOURCES:
                enriched.append(self._in_languages(vigilance, languages))
                continue
            if vigilance.source != "meteoalarm" or not vigilance.link:
                enriched.append(vigilance)
                continue
            document = documents.get(vigilance.link)
            if document is None or not document.available:
                without += 1
                enriched.append(vigilance)
            else:
                enriched.append(with_cap(vigilance, document, languages))
        return enriched, without

    @staticmethod
    def _in_languages(vigilance: Vigilance, languages: Optional[tuple[str, ...]]) -> Vigilance:
        """The warning with only the texts in the languages asked for (a prefix of the language tag); all by default."""
        if not languages:
            return vigilance
        texts: list[VigilanceText] = [t for t in vigilance.texts if (t.language or "").lower().startswith(languages)]
        return vigilance.model_copy(update={"texts": texts})

    def _forget_unlinked_documents(self) -> None:
        """Drop the CAP documents that no cached feed links to any more (a warning ended)."""
        linked = {
            v.link
            for entry in self.meteoalarm._cache.values()
            for v in entry.value.vigilances
            if v.link
        }
        self.meteoalarm_cap.prune(linked)

    # ------------------------------------------------------------------ the answers

    def _response(
        self,
        query: VigilanceQuery,
        countries: list[str],
        sources: list[SourceData],
        vigilances: list[Vigilance],
        without_texts: int,
    ) -> VigilancesResponse:
        coverage = [data.coverage for data in sources]
        used = {data.source for data in sources}
        answered = {data.source for data in sources if data.coverage.status != "unavailable"}  # a source that said nothing is not credited
        if without_texts:
            for item in coverage:
                if item.source == "meteoalarm":
                    extra = (
                        f"{without_texts} of the warnings returned have no text yet (the CAP document is not available, "
                        f"or at most {CAP_DOCUMENTS_PER_REQUEST} new ones are fetched per request)."
                    )
                    item.note = f"{item.note} {extra}" if item.note else extra
                    break
        note = None
        if not sources:
            note = METSERVICE_OFF_NOTE if "NZ" in countries and self.metservice is None else NO_SOURCE_NOTE
        return VigilancesResponse(
            server_utc=int(clock.wall()),
            query=query,
            countries=countries,
            vigilances=vigilances,
            coverage=coverage,
            attribution=merge_credits(*(credit_text(credit) for source, credit in CREDIT_KEYS.items() if source in answered)),
            disclaimer=METEOALARM_DISCLAIMER if "meteoalarm" in used else None,
            note=note,
        )

    @staticmethod
    def _sorted(vigilances: list[Vigilance]) -> list[Vigilance]:
        return sorted(vigilances, key=lambda v: (-level_rank(v.level), v.onset_utc or 0, v.id))

    def _without_covered_flooding(self, found: list[Vigilance]) -> list[Vigilance]:
        """France: the MeteoAlarm "flooding" warnings that a Vigicrues river section in the same answer already says, finer.

        MeteoAlarm's French warnings of type "flooding" (awareness type 12) are Météo-France's "inondation" vigilance, one
        colour per département, which is built from the Vigicrues sections. The same flood is then in the answer twice, as a
        département and as a river section. The rule: a "flooding" warning is left out when a Vigicrues section of the answer
        crosses its département **at the same level or higher**; when no section does (the département is warned but no
        section within the radius, or only a lower one), it stays, because it says something the sections do not. "Rain-flood"
        (pluie-inondation) is another vigilance and always stays. Without a Vigicrues answer nothing is left out.
        """
        rivers = [v for v in found if v.source == "vigicrues" and v.shape is not None]
        if not rivers:
            return found
        kept: list[Vigilance] = []
        for vigilance in found:
            if vigilance.source == "meteoalarm" and vigilance.country == FRANCE and (vigilance.phenomenon_original or "").lower() == FLOODING_WORD:
                department, _ = geometry_of(vigilance, self.geometries)
                if department is not None and any(
                    level_rank(river.level) >= level_rank(vigilance.level) and river.shape.intersects(department)  # type: ignore[union-attr]
                    for river in rivers
                ):
                    continue
            kept.append(vigilance)
        return kept

    async def for_point(
        self,
        lat: float,
        lon: float,
        languages: Optional[tuple[str, ...]] = None,
        river_radius_km: float = DEFAULT_RIVER_RADIUS_KM,
    ) -> VigilancesResponse:
        """The warnings that cover the exact position (a polygon of the source, a code whose shape is known, or a river section within ``river_radius_km``)."""
        countries = [c for c in self.countries.countries_for_point(lat, lon) if c in SUPPORTED_COUNTRIES]
        for country in countries:
            self.remember(country)
        sources = await self._sources(countries)
        locator = PointLocator(lat, lon, self.geometries, river_radius_km)
        found: list[Vigilance] = []
        for data in sources:
            self._finish_coverage(data)
            found.extend(self._localized(v, locator) for v in data.vigilances if locator.covering(v))
        ordered = self._sorted(self._without_covered_flooding(found))
        enriched, without = await self._with_texts(ordered, languages)
        self._forget_unlinked_documents()
        query = VigilanceQuery(lat=lat, lon=lon, river_radius_km=river_radius_km)
        return self._response(query, countries, sources, enriched, without)

    async def for_country(self, country: str, languages: Optional[tuple[str, ...]] = None) -> VigilancesResponse:
        """Every warning of a country, whether or not its area can be matched to a point."""
        country = country.upper()
        if country not in SUPPORTED_COUNTRIES:
            raise KeyError(country)
        self.remember(country)
        sources = await self._sources([country])
        found: list[Vigilance] = []
        for data in sources:
            self._finish_coverage(data)
            found.extend(self._localized(v) for v in data.vigilances)
        ordered = self._sorted(self._without_covered_flooding(found))
        enriched, without = await self._with_texts(ordered, languages)
        self._forget_unlinked_documents()
        return self._response(VigilanceQuery(country=country), [country], sources, enriched, without)

    def to_geojson(self, response: VigilancesResponse) -> VigilanceGeoJSONCollection:
        """The same answer as a GeoJSON FeatureCollection (RFC 7946); the geometry is null where the area has no shape here."""
        features: list[VigilanceGeoJSONFeature] = []
        for vigilance in response.vigilances:
            geometry, origin = geometry_of(vigilance, self.geometries)
            properties = vigilance.model_dump(mode="json", exclude={"id"})
            properties["geometry_source"] = origin
            features.append(
                VigilanceGeoJSONFeature(
                    id=vigilance.id,
                    geometry=None if geometry is None else shape_to_geojson(geometry),
                    properties=properties,
                )
            )
        return VigilanceGeoJSONCollection(
            metadata={
                "query": response.query.model_dump(),
                "countries": response.countries,
                "coverage": [item.model_dump() for item in response.coverage],
                "attribution": response.attribution,
                "disclaimer": response.disclaimer,
                "note": response.note,
                "server_utc": response.server_utc,
            },
            features=features,
        )

    # ------------------------------------------------------------------ the changes to push

    async def _current(self, lat: float, lon: float) -> tuple[dict[str, Vigilance], set[str], set[str], set[str]]:
        """The warnings covering a position by ``track_key`` (without the issuer's words: a cheap, cached read).

        With them, the sources that answered and those that did not (the picture is incomplete for the latter only), and
        the keys of the warnings still in force that a river section says better (see ``_without_covered_flooding``).
        The copies a fallback source gives of a national service's warnings are left out (they are in the answer, not in
        the pushes).
        """
        countries = [c for c in self.countries.countries_for_point(lat, lon) if c in SUPPORTED_COUNTRIES]
        sources = [data for data in await self._sources(countries) if not data.fallback]
        locator = PointLocator(lat, lon, self.geometries)
        found = [v for data in sources for v in data.vigilances if locator.covering(v)]
        shown = keep_the_strongest(self._without_covered_flooding(found))
        covered = set(keep_the_strongest(found)) - set(shown)
        silent = {data.source for data in sources if data.coverage.status == "unavailable"}
        return shown, {data.source for data in sources} - silent, silent, covered

    async def refresh(self, points: list[FollowedPoint]) -> None:
        """Keep the feeds of the needed countries fresh (a cached feed costs nothing): called by the scheduler."""
        extra = [(p.lat, p.lon) for p in points]
        countries = self.needed_countries(extra)
        fetches = [
            getattr(self, f"_{source}")(*([country] if source == "meteoalarm" else []))
            for country in countries
            for source in self._plan(country)
        ]
        await asyncio.gather(*fetches, return_exceptions=True)

    async def poll_changes(self, points: list[FollowedPoint]) -> list[tuple[FollowedPoint, VigilanceChange]]:
        """Compare, for each followed point, the warnings now with the ones the last call saw.

        A warning that appears, whose level goes up, or that ends (expires, is cancelled or is no longer listed)
        is a change. The first look at a point only sets the baseline: nothing is pushed for what was already there.
        An unreadable source never ends a warning: what it had said of the point is kept as it was, while the other
        sources of the point go on being followed (France: Vigicrues down, MeteoAlarm still pushes). A source that
        answers for the first time after the baseline (it was down then) sets its own baseline: what it already
        had in force is not pushed as new.
        """
        changes: list[tuple[FollowedPoint, VigilanceChange]] = []
        seen = {point.key for point in points}
        for key in [k for k in self._tracked if k not in seen]:
            del self._tracked[key]
            self._known.pop(key, None)
        for point in points:
            try:
                current, answered, silent, covered = await self._current(point.lat, point.lon)
            except Exception:
                continue
            previous = self._tracked.get(point.key)
            known = self._known.get(point.key, set())
            if previous is not None and silent:
                # What a silent source said at the last look still stands, and still covers the flooding warnings it covered.
                merged = {**{key: v for key, v in previous.items() if v.source in silent}, **current}
                kept = {v.id for v in self._without_covered_flooding(list(merged.values()))}
                current = {key: v for key, v in merged.items() if v.id in kept}
            self._tracked[point.key] = current
            self._known[point.key] = known | answered
            if previous is None:
                continue
            now = int(clock.wall())
            for key, vigilance in current.items():
                before = previous.get(key)
                if before is None:
                    if vigilance.source not in known:
                        continue  # the first answer of that source: its baseline
                    changes.append((point, self._change(point, "appeared", vigilance, None, now)))
                elif level_rank(vigilance.level) > level_rank(before.level) >= 0:
                    changes.append((point, self._change(point, "upgraded", vigilance, before.level, now)))
            for key, vigilance in previous.items():
                if key not in current and key not in covered:  # a warning a river section now says better has not ended
                    changes.append((point, self._change(point, "ended", vigilance, None, now)))
        return await self._with_texts_for_changes(changes)

    @staticmethod
    def _change(
        point: FollowedPoint, kind: str, vigilance: Vigilance, previous_level: Optional[SeverityLevel], now: int
    ) -> VigilanceChange:
        label = {"lat": point.lat, "lon": point.lon, **point.label}
        return VigilanceChange(
            server_utc=now,
            change=kind,  # type: ignore[arg-type]
            point=label,
            vigilance=vigilance,
            previous_level=previous_level,
            attribution=vigilance.attribution,
            disclaimer=METEOALARM_DISCLAIMER if vigilance.source == "meteoalarm" else None,
        )

    async def _with_texts_for_changes(
        self, changes: list[tuple[FollowedPoint, VigilanceChange]]
    ) -> list[tuple[FollowedPoint, VigilanceChange]]:
        """Add the issuer's words to the warnings of the changes only (an ended warning keeps what it had)."""
        wanted = [change.vigilance for _, change in changes if change.change != "ended"]
        if not wanted:
            return changes
        enriched, _ = await self._with_texts(wanted, None)
        by_id = {v.id: v for v in enriched}
        out: list[tuple[FollowedPoint, VigilanceChange]] = []
        locators: dict[str, PointLocator] = {}
        for point, change in changes:
            locator = locators.setdefault(point.key, PointLocator(point.lat, point.lon, self.geometries))
            replacement = by_id.get(change.vigilance.id)
            if replacement is not None and change.change != "ended":
                change = change.model_copy(update={"vigilance": self._localized(replacement, locator), "attribution": replacement.attribution})
            else:
                change = change.model_copy(update={"vigilance": self._localized(change.vigilance, locator)})
            out.append((point, change))
        return out
