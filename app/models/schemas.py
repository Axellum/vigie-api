"""Modèles Pydantic v2 pour Vigie Mondiale API (alerte_mondiale_api).

Couvre :
1. Le profil embarqué ultra-compact (< 12 Ko) pour l'ESP32-P4 Waveshare 7B (/v1/snapshot).
2. Le profil GPS mobile & marine (/v1/point) avec coordonnées exactes, SOG/COG,
   précipitations aux 15 min (Nowcast), radar RainViewer, houle/vagues/courants.
3. Le standard international IETF RFC 7946 GeoJSON (/v1/geojson/hazards).
4. Le standard international OASIS CAP 1.2 Common Alerting Protocol (/v1/cap/alerts).
5. La télémétrie de santé des collecteurs et de leur Circuit Breaker (/v1/providers).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class SeverityLevel(str, Enum):
    """Niveaux de sévérité unifiés (DEFCON / Vigilance)."""

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


SEVERITY_RANK: dict[SeverityLevel, int] = {
    SeverityLevel.GREEN: 0,
    SeverityLevel.YELLOW: 1,
    SeverityLevel.ORANGE: 2,
    SeverityLevel.RED: 3,
}


class HazardCategory(str, Enum):
    """Catégories normalisées de catastrophes naturelles mondiales."""

    EARTHQUAKE = "EARTHQUAKE"
    TSUNAMI = "TSUNAMI"
    VOLCANO = "VOLCANO"
    HURRICANE = "HURRICANE"
    TORNADO = "TORNADO"
    FLOOD = "FLOOD"
    EXTREME_RAIN = "EXTREME_RAIN"
    WILDFIRE = "WILDFIRE"
    SOLAR_STORM = "SOLAR_STORM"


class CircuitState(str, Enum):
    """États du disjoncteur (Circuit Breaker) d'un collecteur externe."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ProviderHealth(BaseModel):
    """État de santé temps réel d'un fournisseur de données externe."""

    provider_id: str = Field(..., description="Identifiant unique du collecteur")
    name: str = Field(..., description="Nom lisible de l'agence ou API")
    domain: str = Field(..., description="Domaine métier (seismic, disasters, weather, marine, space)")
    is_fallback: bool = Field(default=False, description="Indique s'il s'agit d'un provider de secours")
    healthy: bool = Field(..., description="Disponibilité opérationnelle (circuit fermé ou cache valide)")
    circuit_state: CircuitState = Field(default=CircuitState.CLOSED, description="État du Circuit Breaker")
    latency_ms: float = Field(default=0.0, description="Latence moyenne exponentielle en millisecondes")
    consecutive_failures: int = Field(default=0, description="Nombre d'échecs consécutifs")
    last_success_utc: Optional[int] = Field(default=None, description="Timestamp UTC du dernier succès")
    cache_age_s: Optional[int] = Field(default=None, description="Âge des données en cache (secondes)")
    last_error: Optional[str] = Field(default=None, description="Dernier message d'erreur éventuel")


class GPSCoordinates(BaseModel):
    """Coordonnées GPS et vecteur cinématique (Mode Bateau / Véhicule)."""

    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude WGS84 en degrés décimaux")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude WGS84 en degrés décimaux")
    sog_knots: Optional[float] = Field(
        default=None, ge=0.0, description="Speed Over Ground (vitesse fond en nœuds)"
    )
    cog_deg: Optional[float] = Field(
        default=None, ge=0.0, lt=360.0, description="Course Over Ground (route fond en degrés)"
    )


