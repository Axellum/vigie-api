"""Models of the status of the watched points (``GET /v1/watched-points/status``).

For each point: the events of the last days within its radius, from the history of hazards the API keeps, and, when there are none,
whether the API may say "nothing happened". It may only if **every hazard source answered at the last reading and has been read
without a gap** over the period it speaks of. Otherwise the point says which source is unreachable, or that it has not been
watched long enough (``docs/decisions/0008-sensitive-points.md``).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.places import WatchedPoint
from app.models.schemas import HazardCategory, SeverityLevel

# events: something happened within the radius; quiet: nothing did, and the sources prove it; unknown: nothing was found but the
# sources do not prove it (one did not answer, or the API has not read them without a gap for that long).
WatchState = Literal["events", "quiet", "unknown"]


class WatchedSource(BaseModel):
    """A hazard source, and whether the API can vouch for what it did not report."""

    provider_id: str = Field(..., description="Identifier of the source, as in `GET /v1/providers`")
    name: str = Field(..., description="Name of the source")
    answered: bool = Field(..., description="True when the source answered at the last reading (a stale answer served after a failure does not count)")
    observed_since_utc: Optional[int] = Field(
        default=None,
        description="Start (UTC) of the current run of readings of this source without a gap longer than the allowed one "
        "(`VIGIE_QUIET_MAX_GAP_S`); null when it has never answered",
    )


class WatchedEvent(BaseModel):
    """A hazard that happened, or is going on, within the radius of a watched point."""

    id: str
    category: HazardCategory
    severity: SeverityLevel
    title: str
    source: str
    sources: list[str] = Field(default_factory=list)
    timestamp_utc: int = Field(..., description="Event time, UTC epoch seconds, as the source gives it")
    lat: float
    lon: float
    distance_km: float = Field(..., ge=0.0, description="Great-circle distance from the point to the event's position, in km")
    value_primary: Optional[float] = None
    value_unit: Optional[str] = None
    ongoing: bool = Field(..., description="True when the event is in the list of active hazards now")
    attribution: Optional[str] = Field(default=None, description="Credit each reporting source asks for")


class WatchedPointStatus(BaseModel):
    """What happened near one watched point."""

    point: WatchedPoint
    state: WatchState = Field(
        ..., description="events: something happened within the radius. quiet: nothing did and the sources vouch for it. unknown: nothing was found but they do not"
    )
    events_count: int = Field(..., ge=0, description="Events within the radius in the window (`events` holds the most recent ones)")
    events: list[WatchedEvent] = Field(default_factory=list, description="The most recent events, nearest to the present first")
    quiet_since_utc: Optional[int] = Field(
        default=None,
        description="Only with state `quiet`: nothing happened within the radius since this instant (UTC). It is the start of the "
        "window, or later when the sources have been read without a gap for less than the window",
    )
    window_covered: Optional[bool] = Field(
        default=None, description="Only with state `quiet`: true when `quiet_since_utc` is the start of the whole window"
    )
    unreachable_sources: list[str] = Field(
        default_factory=list,
        description="`provider_id` of the sources that did not answer at the last reading. With events, the list may be incomplete; "
        "without, the point cannot be said to be quiet",
    )


class WatchedPointsStatus(BaseModel):
    """The status of every watched point (``GET /v1/watched-points/status``)."""

    generated_utc: int
    window_days: int = Field(..., description="Length of the window the events are looked for in, in days")
    window_start_utc: int
    observed_since_utc: Optional[int] = Field(
        default=None,
        description="Start of the period every source has been read without a gap; null when a source has never answered",
    )
    sources: list[WatchedSource]
    points: list[WatchedPointStatus]
    derived: list[str] = Field(
        default_factory=lambda: ["state", "quiet_since_utc", "window_covered", "distance_km"],
        description="The fields computed by Vigie from its own readings: everything else is a source's report",
    )
    attribution: Optional[str] = Field(default=None, description="Credit of the sources that reported the events listed")
