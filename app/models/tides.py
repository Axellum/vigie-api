"""Models of the tide (``GET /v1/tides``, ``GET /v1/tides/coast``; ADR 0013).

Every figure here is computed by Vigie from the EOT20 ocean tide model at the nearest model cell: nothing is a provider's report, no
provider is asked, and it is **not for navigation**. The heights are relative to the mean sea level of the model, not to the chart
datum of a port. The "estimated coefficient" is ours, unofficial: the French coefficient is SHOM's and is not used.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

NOT_FOR_NAVIGATION = (
    "Tide computed by Vigie from a global ocean tide model: not for navigation, and not an official tide table. Heights are relative "
    "to the mean sea level of the model, not to the chart datum of a port."
)
LIMITS = (
    "The tide of the open coast near the point, from a model cell up to 30 km away. Inside a bay, a harbour, an estuary, a lagoon or a "
    "sound the real tide can come an hour or more later and be higher or lower, and the model can be wrong there; the weather (surge) is "
    "not included. Measured: on the coasts of France the high and low waters came within 3 to 23 minutes (median) of the observed ones; in "
    "harbours and bays of the United States up to 2 h 22 early (docs/measurements/tides-2026-10.md)."
)
COEFFICIENT_NOTE = (
    "Estimated by Vigie from the model's tide at Brest: 100 times the range of each semi-diurnal tide at Brest divided by 6.10 m. Not the "
    "official coefficient, which is SHOM's. In France the coefficient is used for the Atlantic, Channel and North Sea coasts, with the "
    "value of the high water at Brest; it says nothing of the Mediterranean or of other countries."
)


class TideCell(BaseModel):
    """The model cell the tide is computed at."""

    lat: float = Field(..., description="Latitude of the centre of the cell")
    lon: float = Field(..., description="Longitude of the centre of the cell")
    distance_km: float = Field(..., description="Distance from the point asked to the centre of the cell (km)")


class TideHeight(BaseModel):
    time_utc: int = Field(..., description="UTC epoch seconds")
    height_m: float = Field(..., description="Height relative to the model's mean sea level (m)")


class TideExtreme(BaseModel):
    """A high or a low water."""

    time_utc: int = Field(..., description="UTC epoch seconds, to the minute")
    kind: Literal["high", "low"]
    height_m: float = Field(..., description="Height relative to the model's mean sea level (m)")
    range_m: Optional[float] = Field(
        default=None, description="The range of the tide that ends here: the difference with the previous extreme (m); null for the first"
    )


class TideNow(BaseModel):
    """The tide at the time of the answer."""

    time_utc: int
    height_m: float
    trend: Literal["rising", "falling"] = Field(..., description="Whether the height grows or falls at this time")
    next_high: Optional[TideExtreme] = None
    next_low: Optional[TideExtreme] = None


class TideCoefficient(BaseModel):
    brest_high_water_utc: int = Field(..., description="Time of the high water at Brest the value belongs to (UTC epoch seconds)")
    value: int = Field(..., description="Estimated coefficient (ours, unofficial): 20 is a small tide, 120 the largest")


class TidesQuery(BaseModel):
    lat: float
    lon: float
    start_utc: int
    days: float
    interval_minutes: int


class TidesResponse(BaseModel):
    """The tide at a point: heights, high and low waters, ranges, the tide now, and the estimated French coefficient."""

    generated_utc: int
    query: TidesQuery
    model: str = Field(..., description="The ocean tide model the tide is computed from")
    cell: TideCell
    end_utc: int
    heights: list[TideHeight] = Field(default_factory=list, description="Every `interval_minutes` from `start_utc` to `end_utc`")
    extremes: list[TideExtreme] = Field(default_factory=list, description="The high and low waters of the window, in time order")
    now: TideNow
    coefficients_estimated: list[TideCoefficient] = Field(
        default_factory=list, description="The estimated French coefficient of each high water at Brest in the window (ours, unofficial)"
    )
    coefficient_note: str = COEFFICIENT_NOTE
    derived: list[str] = Field(
        default_factory=lambda: ["heights", "extremes", "now", "coefficients_estimated"],
        description="The fields computed by Vigie: here, all of them",
    )
    limits: str = LIMITS
    disclaimer: str = NOT_FOR_NAVIGATION
    attribution: str


class CoastalTideProperties(BaseModel):
    """The tide now at one point of a coast."""

    lat: float
    lon: float
    height_m: float = Field(..., description="Height now, relative to the model's mean sea level (m)")
    trend: Literal["rising", "falling"]
    next_high: Optional[TideExtreme] = None
    next_low: Optional[TideExtreme] = None


class CoastalTideFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict
    properties: CoastalTideProperties


class CoastalTidesMetadata(BaseModel):
    generated_utc: int
    model: str
    bbox: list[float] = Field(..., description="west, south, east, north (degrees)")
    spacing_deg: float = Field(..., description="At most one point per square of this side: the coast is thinned to stay under `limit`")
    count: int = Field(..., description="The points returned")
    derived: list[str] = Field(default_factory=lambda: ["height_m", "trend", "next_high", "next_low"])
    limits: str = LIMITS
    disclaimer: str = NOT_FOR_NAVIGATION
    attribution: str


class CoastalTides(BaseModel):
    """The tide now along the coasts of a box, as an RFC 7946 FeatureCollection of the model cells next to the coast."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[CoastalTideFeature] = Field(default_factory=list)
    metadata: CoastalTidesMetadata