class CurrentWeather(BaseModel):
    """Conditions météorologiques courantes normalisées."""

    temp_c: float = Field(..., description="Température à 2m (°C)")
    feels_like_c: float = Field(..., description="Température ressentie (°C)")
    humidity_pct: int = Field(..., ge=0, le=100, description="Humidité relative (%)")
    pressure_hpa: float = Field(..., description="Pression atmosphérique au niveau de la mer (hPa)")
    wind_kmh: float = Field(..., ge=0.0, description="Vitesse du vent à 10m (km/h)")
    gust_kmh: float = Field(..., ge=0.0, description="Rafales de vent à 10m (km/h)")
    wind_deg: int = Field(..., ge=0, le=360, description="Direction du vent (degrés)")
    rain_1h_mm: float = Field(..., ge=0.0, description="Précipitations sur l'heure courante (mm)")
    weather_code: int = Field(..., description="Code météo OMM / WMO standard")
    uv_index: float = Field(default=0.0, ge=0.0, description="Indice UV")
    aqi_eu: int = Field(default=20, ge=0, description="Indice de qualité de l'air européen")


class DailyForecast(BaseModel):
    """Prévision synthétique journalière (optimisée pour le contrat < 12 Ko)."""

    d: str = Field(..., description="Jour abrégé (ex: Lun, Mar)")
    code: int = Field(..., description="Code météo WMO dominant")
    tmin: float = Field(..., description="Température minimale (°C)")
    tmax: float = Field(..., description="Température maximale (°C)")
    rain_mm: float = Field(default=0.0, ge=0.0, description="Cumul de pluie journalier (mm)")
    wind_max: float = Field(default=0.0, ge=0.0, description="Vent maximal journalier (km/h)")


class RainNowcast(BaseModel):
    """Prévision de pluie hyper-locale à 60 minutes (pas de 15 min) + Radar RainViewer."""

    interval_minutes: int = Field(default=15, description="Pas temporel en minutes")
    mm_per_step: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0],
        description="Précipitations (mm) par tranche de 15 minutes sur la prochaine heure",
    )
    rain_expected_60m: bool = Field(default=False, description="Pluie attendue dans les 60 prochaines minutes")
    max_mm_h: float = Field(default=0.0, ge=0.0, description="Intensité maximale équivalente (mm/h)")
    radar_tile_url_template: Optional[str] = Field(
        default=None, description="Modèle d'URL de tuiles radar RainViewer (slippy map)"
    )
    radar_timestamp_utc: Optional[int] = Field(
        default=None, description="Horodatage UTC de la dernière image radar RainViewer"
    )


class MarineConditions(BaseModel):
    """Conditions océanographiques et marines aux coordonnées GPS exactes."""

    wave_height_m: float = Field(default=0.0, ge=0.0, description="Hauteur significative des vagues (m)")
    wave_direction_deg: int = Field(default=0, ge=0, le=360, description="Direction des vagues (°)")
    wave_period_s: float = Field(default=0.0, ge=0.0, description="Période des vagues (s)")
    swell_wave_height_m: float = Field(default=0.0, ge=0.0, description="Hauteur de la houle primaire (m)")
    swell_wave_direction_deg: int = Field(default=0, ge=0, le=360, description="Direction de la houle (°)")
    swell_wave_period_s: float = Field(default=0.0, ge=0.0, description="Période de la houle (s)")
    ocean_current_velocity_kmh: float = Field(
        default=0.0, ge=0.0, description="Vitesse du courant océanique de surface (km/h)"
    )
    ocean_current_direction_deg: int = Field(
        default=0, ge=0, le=360, description="Direction du courant océanique (°)"
    )
    sea_state_label: Literal["CALM", "SMOOTH", "MODERATE", "ROUGH", "VERY_ROUGH", "HIGH", "PHENOMENAL"] = Field(
        default="CALM", description="État de la mer selon l'échelle Douglas simplifiée"
    )
    navigation_warning: bool = Field(
        default=False, description="Alerte de navigation active (vagues >= 3.5m ou courant violent)"
    )


