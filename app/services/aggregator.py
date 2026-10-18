"""Orchestrateur Multi-Providers, Geofencing, Persistance SQLite WAL, Fallback Météo et Simulateur.

Responsabilités principales :
1. Compiler en parallèle les flux sismiques, volcaniques et catastrophes (`USGS`, `EMSC`, `GDACS`,
   `NOAA NWS`, `USGS Volcano / Smithsonian GVP`) et fusionner les doublons via `deduplicate_hazards`.
2. Assurer la continuité météo par coordonnées GPS (`lat, lon`) avec bascule automatique
   transparente : `Open-Meteo HD (minutely_15)` -> `MET Norway Locationforecast 2.0 (Fallback)`
   -> profil synthétique de continuité, complété par le radar `RainViewer`, l'hydrologie
   `Open-Meteo Flood (GloFAS)` et la qualité de l'air / poussières `Open-Meteo Air Quality`.
3. Persister en SQLite WAL (`SQLiteWALStorage`) la Watchlist dynamique (`/v1/zones`),
   l'historique des catastrophes (`/v1/history`) et le dernier snapshot valide (`snapshot_cache`).
4. Enrichir chaque catastrophe avec les calculs géodésiques (`distance_to_zone_km`,
   `bearing_deg`, `bearing_cardinal`, `tsunami_eta_minutes`).
5. Générer les représentations standards internationales (**IETF RFC 7946 GeoJSON**, **OASIS CAP 1.2**)
   ainsi que la **Trame Compacte Embarquée (`GET /v1/embedded/frame`)** protégée par `CRC32`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Optional

from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector
from app.models.schemas import (
    AirQualityStatus,
    CAPAlert,
    CAPArea,
    CAPFeedResponse,
    CAPInfo,
    CurrentWeather,
    DailyForecast,
    DeviceType,
    FloodRiskStatus,
    GPSCoordinates,
    GeoJSONFeature,
    GeoJSONFeatureCollection,
    GeoJSONPointGeometry,
    GlobalStatus,
    HazardAlert,
    HazardCategory,
    HistoryResponse,
    MarineConditions,
    MonitoredZoneConfig,
    MonitoredZoneUpsertRequest,
    PointResponse,
    ProviderHealth,
    RainNowcast,
    SEVERITY_RANK,
    SeverityLevel,
    SimulationRequest,
    SimulationResponse,
    SimulationScenario,
    SnapshotResponse,
    SpaceWeatherStatus,
    ZoneWeather,
)
from app.services.embedded_frame import serialize_embedded_frame
from app.services.geodesy import (
    deduplicate_hazards,
    enrich_hazard_for_point,
    haversine_km,
)
from app.services.storage_wal import DEFAULT_WATCHLIST_SEED, SQLiteWALStorage


DEFAULT_MONITORED_ZONES: list[dict[str, Any]] = list(DEFAULT_WATCHLIST_SEED)

DEVICE_DEFAULT_MAX_HAZARDS: dict[DeviceType, int] = {
    DeviceType.WAVESHARE_7B: 15,
    DeviceType.TAB5: 15,
    DeviceType.MARINE: 12,
    DeviceType.WEB: 50,
    DeviceType.GENERIC: 15,
}


class AggregatorService:
    """Orchestrateur central multi-providers et persistance SQLite WAL de Vigie Mondiale API."""

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
        flood: Optional[OpenMeteoFloodCollector] = None,
        air_quality: Optional[OpenMeteoAirQualityCollector] = None,
        swpc: Optional[NOAASWPCCollector] = None,
        storage: Optional[SQLiteWALStorage] = None,
        db_path: Optional[str | Path] = None,
    ) -> None:
        self.started_at_utc = int(time.time())
        self.usgs = usgs or USGSCollector()
        self.emsc = emsc or EMSCCollector()
        self.gdacs = gdacs or GDACSCollector()
        self.noaa_nws = noaa_nws or NOAANWSCollector()
        self.volcano = volcano or USGSVolcanoCollector()
        self.open_meteo = open_meteo or OpenMeteoCollector()
        self.met_norway = met_norway or MetNorwayCollector()
        self.rainviewer = rainviewer or RainViewerCollector()
        self.marine = marine or OpenMeteoMarineCollector()
        self.flood = flood or OpenMeteoFloodCollector()
        self.air_quality = air_quality or OpenMeteoAirQualityCollector()
        self.swpc = swpc or NOAASWPCCollector()

        self.storage = storage or SQLiteWALStorage(db_path=db_path)
        self.monitored_zones: list[dict[str, Any]] = [
            z.model_dump() for z in self.storage.list_zones()
        ]
        self.simulation_active: bool = False
        self.active_scenario: SimulationScenario = SimulationScenario.CLEAR
        self.simulated_hazards: list[HazardAlert] = []
        self.last_weather_fallback_used: bool = False

    def refresh_zones_from_storage(self) -> list[MonitoredZoneConfig]:
        """Recharge la liste des zones surveillées depuis SQLite WAL."""
        zones = self.storage.list_zones()
        self.monitored_zones = [z.model_dump() for z in zones]
        return zones

    def list_zones(self) -> list[MonitoredZoneConfig]:
        """Retourne les zones surveillées actives."""
        return self.refresh_zones_from_storage()

    def upsert_zone(self, req: MonitoredZoneUpsertRequest) -> MonitoredZoneConfig:
        """Crée ou met à jour une zone surveillée et rafraîchit le cache mémoire."""
        created = self.storage.upsert_zone(req)
        self.refresh_zones_from_storage()
        return created

    def delete_zone(self, zone_id: str) -> bool:
        """Supprime une zone surveillée et rafraîchit le cache mémoire."""
        deleted = self.storage.delete_zone(zone_id)
        self.refresh_zones_from_storage()
        return deleted

    def get_history(
        self,
        category: Optional[HazardCategory] = None,
        min_severity: Optional[SeverityLevel] = None,
        limit: int = 50,
    ) -> HistoryResponse:
        """Retourne l'historique SQLite WAL des catastrophes filtrées."""
        items = self.storage.query_history(category=category, min_severity=min_severity, limit=limit)
        return HistoryResponse(count=len(items), items=items)

    def get_all_collectors(self) -> list[Any]:
        """Retourne la liste ordonnée de tous les collecteurs gérés."""
        return [
            self.usgs,
            self.emsc,
            self.gdacs,
            self.noaa_nws,
            self.volcano,
            self.open_meteo,
            self.met_norway,
            self.rainviewer,
            self.marine,
            self.flood,
            self.air_quality,
            self.swpc,
        ]

    def get_providers_health(self) -> list[ProviderHealth]:
        """Retourne l'état de santé détaillé de chaque collecteur."""
        return [collector.get_health() for collector in self.get_all_collectors()]

    async def fetch_weather_with_fallback(
        self, lat: float, lon: float, days: int = 5
    ) -> tuple[dict[str, Any], bool]:
        """Récupère la météo d'un point GPS via Open-Meteo avec bascule automatique sur MET Norway."""
        try:
            bundle = await self.open_meteo.fetch_point(lat, lon, days=days, allow_stale_on_error=False)
            self.last_weather_fallback_used = False
            return bundle, False
        except Exception:
            pass

        try:
            bundle = await self.met_norway.fetch_point(lat, lon, days=days, allow_stale_on_error=True)
            self.last_weather_fallback_used = True
            return bundle, True
        except Exception:
            pass

        stale_om = self.open_meteo._get_stale_cache(f"om:{round(lat, 2):.2f},{round(lon, 2):.2f}:{days}")
        if stale_om is not None:
            self.last_weather_fallback_used = True
            return stale_om, True

        self.last_weather_fallback_used = True
        return self._build_offline_weather_bundle(lat, lon, days=days), True

    @staticmethod
    def _build_offline_weather_bundle(lat: float, lon: float, days: int = 5) -> dict[str, Any]:
        """Profil météo de secours hors-ligne basé sur la latitude."""
        abs_lat = abs(lat)
        base_temp = round(27.0 - 0.35 * abs_lat, 1)
        curr = CurrentWeather(
            temp_c=base_temp,
            feels_like_c=base_temp,
            humidity_pct=72,
            pressure_hpa=1013.2,
            wind_kmh=16.0,
            gust_kmh=24.0,
            wind_deg=240,
            rain_1h_mm=0.0,
            weather_code=2,
            uv_index=3.0,
            aqi_eu=20,
        )
        daily = [
            DailyForecast(
                d=day_name,
                code=2,
                tmin=round(base_temp - 4.0, 1),
                tmax=round(base_temp + 3.0, 1),
                rain_mm=0.0,
                wind_max=22.0,
            )
            for day_name in ("Lun", "Mar", "Mer", "Jeu", "Ven")[:days]
        ]
        return {
            "provider_name": "MET Norway / Offline Safety Cache",
            "utc_offset_s": int(round(lon / 15.0) * 3600),
            "current": curr,
            "rain_next_60m": [0.0, 0.0, 0.0, 0.0],
            "daily_10d": daily,
        }

    async def collect_and_fuse_hazards(self) -> list[HazardAlert]:
        """Collecte en parallèle USGS, EMSC, GDACS, NOAA NWS et USGS Volcano, déduplique et historise en SQLite WAL."""
        results = await asyncio.gather(
            self.usgs.fetch(),
            self.emsc.fetch(),
            self.gdacs.fetch(),
            self.noaa_nws.fetch(),
            self.volcano.fetch(),
            return_exceptions=True,
        )

        raw_hazards: list[HazardAlert] = []
        for res in results:
            if isinstance(res, list):
                raw_hazards.extend(res)

        if self.simulation_active and self.simulated_hazards:
            raw_hazards.extend(self.simulated_hazards)

        fused = deduplicate_hazards(raw_hazards)
        enriched = [self._attach_nearest_monitored_zone(h) for h in fused]
        try:
            self.storage.record_hazards(enriched)
        except Exception:
            pass
        return enriched

    def _attach_nearest_monitored_zone(self, hazard: HazardAlert) -> HazardAlert:
        """Associe à une alerte la zone surveillée la plus proche, sa distance, son cap et son ETA tsunami."""
        if not self.monitored_zones:
            return hazard

        best_zone: Optional[dict[str, Any]] = None
        best_dist: float = float("inf")
        for z in self.monitored_zones:
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

    async def get_space_weather_safe(self) -> SpaceWeatherStatus:
        """Récupère la météo spatiale NOAA SWPC sans risque d'exception."""
        try:
            status = await self.swpc.fetch()
            if self.simulation_active and self.active_scenario == SimulationScenario.MEGA_TSUNAMI_JAPAN:
                return SpaceWeatherStatus(
                    solar_kp=max(status.solar_kp, 6.3),
                    solar_storm_level="G2",
                    gps_degraded=True,
                    hf_radio_blackout_risk=True,
                )
            return status
        except Exception:
            return SpaceWeatherStatus(
                solar_kp=2.3,
                solar_storm_level="G0",
                gps_degraded=False,
                hf_radio_blackout_risk=False,
            )

    async def get_flood_risk_safe(self, lat: float, lon: float) -> FloodRiskStatus:
        """Récupère le risque hydrologique GloFAS aux coordonnées (`lat, lon`) sans risque d'échec."""
        try:
            return await self.flood.fetch_flood_risk(lat, lon)
        except Exception:
            return FloodRiskStatus()

    async def get_air_quality_safe(self, lat: float, lon: float) -> AirQualityStatus:
        """Récupère la qualité de l'air, l'UV et les poussières/cendres aux coordonnées (`lat, lon`)."""
        try:
            status = await self.air_quality.fetch_air_quality(lat, lon)
            if self.simulation_active and self.active_scenario == SimulationScenario.VOLCANO_ERUPTION_ICELAND:
                return AirQualityStatus(
                    european_aqi=max(status.european_aqi, 145),
                    us_aqi=max(status.us_aqi, 180),
                    pm2_5_ugm3=max(status.pm2_5_ugm3, 65.0),
                    pm10_ugm3=max(status.pm10_ugm3, 190.0),
                    uv_index=status.uv_index,
                    dust_ugm3=max(status.dust_ugm3, 240.0),
                    ash_or_dust_warning=True,
                )
            return status
        except Exception:
            return AirQualityStatus()

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
    ) -> PointResponse:
        """Construit la réponse hyper-locale `/v1/point` pour un équipement GPS (Bateau, M5Stack Tab5, Véhicule)."""
        include_marine = bool(marine or device_type == DeviceType.MARINE)
        days_count = 5 if compact else 10
        limit_hazards = max_hazards or DEVICE_DEFAULT_MAX_HAZARDS.get(device_type, 15)

        weather_task = self.fetch_weather_with_fallback(lat, lon, days=days_count)
        hazards_task = self.collect_and_fuse_hazards()
        space_task = self.get_space_weather_safe()
        flood_task = self.get_flood_risk_safe(lat, lon)
        aqi_task = self.get_air_quality_safe(lat, lon)
        radar_task = self.rainviewer.fetch()
        marine_task = self.marine.fetch_marine(lat, lon) if include_marine else None

        (weather_bundle, fallback_used), all_hazards, space_status, flood_status, aqi_status = await asyncio.gather(
            weather_task, hazards_task, space_task, flood_task, aqi_task
        )

        try:
            radar_meta = await radar_task
        except Exception:
            radar_meta = {
                "radar_timestamp_utc": int(time.time()),
                "radar_tile_url_template": "https://tilecache.rainviewer.com/v2/radar/nowcast/256/{z}/{x}/{y}/2/1_1.png",
            }

        marine_conditions: Optional[MarineConditions] = None
        if marine_task is not None:
            try:
                marine_conditions = await marine_task
            except Exception:
                marine_conditions = MarineConditions(
                    wave_height_m=1.2,
                    wave_direction_deg=260,
                    wave_period_s=8.5,
                    swell_wave_height_m=1.0,
                    swell_wave_direction_deg=265,
                    swell_wave_period_s=10.0,
                    ocean_current_velocity_kmh=1.1,
                    ocean_current_direction_deg=90,
                    sea_state_label="MODERATE",
                    navigation_warning=False,
                )

            if self.simulation_active and self.active_scenario == SimulationScenario.SEVERE_SQUALL_MARINE:
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
                )

        enriched_nearby: list[HazardAlert] = []
        local_level = SeverityLevel.GREEN

        for h in all_hazards:
            enriched = enrich_hazard_for_point(
                h, ref_lat=lat, ref_lon=lon, zone_id="gps_point", zone_name="Position GPS"
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

        rain_steps: list[float] = list(weather_bundle.get("rain_next_60m") or [0.0, 0.0, 0.0, 0.0])
        max_mm_h = round(max(rain_steps) * 4.0, 2) if rain_steps else 0.0
        rain_nowcast = RainNowcast(
            interval_minutes=15,
            mm_per_step=rain_steps,
            rain_expected_60m=any(v > 0.05 for v in rain_steps),
            max_mm_h=max_mm_h,
            radar_tile_url_template=radar_meta.get("radar_tile_url_template"),
            radar_timestamp_utc=radar_meta.get("radar_timestamp_utc"),
        )

        if marine_conditions and marine_conditions.navigation_warning:
            if SEVERITY_RANK[SeverityLevel.ORANGE] > SEVERITY_RANK[local_level]:
                local_level = SeverityLevel.ORANGE

        if SEVERITY_RANK[flood_status.flood_alert_level] > SEVERITY_RANK[local_level]:
            local_level = flood_status.flood_alert_level

        if aqi_status.ash_or_dust_warning and SEVERITY_RANK[SeverityLevel.ORANGE] > SEVERITY_RANK[local_level]:
            local_level = SeverityLevel.ORANGE

        current_weather: CurrentWeather = weather_bundle["current"].model_copy(
            update={
                "aqi_eu": aqi_status.european_aqi,
                "uv_index": aqi_status.uv_index or weather_bundle["current"].uv_index,
            }
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
            weather_provider=str(weather_bundle.get("provider_name") or "Open-Meteo"),
            fallback_used=fallback_used,
            local_alert_level=local_level,
            current=current_weather,
            rain_nowcast=rain_nowcast,
            marine=marine_conditions,
            flood_risk=flood_status,
            air_quality=aqi_status,
            space_weather=space_status,
            nearby_hazards=enriched_nearby,
        )

    async def build_snapshot(
        self,
        device_type: DeviceType = DeviceType.WAVESHARE_7B,
        max_hazards: Optional[int] = None,
        compact: bool = True,
    ) -> SnapshotResponse:
        """Construit le snapshot global (< 12 Ko en mode compact) et le sauvegarde dans SQLite WAL."""
        days_per_zone = 5 if compact else 10
        limit_hazards = max_hazards or DEVICE_DEFAULT_MAX_HAZARDS.get(device_type, 15)

        hazards_task = self.collect_and_fuse_hazards()
        space_task = self.get_space_weather_safe()
        zone_weather_tasks = [
            self.fetch_weather_with_fallback(float(z["lat"]), float(z["lon"]), days=days_per_zone)
            for z in self.monitored_zones
        ]

        all_hazards, space_status, *zone_results = await asyncio.gather(
            hazards_task, space_task, *zone_weather_tasks
        )

        any_fallback = False
        zones_out: list[ZoneWeather] = []

        for idx, z_cfg in enumerate(self.monitored_zones):
            w_bundle, fb_used = zone_results[idx]
            any_fallback = any_fallback or fb_used

            z_lat = float(z_cfg["lat"])
            z_lon = float(z_cfg["lon"])
            z_radius = float(z_cfg.get("alert_radius_km", 500.0))

            local_lvl = SeverityLevel.GREEN
            for h in all_hazards:
                dist = haversine_km(z_lat, z_lon, h.lat, h.lon)
                if dist <= z_radius and SEVERITY_RANK[h.severity] > SEVERITY_RANK[local_lvl]:
                    local_lvl = h.severity

            zones_out.append(
                ZoneWeather(
                    id=str(z_cfg["id"]),
                    name=str(z_cfg["name"]),
                    lat=z_lat,
                    lon=z_lon,
                    utc_offset_s=int(w_bundle.get("utc_offset_s", z_cfg.get("utc_offset_s", 0))),
                    alert_radius_km=z_radius,
                    local_alert_level=local_lvl,
                    weather_source="MET Norway" if fb_used else "Open-Meteo",
                    current=w_bundle["current"],
                    rain_next_60m=list(w_bundle.get("rain_next_60m") or [0.0, 0.0, 0.0, 0.0]),
                    daily_10d=list(w_bundle.get("daily_10d") or [])[:days_per_zone],
                )
            )

        selected_hazards = all_hazards[:limit_hazards]

        red_count = sum(1 for h in all_hazards if h.severity == SeverityLevel.RED)
        orange_count = sum(1 for h in all_hazards if h.severity == SeverityLevel.ORANGE)
        yellow_count = sum(1 for h in all_hazards if h.severity == SeverityLevel.YELLOW)

        max_sev = SeverityLevel.GREEN
        if red_count > 0:
            max_sev = SeverityLevel.RED
        elif orange_count > 0:
            max_sev = SeverityLevel.ORANGE
        elif yellow_count > 0:
            max_sev = SeverityLevel.YELLOW

        global_status = GlobalStatus(
            max_severity=max_sev,
            active_red_count=red_count,
            active_orange_count=orange_count,
            active_yellow_count=yellow_count,
            solar_kp=space_status.solar_kp,
            solar_storm_level=space_status.solar_storm_level,
            gps_degraded=space_status.gps_degraded,
            hf_radio_blackout_risk=space_status.hf_radio_blackout_risk,
            weather_fallback_active=any_fallback,
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
        """Construit la trame texte compacte (`HEAD|...`, `GPS|...`, `ZONE|...`, `HAZ|...`, `END|CRC32`)."""
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
        """Construit une `FeatureCollection` standard IETF RFC 7946 pour SIG / MapLibre / Leaflet."""
        hazards = await self.collect_and_fuse_hazards()
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
            },
            features=features,
        )

    async def build_cap_feed(self) -> CAPFeedResponse:
        """Construit le flux d'alertes normalisé selon le standard international OASIS CAP 1.2."""
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
            SeverityLevel.RED: ("Extreme", "Immediate", "Mise à l'abri immédiate / Évacuation des zones côtières si risque tsunami."),
            SeverityLevel.ORANGE: ("Severe", "Expected", "Vigilance renforcée, préparez votre kit d'urgence et suivez les consignes locales."),
            SeverityLevel.YELLOW: ("Moderate", "Future", "Soyez attentif à l'évolution de la situation météorologique ou sismique."),
            SeverityLevel.GREEN: ("Minor", "Past", "Aucune action immédiate requise, phénomène sous surveillance."),
        }

        cap_alerts: list[CAPAlert] = []
        for h in hazards:
            cap_cat = cap_category_map.get(h.category, "Other")
            cap_sev, cap_urg, instruction = cap_severity_map[h.severity]
            certainty = "Observed" if len(h.sources) >= 1 else "Likely"
            event_iso = datetime.fromtimestamp(h.timestamp_utc, tz=timezone.utc).isoformat()
            radius_km = 300 if h.severity == SeverityLevel.RED else 150

            desc_parts = [
                f"{h.title} — Valeur : {h.value_primary} {h.value_unit}.",
                f"Sources confirmantes : {', '.join(h.sources or [h.source])}.",
            ]
            if h.tsunami_warning:
                eta_str = f" (ETA estimé : ~{h.tsunami_eta_minutes} min)" if h.tsunami_eta_minutes else ""
                desc_parts.append(f"ALERTE TSUNAMI ACTIVE{eta_str}.")
            if h.distance_to_zone_km is not None and h.bearing_cardinal is not None:
                desc_parts.append(
                    f"Position relative à {h.nearest_zone_name or 'la cible'} : "
                    f"{h.distance_to_zone_km} km cap {h.bearing_deg}° ({h.bearing_cardinal})."
                )

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
                            },
                            area=[
                                CAPArea(
                                    areaDesc=h.title,
                                    circle=f"{h.lat:.4f},{h.lon:.4f} {radius_km}",
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
        """Active ou désactive un scénario de crise réaliste et l'historise dans SQLite WAL."""
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
                    title="M 7.8 - Fosse du Japon au large de Sendai (SIMULATION)",
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
                    title="Alerte Tsunami Majeur - Côtes Pacifique Nord-Ouest (SIMULATION)",
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
                    title="Ouragan Majeur Catégorie 5 - Approche Arc Antillais (SIMULATION)",
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
                    title="Éruption Explosive & Panache de Cendres - Péninsule de Reykjanes (SIMULATION)",
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
                    title="Grain Orageux Violent en Mer & Houle Déferlante 5.4m (SIMULATION)",
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
