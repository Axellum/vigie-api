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
    part: bool = Field(
        default=False,
        description=(
            "DERIVED: true when the destination is a part of the country only (the American \"Gaza\", the Canadian \"Azores\", the "
            "Dutch \"Bonaire\"): its advice is not the one of the whole country, and the comparison leaves it out"
        ),
    )
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


# --- The answers of GET /v1/travel and GET /v1/travel/{cc} --------------------------------------------------------------

TravelFeedState = Literal["ok", "stale", "loading", "kept", "unavailable"]


class TravelScaleStep(BaseModel):
    """One step of a ministry's own scale, in its words."""

    level: int = Field(..., description="The step (1 lowest)")
    words: dict[str, str] = Field(..., description="The ministry's words for it, by language")


class TravelMinistry(BaseModel):
    """A ministry that publishes travel advice: what Vigie reads of it, or why it reads nothing."""

    id: str = Field(..., description="The ISO code of the issuing country, lower case (gb, us, ca, au, nl, de, fr)")
    name: str = Field(..., description="The ministry")
    country: str = Field(..., description="ISO 3166-1 alpha-2 country that issues the advice")
    url: str = Field(..., description="The ministry's own pages of travel advice")
    read: bool = Field(..., description="True when this instance reads the ministry's feed")
    why_not_read: Optional[str] = Field(default=None, description="When it is not read: why, in one sentence")
    levels: list[TravelScaleStep] = Field(
        default_factory=list, description="The ministry's scale, in its own words (empty when it has no numbered scale)"
    )
    statuses: list[str] = Field(default_factory=list, description="The FCDO only: the alert statuses its pages use")
    licence: Optional[str] = Field(default=None, description="The licence of the feed, as read (SOURCES.md)")
    commercial_use: Optional[Literal["allowed", "forbidden", "not_verified"]] = Field(
        default=None, description="Whether its terms allow commercial use (SOURCES.md, \"Commercial use\")"
    )
    state: Optional[TravelFeedState] = Field(
        default=None,
        description=(
            "For a ministry that is read: `ok`; `stale` (the last answer: the ministry did not answer again); `loading` (the first "
            "read is still running: ask again in a minute); `kept` (the FCDO did not answer: the pages read before are served, each "
            "with the date it was last confirmed); `unavailable`"
        ),
    )
    advices: Optional[int] = Field(default=None, description="How many advices of this ministry are in the answer")
    unplaced: list[str] = Field(default_factory=list, description="Destinations of its feed no table places in a country (their titles)")
    pending: list[str] = Field(default_factory=list, description="The FCDO: pages with a newer version that could not be read yet")
    attribution: Optional[str] = Field(default=None, description="The credit of the ministry")


class TravelComparison(BaseModel):
    """How the ministries' advices for a country compare on the common scale. Everything here is Vigie's, not a ministry's."""

    compared: list[str] = Field(
        default_factory=list, description="The ministries compared: those with an advice for the whole country and a common step"
    )
    disagree: Optional[bool] = Field(
        default=None,
        description="DERIVED: true when two of them cannot be at the same step of the common scale; null when fewer than two are compared",
    )
    gap: Optional[int] = Field(default=None, description="DERIVED: how many steps apart the two furthest are (0 when they can agree)")
    strictest: list[str] = Field(default_factory=list, description="DERIVED: when they disagree, the ministries whose advice is the strictest")
    laxest: list[str] = Field(default_factory=list, description="DERIVED: when they disagree, the ministries whose advice is the least strict")
    derived: list[str] = Field(default_factory=lambda: ["compared", "disagree", "gap", "strictest", "laxest"])


class TravelLevel(BaseModel):
    """The advice of one ministry for a country, without its texts (``GET /v1/travel``)."""

    ministry: Ministry
    title: str = Field(..., description="The destination as the ministry names it")
    part: bool = Field(default=False, description="DERIVED: the destination is a part of the country only")
    level: Optional[int] = Field(default=None, description="Step on the ministry's own scale (`ministries[].levels`)")
    statuses: list[str] = Field(default_factory=list, description="The FCDO's alert statuses, as given")
    regional: Optional[bool] = Field(default=None, description="The ministry gives a stricter advice for parts of the destination")
    common_min: Optional[int] = Field(default=None, description="DERIVED, NOT OFFICIAL: the common scale, lowest step")
    common_max: Optional[int] = Field(default=None, description="DERIVED, NOT OFFICIAL: the common scale, highest step")
    published_utc: Optional[int] = Field(default=None, description="When the ministry published this version")
    published_text: Optional[str] = Field(default=None, description="That date as written, when it has no time zone")
    checked_utc: Optional[int] = Field(default=None, description="When Vigie last read it")


class TravelCountry(BaseModel):
    """Every ministry's level for one country."""

    country: str = Field(..., description="ISO 3166-1 alpha-2")
    name: Optional[str] = Field(
        default=None, description="The country's English name (Natural Earth); null when the bundled borders do not have it"
    )
    advices: list[TravelLevel]
    comparison: TravelComparison


class TravelOverview(BaseModel):
    """``GET /v1/travel``: the level of each ministry for each country."""

    generated_utc: int
    ministries: list[TravelMinistry]
    countries: list[TravelCountry]
    common_scale: dict[int, str] = Field(
        default_factory=lambda: dict(COMMON_SCALE), description="OURS, NOT OFFICIAL: the steps of the common scale (docs/data-sources.md)"
    )
    notice: str
    attribution: Optional[str] = Field(default=None, description="The credits of the ministries that answered")


class TravelChange(BaseModel):
    """One version of an advice as Vigie read it: its archive starts the day this instance first read the ministry."""

    ministry: Ministry
    title: str
    observed_utc: int = Field(..., description="When Vigie read this version (UTC epoch seconds)")
    first: bool = Field(..., description="True for the first version Vigie read of this advice: not a change, the start of the archive")
    level_changed: Optional[bool] = Field(
        default=None, description="DERIVED: the level (or the FCDO's statuses) differs from the version before; null for the first"
    )
    level: Optional[int] = None
    level_texts: dict[str, str] = Field(default_factory=dict)
    statuses: list[str] = Field(default_factory=list)
    common_min: Optional[int] = None
    common_max: Optional[int] = None
    published_utc: Optional[int] = None
    published_text: Optional[str] = None


class TravelCountryDetail(BaseModel):
    """``GET /v1/travel/{cc}``: every ministry's advice for one country, its texts, how they compare and their history."""

    generated_utc: int
    country: str
    name: Optional[str] = None
    advices: list[TravelAdvice] = Field(default_factory=list, description="The advice of each ministry, with its text, as given")
    comparison: TravelComparison
    history: list[TravelChange] = Field(
        default_factory=list,
        description="The versions Vigie has read, newest first (archived by this instance: it starts the day it first read each ministry)",
    )
    history_since_utc: Optional[int] = Field(default=None, description="When this instance started to archive the travel advice")
    not_covered: list[str] = Field(default_factory=list, description="The ministries read that publish no advice for this country")
    ministries: list[TravelMinistry]
    notice: str
    attribution: Optional[str] = None