class SpaceWeatherStatus(BaseModel):
    """État de la météo spatiale (NOAA SWPC) pour la fiabilité GPS et radio HF en mer."""

    solar_kp: float = Field(default=2.0, ge=0.0, le=9.0, description="Indice géomagnétique planétaire Kp")
    solar_storm_level: Literal["G0", "G1", "G2", "G3", "G4", "G5"] = Field(
        default="G0", description="Échelle NOAA de tempête géomagnétique"
    )
    gps_degraded: bool = Field(
        default=False, description="Risque de dégradation de précision GNSS/GPS (Kp >= 6 / G2+)"
    )
    hf_radio_blackout_risk: bool = Field(
        default=False, description="Risque de perturbation des communications radio HF (Kp >= 5 / G1+)"
    )


class HazardAlert(BaseModel):
    """Alerte catastrophe naturelle normalisée, enrichie et dédupliquée."""

    id: str = Field(..., description="Identifiant unique de l'alerte")
    category: HazardCategory = Field(..., description="Catégorie de catastrophe")
    severity: SeverityLevel = Field(..., description="Niveau de sévérité (GREEN, YELLOW, ORANGE, RED)")
    title: str = Field(..., description="Titre synthétique localisé")
    source: str = Field(..., description="Chaîne des sources combinées (ex: USGS+EMSC+GDACS)")
    sources: list[str] = Field(
        default_factory=list, description="Liste explicite des agences confirmant l'événement"
    )
    timestamp_utc: int = Field(..., description="Horodatage UTC de l'événement (secondes epoch)")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude de l'épicentre / phénomène")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude de l'épicentre / phénomène")
    value_primary: float = Field(..., description="Valeur physique principale (Magnitude, Vent km/h, Kp...)")
    value_unit: str = Field(..., description="Unité de la valeur principale (Mw, km/h, m, Kp, VEI)")
    depth_km: Optional[float] = Field(default=None, description="Profondeur hypocentrale pour un séisme (km)")
    tsunami_warning: bool = Field(default=False, description="Alerte ou potentiel tsunami associé")
    tsunami_eta_minutes: Optional[int] = Field(
        default=None, description="Temps d'arrivée estimé de l'onde tsunami à la cible (minutes)"
    )
    nearest_zone_id: Optional[str] = Field(default=None, description="Identifiant de la zone surveillée la plus proche")
    nearest_zone_name: Optional[str] = Field(default=None, description="Nom de la zone surveillée la plus proche")
    distance_to_zone_km: Optional[float] = Field(
        default=None, description="Distance orthodromique Haversine jusqu'à la cible (km)"
    )
    bearing_deg: Optional[float] = Field(
        default=None, description="Relèvement initial depuis la cible vers la catastrophe (0-360°)"
    )
    bearing_cardinal: Optional[str] = Field(
        default=None, description="Direction cardinale depuis la cible vers la catastrophe (N, NE, E...)"
    )


class ZoneWeather(BaseModel):
    """État météo et niveau d'alerte d'une zone géographique surveillée."""

    id: str = Field(..., description="Identifiant court de la zone (ex: z0)")
    name: str = Field(..., description="Nom de la ville ou zone maritime")
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    utc_offset_s: int = Field(default=0, description="Décalage horaire par rapport à UTC (secondes)")
    alert_radius_km: float = Field(default=500.0, description="Rayon de surveillance géodésique (km)")
    local_alert_level: SeverityLevel = Field(
        default=SeverityLevel.GREEN, description="Niveau d'alerte maximal impactant cette zone"
    )
    weather_source: str = Field(default="Open-Meteo", description="Fournisseur ayant servi la météo de la zone")
    current: CurrentWeather
    rain_next_60m: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0],
        description="Pluie prévue par pas de 15 min sur la prochaine heure (mm)",
    )
    daily_10d: list[DailyForecast] = Field(
        default_factory=list, description="Prévisions quotidiennes sur 10 jours"
    )


