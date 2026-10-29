"""Multi-provider orchestrator: geofencing, SQLite persistence, weather fallback and simulator.

Main responsibilities:

1. Collect the seismic, volcanic and disaster feeds in parallel (USGS, EMSC, GDACS, NOAA NWS,
   USGS Volcano, the NHC storms, the tsunami warning centres) and merge duplicates with ``deduplicate_hazards``.
2. Keep the weather available at any GPS position with an automatic switch:
   Open-Meteo (``minutely_15``) -> MET Norway Locationforecast 2.0 -> the last cached Open-Meteo
   value. When all three fail the position has **no weather** (``None``): nothing is invented.
   The weather is completed by the RainViewer radar, Open-Meteo Flood (GloFAS) and Open-Meteo
   Air Quality.
3. Persist the watchlist (``/v1/zones``), the hazard history (``/v1/history``) and the last
   valid snapshot in SQLite (``SQLiteWALStorage``).
4. Enrich every hazard with geodesic values (``distance_to_zone_km``, ``bearing_deg``,
   ``bearing_cardinal``, ``tsunami_eta_minutes``).
5. Produce the standard representations (IETF RFC 7946 GeoJSON, OASIS CAP 1.2) and the
   embedded frame (``GET /v1/embedded/frame``) protected by a CRC32.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Optional, TypeVar

from app import clock
from app.attribution import (
    MET_NORWAY_WEATHER,
    OPEN_METEO_WEATHER,
    RADAR,
    SIMULATED_ID_PREFIX,
    SIMULATION_CREDIT,
    credit_text,
    merge_credits,
)
from app.collectors.aurora import (
    SWPCAlertsCollector,
    SWPCKpForecastCollector,
    SWPCOvationCollector,
    SWPCSolarWindCollector,
)
from app.collectors.budget import share_budgets
from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.dwd import DWDCollector
from app.collectors.eccc import ECCCCollector
from app.collectors.firms import FIRMSCollector
from app.collectors.firms import load_settings as load_firms_settings
from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.health import ECDCHealthCollector
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.meteoalarm import MeteoAlarmCapCollector, MeteoAlarmCollector
from app.collectors.metservice import MetServiceCollector, metservice_enabled_from_env
from app.collectors.nhc import NHCCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.surf import SurfForecastCollector
from app.collectors.swic import SwicCollector
from app.collectors.swic_registry import SwicRegistryCollector, wmo_sources_from_env
from app.collectors.travel import (
    CanadaTravelCollector,
    FCDOTravelCollector,
    NetherlandsTravelCollector,
    SmartravellerCollector,
    USTravelCollector,
)
from app.collectors.tsunami import TsunamiCollector
from app.collectors.vigicrues import VigicruesCollector
from app.collectors.weather import (
    MetNorwayCollector,
    OpenMeteoCollector,
    RainViewerCollector,
    as_stale_bundle,
    rainviewer_enabled_from_env,
)
from app.models.earthquakes import EarthquakesQuery, EarthquakesResponse
from app.models.events import EventsQuery, EventsResponse
from app.models.forecast import HazardForecast
from app.models.places import WatchedPoint, WatchedPointUpsertRequest
from app.models.schemas import (
    SEVERITY_RANK,
    AirQualityStatus,
    CAPAlert,
    CAPArea,
    CAPFeedResponse,
    CAPInfo,
    CircuitState,
    CurrentWeather,
    DeviceType,
    FloodRiskStatus,
    GeoJSONFeature,
    GeoJSONFeatureCollection,
    GeoJSONPointGeometry,
    GlobalStatus,
    GPSCoordinates,
    HazardAlert,
    HazardCategory,
    HistoryResponse,
    MarineConditions,
    MonitoredZoneConfig,
    MonitoredZoneUpsertRequest,
    PointResponse,
    ProviderHealth,
    RainNowcast,
    SeverityLevel,
    SimulationRequest,
    SimulationResponse,
    SimulationScenario,
    SnapshotResponse,
    SpaceWeatherStatus,
    ZoneWeather,
)
from app.models.watch import WatchedPointsStatus, WatchedSource
from app.services.aurora import AuroraService
from app.services.earthquakes import build_earthquakes, ids_within
from app.services.eclipses import EclipseService
from app.services.embedded_frame import serialize_embedded_frame
from app.services.events import count as count_events
from app.services.events import group_hazards, group_warnings, sorted_events
from app.services.fires import NEARBY_RADIUS_DEFAULT_KM, FireService
from app.services.forecasts import empty as empty_forecast
from app.services.forecasts import has_storm_forecast, has_tsunami_forecast, storm_forecast, tsunami_forecast
from app.services.geodesy import (
    deduplicate_hazards,
    enrich_hazard_for_point,
    haversine_km,
)
from app.services.geoindex import CountryIndex, GeometryIndex, default_countries, default_geometries
from app.services.health import HealthService
from app.services.places import FaultIndex, NearPlacesService, PlaceSettings
from app.services.storage_wal import DEFAULT_WATCHLIST_SEED, SQLiteWALStorage
from app.services.travel import TravelService
from app.services.travel_store import TravelStore
from app.services.vigilance import NWS_COUNTRY, TSUNAMI_SOURCE, VigilanceService
from app.services.vigilance_archive import VigilanceArchive
from app.services.watch import SourceReads, build_status, candidate_ids, parse_window_days

T = TypeVar("T")

log = logging.getLogger(__name__)

EVENTS_NOTE = (
    "The events are Vigie's own grouping (ADR 0012): `title`, `level`, `areas_count`, the period and `bbox` are worked out (listed in "
    "`derived`); the warnings and hazards inside are as their sources gave them. A source that does not answer is in `coverage`: its "
    "warnings are missing, which is not \"nothing in force\". A MeteoAlarm warning has its words once its CAP document is read (at most "
    "40 new ones per request). This is not an official warning service: follow the warnings of your national authorities."
)

DEFAULT_MONITORED_ZONES: list[dict[str, Any]] = list(DEFAULT_WATCHLIST_SEED)

DEVICE_DEFAULT_MAX_HAZARDS: dict[DeviceType, int] = {
    DeviceType.WAVESHARE_7B: 15,
    DeviceType.TAB5: 15,
    DeviceType.MARINE: 12,
    DeviceType.WEB: 50,
    DeviceType.GENERIC: 15,
}


async def _or_none(awaitable: Awaitable[T]) -> Optional[T]:
    """Await ``awaitable`` and return ``None`` if it raises: an unavailable provider means unknown."""
    try:
        return await awaitable
    except Exception:
        return None


async def _none_coro() -> None:
    """Awaitable that yields ``None`` (placeholder for an optional provider that was not requested)."""
    return None


class AggregatorService:
    """Central multi-provider orchestrator and SQLite persistence of the Vigie API."""

    def __init__(
        self,
        usgs: Optional[USGSCollector] = None,
        emsc: Optional[EMSCCollector] = None,
        gdacs: Optional[GDACSCollector] = None,
        noaa_nws: Optional[NOAANWSCollector] = None,
        volcano: Optional[USGSVolcanoCollector] = None,
        open_meteo: Optional[OpenMeteoCollector] = None,
        met_norway: Optional[MetNorwayCollector] = None,
        rainviewer: Optional[RainViewerCollector] = None,
        marine: Optional[OpenMeteoMarineCollector] = None,
        surf: Optional[SurfForecastCollector] = None,
        flood: Optional[OpenMeteoFloodCollector] = None,
        air_quality: Optional[OpenMeteoAirQualityCollector] = None,
        swpc: Optional[NOAASWPCCollector] = None,
        storage: Optional[SQLiteWALStorage] = None,
        db_path: Optional[str | Path] = None,
        rainviewer_enabled: Optional[bool] = None,
        metservice_enabled: Optional[bool] = None,
        meteoalarm: Optional[MeteoAlarmCollector] = None,
        meteoalarm_cap: Optional[MeteoAlarmCapCollector] = None,
        dwd: Optional[DWDCollector] = None,
        eccc: Optional[ECCCCollector] = None,
        metservice: Optional[MetServiceCollector] = None,
        vigicrues: Optional[VigicruesCollector] = None,
        nhc: Optional[NHCCollector] = None,
        tsunami: Optional[TsunamiCollector] = None,
        swic: Optional[SwicCollector] = None,
        swic_registry: Optional[SwicRegistryCollector] = None,
        wmo_sources: Optional[str] = None,
        firms: Optional[FIRMSCollector] = None,
        countries: Optional[CountryIndex] = None,
        geometries: Optional[GeometryIndex] = None,
        place_settings: Optional[PlaceSettings] = None,
        faults: Optional[FaultIndex] = None,
        swpc_ovation: Optional[SWPCOvationCollector] = None,
        swpc_kp_forecast: Optional[SWPCKpForecastCollector] = None,
        swpc_solar_wind: Optional[SWPCSolarWindCollector] = None,
        swpc_alerts: Optional[SWPCAlertsCollector] = None,
        travel_gb: Optional[FCDOTravelCollector] = None,
        travel_us: Optional[USTravelCollector] = None,
        travel_ca: Optional[CanadaTravelCollector] = None,
        travel_au: Optional[SmartravellerCollector] = None,
        travel_nl: Optional[NetherlandsTravelCollector] = None,
        health_ecdc: Optional[ECDCHealthCollector] = None,
    ) -> None:
        self.started_at_utc = int(time.time())
        # How far the hazards look for the places near them (faults, volcanoes in alert): settings, not
        # sourced facts (``PlaceSettings``). ``faults`` replaces the bundled fault file (a test).
        self.places = NearPlacesService(place_settings or PlaceSettings.from_env(), (lambda: faults) if faults is not None else None)
        # RainViewer is on unless VIGIE_ENABLE_RAINVIEWER turns it off (see ``rainviewer_enabled_from_env``).
        self.rainviewer_enabled = rainviewer_enabled_from_env() if rainviewer_enabled is None else rainviewer_enabled
        # MetService is on unless VIGIE_ENABLE_METSERVICE turns it off (see ``metservice_enabled_from_env``).
        self.metservice_enabled = metservice_enabled_from_env() if metservice_enabled is None else metservice_enabled
        # The services of the WMO registry: ``verified`` (default), ``all`` or ``off`` (see ``wmo_sources_from_env``).
        self.wmo_sources = wmo_sources_from_env() if wmo_sources is None else wmo_sources
        self.usgs = usgs or USGSCollector()
        self.emsc = emsc or EMSCCollector()
        self.gdacs = gdacs or GDACSCollector()
        self.noaa_nws = noaa_nws or NOAANWSCollector()
        self.volcano = volcano or USGSVolcanoCollector()
        self.open_meteo = open_meteo or OpenMeteoCollector()
        self.met_norway = met_norway or MetNorwayCollector()
        self.rainviewer = rainviewer or RainViewerCollector()
        self.marine = marine or OpenMeteoMarineCollector()
        self.surf = surf or SurfForecastCollector()
        self.flood = flood or OpenMeteoFloodCollector()
        self.air_quality = air_quality or OpenMeteoAirQualityCollector()
        self.swpc = swpc or NOAASWPCCollector()
        # The aurora (ADR 0013): four more SWPC products, one copy each for the whole instance.
        self.swpc_ovation = swpc_ovation or SWPCOvationCollector()
        self.swpc_kp_forecast = swpc_kp_forecast or SWPCKpForecastCollector()
        self.swpc_solar_wind = swpc_solar_wind or SWPCSolarWindCollector()
        self.swpc_alerts = swpc_alerts or SWPCAlertsCollector()
        self.aurora = AuroraService(
            self.swpc_ovation, self.swpc_kp_forecast, self.swpc_solar_wind, self.swpc_alerts, self.clouds_for_aurora
        )
        self.eclipses = EclipseService(self.clouds_for_aurora)  # NASA's bundled catalogue: no request but the clouds
        self.meteoalarm = meteoalarm or MeteoAlarmCollector()
        self.meteoalarm_cap = meteoalarm_cap or MeteoAlarmCapCollector()
        self.dwd = dwd or DWDCollector()
        self.eccc = eccc or ECCCCollector()
        self.metservice = metservice or MetServiceCollector()
        self.vigicrues = vigicrues or VigicruesCollector()
        self.nhc = nhc or NHCCollector()
        self.tsunami = tsunami or TsunamiCollector()
        self.swic = swic or SwicCollector()
        self.swic_registry = swic_registry or SwicRegistryCollector()
        # The travel advice of five foreign ministries (read when asked for, and kept fresh by the scheduler).
        self.travel_gb = travel_gb or FCDOTravelCollector()
        self.travel_us = travel_us or USTravelCollector()
        self.travel_ca = travel_ca or CanadaTravelCollector()
        self.travel_au = travel_au or SmartravellerCollector()
        self.travel_nl = travel_nl or NetherlandsTravelCollector()
        # The ECDC's publications on communicable disease threats, for the travel pages.
        self.health_ecdc = health_ecdc or ECDCHealthCollector()
        # NASA FIRMS (active fires) is read from its static files, which need no key, unless ``VIGIE_ENABLE_FIRMS=0`` (or no collector is
        # given): then there is no collector, ``GET /v1/health`` lists the source as disabled and the fire routes say so. An optional
        # ``VIGIE_FIRMS_MAP_KEY`` lets a source whose file could not be read be read from the area API.
        firms_settings = load_firms_settings()
        if firms_settings.key_problem is not None:
            log.warning(firms_settings.key_problem)
        if firms is None and firms_settings.enabled:
            firms = FIRMSCollector(firms_settings.key, firms_settings.sources, firms_settings.refresh_s, firms_settings.window_hours)
        self.firms = firms
        self.fires = FireService(self.firms, None if self.firms is not None else firms_settings.off_reason)
        share_budgets(self.get_all_collectors())  # the four Open-Meteo APIs share one quota

        self.storage = storage or SQLiteWALStorage(db_path=db_path)
        # The FCDO pages read one by one survive a restart, in the same database (a storage without a file keeps none).
        db_file = getattr(self.storage, "db_path", None)
        travel_store = TravelStore(db_file) if db_file is not None else None
        if self.travel_gb.store is None:
            self.travel_gb.store = travel_store
        self.monitored_zones: list[dict[str, Any]] = [
            z.model_dump() for z in self.storage.list_zones()
        ]
        # The watched points and the runs of readings are read from the database once per storage (see the properties below).
        self._watched: list[WatchedPoint] = []
        self._watched_storage: Optional[SQLiteWALStorage] = None
        self._reads: Optional[SourceReads] = None
        self._reads_storage: Optional[SQLiteWALStorage] = None
        self.simulation_active: bool = False
        self.active_scenario: SimulationScenario = SimulationScenario.CLEAR
        self.simulated_hazards: list[HazardAlert] = []
        self.last_weather_fallback_used: bool = False
        # Every official warning a source lists is archived (``GET /v1/vigilances/archive``), in the database of the moment.
        self.vigilance_archive = VigilanceArchive(lambda: self.storage)
        # The official warnings (MeteoAlarm, the national services, the NWS): the NWS answer is the hazards' own, no
        # request of its own.
        self.vigilances = VigilanceService(
            meteoalarm=self.meteoalarm,
            meteoalarm_cap=self.meteoalarm_cap,
            noaa_nws=self.noaa_nws,
            countries=countries or default_countries(),
            geometries=geometries or default_geometries(),
            watched_zones=lambda: self.monitored_zones,
            dwd=self.dwd,
            eccc=self.eccc,
            metservice=self.metservice if self.metservice_enabled else None,
            vigicrues=self.vigicrues,
            tsunami=self.tsunami,
            swic=self.swic if self.wmo_sources != "off" else None,
            swic_registry=self.swic_registry if self.wmo_sources != "off" else None,
            swic_mode=self.wmo_sources,
            archive=self.vigilance_archive,
        )
        # The travel advice: every ministry's level for each country, one country's texts, and the archive of the versions.
        self.travel = TravelService(
            {"gb": self.travel_gb, "us": self.travel_us, "ca": self.travel_ca, "au": self.travel_au, "nl": self.travel_nl},
            countries=self.vigilances.countries,
            store=travel_store,
            health=HealthService(self.health_ecdc, self.vigilances.countries),
        )

    @property
    def place_settings(self) -> PlaceSettings:
        """How far this instance looks for the places near a hazard (one object: the one ``self.places`` uses)."""
        return self.places.settings

    def refresh_zones_from_storage(self) -> list[MonitoredZoneConfig]:
        """Reload the watched zones from SQLite."""
        zones = self.storage.list_zones()
        self.monitored_zones = [z.model_dump() for z in zones]
        return zones

    def list_zones(self) -> list[MonitoredZoneConfig]:
        """Return the active watched zones."""
        return self.refresh_zones_from_storage()

    def upsert_zone(self, req: MonitoredZoneUpsertRequest) -> MonitoredZoneConfig:
        """Create or update a watched zone and refresh the in-memory copy."""
        created = self.storage.upsert_zone(req)
        self.refresh_zones_from_storage()
        return created

    def delete_zone(self, zone_id: str) -> bool:
        """Delete a watched zone and refresh the in-memory copy."""
        deleted = self.storage.delete_zone(zone_id)
        self.refresh_zones_from_storage()
        return deleted

    @property
    def watched_points(self) -> list[WatchedPoint]:
        """The points the user watches (``/v1/watched-points``), read from SQLite again whenever the storage is another one."""
        if self._watched_storage is not self.storage:
            self.refresh_watched_points_from_storage()
        return self._watched

    def refresh_watched_points_from_storage(self) -> list[WatchedPoint]:
        """Reload the watched points from SQLite."""
        self._watched = self.storage.list_watched_points()
        self._watched_storage = self.storage
        return self._watched

    def upsert_watched_point(self, req: WatchedPointUpsertRequest) -> WatchedPoint:
        """Add or update a watched point (``WatchedPointsLimitError`` when a new one would go over the limit)."""
        created = self.storage.upsert_watched_point(req, self.place_settings.watched_points_max)
        self.refresh_watched_points_from_storage()
        return created

    def delete_watched_point(self, point_id: str) -> bool:
        """Delete a watched point and refresh the in-memory copy."""
        deleted = self.storage.delete_watched_point(point_id)
        self.refresh_watched_points_from_storage()
        return deleted

    def hazard_collectors(self) -> list[Any]:
        """The collectors of the hazards, in the order ``collect_hazards_with_status`` reads them."""
        return [self.usgs, self.emsc, self.gdacs, self.noaa_nws, self.volcano, self.nhc, self.tsunami]

    def source_reads(self) -> SourceReads:
        """What the API knows of the readings of the hazard sources (the runs are loaded from SQLite once per storage)."""
        if self._reads is None or self._reads_storage is not self.storage:
            try:
                runs = self.storage.load_source_runs()
            except Exception:
                runs = {}
            self._reads = SourceReads(runs, self.place_settings.quiet_max_gap_s)
            self._reads_storage = self.storage
        return self._reads

    def _note_source_reads(self, collectors: list[Any], results: list[Any]) -> None:
        """Note which hazard sources answered this time. An answer counts only if it is not a stale one served after a failure."""
        answered = {
            c.provider_id: isinstance(res, list) and c.consecutive_failures == 0 and c.circuit_state == CircuitState.CLOSED
            for c, res in zip(collectors, results, strict=True)
        }
        reads = self.source_reads()
        now_utc = int(clock.wall())
        reads.record(answered, now_utc)
        if reads.dirty(now_utc):
            try:
                self.storage.save_source_runs(reads.runs)
            except Exception:
                pass  # the runs stay in memory; the next reading writes them
            reads.persisted(now_utc)

    def get_history(
        self,
        category: Optional[HazardCategory] = None,
        min_severity: Optional[SeverityLevel] = None,
        limit: int = 50,
    ) -> HistoryResponse:
        """Return the filtered hazard history from SQLite."""
        items = self.storage.query_history(category=category, min_severity=min_severity, limit=limit)
        return HistoryResponse(count=len(items), items=items)

    def get_all_collectors(self) -> list[Any]:
        """Return the ordered list of every managed collector (without RainViewer when it is turned off)."""
        collectors: list[Any] = [
            self.usgs,
            self.emsc,
            self.gdacs,
            self.noaa_nws,
            self.volcano,
            self.open_meteo,
            self.met_norway,
            self.rainviewer,
            self.marine,
            self.surf,
            self.flood,
            self.air_quality,
            self.swpc,
            self.swpc_ovation,
            self.swpc_kp_forecast,
            self.swpc_solar_wind,
            self.swpc_alerts,
            self.meteoalarm,
            self.meteoalarm_cap,
            self.dwd,
            self.eccc,
            self.metservice,
            self.vigicrues,
            self.nhc,
            self.tsunami,
            self.swic,
            self.swic_registry,
            self.travel_gb,
            self.travel_us,
            self.travel_ca,
            self.travel_au,
            self.travel_nl,
            self.health_ecdc,
        ]
        off = [
            c
            for c, enabled in (
                (self.rainviewer, self.rainviewer_enabled),
                (self.metservice, self.metservice_enabled),
                (self.swic, self.wmo_sources != "off"),
                (self.swic_registry, self.wmo_sources != "off"),
            )
            if not enabled
        ]
        if self.firms is not None:
            collectors.append(self.firms)
        return [c for c in collectors if c not in off]

    def disabled_sources(self) -> list[dict[str, str]]:
        """The optional sources that are off on this instance and why (``GET /v1/health``); a source that is off is not in the providers."""
        if self.firms is not None:
            return []
        return [
            {
                "provider_id": "nasa_firms",
                "name": "NASA FIRMS (active fires)",
                "domain": "fires",
                "reason": self.fires.off_reason,
            }
        ]

    def get_providers_health(self) -> list[ProviderHealth]:
        """Return the detailed health of each collector."""
        return [collector.get_health() for collector in self.get_all_collectors()]

    async def fetch_weather_with_fallback(
        self, lat: float, lon: float, utc_offset_s: Optional[int] = None
    ) -> tuple[Optional[dict[str, Any]], bool]:
        """Fetch the weather of a GPS point: Open-Meteo, then MET Norway, then the stale cache.

        Returns ``(bundle, fallback_used)``. ``bundle`` is ``None`` when every source failed:
        the caller must then report the weather as unknown. The bundle carries the longest
        forecast (10 days): the caller keeps the days its profile needs. ``utc_offset_s`` (the
        zone configuration) only helps MET Norway to group its hourly series into local days.

        When the Open-Meteo call budget is used up, Open-Meteo hands back its expired entry: MET Norway
        is asked first, and that expired entry (marked stale) is served only when MET Norway has no
        fresh answer either.
        """
        try:
            bundle = await self.open_meteo.fetch_point(lat, lon, allow_stale_on_error=False)
        except Exception:
            pass
        else:
            if not bundle.get("stale"):
                self.last_weather_fallback_used = False
                return bundle, False
            try:
                relay = await self.met_norway.fetch_point(
                    lat, lon, allow_stale_on_error=False, utc_offset_s=utc_offset_s
                )
            except Exception:
                relay = None
            self.last_weather_fallback_used = True
            # A MET Norway answer is stale only when its own budget is used up: the Open-Meteo entry is then kept.
            return (relay if relay is not None and not relay.get("stale") else bundle), True

        try:
            bundle = await self.met_norway.fetch_point(
                lat, lon, allow_stale_on_error=True, utc_offset_s=utc_offset_s
            )
            self.last_weather_fallback_used = True
            return bundle, True
        except Exception:
            pass

        stale = self.open_meteo.get_stale_point(lat, lon)
        if stale is not None:
            self.last_weather_fallback_used = True
            return as_stale_bundle(stale), True

        self.last_weather_fallback_used = True
        return None, False

    async def collect_hazards_with_status(self) -> tuple[list[HazardAlert], int, int]:
        """Collect, de-duplicate and record the hazards.

        Returns ``(hazards, sources_ok, sources_total)``. A source that raised (and has no
        cached value) counts as not answering, so an empty list from a working source can be
        told apart from "no source answered".
        """
        collectors = self.hazard_collectors()
        results = await asyncio.gather(*(c.fetch() for c in collectors), return_exceptions=True)

        raw_hazards: list[HazardAlert] = []
        sources_ok = 0
        for res in results:
            if isinstance(res, list):
                sources_ok += 1
                raw_hazards.extend(res)
        self._note_source_reads(collectors, list(results))

        if self.simulation_active and self.simulated_hazards:
            raw_hazards.extend(self.simulated_hazards)

        fused = deduplicate_hazards(raw_hazards)
        enriched = [self._attach_nearest_monitored_zone(h) for h in fused]
        try:  # the places near each hazard; they never change a severity, and a failure of theirs never stops the hazards
            enriched = self.places.annotate(enriched)
        except Exception:
            log.warning("near_places could not be worked out", exc_info=True)
        try:
            self.storage.record_hazards(enriched)
        except Exception:
            pass
        return enriched, sources_ok, len(results)

    async def collect_and_fuse_hazards(self) -> list[HazardAlert]:
        """Collect USGS, EMSC, GDACS, NOAA NWS, USGS Volcano, the NHC storms and the tsunami centres in parallel, de-duplicate and record them."""
        hazards, _, _ = await self.collect_hazards_with_status()
        return hazards

    def _attach_nearest_monitored_zone(self, hazard: HazardAlert) -> HazardAlert:
        """Attach the nearest watched zone, its distance, bearing and tsunami ETA to an alert."""
        zones = list(self.monitored_zones)
        if not zones:
            return hazard

        best_zone: Optional[dict[str, Any]] = None
        best_dist: float = float("inf")
        for z in zones:
            d = haversine_km(float(z["lat"]), float(z["lon"]), hazard.lat, hazard.lon)
            if d < best_dist:
                best_dist = d
                best_zone = z

        if best_zone is None:
            return hazard

        return enrich_hazard_for_point(
            hazard,
            ref_lat=float(best_zone["lat"]),
            ref_lon=float(best_zone["lon"]),
            zone_id=str(best_zone["id"]),
            zone_name=str(best_zone["name"]),
        )

    async def clouds_for_aurora(
        self, lat: float, lon: float
    ) -> tuple[Optional[list[tuple[int, Optional[float]]]], Optional[int], Optional[str]]:
        """The hourly cloud cover of the next 48 hours at a place, the local UTC offset and the credit of the source.

        Open-Meteo's daily request (shared with ``/v1/point``), then MET Norway; ``(None, None, None)`` when neither answers.
        """
        try:
            clouds, offset = await self.open_meteo.fetch_hourly_clouds(lat, lon)
            return clouds, offset, credit_text(*OPEN_METEO_WEATHER)
        except Exception:
            pass
        try:
            bundle = await self.met_norway.fetch_point(lat, lon, allow_stale_on_error=True)
        except Exception:
            return None, None, None
        return list(bundle.get("hourly_cloud_cover", [])), None, credit_text(*MET_NORWAY_WEATHER)

    async def get_space_weather_safe(self) -> SpaceWeatherStatus:
        """Read the NOAA SWPC space weather. If SWPC cannot be read every value is unknown (``None``)."""
        status = await _or_none(self.swpc.fetch())
        if status is None:
            status = SpaceWeatherStatus()
        if self.simulation_active and self.active_scenario == SimulationScenario.MEGA_TSUNAMI_JAPAN:
            return SpaceWeatherStatus(
                solar_kp=max(status.solar_kp or 0.0, 6.3),
                solar_storm_level="G2",
                gps_degraded=True,
                hf_radio_blackout_risk=True,
                attribution=SIMULATION_CREDIT,
            )
        return status

    async def get_flood_risk_safe(self, lat: float, lon: float) -> Optional[FloodRiskStatus]:
        """Read the GloFAS river discharge at ``lat, lon``; ``None`` when it cannot be read."""
        return await _or_none(self.flood.fetch_flood_risk(lat, lon))

    async def get_air_quality_safe(self, lat: float, lon: float) -> Optional[AirQualityStatus]:
        """Read air quality, UV and dust at ``lat, lon``; ``None`` when it cannot be read."""
        status = await _or_none(self.air_quality.fetch_air_quality(lat, lon))
        if self.simulation_active and self.active_scenario == SimulationScenario.VOLCANO_ERUPTION_ICELAND:
            return AirQualityStatus(
                european_aqi=max(status.european_aqi or 0, 145) if status else 145,
                us_aqi=max(status.us_aqi or 0, 180) if status else 180,
                pm2_5_ugm3=max(status.pm2_5_ugm3 or 0.0, 65.0) if status else 65.0,
                pm10_ugm3=max(status.pm10_ugm3 or 0.0, 190.0) if status else 190.0,
                uv_index=status.uv_index if status else None,
                dust_ugm3=max(status.dust_ugm3 or 0.0, 240.0) if status else 240.0,
                ash_or_dust_warning=True,
                attribution=SIMULATION_CREDIT,
            )
        return status

    @staticmethod
    def _with_air_quality(
        current: CurrentWeather, aqi_status: Optional[AirQualityStatus]
    ) -> CurrentWeather:
        """Complete the current weather with the EAQI and, when the weather has none, the UV index."""
        if aqi_status is None:
            return current
        uv = current.uv_index if current.uv_index is not None else aqi_status.uv_index
        return current.model_copy(update={"aqi_eu": aqi_status.european_aqi, "uv_index": uv})

    async def build_point_response(
        self,
        lat: float,
        lon: float,
        marine: bool = False,
        radius_km: float = 500.0,
        sog_knots: Optional[float] = None,
        cog_deg: Optional[float] = None,
        device_type: DeviceType = DeviceType.TAB5,
        max_hazards: Optional[int] = None,
        compact: bool = True,
        fire_radius_km: float = NEARBY_RADIUS_DEFAULT_KM,
    ) -> PointResponse:
        """Build the ``/v1/point`` response for a GPS device (boat, M5Stack Tab5, vehicle)."""
        include_marine = bool(marine or device_type == DeviceType.MARINE)
        days_count = 5 if compact else 10
        limit_hazards = max_hazards or DEVICE_DEFAULT_MAX_HAZARDS.get(device_type, 15)

        (
            (weather_bundle, fallback_used),
            all_hazards,
            space_status,
            flood_status,
            aqi_status,
            radar_meta,
            marine_conditions,
            fires_nearby,
        ) = await asyncio.gather(
            self.fetch_weather_with_fallback(lat, lon),
            self.collect_and_fuse_hazards(),
            self.get_space_weather_safe(),
            self.get_flood_risk_safe(lat, lon),
            self.get_air_quality_safe(lat, lon),
            _or_none(self.rainviewer.fetch()) if self.rainviewer_enabled else _none_coro(),
            _or_none(self.marine.fetch_marine(lat, lon)) if include_marine else _none_coro(),
            _or_none(self.fires.nearby(lat, lon, fire_radius_km)),
        )
        radar_meta = radar_meta or {}

        if include_marine and self.simulation_active and self.active_scenario == SimulationScenario.SEVERE_SQUALL_MARINE:
            marine_conditions = MarineConditions(
                wave_height_m=5.4,
                wave_direction_deg=285,
                wave_period_s=12.5,
                swell_wave_height_m=4.8,
                swell_wave_direction_deg=290,
                swell_wave_period_s=14.0,
                ocean_current_velocity_kmh=6.8,
                ocean_current_direction_deg=105,
                sea_state_label="HIGH",
                navigation_warning=True,
                attribution=SIMULATION_CREDIT,
            )

        enriched_nearby: list[HazardAlert] = []
        local_level = SeverityLevel.GREEN

        for h in all_hazards:
            enriched = enrich_hazard_for_point(
                h, ref_lat=lat, ref_lon=lon, zone_id="gps_point", zone_name="GPS position"
            )
            dist = enriched.distance_to_zone_km or 99999.0
            if dist <= radius_km or enriched.severity == SeverityLevel.RED:
                enriched_nearby.append(enriched)
                if dist <= radius_km and SEVERITY_RANK[enriched.severity] > SEVERITY_RANK[local_level]:
                    local_level = enriched.severity

        enriched_nearby.sort(
            key=lambda x: (
                0 if (x.distance_to_zone_km or 99999.0) <= radius_km else 1,
                -SEVERITY_RANK[x.severity],
                x.distance_to_zone_km or 99999.0,
            )
        )
        enriched_nearby = enriched_nearby[:limit_hazards]
        if compact:  # the 16 KiB profile carries no `near_places` (docs/decisions/0008-sensitive-points.md): ask `compact=false`
            enriched_nearby = [h.model_copy(update={"near_places": None}) for h in enriched_nearby]

        rain_steps: Optional[list[float]] = weather_bundle.get("rain_next_60m") if weather_bundle else None
        rain_nowcast = RainNowcast(
            interval_minutes=15,
            mm_per_step=rain_steps,
            rain_expected_60m=None if rain_steps is None else any(v > 0.05 for v in rain_steps),
            max_mm_h=None if rain_steps is None else round(max(rain_steps) * 4.0, 2),
            radar_tile_url_template=radar_meta.get("radar_tile_url_template"),
            radar_timestamp_utc=radar_meta.get("radar_timestamp_utc"),
            # the rain steps come from the weather provider (Open-Meteo), the radar fields from RainViewer
            attribution=merge_credits(
                weather_bundle.get("attribution") if weather_bundle and rain_steps is not None else None,
                credit_text(*RADAR)
                if radar_meta.get("radar_tile_url_template") or radar_meta.get("radar_timestamp_utc") is not None
                else None,
            ),
        )

        if marine_conditions and marine_conditions.navigation_warning:
            if SEVERITY_RANK[SeverityLevel.ORANGE] > SEVERITY_RANK[local_level]:
                local_level = SeverityLevel.ORANGE

        # River discharge never raises the level: the source gives no flood threshold (see flood_air.parse_response).

        if aqi_status is not None and aqi_status.ash_or_dust_warning:
            if SEVERITY_RANK[SeverityLevel.ORANGE] > SEVERITY_RANK[local_level]:
                local_level = SeverityLevel.ORANGE

        current_weather: Optional[CurrentWeather] = (
            self._with_air_quality(weather_bundle["current"], aqi_status) if weather_bundle else None
        )

        return PointResponse(
            version="1.1",
            server_utc=int(time.time()),
            device_type=device_type,
            compact=compact,
            coordinates=GPSCoordinates(
                lat=round(lat, 4),
                lon=round(lon, 4),
                sog_knots=sog_knots,
                cog_deg=cog_deg,
            ),
            utc_offset_s=weather_bundle.get("utc_offset_s") if weather_bundle else None,
            weather_provider=str(weather_bundle["provider_name"]) if weather_bundle else None,
            fallback_used=bool(weather_bundle) and fallback_used,
            weather_attribution=(
                merge_credits(weather_bundle.get("attribution"), aqi_status.attribution if aqi_status else None)
                if weather_bundle
                else None
            ),
            local_alert_level=local_level,
            current=current_weather,
            daily=list(weather_bundle.get("daily_10d") or [])[:days_count] if weather_bundle else [],
            rain_nowcast=rain_nowcast,
            marine=marine_conditions,
            flood_risk=flood_status,
            air_quality=aqi_status,
            space_weather=space_status,
            nearby_hazards=enriched_nearby,
            # Informative only: the fires never enter ``local_alert_level`` (a hotspot is a satellite pixel, not a warning).
            fires_nearby=fires_nearby,
        )

    async def build_snapshot(
        self,
        device_type: DeviceType = DeviceType.WAVESHARE_7B,
        max_hazards: Optional[int] = None,
        compact: bool = True,
    ) -> SnapshotResponse:
        """Build the global snapshot (under 16 KiB in compact mode) and save it to SQLite."""
        days_per_zone = 5 if compact else 10
        limit_hazards = max_hazards or DEVICE_DEFAULT_MAX_HAZARDS.get(device_type, 15)
        zones_cfg = list(self.monitored_zones)

        weather_tasks = [
            self.fetch_weather_with_fallback(
                float(z["lat"]), float(z["lon"]), utc_offset_s=int(z["utc_offset_s"])
            )
            for z in zones_cfg
        ]
        aqi_tasks = [self.get_air_quality_safe(float(z["lat"]), float(z["lon"])) for z in zones_cfg]

        (all_hazards, sources_ok, sources_total), space_status, *rest = await asyncio.gather(
            self.collect_hazards_with_status(),
            self.get_space_weather_safe(),
            *weather_tasks,
            *aqi_tasks,
        )
        zone_results = rest[: len(zones_cfg)]
        aqi_results = rest[len(zones_cfg) :]

        any_fallback = False
        without_weather = 0
        zones_out: list[ZoneWeather] = []

        for idx, z_cfg in enumerate(zones_cfg):
            w_bundle, fb_used = zone_results[idx]
            any_fallback = any_fallback or (fb_used and w_bundle is not None)

            z_lat = float(z_cfg["lat"])
            z_lon = float(z_cfg["lon"])
            z_radius = float(z_cfg.get("alert_radius_km", 500.0))

            local_lvl = SeverityLevel.GREEN
            for h in all_hazards:
                dist = haversine_km(z_lat, z_lon, h.lat, h.lon)
                if dist <= z_radius and SEVERITY_RANK[h.severity] > SEVERITY_RANK[local_lvl]:
                    local_lvl = h.severity

            if w_bundle is None:
                without_weather += 1
                offset = int(z_cfg["utc_offset_s"])
                zone_weather = ZoneWeather(
                    id=str(z_cfg["id"]),
                    name=str(z_cfg["name"]),
                    lat=z_lat,
                    lon=z_lon,
                    utc_offset_s=offset,
                    alert_radius_km=z_radius,
                    local_alert_level=local_lvl,
                )
            else:
                provider_offset = w_bundle.get("utc_offset_s")
                zone_weather = ZoneWeather(
                    id=str(z_cfg["id"]),
                    name=str(z_cfg["name"]),
                    lat=z_lat,
                    lon=z_lon,
                    utc_offset_s=int(provider_offset) if provider_offset is not None else int(z_cfg["utc_offset_s"]),
                    alert_radius_km=z_radius,
                    local_alert_level=local_lvl,
                    weather_source=str(w_bundle["provider_name"]),
                    current=self._with_air_quality(w_bundle["current"], aqi_results[idx]),
                    rain_next_60m=w_bundle.get("rain_next_60m"),
                    daily_10d=list(w_bundle.get("daily_10d") or [])[:days_per_zone],
                    # the weather provider, and the air quality provider when its index was added to `current`
                    attribution=merge_credits(
                        w_bundle.get("attribution"), aqi_results[idx].attribution if aqi_results[idx] else None
                    ),
                )
            zones_out.append(zone_weather)

        selected_hazards = all_hazards[:limit_hazards]
        if compact:  # the 16 KiB profile carries no `near_places` (docs/decisions/0008-sensitive-points.md): ask `compact=false`
            selected_hazards = [h.model_copy(update={"near_places": None}) for h in selected_hazards]

        hazards_known = sources_ok > 0 or (self.simulation_active and bool(self.simulated_hazards))
        red_count = sum(1 for h in all_hazards if h.severity == SeverityLevel.RED)
        orange_count = sum(1 for h in all_hazards if h.severity == SeverityLevel.ORANGE)
        yellow_count = sum(1 for h in all_hazards if h.severity == SeverityLevel.YELLOW)

        max_sev: Optional[SeverityLevel] = None
        if hazards_known:
            max_sev = SeverityLevel.GREEN
            if red_count > 0:
                max_sev = SeverityLevel.RED
            elif orange_count > 0:
                max_sev = SeverityLevel.ORANGE
            elif yellow_count > 0:
                max_sev = SeverityLevel.YELLOW

        global_status = GlobalStatus(
            max_severity=max_sev,
            active_red_count=red_count if hazards_known else None,
            active_orange_count=orange_count if hazards_known else None,
            active_yellow_count=yellow_count if hazards_known else None,
            hazard_sources_ok=sources_ok,
            hazard_sources_total=sources_total,
            solar_kp=space_status.solar_kp,
            solar_storm_level=space_status.solar_storm_level,
            gps_degraded=space_status.gps_degraded,
            hf_radio_blackout_risk=space_status.hf_radio_blackout_risk,
            space_weather_attribution=space_status.attribution if space_status.solar_kp is not None else None,
            weather_fallback_active=any_fallback,
            zones_without_weather=without_weather,
        )

        snapshot = SnapshotResponse(
            version="1.1",
            server_utc=int(time.time()),
            device_type=device_type,
            compact=compact,
            simulation_active=self.simulation_active,
            global_status=global_status,
            zones=zones_out,
            hazards=selected_hazards,
        )
        try:
            self.storage.save_snapshot(snapshot)
        except Exception:
            pass
        return snapshot

    async def build_embedded_frame(
        self,
        device_type: DeviceType = DeviceType.WAVESHARE_7B,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        marine: bool = False,
        max_hazards: int = 12,
    ) -> str:
        """Build the compact text frame (``HEAD|...``, ``ZONE|...``, ``HAZ|...``, ``END|CRC32``)."""
        snapshot = await self.build_snapshot(
            device_type=device_type,
            max_hazards=max_hazards,
            compact=True,
        )
        point_resp: Optional[PointResponse] = None
        if lat is not None and lon is not None:
            point_resp = await self.build_point_response(
                lat=lat,
                lon=lon,
                marine=marine or device_type == DeviceType.MARINE,
                device_type=device_type,
                max_hazards=max_hazards,
                compact=True,
            )
        return serialize_embedded_frame(snapshot=snapshot, point=point_resp)

    async def build_geojson_feature_collection(self) -> GeoJSONFeatureCollection:
        """Build an IETF RFC 7946 ``FeatureCollection`` for GIS tools (MapLibre, Leaflet, QGIS)."""
        hazards = await self.collect_and_fuse_hazards()
        with_forecast = self.hazards_with_forecast()
        features: list[GeoJSONFeature] = []

        lons: list[float] = []
        lats: list[float] = []

        for h in hazards:
            lons.append(h.lon)
            lats.append(h.lat)
            coords = [h.lon, h.lat]
            if h.depth_km is not None:
                coords.append(h.depth_km)

            features.append(
                GeoJSONFeature(
                    id=h.id,
                    geometry=GeoJSONPointGeometry(coordinates=coords),
                    properties={
                        "category": h.category.value,
                        "severity": h.severity.value,
                        "title": h.title,
                        "source": h.source,
                        "sources": h.sources,
                        "timestamp_utc": h.timestamp_utc,
                        "value_primary": h.value_primary,
                        "value_unit": h.value_unit,
                        "depth_km": h.depth_km,
                        "tsunami_warning": h.tsunami_warning,
                        "tsunami_eta_minutes": h.tsunami_eta_minutes,
                        "nearest_zone_id": h.nearest_zone_id,
                        "nearest_zone_name": h.nearest_zone_name,
                        "distance_to_zone_km": h.distance_to_zone_km,
                        "bearing_deg": h.bearing_deg,
                        "bearing_cardinal": h.bearing_cardinal,
                        "has_forecast": h.id in with_forecast,
                        # computed by Vigie from its reference data and settings, named in `derived` (null: not worked out)
                        "near_places": None if h.near_places is None else [p.model_dump() for p in h.near_places],
                        "derived": h.derived,
                        "attribution": h.attribution,
                    },
                )
            )

        bbox = [min(lons), min(lats), max(lons), max(lats)] if lons and lats else None
        return GeoJSONFeatureCollection(
            bbox=bbox,
            metadata={
                "standard": "IETF RFC 7946 GeoJSON",
                "generated_utc": int(time.time()),
                "count": len(features),
                "simulation_active": self.simulation_active,
                "attribution": merge_credits(*(h.attribution for h in hazards)),
            },
            features=features,
        )

    async def events(
        self,
        country: Optional[str] = None,
        languages: Optional[tuple[str, ...]] = None,
        compact: bool = False,
        min_impact: Optional[int] = None,
        families: Optional[list[str]] = None,
    ) -> EventsResponse:
        """The official warnings grouped, the earthquakes in sequences and the other hazards, of a country or of the world (ADR 0012).

        Without ``country`` every country this instance serves is read (``VigilanceService.for_world``). The hazards that only the NWS
        reports are left out when the warnings of the United States are in the answer: they are the same alerts.
        """
        if country is not None:
            warnings = await self.vigilances.for_country(country, languages)
        else:
            warnings = await self.vigilances.for_world(languages)
        hazards, answered, asked = await self.collect_hazards_with_status()
        index = self.vigilances.countries

        def country_of(hazard: HazardAlert) -> Optional[str]:
            found = index.countries_for_point(hazard.lat, hazard.lon)
            return found[0] if len(found) == 1 else None

        if NWS_COUNTRY in warnings.countries:
            hazards = [h for h in hazards if set(h.sources or [h.source]) != {"NOAA_NWS"}]
        if country is not None:
            hazards = [h for h in hazards if country in index.countries_for_point(h.lat, h.lon)]
        messages = [v for v in warnings.vigilances if v.source == TSUNAMI_SOURCE]
        hazard_events, joined = group_hazards(hazards, country_of, messages)
        warning_events = group_warnings([v for v in warnings.vigilances if v.id not in joined], self.vigilances.box_of, index.name)
        found = sorted_events(hazard_events + warning_events)
        if min_impact is not None:
            found = [event for event in found if event.impact >= min_impact]
        if families:
            found = [event for event in found if event.family in families]
        if compact:
            found = [event.model_copy(update={"alerts": [], "hazards": []}) for event in found]
        return EventsResponse(
            server_utc=int(clock.wall()),
            query=EventsQuery(country=country, min_impact=min_impact, families=families, compact=compact),
            events=found,
            counts=count_events(found),
            coverage=warnings.coverage,
            hazard_sources_answered=answered,
            hazard_sources_total=asked,
            attribution=merge_credits(*(event.attribution for event in found)),
            disclaimer=warnings.disclaimer,
            note=EVENTS_NOTE,
        )

    async def watched_points_status(self, days: Optional[int] = None) -> WatchedPointsStatus:
        """What happened near each watched point in the last ``days`` days (7 by default), and whether "nothing" can be said."""
        window_days = parse_window_days(days)
        hazards, _, _ = await self.collect_hazards_with_status()  # reads the sources now, and notes which ones answered
        now_utc = int(clock.wall())
        points = list(self.watched_points)
        reads = self.source_reads()
        sources = [
            WatchedSource(
                provider_id=c.provider_id,
                name=c.name,
                answered=reads.answered.get(c.provider_id, False),
                observed_since_utc=reads.run_since(c.provider_id),
            )
            for c in self.hazard_collectors()
        ]
        history: list[HazardAlert] = []
        if points:
            positions = self.storage.hazard_positions_since(now_utc - window_days * 86400)
            history = self.storage.hazards_by_ids(candidate_ids(points, positions))
        active = [h for h in hazards if not h.id.startswith(SIMULATED_ID_PREFIX)]
        return build_status(points, history, active, sources, now_utc, window_days)

    async def earthquakes_near(self, query: EarthquakesQuery, limit: int) -> EarthquakesResponse:
        """The earthquakes within ``query.radius_km`` of a position in the last ``query.hours`` hours, strongest first.

        The hazards are read as every other route reads them (the feeds behind their cache, so no client adds a request to a
        provider), and the history the API kept gives the earthquakes the feeds no longer list. No weather provider is asked.
        """
        hazards, _, _ = await self.collect_hazards_with_status()
        now_utc = int(clock.wall())
        positions = self.storage.hazard_positions_since(now_utc - int(query.hours * 3600))
        history = self.storage.hazards_by_ids(ids_within(positions, query.lat, query.lon, query.radius_km))
        reads = self.source_reads()
        seismic = [self.usgs, self.emsc]
        sources = [
            WatchedSource(
                provider_id=c.provider_id,
                name=c.name,
                answered=reads.answered.get(c.provider_id, False),
                observed_since_utc=reads.run_since(c.provider_id),
            )
            for c in seismic
        ]
        credits = [credit_text(key) for c, key in zip(seismic, ("usgs", "emsc"), strict=True) if reads.answered.get(c.provider_id, False)]
        return build_earthquakes(query, hazards, history, sources, now_utc, limit, merge_credits(*credits))

    def hazards_with_forecast(self) -> set[str]:
        """The ids of the hazards that have a forecast to draw (``/v1/geojson/hazards/{id}/forecast``), from the last answers read."""
        ids = {f"nhc_{storm.id}" for storm in self.nhc.last_state.storms if has_storm_forecast(storm)}
        ids.update(event.hazard_id for event in self.tsunami.last_state.events if has_tsunami_forecast(event))
        return ids

    async def build_hazard_forecast(self, hazard_id: str) -> Optional[HazardForecast]:
        """The forecast of a hazard as GeoJSON; ``None`` when no hazard has this id, an empty forecast with a reason when it has none."""
        hazards = await self.collect_and_fuse_hazards()
        hazard = next((h for h in hazards if h.id == hazard_id), None)
        if hazard is None:
            return None
        storm = self.nhc.storm_for(hazard.id)
        if storm is not None:
            return storm_forecast(hazard, storm)
        event = self.tsunami.event_for(hazard.id)
        if event is not None:
            return tsunami_forecast(hazard, event)
        return empty_forecast(hazard)

    async def build_cap_feed(self) -> CAPFeedResponse:
        """Build the alert feed in the OASIS CAP 1.2 standard."""
        hazards = await self.collect_and_fuse_hazards()
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        cap_category_map: dict[HazardCategory, str] = {
            HazardCategory.EARTHQUAKE: "Geo",
            HazardCategory.TSUNAMI: "Geo",
            HazardCategory.VOLCANO: "Geo",
            HazardCategory.HURRICANE: "Met",
            HazardCategory.TORNADO: "Met",
            HazardCategory.FLOOD: "Met",
            HazardCategory.EXTREME_RAIN: "Met",
            HazardCategory.WILDFIRE: "Fire",
            HazardCategory.SOLAR_STORM: "Env",
        }

        cap_severity_map: dict[SeverityLevel, tuple[str, str, str]] = {
            SeverityLevel.RED: ("Extreme", "Immediate", "Take shelter immediately. Move away from the coast if a tsunami is possible."),
            SeverityLevel.ORANGE: ("Severe", "Expected", "Stay alert, prepare an emergency kit and follow local authority instructions."),
            SeverityLevel.YELLOW: ("Moderate", "Future", "Monitor how the situation develops."),
            SeverityLevel.GREEN: ("Minor", "Past", "No immediate action required; the event is being monitored."),
        }

        cap_alerts: list[CAPAlert] = []
        for h in hazards:
            cap_cat = cap_category_map.get(h.category, "Other")
            cap_sev, cap_urg, instruction = cap_severity_map[h.severity]
            certainty = "Observed" if len(h.sources) >= 1 else "Likely"
            event_iso = datetime.fromtimestamp(h.timestamp_utc, tz=timezone.utc).isoformat()

            desc_parts = [f"{h.title}."]
            if h.value_primary is not None:
                desc_parts.append(f"Value: {h.value_primary} {h.value_unit or ''}".rstrip() + ".")
            desc_parts.append(f"Reporting sources: {', '.join(h.sources or [h.source])}.")
            if h.tsunami_warning:
                eta_str = f" (estimated ETA: about {h.tsunami_eta_minutes} min)" if h.tsunami_eta_minutes else ""
                desc_parts.append(f"TSUNAMI WARNING ACTIVE{eta_str}.")
            if h.distance_to_zone_km is not None and h.bearing_cardinal is not None:
                desc_parts.append(
                    f"Position relative to {h.nearest_zone_name or 'the target'}: "
                    f"{h.distance_to_zone_km} km, bearing {h.bearing_deg} deg ({h.bearing_cardinal})."
                )
            if h.attribution:
                desc_parts.append(f"Attribution: {h.attribution}")  # last, no full stop that could read as part of a URL

            cap_alerts.append(
                CAPAlert(
                    identifier=h.id,
                    sent=now_iso,
                    status="Exercise" if self.simulation_active else "Actual",
                    msgType="Alert",
                    scope="Public",
                    info=[
                        CAPInfo(
                            category=cap_cat,  # type: ignore[arg-type]
                            event=h.category.value,
                            urgency=cap_urg,  # type: ignore[arg-type]
                            severity=cap_sev,  # type: ignore[arg-type]
                            certainty=certainty,  # type: ignore[arg-type]
                            effective=event_iso,
                            headline=h.title,
                            description=" ".join(desc_parts),
                            instruction=instruction,
                            parameters={
                                "value_primary": h.value_primary,
                                "value_unit": h.value_unit,
                                "tsunami_warning": h.tsunami_warning,
                                "tsunami_eta_minutes": h.tsunami_eta_minutes,
                                "sources": h.sources,
                                "bearing_deg": h.bearing_deg,
                                "bearing_cardinal": h.bearing_cardinal,
                                "attribution": h.attribution,
                            },
                            # The affected radius is not known: the area is the event position (radius 0).
                            area=[
                                CAPArea(
                                    areaDesc=h.title,
                                    circle=f"{h.lat:.4f},{h.lon:.4f} 0",
                                )
                            ],
                        )
                    ],
                )
            )

        return CAPFeedResponse(
            generated_at=now_iso,
            alert_count=len(cap_alerts),
            alerts=cap_alerts,
        )

    def trigger_simulation(self, req: SimulationRequest) -> SimulationResponse:
        """Start or stop a simulated scenario and record it in the history.

        Simulated alerts are always labelled: their title ends with ``(SIMULATION)``, the
        snapshot and the frame carry ``simulation_active`` and the CAP status is ``Exercise``.
        """
        now_utc = int(time.time())

        if req.scenario == SimulationScenario.CLEAR:
            self.simulation_active = False
            self.active_scenario = SimulationScenario.CLEAR
            self.simulated_hazards = []
            return SimulationResponse(
                status="cleared",
                scenario=SimulationScenario.CLEAR,
                simulation_active=False,
                injected_hazards_count=0,
                injected_hazards=[],
            )

        self.simulation_active = True
        self.active_scenario = req.scenario

        if req.scenario == SimulationScenario.MEGA_TSUNAMI_JAPAN:
            lat = req.target_lat if req.target_lat is not None else 38.10
            lon = req.target_lon if req.target_lon is not None else 142.85
            hazards = [
                HazardAlert(
                    id="sim_eq_sendai_m78",
                    category=HazardCategory.EARTHQUAKE,
                    severity=SeverityLevel.RED,
                    title="M 7.8 - Japan Trench off Sendai (SIMULATION)",
                    source="USGS+EMSC+GDACS",
                    sources=["USGS", "EMSC", "GDACS"],
                    timestamp_utc=now_utc,
                    lat=lat,
                    lon=lon,
                    value_primary=7.8,
                    value_unit="Mw",
                    depth_km=24.0,
                    tsunami_warning=True,
                ),
                HazardAlert(
                    id="sim_ts_pacific_red",
                    category=HazardCategory.TSUNAMI,
                    severity=SeverityLevel.RED,
                    title="Major tsunami warning - North-West Pacific coasts (SIMULATION)",
                    source="NOAA_NWS+GDACS",
                    sources=["NOAA_NWS", "GDACS"],
                    timestamp_utc=now_utc,
                    lat=lat - 0.15,
                    lon=lon - 0.25,
                    value_primary=6.5,
                    value_unit="m",
                    tsunami_warning=True,
                ),
            ]
        elif req.scenario == SimulationScenario.CAT5_HURRICANE_CARIBBEAN:
            lat = req.target_lat if req.target_lat is not None else 16.10
            lon = req.target_lon if req.target_lon is not None else -61.20
            hazards = [
                HazardAlert(
                    id="sim_tc_cat5_antilles",
                    category=HazardCategory.HURRICANE,
                    severity=SeverityLevel.RED,
                    title="Category 5 hurricane - approaching the Lesser Antilles (SIMULATION)",
                    source="NOAA_NWS+GDACS",
                    sources=["NOAA_NWS", "GDACS"],
                    timestamp_utc=now_utc,
                    lat=lat,
                    lon=lon,
                    value_primary=285.0,
                    value_unit="km/h",
                    tsunami_warning=False,
                )
            ]
        elif req.scenario == SimulationScenario.VOLCANO_ERUPTION_ICELAND:
            lat = req.target_lat if req.target_lat is not None else 63.98
            lon = req.target_lon if req.target_lon is not None else -22.45
            hazards = [
                HazardAlert(
                    id="sim_vo_reykjanes_red",
                    category=HazardCategory.VOLCANO,
                    severity=SeverityLevel.RED,
                    title="Explosive eruption and ash plume - Reykjanes peninsula (SIMULATION)",
                    source="GDACS+USGS_VOLCANO",
                    sources=["GDACS", "USGS_VOLCANO"],
                    timestamp_utc=now_utc,
                    lat=lat,
                    lon=lon,
                    value_primary=4.0,
                    value_unit="VEI",
                    tsunami_warning=False,
                )
            ]
        else:  # SEVERE_SQUALL_MARINE
            lat = req.target_lat if req.target_lat is not None else 47.65
            lon = req.target_lon if req.target_lon is not None else -3.50
            hazards = [
                HazardAlert(
                    id="sim_marine_squall_orange",
                    category=HazardCategory.EXTREME_RAIN,
                    severity=SeverityLevel.ORANGE,
                    title="Violent squall at sea and 5.4 m breaking swell (SIMULATION)",
                    source="Open-Meteo+NOAA_NWS",
                    sources=["Open-Meteo", "NOAA_NWS"],
                    timestamp_utc=now_utc,
                    lat=lat,
                    lon=lon,
                    value_primary=65.0,
                    value_unit="mm/h",
                    tsunami_warning=False,
                )
            ]

        self.simulated_hazards = [self._attach_nearest_monitored_zone(h) for h in hazards]
        try:
            self.storage.record_hazards(self.simulated_hazards)
        except Exception:
            pass
        return SimulationResponse(
            status="active",
            scenario=req.scenario,
            simulation_active=True,
            injected_hazards_count=len(self.simulated_hazards),
            injected_hazards=self.simulated_hazards,
        )

