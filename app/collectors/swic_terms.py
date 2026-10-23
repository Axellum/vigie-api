"""The terms of the services of the WMO registry, one record per service whose terms were looked at, and what is left out.

The WMO says of the warnings it lists that they are free to reuse citing the issuer, and that each authority keeps its
rights (https://severeweather.wmo.int/note.html, read 2026-10-20: see ``SOURCES.md``). So the terms of **each service** are what
count. A service is ``verified`` only when its **own** words allow what this API does (relay the warning, with its credit,
unchanged), read on 2026-10-20 at the address given, and in one of two forms:

* a terms page of the issuer that allows the reuse (AEMET's legal note), or
* an **open licence named** in the issuer's own feed (``CC BY 4.0`` in the feed's ``rights`` / ``copyright`` element, on a host
  the issuer runs or chose for its feed), and no restriction found in the issuer's own terms.

Everything else is ``unverified``: silence, a page that could not be read, terms that restrict or contradict each other, and the
bare line "public domain". That line is the one the WMO's own feed template puts on the feeds it hosts, and the one many issuers
copied from the Alert Hub's sample feed: 15 of the 25 services whose feed is outside the WMO's bucket and answered carry it, among
them four (Singapore, Thailand, Kuwait, Trinidad and Tobago) whose issuer's own site terms restrict copying. It says nothing sure of
what the issuer allows (docs/decisions/0006-wmo-registry-services.md).

An instance reads only the ``verified`` services unless it is told otherwise (``VIGIE_WMO_SOURCES=all``, for an instance only its
owner uses). The services in ``EXCLUDED`` are never read, whatever the setting.

``SOURCES.md`` has one row for each record below (``tests/test_sources_page.py`` checks it, and that each credit is quoted exactly);
``tools/check_live_feeds.py`` checks every week that the licence line of a ``verified`` feed is still in the feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from app.attribution import credit_text, merge_credits
from app.collectors.swic_registry import SwicSource

Status = Literal["verified", "unverified"]
EvidenceKind = Literal["terms page", "own feed", "none found"]
# What the terms read say, for an unverified service: nothing found or a bare line (``""``), a restriction on passing the warnings on
# (``"restricts"``), or a feed line and a site that contradict each other (``"contradictory"``). The answer says it for a country that
# is not read (``app/services/uncovered.py``).
Restriction = Literal["", "restricts", "contradictory"]


@dataclass(frozen=True)
class SwicTerms:
    """What was found of one service's terms."""

    source_id: str
    country: str
    issuer: str  # the service's name, as it calls itself
    status: Status
    licence: str  # the licence or the terms, in a few words, as stated
    evidence: str  # the address that was read: a terms page, or the service's feed
    evidence_kind: EvidenceKind
    credit: str  # the credit the API puts on the service's warnings (never holds the separator " | ")
    note: str = ""  # what is not verified, or why it is not
    feed_marker: str = ""  # a verified licence read in a feed: the words (lower case) the weekly check looks for in it
    restriction: Restriction = ""  # for an unverified service: what the terms read say (see ``Restriction``)


_CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"

_BARE = (
    "Its feed says \"public domain\" and nothing more, and no terms page was found. A bare line is not enough: 15 of the 25 "
    "services whose feed is outside the WMO's bucket and answered on 2026-10-20 carry it, four of them with site terms that restrict "
    "copying"
)

