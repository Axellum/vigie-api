"""Deterministic snapshot used to pin the embedded frame contract and to build the documented example."""

from __future__ import annotations

from app.models.schemas import (
    CurrentWeather,
    DailyForecast,
    DeviceType,
    GlobalStatus,
    GPSCoordinates,
    HazardAlert,
    HazardCategory,
    MarineConditions,
    PointResponse,
    RainNowcast,
    SeverityLevel,
    SnapshotResponse,
    SpaceWeatherStatus,
    ZoneWeather,
)
from tests import samples

SERVER_UTC = 1792334600


def _quake() -> HazardAlert:
    return HazardAlert(
        id="usgs_us7000test",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.ORANGE,
        title="M 6.5 - Crete, Greece",
        source="USGS+EMSC",
        sources=["USGS", "EMSC"],
        timestamp_utc=1792334500,
        lat=35.15,
        lon=25.2,
        value_primary=6.5,
        value_unit="Mw",
        depth_km=18.0,
        tsunami_warning=True,
    )


def _tornado() -> HazardAlert:
    """A NOAA alert: no magnitude and no depth, both unknown."""
    return HazardAlert(
        id="noaa_1234",
        category=HazardCategory.TORNADO,
        severity=SeverityLevel.RED,
        title="Tornado Warning - Oklahoma County, OK",
        source="NOAA_NWS",
        sources=["NOAA_NWS"],
        timestamp_utc=1792334300,
        lat=35.5,
        lon=-97.5,
        value_primary=None,
        value_unit="km/h",
    )


def example_snapshot() -> SnapshotResponse:
    """Three zones: full weather, no weather at all, and partial weather (MET Norway-like)."""
    parsed = samples.parsed_open_meteo()
    full_current = parsed["current"].model_copy(update={"aqi_eu": 31})
    partial_current = CurrentWeather(
        temp_c=3.5, humidity_pct=88, pressure_hpa=998.2, wind_kmh=30.2, wind_deg=200, weather_code=63
    )
    return SnapshotResponse(
        server_utc=SERVER_UTC,
        device_type=DeviceType.WAVESHARE_7B,
        global_status=GlobalStatus(
            max_severity=SeverityLevel.RED,
            active_red_count=1,
            active_orange_count=1,
            active_yellow_count=0,
            hazard_sources_ok=5,
            hazard_sources_total=5,
            solar_kp=2.3,
            solar_storm_level="G0",
            gps_degraded=False,
            hf_radio_blackout_risk=False,
        ),
        zones=[
            ZoneWeather(
                id="z0",
                name="Paris, FR",
                lat=48.8566,
                lon=2.3522,
                utc_offset_s=7200,
                weather_source="Open-Meteo",
                current=full_current,
                rain_next_60m=parsed["rain_next_60m"],
                daily_10d=parsed["daily_10d"][:3],
            ),
            ZoneWeather(id="z1", name="Tokyo, JP", lat=35.6762, lon=139.6503, utc_offset_s=32400),
            ZoneWeather(
                id="z2",
                name="Reykjavik, IS",
                lat=64.1466,
                lon=-21.9426,
                utc_offset_s=0,
                weather_source="MET Norway (fallback)",
                current=partial_current,
                rain_next_60m=None,
                daily_10d=[
                    DailyForecast(date="2026-10-20", code=63, tmin=1.0, tmax=4.5, rain_mm=3.2, wind_max=38.0)
                ],
            ),
        ],
        hazards=[_quake(), _tornado()],
    )


def example_point() -> PointResponse:
    """A GPS position with weather, a 15-minute nowcast and marine conditions."""
    parsed = samples.parsed_open_meteo()
    return PointResponse(
        server_utc=SERVER_UTC,
        device_type=DeviceType.WAVESHARE_7B,
        coordinates=GPSCoordinates(lat=47.75, lon=-3.36),
        utc_offset_s=7200,
        weather_provider="Open-Meteo",
        current=parsed["current"],
        daily=parsed["daily_10d"][:2],
        rain_nowcast=RainNowcast(mm_per_step=parsed["rain_next_60m"]),
        marine=MarineConditions(
            wave_height_m=4.2,
            wave_direction_deg=275,
            wave_period_s=11.5,
            swell_wave_height_m=3.8,
            ocean_current_velocity_kmh=3.7,
        ),
        space_weather=SpaceWeatherStatus(),
        nearby_hazards=[_quake()],
    )
