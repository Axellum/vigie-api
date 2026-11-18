"""Probabilities of rain, snow, gusts, hail and storm at a point in France from Météo-France's ensemble forecasts (PE-AROME, PE-ARPEGE)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ShortRangeWindow(BaseModel):
    """What PE-AROME gives for the three hours that end at `to_utc` (or for that instant, for the last four fields)."""

    to_utc: int = Field(description="end of the three hours, UTC seconds")
    rain_1mm_pct: int = Field(ge=0, le=100, description="probability that the rain of the three hours reaches 1 mm or more (%)")
    rain_5mm_pct: int = Field(ge=0, le=100, description="probability that the rain of the three hours reaches 5 mm or more (%)")
    snow_1mm_pct: int = Field(ge=0, le=100, description="probability that the snow of the three hours reaches 1 mm of water or more (%)")
    gust_30kt_pct: int = Field(ge=0, le=100, description="probability of a gust over 30 knots (about 56 km/h) at the end of the three hours (%)")
    gust_50kt_pct: int = Field(ge=0, le=100, description="probability of a gust over 50 knots (about 93 km/h) at the end of the three hours (%)")
    hail_pct: int = Field(ge=0, le=100, description="probability that AROME's hail diagnostic exceeds 8 kg/m2 (small hail) at the end of the three hours (%)")
    storm_pct: int = Field(ge=0, le=100, description="probability that the maximum radar reflectivity exceeds 40 dBZ (a strong shower or a thunderstorm) at the end of the three hours (%)")


class LongRangeWindow(BaseModel):
    """What PE-ARPEGE gives for the 24 hours that end at `to_utc`."""

    to_utc: int = Field(description="end of the 24 hours, UTC seconds")
    rain_1mm_pct: int = Field(ge=0, le=100, description="probability that the rain of the 24 hours reaches 1 mm or more (%)")
    rain_5mm_pct: int = Field(ge=0, le=100, description="probability that the rain of the 24 hours reaches 5 mm or more (%)")
    rain_10mm_pct: int = Field(ge=0, le=100, description="probability that the rain of the 24 hours reaches 10 mm or more (%)")
    rain_20mm_pct: int = Field(ge=0, le=100, description="probability that the rain of the 24 hours reaches 20 mm or more (%)")


class ShortRange(BaseModel):
    run_utc: int = Field(description="start of the PE-AROME run, UTC seconds (a run every 6 hours, 35 members)")
    members: int
    window_hours: int = 3
    windows: list[ShortRangeWindow] = Field(
        description="the windows that are not entirely past, soonest first, up to 51 hours after the run; in the compact profile (the default) only "
        "the ones that end within 24 hours, ask `compact=false` for all"
    )


class LongRange(BaseModel):
    run_utc: int = Field(description="start of the PE-ARPEGE run, UTC seconds (a run every 6 hours, 35 members)")
    members: int
    window_hours: int = 24
    windows: list[LongRangeWindow] = Field(
        description="the 24-hour windows that are not entirely past, soonest first, up to 96 hours after the run; they follow the run's hour, "
        "not the calendar days"
    )


class RainPeak(BaseModel):
    """The most likely three hours of the next 24 hours: the largest `rain_1mm_pct` of `short_range`, which is **not** the probability that it rains
    at some time in the day (that one is higher and the ensemble does not give it)."""

    rain_1mm_pct: int = Field(ge=0, le=100)
    to_utc: int = Field(description="end of the three hours where it is reached (the first one when several are)")


class Probabilities(BaseModel):
    """Ensemble probabilities at the position: PE-AROME for the next 51 hours (windows of three hours) and PE-ARPEGE for the next 96 hours (windows of
    24 hours). Informative only: never part of `local_alert_level`, never an official warning (those are `nearby_hazards`)."""

    short_range: Optional[ShortRange] = Field(None, description="PE-AROME; null when the gateway has no recent run of it")
    long_range: Optional[LongRange] = Field(None, description="PE-ARPEGE; null when the gateway has no recent run of it")
    rain_peak_next_24h: Optional[RainPeak] = Field(None, description="from `short_range`; null without it or when no window ends within 24 hours")
    attribution: str
    note: str = Field(description="what this is and is not")
