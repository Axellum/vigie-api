# Sources, licences and credits

The **code** of this repository is under the [MIT licence](LICENSE). The **data** it relays belongs to
each provider and stays under that provider's own terms: this page lists them, one row per source.

The Vigie API is a personal, **non-commercial** project, made with the help of AI and shared in case it
helps. It is **not an official warning service**: the alerts are relayed from public feeds, can be late,
incomplete or wrong, and must not be the only thing a decision rests on. **GDACS alerts are automatic and
do not replace official warnings.**

How to read this page:

- Checked on **2026-10-20** by opening the pages linked below (some through an automated reader that
  summarizes a page: re-read the wording that matters in a browser before any public launch). What could
  not be checked is marked **not verified**. Terms change: re-check them before any change of use
  (sharing the data, a public instance, anything commercial).
- **Credit in the responses** is the text the API puts in the `attribution` field of the block of data
  from that source. The texts are declared once, in [`app/attribution.py`](app/attribution.py), and a test
  checks that this page quotes them exactly. *Wording*: **provider** = the provider gives this text;
  **suggested** = the provider names a credit line and this is it; **ours** = the provider prescribes no
  wording, the text is ours (it names the source and, where the terms ask for it, says that the alert is
  automatic or that the provider does not endorse this service).
- **Quota** is what the provider publishes. Where one is known the API holds itself to 90 % of it
  (`VIGIE_BUDGET_FRACTION`, declared in `app/collectors/budget.py`); where none is published the calls are
  counted but not limited. How the data is cached and how many calls it costs: [`docs/data-sources.md`](docs/data-sources.md).

## Hazards