# The services whose terms were looked at: the verified ones first, then the others, by country.
TERMS: tuple[SwicTerms, ...] = (
    # ------------------------------------------------------------------------------------------------ verified
    SwicTerms(
        "ar-smn-es", "AR", "Servicio Meteorológico Nacional (SMN)", "verified",
        "CC BY 4.0, stated in the copyright element of its feed (\"Licencia CC BY 4.0\")",
        "https://ssl.smn.gob.ar/CAP/AR.php", "own feed",
        f"Source: Servicio Meteorológico Nacional (SMN), Argentina, licensed under Creative Commons BY 4.0 ({_CC_BY_URL})",
        "The SMN's data policy was found only as a PDF whose text could not be extracted.",
        "cc by 4.0",
    ),
    SwicTerms(
        "bb-dem-xx", "BB", "Department of Emergency Management", "verified",
        "CC BY 4.0, stated in the rights element of its feed",
        "https://brb-primary.capews.com/capews/public/atom?type=cap", "own feed",
        f"Source: Department of Emergency Management, Barbados, licensed under Creative Commons BY 4.0 ({_CC_BY_URL})",
        "The feed runs on a vendor's platform (CAP-EWS) whose own terms were not found; the line names the department as holder. "
        "No warning was in force on 2026-10-20.",
        "cc by 4.0",
    ),
    SwicTerms(
        "dz-onm-fr", "DZ", "Office National de la Météorologie (ONM)", "verified",
        "CC BY 4.0, stated in the rights element of its feed",
        "https://ametvigilance.meteo.dz/rss/rss_meteo_dz.xml", "own feed",
        f"Source: Office National de la Météorologie (ONM), Algeria, licensed under Creative Commons BY 4.0 ({_CC_BY_URL})",
        "No terms page could be read (the ONM's site answered with a title only, probably because it is built in JavaScript).",
        "cc by 4.0",
    ),
    SwicTerms(
        "es-aemet-es", "ES", "Agencia Estatal de Meteorología (AEMET)", "verified",
        "Legal note: reuse for commercial and non-commercial purposes authorised, AEMET cited as the source "
        "(\"© AEMET\" or \"Información elaborada por la Agencia Estatal de Meteorología\")",
        "https://www.aemet.es/es/nota_legal", "terms page",
        "Source: AEMET (Agencia Estatal de Meteorología), Spain. Información elaborada por la Agencia Estatal de Meteorología. © AEMET",
        "The note asks to keep the content unchanged, not to suggest that AEMET supports the reuse, and to keep the update date "
        "and the reuse conditions of the document: the API passes the document's own dates and texts. It does not mention CAP "
        "by name. The feed's copyright element points to the note.",
        "nota_legal",
    ),
    SwicTerms(
        "jm-jms-en", "JM", "Meteorological Service of Jamaica", "verified",
        "CC BY 4.0, stated in the rights element of its feed",
        "https://alert.metservice.gov.jm/capfeed.php", "own feed",
        f"Source: Meteorological Service of Jamaica, licensed under Creative Commons BY 4.0 ({_CC_BY_URL})",
        "The service's main site could not be read. When nothing is in force the feed holds one entry that says so, with no CAP "
        "document: the API reads that as an empty list.",
        "cc by 4.0",
    ),
    SwicTerms(
        "mx-smn-es", "MX", "Servicio Meteorológico Nacional (SMN), CONAGUA", "verified",
        "CC BY 4.0, stated in its feed; the terms page lets anyone use and share the resources citing the institution",
        "https://smn.conagua.gob.mx/tools/PHP/feedsmn/cap.php", "own feed",
        f"Source: Servicio Meteorológico Nacional (SMN), CONAGUA, Mexico, licensed under Creative Commons BY 4.0 ({_CC_BY_URL}). "
        "Not an official channel of the SMN",
        "Terms page: https://smn.conagua.gob.mx/es/terminos-y-condiciones. It forbids passing as an official channel and copying "
        "the original for direct profit: this API is neither. It speaks of the site's information, data, maps and services, not "
        "of the CAP feed by name.",
        "creative commons attribution 4.0",
    ),
    SwicTerms(
        "ph-pagasa-en", "PH", "PAGASA (Philippine Atmospheric, Geophysical and Astronomical Services Administration)", "verified",
        "CC BY 4.0, stated in the rights element of its feed",
        "https://publicalert.pagasa.dost.gov.ph/feeds/", "own feed",
        f"Source: PAGASA (Philippines), licensed under Creative Commons BY 4.0 ({_CC_BY_URL})",
        "No terms page of pagasa.dost.gov.ph was found. The server is slow: 34 s for the read of 2026-10-20.",
        "cc by 4.0",
    ),
    # ------------------------------------------------------------------------------------------------ not verified
    SwicTerms(
        "by-belhydromet-en", "BY", "Belhydromet", "unverified",
        "None found; the feed, run by the Hydrometeorological Centre of Russia, says \"public domain\"",
        "https://meteoalert.meteoinfo.ru/belarus/cap-feed/en/atom.xml", "own feed",
        "Source: Belhydromet (Belarus). Reuse terms not verified",
        "The line is the host's, not the issuer's; Belhydromet's own site was not read.",
    ),
    SwicTerms(
        "co-ungrd-es", "CO", "UNGRD (Unidad Nacional para la Gestión del Riesgo de Desastres), alerts of the IDEAM", "unverified",
        "\"Dominio Público\" in the rights element of its feed",
        "https://www.gestiondelriesgo.gov.co/rss/AtomAlert.aspx", "own feed",
        "Source: UNGRD (Colombia), alerts of the IDEAM. Reuse terms not verified",
        f"{_BARE}. The terms pages of the UNGRD and of the IDEAM could not be read. Every document read on 2026-10-20 had the "
        "status Test, which the API does not return.",
        "dominio publico",
    ),
    SwicTerms(
        "gy-hms-en", "GY", "Hydrometeorological Service of Guyana", "unverified",
        "\"public domain\" in the copyright element of its feed",
        "https://hydromet.gov.gy/cap/en/alerts/rss.xml", "own feed",
        "Source: Hydrometeorological Service of Guyana. Reuse terms not verified",
        f"{_BARE}. The feed's own date is July 2024.",
        "public domain",
    ),
    SwicTerms(
        "in-ndma-xx", "IN", "National Disaster Management Authority (NDMA), SACHET", "unverified",
        "\"public domain\" in the copyright element of its feed",
        "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml", "own feed",
        "Source: National Disaster Management Authority (NDMA), India. Reuse terms not verified",
        f"{_BARE}. SACHET relays the alerts of the IMD and of the states' agencies; its pages describe the RSS as meant for news "
        "agencies to rebroadcast alerts, which is not a licence. The documents name districts by code and carry no polygon: "
        "they are listed by country and not placed. The feed took 21 s to read.",
        "public domain",
    ),
    SwicTerms(
        "in-imd-en", "IN", "India Meteorological Department (IMD)", "unverified",
        "None found for the CAP feed (the line \"public domain\" is the WMO's template, the feed being in its bucket)",
        "https://mausam.imd.gov.in/imd_latest/contents/disclaimer.php", "none found",
        "Source: India Meteorological Department (IMD). Reuse terms not verified",
        "The disclaimer that could be read is a disclaimer of liability only; the IMD's main domain refused the connection. "
        "The feed held no alert on 2026-10-20.",
    ),
    SwicTerms(
        "id-inatews-id", "ID", "BMKG (Badan Meteorologi, Klimatologi, dan Geofisika)", "unverified",
        "Terms of use: cite \"Sumber: BMKG\"; repackaging, integration in a third party's application or commercial use need BMKG's "
        "written permission",
        "https://www.bmkg.go.id/ketentuan-penggunaan", "terms page",
        "Source: BMKG (Badan Meteorologi, Klimatologi, dan Geofisika), Indonesia. Reuse terms not verified",
        "The page (updated 8 July 2026) does not mention CAP; a relay is probably \"repackaging\": to confirm with BMKG. The feed "
        "answered 403 with a bot-detection page on 2026-10-20: the API does not get round it.",
        restriction="restricts",
    ),
    SwicTerms(
        "kz-kazhydromet-ru", "KZ", "Kazhydromet", "unverified",
        "The feed, run by the Hydrometeorological Centre of Russia, says \"public domain\"; Kazhydromet's own site says all rights "
        "are reserved",
        "https://meteoalert.meteoinfo.ru/kazakhstan/cap-feed/en/atom.xml", "own feed",
        "Source: Kazhydromet (Kazakhstan). Reuse terms not verified",
        "The two statements contradict each other; the line in the feed is the host's.",
        restriction="contradictory",
    ),
    SwicTerms(
        "kw-dom-en", "KW", "Meteorological Department of Kuwait (DGCA)", "unverified",
        "Contradictory: the feed says \"public domain\"; the site's notice says its meteorological information is the property of "
        "the Directorate General of Civil Aviation and needs its prior written approval to be broadcast or published",
        "https://met.gov.kw/aboutus.php?lang=eng", "terms page",
        "Source: Meteorological Department, Kuwait (DGCA). Reuse terms not verified",
        "The notice speaks of the site, not of the alerts by name.",
        restriction="contradictory",
    ),
    SwicTerms(
        "mv-met-en", "MV", "Maldives Meteorological Service", "unverified",
        "\"public domain\" in the copyright element of its feed",
        "https://cap.meteorology.gov.mv/rss/alerts/", "own feed",
        "Source: Maldives Meteorological Service. Reuse terms not verified",
        f"{_BARE}. The service's page on climatological data forbids handing the data asked for on a form to a third party; it "
        "does not speak of the alerts.",
        "public domain",
    ),
    SwicTerms(
        "ru-roshydromet-en", "RU", "Hydrometeorological Centre of Russia (Roshydromet)", "unverified",
        "\"public domain\" in the rights element of its feed",
        "https://meteoinfo.ru/hmc-output/cap/cap-feed/en/atom.xml", "own feed",
        "Source: Hydrometeorological Centre of Russia (Roshydromet). Reuse terms not verified",
        f"{_BARE}. meteoinfo.ru has no terms page (its footer holds only the copyright line).",
        "public domain",
    ),
    SwicTerms(
        "sg-mss-en", "SG", "Meteorological Service Singapore (MSS)", "unverified",
        "Contradictory: the feed says \"public domain\"; the terms of use forbid reproducing, republishing or distributing the "
        "site's contents without MSS's prior written permission and allow personal, internal, non-commercial use only",
        "https://www.weather.gov.sg/terms-of-use/", "terms page",
        "Source: Meteorological Service Singapore (MSS). Reuse terms not verified",
        "The terms speak of the site's contents; they mention neither the CAP feed nor the Singapore Open Data Licence. Fine for "
        "an instance only its owner uses, not for a relay.",
        restriction="contradictory",
    ),
    SwicTerms(
        "th-tmd-th", "TH", "Thai Meteorological Department (TMD)", "unverified",
        "Contradictory: the feed says \"public domain\"; the copyright page allows public dissemination without commercial purpose, "
        "citing the Department, and the policy page forbids copying or reselling the service without its permission",
        "https://www.tmd.go.th/content/copyright", "terms page",
        "Source: Thai Meteorological Department (TMD). Reuse terms not verified",
        "Both pages speak of the site's content, not of the alerts. Some documents date themselves in the Buddhist era (year "
        "2568): the API reads them as Gregorian.",
        restriction="contradictory",
    ),
    SwicTerms(
        "tt-ttms-en", "TT", "Trinidad and Tobago Meteorological Service (TTMS)", "unverified",
        "Contradictory: the feed says \"public domain\"; the website terms forbid copying and distributing any part of the site "
        "except as the terms expressly permit",
        "https://www.metoffice.gov.tt/terms-conditions", "terms page",
        "Source: Trinidad and Tobago Meteorological Service (TTMS). Reuse terms not verified",
        "The terms page was read through a summarising tool only (the raw page timed out). The feed lists 848 items in 641 KB "
        "and took 44 s to read, with no validator: only the items of the last 14 days are read.",
        restriction="contradictory",
    ),
    SwicTerms(
        "uy-inumet-es", "UY", "Instituto Uruguayo de Meteorología (INUMET)", "unverified",
        "\"public domain\" in the copyright element of its feed",
        "https://www.inumet.gub.uy/reportes/riesgo/rss.xml", "own feed",
        "Source: Instituto Uruguayo de Meteorología (INUMET). Reuse terms not verified",
        f"{_BARE}. The site mentions an open data catalogue whose terms could not be read.",
        "public domain",
    ),
    SwicTerms(
        "br-inmet-pt", "BR", "Instituto Nacional de Meteorologia (INMET)", "unverified",
        "None found (the pages of portal.inmet.gov.br that could be read carry no terms)",
        "https://portal.inmet.gov.br/", "none found",
        "Source: Instituto Nacional de Meteorologia (INMET), Brazil. Reuse terms not verified",
        "The feed's server closed the connection on every try from the author's PC on 2026-10-20: not read.",
    ),
    SwicTerms(
        "cl-meteo-es", "CL", "Dirección Meteorológica de Chile (DMC)", "unverified",
        "None found (the site could not be read)",
        "https://www.meteochile.gob.cl/", "none found",
        "Source: Dirección Meteorológica de Chile (DMC). Reuse terms not verified",
        "The feed's server did not answer in time from the author's PC on 2026-10-20: not read.",
    ),
    SwicTerms(
        "ke-kmd-en", "KE", "Kenya Meteorological Department (KMD)", "unverified",
        "None found (the site has no terms, privacy or copyright page)",
        "https://meteo.go.ke/", "none found",
        "Source: Kenya Meteorological Department (KMD). Reuse terms not verified",
        "The registry lists a feed in the WMO's bucket, which carries the WMO's template line, not the KMD's words.",
    ),
    SwicTerms(
        "ng-nimet-en", "NG", "Nigerian Meteorological Agency (NiMet)", "unverified",
        "None read: its data policy is a PDF whose text could not be extracted",
        "https://nimet.gov.ng/admin/uploads/NIMET%20DATA%20POLICY-2SUUFhcs-6.pdf", "none found",
        "Source: Nigerian Meteorological Agency (NiMet). Reuse terms not verified",
        "The registry lists a feed in the WMO's bucket, which carries the WMO's template line, not NiMet's words.",
    ),
    SwicTerms(
        "sa-ncm-en", "SA", "National Center for Meteorology (NCM), Saudi Arabia", "unverified",
        "None read (the NCM's site did not answer)",
        "https://ncm.gov.sa/", "none found",
        "Source: National Center for Meteorology (NCM), Saudi Arabia. Reuse terms not verified",
        "The feed address of the registry answers 404 (after 50 s). A press article, not checked, says a licence is needed to "
        "publish weather information in the country.",
    ),
    SwicTerms(
        "vu-vms-xx", "VU", "Vanuatu Meteorology and Geo-hazards Department (VMGD)", "unverified",
        "Terms and conditions: for the user's own organisation's internal use, no modification without written permission, VMGD "
        "acknowledged; redistribution for commercial purposes forbidden",
        "https://www.vmgd.gov.vu/terms-and-conditions", "terms page",
        "Source: Vanuatu Meteorology and Geo-hazards Department (VMGD). Reuse terms not verified",
        "The terms speak of the website. The alert server did not answer from the author's PC on 2026-10-20.",
        restriction="restricts",
    ),
    SwicTerms(
        "za-saws-en", "ZA", "South African Weather Service (SAWS)", "unverified",
        "The South African Weather Service Act, article 26: what is supplied to a client may not be given to a third party "
        "without the written consent of the chief executive, and the Service is to be acknowledged",
        "https://www.acts.co.za/south-african-weather-service-act-2001/26_intellectual_property_rights", "terms page",
        "Source: South African Weather Service (SAWS). Reuse terms not verified",
        "A third party's copy of the Act, not the Service's own terms (its sites could not be read). The article speaks of data "
        "supplied to a client, not of the public feed. The feed's certificate did not verify from the author's PC: not read.",
        restriction="restricts",
    ),
)

