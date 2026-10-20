"""The official warnings of the national services for a point or a country, and the changes to push.

``VigilanceService`` joins the sources (MeteoAlarm feeds per country, the NWS answer the hazards already use),
finds the country of a point **offline** (``geoindex``), keeps the warnings that cover the point, and adds the
words of the issuer (the CAP documents) to the ones it returns. It asks the providers as little as it can:

- A MeteoAlarm feed is asked only for the countries that are needed: the one(s) of the point or the country
  asked, and, for the scheduler, those of the watched zones, of the points a client follows, and of the
  countries asked for recently (at most ``RECENT_COUNTRIES_MAX`` of them, forgotten after ``RECENT_COUNTRIES_TTL_S``).
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
from app.collectors.meteoalarm import (
    COUNTRY_FEEDS,
    CapDocument,
    MeteoAlarmCapCollector,
    MeteoAlarmCollector,
    MeteoAlarmFeed,
    with_cap,
)
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
)
from app.services.geoindex import (
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
SUPPORTED_COUNTRIES = sorted({*COUNTRY_FEEDS, NWS_COUNTRY})

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
    ) -> None:
        self.meteoalarm = meteoalarm
        self.meteoalarm_cap = meteoalarm_cap
        self.noaa_nws = noaa_nws
        self.countries = countries
        self.geometries = geometries
        self._watched_zones = watched_zones
        self._recent: OrderedDict[str, float] = OrderedDict()
        self._tracked: dict[str, dict[str, Vigilance]] = {}

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

    def needed_countries(self, extra_points: Optional[list[tuple[float, float]]] = None) -> list[str]:
        """The MeteoAlarm countries worth keeping fresh: watched zones, followed points, recent requests."""
        needed: set[str] = set()
        points = [(float(z["lat"]), float(z["lon"])) for z in self._watched_zones()] + list(extra_points or [])
        for lat, lon in points:
            needed.update(self.countries.countries_for_point(lat, lon))
        needed.update(self.recent_countries())
        return sorted(country for country in needed if country in COUNTRY_FEEDS)

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
            return SourceData(
                "noaa_nws",
                NWS_COUNTRY,
                [],
                VigilanceCoverage(source="noaa_nws", country=NWS_COUNTRY, status="unavailable", note=f"The provider gave no answer: {exc}"),
            )
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
    def _age(collector: Any, key: str) -> Optional[int]:
        entry = collector._cache.get(key)
        return None if entry is None else max(0, int(clock.monotonic() - entry.stored_at))

    async def _sources(self, countries: list[str]) -> list[SourceData]:
        tasks = []
        for country in countries:
            if country == NWS_COUNTRY:
                tasks.append(self._nws())
            elif country in COUNTRY_FEEDS:
                tasks.append(self._meteoalarm(country))
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------ localization and the words of the issuer

    def _localized(self, vigilance: Vigilance) -> Vigilance:
        """The warning with how a point can be matched to its area on this instance."""
        localization = localization_of(vigilance, self.geometries)
        return vigilance.model_copy(update={"area": vigilance.area.model_copy(update={"localization": localization})})

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
            note = "No official warning source covers this point (MeteoAlarm: 39 European countries; NWS: the United States)."
        return VigilancesResponse(
            server_utc=int(clock.wall()),
            query=query,
            countries=countries,
            vigilances=vigilances,
            coverage=coverage,
            attribution=merge_credits(
                *(credit_text(key) for key in ("meteoalarm", "noaa_nws") if key in used)
            ),
            disclaimer=METEOALARM_DISCLAIMER if "meteoalarm" in used else None,
            note=note,
        )

    @staticmethod
    def _sorted(vigilances: list[Vigilance]) -> list[Vigilance]:
        return sorted(vigilances, key=lambda v: (-level_rank(v.level), v.onset_utc or 0, v.id))

    async def for_point(self, lat: float, lon: float, languages: Optional[tuple[str, ...]] = None) -> VigilancesResponse:
        """The warnings that cover the exact position (a polygon of the source, or a code whose shape is known)."""
        countries = [c for c in self.countries.countries_for_point(lat, lon) if c in SUPPORTED_COUNTRIES]
        for country in countries:
            self.remember(country)
        sources = await self._sources(countries)
        locator = PointLocator(lat, lon, self.geometries)
        found: list[Vigilance] = []
        for data in sources:
            self._finish_coverage(data)
            found.extend(self._localized(v) for v in data.vigilances if locator.covering(v))
        ordered = self._sorted(found)
        enriched, without = await self._with_texts(ordered, languages)
        self._forget_unlinked_documents()
        return self._response(VigilanceQuery(lat=lat, lon=lon), countries, sources, enriched, without)

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
        ordered = self._sorted(found)
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

    async def _current(self, lat: float, lon: float) -> tuple[dict[str, Vigilance], bool]:
        """The warnings covering a position by ``track_key`` (without the issuer's words: a cheap, cached read).

        The flag is False when a source did not answer: the picture is then incomplete.
        """
        countries = [c for c in self.countries.countries_for_point(lat, lon) if c in SUPPORTED_COUNTRIES]
        sources = await self._sources(countries)
        locator = PointLocator(lat, lon, self.geometries)
        found = [v for data in sources for v in data.vigilances if locator.covering(v)]
        return keep_the_strongest(found), all(data.coverage.status != "unavailable" for data in sources)

    async def refresh(self, points: list[FollowedPoint]) -> None:
        """Keep the feeds of the needed countries fresh (a cached feed costs nothing): called by the scheduler."""
        extra = [(p.lat, p.lon) for p in points]
        countries = self.needed_countries(extra)
        await asyncio.gather(*(self._meteoalarm(c) for c in countries if c in COUNTRY_FEEDS), return_exceptions=True)

    async def poll_changes(self, points: list[FollowedPoint]) -> list[tuple[FollowedPoint, VigilanceChange]]:
        """Compare, for each followed point, the warnings now with the ones the last call saw.

        A warning that appears, whose level goes up, or that ends (expires, is cancelled or is no longer listed)
        is a change. The first look at a point only sets the baseline: nothing is pushed for what was already there.
        An unreadable source never ends a warning: a point whose sources did not answer is left as it was.
        """
        changes: list[tuple[FollowedPoint, VigilanceChange]] = []
        seen = {point.key for point in points}
        for key in [k for k in self._tracked if k not in seen]:
            del self._tracked[key]
        for point in points:
            try:
                current, complete = await self._current(point.lat, point.lon)
            except Exception:
                continue
            if not complete:
                continue
            previous = self._tracked.get(point.key)
            self._tracked[point.key] = current
            if previous is None:
                continue
            now = int(clock.wall())
            for key, vigilance in current.items():
                before = previous.get(key)
                if before is None:
                    changes.append((point, self._change(point, "appeared", vigilance, None, now)))
                elif level_rank(vigilance.level) > level_rank(before.level) >= 0:
                    changes.append((point, self._change(point, "upgraded", vigilance, before.level, now)))
            for key, vigilance in previous.items():
                if key not in current:
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
        for point, change in changes:
            replacement = by_id.get(change.vigilance.id)
            if replacement is not None and change.change != "ended":
                change = change.model_copy(update={"vigilance": self._localized(replacement), "attribution": replacement.attribution})
            else:
                change = change.model_copy(update={"vigilance": self._localized(change.vigilance)})
            out.append((point, change))
        return out
