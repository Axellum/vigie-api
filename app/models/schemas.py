"""Pydantic models of the Vigie API.

Convention used everywhere in this module: a value that the providers did not
supply is ``None`` (JSON ``null``) and is sent as an empty field in the
embedded frame. No model carries a default number that stands in for missing
data (see ``docs/decisions/0001-two-repos-no-fabricated-data-frame-format.md``).

The models cover:

1. The compact snapshot served to embedded displays (``/v1/snapshot``).
2. The GPS / marine point profile (``/v1/point``).
3. GeoJSON (IETF RFC 7946) hazards (``/v1/geojson/hazards``).
4. OASIS CAP 1.2 alerts (``/v1/cap/alerts``).
5. Collector health and circuit-breaker state (``/v1/providers``).
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def cap_sender() -> str:
    """Return the CAP ``sender`` value: ``VIGIE_CAP_SENDER`` or the neutral ``vigie-api``."""
    return os.environ.get("VIGIE_CAP_SENDER", "").strip() or "vigie-api"


class SeverityLevel(str, Enum):
    """Unified severity levels."""

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
    """Normalized categories of natural hazards."""

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
    """States of the circuit breaker wrapped around each external collector."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class QuotaLimitStatus(BaseModel):
    """One limit of a published quota."""

    window_s: int = Field(..., description="Length of the sliding window (seconds)")
    limit: float = Field(..., description="The limit the provider publishes for that window (calls)")
    budget: float = Field(..., description="What the API lets through (the limit times VIGIE_BUDGET_FRACTION)")
    used: float = Field(..., description="Weight sent in that window, by every provider that shares the quota")


class QuotaStatus(BaseModel):
    """The published quota of a provider and how much of it is used."""

    group: str = Field(..., description="Providers that share this quota have the same group")
    source_url: str = Field(..., description="Page that publishes the quota")
    exhausted: bool = Field(..., description="True when the budget refuses a request: the cache is served, flagged stale")
    limits: list[QuotaLimitStatus]


class ProviderHealth(BaseModel):
    """Live health of one external data provider."""

    provider_id: str = Field(..., description="Unique collector identifier")
    name: str = Field(..., description="Human-readable name of the agency or API")
    domain: str = Field(..., description="Domain (seismic, disasters, weather, marine, space)")
    is_fallback: bool = Field(default=False, description="True for a backup provider")
    healthy: bool = Field(..., description="Operational (circuit closed or a cached value exists)")
    circuit_state: CircuitState = Field(default=CircuitState.CLOSED, description="Circuit breaker state")
    latency_ms: float = Field(default=0.0, description="Exponential moving average of the latency (ms)")
    consecutive_failures: int = Field(default=0, description="Consecutive failures")
    last_success_utc: Optional[int] = Field(default=None, description="UTC timestamp of the last success")
    cache_age_s: Optional[int] = Field(default=None, description="Age of the cached data (seconds)")
    last_error: Optional[str] = Field(default=None, description="Last error message, if any")
    calls_24h: int = Field(default=0, ge=0, description="Requests really sent to the provider in the last 24 hours")
    weight_24h: float = Field(
        default=0.0,
        ge=0.0,
        description="Weight of those requests (Open-Meteo counts a large request as several calls; 1 per request elsewhere)",
    )
    quota: Optional[QuotaStatus] = Field(
        default=None, description="Published quota and its use; null when the provider publishes none (not verified)"
    )
    budget_refusals: int = Field(
        default=0, ge=0, description="Requests not sent since the start because the budget was exhausted"
    )


class GPSCoordinates(BaseModel):
    """GPS position and motion vector (boat / vehicle mode)."""

    lat: float = Field(..., ge=-90.0, le=90.0, description="WGS84 latitude in decimal degrees")
    lon: float = Field(..., ge=-180.0, le=180.0, description="WGS84 longitude in decimal degrees")
    sog_knots: Optional[float] = Field(default=None, ge=0.0, description="Speed over ground (knots)")
    cog_deg: Optional[float] = Field(default=None, ge=0.0, lt=360.0, description="Course over ground (degrees)")


