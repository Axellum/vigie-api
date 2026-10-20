"""Models of the official national weather warnings ("vigilances"): MeteoAlarm and the US National Weather Service.

The principle (``docs/architecture.md``): **the official content stays intact**, our normalized fields stand
beside it and say that they are ours.

- What the source says is kept as given: the original identifier, the times, the original level text, the
  original event text, the headline, the description and the instruction with their language, the codes of the
  area, the link to the original document. Nothing is translated, shortened or rewritten.
- What the API derives is the normalized ``level`` (green, yellow, orange, red), the normalized
  ``phenomenon`` (wind, rain, ...) and the ``status`` (new, update, cancel). They are listed in ``derived`` and
  documented in ``docs/data-sources.md`` (the tables). A value the source does not give, or that no table
  covers, is ``None``: it is never guessed.
- Every vigilance carries its ``attribution``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr
from shapely.geometry.base import BaseGeometry

from app.models.schemas import SeverityLevel


class VigilancePhenomenon(str, Enum):
    """The phenomena the API groups the warnings of every source in (derived from the source's own wording)."""

    WIND = "wind"
    RAIN = "rain"
    THUNDERSTORM = "thunderstorm"
    SNOW_ICE = "snow-ice"
    HEAT = "heat"
    COLD = "cold"
    FLOOD = "flood"
    AVALANCHE = "avalanche"
    COASTAL = "coastal"
    FIRE = "fire"
    FOG = "fog"
    OTHER = "other"


class VigilanceStatus(str, Enum):
    """What the message says about itself, from its CAP ``msgType``: a first warning, a replacement or a cancellation."""

    NEW = "new"
    UPDATE = "update"
    CANCEL = "cancel"


class VigilanceGeocode(BaseModel):
    """A code of the area as the source gives it (``EMMA_ID`` and ``NUTS3`` for MeteoAlarm, ``UGC`` and ``SAME`` for the NWS)."""

    scheme: str = Field(..., description="Code system, as the source names it (EMMA_ID, NUTS3, UGC, SAME...)")
    value: str = Field(..., description="The code")


class VigilanceParameter(BaseModel):
    """A CAP ``parameter`` of the warning, as given (for MeteoAlarm ``awareness_level``, ``awareness_type``, ``impacts``...)."""

    name: str
    value: str


class VigilanceText(BaseModel):
    """The words of the issuer in one language: kept untouched. A warning can carry several languages."""

    language: Optional[str] = Field(default=None, description="Language tag of the issuer (fr-FR, en-GB...), as given")
    sender_name: Optional[str] = Field(default=None, description="Name of the issuing service in this language (CAP senderName), as given")
    event: Optional[str] = Field(default=None, description="CAP event, as given")
    headline: Optional[str] = Field(default=None, description="CAP headline, as given")
    description: Optional[str] = Field(default=None, description="CAP description, as given")
    instruction: Optional[str] = Field(default=None, description="CAP instruction, as given")
    web: Optional[str] = Field(default=None, description="Web page the issuer gives for the warning")
    parameters: list[VigilanceParameter] = Field(default_factory=list, description="CAP parameters of this language, as given")


class VigilanceArea(BaseModel):
    """Where the warning applies: a polygon when the source gives one, else codes (names and codes as given)."""

    description: Optional[str] = Field(default=None, description="Name of the area, as given (CAP areaDesc)")
    geocodes: list[VigilanceGeocode] = Field(default_factory=list, description="Codes of the area, as given")
    has_polygon: bool = Field(default=False, description="True when the source gives the polygon(s) of the area")
    localization: Literal["polygon", "geocode", "none"] = Field(
        default="none",
        description=(
            "How a point can be matched to this area on this instance: `polygon` (the source's polygon), "
            "`geocode` (the shape of one of its codes is known here, simplified: a point within about 2 km of "
            "its edge counts as inside) or `none` (no shape: the warning cannot be matched to a point)"
        ),
    )
    # The polygons themselves are not part of the JSON answer (they are large): `GET /v1/vigilances.geojson` has them.


class Vigilance(BaseModel):
    """One official warning for one area."""

    id: str = Field(..., description="Identifier of this warning for this area in this API (unique, stable while the source keeps it)")
    source: Literal["meteoalarm", "noaa_nws"] = Field(..., description="Where the warning comes from")
    country: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2 country of the warning")
    attribution: str = Field(..., description="The credit the source asks for (with the issuer where the source names it)")
    identifier: str = Field(..., description="Identifier of the original CAP message, as given")
    sent_utc: Optional[int] = Field(default=None, description="Time of issue (UTC epoch seconds). Unknown: null")
    effective_utc: Optional[int] = Field(default=None, description="CAP effective time (UTC epoch seconds)")
    onset_utc: Optional[int] = Field(default=None, description="Expected start of the event (UTC epoch seconds)")
    expires_utc: Optional[int] = Field(default=None, description="Expiry of the message (UTC epoch seconds)")
    ends_utc: Optional[int] = Field(default=None, description="Expected end of the event, when the source gives it (NWS)")
    message_type: Optional[str] = Field(default=None, description="CAP msgType, as given (Alert, Update, Cancel)")
    status: Optional[VigilanceStatus] = Field(default=None, description="DERIVED from `message_type`: new, update or cancel")
    level: Optional[SeverityLevel] = Field(
        default=None, description="DERIVED level: GREEN, YELLOW, ORANGE or RED (tables in docs/data-sources.md). Null when the source's level is not in the table"
    )
    level_original: Optional[str] = Field(default=None, description="The level as the source says it (MeteoAlarm colour, NWS severity)")
    phenomenon: Optional[VigilancePhenomenon] = Field(
        default=None, description="DERIVED phenomenon (tables in docs/data-sources.md). Null when the source does not name one"
    )
    phenomenon_original: Optional[str] = Field(default=None, description="The phenomenon as the source says it (MeteoAlarm type, NWS event)")
    event: Optional[str] = Field(default=None, description="CAP event of the source's own listing, as given")
    issuer: Optional[str] = Field(default=None, description="Name of the issuing service (CAP senderName), as given; null until the CAP document is read")
    texts: list[VigilanceText] = Field(
        default_factory=list,
        description="Headline, description and instruction of the issuer, in each language it gives, untouched. Empty when the CAP document could not be read",
    )
    area: VigilanceArea
    link: Optional[str] = Field(default=None, description="Link to the original (the CAP document)")
    web: Optional[str] = Field(default=None, description="Web page of the source for this warning")
    derived: list[str] = Field(default_factory=lambda: ["status", "level", "phenomenon"], description="The fields above that the API derived: everything else is the source's own")

    # The shape of the polygons, built once when the warning is parsed (private: not serialized, not in the schema).
    _shape: Optional[BaseGeometry] = PrivateAttr(default=None)

    @property
    def shape(self) -> Optional[BaseGeometry]:
        """The polygons of the area as one shapely geometry (``None`` without polygon)."""
        return self._shape

    def with_shape(self, shape: Optional[BaseGeometry]) -> "Vigilance":
        """Set the shape of the polygons and return the warning."""
        self._shape = shape
        return self


class VigilanceQuery(BaseModel):
    """What was asked."""

    lat: Optional[float] = None
    lon: Optional[float] = None
    country: Optional[str] = None


class VigilanceCoverage(BaseModel):
    """What the API knows of one source for one country, so that an empty answer is not read as "nothing to fear"."""

    source: str = Field(..., description="meteoalarm or noaa_nws")
    country: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2 country")
    status: Literal["ok", "stale", "unavailable"] = Field(
        ..., description="`stale`: the provider did not answer, the last answer is served; `unavailable`: no answer at all"
    )
    warnings_in_feed: Optional[int] = Field(default=None, description="Warnings the source lists for the country (per area), whatever the point")
    warnings_not_matchable: Optional[int] = Field(
        default=None, description="Of those, the ones whose area has no shape on this instance: they cannot be matched to a point"
    )
    feed_updated_utc: Optional[int] = Field(default=None, description="Time the source says its list was updated")
    fetched_age_s: Optional[int] = Field(default=None, description="Age of the answer the API holds (seconds)")
    note: Optional[str] = None


class VigilancesResponse(BaseModel):
    """The warnings for a point or a country."""

    version: str = "1.0"
    server_utc: int
    query: VigilanceQuery
    countries: list[str] = Field(default_factory=list, description="Countries whose warnings were read for this query")
    vigilances: list[Vigilance] = Field(default_factory=list)
    coverage: list[VigilanceCoverage] = Field(default_factory=list)
    attribution: Optional[str] = Field(default=None, description="The credits of the sources used")
    disclaimer: Optional[str] = Field(default=None, description="Notice the sources ask to publish with their warnings")
    note: Optional[str] = Field(default=None, description="Set when no source covers the point")


class VigilanceGeoJSONFeature(BaseModel):
    """A warning as a GeoJSON feature (RFC 7946). The geometry is null when the area has no shape on this instance."""

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Optional[dict[str, Any]] = None
    properties: dict[str, Any]


class VigilanceGeoJSONCollection(BaseModel):
    """FeatureCollection of warnings (RFC 7946)."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: dict[str, Any] = Field(default_factory=dict)
    features: list[VigilanceGeoJSONFeature] = Field(default_factory=list)


class VigilanceChange(BaseModel):
    """A change pushed on `/v1/stream` for a point a client follows."""

    event: Literal["vigilance_push"] = "vigilance_push"
    server_utc: int
    change: Literal["appeared", "upgraded", "ended"]
    point: dict[str, Any] = Field(description="The followed point: lat, lon, and the zone id and name for a watched zone")
    vigilance: Vigilance
    previous_level: Optional[SeverityLevel] = Field(default=None, description="For `upgraded`: the level before")
    attribution: Optional[str] = None
    disclaimer: Optional[str] = None
