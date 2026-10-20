"""ECCC collector: the weather alerts of Environment and Climate Change Canada as ``Vigilance`` objects.

The alerts are published by the Meteorological Service of Canada (MSC) in two ways: as CAP 1.2 files on the MSC
Datamart (``https://dd.weather.gc.ca/today/alerts/cap/{YYYYMMDD}/{EEEE}/{hh}/``, one file per transmission, grouped by
day, office and hour), and through MSC GeoMet, the OGC API - Features collection ``weather-alerts``
(https://api.weather.gc.ca/collections/weather-alerts/items), which lists the alerts in force, each with its polygon.

**This collector reads GeoMet, not the Datamart**, because of the MSC Open Data Service Usage Policy
(https://eccc-msc.github.io/open-data/usage-policy/readme_en/, read on 2026-10-20): for the Datamart HTTPS service it
forbids requesting directory listings to see whether new data has arrived ("the AMQPS notification service must be
used") and reserves ``curl`` / ``wget`` for ad hoc retrieval ("for systematic data retrieval, the AMQPS service must be
used"). Finding the new CAP files of the Datamart without listing its directories is not possible, and an AMQPS client
is a new dependency and a connection that stays open: the owner decided on 2026-10-20 to stay on GeoMet (see ``docs/decisions/0003``).
GeoMet has no such rule: one request lists every alert in force in Canada, and the usage limit is 86,400 requests a day.

What GeoMet gives is a flattened form of the same alerts: the words of the issuer in English and French
(``alert_name_*``, ``alert_text_*``), the colour, the times and the polygon of the area. It has no CAP ``instruction``,
``senderName`` or ``msgType``: those stay unknown here (``None``). The licence wants an alert reproduced "in such a way
that there is no alteration of the content or intent of the alert": the texts are returned as given.

Checked on one alert of 2026-10-20 against its CAP file of the Datamart (a frost advisory for Chatham-Kent - Rondeau
Park, the same ``Parent_URI`` identifier): ``validity_datetime`` is the CAP ``onset``, ``expiration_datetime`` the CAP
``expires``, ``event_end_datetime`` the CAP ``Event_End_Time``, and ``status_en`` ``continued`` goes with a CAP
``msgType`` Update. The server sends no ``ETag``, ``Last-Modified`` or cache lifetime: no conditional request.

**An ended alert stays in the list for a while** (seen live at 13:11 UTC on 2026-10-20: that same frost advisory, re-
published at 13:06 with ``status_en`` ``ended`` and an ``expiration_datetime`` one hour later, while the official page of
the place already said "no alert in force"). It is not an alert in force: it is skipped, never served
(``tests/fixtures/eccc/ended_item.json`` is that item).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.attribution import credit_text
from app.collectors.base import BaseCollector, Collected, Fetched, parse_iso_utc
from app.geo import geojson_to_shape
from app.models.schemas import SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilanceParameter,
    VigilancePhenomenon,
    VigilanceStatus,
    VigilanceText,
)

ITEMS_URL = "https://api.weather.gc.ca/collections/weather-alerts/items"
PAGE_SIZE = 1000
MAX_PAGES = 10  # ten thousand alerts at once is a mistake, not a storm

# ``risk_colour_en`` -> our level. The colour is the one ECCC puts on the alert (statements have none of these).
LEVELS: dict[str, SeverityLevel] = {
    "yellow": SeverityLevel.YELLOW,
    "orange": SeverityLevel.ORANGE,
    "red": SeverityLevel.RED,
}

# ``status_en`` -> our status. Only the words seen on 2026-10-20 are in the table (not verified: the schema of the
# collection documents no value); any other word gives ``None``. ``ended`` is the third word seen: the alert is over, so
# it is skipped (see ``is_ended``) and has no status here.
ENDED = "ended"
STATUSES: dict[str, VigilanceStatus] = {
    "issued": VigilanceStatus.NEW,
    "continued": VigilanceStatus.UPDATE,
}

# English alert name -> our phenomenon: the first rule whose words are all in the lower-case name wins, so the order
# matters ("freezing rain" is snow-ice, not rain; a "flash flood" is a flood). A name no rule covers is "other".
PHENOMENA: tuple[tuple[tuple[str, ...], VigilancePhenomenon], ...] = (
    (("tornado",), VigilancePhenomenon.WIND),
    (("hurricane",), VigilancePhenomenon.WIND),
    (("tropical storm",), VigilancePhenomenon.WIND),
    (("storm surge",), VigilancePhenomenon.COASTAL),
    (("tsunami",), VigilancePhenomenon.COASTAL),
    (("flood",), VigilancePhenomenon.FLOOD),
    (("thunderstorm",), VigilancePhenomenon.THUNDERSTORM),
    (("blizzard",), VigilancePhenomenon.SNOW_ICE),
    (("blowing snow",), VigilancePhenomenon.SNOW_ICE),
    (("snow squall",), VigilancePhenomenon.SNOW_ICE),
    (("snowfall",), VigilancePhenomenon.SNOW_ICE),
    (("winter storm",), VigilancePhenomenon.SNOW_ICE),
    (("freezing rain",), VigilancePhenomenon.SNOW_ICE),
    (("freezing drizzle",), VigilancePhenomenon.SNOW_ICE),
    (("flash freeze",), VigilancePhenomenon.SNOW_ICE),
    (("snow",), VigilancePhenomenon.SNOW_ICE),
    (("arctic outflow",), VigilancePhenomenon.COLD),
    (("wind chill",), VigilancePhenomenon.COLD),
    (("extreme cold",), VigilancePhenomenon.COLD),
    (("frost",), VigilancePhenomenon.COLD),
    (("heat",), VigilancePhenomenon.HEAT),
    (("fog",), VigilancePhenomenon.FOG),
    (("rainfall",), VigilancePhenomenon.RAIN),
    (("rain",), VigilancePhenomenon.RAIN),
    (("wind",), VigilancePhenomenon.WIND),
)


def is_ended(props: dict[str, Any]) -> bool:
    """Whether an item is an alert that is over: ``status_en`` is ``ended`` (seen live), or ``alert_type`` says it (not seen)."""
    return ENDED in {str(props.get("status_en") or "").strip().lower(), str(props.get("alert_type") or "").strip().lower()}


@dataclass(frozen=True)
class ECCCAlerts:
    """The alerts in force in Canada as GeoMet lists them (one per forecast area), and how many items were skipped."""

    vigilances: tuple[Vigilance, ...]
    skipped: int = 0  # items that are not an alert in force (an ended one) or could not be read


def derive_level(colour: Optional[str]) -> Optional[SeverityLevel]:
    """Our level for the colour of an alert (``None`` for a statement, which has none of the three)."""
    return LEVELS.get(colour.strip().lower()) if colour else None


def derive_phenomenon(name: Optional[str]) -> Optional[VigilancePhenomenon]:
    """Our phenomenon for an English alert name; ``None`` when there is no name, "other" when no rule covers it."""
    if not name or not name.strip():
        return None
    lowered = name.lower()
    for words, phenomenon in PHENOMENA:
        if all(word in lowered for word in words):
            return phenomenon
    return VigilancePhenomenon.OTHER


def _clean(value: Any) -> Optional[str]:
    """A provider text as it is, ``None`` when it is missing or blank."""
    if not isinstance(value, str):
        return None
    return value if value.strip() else None


def _text(props: dict[str, Any], language: str) -> VigilanceText:
    """The words of the issuer in one language (``en`` or ``fr``), untouched; the other properties ride along as parameters."""
    parameters = [
        VigilanceParameter(name=name, value=str(props[key]))
        for name, key in (
            ("alert_short_name", f"alert_short_name_{language}"),
            ("colour", f"risk_colour_{language}"),
            ("confidence", f"confidence_{language}"),
            ("impact", f"impact_{language}"),
            ("status", f"status_{language}"),
            ("area", f"feature_name_{language}"),
        )
        if props.get(key) not in (None, "")
    ]
    return VigilanceText(
        language=language,
        event=_clean(props.get(f"alert_name_{language}")),
        description=_clean(props.get(f"alert_text_{language}")),
        parameters=parameters,
    )


def parse_feature(feature: dict[str, Any]) -> Optional[Vigilance]:
    """One GeoMet item as a warning; ``None`` when it has no identifier or is an alert that ended."""
    props = feature.get("properties") or {}
    item_id = feature.get("id") or props.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None
    if is_ended(props):
        return None  # over: GeoMet keeps listing it for a while with status_en "ended" (seen live on 2026-10-20)
    name = _clean(props.get("alert_name_en"))
    colour = _clean(props.get("risk_colour_en"))
    status_word = _clean(props.get("status_en"))
    shape = geojson_to_shape(feature.get("geometry"))
    geocodes = [
        VigilanceGeocode(scheme=scheme, value=str(props[key]))
        for scheme, key in (("province", "province"), ("feature_id", "feature_id"))
        if props.get(key)
    ]
    vigilance = Vigilance(
        id=f"eccc:{item_id}",
        source="eccc",
        country="CA",
        attribution=credit_text("eccc_alerts"),
        identifier=item_id,
        sent_utc=parse_iso_utc(props.get("publication_datetime")),
        onset_utc=parse_iso_utc(props.get("validity_datetime")),
        expires_utc=parse_iso_utc(props.get("expiration_datetime")),
        ends_utc=parse_iso_utc(props.get("event_end_datetime")),
        status=STATUSES.get(status_word.lower()) if status_word else None,
        level=derive_level(colour),
        level_original=colour,
        phenomenon=derive_phenomenon(name),
        phenomenon_original=name,
        event=name,
        texts=[_text(props, "en"), _text(props, "fr")],
        area=VigilanceArea(
            description=_clean(props.get("feature_name_en")),
            geocodes=geocodes,
            has_polygon=shape is not None,
        ),
        link=f"{ITEMS_URL}/{item_id}",
    )
    return vigilance.with_shape(shape)


def parse_items(payload: Any) -> ECCCAlerts:
    """Parse one GeoMet page. An empty list is a valid answer; a list whose items all fail is an error."""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("ECCC answer has no 'features' list")
    vigilances: list[Vigilance] = []
    skipped = 0
    broken = 0
    for feature in features:
        try:
            parsed = parse_feature(feature) if isinstance(feature, dict) else None
        except (ValueError, TypeError, KeyError, AttributeError):
            broken += 1
            continue
        if parsed is None:
            skipped += 1
        else:
            vigilances.append(parsed)
    if features and not vigilances and broken:
        raise ValueError(f"ECCC answer: {broken} items, none could be read")
    return ECCCAlerts(vigilances=tuple(vigilances), skipped=skipped + broken)


class ECCCCollector(BaseCollector[ECCCAlerts]):
    """The alerts in force in Canada, one request (a few more if a stormy day needs pages), cached 300 seconds."""

    # 300 s: GeoMet sends no cache lifetime and the MSC documents no update rhythm for the list (not verified); the CAP
    # files are posted as the alerts are issued. Five minutes keeps the load at one request per refresh.
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="eccc_alerts",
            name="ECCC weather alerts (MSC GeoMet)",
            domain="warnings",
            ttl_seconds=300,
            http_client=http_client,
        )

    async def fetch_alerts(self, allow_stale_on_error: bool = True) -> Fetched[ECCCAlerts]:
        """The alerts of Canada, from the cache while it is fresh, else the request(s)."""
        return await self.fetch_ex(cache_key="alerts", allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[ECCCAlerts]:
        vigilances: list[Vigilance] = []
        skipped = 0
        for page in range(MAX_PAGES):
            resp = await self._get(
                client,
                ITEMS_URL,
                params={"f": "json", "limit": str(PAGE_SIZE), "offset": str(page * PAGE_SIZE)},
                headers={"Accept": "application/geo+json, application/json;q=0.9"},
            )
            resp.raise_for_status()
            payload = resp.json()
            alerts = parse_items(payload)
            vigilances.extend(alerts.vigilances)
            skipped += alerts.skipped
            returned = len(payload["features"])
            matched = payload.get("numberMatched")
            if returned < PAGE_SIZE or not isinstance(matched, int) or (page + 1) * PAGE_SIZE >= matched:
                break
        return Collected(ECCCAlerts(vigilances=tuple(vigilances), skipped=skipped))
