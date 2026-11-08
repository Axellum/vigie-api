"""Models of the activities forecast (``GET /v1/activities/{spot_id}``; ADR 0016).

For one surf spot, four activities (surf, kite or wing or windsurf, boat, shore fishing), each with a verdict hour by hour, the best
daylight slot of the next 24 hours, and the criteria behind the verdict. The swell, the wind and the warnings are the providers';
the tide is computed by Vigie (EOT20, as ``GET /v1/tides``). **Every verdict is a rule of ours**, with its thresholds in ``rules``:
it knows neither the spot nor the person, and it is not for safety.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.surf import SurfSpot
from app.models.tides import TideCoefficient, TideExtreme

ACTIVITIES_LIMITS = (
    "The verdicts are rules of Vigie, written in `rules`: a few thresholds on the swell, the wind, the tide and the official coastal "
    "warnings. They do not know the beach, the bottom, the shelter of a bay or the person who goes: a beginner and an expert do not "
    "have the same limits (the wind range of the kite is the one asked for). The swell is Météo-France's MFWAM model on a grid of "
    "about 8 km; the wind is a forecast at 10 m; the tide is computed from a global model and can be an hour late or more inside a "
    "bay. Hours are whole hours."
)
ACTIVITIES_DISCLAIMER = (
    "Not for safety and not for navigation: look at the sea on the spot, the flags and the lifeguards, read the tide tables and the "
    "official warnings, and follow the instructions of the authorities."
)

Verdict = Literal["good", "possible", "poor"]
ActivityId = Literal["surf", "kite", "boat", "shore_fishing"]


class ActivityCriterion(BaseModel):
    """One thing a verdict looks at: its value, what the rule makes of it, and for the surf the points of the rating."""

    name: str = Field(description="The criterion: `swell_height`, `wind`, `gust_gap`, `wind_force`, `sea_height`, `low_water`, `coefficient`...")
    value: Optional[float] = Field(default=None, description="Its value, in `unit`; null when unknown")
    unit: str
    state: Optional[str] = Field(
        default=None, description="What the rule makes of the value, as a code: `in_range`, `too_strong`, `offshore`, `outside_window`...; null when unknown"
    )
    verdict: Optional[Verdict] = Field(default=None, description="Null when the value or what it is compared with is unknown: it then counts for nothing")
    points: Optional[int] = Field(default=None, description="Surf only: the points the rating gives (the criteria are those of `GET /v1/surf/spots/{id}`)")
    max_points: Optional[int] = None


class ActivityHour(BaseModel):
    time_utc: int = Field(description="UTC epoch seconds, the start of the hour")
    daylight: Optional[bool] = None
    verdict: Optional[Verdict] = Field(default=None, description="Null when too little is known to give one")
    reason: Optional[str] = Field(
        default=None, description="Why it is not `good`: `<criterion>.<state>` of the first criterion at the lowest verdict, or `vigilance.red`, `vigilance.orange`"
    )


class ActivitySlot(BaseModel):
    start_utc: int
    end_utc: int = Field(description="The end of the last hour of the slot (exclusive)")


class ActivityDetail(BaseModel):
    """The hour the verdict stands on (the first hour of the best slot; else the first daylight hour) and its criteria."""

    time_utc: int
    verdict: Optional[Verdict] = None
    reason: Optional[str] = None
    criteria: list[ActivityCriterion]
    points: Optional[int] = Field(default=None, description="Surf only: the points of the rating of that hour")
    out_of: Optional[int] = Field(default=None, description="Surf only: the most the criteria that could be scored can give")


class Activity(BaseModel):
    id: ActivityId
    available: bool = Field(description="False where the activity cannot be judged here (no tide model near for shore fishing): then nothing else is set")
    verdict: Optional[Verdict] = Field(
        default=None,
        description=(
            "The best verdict of the daylight hours of the next 24 hours (the question asked is: can I go today?). "
            "Null when there is no daylight hour with a verdict"
        ),
    )
    reason: Optional[str] = Field(default=None, description="The reason of the hour `detail` stands on, when it is not `good`")
    best_slot: Optional[ActivitySlot] = Field(
        default=None, description="The longest run of consecutive daylight hours at the best verdict, the first on a tie; null when that verdict is `poor` (no slot to go)"
    )
    hours: list[ActivityHour] = Field(default_factory=list, description="From the current hour, for `days` days")
    detail: Optional[ActivityDetail] = None
    note: Optional[str] = None


class ActivitySun(BaseModel):
    sunrise_utc: Optional[int] = None
    sunset_utc: Optional[int] = None


class ActivityTide(BaseModel):
    """The tide at the spot over the same days: the high and low waters, and the coefficient of Vigie's estimate (not SHOM's)."""

    extremes: list[TideExtreme]
    coefficients_estimated: list[TideCoefficient]
    note: str


class ActivityAlert(BaseModel):
    """An official coastal warning that covers the spot, in short: the words of the issuer are in ``GET /v1/vigilances``."""

    id: str
    source: str
    attribution: str = Field(description="The credit the source asks for")
    level: Optional[str] = Field(default=None, description="GREEN, YELLOW, ORANGE or RED (the API's table; null when the source's level is not in it)")
    onset_utc: Optional[int] = None
    expires_utc: Optional[int] = None
    ends_utc: Optional[int] = None


class ActivityVigilance(BaseModel):
    status: Literal["ok", "unknown"] = Field(
        description="`unknown` when the warnings could not be read: nothing is capped, and an empty list is not read as 'nothing to fear'"
    )
    alerts: list[ActivityAlert] = Field(default_factory=list, description="The warnings of phenomenon `coastal` (in France the wave-submersion vigilance) at the spot")


class WindRange(BaseModel):
    min_kn: float
    max_kn: float


class ActivitiesForecast(BaseModel):
    generated_utc: int
    spot: SurfSpot
    days: int
    wind_range: WindRange = Field(description="The wind range of the kite, wing or windsurf: the one asked for, else the default")
    activities: list[Activity]
    tide: Optional[ActivityTide] = Field(default=None, description="Null where the tide model has no cell near the spot")
    sun: list[ActivitySun] = Field(default_factory=list, description="Sunrise and sunset of each day from the first hour; empty when unknown")
    vigilance: ActivityVigilance
    rules: dict[str, str]
    derived: list[str]
    limits: str = ACTIVITIES_LIMITS
    disclaimer: str = ACTIVITIES_DISCLAIMER
    stale: bool = Field(description="True when a provider did not answer and its last answer is served")
    attribution: str


# --------------------------------------------------------------------------------------------- every spot at once (the overview)


class ActivityOverview(BaseModel):
    """One activity of one spot in short: what ``GET /v1/activities/{spot_id}`` says in its headline, without the hours or the criteria."""

    id: ActivityId
    available: bool = Field(description="False where the activity cannot be judged here (no tide model near for shore fishing): then nothing else is set")
    verdict: Optional[Verdict] = Field(default=None, description="The best verdict of the daylight hours of the next 24 hours, as in `GET /v1/activities/{spot_id}`")
    reason: Optional[str] = Field(default=None, description="Why it is not `good`: `<criterion>.<state>`, or `vigilance.red`, `vigilance.orange`")
    best_slot: Optional[ActivitySlot] = Field(default=None, description="The longest run of consecutive daylight hours at the best verdict; null when it is `poor`")
    now: Optional[Verdict] = Field(
        default=None, description="The verdict of the current hour (null when unknown, or when the hour is not in the forecast): daylight or not"
    )


class SpotActivities(BaseModel):
    id: str
    name: str
    place: str
    lat: float
    lon: float
    activities: list[ActivityOverview] = Field(description="Always surf, kite, boat, shore_fishing, in that order")
    warnings: Literal["ok", "unknown"] = Field(
        description="`unknown` when the official coastal warnings could not be read for this spot: nothing is capped, and no warning is not read as 'nothing to fear'"
    )
    coastal_level: Optional[str] = Field(
        default=None, description="The highest level of the official coastal warnings in force at the spot in the next 24 hours: ORANGE or RED (yellow changes no verdict); null otherwise"
    )


class ActivitiesOverview(BaseModel):
    generated_utc: int
    count: int
    wind_range: WindRange = Field(description="The wind range of the kite, wing or windsurf: the one asked for, else the default")
    spots: list[SpotActivities] = Field(description="Every surf spot, north to south, as `GET /v1/surf/spots`; a spot the wave model gave nothing for is left out")
    rules: dict[str, str]
    derived: list[str]
    limits: str = ACTIVITIES_LIMITS
    disclaimer: str = ACTIVITIES_DISCLAIMER
    stale: bool = Field(description="True when a provider did not answer and its last answer is served")
    attribution: str