class CurrentWeather(BaseModel):
    """Current weather conditions. Every field is ``None`` when the provider did not supply it."""

    temp_c: Optional[float] = Field(default=None, description="Temperature at 2 m (deg C)")
    feels_like_c: Optional[float] = Field(default=None, description="Apparent temperature (deg C)")
    humidity_pct: Optional[int] = Field(default=None, ge=0, le=100, description="Relative humidity (%)")
    pressure_hpa: Optional[float] = Field(default=None, description="Mean sea-level pressure (hPa)")
    wind_kmh: Optional[float] = Field(default=None, ge=0.0, description="Wind speed at 10 m (km/h)")
    gust_kmh: Optional[float] = Field(default=None, ge=0.0, description="Wind gusts at 10 m (km/h)")
    wind_deg: Optional[int] = Field(default=None, ge=0, le=360, description="Wind direction (degrees)")
    rain_1h_mm: Optional[float] = Field(
        default=None, ge=0.0, description="Precipitation reported by the provider for the current period (mm)"
    )
    weather_code: Optional[int] = Field(default=None, description="WMO weather interpretation code")
    uv_index: Optional[float] = Field(default=None, ge=0.0, description="UV index")
    aqi_eu: Optional[int] = Field(default=None, ge=0, description="European air quality index (EAQI)")


class DailyForecast(BaseModel):
    """Daily forecast. Every numeric field is ``None`` when the provider did not supply it."""

    date: str = Field(..., description="Local calendar day, ISO 8601 (YYYY-MM-DD)")
    code: Optional[int] = Field(default=None, description="WMO weather code of the day (most severe)")
    tmin: Optional[float] = Field(default=None, description="Minimum temperature (deg C)")
    tmax: Optional[float] = Field(default=None, description="Maximum temperature (deg C)")
    rain_mm: Optional[float] = Field(default=None, ge=0.0, description="Daily precipitation total (mm)")
    rain_probability_pct: Optional[int] = Field(
        default=None, ge=0, le=100, description="Maximum precipitation probability of the day (%)"
    )
    wind_max: Optional[float] = Field(default=None, ge=0.0, description="Maximum wind speed of the day (km/h)")
    uv_max: Optional[float] = Field(default=None, ge=0.0, description="Maximum UV index of the day")


class RainNowcast(BaseModel):
    """Precipitation over the next 60 minutes (Open-Meteo 15-minute steps) and the latest observed radar frame."""

    interval_minutes: int = Field(default=15, description="Time step (minutes)")
    mm_per_step: Optional[list[float]] = Field(
        default=None,
        description="Precipitation (mm) per 15-minute step over the next hour; null when unknown",
    )
    rain_expected_60m: Optional[bool] = Field(default=None, description="Rain expected within 60 minutes")
    max_mm_h: Optional[float] = Field(default=None, ge=0.0, description="Peak equivalent intensity (mm/h)")
    radar_tile_url_template: Optional[str] = Field(
        default=None, description="RainViewer radar tile URL template (slippy map)"
    )
    radar_timestamp_utc: Optional[int] = Field(
        default=None, description="UTC timestamp of the latest observed RainViewer radar frame"
    )


class MarineConditions(BaseModel):
    """Ocean conditions at the exact GPS position. Every field is ``None`` when unknown."""

    wave_height_m: Optional[float] = Field(default=None, ge=0.0, description="Significant wave height (m)")
    wave_direction_deg: Optional[int] = Field(default=None, ge=0, le=360, description="Wave direction (degrees)")
    wave_period_s: Optional[float] = Field(default=None, ge=0.0, description="Wave period (s)")
    swell_wave_height_m: Optional[float] = Field(default=None, ge=0.0, description="Primary swell height (m)")
    swell_wave_direction_deg: Optional[int] = Field(
        default=None, ge=0, le=360, description="Swell direction (degrees)"
    )
    swell_wave_period_s: Optional[float] = Field(default=None, ge=0.0, description="Swell period (s)")
    ocean_current_velocity_kmh: Optional[float] = Field(
        default=None, ge=0.0, description="Surface ocean current speed (km/h)"
    )
    ocean_current_direction_deg: Optional[int] = Field(
        default=None, ge=0, le=360, description="Ocean current direction (degrees)"
    )
    sea_state_label: Optional[
        Literal["CALM", "SMOOTH", "MODERATE", "ROUGH", "VERY_ROUGH", "HIGH", "PHENOMENAL"]
    ] = Field(default=None, description="Sea state (simplified Douglas scale), from the wave height")
    navigation_warning: bool = Field(
        default=False, description="True when a known value is dangerous (waves or swell >= 3.5 m, current >= 6 km/h)"
    )
    stale: bool = Field(
        default=False,
        description="True when the values come from an expired cache entry (the provider could not or must not be asked)",
    )


