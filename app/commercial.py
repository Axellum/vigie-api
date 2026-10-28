"""Whether each source's terms allow commercial use: one table, for the paid Android app to come.

The website and this API stay free and non-commercial. An Android app with a paid membership will come from them
(decision of 2026-10-28), and an app with a subscription or ads is commercial use. Each source says whether that is
allowed, here, **once**, keyed like the credits (``app/attribution.py``): a test fails when a credit has no line, so a
new source cannot be added without saying it.

A status is one of:

* ``allowed``: the terms or the licence read allow commercial use (public domain, CC BY 4.0, CC BY-SA 4.0, Licence
  Ouverte 2.0, NLOD 2.0), within their conditions (credit, no alteration, no suggested endorsement...);
* ``forbidden``: the terms read restrict the data, or the way it is reached, to non-commercial use;
* ``not_verified``: the terms say nothing of it, could not be read, or contradict each other.

A block of data carries several credits (Open-Meteo **and** the model behind it): its status is the most restrictive
of them (``commercial_status``). The marine block is ``forbidden`` while it comes from Open-Meteo's free API, although
the DWD's data behind it is open: it is the way the data is reached that is not.

What a line says was read on the date given, partly through an automated reader that summarizes a page: re-read the
wording in a browser before anything commercial. ``SOURCES.md`` quotes this table (``tests/test_commercial_use.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CommercialStatus = Literal["allowed", "forbidden", "not_verified"]

# From the least to the most restrictive: a block takes the last one any of its credits has.
_ORDER: tuple[CommercialStatus, ...] = ("allowed", "not_verified", "forbidden")


@dataclass(frozen=True)
class CommercialUse:
    """What the terms of one source say of commercial use."""

    key: str  # a key of ``app.attribution.CREDITS``
    status: CommercialStatus
    reason: str  # in a few words, as read
    evidence: tuple[str, ...]  # the pages that were read
    read_on: str = "2026-10-20"


_DWD_LEGAL_NOTICE = "https://www.dwd.de/EN/service/legal_notice/legal_notice_node.html"
_NWS_DISCLAIMER = "https://www.weather.gov/disclaimer"

COMMERCIAL_USE: dict[str, CommercialUse] = {
    use.key: use
    for use in (
        CommercialUse(
            "open_meteo",
            "forbidden",
            "The free API is for non-commercial use only, and an app with a subscription or ads is commercial. The paid "
            "API plans allow it (no price shown on the pricing page), and so does self-hosting the open-source server.",
            (
                "https://open-meteo.com/en/terms",
                "https://open-meteo.com/en/pricing",
                "https://github.com/open-meteo/open-meteo/blob/main/docs/getting-started.md",
            ),
            "2026-10-28",
        ),
        CommercialUse(
            "ecmwf_opendata",
            "allowed",
            "ECMWF open data, CC BY 4.0.",
            ("https://www.ecmwf.int/en/forecasts/datasets/open-data",),
            "2026-10-28",
        ),
        CommercialUse(
            "dwd_icon_wave",
            "allowed",
            "The DWD's open data is under CC BY 4.0. Here it comes through Open-Meteo's free API, which is not: the "
            "marine block stays forbidden while it does.",
            (_DWD_LEGAL_NOTICE,),
        ),
        CommercialUse(
            "cams",
            "allowed",
            "Copernicus licence: any lawful use, with the credit. Here it comes through Open-Meteo's free API, which is "
            "not.",
            ("https://apps.ecmwf.int/datasets/licences/cams",),
            "2026-10-28",
        ),
        CommercialUse(
            "glofas",
            "not_verified",
            "The terms of the GloFAS data themselves were not read.",
            ("https://open-meteo.com/en/docs/flood-api",),
        ),
        CommercialUse(
            "met_norway",
            "allowed",
            "NLOD 2.0 and CC BY 4.0. The terms of service (identification, cache, rate) still apply.",
            ("https://api.met.no/doc/License", "https://api.met.no/doc/TermsOfService"),
        ),
        CommercialUse(
            "rainviewer",
            "forbidden",
            "The API is for personal and educational use only.",
            ("https://www.rainviewer.com/api.html",),
        ),
        CommercialUse(
            "usgs",
            "allowed",
            "USGS-authored data is in the U.S. public domain.",
            ("https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",),
        ),
        CommercialUse(
            "usgs_volcano",
            "not_verified",
            "The API page states no licence; the general USGS policy presumably applies.",
            ("https://volcanoes.usgs.gov/vsc/api/",),
        ),
        CommercialUse(
            "emsc",
            "allowed",
            "CC BY 4.0. Mirroring or bulk copying of the database needs written permission.",
            ("https://www.seismicportal.eu/terms.html",),
        ),
        CommercialUse(
            "gdacs",
            "not_verified",
            "No reuse licence found on the terms page.",
            ("https://data.gdacs.org/About/termofuse.aspx",),
        ),
        CommercialUse(
            "noaa_nws",
            "allowed",
            "Public domain, unless noted otherwise. No altered content presented as official, no suggested endorsement.",
            (_NWS_DISCLAIMER,),
        ),
        CommercialUse(
            "noaa_nhc",
            "allowed",
            "Public domain, unless marked otherwise. No altered content presented as official.",
            ("https://www.nhc.noaa.gov/disclaimer.shtml",),
        ),
        CommercialUse(
            "noaa_tsunami",
            "allowed",
            "Public domain, by the NWS disclaimer that tsunami.gov links to.",
            (_NWS_DISCLAIMER,),
        ),
        CommercialUse(
            "noaa_swpc",
            "not_verified",
            "Public domain deduced from the NWS policy: the SWPC's data access page states no licence.",
            ("https://www.spaceweather.gov/content/data-access", _NWS_DISCLAIMER),
        ),
        CommercialUse(
            "nasa_firms",
            "not_verified",
            "No licence is named. NASA's policy is the full and open sharing of its data; commercial use is not stated.",
            ("https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-information-guidance",),
        ),
        CommercialUse(
            "meteoalarm",
            "not_verified",
            "Terms equivalent to CC BY 4.0, with additional requirements for redistributors that were not read.",
            ("https://meteoalarm.org/en/live/page/terms-and-conditions",),
        ),
        CommercialUse(
            "dwd_warnings",
            "allowed",
            "CC BY 4.0, the source acknowledged. An altered warning must lose the DWD's name.",
            (_DWD_LEGAL_NOTICE,),
        ),
        CommercialUse(
            "eccc_alerts",
            "not_verified",
            "ECCC Data Services End-use Licence 2.1.1: what it says of commercial use was not read.",
            ("https://eccc-msc.github.io/open-data/licence/readme_en/",),
        ),
        CommercialUse(
            "metservice_alerts",
            "not_verified",
            "The feed says CC BY 4.0; the website terms it links to allow personal use only.",
            ("https://alerts.metservice.com/cap/rss", "https://about.metservice.com/web-terms-of-use"),
        ),
        CommercialUse(
            "wmo_swic",
            "not_verified",
            "Each national service has its own terms (app/collectors/swic_terms.py); they were not reviewed for "
            "commercial use one by one.",
            ("https://severeweather.wmo.int/note.html",),
        ),
        CommercialUse(
            "gem_faults",
            "allowed",
            "CC BY-SA 4.0: commercial use allowed, and data derived from it stays under the same licence.",
            ("https://github.com/GEMScienceTools/gem-global-active-faults",),
        ),
        CommercialUse(
            "vigicrues",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): the source and the date of the data named.",
            ("https://www.etalab.gouv.fr/licence-ouverte-open-licence/",),
        ),
        CommercialUse(
            "eot20",
            "allowed",
            "CC BY 4.0 (the SEANOE page): the credit, a link to the licence and a statement of the changes. Computed by "
            "Vigie, with no request to anyone.",
            ("https://www.seanoe.org/data/00683/79489/", "https://creativecommons.org/licenses/by/4.0/"),
            "2026-10-28",
        ),
    )
}


def commercial_status(*keys: str) -> CommercialStatus:
    """The status of a block of data that carries these credits: the most restrictive of them.

    A block with no credit, or a credit with no line, is ``not_verified``: nothing says it is allowed.
    """
    if not keys:
        return "not_verified"
    statuses: list[CommercialStatus] = [
        COMMERCIAL_USE[key].status if key in COMMERCIAL_USE else "not_verified" for key in keys
    ]
    return max(statuses, key=_ORDER.index)