class GlobalStatus(BaseModel):
    """Synthèse mondiale DEFCON et météo spatiale."""

    max_severity: SeverityLevel = Field(default=SeverityLevel.GREEN)
    active_red_count: int = Field(default=0, ge=0)
    active_orange_count: int = Field(default=0, ge=0)
    active_yellow_count: int = Field(default=0, ge=0)
    solar_kp: float = Field(default=2.0, ge=0.0, le=9.0)
    solar_storm_level: str = Field(default="G0")
    gps_degraded: bool = Field(default=False)
    hf_radio_blackout_risk: bool = Field(default=False)
    weather_fallback_active: bool = Field(
        default=False, description="Indique si le filet de sécurité MET Norway a pris le relais"
    )


class DeviceType(str, Enum):
    """Profils matériels de la gamme d'écrans et clients Vigie Mondiale."""

    WAVESHARE_7B = "waveshare-7b"  # Terminal 7" 1024x600 ESP32-P4 fixe/bureau
    TAB5 = "tab5"                  # M5Stack Tab5 5" 1280x720 ESP32-P4 sur batterie + GPS Grove
    MARINE = "marine"              # Terminal navigation bateau avec GPS/NMEA + houle/courants
    WEB = "web"                    # Client Web / Salle de crise / Mur d'écrans
    GENERIC = "generic"            # Autres formats d'écrans embarqués


class SnapshotResponse(BaseModel):
    """Payload ultra-compact (< 12 Ko) multi-écrans (Waveshare 7B, M5Stack Tab5, Marine, Web)."""

    version: str = Field(default="1.1")
    server_utc: int = Field(..., description="Timestamp UTC du serveur")
    device_type: DeviceType = Field(
        default=DeviceType.WAVESHARE_7B,
        description="Profil matériel servi (waveshare-7b, tab5, marine, web, generic)",
    )
    compact: bool = Field(
        default=True,
        description="Indique si le profil compact (< 12 Ko sans allocation dynamique) est appliqué",
    )
    simulation_active: bool = Field(default=False, description="Indique si un scénario simulé est actif")
    global_status: GlobalStatus
    zones: list[ZoneWeather]
    hazards: list[HazardAlert]


class FloodRiskStatus(BaseModel):
    """Risque hydrologique et crue fluviale (GloFAS Copernicus via Open-Meteo Flood API)."""

    river_discharge_m3s: float = Field(default=0.0, ge=0.0, description="Débit fluvial actuel (m³/s)")
    river_discharge_mean_m3s: float = Field(default=0.0, ge=0.0, description="Débit moyen historique (m³/s)")
    river_discharge_max_m3s: float = Field(default=0.0, ge=0.0, description="Débit maximal prévu sur 10j (m³/s)")
    anomaly_ratio: float = Field(default=1.0, ge=0.0, description="Ratio débit_max / débit_moyen")
    flood_alert_level: SeverityLevel = Field(
        default=SeverityLevel.GREEN, description="Niveau d'alerte crue/inondation"
    )


class AirQualityStatus(BaseModel):
    """Qualité de l'air, indice UV et panaches de poussières/cendres (Open-Meteo Air Quality API)."""

    european_aqi: int = Field(default=20, ge=0, description="Indice européen de qualité de l'air (EAQI)")
    us_aqi: int = Field(default=25, ge=0, description="Indice américain de qualité de l'air (US AQI)")
    pm2_5_ugm3: float = Field(default=5.0, ge=0.0, description="Particules fines PM2.5 (µg/m³)")
    pm10_ugm3: float = Field(default=12.0, ge=0.0, description="Particules PM10 (µg/m³)")
    uv_index: float = Field(default=3.0, ge=0.0, description="Indice UV")
    dust_ugm3: float = Field(
        default=0.0, ge=0.0, description="Concentration de poussières désertiques / cendres volcaniques (µg/m³)"
    )
    ash_or_dust_warning: bool = Field(
        default=False, description="Alerte panache de poussières/cendres (dust >= 100 µg/m³ ou PM10 >= 120 µg/m³)"
    )


