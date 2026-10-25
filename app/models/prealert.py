"""Models of the experimental pre-alert for Turkey (``GET /v1/experimental/prealert``, ADR 0011 and its amendment).

**Not an official warning.** Levels computed by Vigie from the ECMWF forecast with thresholds of its own, for testers who asked for
it. The official warnings of Turkey are the MGM's (``official_warnings_url``). The answer says so in ``notice`` and in every field
that could be taken for a warning.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

LevelWord = Literal["none", "watch", "strong", "severe"]


class PrealertPhenomenon(BaseModel):
    """One phenomenon of one day in the province."""

    value: Optional[float] = Field(default=None, description="The province's value (90 % quantile of its grid points); null when the run lacks it")
    unit: str = Field(..., description="Unit of the value and of the thresholds")
    level: Optional[int] = Field(default=None, description="0 none, 1 watch, 2 strong, 3 severe (computed by Vigie); null when unknown")
    level_word: Optional[LevelWord] = Field(default=None, description="The level as a word (ours, not the MGM's colours)")
    thresholds: Optional[list[float]] = Field(default=None, description="The province's thresholds of levels 1, 2 and 3 (ours, frozen on 2026-10-25)")


class PrealertDay(BaseModel):
    """One local day (Europe/Istanbul)."""

    day: str = Field(..., description="Local day, YYYY-MM-DD")
    max_level: Optional[int] = Field(default=None, description="The highest level of the day's phenomena; null when none is known")
    phenomena: dict[str, PrealertPhenomenon] = Field(..., description="rain, wind, snow, heat, cold")


class PrealertResponse(BaseModel):
    """The experimental pre-alert of the province a position is in."""

    experimental: bool = Field(default=True, description="Always true: a test, being improved")
    official: bool = Field(default=False, description="Always false: this is not an official warning")
    notice: str = Field(..., description="What this is, in one paragraph (English)")
    province_code: str = Field(..., description="ISO 3166-2 code of the province (TR-01 to TR-81)")
    province_name: str = Field(..., description="The province's Turkish name")
    run_utc: Optional[str] = Field(default=None, description="The ECMWF run the levels come from; null before the first run is stored")
    days: list[PrealertDay] = Field(default_factory=list, description="Today and the next days the run covers, local days")
    quality: str = Field(..., description="What the measure of 2026-10-25 found (ADR 0011): how often it saw the official warnings")
    official_warnings_url: str = Field(..., description="Where the official warnings of Turkey are (the MGM)")
    attribution: str = Field(..., description="The credit of the forecast the levels are computed from")
    derived: list[str] = Field(default_factory=lambda: ["value", "level", "level_word", "thresholds", "max_level"], description="The fields computed by Vigie")