# Services never read, whatever ``VIGIE_WMO_SOURCES`` says, and why.
EXCLUDED: dict[str, str] = {
    "hk-hko-xx": "Hong Kong Observatory: contradictory terms (the owner's decision of 2026-10-20)",
    "cn-cma-xx": (
        "China Meteorological Administration: the feed lists 1,000 items (1 MB) and each CAP document weighs about 89 KiB; "
        "reading it would cost tens of megabytes at each start, and its terms were not found"
    ),
}

# A country that has MeteoAlarm's feed but whose own service gives what MeteoAlarm's does not (polygons). Decided on evidence:
# docs/decisions/0006-wmo-registry-services.md.
COUNTRY_OVERRIDES: dict[str, str] = {"ES": "es-aemet-es"}

_BY_ID = {terms.source_id: terms for terms in TERMS}


def terms_of(source_id: str) -> Optional[SwicTerms]:
    return _BY_ID.get(source_id)


def status_of(source_id: str) -> Status:
    """``verified`` or ``unverified`` (a service no record knows is unverified)."""
    terms = _BY_ID.get(source_id)
    return terms.status if terms is not None else "unverified"


def is_eligible(source: SwicSource, mode: str) -> bool:
    """Whether an instance in ``mode`` (``verified``, ``all`` or ``off``) reads this service."""
    if mode == "off" or source.source_id in EXCLUDED or not source.feeds or not source.country:
        return False
    return mode == "all" or status_of(source.source_id) == "verified"


def credit_of(source: SwicSource) -> str:
    """The credit of a service's warnings: the record's text, else one built from the registry; then the WMO's."""
    terms = _BY_ID.get(source.source_id)
    if terms is not None:
        own = terms.credit
    else:
        where = f" ({source.country_name})" if source.country_name and source.country_name not in source.name else ""
        own = f"Source: {source.name}{where}. Reuse terms not verified"
    return merge_credits(own, credit_text("wmo_swic")) or own