class MonitoredZoneConfig(BaseModel):
    """Configuration persistante d'une zone géographique surveillée (Watchlist SQLite WAL)."""

    id: str = Field(..., description="Identifiant unique de la zone (ex: z0, z5)")
    name: str = Field(..., min_length=1, max_length=64, description="Nom lisible de la zone ou du port")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude WGS84")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude WGS84")
    utc_offset_s: int = Field(default=0, description="Décalage horaire par rapport à UTC (secondes)")
    alert_radius_km: float = Field(default=500.0, gt=0.0, le=5000.0, description="Rayon d'alerte géodésique (km)")
    is_marine: bool = Field(default=False, description="Zone maritime / côtière avec surveillance houle")


class MonitoredZoneUpsertRequest(BaseModel):
    """Requête d'ajout ou de modification d'une zone surveillée (`POST /v1/zones`)."""

    id: Optional[str] = Field(default=None, description="Identifiant optionnel (auto-généré si absent)")
    name: str = Field(..., min_length=1, max_length=64, description="Nom de la ville, île ou zone maritime")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude WGS84")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude WGS84")
    utc_offset_s: int = Field(default=0, description="Décalage horaire UTC (secondes)")
    alert_radius_km: float = Field(default=500.0, gt=0.0, le=5000.0, description="Rayon de surveillance (km)")
    is_marine: bool = Field(default=False, description="Indique s'il s'agit d'une zone maritime")


class HistoryResponse(BaseModel):
    """Réponse paginée de l'historique SQLite WAL des catastrophes mondiales (`GET /v1/history`)."""

    count: int
    items: list[HazardAlert]


class PointResponse(BaseModel):
    """Payload complet pour une coordonnée GPS exacte / Bateau / Tab5 GPS Grove (/v1/point)."""

    version: str = Field(default="1.1")
    server_utc: int
    device_type: DeviceType = Field(
        default=DeviceType.TAB5,
        description="Profil matériel servi (tab5 avec GPS Grove, marine, waveshare-7b, web)",
    )
    compact: bool = Field(default=True, description="Indique si le filtrage compact embarqué est actif")
    coordinates: GPSCoordinates
    weather_provider: str = Field(..., description="Provider météo utilisé (Open-Meteo ou MET Norway Fallback)")
    fallback_used: bool = Field(default=False, description="True si le fallback MET Norway a été utilisé")
    local_alert_level: SeverityLevel = Field(default=SeverityLevel.GREEN)
    current: CurrentWeather
    rain_nowcast: RainNowcast
    marine: Optional[MarineConditions] = Field(
        default=None, description="Conditions marines (présent si marine=true ou device_type=marine)"
    )
    flood_risk: Optional[FloodRiskStatus] = Field(
        default=None, description="Risque hydrologique et débit fluvial GloFAS Copernicus"
    )
    air_quality: Optional[AirQualityStatus] = Field(
        default=None, description="Qualité de l'air, UV et poussières/cendres"
    )
    space_weather: SpaceWeatherStatus
    nearby_hazards: list[HazardAlert] = Field(
        default_factory=list,
        description="Catastrophes mondiales filtrées et enrichies par rapport à ce point GPS",
    )


# ============================================================================
# Standard IETF RFC 7946 GeoJSON (/v1/geojson/hazards)
# ============================================================================


class GeoJSONPointGeometry(BaseModel):
    """Géométrie Point conforme RFC 7946 §3.1.2 ([lon, lat] ou [lon, lat, elev])."""

    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(
        ..., min_length=2, max_length=3, description="[longitude, latitude, elevation_or_depth]"
    )


