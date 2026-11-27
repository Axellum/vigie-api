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

Credits: every block of data from one source carries an ``attribution`` field, the text its source asks
for (``app/attribution.py`` holds them all, ``SOURCES.md`` lists them). A response that mixes blocks
names the credit of its own fields (``weather_attribution``, ``space_weather_attribution``). The
credit is ``None`` when the block holds no data. The compact embedded frame has no such field.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr, computed_field

from app.attribution import hazard_credit, merge_credits, place_credit
from app.models.avalanches import AvalancheStatus
from app.models.drought import DroughtStatus
from app.models.ensemble import Probabilities
from app.models.places import NearPlace
from app.models.pollens import PollenStatus
from app.models.shortterm import ShortTerm


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
    source_url: str = Field(..., description="Page that publishes the quota (for a ceiling this API sets itself, the page of this project that states it)")
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
    elevation_m: Optional[int] = Field(
        default=None,
        description="Altitude (m) the weather was asked for (`elevation`, rounded to the metre); null when none was asked: the weather "
        "is then the one of the forecast cell, at its own altitude",
    )


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
    sunshine_h: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=24.0,
        description="Hours of sunshine of the day (Open-Meteo's sunshine duration: direct normal irradiance above 120 W/m2, the WMO "
        "threshold, calculated from the model's radiation)",
    )
    snowfall_cm: Optional[float] = Field(
        default=None, ge=0.0, description="Fresh snow of the day (cm), as the model gives it (Open-Meteo's snowfall sum)"
    )


class HourlyWeatherPoint(BaseModel):
    """One hour of the point weather forecast (up to 72 hours). Every field a provider did not supply is ``None``."""

    time_utc: int = Field(..., description="Start of the hour, UTC epoch seconds")
    temp_c: Optional[float] = Field(default=None, description="Temperature at 2 m (deg C)")
    feels_like_c: Optional[float] = Field(default=None, description="Apparent temperature (deg C)")
    dew_point_c: Optional[float] = Field(default=None, description="Dew point temperature at 2 m (deg C)")
    precipitation_mm: Optional[float] = Field(default=None, ge=0.0, description="Hourly precipitation total (mm)")
    precipitation_probability_pct: Optional[int] = Field(
        default=None, ge=0, le=100, description="Hourly precipitation probability (%)"
    )
    wind_kmh: Optional[float] = Field(default=None, ge=0.0, description="Wind speed at 10 m (km/h)")
    wind_gust_kmh: Optional[float] = Field(default=None, ge=0.0, description="Wind gusts at 10 m (km/h)")
    wind_deg: Optional[int] = Field(default=None, ge=0, le=360, description="Wind direction at 10 m (degrees)")
    cloud_cover_pct: Optional[int] = Field(default=None, ge=0, le=100, description="Total cloud cover (%)")
    cloud_low_pct: Optional[int] = Field(default=None, ge=0, le=100, description="Low-level cloud cover (%)")
    cloud_mid_pct: Optional[int] = Field(default=None, ge=0, le=100, description="Mid-level cloud cover (%)")
    cloud_high_pct: Optional[int] = Field(default=None, ge=0, le=100, description="High-level cloud cover (%)")
    pressure_hpa: Optional[float] = Field(default=None, ge=0.0, description="Mean sea-level pressure (hPa)")
    uv_index: Optional[float] = Field(default=None, ge=0.0, description="Hourly UV index")
    shortwave_wm2: Optional[float] = Field(default=None, ge=0.0, description="Shortwave solar radiation (W/m2)")
    weather_code: Optional[int] = Field(default=None, ge=0, le=99, description="WMO weather interpretation code")
    is_day: Optional[bool] = Field(default=None, description="True during local daylight, False at night, null when unknown")
    freezing_level_m: Optional[int] = Field(
        default=None,
        description="Freezing level (iso 0): the altitude of the 0 °C isotherm, in m above sea level, as Open-Meteo gives it from its "
        "global models (also on the hours of the Météo-France models, which have none). When the air is below 0 °C down to the "
        "ground, the models give a height near their own ground. Null when no model gives it (MET Norway)",
    )


