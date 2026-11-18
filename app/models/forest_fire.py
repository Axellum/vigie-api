"""Pydantic models of the forest fire danger of Météo-France (``GET /v1/forest-fire``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ForestFireStatus = Literal["available", "out_of_season", "outside_coverage", "not_configured", "unavailable"]


class ForestFireLevel(BaseModel):
    """A danger level of one day: 1 low, 2 moderate, 3 high, 4 very high (Météo-France's four levels)."""

    level: Optional[int] = Field(default=None, ge=1, le=4)
    label: Optional[str] = Field(default=None, description="`low`, `moderate`, `high` or `very high`")


class ForestFireDepartment(BaseModel):
    code: str = Field(description="INSEE department code (`13`, `2A`...)")
    name: str


class ForestFireResponse(BaseModel):
    """The forest fire danger of the department of a position, for the two days after the publication."""

    status: ForestFireStatus
    lat: float
    lon: float
    department: Optional[ForestFireDepartment] = None
    reference_time: Optional[str] = Field(default=None, description="When Météo-France published this map (UTC, ISO)")
    day1: Optional[ForestFireLevel] = Field(default=None, description="The day after the publication (J+1)")
    day2: Optional[ForestFireLevel] = Field(default=None, description="Two days after the publication (J+2)")
    checked_at_utc: Optional[int] = Field(default=None, description="When the gateway last asked Météo-France (epoch seconds)")
    note: Optional[str] = None
    attribution: str
