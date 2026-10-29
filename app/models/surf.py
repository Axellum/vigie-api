"""Models of the surf forecast (``GET /v1/surf/spots``, ``GET /v1/surf/spots/{spot_id}``).

The swell and the wind are the providers' (Météo-France's MFWAM and Open-Meteo, through Open-Meteo); the tide is computed by
Vigie (EOT20, as ``GET /v1/tides``). Everything else is worked out by Vigie and named in ``derived``: the facing of a spot (from the
OpenStreetMap coastline), the wind relative to the coast, the swell's angle to the beach, its power, the tide stage, the rating
and the best slot. The rating is a rule of ours with its thresholds in ``rules``; it does not know the spot.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SURF_LIMITS = (
    "The swell is Météo-France's MFWAM wave model on its grid of about 8 km, computed every 3 hours (Open-Meteo gives it every "
    "hour): it does not see the sandbanks, the reefs, the jetties or the shelter of a beach, and the waves on the beach can be "
    "much smaller or larger. The wind is a forecast at 10 m. The tide is computed from a global model and can be an hour late or "
    "more inside a bay. The facing of a spot is worked out from the OpenStreetMap coastline. The rating is Vigie's own rule: a "
    "few thresholds, given in `rules`; it knows neither the spot nor the surfer."
)
SURF_DISCLAIMER = (
    "Not for safety and not for navigation: look at the sea on the spot, the flags and the lifeguards before going in."
)

Criterion = Literal["swell_height", "swell_period", "swell_direction", "wind", "tide"]


class SpotEvidence(BaseModel):
    """An OpenStreetMap element that makes the place a spot: a surf spot, or a surf school or club near the beach."""

    osm: str = Field(description="OpenStreetMap element, `node/1`, `way/2` or `relation/3`")
    name: Optional[str] = None
    kind: Literal["spot", "school"]


class SurfSpot(BaseModel):
    id: str
    name: str = Field(description="The name OpenStreetMap gives the spot or its beach")
    place: Optional[str] = Field(
        default=None, description="The nearest city, town or village OpenStreetMap names, else a suburb within 2 km (ours)"
    )
    lat: float
    lon: float
    facing_deg: int = Field(description="Direction from the beach to the open sea, degrees from north (ours, from the coastline)")
    beach_surface: Optional[str] = Field(default=None, description="The `surface` tag of the beach (what the beach is made of)")
    bottom: Optional[str] = Field(default=None, description="The bottom under the waves: unknown (no open source gives it)")
    best_tide: Optional[Literal["low", "mid", "high", "any"]] = Field(default=None, description="Unknown (no open source)")
    best_swell_from_deg: Optional[int] = Field(default=None, description="Unknown (no open source)")
    osm: str
    evidence: list[SpotEvidence]


class SurfSpots(BaseModel):
    count: int
    built_utc: str
    spots: list[SurfSpot]
    derived: list[str]
    licence: str
    attribution: str


class Swell(BaseModel):
    height_m: Optional[float] = None
    period_s: Optional[float] = None
    from_deg: Optional[int] = Field(default=None, description="Where the waves come from, degrees from north")


class SurfWind(BaseModel):
    speed_kmh: Optional[float] = None
    gust_kmh: Optional[float] = None
    from_deg: Optional[int] = None
    relative: Optional[Literal["offshore", "cross-shore", "onshore"]] = Field(
        default=None, description="Ours: from the land side within 45 degrees, from the sea side within 45 degrees, or across"
    )


class SurfTide(BaseModel):
    height_m: float = Field(description="Relative to the model's mean sea level (EOT20, computed by Vigie)")
    trend: Literal["rising", "falling"]
    stage: Optional[Literal["low", "mid", "high"]] = Field(
        default=None, description="Ours: the third of the range between the low and the high water around this hour"
    )


class SurfCriterionScore(BaseModel):
    name: Criterion
    value: Optional[float] = None
    unit: str
    points: Optional[int] = Field(default=None, description="null: the value or what it is compared with is unknown")
    max_points: int


class SurfRating(BaseModel):
    points: int
    out_of: int = Field(description="The most the criteria that could be scored can give")
    criteria: list[SurfCriterionScore]
    unknown: list[Criterion] = Field(description="The criteria left out because a value is unknown")


class SurfHour(BaseModel):
    time_utc: int
    daylight: Optional[bool] = None
    waves: Swell = Field(description="The whole sea state: significant height, mean period and direction")
    swell: Swell = Field(description="The primary swell")
    secondary_swell: Swell
    wind_wave_height_m: Optional[float] = None
    swell_power_kw_m: Optional[float] = Field(
        default=None, description="Ours: deep-water power of the primary swell per metre of crest, 0.49 x height² x period"
    )
    swell_angle_deg: Optional[int] = Field(default=None, description="Ours: between where the swell comes from and the facing")
    wind: SurfWind
    tide: Optional[SurfTide] = None
    rating: Optional[SurfRating] = None


class SurfSlot(BaseModel):
    time_utc: int
    rating: SurfRating


class SurfForecast(BaseModel):
    generated_utc: int
    spot: SurfSpot
    days: int
    swell_model: str
    swell_cell: Optional[dict[str, float]] = Field(default=None, description="The grid point of the wave model that answered")
    hours: list[SurfHour]
    best_slot: Optional[SurfSlot] = Field(
        default=None, description="Ours: the daylight hour of the next 72 with the best rating (the first one on a tie)"
    )
    rules: dict[str, str] = Field(description="The thresholds of the rating, criterion by criterion (ours)")
    derived: list[str]
    stale: bool = False
    limits: str = SURF_LIMITS
    disclaimer: str = SURF_DISCLAIMER
    attribution: str
