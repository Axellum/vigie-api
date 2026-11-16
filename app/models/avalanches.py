"""Pydantic models for avalanche bulletins (CAAML v6 from SLF Switzerland/Liechtenstein and EAWS Euregio; Météo-France BRA)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# European Avalanche Danger Scale (EAWS CAAML v6 `mainValue` -> numeric level 1..5).
EAWS_DANGER_LEVELS: dict[str, int] = {
    "low": 1,
    "moderate": 2,
    "considerable": 3,
    "high": 4,
    "very_high": 5,
}
EAWS_DANGER_LABELS: dict[int, str] = {level: label for label, level in EAWS_DANGER_LEVELS.items()}

AvalancheSourceId = Literal["slf_avalanche", "euregio_avalanche", "meteofrance_bra"]
AvalancheFeedStatus = Literal["active", "confirmed_none", "outside_coverage", "unavailable"]


class AvalancheQuery(BaseModel):
    """Echoed WGS84 query coordinates for `GET /v1/avalanches`."""

    lat: float
    lon: float


class AvalancheElevation(BaseModel):
    """Elevation band boundaries reported in CAAML v6 (meters or `'treeline'`)."""

    lower_bound: Optional[str | int] = Field(
        default=None,
        description="Lower elevation boundary in meters above sea level or `'treeline'`, when specified.",
    )
    upper_bound: Optional[str | int] = Field(
        default=None,
        description="Upper elevation boundary in meters above sea level or `'treeline'`, when specified.",
    )


class AvalancheDangerRating(BaseModel):
    """Single CAAML v6 danger rating entry."""

    main_value: str = Field(..., description="CAAML mainValue (`low`, `moderate`, `considerable`, `high`, `very_high`).")
    level: Optional[int] = Field(default=None, ge=1, le=5, description="Numeric European danger level 1..5, when standard.")
    valid_time_period: Optional[str] = Field(default=None, description="`all_day`, `earlier`, or `later` when specified.")
    elevation: Optional[AvalancheElevation] = Field(default=None, description="Applicable elevation band, when specified.")
    subdivision: Optional[str] = Field(
        default=None,
        description="Provider-supplied danger level subdivision (e.g. SLF `plus`, `neutral`, `minus`).",
    )


class AvalancheProblem(BaseModel):
    """Single CAAML v6 avalanche problem entry."""

    problem_type: str = Field(
        ...,
        description="EAWS avalanche problem type (`new_snow`, `wind_slab`, `persistent_weak_layers`, `wet_snow`, `gliding_snow`, `favourable_situation`). "
        "For `meteofrance_bra` it is derived from the code of the typical avalanche situation (`SitAvalTyp`): codes 1, 2 and 4 were checked against live "
        "bulletins, 3 and 5 follow the EAWS order and are not confirmed.",
    )
    danger_rating_value: Optional[str] = Field(default=None, description="Problem-specific CAAML danger value when provided.")
    danger_level: Optional[int] = Field(default=None, ge=1, le=5, description="Numeric danger level 1..5 for this problem, if any.")
    valid_time_period: Optional[str] = Field(default=None, description="`all_day`, `earlier`, or `later` when specified.")
    elevation: Optional[AvalancheElevation] = Field(default=None, description="Applicable elevation band, when specified.")
    aspects: list[str] = Field(default_factory=list, description="Compass aspects affected (e.g. `N`, `NE`, `NW`).")
    snowpack_stability: Optional[str] = Field(default=None, description="EAWS snowpack stability (`very_poor`, `poor`, `fair`, `good`).")
    frequency: Optional[str] = Field(default=None, description="EAWS trigger frequency (`many`, `some`, `few`, `none`).")
    avalanche_size: Optional[int] = Field(default=None, ge=1, le=5, description="Expected maximum avalanche size (1..5), when provided.")
    avalanche_type: Optional[str] = Field(default=None, description="Provider custom avalanche type (e.g. `slab`, `loose`).")
    core_zone_text: Optional[str] = Field(default=None, description="Provider verbatim core-zone text when present.")
    comment: Optional[str] = Field(default=None, description="Provider verbatim problem description (plain text, tags stripped).")


class AvalancheRegion(BaseModel):
    """EAWS micro-region referenced by a CAAML v6 bulletin."""

    region_id: str = Field(..., description="Official EAWS/national micro-region identifier (e.g. `CH-6111`, `AT-07-14`).")
    name: Optional[str] = Field(default=None, description="Official micro-region name from the bulletin or EAWS shapefile.")


class AvalancheTendency(BaseModel):
    """CAAML v6 outlook / tendency block."""

    tendency_type: Optional[str] = Field(default=None, description="`decreasing`, `steady`, or `increasing`, when provided.")
    valid_from: Optional[datetime] = Field(default=None, description="Tendency validity start (UTC).")
    valid_until: Optional[datetime] = Field(default=None, description="Tendency validity end (UTC).")
    highlights: Optional[str] = Field(default=None, description="Provider verbatim outlook highlights.")
    comment: Optional[str] = Field(default=None, description="Provider verbatim outlook text.")


class AvalancheBulletin(BaseModel):
    """Normalized CAAML v6 avalanche bulletin covering a query point or region."""

    bulletin_id: str = Field(..., description="Official provider bulletin UUID/identifier (`FR-BRA-<massif code>-<date>` for Météo-France, built by Vigie).")
    source: AvalancheSourceId = Field(..., description="Collector key (`slf_avalanche`, `euregio_avalanche` or `meteofrance_bra`).")
    lang: Optional[str] = Field(default=None, description="Bulletin language (`en`).")
    publication_time: Optional[datetime] = Field(default=None, description="Publication timestamp (UTC).")
    valid_from: Optional[datetime] = Field(default=None, description="Bulletin validity start (UTC).")
    valid_until: Optional[datetime] = Field(default=None, description="Bulletin validity end (UTC).")
    next_update: Optional[datetime] = Field(default=None, description="Scheduled next update timestamp (UTC), when published.")
    unscheduled: bool = Field(default=False, description="True when SLF/provider marks this as an unscheduled update.")
    is_expired: bool = Field(default=False, description="True when `valid_until` is strictly before the current server UTC time.")
    max_danger_level: Optional[int] = Field(default=None, ge=1, le=5, description="Highest numeric danger rating in this bulletin (1..5).")
    max_danger_label: Optional[str] = Field(default=None, description="CAAML label for `max_danger_level`.")
    regions: list[AvalancheRegion] = Field(default_factory=list, description="Matched or listed EAWS micro-regions.")
    danger_ratings: list[AvalancheDangerRating] = Field(default_factory=list)
    avalanche_problems: list[AvalancheProblem] = Field(default_factory=list)
    avalanche_activity_highlights: Optional[str] = Field(default=None)
    avalanche_activity_comment: Optional[str] = Field(default=None)
    snowpack_highlights: Optional[str] = Field(default=None)
    snowpack_comment: Optional[str] = Field(default=None)
    tendency: list[AvalancheTendency] = Field(default_factory=list)
    bulletin_url: Optional[str] = Field(default=None, description="Official public bulletin page URL.")
    attribution: str = Field(..., description="Required attribution line and licence for this bulletin.")


class AvalancheStatus(BaseModel):
    """Compact avalanche summary attached to `PointResponse.avalanche` inside covered Alpine regions.

    Informative only: avalanche danger levels do not modify `local_alert_level`.
    """

    status: Literal["active", "confirmed_none"] = Field(
        ...,
        description="`active` when an in-validity bulletin covers this point; "
        "`confirmed_none` when the point lies in a covered territory with no currently active bulletin "
        "(off-season, expired feed, or uncovered summer day).",
    )
    covered_by: AvalancheSourceId = Field(..., description="Authoritative CAAML provider matched for this coordinate.")
    region_id: Optional[str] = Field(default=None, description="Matched EAWS/SLF micro-region identifier or `FR-BRA-<massif code>`, when resolved.")
    region_name: Optional[str] = Field(default=None, description="Matched micro-region name, when resolved.")
    max_danger_level: Optional[int] = Field(default=None, ge=1, le=5, description="Highest danger level (1..5) in force, or null.")
    max_danger_label: Optional[str] = Field(
        default=None,
        description="CAAML label (`low`, `moderate`, `considerable`, `high`, `very_high`), or null.",
    )
    subdivision: Optional[str] = Field(default=None, description="SLF danger subdivision (`plus`, `neutral`, `minus`), when available.")
    problem_types: list[str] = Field(default_factory=list, description="Distinct CAAML problem types currently active for this zone.")
    valid_from: Optional[datetime] = Field(default=None, description="Validity start (UTC) of the active bulletin.")
    valid_until: Optional[datetime] = Field(default=None, description="Validity end (UTC) of the active bulletin.")
    unscheduled: bool = Field(default=False, description="True if SLF marks the active bulletin as unscheduled.")
    bulletin_url: str = Field(..., description="Official public bulletin page URL.")
    stale: bool = Field(default=False, description="True when served from stale cache after an upstream error.")
    attribution: str = Field(..., description="Required provider attribution and CC BY 4.0 licence.")


class AvalancheResponse(BaseModel):
    """Full response returned by `GET /v1/avalanches`."""

    version: str
    server_utc: datetime
    query: AvalancheQuery
    status: AvalancheFeedStatus = Field(
        ...,
        description="`active` (current bulletin in force), `confirmed_none` (in coverage, no active bulletin right now), "
        "`outside_coverage` (outside SLF CH/LI, Euregio AT-07/IT-32-BZ/IT-32-TN and the Météo-France BRA massifs), or `unavailable` "
        "(upstream error without cache, or `meteofrance_bra` covers the point but this instance has no Météo-France key).",
    )
    covered_by: Optional[AvalancheSourceId] = Field(default=None, description="Provider that covers the point (`slf_avalanche`, `euregio_avalanche` or `meteofrance_bra`).")
    note: str = Field(..., description="Explicit explanation of validity, off-season status, or coverage boundary.")
    region_id: Optional[str] = Field(default=None, description="Matched micro-region ID, when resolved.")
    region_name: Optional[str] = Field(default=None, description="Matched micro-region name, when resolved.")
    max_danger_level: Optional[int] = Field(default=None, ge=1, le=5, description="Maximum active danger level (1..5), or null.")
    max_danger_label: Optional[str] = Field(default=None, description="CAAML danger label for `max_danger_level`, or null.")
    bulletins: list[AvalancheBulletin] = Field(default_factory=list, description="Matching bulletins (active by default; includes expired if requested).")
    stale: bool = Field(default=False, description="True when served from stale cache.")
    attribution: Optional[str] = Field(default=None, description="Required provider attribution and licence, when a source was consulted.")
