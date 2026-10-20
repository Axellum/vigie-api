"""The recorded feeds of 2026-10-20 (``tests/fixtures/meteoalarm/``) and a few synthetic warnings for the tests of the warnings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.collectors.meteoalarm import CapDocument, MeteoAlarmFeed, parse_cap, parse_feed
from app.models.schemas import SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilancePhenomenon,
    VigilanceStatus,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "meteoalarm"

# The recorded feeds, by country; the recording date is in ``tests/fixtures/meteoalarm/README.md``.
FEED_FILES = {
    "FR": "atom_france.xml",
    "IS": "atom_iceland.xml",
    "CH": "atom_switzerland.xml",
    "DE": "atom_germany.xml",
    "BE": "atom_belgium.xml",
    "IT": "atom_italy.xml",
    "NO": "atom_norway.xml",
    "DK": "atom_empty.xml",
}

# Where a point is (WGS84): inside the area of a recorded warning, or not.
VALENCE = (44.9334, 4.8924)  # Drôme (FR713): yellow flooding
MONTPELLIER = (43.6108, 3.8767)  # Hérault (FR813): yellow flooding (an orange one, over at 22:00 UTC the day before, is still listed)
BASTIA = (42.6977, 9.4509)  # Haute-Corse (FR832): red, yellow and thunderstorm warnings at once
CAGLIARI = (39.2238, 9.1217)  # Sardinia: Italian warnings name EMMA_ID codes only
PARIS = (48.8566, 2.3522)  # no warning in the recorded feed
RECORDED_AT = 1_792_490_744  # the "updated" time of the recorded French feed (2026-10-20, morning UTC)
GLARUS = (46.9487, 9.1508)  # Glarus Süd-Sernftal (CH): yellow rain, polygon of the source
REYKJAVIK = (64.1466, -21.9426)  # Iceland: the warnings name EMMA_ID codes (no shape bundled)
SAN_FRANCISCO = (37.7749, -122.4194)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def feed(country: str) -> MeteoAlarmFeed:
    """The recorded Atom feed of a country, parsed."""
    return parse_feed(fixture(FEED_FILES[country]), country)


def cap(name: str = "cap_france_b77d3bc0.xml") -> CapDocument:
    return parse_cap(fixture(name))


def nws_payload() -> dict[str, Any]:
    return json.loads(fixture("nws_active_severe_extreme.json"))


def warning(
    area_code: str = "FR713",
    level: Optional[SeverityLevel] = SeverityLevel.YELLOW,
    phenomenon: Optional[VigilancePhenomenon] = VigilancePhenomenon.FLOOD,
    *,
    identifier: str = "test.1",
    link: Optional[str] = None,
    country: str = "FR",
    source: str = "meteoalarm",
    scheme: str = "NUTS3",
    sent: int = 1_792_000_000,
    expires: Optional[int] = 1_792_100_000,
    status: Optional[VigilanceStatus] = VigilanceStatus.NEW,
    onset: Optional[int] = 1_792_000_000,
) -> Vigilance:
    """A synthetic warning for one code, for the tests of the diff and of the localization."""
    return Vigilance(
        id=f"{source}:{identifier}:{area_code}",
        source=source,  # type: ignore[arg-type]
        country=country,
        attribution="test credit",
        identifier=identifier,
        sent_utc=sent,
        onset_utc=onset,
        expires_utc=expires,
        status=status,
        level=level,
        level_original=level.value.title() if level else None,
        phenomenon=phenomenon,
        phenomenon_original=phenomenon.value if phenomenon else None,
        area=VigilanceArea(description=area_code, geocodes=[VigilanceGeocode(scheme=scheme, value=area_code)]),
        link=link,
    )
