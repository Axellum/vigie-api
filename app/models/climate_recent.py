"""Pydantic models of the days a Météo-France station measured (``GET /v1/climate/recent``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.climate import ClimateStation

ClimateRecentStatus = Literal["available", "no_recent_data", "outside_coverage", "not_configured", "unavailable"]


class ClimateDay(BaseModel):
    """What the station measured on one day, as Météo-France's daily climatological data give it."""

    date: str = Field(description="The day (ISO). The rain is the one of 06:00 UTC that day to 06:00 UTC the next day")
    rain_mm: Optional[float] = Field(default=None, description="Rain of the day (mm)")
    tmin_c: Optional[float] = Field(default=None, description="Minimum temperature under shelter (°C)")
    tmax_c: Optional[float] = Field(default=None, description="Maximum temperature under shelter (°C)")
    tmean_c: Optional[float] = Field(default=None, description="Mean of the hourly temperatures of the day (°C)")
    wind_mean_ms: Optional[float] = Field(default=None, description="Mean of the day of the wind averaged over 10 minutes, at 10 m (m/s)")
    gust_ms: Optional[float] = Field(default=None, description="Highest instantaneous wind of the day, at 10 m (m/s)")
    doubtful: list[str] = Field(
        default_factory=list,
        description="Measures left out (null) because Météo-France marks them doubtful, under verification (quality code 2)",
    )


class ClimateRecentNormals(BaseModel):
    """The normals of the month of the last day, to set the days against (a mean of the month, not of one day)."""

    month: int
    tmax_mean_c: Optional[float] = None
    tmin_mean_c: Optional[float] = None
    period: Optional[str] = Field(default=None, description="Years the normals were computed over, as the climate sheet says")


class ClimateRecentResponse(BaseModel):
    """The last days a Météo-France station near a position measured."""

    status: ClimateRecentStatus
    lat: float
    lon: float
    station: Optional[ClimateStation] = None
    last_day: Optional[str] = Field(
        default=None, description="The last day Météo-France could give when it was asked (yesterday from 11:30 UTC, the day before until then)"
    )
    fetched_at_utc: Optional[int] = Field(default=None, description="When the gateway asked Météo-France for this station (epoch seconds)")
    days: list[ClimateDay] = Field(default_factory=list, description="From the oldest to the most recent")
    normals_month: Optional[ClimateRecentNormals] = None
    derived: list[str] = Field(default_factory=list, description="What Vigie works out rather than copies: the choice of the station and its distance")
    note: Optional[str] = None
    attribution: str
