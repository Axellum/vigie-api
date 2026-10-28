"""Models of the travel advice of the foreign ministries (United Kingdom, United States, Canada, Australia, the Netherlands).

The principle is the one of the official warnings (``app/models/vigilance.py``): **the ministry's words stay intact**, and
what the API works out stands beside them and says that it is its own.

- As given: the destination's name, the level in the ministry's own words (``level_texts``), the FCDO's alert statuses, the
  text on the risks (``summary``), the note of the latest change, the date of the version, the link to the official page.
  Nothing is translated, shortened or rewritten; a ministry that publishes in two languages (Canada) keeps both.
- Ours, listed in ``derived``: the country code (each ministry names its destinations its own way, see
  ``app/collectors/travel_codes.py``), the Dutch colour read from the sentence that states it, and the **common scale**
  (``common_min``, ``common_max``), which lets the ministries be compared and is **never an official level**. Its tables
  are in ``docs/data-sources.md``. A value no ministry gives, or no table covers, is ``None``: never guessed.
- No ministry publishes a shape for the areas it advises against inside a country: those areas stay in the text, and the
  API draws none.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# The ministries, by the ISO 3166-1 alpha-2 code of the country that issues the advice (lower case).
Ministry = Literal["gb", "us", "ca", "au", "nl"]

# The common scale (ours, docs/data-sources.md): 1 normal precautions, 2 increased caution, 3 avoid non-essential travel,
# 4 avoid all travel.
COMMON_SCALE: dict[int, str] = {
    1: "normal precautions",
    2: "increased caution",
    3: "avoid non-essential travel",
    4: "avoid all travel",
}


class TravelText(BaseModel):
    """A text of the ministry, in one language, as given."""

    language: str = Field(..., description="Language of the text (en, fr, nl)")
    format: Literal["html", "text"] = Field(
        ..., description="`html`: the ministry's HTML, as given (sanitize it before you show it); `text`: plain text"
    )
    title: Optional[str] = Field(default=None, description="Title of the section the text comes from, as given (\"Warnings and insurance\")")
    content: str = Field(..., description="The text, untouched")


class TravelSourceChange(BaseModel):
    """One entry of the history of changes a ministry publishes itself (the FCDO's ``change_history``), as given."""

    published_utc: Optional[int] = Field(default=None, description="When the ministry published this change (UTC epoch seconds)")
    note: str = Field(..., description="The ministry's note, as given")


class TravelAdvice(BaseModel):
    """The advice of one ministry for one destination."""

    ministry: Ministry = Field(..., description="Who issues the advice: the ISO code of its country (gb, us, ca, au, nl)")
    country: str = Field(
        ...,
        description=(
            "DERIVED: ISO 3166-1 alpha-2 country the advice is about, converted from the ministry's own code or page name "
            "(`source_code`). An advice about a part of a country (\"Gaza\", \"Azores\") carries the code of that country"
        ),
    )
    source_code: str = Field(..., description="The ministry's own code or page name for the destination (`destination.irq`, `MX`, `MEX`, `turkiye`)")
    title: str = Field(..., description="The destination as the ministry names it, as given")
    level: Optional[int] = Field(
        default=None,
        description=(
            "Position on the ministry's own scale, 1 (lowest) to 4 (`TravelMinistry.levels`). Null when the ministry has no "
            "numbered scale (the FCDO: see `statuses`) or gives no single level for the whole destination (a Dutch advice with "
            "several colours)"
        ),
    )
    level_texts: dict[str, str] = Field(
        default_factory=dict, description="The level in the ministry's own words, by language (`{\"en\": \"Level 4: Do Not Travel\"}`)"
    )
    statuses: list[str] = Field(
        default_factory=list,
        description=(
            "The FCDO only: its alert statuses, as given (`avoid_all_travel_to_parts`, `avoid_all_but_essential_travel_to_parts`, "
            "`avoid_all_but_essential_travel_to_whole_country`, `avoid_all_travel_to_whole_country`); empty when the FCDO "
            "advises against no travel there"
        ),
    )
    regional: Optional[bool] = Field(
        default=None,
        description=(
            "True when the ministry gives a stricter advice for parts of the destination (Canada's `has-regional-advisory`, "
            "an FCDO status `..._to_parts`, a Dutch advice naming several colours). Null when its feed does not say (the "
            "areas are then only in the text)"
        ),
    )
    common_min: Optional[int] = Field(
        default=None,
        description=(
            "DERIVED, NOT OFFICIAL: the lowest step of the common scale (1 normal precautions, 2 increased caution, 3 avoid "
            "non-essential travel, 4 avoid all travel) the ministry's level for the whole destination can be. Null when unknown"
        ),
    )
    common_max: Optional[int] = Field(
        default=None,
        description="DERIVED, NOT OFFICIAL: the highest step it can be (the FCDO's \"no advice against travel\" is 1 to 2). Null when unknown",
    )
    published_utc: Optional[int] = Field(default=None, description="When the ministry published this version (UTC epoch seconds). Null when the source gives no time zone")
    published_text: Optional[str] = Field(default=None, description="That date as the ministry writes it, when it gives no time zone (Smartraveller)")
    checked_utc: Optional[int] = Field(default=None, description="When Vigie last read this advice from the ministry (UTC epoch seconds)")
    url: str = Field(..., description="The official page of the advice")
    summary: list[TravelText] = Field(
        default_factory=list, description="The ministry's text on the risks, untouched (only in the answer for one country)"
    )
    change_note: list[TravelText] = Field(default_factory=list, description="The ministry's own note of its latest change, as given")
    source_history: list[TravelSourceChange] = Field(
        default_factory=list, description="The history of changes the ministry publishes itself (the FCDO), as given (only in the answer for one country)"
    )
    derived: list[str] = Field(default_factory=list, description="The fields of this advice that the API works out (not the ministry's)")
    attribution: str = Field(..., description="The credit of the ministry")
