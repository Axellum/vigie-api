"""Models of the events (``GET /v1/events``) and of the families of phenomena (``GET /v1/families``).

An event is **ours**: a group of official warnings, a sequence of earthquakes, or one other hazard, with a short title, a level, a
family, an impact score and a period that Vigie works out (listed in ``derived``). Inside, the warnings and hazards it groups are given as ``GET /v1/vigilances``
and ``GET /v1/geojson/hazards`` give them: the words of the issuer untouched. How the groups are made:
``docs/decisions/0012-events-and-impact.md``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.models.vigilance import Vigilance, VigilanceCoverage, VigilancePhenomenon

FamilyId = Literal[
    "wind",
    "rain-flood",
    "thunderstorm",
    "snow-ice",
    "heat",
    "cold",
    "fire",
    "coastal",
    "earthquake",
    "volcano",
    "cyclone",
    "tsunami",
    "air",
    "space",
    "other",
]


class Family(BaseModel):
    """A family of phenomena: what a filter or a profile picks, and what it holds."""

    id: FamilyId
    name: str = Field(..., description="English name (ours)")
    phenomena: list[str] = Field(default_factory=list, description="The `phenomenon` values of the warnings in this family")
    hazard_categories: list[str] = Field(default_factory=list, description="The `category` values of the hazards in this family")
    note: Optional[str] = None


class FamiliesResponse(BaseModel):
    """The families of phenomena (``GET /v1/families``)."""

    version: str = "1.0"
    families: list[Family]
    world_min_impact: int = Field(..., description="The `min_impact` of a world view: what counts (ADR 0012)")
    note: str


class Event(BaseModel):
    """A group of warnings, a sequence of earthquakes, or one other hazard."""

    id: str = Field(..., description="Identifier of the event in this answer (the same while its first warning or hazard stays)")
    kind: Literal["warnings", "earthquakes", "tsunami", "hazard"] = Field(
        ...,
        description=(
            "`warnings`: official warnings of one country, phenomenon and level whose periods overlap and whose areas are close; "
            "`earthquakes`: earthquakes of one sequence (close in place and time); `tsunami`: the hazard of a tsunami warning centre and "
            "the official messages that carry its code; `hazard`: any other hazard on its own"
        ),
    )
    phenomenon: Optional[VigilancePhenomenon] = Field(
        default=None, description="For `warnings`: the phenomenon of the warnings (the API's own); null when the source names none the tables cover"
    )
    category: Optional[HazardCategory] = Field(default=None, description="For the other kinds: the category of the strongest hazard")
    family: FamilyId = Field(..., description="DERIVED: the family of the phenomenon or category (`GET /v1/families`)")
    impact: int = Field(
        ..., ge=0, le=100, description="DERIVED: Vigie's impact score, 0 to 100 (ADR 0012); `min_impact=60` keeps what counts in a world view"
    )
    impact_reasons: list[str] = Field(default_factory=list, description="DERIVED: what the score rests on, in English")
    level: Optional[SeverityLevel] = Field(
        default=None, description="DERIVED: the level of the warnings, or the strongest severity of the hazards (or of the tsunami messages); null when unknown"
    )
    title: str = Field(..., description="DERIVED: a short title in English, ours (a client writes its own in its language from the other fields)")
    country: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2 country; null when the event is in none (at sea) or in several")
    areas_count: Optional[int] = Field(default=None, description="DERIVED: how many areas the warnings name; null for hazards")
    area_names: list[str] = Field(default_factory=list, description="The names of those areas, as the sources give them")
    phenomenon_words: list[str] = Field(default_factory=list, description="The words of the sources for the phenomenon (`phenomenon_original`), as given")
    start_utc: Optional[int] = Field(default=None, description="DERIVED: the earliest start (onset, else effective time, time of issue; the time of a hazard)")
    end_utc: Optional[int] = Field(
        default=None, description="DERIVED: the latest end of the warnings (the later of end and expiry), the last earthquake of a sequence; null when unknown or open"
    )
    bbox: Optional[list[float]] = Field(
        default=None, description="DERIVED: [west, south, east, north] around the areas or hazards whose place is known on this instance; null when none is"
    )
    sources: list[str] = Field(default_factory=list, description="The sources of the warnings and hazards")
    alert_ids: list[str] = Field(default_factory=list, description="The ids of the warnings (`GET /v1/vigilances`), even when `alerts` is left out")
    alerts: list[Vigilance] = Field(default_factory=list, description="The official warnings of the event, as `GET /v1/vigilances` serves them (empty with `compact=true`)")
    hazards: list[HazardAlert] = Field(
        default_factory=list, description="The hazards of the event, as `GET /v1/geojson/hazards` describes them (empty with `compact=true`)"
    )
    attribution: Optional[str] = Field(default=None, description="The credits of the sources of this event")
    derived: list[str] = Field(default_factory=list, description="The fields of this event that Vigie worked out")


class EventsQuery(BaseModel):
    """What was asked."""

    country: Optional[str] = None
    min_impact: Optional[int] = None
    families: Optional[list[str]] = None
    compact: bool = False


class EventCounts(BaseModel):
    """How many events, per family and per level (the green ones are counted apart: a client leaves them out of its counters)."""

    events: int
    by_family: dict[str, int] = Field(default_factory=dict)
    by_level: dict[str, int] = Field(default_factory=dict, description="Per level; `unknown` for an event whose level is not known")


class EventsResponse(BaseModel):
    """The events of the world or of a country, the strongest impact first."""

    version: str = "1.0"
    server_utc: int
    query: EventsQuery
    events: list[Event] = Field(default_factory=list)
    counts: EventCounts
    coverage: list[VigilanceCoverage] = Field(
        default_factory=list, description="For each source of official warnings read, what the API knows of it (as in `GET /v1/vigilances`)"
    )
    hazard_sources_answered: int = Field(..., description="How many hazard sources answered")
    hazard_sources_total: int = Field(..., description="How many hazard sources were asked")
    attribution: Optional[str] = Field(default=None, description="The credits of every source of the events of this answer")
    disclaimer: Optional[str] = Field(default=None, description="Notice the sources ask to publish with their warnings")
    note: Optional[str] = None
