"""The credit that goes with the data: one table, read by every response and by the dashboard.

Each provider asks (or at least suggests) a credit line for the data it publishes; some also ask that
their alerts are not taken for official warnings. The text of each credit is declared **here, once**
(``CREDITS``). The models carry it in an ``attribution`` field, the dashboard prints it, and
``SOURCES.md`` (licence, terms, quota and clauses of every source) quotes the same texts: a test fails
when the two drift apart.

Where a provider prescribes no wording, the text is ours and says so (``wording="ours"``); nothing is
presented as the provider's own words when it is not. See ``SOURCES.md`` for what was checked on
2026-10-20 and what is **not verified**.

The compact embedded frame (``docs/embedded-frame.md``) has no field for a credit and does not get one:
a display that shows its data credits the sources itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Optional

# What separates two credits in one ``attribution`` string.
SEPARATOR = " | "

# Alerts with this id prefix come from the simulator (``POST /v1/simulate``): no provider issued them.
SIMULATED_ID_PREFIX = "sim_"
SIMULATION_CREDIT = "Simulated test data: not issued by any provider"


@dataclass(frozen=True)
class Credit:
    """The credit line of one source.

    ``wording`` tells whose words the text is: ``"provider"`` (the provider prescribes this text),
    ``"suggested"`` (the provider names a credit line and this is it) or ``"ours"`` (the provider
    prescribes no wording: the text is ours, written to name the source and, where the terms ask, to
    say that the alert is automatic or that the provider does not endorse this service).
    """

    key: str
    provider: str
    text: str
    wording: Literal["provider", "suggested", "ours"]


CREDITS: dict[str, Credit] = {
    credit.key: credit
    for credit in (
        Credit("open_meteo", "Open-Meteo", "Weather data by Open-Meteo.com (https://open-meteo.com/)", "provider"),
        Credit(
            "dwd_icon_wave",
            "DWD (through Open-Meteo Marine)",
            "Generated using ICON Wave forecast from the German Weather Service DWD",
            "provider",
        ),
        Credit(
            "cams",
            "CAMS (through Open-Meteo Air Quality)",
            "Air quality: CAMS ENSEMBLE data provider (Copernicus Atmosphere Monitoring Service)",
            "provider",
        ),
        Credit(
            "glofas",
            "GloFAS (through Open-Meteo Flood)",
            "River discharge: Global Flood Awareness System (GloFAS)",
            "ours",
        ),
        Credit("met_norway", "MET Norway", "Data from MET Norway (https://api.met.no/)", "suggested"),
        Credit(
            "rainviewer",
            "RainViewer",
            "Radar data: RainViewer (https://www.rainviewer.com/)",
            "ours",
        ),
        Credit("usgs", "USGS Earthquake Hazards Program", "Credit: U.S. Geological Survey", "suggested"),
        Credit(
            "usgs_volcano",
            "USGS Volcano Hazards Program",
            "Credit: U.S. Geological Survey, Volcano Hazards Program",
            "ours",
        ),
        Credit(
            "emsc",
            "EMSC / SeismicPortal",
            "Credit: EMSC-CSEM SeismicPortal, https://www.seismicportal.eu",
            "provider",
        ),
        Credit(
            "gdacs",
            "GDACS",
            "Global Disaster Alert and Coordination System, GDACS (https://www.gdacs.org). "
            "GDACS alerts are automatic and do not replace official warnings",
            "ours",
        ),
        Credit(
            "noaa_nws",
            "NOAA National Weather Service",
            "Source: NOAA National Weather Service (https://api.weather.gov). Not endorsed by NOAA or the NWS",
            "ours",
        ),
        Credit(
            "noaa_swpc",
            "NOAA Space Weather Prediction Center",
            "Source: NOAA Space Weather Prediction Center (https://www.swpc.noaa.gov). Not endorsed by NOAA",
            "ours",
        ),
        Credit(
            "meteoalarm",
            "MeteoAlarm (EUMETNET)",
            "Source: EUMETNET – MeteoAlarm (https://meteoalarm.org/en/live/), warnings of the national meteorological "
            "and hydrological services. Terms equivalent to CC BY 4.0 with additional requirements for redistributors",
            "ours",
        ),
    )
}

# The sentence the terms of MeteoAlarm oblige every redistributor to publish, word for word (terms of 2024-03-15,
# section 5, read on 2026-10-20: https://meteoalarm.org/en/live/page/terms-and-conditions).
METEOALARM_DISCLAIMER = (
    "Time delays between this website and the www.meteoalarm.org website are possible. For the most up-to-date "
    "awareness information as published by the participating National Meteorological and Hydrological Services, "
    "please refer to www.meteoalarm.org."
)

# The ``source`` / ``sources`` names a hazard alert carries -> the credit of that source.
HAZARD_SOURCE_CREDITS: dict[str, str] = {
    "USGS": "usgs",
    "EMSC": "emsc",
    "GDACS": "gdacs",
    "NOAA_NWS": "noaa_nws",
    "USGS_VOLCANO": "usgs_volcano",
}

# Blocks of data and the credits they carry (the order is the order of the text).
OPEN_METEO_WEATHER = ("open_meteo",)
MET_NORWAY_WEATHER = ("met_norway",)
AIR_QUALITY = ("open_meteo", "cams")
FLOOD = ("open_meteo", "glofas")
MARINE = ("open_meteo", "dwd_icon_wave")
SPACE_WEATHER = ("noaa_swpc",)
RADAR = ("rainviewer",)


def credit_text(*keys: str) -> str:
    """The text of the credits ``keys``, in order, without repeats. An unknown key is a bug and raises ``KeyError``."""
    return merge_credits(*(CREDITS[key].text for key in keys)) or ""


def merge_credits(*texts: Optional[str]) -> Optional[str]:
    """Join credit texts (each may already hold several), without repeats; ``None`` when there is none."""
    parts: list[str] = []
    for text in texts:
        for part in (text or "").split(SEPARATOR):
            if part and part not in parts:
                parts.append(part)
    return SEPARATOR.join(parts) if parts else None


def hazard_credit(hazard_id: str, sources: Iterable[str]) -> Optional[str]:
    """The credit of a hazard alert, from the sources that report it.

    A source this table does not know adds nothing (no credit is invented); an alert none of whose
    sources is known has no credit. A simulated alert is credited to no provider.
    """
    if hazard_id.startswith(SIMULATED_ID_PREFIX):
        return SIMULATION_CREDIT
    texts: list[Optional[str]] = []
    for source in sources:
        for name in str(source).split("+"):
            key = HAZARD_SOURCE_CREDITS.get(name.strip())
            if key is not None:
                texts.append(CREDITS[key].text)
    return merge_credits(*texts)
