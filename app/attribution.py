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
from typing import Any, Literal, Optional

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
            "ecmwf_opendata",
            "ECMWF (open data)",
            "Forecast: ECMWF IFS open data (CC BY 4.0). Unofficial pre-alert computed by Vigie, not an official warning",
            "ours",
        ),
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
            "noaa_nhc",
            "NOAA National Hurricane Center",
            "Source: NOAA National Weather Service, National Hurricane Center (https://www.nhc.noaa.gov). Not endorsed by NOAA or the NWS",
            "ours",
        ),
        Credit(
            "noaa_tsunami",
            "NOAA U.S. Tsunami Warning Centers",
            "Source: NOAA National Weather Service, U.S. Tsunami Warning Centers (https://www.tsunami.gov). Not endorsed by NOAA or the NWS",
            "ours",
        ),
        Credit(
            "noaa_swpc",
            "NOAA Space Weather Prediction Center",
            "Source: NOAA Space Weather Prediction Center (https://www.swpc.noaa.gov). Not endorsed by NOAA",
            "ours",
        ),
        Credit(
            "nasa_eclipses",
            "NASA eclipse predictions (Fred Espenak, GSFC)",
            "Eclipse Predictions by Fred Espenak, NASA's GSFC",
            "provider",
        ),
        Credit(
            "nasa_firms",
            "NASA FIRMS (LANCE, ESDIS)",
            "We acknowledge the use of data and/or imagery from NASA's Fire Information for Resource Management System (FIRMS) "
            "(https://earthdata.nasa.gov/firms), part of NASA's Land, Atmosphere Near real-time Capability for Earth observations "
            "(LANCE) (https://earthdata.nasa.gov/lance) and NASA's Earth Science Data and Information System (ESDIS).",
            "provider",
        ),
        Credit(
            "nasa_gibs_imerg",
            "NASA GIBS (GPM IMERG, ESDIS)",
            "We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), "
            "part of NASA's Earth Science Data and Information System (ESDIS).",
            "provider",
        ),
        Credit(
            "meteoalarm",
            "MeteoAlarm (EUMETNET)",
            "Source: EUMETNET – MeteoAlarm (https://meteoalarm.org/en/live/), warnings of the national meteorological "
            "and hydrological services. Terms equivalent to CC BY 4.0 with additional requirements for redistributors",
            "ours",
        ),
        Credit("dwd_warnings", "Deutscher Wetterdienst (DWD)", "Source: Deutscher Wetterdienst", "provider"),
        Credit(
            "eccc_alerts",
            "Environment and Climate Change Canada (MSC)",
            "Data Source: Environment and Climate Change Canada. Contains information licenced under the Data Server "
            "End-use Licence of Environment and Climate Change Canada (https://eccc-msc.github.io/open-data/licence/readme_en/)",
            "provider",
        ),
        Credit(
            "metservice_alerts",
            "MetService (New Zealand)",
            "Source: MetService (Meteorological Service of New Zealand Limited), licensed under Creative Commons BY 4.0 "
            "(https://creativecommons.org/licenses/by/4.0/)",
            "ours",
        ),
        Credit(
            "wmo_swic",
            "WMO Severe Weather Information Centre (national services)",
            "Warnings of a national meteorological service, from the registry of the WMO Severe Weather Information "
            "Centre (https://severeweather.wmo.int). The issuer's own terms apply to each warning",
            "ours",
        ),
        Credit(
            "gem_faults",
            "GEM Global Active Faults Database",
            "Active faults: GEM Global Active Faults Database (Styron and Pagani, 2020, Earthquake Spectra, "
            "doi:10.1177/8755293020944182), CC BY-SA 4.0, simplified by Vigie",
            "suggested",
        ),
        Credit(
            "eot20",
            "EOT20 ocean tide model (DGFI-TUM)",
            "Tides computed by Vigie from the EOT20 ocean tide model (Hart-Davis et al. 2021, DGFI-TUM, SEANOE, "
            "https://doi.org/10.17882/79489), CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); constants kept near the coasts "
            "and rounded by Vigie. Not for navigation",
            "ours",
        ),
        Credit(
            "shom_refmar",
            "REFMAR tide gauges (SHOM)",
            "Source: SHOM, REFMAR network of tide gauges (Licence Ouverte 2.0), https://refmar.shom.fr; the correction of the tide worked "
            "out from it is Vigie's",
            "ours",
        ),
        Credit(
            "copernicus_mfwam",
            "Copernicus Marine (Météo-France MFWAM, through Open-Meteo Marine)",
            "Generated using E.U. Copernicus Marine Service Information; https://doi.org/10.48670/moi-00017",
            "provider",
        ),
        Credit(
            "copernicus_ocean",
            "Copernicus Marine (Météo-France ocean model, through Open-Meteo Marine)",
            "Generated using E.U. Copernicus Marine Service Information; https://doi.org/10.48670/moi-00016",
            "provider",
        ),
        Credit(
            "osm_spots",
            "OpenStreetMap (surf spots)",
            "Surf spots: © OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright); chosen and their "
            "facing worked out by Vigie",
            "ours",
        ),
        Credit(
            "gip_littoral_plages",
            "GIP Littoral (beaches of Nouvelle-Aquitaine)",
            "Beaches of Nouvelle-Aquitaine: GIP Littoral, \"Plages de Nouvelle-Aquitaine\" (PIGMA / data.gouv.fr, read on 2026-11-15), "
            "Licence Ouverte 2.0",
            "ours",
        ),
        Credit(
            "shom_ndf",
            "Shom (Natures de fond au 1/50 000)",
            "Shom, 2025. Natures de fond au 1/50 000, https://dx.doi.org/10.17183/HOM_GEOL_NATURES_FOND_50 (not for navigation)",
            "provider",
        ),
        Credit(
            "osm_places",
            "OpenStreetMap (places of the coast)",
            "Places and localities: © OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright); chosen, merged "
            "and named after the nearest locality by Vigie",
            "ours",
        ),
        Credit(
            "sandre_ports",
            "Sandre (Système d'Information sur l'Eau), ports",
            "Ports: Sandre / Système d'Information sur l'Eau, \"Ports - Espace maritime français\" (file of 2023-12-19), Licence Ouverte; "
            "the ports with pleasure boats are chosen by Vigie",
            "ours",
        ),
        Credit(
            "qsb_bathing",
            "Quality of the bathing sites (TACCT, from the Ministry of Health's results)",
            "Bathing sites: \"Qualité des sites de baignade\", TACCT on data.gouv.fr (Licence Ouverte 2.0), classes of the four years "
            "2021 to 2024 calculated by TACCT from the results of the Ministère des Solidarités et de la Santé",
            "ours",
        ),
        Credit(
            "candhis_stations",
            "CANDHIS (Cerema), list of the wave buoys",
            "Source: Candhis, Cerema (Licence Ouverte 2.0), list of the stations of 2024-12-03; the distances to the surf spots are "
            "calculated by Vigie",
            "ours",
        ),
        Credit(
            "candhis_tr",
            "CANDHIS (Cerema), latest record of the wave buoys",
            "Source: Candhis, Cerema (Licence Ouverte 2.0), latest real-time record of the buoy, dated as in the answer (measured_at, UTC); "
            "the choice of the buoy, its distance and its age are calculated by Vigie",
            "ours",
        ),
        Credit(
            "noaa_ndbc",
            "NOAA National Data Buoy Center (NDBC), latest marine buoy observations",
            "Source: NOAA National Weather Service, National Data Buoy Center (https://www.ndbc.noaa.gov), latest buoy observation "
            "(measured_at, UTC); the choice of the buoy, its distance and its age are calculated by Vigie. Not endorsed by NOAA or the NWS",
            "ours (follows the NWS disclaimer: no credit required, no implied endorsement)",
        ),
        Credit(
            "surf_height_factors",
            "Factors of the sea height of some surf spots, found against CANDHIS buoys",
            "Height of the sea at some surf spots, when the correction is asked: factors calculated by Vigie from the measures of the wave buoys of "
            "Candhis, Cerema (Licence Ouverte 2.0), 2025-12-01 to 2026-10-31, against the MFWAM forecast of Météo-France (Copernicus Marine, through Open-Meteo)",
            "ours",
        ),
        Credit(
            "refmar_tides",
            "REFMAR (SHOM), tide gauge observations",
            "Calibration of the tide: observations of REFMAR, SHOM (Licence Ouverte 2.0, https://refmar.shom.fr); the correction is "
            "calculated by Vigie",
            "ours",
        ),
        Credit(
            "vigicrues",
            "Vigicrues (SCHAPI)",
            "Source: Vigicrues, Service central d'hydrométéorologie et d'appui à la prévision des inondations (SCHAPI), "
            "https://www.vigicrues.gouv.fr. Licence Ouverte 2.0",
            "ours",
        ),
        Credit(
            "meteofrance_vigilance",
            "Météo-France (DPVigilance)",
            "Source: Météo-France, Vigilance météorologique (https://vigilance.meteofrance.fr). Licence Ouverte 2.0",
            "ours",
        ),
        Credit(
            "meteofrance_stations",
            "Météo-France (POSTES_MF.csv), list of the coastal ground stations",
            "Source: Météo-France, file 'POSTES_MF.csv' (Licence Ouverte 2.0), open real-time stations within 35 km of the surf spots; "
            "the distances to the surf spots are calculated by Vigie",
            "ours",
        ),
        Credit(
            "meteofrance_station_names",
            "Météo-France (POSTES_MF.csv), names and altitudes of the ground stations",
            "Source: Météo-France, file 'POSTES_MF.csv' (Licence Ouverte 2.0), names and altitudes of the open ground stations",
            "ours",
        ),
        Credit(
            "meteofrance_obs",
            "Météo-France (DPPaquetObs), latest 6-minute observations",
            "Source: Météo-France, observations infrahoraires 6 min (https://portail-api.meteofrance.fr/web/fr/api/PaquetObs, Licence Ouverte 2.0), "
            "dated as in the answer (measured_at, UTC); the choice of the station, its distance, its age and the relative wind are calculated by Vigie",
            "ours",
        ),
        Credit(
            "meteofrance_buoys",
            "Météo-France (DPObs), latest records of the moored buoys",
            "Source: Météo-France, observations des bouées (https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesObservation), "
            "dated as in the answer (measured_at, UTC); the distance and the age are calculated by Vigie, the units converted by Vigie",
            "ours",
        ),
        Credit(
            "meteofrance_piaf",
            "Météo-France (PIAF), rain over the next hour",
            "Source: Météo-France, prévision immédiate des précipitations PIAF (https://portail-api.meteofrance.fr), "
            "5-minute steps read through a vigie-meteo gateway; the 15-minute amounts are summed by Vigie",
            "ours",
        ),
        Credit(
            "meteofrance_piaf_tiles",
            "Météo-France (PIAF), rain nowcast tiles over France",
            "Source: Météo-France, prévision immédiate des précipitations PIAF (https://portail-api.meteofrance.fr), "
            "5-minute steps drawn as tiles by a vigie-meteo gateway: the intensity classes and their colours are ours",
            "ours",
        ),
        Credit(
            "meteofrance_weather",
            "Météo-France (AROME, ARPEGE), weather over France",
            "Source: Météo-France, modèles AROME et ARPEGE (https://portail-api.meteofrance.fr), "
            "through an Open-Meteo server (https://open-meteo.com/) synchronised with the Météo-France models "
            "and a vigie-meteo gateway; the weather code is calculated by Open-Meteo",
            "ours",
        ),
        Credit(
            "meteofrance_overseas",
            "Météo-France (AROME Outre-mer), weather of the overseas territories",
            "Source: Météo-France, modèle AROME Outre-mer (https://portail-api.meteofrance.fr, Licence Ouverte 2.0), read by a vigie-meteo gateway, "
            "dated by its run; the weather code is worked out by Vigie from the rain and the cloud cover, not a Météo-France product",
            "ours",
        ),
        Credit(
            "meteofrance_radar",
            "Météo-France (DPRadar), radar mosaics of metropolitan France and of the overseas areas",
            "Source: Météo-France, mosaïque radar lame d'eau (https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesRadar, "
            "Licence Ouverte 2.0), drawn as tiles by a vigie-meteo gateway: the intensity classes and their colours are ours",
            "ours",
        ),
        Credit(
            "meteofrance_shortterm",
            "Météo-France (AROME-PI), hail, gusts, visibility and type of precipitation of the next hours",
            "Source: Météo-France, modèle AROME-PI (https://portail-api.meteofrance.fr, Licence Ouverte 2.0), read by a vigie-meteo gateway; "
            "the levels (strong gusts, reduced visibility) and the kinds of precipitation are worked out by Vigie, not an official warning",
            "ours",
        ),
        Credit(
            "meteofrance_ensemble",
            "Météo-France (PE-AROME, PE-ARPEGE), probabilities of rain, snow, gusts, hail and storm",
            "Source: Météo-France, prévisions d'ensemble PE-AROME and PE-ARPEGE (https://portail-api.meteofrance.fr, Licence Ouverte 2.0), read by a "
            "vigie-meteo gateway; the PE-AROME probabilities are Météo-France's, the PE-ARPEGE ones are counted by Vigie among the 35 members; "
            "a model forecast, not an official warning",
            "ours",
        ),
        Credit(
            "meteofrance_station_daily",
            "Météo-France (données climatologiques de base), daily measures of the stations",
            "Source: Météo-France, données climatologiques de base quotidiennes (https://portail-api.meteofrance.fr, Licence Ouverte 2.0), "
            "read by a vigie-meteo gateway and dated as in the answer (fetched_at_utc); a measure Météo-France marks doubtful is left out; "
            "the choice of the station and its distance are calculated by Vigie",
            "ours",
        ),
        Credit(
            "meteofrance_forests",
            "Météo-France (Météo des forêts), forest fire danger of the departments",
            "Source: Météo-France, Météo des forêts (https://portail-api.meteofrance.fr, Licence Ouverte 2.0), read by a vigie-meteo gateway and "
            "dated as in the answer (reference_time); a danger for a whole department, not a warning and not a fire detected; the department of "
            "a position is found by Vigie",
            "ours",
        ),
        Credit(
            "meteofrance_uv",
            "Météo-France (PHEALTH), daily UV index",
            "Source: Météo-France, indice UV quotidien du modèle PHEALTH (https://portail-api.meteofrance.fr); "
            "the UV index of the current hour is calculated by Vigie from the maximum of the day",
            "ours",
        ),
        Credit(
            "meteofrance_wind_field",
            "Météo-France (AROME, ARPEGE), wind forecast at 10 m for the map",
            "Source: Météo-France, forecast models AROME (France) and ARPEGE (Europe, world), wind at 10 m "
            "(https://portail-api.meteofrance.fr, Licence Ouverte 2.0), dated by the run in the answer (run_at); drawn by Vigie "
            "at a reduced resolution (every 2nd node, lightly smoothed)",
            "ours",
        ),
        Credit(
            "meteofrance_rain_field",
            "Météo-France (AROME, ARPEGE), precipitation forecast for the map",
            "Source: Météo-France, forecast models AROME (France) and ARPEGE (Europe, world), total precipitation "
            "(https://portail-api.meteofrance.fr, Licence Ouverte 2.0), dated by the run in the answer (run_at); rendered by Vigie "
            "at a reduced resolution",
            "ours",
        ),
        Credit(
            "meteofrance_climate",
            "Météo-France (Fiches climatologiques), normals 1991-2020 and records of the stations",
            "Source: Météo-France, Fiches climatologiques (https://www.data.gouv.fr/datasets/fiches-climatologiques, Licence Ouverte 2.0), "
            "dated as in the answer (sheet_edited); the choice of the station, its distance and the comparison with a given value are "
            "calculated by Vigie",
            "ours",
        ),
        Credit(
            "vigieau",
            "VigiEau (Ministère de la Transition écologique)",
            "Source: VigiEau, Ministère de la Transition écologique (https://vigieau.gouv.fr). Licence Ouverte 2.0",
            "ours",
        ),
        Credit(
            "gaspar_catnat",
            "GASPAR CatNat (TACCT extraction on data.gouv.fr) and API Découpage Administratif (DINUM/Etalab)",
            "Source: Arrêtés de catastrophe naturelle par commune (TACCT / GASPAR extraction, 2026-07-20, Licence Ouverte 2.0); "
            "commune centroids from API Découpage Administratif (DINUM/Etalab, Licence Ouverte 2.0)",
            "ours",
        ),
        Credit(
            "slf_avalanche",
            "WSL Institute for Snow and Avalanche Research SLF",
            "Source: WSL Institute for Snow and Avalanche Research SLF (https://www.slf.ch). CC BY 4.0",
            "ours",
        ),
        Credit(
            "euregio_avalanche",
            "Avalanche.report (Tirol, Südtirol, Trentino)",
            "Source: Avalanche.report — Avalanche Warning Service Tirol, Südtirol, Trentino (https://avalanche.report). CC BY 4.0 DEED",
            "ours",
        ),
        Credit(
            "meteofrance_bra",
            "Météo-France (DPBRA), avalanche-risk bulletins",
            "Source: Météo-France, bulletin d'estimation du risque d'avalanche (BRA, https://meteofrance.com/meteo-montagne). "
            "Licence Ouverte 2.0 (Etalab)",
            "ours",
        ),
        # The travel advice of the foreign ministries (SOURCES.md, "Travel advice").
        Credit(
            "travel_gb",
            "Foreign, Commonwealth & Development Office (GOV.UK)",
            "Source: FCDO foreign travel advice (https://www.gov.uk/foreign-travel-advice). "
            "Contains public sector information licensed under the Open Government Licence v3.0.",
            "ours",
        ),
        Credit(
            "travel_us",
            "U.S. Department of State, Bureau of Consular Affairs",
            "Source: Bureau of Consular Affairs, U.S. Department of State (https://travel.state.gov)",
            "suggested",
        ),
        Credit(
            "travel_ca",
            "Global Affairs Canada",
            "Source: Global Affairs Canada, travel advice and advisories (https://travel.gc.ca). "
            "Contains information licensed under the Open Government Licence – Canada.",
            "ours",
        ),
        Credit(
            "travel_au",
            "Smartraveller (Australian Government, DFAT)",
            "Source: the Department of Foreign Affairs and Trade's Smartraveller website - www.smartraveller.gov.au",
            "suggested",
        ),
        Credit(
            "travel_nl",
            "Ministerie van Buitenlandse Zaken (NederlandWereldwijd)",
            "Source: Ministerie van Buitenlandse Zaken, NederlandWereldwijd (https://www.nederlandwereldwijd.nl/reisadvies), CC0 1.0",
            "ours",
        ),
        Credit(
            "health_ecdc",
            "European Centre for Disease Prevention and Control (ECDC)",
            "Source: European Centre for Disease Prevention and Control (ECDC), https://www.ecdc.europa.eu. Licensed under CC BY 4.0 "
            "(https://creativecommons.org/licenses/by/4.0/); the countries an item names are matched by Vigie",
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

# What NASA asks of whoever provides FIRMS data to a third party, besides the credit above: its disclaimer. Word for word, the sentence that the FIRMS
# page puts under "Disclaimer", then the second paragraph of the full LANCE disclaimer (read on 2026-10-20: https://www.earthdata.nasa.gov/data/tools/firms
# and https://www.earthdata.nasa.gov/data/projects/lance/disclaimer). The full disclaimer goes on with the limits of liability and ends with the remark
# that the designations imply no opinion on any frontier: not reproduced. Every answer that carries fire detections has it.
FIRMS_DISCLAIMER = (
    "The information presented through LANCE, GIBS, Worldview, and FIRMS are provided “as is” and users bear all responsibility and "
    "liability for their use of data. Due to the spatial resolution and other characteristics of these data, their use for tactical "
    "decision-making or informing about conditions at a local scale are not advised."
)

# The ``source`` / ``sources`` names a hazard alert carries -> the credit of that source.
HAZARD_SOURCE_CREDITS: dict[str, str] = {
    "USGS": "usgs",
    "EMSC": "emsc",
    "GDACS": "gdacs",
    "NOAA_NWS": "noaa_nws",
    "USGS_VOLCANO": "usgs_volcano",
    "NHC": "noaa_nhc",
    "PTWC": "noaa_tsunami",
    "NTWC": "noaa_tsunami",
}

# The ``sources`` of a place named in ``near_places`` -> the credit of that source. A volcano carries the names of the hazard
# sources that report it (``HAZARD_SOURCE_CREDITS``); a fault carries ``GEM``; an active fire carries its FIRMS product(s);
# a point the user watches carries none.
PLACE_SOURCE_CREDITS: dict[str, str] = {
    "GEM": "gem_faults",
    "VIIRS_NOAA20_NRT": "nasa_firms",
    "VIIRS_NOAA21_NRT": "nasa_firms",
    "NASA_FIRMS": "nasa_firms",
}

# Blocks of data and the credits they carry (the order is the order of the text).
OPEN_METEO_WEATHER = ("open_meteo",)
MET_NORWAY_WEATHER = ("met_norway",)
AIR_QUALITY = ("open_meteo", "cams")
FLOOD = ("open_meteo", "glofas")
MARINE = ("open_meteo", "dwd_icon_wave")
SPACE_WEATHER = ("noaa_swpc",)
ECLIPSES = ("nasa_eclipses",)
RADAR = ("rainviewer",)
FIRES = ("nasa_firms",)
DROUGHT = ("vigieau",)
GASPAR_CATNAT = ("gaspar_catnat",)
PREALERT = ("ecmwf_opendata",)
AVALANCHE_SLF = ("slf_avalanche",)
AVALANCHE_EUREGIO = ("euregio_avalanche",)
AVALANCHE_BRA = ("meteofrance_bra",)
RAIN_PIAF = ("meteofrance_piaf",)
METEOFRANCE_CLIMATE = ("meteofrance_climate",)
TIDES = ("eot20",)
TIDES_CALIBRATED = ("eot20", "refmar_tides")
TIDE_CORRECTION = ("shom_refmar",)
SURF_SPOTS = ("osm_spots", "gip_littoral_plages", "shom_ndf")
SURF = ("open_meteo", "copernicus_mfwam", "osm_spots", "gip_littoral_plages", "shom_ndf", "eot20")
SURF_BUOYS = ("candhis_stations", "osm_spots")
# Added to the credit of marine conditions that carry the temperature of the sea.
MARINE_SEA_TEMPERATURE = ("copernicus_ocean",)


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


def place_credit(sources: Iterable[str]) -> Optional[str]:
    """The credit of the places named in ``near_places``, from their ``sources`` (a name this table does not know adds nothing)."""
    texts: list[Optional[str]] = []
    for source in sources:
        for name in str(source).split("+"):
            key = PLACE_SOURCE_CREDITS.get(name.strip()) or HAZARD_SOURCE_CREDITS.get(name.strip())
            if key is not None:
                texts.append(CREDITS[key].text)
    return merge_credits(*texts)


def has_fire_near_places(hazards: Iterable[Any]) -> bool:
    """Whether any hazard carries NASA FIRMS active fire detections in ``near_places``."""
    return any(
        place.kind == "fire"
        for hazard in hazards
        for place in (getattr(hazard, "near_places", None) or ())
    )


def merge_disclaimers(*texts: Optional[str]) -> Optional[str]:
    """Join distinct non-empty disclaimers with a blank line; ``None`` when there is none."""
    parts: list[str] = []
    for text in texts:
        cleaned = (text or "").strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return "\n\n".join(parts) if parts else None


def fires_disclaimer_for(hazards: Iterable[Any]) -> Optional[str]:
    """``FIRMS_DISCLAIMER`` when any hazard carries NASA FIRMS detections in ``near_places``, else ``None``."""
    return FIRMS_DISCLAIMER if has_fire_near_places(hazards) else None