class PressureHistoryPoint(BaseModel):
    """One past hourly mean sea-level pressure sample (up to 24 hours before now)."""

    time_utc: int = Field(..., description="Start of the hour, UTC epoch seconds")
    pressure_hpa: Optional[float] = Field(default=None, ge=0.0, description="Mean sea-level pressure (hPa)")


class AirHourlyPoint(BaseModel):
    """One hour of the air quality forecast (up to 72 hours, CAMS via Open-Meteo). Missing values are ``None``."""

    time_utc: int = Field(..., description="Start of the hour, UTC epoch seconds")
    pm2_5: Optional[float] = Field(default=None, ge=0.0, description="PM2.5 particles (ug/m3)")
    pm10: Optional[float] = Field(default=None, ge=0.0, description="PM10 particles (ug/m3)")
    ozone: Optional[float] = Field(default=None, ge=0.0, description="Ozone O3 (ug/m3)")
    nitrogen_dioxide: Optional[float] = Field(default=None, ge=0.0, description="Nitrogen dioxide NO2 (ug/m3)")
    dust: Optional[float] = Field(default=None, ge=0.0, description="Desert dust / volcanic ash (ug/m3)")
    european_aqi: Optional[int] = Field(default=None, ge=0, description="European air quality index (EAQI)")