class SpaceWeatherStatus(BaseModel):
    """Space weather (NOAA SWPC). Every field is ``None`` when SWPC could not be read."""

    solar_kp: Optional[float] = Field(default=None, ge=0.0, le=9.0, description="Planetary Kp index")
    solar_storm_level: Optional[Literal["G0", "G1", "G2", "G3", "G4", "G5"]] = Field(
        default=None, description="NOAA geomagnetic storm scale"
    )
    gps_degraded: Optional[bool] = Field(default=None, description="GNSS accuracy likely degraded (Kp >= 6)")
    hf_radio_blackout_risk: Optional[bool] = Field(
        default=None, description="HF radio disturbance likely (Kp >= 5)"
    )


class HazardAlert(BaseModel):
    """Normalized, enriched and de-duplicated natural hazard alert."""

    id: str = Field(..., description="Unique alert identifier")
    category: HazardCategory = Field(..., description="Hazard category")
    severity: SeverityLevel = Field(..., description="Severity (GREEN, YELLOW, ORANGE, RED)")
    title: str = Field(..., description="Short title as given by the source")
    source: str = Field(..., description="Combined sources (e.g. USGS+EMSC+GDACS)")
    sources: list[str] = Field(default_factory=list, description="Agencies that report the event")
    timestamp_utc: int = Field(..., description="Event time, UTC epoch seconds")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude of the epicentre / phenomenon")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude of the epicentre / phenomenon")
    value_primary: Optional[float] = Field(
        default=None,
        description="Main physical value (magnitude, wind km/h, VEI...); null when the source gives none",
    )
    value_unit: Optional[str] = Field(default=None, description="Unit of the main value (Mw, km/h, m, VEI)")
    depth_km: Optional[float] = Field(default=None, description="Hypocentre depth for an earthquake (km)")
    tsunami_warning: bool = Field(default=False, description="Tsunami warning or potential")
    tsunami_eta_minutes: Optional[int] = Field(
        default=None, description="Estimated tsunami arrival time at the target (minutes)"
    )
    nearest_zone_id: Optional[str] = Field(default=None, description="Nearest watched zone identifier")
    nearest_zone_name: Optional[str] = Field(default=None, description="Nearest watched zone name")
    distance_to_zone_km: Optional[float] = Field(default=None, description="Great-circle distance to the target (km)")
    bearing_deg: Optional[float] = Field(
        default=None, description="Initial bearing from the target to the hazard (0-360 degrees)"
    )
    bearing_cardinal: Optional[str] = Field(
        default=None, description="Cardinal direction from the target to the hazard (N, NE, E...)"
    )


class ZoneWeather(BaseModel):
    """Weather and alert level of one watched zone."""

    id: str = Field(..., description="Short zone identifier (e.g. z0)")
    name: str = Field(..., description="City or sea area name")
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    utc_offset_s: Optional[int] = Field(default=None, description="UTC offset of the zone (seconds)")
    alert_radius_km: float = Field(default=500.0, description="Watch radius (km)")
    local_alert_level: SeverityLevel = Field(
        default=SeverityLevel.GREEN, description="Highest severity of the hazards inside the watch radius"
    )
    weather_source: Optional[str] = Field(default=None, description="Provider of the zone weather; null when none")
    current: Optional[CurrentWeather] = Field(
        default=None, description="Current weather; null when no provider answered"
    )
    rain_next_60m: Optional[list[float]] = Field(
        default=None, description="Precipitation per 15-minute step over the next hour (mm); null when unknown"
    )
    daily_10d: list[DailyForecast] = Field(default_factory=list, description="Daily forecasts")