| Source (id) | Licence | Terms | Credit in the responses | Quota | Clauses, and what is not verified |
| --- | --- | --- | --- | --- | --- |
| **USGS Earthquake Hazards Program** (`usgs_eq`), GeoJSON feed `summary/4.5_day.geojson` | USGS-authored data are in the U.S. public domain, says the copyrights page; third-party content on usgs.gov pages may not be. | [Copyrights and credits](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits), [feed](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | `Credit: U.S. Geological Survey` (wording: suggested, asked but not imposed) | none published on the feed page | The feed page gives no rate limit and no condition of its own. **Not verified:** that the feed is covered by the general copyrights page (assumed). |
| **USGS Volcano Hazards Program** (`usgs_volcano_gvp`), `volcanoApi/elevated` | Not stated on the API page. The general USGS policy (public domain) presumably applies: **not verified** for this API. | [API page](https://volcanoes.usgs.gov/vsc/api/) | `Credit: U.S. Geological Survey, Volcano Hazards Program` (wording: ours) | none found | The page says the items are freely available but designed to support USGS applications, with no guarantee of continuing support. No terms page for this API could be found. |
| **EMSC / SeismicPortal** (`emsc_seismic`), FDSN event service | CC BY 4.0 | [Terms](https://www.seismicportal.eu/terms.html) | `Credit: EMSC-CSEM SeismicPortal, https://www.seismicportal.eu` (wording: provider) | none published | Mirroring or bulk copying of the database needs written permission. The data is for information only and can be incomplete, late or wrong; EMSC accepts no liability. |
| **GDACS** (UN / European Commission JRC), `geteventlist/SEARCH` (`gdacs_global`) | No reuse licence found on the terms page (**not verified**). | [Terms of use](https://data.gdacs.org/About/termofuse.aspx), [API](https://www.gdacs.org/gdacsapi/swagger/index.html) | `Global Disaster Alert and Coordination System, GDACS (https://www.gdacs.org). GDACS alerts are automatic and do not replace official warnings` (wording: ours; the terms page asks for no credit) | none published | The terms say the data is provided "as is", that GDACS services are not meant to substitute or override any official information, and that the service can be down. **Not verified:** the right to re-publish the data. |
| **NOAA National Weather Service**, `api.weather.gov/alerts/active` (`noaa_nws`) | Public domain unless noted otherwise. | [Disclaimer](https://www.weather.gov/disclaimer), [API documentation](https://www.weather.gov/documentation/services-web-api) | `Source: NOAA National Weather Service (https://api.weather.gov). Not endorsed by NOAA or the NWS` (wording: ours; no credit is required) | exists but "is not public information" (documentation); one request per 30 s at most (reported on 2026-10-20, not re-checked on the documentation page: **not verified**). No budget in the API: the cache is 120 s. | The content must not imply an endorsement or an affiliation with NOAA / NWS, and must not be presented as one's own; the NWS name and visual identifier need permission. The API requires an identifying `User-Agent` with a contact (`VIGIE_CONTACT`). |
| **NOAA Space Weather Prediction Center**, `noaa-planetary-k-index.json` (`noaa_swpc`) | Public domain by the NWS policy: that it extends to SWPC is deduced, **not verified** (the data access page states no licence). | [Data access](https://www.spaceweather.gov/content/data-access) | `Source: NOAA Space Weather Prediction Center (https://www.swpc.noaa.gov). Not endorsed by NOAA` (wording: ours) | none stated | Do not present the service as NOAA's; respect the update intervals. SWPC's own terms: **not verified**. |

## Weather

| Source (id) | Licence | Terms | Credit in the responses | Quota | Clauses, and what is not verified |
| --- | --- | --- | --- | --- | --- |
| **Open-Meteo** forecast (`open_meteo_hd`) | Data under CC BY 4.0 (the code of Open-Meteo is AGPLv3 and can be self-hosted). The weather models behind it have their own licences. | [Terms](https://open-meteo.com/en/terms), [Licence](https://open-meteo.com/en/licence) | `Weather data by Open-Meteo.com (https://open-meteo.com/)` (wording: provider; a link next to every place the data is shown) | 10,000 calls a day, 5,000 an hour, 600 a minute (300,000 a month), counted **by weight**: a request of more than 10 variables or 14 days counts as several. One quota for the four Open-Meteo APIs. | **Free API for non-commercial use only**: personal home automation, public research and teaching are allowed; a site or an app with a subscription or advertising counts as commercial. A commercial use needs a paid plan with an API key (not used here). |
| **Open-Meteo Marine** (`open_meteo_marine`) | Same. | [Docs](https://open-meteo.com/en/docs/marine-weather-api) | The Open-Meteo credit, then `Generated using ICON Wave forecast from the German Weather Service DWD` (wording: provider; the page asks for a clear attribution to DWD and a reference to Open-Meteo) | Same quota. | Same terms. |
| **Open-Meteo Flood** (`open_meteo_flood`), Global Flood Awareness System (GloFAS) | Same. | [Docs](https://open-meteo.com/en/docs/flood-api) | The Open-Meteo credit, then `River discharge: Global Flood Awareness System (GloFAS)` (wording: ours) | Same quota. | The page asks for no credit to GloFAS or Copernicus. **Not verified:** whether the terms of the GloFAS data themselves ask for one. The flood level the API computes is a heuristic of its own (maximum over mean forecast discharge), not an official warning. |
| **Open-Meteo Air Quality** (`open_meteo_aqi`), Copernicus Atmosphere Monitoring Service (CAMS) | Same. | [Docs](https://open-meteo.com/en/docs/air-quality-api) | The Open-Meteo credit, then `Air quality: CAMS ENSEMBLE data provider (Copernicus Atmosphere Monitoring Service)` (wording: provider; the page requires a clear attribution to the CAMS ENSEMBLE data provider) | Same quota. | **Not verified:** the full citation the page suggests (it lists the institutes of the ensemble and points to the CAMS attribution guide) is not reproduced in the credit: read the page before showing this data publicly. |
| **MET Norway Locationforecast 2.0**, compact (`met_norway_fallback`) | NLOD 2.0 and CC BY 4.0 | [Terms of service](https://api.met.no/doc/TermsOfService), [Licence](https://api.met.no/doc/License) | `Data from MET Norway (https://api.met.no/)` (wording: suggested; the licence page asks for credit to the Norwegian Meteorological Institute, MET Norway) | 20 requests a second per application; above that a special agreement is needed. | An identifying `User-Agent` with a contact is required (a missing or fake one may be blocked); the API sends `If-Modified-Since` and respects `Expires` as the terms ask, and at most 4 decimals in the coordinates (5 or more get a 403). The credit must not suggest that MET Norway endorses the service; "Yr" must not appear in its name or logo. |
| **RainViewer**, `weather-maps.json` (`rainviewer_radar`) | No data licence. The API is for **personal and educational use only**. | [API](https://www.rainviewer.com/api.html), [changes of 2026](https://rainviewer.com/api/transition-faq.html) | `Radar data: RainViewer (https://www.rainviewer.com/)` (wording: ours; the page asks to mention RainViewer as the source, with a link) | 100 requests per IP address a minute since 2026-01-01; zoom 7 at most. | **No nowcast (future frames) since 2026-01-01**: the API reads past frames only and the embedded frame does not use RainViewer at all. The provider does not guarantee the data (it can ask for a removal, a change of format or an end of sharing). A public relay of this data probably goes beyond personal use: ask RainViewer for a written agreement, or turn it off with `VIGIE_ENABLE_RAINVIEWER=0` (the Docker image does). |

## Where the credits go

- **JSON responses.** `attribution` on each hazard alert (`/v1/snapshot`, `/v1/point`, `/v1/history`,
  `/v1/geojson/hazards`, `/v1/cap/alerts`, `/v1/stream`), on each zone of the snapshot (the weather, plus
  the air quality when its index was added), and on the `rain_nowcast`, `marine`, `flood_risk`,
  `air_quality` and `space_weather` blocks of `/v1/point`. Where one object mixes several blocks the
  field is named after what it credits: `weather_attribution` in `/v1/point`,
  `global_status.space_weather_attribution` in the snapshot. A block with no data has `attribution: null`.
  A simulated alert or block is credited to no provider.
- **GeoJSON and CAP.** The attribution is a property of each feature (and in `metadata.attribution`), a
  parameter of each CAP alert and the last sentence of its description.
- **Dashboard.** The page ends with the table above (credits and links) and the notice that GDACS
  alerts are automatic and do not replace official warnings.
- **The compact frame** (`/v1/embedded/frame`) has **no** credit field: its contract is fixed
  ([`docs/embedded-frame.md`](docs/embedded-frame.md)). A display that shows its data credits the sources
  itself (the display repository: whether it does so was not checked).

## Before running a public instance

This project was built for one person's home. Each of these points comes from the terms above:

1. Open-Meteo's free API is for non-commercial use, within its quota: a public instance with many clients
   needs a paid plan or a self-hosted Open-Meteo.
2. RainViewer's API is for personal and educational use: keep `VIGIE_ENABLE_RAINVIEWER=0` unless RainViewer agrees in writing.
3. Set `VIGIE_CONTACT` to a real e-mail address or URL: MET Norway and NOAA require an identifying contact.
4. Keep the credits and the "not an official warning" notice wherever the data is shown.
5. Leave the simulator off (`VIGIE_ENABLE_SIMULATION` unset) and do not expose the unauthenticated routes
   (`POST /v1/zones`, `POST /v1/scheduler/trigger`) to the Internet.
