"""Pydantic models for French drought restriction alerts from VigiEau (api.vigieau.gouv.fr)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

DroughtLevel = Literal["vigilance", "alerte", "alerte_renforcee", "crise"]
DroughtWaterType = Literal["AEP", "SUP", "SOU"]
DroughtAvailabilityStatus = Literal["available", "confirmed_none", "unavailable"]
DroughtProfile = Literal["particulier", "entreprise", "collectivite", "exploitation"]

# Official VigiEau gravity scale in ascending order of restriction severity.
# ``vigilance`` is awareness only (encouraging economical water use, no mandatory restrictions);
# mandatory restrictions start at ``alerte``.
DROUGHT_LEVEL_RANK: dict[str, int] = {
    "vigilance": 1,
    "alerte": 2,
    "alerte_renforcee": 3,
    "crise": 4,
}


def worst_drought_level(levels: list[Optional[DroughtLevel]]) -> Optional[DroughtLevel]:
    """Return the highest non-null VigiEau gravity level from ``levels``, or ``None``."""
    best: Optional[DroughtLevel] = None
    best_rank = 0
    for level in levels:
        if level is None:
            continue
        rank = DROUGHT_LEVEL_RANK.get(level, 0)
        if rank > best_rank:
            best_rank = rank
            best = level
    return best


def restrictions_in_force_for(level: Optional[DroughtLevel]) -> bool:
    """Return ``True`` when mandatory restrictions apply (``alerte`` or higher), ``False`` for ``vigilance`` or ``None``."""
    if level is None:
        return False
    return DROUGHT_LEVEL_RANK.get(level, 0) >= DROUGHT_LEVEL_RANK["alerte"]


class DroughtAvailability(BaseModel):
    """Per-water-type availability metadata returned verbatim by VigiEau ``/api/zones/v2`` and ``/api/departements``."""

    status: DroughtAvailabilityStatus
    as_of: Optional[str] = Field(default=None, description="Upstream ``asOf`` ISO-8601 UTC timestamp when known")
    source_revision: Optional[str] = Field(default=None, description="Upstream ``sourceRevision`` identifier")
    official_url: Optional[str] = Field(default=None, description="Optional upstream departmental fallback URL")
    freshness: Optional[str] = Field(default=None, description="Optional upstream freshness marker (e.g. ``updating``)")
    pending_since: Optional[str] = Field(default=None, description="ISO-8601 timestamp when an update started, if any")


class DroughtDecree(BaseModel):
    """Prefectural restriction decree attached to a VigiEau drought zone."""

    id: Optional[int] = None
    pdf_url: Optional[str] = Field(default=None, description="Prefectural restriction decree PDF URL (``cheminFichier``)")
    framework_pdf_url: Optional[str] = Field(
        default=None,
        description="Framework decree PDF URL (``cheminFichierArreteCadre``)",
    )
    valid_from: Optional[str] = Field(default=None, description="Validity start timestamp (``dateDebutValidite``)")
    valid_until: Optional[str] = Field(default=None, description="Validity end timestamp (``dateFinValidite``)")


class DroughtUsageRestriction(BaseModel):
    """One water usage restriction rule attached to an active VigiEau zone."""

    id: Optional[int] = None
    name: str
    theme: str
    description: Optional[str] = None
    concerns_individual: Optional[bool] = None
    concerns_business: Optional[bool] = None
    concerns_municipality: Optional[bool] = None
    concerns_farm: Optional[bool] = None


class DroughtZone(BaseModel):
    """One active VigiEau drought alert zone with decree links and usage restrictions."""

    id: int
    name: str
    code: Optional[str] = None
    water_type: DroughtWaterType = Field(
        ...,
        description="``AEP`` (drinking water), ``SUP`` (surface water), or ``SOU`` (groundwater)",
    )
    department: Optional[str] = None
    level: Optional[DroughtLevel] = Field(default=None, description="Native VigiEau ``niveauGravite`` value")
    influenced_resource: Optional[bool] = Field(
        default=None,
        description="Upstream ``ressourceInfluencee`` flag (river/reservoir regulated by upstream releases)",
    )
    sandre_id: Optional[int] = None
    decree: Optional[DroughtDecree] = None
    municipal_decree_url: Optional[str] = None
    usages: list[DroughtUsageRestriction] = Field(default_factory=list)


class DroughtZoneSummary(BaseModel):
    """Compact zone summary embedded in ``GET /v1/point`` responses."""

    id: int
    name: str
    water_type: DroughtWaterType
    level: Optional[DroughtLevel] = None
    influenced_resource: Optional[bool] = None
    decree_valid_until: Optional[str] = None


class DroughtStatus(BaseModel):
    """Compact French drought restriction summary embedded in ``GET /v1/point`` (null outside French coverage)."""

    status: Literal["available", "confirmed_none", "unavailable"] = Field(
        ...,
        description="Aggregate status across water types (`confirmed_none` = checked and no zone active; `unavailable` = incomplete/outage)",
    )
    max_level: Optional[DroughtLevel] = Field(
        default=None,
        description="Derived highest ``niveauGravite`` across active zones; null when none or unavailable",
    )
    restrictions_in_force: Optional[bool] = Field(
        default=None,
        description="Derived: true when any zone is at ``alerte``, ``alerte_renforcee`` or ``crise``; false for ``vigilance`` or ``confirmed_none``; null when unavailable",
    )
    department: Optional[str] = None
    zones: list[DroughtZoneSummary] = Field(default_factory=list)
    availability: dict[str, DroughtAvailability] = Field(default_factory=dict)
    as_of: Optional[str] = None
    attribution: str


class DroughtResponse(BaseModel):
    """Full response returned by ``GET /v1/drought`` for a position or French commune INSEE code."""

    version: str = "1.1"
    server_utc: int
    query: dict[str, Any] = Field(default_factory=dict)
    status: Literal["available", "confirmed_none", "unavailable", "outside_coverage", "ambiguous_commune", "invalid_query"]
    note: Optional[str] = None
    ambiguous_water_types: list[str] = Field(default_factory=list)
    max_level: Optional[DroughtLevel] = None
    restrictions_in_force: Optional[bool] = None
    department: Optional[str] = None
    zones: list[DroughtZone] = Field(default_factory=list)
    availability: dict[str, DroughtAvailability] = Field(default_factory=dict)
    as_of: Optional[str] = None
    stale: bool = False
    attribution: str


class DroughtDepartmentSummary(BaseModel):
    """One French department's maximum drought restriction levels across water types."""

    code: str
    name: str
    region: Optional[str] = None
    max_level: Optional[DroughtLevel] = None
    surface_level: Optional[DroughtLevel] = Field(default=None, description="Upstream ``niveauGraviteSupMax``")
    groundwater_level: Optional[DroughtLevel] = Field(default=None, description="Upstream ``niveauGraviteSouMax``")
    drinking_water_level: Optional[DroughtLevel] = Field(default=None, description="Upstream ``niveauGraviteAepMax``")
    restrictions_in_force: Optional[bool] = None
    availability: dict[str, DroughtAvailability] = Field(default_factory=dict)


class DroughtDepartmentsResponse(BaseModel):
    """Response returned by ``GET /v1/drought/departments``."""

    version: str = "1.1"
    server_utc: int
    count: int
    departments: list[DroughtDepartmentSummary] = Field(default_factory=list)
    stale: bool = False
    attribution: str
