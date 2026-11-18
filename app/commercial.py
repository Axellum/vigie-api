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
            "Copernicus CAMS licence: any lawful use (including commercial) with the clause 1.3 credit "
            "('Generated using Copernicus Atmosphere Monitoring Service Information [Year]') and non-responsibility "
            "notice. Here it comes through Open-Meteo's free API, which is non-commercial only.",
            ("https://apps.ecmwf.int/datasets/licences/cams",),
            "2026-11-15",
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
            "nasa_eclipses",
            "allowed",
            "The eclipse site's copyright page: NASA material is not protected by copyright unless noted and may be reproduced "
            "without further permission. Each page grants reproduction of the data with the acknowledgment, which is the credit.",
            (
                "https://eclipse.gsfc.nasa.gov/SEpubs/copyright.html",
                "https://eclipse.gsfc.nasa.gov/SEbeselm/SEbeselm2001/SE2027Aug02Tbeselm.html",
            ),
            "2026-10-28",
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
            "vigieau",
            "allowed",
            "Licence Ouverte 2.0 (Etalab) on VigiEau open datasets (source and date named; prefectural decrees in RAA are authoritative; the API dataservice page leaves licence unset).",
            (
                "https://www.data.gouv.fr/datasets/donnee-secheresse-vigieau",
                "https://www.data.gouv.fr/datasets/vigieau-arretes-secheresse-en-vigueur",
                "https://www.data.gouv.fr/dataservices/api-vigieau",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "gaspar_catnat",
            "allowed",
            "Licence Ouverte 2.0 (Etalab, lov2) on data.gouv.fr for both the TACCT GASPAR CatNat dataset and DINUM/Etalab API Découpage Administratif (source and date named; TACCT extraction is not live GASPAR/Géorisques and Journal officiel decrees remain authoritative).",
            (
                "https://www.data.gouv.fr/datasets/arretes-de-catastrophe-naturelle-par-commune",
                "https://www.data.gouv.fr/dataservices/api-decoupage-administratif-api-geo",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "slf_avalanche",
            "allowed",
            "CC BY 4.0 on SLF Data Service (visible source attribution to WSL Institute for Snow and Avalanche Research SLF with link to www.slf.ch required; infrastructure must not be overloaded).",
            (
                "https://www.slf.ch/en/services-and-products/slf-data-service/",
                "https://aws.slf.ch/api/bulletin/caaml/en/geojson",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "meteofrance_bra",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): any use, commercial included, with the source and the date of the data named.",
            (
                "https://www.data.gouv.fr/datasets/archive-des-bulletins-avalanches",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-16",
        ),
        CommercialUse(
            "euregio_avalanche",
            "allowed",
            "CC BY 4.0 DEED on Avalanche.report Open Data (Tirol, Südtirol, Trentino CAAML v6 bulletins; EAWS micro-region boundaries are CC0 1.0 Universal).",
            (
                "https://avalanche.report/content/more/open-data/en.html",
                "https://static.avalanche.report/bulletins/latest/EUREGIO_en_CAAMLv6.json",
                "https://gitlab.com/eaws/eaws-regions/-/raw/master/LICENSE",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "meteofrance_vigilance",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): any use, commercial included, with the source and the date of the data named.",
            (
                "https://www.data.gouv.fr/datasets/675bf4442d16a89b0a767b24",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "meteofrance_climate",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): any use, commercial included, with the source and the date of the data named.",
            (
                "https://www.data.gouv.fr/datasets/fiches-climatologiques",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-17",
        ),
        CommercialUse(
            "meteofrance_stations",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): any use, commercial included, with the source and the date of the data named.",
            (
                "https://www.data.gouv.fr/datasets/66ebf2cb9dd6916802ae90a1",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "meteofrance_piaf",
            "not_verified",
            "Free of charge on the Météo-France portal (its catalogue says PAYANT: Non); the catalogue entry names no licence, "
            "and the terms of this API were not read.",
            ("https://portail-api.meteofrance.fr/api/am/devportal/v3/apis/aa174208-b051-4c95-af8f-dd52241a4d06",),
            "2026-11-16",
        ),
        CommercialUse(
            "meteofrance_weather",
            "not_verified",
            "The models (AROME, ARPEGE) are Météo-France's, published as open data; the files this API reads are the ones an Open-Meteo "
            "server synchronises from Open-Meteo's own bucket (processed by Open-Meteo, whose terms for those files were not read for "
            "this source), and the weather code is Open-Meteo's calculation from ARPEGE. Open-Meteo's terms for its free API are in "
            "the `open_meteo` entry; self-hosting the server is said to allow commercial use there.",
            ("https://open-meteo.com/en/terms",),
            "2026-10-20",
        ),
        CommercialUse(
            "meteofrance_radar",
            "allowed",
            "Licence Ouverte 2.0 (Etalab), as the 'Package Radar' dataset of Météo-France on data.gouv.fr states it (the radar mosaics are "
            "the content of that package): any use, commercial included, with the source named. The page of the API itself "
            "(DonneesPubliquesRadar) names no licence, only 'Source : Météo-France', and was not read beyond its catalogue entry.",
            (
                "https://www.data.gouv.fr/datasets/package-radar/informations",
                "https://portail-api.meteofrance.fr/api/am/devportal/v3/apis/fd7d27d0-9f8e-491a-94ce-8d281edb201d",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-18",
        ),
        CommercialUse(
            "meteofrance_shortterm",
            "allowed",
            "Licence Ouverte 2.0 (Etalab), as the licence document of the AROME-PI API on Météo-France's portal states it (read on 2026-11-18): "
            "reuse, commercial included, with the source named, the date of the last update given (the run) and the data not altered. The levels "
            "(strong gusts, reduced visibility) and the kinds of precipitation are worked out by Vigie.",
            (
                "https://portail-api.meteofrance.fr/api/am/devportal/v3/apis/5b715300-4f8e-46a0-9e45-feceece08280/documents/ee8ea595-7308-4f4f-9033-009a7ca2193e/content",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-18",
        ),
        CommercialUse(
            "meteofrance_ensemble",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): the licence document of each of the two APIs on Météo-France's portal (PE-AROME and PE-ARPEGE, "
            "read on 2026-11-18) says the data are under it and that the reuse, commercial included, needs the source named, the date of the last "
            "update given (the run) and the data not altered or their meaning distorted. The PE-ARPEGE probabilities are counted by Vigie among "
            "the 35 members; the credit says so.",
            (
                "https://portail-api.meteofrance.fr/api/am/devportal/v3/apis/435d98d5-173c-4e0f-ba75-6fa28c7de868/documents/e35741d2-177e-4cc5-84cb-c9326e81815a/content",
                "https://portail-api.meteofrance.fr/api/am/devportal/v3/apis/b3f3f4c9-5289-4c02-97d0-29c7570e83b1/documents/eb0fc7a2-4a86-4583-ae93-1bc27e5f343f/content",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-18",
        ),
        CommercialUse(
            "meteofrance_uv",
            "not_verified",
            "Free of charge on the Météo-France portal; the API's own description names the source ('Source : Météo-France') and no "
            "licence, and its terms were not read. The UV index of the current hour is calculated by Vigie.",
            ("https://portail-api.meteofrance.fr/api/am/devportal/v3/apis/274043ad-73a8-4713-9cba-cb34fce8b746/swagger",),
            "2026-11-18",
        ),
        CommercialUse(
            "meteofrance_buoys",
            "not_verified",
            "Free of charge on the Météo-France portal (catalogue: PAYANT: Non, tags 'données publiques' and 'open data', the family of "
            "DPPaquetObs, which is under Licence Ouverte 2.0); the page of this API is rendered by script and its licence was not read.",
            (
                "https://portail-api.meteofrance.fr/api/am/devportal/v3/apis/c3b1fe90-c209-447c-8cab-8342bf157b83",
                "https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesObservation",
            ),
            "2026-11-16",
        ),
        CommercialUse(
            "meteofrance_obs",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): any use, commercial included, with the source and the date of the data named.",
            (
                "https://portail-api.meteofrance.fr/web/fr/api/PaquetObs",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "copernicus_mfwam",
            "allowed",
            "Copernicus Marine licence: value-added products and derivative works may be distributed \"for any purpose\", "
            "royalty free, with the credit and the product's DOI. Here it comes through Open-Meteo's free API, which is not: "
            "the surf block stays forbidden while it does.",
            ("https://marine.copernicus.eu/user-corner/service-commitments-and-licence",),
            "2026-10-28",
        ),
        CommercialUse(
            "copernicus_ocean",
            "allowed",
            "Copernicus Marine licence: value-added products and derivative works may be distributed \"for any purpose\", "
            "royalty free, with the credit and the product's DOI. Here it comes through Open-Meteo's free API, which is not: "
            "the surf block stays forbidden while it does. Open-Meteo names Météo-France and Copernicus Marine for the sea "
            "temperature, not a product: the DOI is that of the global 1/12 degree product, not confirmed to be the one behind it.",
            ("https://marine.copernicus.eu/user-corner/service-commitments-and-licence",),
            "2026-11-08",
        ),
        CommercialUse(
            "osm_spots",
            "allowed",
            "ODbL 1.0: any use, with the credit, and a database made from it is shared under the ODbL too (the spot file is).",
            ("https://www.openstreetmap.org/copyright", "https://opendatacommons.org/licenses/odbl/1-0/"),
            "2026-10-28",
        ),
        CommercialUse(
            "gip_littoral_plages",
            "allowed",
            "Licence Ouverte 2.0 (`lov2` on data.gouv.fr, read on 2026-11-15): any use, commercial included, naming the source "
            "and the date.",
            (
                "https://www.data.gouv.fr/datasets/plages-de-nouvelle-aquitaine-1",
                "https://www.pigma.org/fr/dataset/datasets/6870/resource/13842/download/",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "shom_ndf",
            "allowed",
            "Licence Ouverte 2.0 d'avril 2017 (read in the ISO 19115 CSW metadata `HOM_GEOL_NATURES_FOND_50.xml`, dated "
            "2025-07-09): any free reuse citing `Shom, 2025. Natures de fond au 1/50 000, https://dx.doi.org/10.17183/HOM_GEOL_NATURES_FOND_50`; "
            "not for navigation.",
            (
                "https://services.data.shom.fr/geonetwork/INSPIRE?service=CSW&version=2.0.2&request=GetRecordById&elementSetName=full&Id=HOM_GEOL_NATURES_FOND_50.xml",
                "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            ),
            "2026-11-15",
        ),
        CommercialUse(
            "osm_places",
            "allowed",
            "ODbL 1.0: any use, with the credit, and a database made from it is shared under the ODbL too (the place file is).",
            ("https://www.openstreetmap.org/copyright", "https://opendatacommons.org/licenses/odbl/1-0/"),
            "2026-11-08",
        ),
        CommercialUse(
            "sandre_ports",
            "allowed",
            "Licence Ouverte (`fr-lo`, the dataset page of data.gouv.fr read on 2026-11-08): any use, with the source and the date of the "
            "data. The version of the licence is not stated on the page.",
            ("https://www.data.gouv.fr/datasets/ports-espace-maritime-francais",),
            "2026-11-08",
        ),
        CommercialUse(
            "qsb_bathing",
            "allowed",
            "Licence Ouverte 2.0 (`lov2`, the dataset page of data.gouv.fr read on 2026-11-08): any use, with the source and the date. "
            "TACCT republishes the ministry's results and calculates the class: not the ministry's own file.",
            ("https://www.data.gouv.fr/datasets/qualite-des-sites-de-baignade-1",),
            "2026-11-08",
        ),
        CommercialUse(
            "candhis_stations",
            "allowed",
            "Licence Ouverte 2.0 (the dataset page of data.gouv.fr and the Candhis terms, `01_Utilisation.fr.pdf` read on 2026-10-28): any use, "
            "with the source and the date. This is the list of the stations only; the measures are `candhis_tr`.",
            ("https://www.data.gouv.fr/datasets/674ee38cbede289cf234d829", "https://candhis.cerema.fr/doc/01_Utilisation.fr.pdf"),
            "2026-11-09",
        ),
        CommercialUse(
            "candhis_tr",
            "allowed",
            "The Candhis site says its data are \"diffusées gratuitement sous licence Etalab\" (the home page, read on 2026-11-09), and its terms "
            "(`01_Utilisation.fr.pdf`, read on 2026-10-28) give Licence Ouverte 2.0: any use, with the source and the date of the data. Not verified: "
            "that the key of the API adds no condition (the e-mail that gave it states only the quota, 150 requests a day).",
            ("https://candhis.cerema.fr/", "https://candhis.cerema.fr/doc/01_Utilisation.fr.pdf", "https://candhis.cerema.fr/doc/04_Candhis_API_v1_Utilisateur.pdf"),
            "2026-11-09",
        ),
        CommercialUse(
            "noaa_ndbc",
            "allowed",
            "Public domain unless noted otherwise (NWS disclaimer linked from ndbc.noaa.gov footer; no implied endorsement, no altered "
            "content presented as official). Not verified: whether international partner buoys in the feed add conditions of their own.",
            ("https://www.ndbc.noaa.gov/", _NWS_DISCLAIMER),
            "2026-11-15",
        ),
        CommercialUse(
            "surf_height_factors",
            "not_verified",
            "The factors are numbers calculated by Vigie from two series. The CANDHIS measures (Licence Ouverte 2.0, `01_Utilisation.fr.pdf` read on 2026-10-28) allow any use "
            "with the source and the date. The forecast they were compared with was read, 11 months of it, through the free API of Open-Meteo, whose terms are non-commercial "
            "(see `open_meteo`); whether a constant calculated from that series may be shown in a paid app was not asked, so it is not claimed. The answer that carries the "
            "corrected height also carries the credits and the lines of `open_meteo` and `copernicus_mfwam`.",
            ("https://candhis.cerema.fr/doc/01_Utilisation.fr.pdf", "https://open-meteo.com/en/terms"),
            "2026-11-10",
        ),
        CommercialUse(
            "refmar_tides",
            "allowed",
            "Licence Ouverte 2.0 (data.gouv.fr, the RONIM network): any use, with the source. Used offline, by the tool that fits the "
            "correction; the answer carries two numbers and the credit, not the observations. The exact mention REFMAR asks for was not read.",
            ("https://refmar.shom.fr/donnees-refmar-sur-data.shom.fr/telechargement-des-donnees",),
            "2026-11-08",
        ),
        CommercialUse(
            "eot20",
            "allowed",
            "CC BY 4.0 (the SEANOE page): the credit, a link to the licence and a statement of the changes. Computed by "
            "Vigie, with no request to anyone.",
            ("https://www.seanoe.org/data/00683/79489/", "https://creativecommons.org/licenses/by/4.0/"),
            "2026-10-28",
        ),
        CommercialUse(
            "shom_refmar",
            "allowed",
            "Licence Ouverte 2.0 (Etalab): any use, commercial included, with the source named (the REFMAR data page of data.gouv.fr). "
            "Not verified: the exact mention REFMAR asks for.",
            ("https://www.data.gouv.fr/datasets/reseaux-de-reference-des-observations-maregraphiques-refmar-maregraphes-ronim/informations",),
            "2026-11-08",
        ),
        CommercialUse(
            "travel_gb",
            "allowed",
            "Open Government Licence v3.0: commercial and non-commercial use, with the statement it prescribes; no crest or "
            "logo, no suggested official status or endorsement.",
            ("https://www.gov.uk/help/reuse-govuk-content", "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"),
            "2026-10-28",
        ),
        CommercialUse(
            "travel_us",
            "allowed",
            "Public domain unless a copyright is indicated (read on the archived copy of 2025-01-07: the live page answered "
            "with a bot check).",
            ("https://travel.state.gov/content/travel/en/copyright-disclaimer.html",),
            "2026-10-28",
        ),
        CommercialUse(
            "travel_ca",
            "allowed",
            "Open Government Licence – Canada, including for commercial purposes, with the statement it prescribes; no "
            "official symbol, no suggested endorsement.",
            ("https://open.canada.ca/data/en/dataset/bef2ebb3-ca9a-485f-aaff-5dc36eb89426", "https://open.canada.ca/en/open-government-licence-canada"),
            "2026-10-28",
        ),
        CommercialUse(
            "travel_au",
            "not_verified",
            "The site is under the latest CC BY, but the same page keeps downloadable digital material from commercial use "
            "without DFAT's written consent, and does not say whether the export is such material. Never the logo.",
            ("https://www.smartraveller.gov.au/copyright", "https://www.smartraveller.gov.au/our-role/resources"),
            "2026-10-28",
        ),
        CommercialUse(
            "travel_nl",
            "allowed",
            "CC0 1.0 Universal, in the licence field of every advice of the v2 feed.",
            ("https://opendata.nederlandwereldwijd.nl/v2/sources/nederlandwereldwijd/infotypes/traveladvice", "https://www.nederlandwereldwijd.nl/open-data"),
            "2026-10-28",
        ),
        CommercialUse(
            "health_ecdc",
            "allowed",
            "CC BY 4.0, \"for commercial or non-commercial purposes\": ECDC named as the creator, a link to the licence, and the "
            "changes said. Not for content of third parties, nor the logo.",
            ("https://www.ecdc.europa.eu/en/copyright",),
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