class GlobalStatus(BaseModel):
    """Global hazard summary and space weather."""

    max_severity: Optional[SeverityLevel] = Field(
        default=None, description="Highest severity among active hazards; null when no hazard source answered"
    )
    active_red_count: Optional[int] = Field(default=None, ge=0)
    active_orange_count: Optional[int] = Field(default=None, ge=0)
    active_yellow_count: Optional[int] = Field(default=None, ge=0)
    hazard_sources_ok: int = Field(default=0, ge=0, description="Hazard sources that answered this cycle")
    hazard_sources_total: int = Field(default=0, ge=0, description="Hazard sources polled")
    solar_kp: Optional[float] = Field(default=None, ge=0.0, le=9.0)
    solar_storm_level: Optional[str] = Field(default=None)
    gps_degraded: Optional[bool] = Field(default=None)
    hf_radio_blackout_risk: Optional[bool] = Field(default=None)
    weather_fallback_active: bool = Field(
        default=False, description="True when a zone weather came from MET Norway or from a stale cache"
    )
    zones_without_weather: int = Field(
        default=0, ge=0, description="Watched zones for which no weather provider answered"
    )


class DeviceType(str, Enum):
    """Hardware profiles of the Vigie clients."""

    WAVESHARE_7B = "waveshare-7b"  # 7-inch 1024x600 ESP32-P4 desktop display
    TAB5 = "tab5"                  # M5Stack Tab5 5-inch 1280x720 ESP32-P4, battery, Grove GPS
    MARINE = "marine"              # Boat navigation terminal with GPS/NMEA, swell and currents
    WEB = "web"                    # Web client / wall of screens
    GENERIC = "generic"            # Other embedded displays


class SnapshotResponse(BaseModel):
    """Compact multi-display payload (target: under 12 KiB)."""

    version: str = Field(default="1.1")
    server_utc: int = Field(..., description="Server UTC timestamp")
    device_type: DeviceType = Field(
        default=DeviceType.WAVESHARE_7B,
        description="Hardware profile served (waveshare-7b, tab5, marine, web, generic)",
    )
    compact: bool = Field(default=True, description="True when the compact profile (under 12 KiB) is applied")
    simulation_active: bool = Field(default=False, description="True while a simulated scenario is active")
    global_status: GlobalStatus
    zones: list[ZoneWeather]
    hazards: list[HazardAlert]


class FloodRiskStatus(BaseModel):
    """River discharge and flood indicator (Copernicus GloFAS through the Open-Meteo Flood API)."""

    river_discharge_m3s: float = Field(..., ge=0.0, description="Current river discharge (m3/s)")
    river_discharge_mean_m3s: Optional[float] = Field(
        default=None, ge=0.0, description="Mean of the forecast ensemble discharge over the period (m3/s)"
    )
    river_discharge_max_m3s: float = Field(..., ge=0.0, description="Maximum forecast discharge (m3/s)")
    anomaly_ratio: Optional[float] = Field(
        default=None, ge=0.0, description="Ratio of the maximum to the mean forecast discharge"
    )
    flood_alert_level: Optional[SeverityLevel] = Field(
        default=None, description="Heuristic flood level from the ratio and the discharge; null when unknown"
    )
    stale: bool = Field(
        default=False,
        description="True when the values come from an expired cache entry (the provider could not or must not be asked)",
    )


class AirQualityStatus(BaseModel):
    """Air quality, UV and dust / ash (Open-Meteo Air Quality API). Every field is ``None`` when unknown."""

    european_aqi: Optional[int] = Field(default=None, ge=0, description="European air quality index (EAQI)")
    us_aqi: Optional[int] = Field(default=None, ge=0, description="US air quality index")
    pm2_5_ugm3: Optional[float] = Field(default=None, ge=0.0, description="PM2.5 particles (ug/m3)")
    pm10_ugm3: Optional[float] = Field(default=None, ge=0.0, description="PM10 particles (ug/m3)")
    uv_index: Optional[float] = Field(default=None, ge=0.0, description="UV index")
    dust_ugm3: Optional[float] = Field(
        default=None, ge=0.0, description="Desert dust / volcanic ash concentration (ug/m3)"
    )
    ash_or_dust_warning: bool = Field(
        default=False, description="True when a known value is high (dust >= 100, PM10 >= 120 or EAQI >= 100)"
    )
    stale: bool = Field(
        default=False,
        description="True when the values come from an expired cache entry (the provider could not or must not be asked)",
    )


