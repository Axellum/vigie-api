"""The next hours at a point in France from Météo-France's AROME-PI model: hail, gusts, reduced visibility, type of precipitation."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PrecipitationKind = Literal[
    "none",
    "rain",
    "drizzle",
    "freezing",
    "snow",
    "wet_snow",
    "rain_snow",
    "ice_pellets",
    "snow_pellets",
    "hail",
    "unknown",
]
GustLevel = Literal["none", "strong", "very_strong"]
HailLevel = Literal["none", "small_hail", "hail"]
VisibilityLevel = Literal["none", "reduced", "very_reduced"]


class ShortTermStep(BaseModel):
    """What the model forecasts for the 15 minutes that end at `time_utc`."""

    time_utc: int = Field(description="end of the 15 minutes, UTC seconds")
    wind_gust_kmh: int = Field(description="strongest gust of the 15 minutes at 10 m (km/h), the strongest of a block of about 4 x 3 km around the point")
    hail_kg_m2: float = Field(
        description="AROME's hail diagnostic (kg/m2, the maximum of the interval and of the block): from 8 the model forecasts small hail, from 16 hail"
    )
    visibility_m: int = Field(description="lowest visibility of the 15 minutes (m, 20000 is the model's ceiling), the lowest of the block: fog or heavy precipitation")
    precipitation: PrecipitationKind = Field(description="the most severe type of precipitation of the 15 minutes (`none` when it does not precipitate)")


class GustSummary(BaseModel):
    level: GustLevel = Field(description="`strong` from 60 km/h, `very_strong` from 90 km/h (our thresholds, not Météo-France's)")
    max_kmh: int
    at_utc: Optional[int] = Field(None, description="end of the first 15 minutes that reach `max_kmh`")


class HailSummary(BaseModel):
    level: HailLevel = Field(description="`small_hail` from 8 kg/m2, `hail` from 16 kg/m2 (the thresholds of Météo-France's own diagnostic of the type of precipitation)")
    max_kg_m2: float
    first_at_utc: Optional[int] = Field(None, description="end of the first 15 minutes at or above the `small_hail` threshold")


class VisibilitySummary(BaseModel):
    level: VisibilityLevel = Field(description="`reduced` under 1,000 m, `very_reduced` under 200 m (our thresholds; fog or heavy precipitation, the model does not say which)")
    min_m: int
    first_at_utc: Optional[int] = Field(None, description="end of the first 15 minutes under 1,000 m")


class PrecipitationSpan(BaseModel):
    kind: PrecipitationKind
    first_at_utc: int = Field(description="end of the first 15 minutes of this type")
    last_at_utc: int = Field(description="end of the last 15 minutes of this type")


class ShortTerm(BaseModel):
    """The model's forecast for the next hours (up to 6) in steps of 15 minutes, and what it makes of them. Informative only: never part
    of `local_alert_level`, never an official warning (those are `nearby_hazards`)."""

    source_model: str = Field(default="meteofrance-arome-pi")
    run_utc: int = Field(description="start of the model run, UTC seconds (a run an hour, published about 20 minutes later)")
    interval_minutes: int = 15
    horizon_minutes: int = Field(description="from now to the end of the last step")
    steps: list[ShortTermStep] = Field(description="the steps that are not entirely past, soonest first; empty in the compact profile (the default), ask `compact=false`")
    gusts: GustSummary
    hail: HailSummary
    visibility: VisibilitySummary
    precipitation: list[PrecipitationSpan] = Field(
        description="each type of precipitation other than `none` the steps contain, in the order it first appears"
    )
    attribution: str
    note: str = Field(description="what this is and is not")