class GeoJSONFeature(BaseModel):
    """Objet Feature conforme RFC 7946 §3.2."""

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: GeoJSONPointGeometry
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    """Collection de Features conforme IETF RFC 7946 §3.3."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    bbox: Optional[list[float]] = Field(
        default=None, description="Bounding Box [min_lon, min_lat, max_lon, max_lat]"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    features: list[GeoJSONFeature] = Field(default_factory=list)


# ============================================================================
# Standard OASIS CAP 1.2 — Common Alerting Protocol (/v1/cap/alerts)
# ============================================================================


class CAPArea(BaseModel):
    """Élément <area> du standard OASIS CAP 1.2."""

    areaDesc: str = Field(..., description="Description textuelle de la zone géographique affectée")
    circle: str = Field(..., description="Cercle au format 'lat,lon radius_km' (spécification CAP 1.2)")


class CAPInfo(BaseModel):
    """Élément <info> du standard OASIS CAP 1.2."""

    language: str = Field(default="fr-FR")
    category: Literal["Geo", "Met", "Safety", "Security", "Rescue", "Fire", "Health", "Env", "Transport", "Infra", "CBRNE", "Other"]
    event: str = Field(..., description="Type d'événement (ex: Earthquake, Tsunami Warning, Tropical Cyclone)")
    urgency: Literal["Immediate", "Expected", "Future", "Past", "Unknown"]
    severity: Literal["Extreme", "Severe", "Moderate", "Minor", "Unknown"]
    certainty: Literal["Observed", "Likely", "Possible", "Unlikely", "Unknown"]
    effective: str = Field(..., description="Date/heure ISO 8601 d'effet")
    headline: str = Field(..., description="Titre court de l'alerte")
    description: str = Field(..., description="Description détaillée incluant valeurs physiques et sources")
    instruction: str = Field(..., description="Consignes de sécurité recommandées")
    web: str = Field(default="https://github.com/Axellum/alerte-mondiale-api")
    parameters: dict[str, Any] = Field(default_factory=dict)
    area: list[CAPArea] = Field(default_factory=list)


class CAPAlert(BaseModel):
    """Message d'alerte conforme au profil OASIS CAP 1.2."""

    xmlns: str = Field(default="urn:oasis:names:tc:emergency:cap:1.2")
    identifier: str
    sender: str = Field(default="vigie-mondiale-api@auxfilsdesidees.fr")
    sent: str = Field(..., description="Horodatage ISO 8601 avec fuseau (+00:00)")
    status: Literal["Actual", "Exercise", "System", "Test", "Draft"] = "Actual"
    msgType: Literal["Alert", "Update", "Cancel", "Ack", "Error"] = "Alert"
    scope: Literal["Public", "Restricted", "Private"] = "Public"
    info: list[CAPInfo]


class CAPFeedResponse(BaseModel):
    """Enveloppe JSON de diffusion d'alertes OASIS CAP 1.2."""

    protocol: str = Field(default="OASIS CAP 1.2")
    generated_at: str
    alert_count: int
    alerts: list[CAPAlert]


# ============================================================================
# Mode Simulation de Crises (/v1/simulate)
# ============================================================================


class SimulationScenario(str, Enum):
    """Scénarios de crise réalistes préconfigurés."""

    MEGA_TSUNAMI_JAPAN = "mega_tsunami_japan"
    CAT5_HURRICANE_CARIBBEAN = "cat5_hurricane_caribbean"
    VOLCANO_ERUPTION_ICELAND = "volcano_eruption_iceland"
    SEVERE_SQUALL_MARINE = "severe_squall_marine"
    CLEAR = "clear"


class SimulationRequest(BaseModel):
    """Requête d'activation d'un scénario de crise ou d'injection personnalisée."""

    scenario: SimulationScenario = Field(
        default=SimulationScenario.MEGA_TSUNAMI_JAPAN,
        description="Nom du scénario de crise à injecter (ou 'clear' pour revenir au mode réel)",
    )
    target_lat: Optional[float] = Field(
        default=None, ge=-90.0, le=90.0, description="Latitude optionnelle pour centrer le scénario près d'un bateau/zone"
    )
    target_lon: Optional[float] = Field(
        default=None, ge=-180.0, le=180.0, description="Longitude optionnelle pour centrer le scénario"
    )


class SimulationResponse(BaseModel):
    """Réponse après injection d'un scénario de simulation."""

    status: str
    scenario: SimulationScenario
    simulation_active: bool
    injected_hazards_count: int
    injected_hazards: list[HazardAlert]
