"""Sample provider payloads and models shared by the tests."""

from __future__ import annotations

from typing import Any

from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector
from app.models.schemas import HazardAlert, HazardCategory, MarineConditions, SeverityLevel


def open_meteo_payload() -> dict[str, Any]:
    """A realistic Open-Meteo response: current, 15-minute nowcast and 5 daily forecasts."""
    return {
        "utc_offset_seconds": 7200,
        "current": {
            "temperature_2m": 18.2,
            "apparent_temperature": 17.6,
            "relative_humidity_2m": 68,
            "pressure_msl": 1016.4,
            "wind_speed_10m": 19.5,
            "wind_gusts_10m": 31.0,
            "wind_direction_10m": 245,
            "precipitation": 0.4,
            "weather_code": 61,
            "uv_index": 1.7,
        },
        "minutely_15": {"precipitation": [0.0, 0.2, 0.5, 0.1]},
        "daily": {
            "time": ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"],
            "weather_code": [61, 2, 1, 3, 63],
            "temperature_2m_min": [12.0, 11.5, 10.0, 11.0, 13.0],
            "temperature_2m_max": [19.0, 20.5, 21.0, 18.5, 17.0],
            "precipitation_sum": [2.4, 0.0, 0.0, 0.5, 6.2],
            "precipitation_probability_max": [80, 10, 5, 35, 90],
            "wind_speed_10m_max": [32.0, 20.0, 18.0, 25.0, 42.0],
            "uv_index_max": [3.5, 4.0, 4.2, 3.0, 2.5],
        },
    }


def parsed_open_meteo() -> dict[str, Any]:
    """The sample Open-Meteo response after parsing."""
    return OpenMeteoCollector.parse_response(open_meteo_payload())


def metno_payload() -> dict[str, Any]:
    """A small MET Norway compact response: two hourly steps on 2026-10-20 and one on 2026-10-21."""
    return {
        "properties": {
            "timeseries": [
                {
                    "time": "2026-10-20T14:00:00Z",
                    "data": {
                        "instant": {
                            "details": {
                                "air_temperature": 14.8,
                                "relative_humidity": 81.0,
                                "air_pressure_at_sea_level": 1009.5,
                                "wind_speed": 6.0,
                                "wind_from_direction": 255.0,
                            }
                        },
                        "next_1_hours": {
                            "summary": {"symbol_code": "lightrain"},
                            "details": {"precipitation_amount": 1.6},
                        },
                        "next_6_hours": {
                            "summary": {"symbol_code": "rain"},
                            "details": {"precipitation_amount": 4.0},
                        },
                    },
                },
                {
                    "time": "2026-10-20T20:00:00Z",
                    "data": {
                        "instant": {"details": {"air_temperature": 12.0, "wind_speed": 9.0}},
                        "next_1_hours": {
                            "summary": {"symbol_code": "cloudy"},
                            "details": {"precipitation_amount": 0.0},
                        },
                    },
                },
                {
                    "time": "2026-10-20T23:00:00Z",
                    "data": {
                        "instant": {"details": {"air_temperature": 10.5, "wind_speed": 4.0}},
                        "next_1_hours": {
                            "summary": {"symbol_code": "partlycloudy_night"},
                            "details": {"precipitation_amount": 0.0},
                        },
                    },
                },
                {
                    "time": "2026-10-21T06:00:00Z",
                    "data": {
                        "instant": {"details": {"air_temperature": 9.0, "wind_speed": 3.0}},
                        "next_6_hours": {
                            "summary": {"symbol_code": "clearsky_day"},
                            "details": {"precipitation_amount": 0.0},
                        },
                    },
                },
            ]
        }
    }


def parsed_metno() -> dict[str, Any]:
    """The sample MET Norway response after parsing."""
    return MetNorwayCollector.parse_response(metno_payload(), days=5)


def marine_payload() -> dict[str, Any]:
    """A marine response with a high sea state."""
    return {
        "current": {
            "wave_height": 4.2,
            "wave_direction": 275,
            "wave_period": 11.5,
            "swell_wave_height": 3.8,
            "swell_wave_direction": 280,
            "swell_wave_period": 13.0,
            "ocean_current_velocity": 2.4,
            "ocean_current_direction": 95,
        }
    }


def parsed_marine() -> MarineConditions:
    """The sample marine response after parsing."""
    return OpenMeteoMarineCollector.parse_response(marine_payload())


def earthquake(
    hazard_id: str = "usgs_us7000test",
    severity: SeverityLevel = SeverityLevel.ORANGE,
    source: str = "USGS",
    value: float | None = 6.5,
    lat: float = 35.15,
    lon: float = 25.20,
    timestamp: int = 1792334500,
    depth_km: float | None = 18.0,
    tsunami: bool = True,
) -> HazardAlert:
    """Build an earthquake alert for the tests."""
    return HazardAlert(
        id=hazard_id,
        category=HazardCategory.EARTHQUAKE,
        severity=severity,
        title=f"M {value} - Crete, Greece" if value is not None else "Earthquake - Crete, Greece",
        source=source,
        sources=[source],
        timestamp_utc=timestamp,
        lat=lat,
        lon=lon,
        value_primary=value,
        value_unit="Mw",
        depth_km=depth_km,
        tsunami_warning=tsunami,
    )