class MonitoredZoneConfig(BaseModel):
    """Persistent configuration of a watched zone (SQLite watchlist)."""

    id: str = Field(..., description="Unique zone identifier (e.g. z0, z5)")
    name: str = Field(..., min_length=1, max_length=64, description="Readable name of the zone or port")
    lat: float = Field(..., ge=-90.0, le=90.0, description="WGS84 latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, description="WGS84 longitude")
    utc_offset_s: int = Field(
        ..., description="Configured UTC offset (seconds), used when the weather provider gives none"
    )
    alert_radius_km: float = Field(default=500.0, gt=0.0, le=5000.0, description="Geodesic alert radius (km)")
    is_marine: bool = Field(default=False, description="Maritime or coastal zone")


class MonitoredZoneUpsertRequest(BaseModel):
    """Request to add or update a watched zone (``POST /v1/zones``)."""

    id: Optional[str] = Field(default=None, description="Optional identifier (generated when absent)")
    name: str = Field(..., min_length=1, max_length=64, description="Name of the city, island or sea area")
    lat: float = Field(..., ge=-90.0, le=90.0, description="WGS84 latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, description="WGS84 longitude")
    utc_offset_s: int = Field(..., description="UTC offset (seconds) used when the weather provider gives none")
    alert_radius_km: float = Field(default=500.0, gt=0.0, le=5000.0, description="Watch radius (km)")
    is_marine: bool = Field(default=False, description="Maritime zone")


class HistoryResponse(BaseModel):
    """Paged hazard history from SQLite (``GET /v1/history``)."""

    count: int
    items: list[HazardAlert]


class PointResponse(BaseModel):
    """Full payload for one exact position: boat, vehicle or Tab5 with Grove GPS (``/v1/point``)."""

    version: str = Field(default="1.1")
    server_utc: int
    device_type: DeviceType = Field(
        default=DeviceType.TAB5,
        description="Hardware profile served (tab5 with Grove GPS, marine, waveshare-7b, web)",
    )
    compact: bool = Field(default=True, description="True when compact filtering is applied")
    coordinates: GPSCoordinates
    utc_offset_s: Optional[int] = Field(default=None, description="UTC offset at the position (seconds), if known")
    weather_provider: Optional[str] = Field(default=None, description="Weather provider used; null when none answered")
    fallback_used: bool = Field(default=False, description="True when the weather came from a fallback source")
    local_alert_level: SeverityLevel = Field(default=SeverityLevel.GREEN)
    current: Optional[CurrentWeather] = Field(default=None, description="Current weather; null when unknown")
    daily: list[DailyForecast] = Field(default_factory=list, description="Daily forecasts at the position")
    rain_nowcast: RainNowcast
    marine: Optional[MarineConditions] = Field(
        default=None, description="Marine conditions (present when requested and available)"
    )
    flood_risk: Optional[FloodRiskStatus] = Field(default=None, description="River discharge (GloFAS); null when unknown")
    air_quality: Optional[AirQualityStatus] = Field(default=None, description="Air quality, UV and dust; null when unknown")
    space_weather: SpaceWeatherStatus
    nearby_hazards: list[HazardAlert] = Field(
        default_factory=list,
        description="Global hazards filtered and enriched relative to this position",
    )


# ============================================================================
# IETF RFC 7946 GeoJSON (/v1/geojson/hazards)
# ============================================================================


class GeoJSONPointGeometry(BaseModel):
    """Point geometry per RFC 7946 section 3.1.2 ([lon, lat] or [lon, lat, elevation])."""

    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(
        ..., min_length=2, max_length=3, description="[longitude, latitude, elevation_or_depth]"
    )


class GeoJSONFeature(BaseModel):
    """Feature object per RFC 7946 section 3.2."""

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: GeoJSONPointGeometry
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    """FeatureCollection per RFC 7946 section 3.3."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    bbox: Optional[list[float]] = Field(
        default=None, description="Bounding box [min_lon, min_lat, max_lon, max_lat]"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    features: list[GeoJSONFeature] = Field(default_factory=list)


# ============================================================================
# OASIS CAP 1.2 - Common Alerting Protocol (/v1/cap/alerts)
# ============================================================================


class CAPArea(BaseModel):
    """``<area>`` element of OASIS CAP 1.2."""

    areaDesc: str = Field(..., description="Text description of the affected area")
    circle: str = Field(..., description="Circle as 'lat,lon radius_km' (CAP 1.2); radius 0 is a point")


class CAPInfo(BaseModel):
    """``<info>`` element of OASIS CAP 1.2."""

    language: str = Field(default="en-US")
    category: Literal["Geo", "Met", "Safety", "Security", "Rescue", "Fire", "Health", "Env", "Transport", "Infra", "CBRNE", "Other"]
    event: str = Field(..., description="Event type (e.g. EARTHQUAKE, TSUNAMI, HURRICANE)")
    urgency: Literal["Immediate", "Expected", "Future", "Past", "Unknown"]
    severity: Literal["Extreme", "Severe", "Moderate", "Minor", "Unknown"]
    certainty: Literal["Observed", "Likely", "Possible", "Unlikely", "Unknown"]
    effective: str = Field(..., description="ISO 8601 effective date and time")
    headline: str = Field(..., description="Short alert title")
    description: str = Field(..., description="Detailed description with physical values and sources")
    instruction: str = Field(..., description="Recommended safety instructions")
    web: Optional[str] = Field(default=None, description="Optional URL with more information")
    parameters: dict[str, Any] = Field(default_factory=dict)
    area: list[CAPArea] = Field(default_factory=list)


class CAPAlert(BaseModel):
    """Alert message following the OASIS CAP 1.2 profile."""

    xmlns: str = Field(default="urn:oasis:names:tc:emergency:cap:1.2")
    identifier: str
    sender: str = Field(default_factory=cap_sender, description="Sender id: VIGIE_CAP_SENDER or 'vigie-api'")
    sent: str = Field(..., description="ISO 8601 timestamp with time zone (+00:00)")
    status: Literal["Actual", "Exercise", "System", "Test", "Draft"] = "Actual"
    msgType: Literal["Alert", "Update", "Cancel", "Ack", "Error"] = "Alert"
    scope: Literal["Public", "Restricted", "Private"] = "Public"
    info: list[CAPInfo]


class CAPFeedResponse(BaseModel):
    """JSON envelope of an OASIS CAP 1.2 alert feed."""

    protocol: str = Field(default="OASIS CAP 1.2")
    generated_at: str
    alert_count: int
    alerts: list[CAPAlert]


# ============================================================================
# Crisis simulation (/v1/simulate)
# ============================================================================


class SimulationScenario(str, Enum):
    """Preconfigured test scenarios. Their data is simulated and always labelled as such."""

    MEGA_TSUNAMI_JAPAN = "mega_tsunami_japan"
    CAT5_HURRICANE_CARIBBEAN = "cat5_hurricane_caribbean"
    VOLCANO_ERUPTION_ICELAND = "volcano_eruption_iceland"
    SEVERE_SQUALL_MARINE = "severe_squall_marine"
    CLEAR = "clear"


class SimulationRequest(BaseModel):
    """Request to start a simulated scenario."""

    scenario: SimulationScenario = Field(
        default=SimulationScenario.MEGA_TSUNAMI_JAPAN,
        description="Scenario to inject, or 'clear' to return to real data",
    )
    target_lat: Optional[float] = Field(
        default=None, ge=-90.0, le=90.0, description="Optional latitude to centre the scenario near a boat or zone"
    )
    target_lon: Optional[float] = Field(
        default=None, ge=-180.0, le=180.0, description="Optional longitude to centre the scenario"
    )


class SimulationResponse(BaseModel):
    """Response after starting or clearing a simulated scenario."""

    status: str
    scenario: SimulationScenario
    simulation_active: bool
    injected_hazards_count: int
    injected_hazards: list[HazardAlert]