class RainNowcast(BaseModel):
    """The rain of four 15-minute steps around the next hour, and the latest observed radar frame.

    ``start_utc`` is the start of the first step; the times of the steps depend on the source of the rain:

    - **Météo-France PIAF**, in France when the instance has a vigie-meteo gateway (``rate_5m_mm_h`` is then set): the twelve
      5-minute rates from the 5-minute boundary at which the rain was read, summed by three. The four steps are the hour that
      starts there, and ``start_utc`` is also the start of the first 5-minute rate.
    - **Otherwise Open-Meteo's ``minutely_15`` precipitation** (its public API, or in France the Météo-France models AROME and
      ARPEGE read through the gateway, in the same format). Open-Meteo gives each value as the sum of the 15 minutes
      **before** its time, and its first time is the start of the quarter-hour in which it answered: the first step is the
      quarter-hour that ended then, the second the quarter-hour in progress, the last two the next ones. This API keeps an
      answer up to 15 minutes, and the gateway keeps its own (15 minutes by default, ``METEO_FORECAST_TTL_SECONDS`` of
      vigie-meteo): ``start_utc`` is then 15 to 45 minutes before ``server_utc``, up to 60 through the gateway, and older for
      an answer served from an expired cache (``weather_provider`` ends with "(stale cache)").
    """

    interval_minutes: int = Field(default=15, description="Length of a step of `mm_per_step` (minutes)")
    start_utc: Optional[int] = Field(
        default=None,
        description="Start of the first step of `mm_per_step` (UTC epoch seconds); step i covers `start_utc + i * "
        "interval_minutes * 60` to the start of the next one. With Météo-France PIAF it is the 5-minute boundary at which the "
        "rain was read, also the start of the first rate of `rate_5m_mm_h`; with Open-Meteo it is 15 to 45 minutes before "
        "`server_utc`, up to 60 through the Météo-France gateway (the first step is already over). null when `mm_per_step` is "
        "null or the provider gave no readable time",
    )
    mm_per_step: Optional[list[float]] = Field(
        default=None,
        description="Precipitation (mm) of four consecutive 15-minute steps from `start_utc`, oldest first. With "
        "Météo-France PIAF (`rate_5m_mm_h` set) they are the hour from the 5-minute boundary at which the rain was read; "
        "otherwise they are Open-Meteo's `minutely_15` values, each the sum of the 15 minutes before its time, so the first "
        "step is a quarter-hour already over and the second the quarter-hour in progress when the provider answered (one or "
        "two quarter-hours before `server_utc` when the answer was kept in a cache). null when unknown",
    )
    rate_5m_mm_h: Optional[list[float]] = Field(
        default=None,
        description="Rain rate (mm/h) of each 5-minute step of the hour from `start_utc` on (twelve values), from "
        "Météo-France PIAF; null when the rain comes from a provider without 5-minute steps",
    )
    rain_expected_60m: Optional[bool] = Field(
        default=None,
        description="Whether one of the four steps of `mm_per_step` has more than 0.05 mm (with Open-Meteo the first step "
        "is already over); null when unknown",
    )
    max_mm_h: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Peak rate (mm/h): the highest of `rate_5m_mm_h` with Météo-France PIAF, otherwise the largest step "
        "of `mm_per_step` times 4; null when unknown",
    )
    radar_tile_url_template: Optional[str] = Field(
        default=None, description="RainViewer radar tile URL template (slippy map)"
    )
    radar_timestamp_utc: Optional[int] = Field(
        default=None, description="UTC timestamp of the latest observed RainViewer radar frame"
    )
    attribution: Optional[str] = Field(
        default=None,
        description="Credit for the rain steps (the weather provider, and Météo-France PIAF when it gave the rain) and, "
        "when the radar fields are filled, for the radar "
        "(RainViewer); null when the block holds no data",
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
    sea_surface_temperature_c: Optional[float] = Field(
        default=None,
        ge=-3.0,
        le=45.0,
        description="Sea surface temperature (°C) of the ocean model, for a cell of about 8 km. Not a measurement: near a river "
        "mouth, in a lagoon or in the surf it can differ by degrees",
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
    attribution: Optional[str] = Field(
        default=None, description="Credit the source asks for, to show with this data; null when there is no data"
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
    attribution: Optional[str] = Field(
        default=None, description="Credit the source asks for, to show with this data; null when there is no data"
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
    near_places: Optional[list[NearPlace]] = Field(
        default=None,
        description="Reference places close to an earthquake, a volcanic event, or a wildfire, nearest first, computed by Vigie: the named active "
        "faults, the volcanoes that the hazard sources name, and active fire detections from NASA FIRMS (the radii are settings). Never the "
        "points a user watches: they are personal and listed by `/v1/watched-points` only. Empty: nothing is near. Null: not worked out (a "
        "hazard that is compared with nothing, such as a storm or a flood; the reference data it needs is off or unknown; the alert is simulated; "
        "or the compact profile of `/v1/snapshot` and `/v1/point`, which keeps to 16 KiB: ask `compact=false`). Named in `derived`; it never changes `severity`",
    )

    # What names the same event in two sources when they do not agree on its time and place (a storm: its name and its
    # season; a tsunami warning of the US centres: its VTEC code). Private: not in any response, only read by the de-duplication.
    _event_key: Optional[str] = PrivateAttr(default=None)

    @property
    def event_key(self) -> Optional[str]:
        """The key two reports of the same event share, or ``None`` when the source gives none."""
        return self._event_key

    def with_event_key(self, key: Optional[str]) -> "HazardAlert":
        """Set the event key and return the alert."""
        self._event_key = key
        return self

    # The name of the place the event is at, when the source gives it (a volcano's name): what an earthquake near it is told.
    # Private for the same reason: it is not in any response, only read by ``app/services/places.py``.
    _place_name: Optional[str] = PrivateAttr(default=None)

    @property
    def place_name(self) -> Optional[str]:
        """The name of the place the event is at (a volcano), or ``None`` when the source gives none."""
        return self._place_name

    def with_place_name(self, name: Optional[str]) -> "HazardAlert":
        """Set the place name (``None`` for a blank one) and return the alert."""
        self._place_name = (name or "").strip() or None
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Credit each reporting source asks for, to show with this alert, and the source of the places named in "
        "`near_places` (the GEM active faults database asks for its citation). GDACS alerts are automatic "
        "and do not replace official warnings. A simulated alert is credited to no provider."
    )
    @property
    def attribution(self) -> Optional[str]:
        place_sources = [source for place in (self.near_places or []) for source in place.sources]
        return merge_credits(hazard_credit(self.id, self.sources or [self.source]), place_credit(place_sources))

    @computed_field(  # type: ignore[prop-decorator]
        description="The fields of this alert that Vigie computed from its own reference data or settings, not reported by a source: "
        "`near_places` when it was worked out. (The fields relative to a watched zone or a position, `distance_to_zone_km`, "
        "`bearing_deg`, `tsunami_eta_minutes`..., are computed too and described one by one.)"
    )
    @property
    def derived(self) -> list[str]:
        return ["near_places"] if self.near_places is not None else []


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
        default=None,
        description="Precipitation (mm) of four consecutive 15-minute steps from `rain_next_60m_start_utc`, oldest first, "
        "timed like `rain_nowcast.mm_per_step` of `GET /v1/point` (with Open-Meteo the first step is a quarter-hour already "
        "over); null when unknown",
    )
    rain_next_60m_start_utc: Optional[int] = Field(
        default=None,
        description="Start of the first step of `rain_next_60m` (UTC epoch seconds), like `rain_nowcast.start_utc` of "
        "`GET /v1/point`: also the start of the first rate of `rain_rate_5m_mm_h`. null when `rain_next_60m` is null or the "
        "provider gave no readable time",
    )
    rain_rate_5m_mm_h: Optional[list[float]] = Field(
        default=None,
        description="Rain rate (mm/h) of each 5-minute step of the hour from `rain_next_60m_start_utc` on, from "
        "Météo-France PIAF; null otherwise",
    )
    daily_10d: list[DailyForecast] = Field(default_factory=list, description="Daily forecasts")
    attribution: Optional[str] = Field(
        default=None,
        description="Credit for the weather of the zone (its provider, and the air quality provider when "
        "the air quality index was added); null when no provider answered",
    )


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
    space_weather_attribution: Optional[str] = Field(
        default=None, description="Credit for solar_kp and solar_storm_level (NOAA SWPC); null when they are unknown"
    )
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
    """Compact multi-display payload (budget: under 16 KiB, with the credit of every item)."""

    version: str = Field(default="1.1")
    server_utc: int = Field(..., description="Server UTC timestamp")
    device_type: DeviceType = Field(
        default=DeviceType.WAVESHARE_7B,
        description="Hardware profile served (waveshare-7b, tab5, marine, web, generic)",
    )
    compact: bool = Field(default=True, description="True when the compact profile (under 16 KiB) is applied")
    simulation_active: bool = Field(default=False, description="True while a simulated scenario is active")
    global_status: GlobalStatus
    zones: list[ZoneWeather]
    hazards: list[HazardAlert]
    disclaimer: Optional[str] = Field(
        default=None,
        description="Notice the sources ask to publish with their data (for instance NASA FIRMS when `near_places` includes active fires)",
    )


class FloodRiskStatus(BaseModel):
    """River discharge and flood indicator (Copernicus GloFAS through the Open-Meteo Flood API)."""

    river_discharge_m3s: float = Field(..., ge=0.0, description="Current river discharge (m3/s)")
    river_discharge_mean_m3s: Optional[float] = Field(
        default=None, ge=0.0, description="Mean of the forecast ensemble discharge over the period (m3/s)"
    )
    river_discharge_max_m3s: float = Field(
        ..., ge=0.0, description="Highest forecast discharge of the ensemble members over the period (m3/s)"
    )
    anomaly_ratio: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Highest member discharge over the mean of the ensemble mean: how far the forecast scenarios spread, "
            "not a flood measure"
        ),
    )
    flood_alert_level: Optional[SeverityLevel] = Field(
        default=None,
        description=(
            "Always null: the source gives no flood threshold, and a level drawn from the ensemble spread raised false "
            "alerts. Official flood warnings: /v1/vigilances"
        ),
    )
    stale: bool = Field(
        default=False,
        description="True when the values come from an expired cache entry (the provider could not or must not be asked)",
    )
    attribution: Optional[str] = Field(
        default=None, description="Credit the source asks for, to show with this data; null when there is no data"
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
    pollens: Optional[PollenStatus] = Field(
        default=None,
        description="European pollen observations and 4-day daily peak forecast (CAMS Europe via Open-Meteo); null outside Europe or when unavailable",
    )
    stale: bool = Field(
        default=False,
        description="True when the values come from an expired cache entry (the provider could not or must not be asked)",
    )
    attribution: Optional[str] = Field(
        default=None, description="Credit the source asks for, to show with this data; null when there is no data"
    )
    # Hourly air quality forecast carried alongside the status for ``/v1/point?compact=false`` only; kept private so
    # ``AirQualityStatus`` (and the compact 16 KiB profile) stays unchanged.
    _hourly: Optional[list[AirHourlyPoint]] = PrivateAttr(default=None)

    @property
    def hourly_series(self) -> Optional[list[AirHourlyPoint]]:
        """Up to 72 hours of air quality forecast, or ``None`` when the provider did not supply hourly data."""
        return self._hourly

    def with_hourly(self, points: Optional[list[AirHourlyPoint]]) -> "AirQualityStatus":
        """Attach the hourly air quality series and return this instance."""
        self._hourly = points or None
        return self


class FiresNearby(BaseModel):
    """The active fires that NASA FIRMS detected around a position in the last 24 hours (``/v1/point``).

    **Computed by Vigie from the detections, never an alert**: it does not raise ``local_alert_level``. No detection is not the absence of
    fire: the satellites pass a given place a few times a day, a cloud hides the ground, and a small fire is not seen. The detections are
    the satellites' pixels (375 m), the latest of them about 3 hours old when FIRMS publishes them. Every field named in ``derived`` is our
    own count or distance; the detections themselves are served by ``GET /v1/fires``.
    """

    radius_km: float = Field(..., gt=0.0, description="Radius around the position that was searched (kilometres)")
    count_24h: int = Field(..., ge=0, description="Detections within the radius whose acquisition time is in the last 24 hours (every confidence, low included)")
    nearest_km: Optional[float] = Field(default=None, ge=0.0, description="Distance to the nearest of them (kilometres); null when there is none")
    max_frp_mw: Optional[float] = Field(
        default=None, ge=0.0, description="Highest fire radiative power among them (megawatts); null when there is none or FIRMS gave none"
    )
    latest_utc: Optional[int] = Field(default=None, description="Acquisition time (UTC epoch seconds) of the most recent of them; null when there is none")
    data_read_utc: int = Field(..., description="When the API last read FIRMS (UTC epoch seconds): the detections are from before that")
    stale: bool = Field(
        default=False,
        description="True when the last attempt to read FIRMS failed, or when a source was not confirmed for longer than its lifetime and the "
        "longest a read may take (the reading is refreshed on request only): these are the detections of an earlier reading",
    )
    sources: list[str] = Field(default_factory=list, description="FIRMS products read (e.g. VIIRS_NOAA20_NRT)")
    derived: list[str] = Field(
        default_factory=lambda: ["count_24h", "nearest_km", "max_frp_mw", "latest_utc"],
        description="The fields above that Vigie computed from the detections",
    )
    attribution: str = Field(..., description="The credit NASA asks for, to show with this data")
    disclaimer: str = Field(..., description="The disclaimer NASA asks to pass on with FIRMS data")


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
    disclaimer: Optional[str] = Field(
        default=None,
        description="Notice the sources ask to publish with their data (for instance NASA FIRMS when `near_places` includes active fires)",
    )


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
    weather_attribution: Optional[str] = Field(
        default=None,
        description="Credit for `current` and `daily` (the weather provider); null when no provider answered. "
        "The other blocks carry their own `attribution`.",
    )
    local_alert_level: SeverityLevel = Field(default=SeverityLevel.GREEN)
    current: Optional[CurrentWeather] = Field(default=None, description="Current weather; null when unknown")
    daily: list[DailyForecast] = Field(default_factory=list, description="Daily forecasts at the position")
    hourly: Optional[list[HourlyWeatherPoint]] = Field(
        default=None,
        description="Hourly weather forecast at the position (up to 72 hours, starting at the current hour). "
        "Populated only when `compact=false` (omitted as null in the 16 KiB compact profile) and when the weather "
        "provider supplied hourly data; every field a provider did not supply stays null",
    )
    pressure_history: Optional[list[PressureHistoryPoint]] = Field(
        default=None,
        description="Hourly mean sea-level pressure over the past 24 hours before the current hour (hPa), for barometer "
        "trend charts. Populated only when `compact=false` and the weather provider supplied past hourly pressure; null otherwise",
    )
    air_hourly: Optional[list[AirHourlyPoint]] = Field(
        default=None,
        description="Hourly air quality forecast at the position (up to 72 hours, starting at the current hour) from "
        "Open-Meteo Air Quality (CAMS). Populated only when `compact=false` and the provider supplied hourly data; null otherwise",
    )
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
    fires_nearby: Optional[FiresNearby] = Field(
        default=None,
        description="Active fires detected around the position in the last 24 hours (NASA FIRMS). Null when the source is off on this "
        "instance (`GET /v1/health`, `disabled_sources`), has not been read yet, or the oldest reading is older than the 24 hours it sums up "
        "(the refresh is started by this request). Informative only: never part of `local_alert_level`",
    )
    disclaimer: Optional[str] = Field(
        default=None,
        description="Notice the sources ask to publish with their data (for instance NASA FIRMS when `near_places` on `nearby_hazards` includes active fires)",
    )
    drought: Optional[DroughtStatus] = Field(
        default=None,
        description="French drought restriction summary (VigiEau, Licence Ouverte 2.0) when the position lies within covered French "
        "territories (`FR, GP, MQ, GF, RE, YT`); null outside French coverage or when VigiEau is unavailable. Informative only: never "
        "part of `local_alert_level`",
    )
    avalanche: Optional[AvalancheStatus] = Field(
        default=None,
        description="CAAML v6 avalanche bulletin summary (SLF CC BY 4.0 for `CH`/`LI`, Avalanche.report CC BY 4.0 DEED for Euregio "
        "`AT-07`/`IT-32-BZ`/`IT-32-TN`); null outside verified coverage or when upstream is unavailable. Informative only: never "
        "part of `local_alert_level`",
    )
    short_term: Optional[ShortTerm] = Field(
        default=None,
        description="The next hours (up to 6, steps of 15 minutes) at the position from Météo-France's AROME-PI model: hail, gusts, reduced "
        "visibility and type of precipitation, with levels worked out by this API. Only in metropolitan France and Corsica and only on an "
        "instance with a vigie-meteo gateway; null elsewhere, when the gateway has no recent run, or when the source is off "
        "(`GET /v1/health`, `disabled_sources`). A model forecast: informative only, never part of `local_alert_level`, never an official warning",
    )
    probabilities: Optional[Probabilities] = Field(
        default=None,
        description="Probabilities from Météo-France's ensemble forecasts at the position: rain (1 and 5 mm in three hours, up to 51 hours; 1, 5, 10 "
        "and 20 mm in 24 hours, up to 96 hours), snow, gusts, hail and storm. Only in metropolitan France and Corsica and only on an instance with a "
        "vigie-meteo gateway; null elsewhere, when the gateway has no recent run, or when the source is off (`GET /v1/health`, `disabled_sources`). "
        "It does not replace `daily[].rain_probability_pct` (Open-Meteo's, the highest hourly probability of the day, any amount): the two stay "
        "separate. A model forecast: informative only, never part of `local_alert_level`, never an official warning",
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
# Simulator (/v1/simulate, off unless VIGIE_ENABLE_SIMULATION=1)
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
