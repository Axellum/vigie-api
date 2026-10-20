# Data sources

Every provider is public and needs no API key. The Vigie API is a **personal, non-commercial**
project. This page says **how each source is used**: what is read, how long it is cached and why, how many
calls it costs. The **licence, terms, required credit and published quota** of each source are in
[`SOURCES.md`](../SOURCES.md), the only list of them. Checked on **2026-10-20** by opening the pages
linked below; what could not be checked is marked **not verified**. Terms change: re-check them before
any change of use (sharing the data, commercial use, a public service).

How a source is used: each collector caches its answer for a TTL, wraps the calls in a circuit
breaker (3 failures open it for 60 s) and falls back to its last cached value when allowed.
Simultaneous requests for the same key share one upstream request. Requests carry the
`User-Agent` `vigie-api (<VIGIE_CONTACT>)`; set `VIGIE_CONTACT` to an e-mail address or a URL (see the
README).

**How a TTL is chosen.** It follows the rhythm at which the source really changes, read in its own
documentation (or, where it has none, in the cache headers it sends, read on 2026-10-20). Where
the rhythm could not be verified the earlier TTL is kept and the row says **not verified**. A source
polled for a position is also asked for a grid cell, not for the exact point (see the README,
`VIGIE_GRID_*_DEG`).

## Hazards

| Source | What is read | Cache (TTL) |
| --- | --- | --- |
| **USGS Earthquake Hazards Program**, GeoJSON feed `summary/4.5_day.geojson` ([feed documentation](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)) | Earthquakes M4.5+ of the last 24 h: magnitude, place, time, depth, tsunami flag, PAGER level. | 60 s: the feed page says "Updated every minute", and the file is sent with `Cache-Control: max-age=60`. |
| **EMSC / SeismicPortal**, FDSN event service ([service](https://www.seismicportal.eu/fdsn-wsevent.html)) | Earthquakes M4.5+, 40 latest. | 60 s: update rhythm **not verified** (the service page documents none; the server answers `max-age=15`). |
| **GDACS** (UN / European Commission JRC), `geteventlist/SEARCH` ([API description](https://www.gdacs.org/gdacsapi/swagger/index.html), [terms of use](https://data.gdacs.org/About/termofuse.aspx)) | Earthquakes, tropical cyclones, floods, volcanoes, wildfires; alert level; severity. One request for every type: the events of the last 30 days at the orange or red level that GDACS still marks current (green = low humanitarian impact, not collected). | 180 s: update rhythm **not verified** (no page documents it). |
| **NOAA National Weather Service**, `api.weather.gov/alerts/active` ([disclaimer](https://www.weather.gov/disclaimer), [API documentation](https://www.weather.gov/documentation/services-web-api)) | Severe and extreme alerts: tornado, hurricane, tsunami, flash flood, fire. | 120 s: update rhythm **not verified**. The API sends `Cache-Control: max-age=5` and documents only that its content "expires based upon the information life cycle". |
| **USGS Volcano Hazards Program**, `volcanoApi/elevated` ([endpoint](https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated)) | Volcanoes under an elevated alert: aviation colour code, alert level, notice time, position. No VEI is given. | 600 s: update rhythm **not verified** (the endpoint documents none and sends no cache header). |
| **NOAA Space Weather Prediction Center**, `noaa-planetary-k-index.json` ([data access](https://www.spaceweather.gov/content/data-access)) | Planetary Kp index (3-hourly): latest value, and the G-scale derived from it. | 300 s: the [product page](https://www.spaceweather.gov/products/planetary-k-index) says the chart "updates every minute" and the file is sent with `max-age=60`; the index has 3-hour intervals, so 5 minutes loses nothing. |

## Weather

| Source | What is read | Cache (TTL) |
| --- | --- | --- |
| **Open-Meteo** forecast ([docs](https://open-meteo.com/en/docs)) | **Primary.** Current conditions, 4 x 15-minute precipitation (`minutely_15`, the only source of the frame's short-term rain; Open-Meteo documents that it is native only in Central Europe and North America and interpolated from hourly data elsewhere), daily forecast (weather code, min/max, precipitation sum and probability, wind, UV). | 15 minutes per cell (0.05 degree): the [docs](https://open-meteo.com/en/docs) say "Current conditions are based on 15-minutely weather model data", and list the model updates (GFS, HRRR, AROME every hour, ICON every 3 hours, IFS every 6 hours); they do not say how often `current` changes. The 4 x 15-minute rain of the frame is at most one TTL old. |
| **Open-Meteo Marine** ([docs](https://open-meteo.com/en/docs/marine-weather-api)) | Waves, swell, ocean current at a GPS position. | 1 hour per cell (0.1 degree): the [docs](https://open-meteo.com/en/docs/marine-weather-api) list hourly (some 3-hourly) data from models updated every 6 to 24 hours. |
| **Open-Meteo Flood API**, based on Copernicus GloFAS ([docs](https://open-meteo.com/en/docs/flood-api)) | Daily river discharge, ensemble mean and maximum. | 6 hours per cell (0.1 degree): the [docs](https://open-meteo.com/en/docs/flood-api) list the GloFAS forecast as updated daily. |
| **Open-Meteo Air Quality**, based on Copernicus CAMS ([docs](https://open-meteo.com/en/docs/air-quality-api)) | European AQI, US AQI, PM2.5, PM10, UV, dust. | 1 hour per cell (0.1 degree): the [docs](https://open-meteo.com/en/docs/air-quality-api) list hourly values (Europe, updated every 24 hours) and 3-hourly ones (global, updated every 12 hours). |
| **MET Norway Locationforecast 2.0**, compact ([terms of service](https://api.met.no/doc/TermsOfService), [licence](https://api.met.no/doc/License)) | **Fallback** when Open-Meteo fails: current conditions and hourly series. It has no gust, no apparent temperature, no UV and no 15-minute nowcast: those stay unknown. | Until the `Expires` of each answer (the terms: "don't repeat requests until the time indicated in the `Expires` response header"); 300 s only if an answer has none (**not verified**). |
| **RainViewer**, `weather-maps.json` ([documentation](https://www.rainviewer.com/api/weather-maps-api.html), [changes of 2026](https://rainviewer.com/api/transition-faq.html)) | Latest observed radar frame timestamp and the tile URL template, sent in `/v1/point` only (past frames only; not used by the embedded frame). The API never downloads tiles. Not asked at all when `VIGIE_ENABLE_RAINVIEWER=0` (its free API is for personal use: see [`SOURCES.md`](../SOURCES.md)). | 10 minutes: the [documentation](https://www.rainviewer.com/api/weather-maps-api.html) says the file holds the past 2 hours of radar data with 10-minute intervals. |

## Official weather warnings

The warnings of the national services, relayed by `GET /v1/vigilances` with their words untouched (the model
and its rules: [`architecture.md`](architecture.md)). Licence, credit and what is not verified: the section
"Official weather warnings" of [`SOURCES.md`](../SOURCES.md).

| Source | What is read | Cache (TTL) |
| --- | --- | --- |
| **MeteoAlarm Atom feeds** (EUMETNET), `feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-<country>` ([index of the 39 feeds](https://feeds.meteoalarm.org/)) | One feed per country, asked only for the countries that are needed (below). One entry is one warning for one area: level and type (in the title), times, identifier, message type, event, codes or polygon of the area, link to the CAP document. Entries that are tests or exercises, or have no identifier, are skipped. A feed is from about 1 KB (no warning) to 730 KB (France 146 KB with 34 departments warned, Switzerland 726 KB, on 2026-10-20), without compression. | 180 s. MeteoAlarm publishes **no update rhythm and no cache lifetime** for the feeds (they send `Cache-Control: max-age=0, private, must-revalidate`, no `ETag`, no `Last-Modified`, no `304`): **not verified**. The value comes from the terms of redistribution, which want the delay between the website and the redistributor to be under five minutes on average and ten at most: a 3-minute cache serves a warning at most 3 minutes old. |
| **MeteoAlarm CAP documents**, `feeds.meteoalarm.org/api/v1/warnings/feeds-<country>/<id>` | The words of the issuer: headline, description and instruction in each language (up to 8 for Germany), the name of the issuing service, the web page. One document holds all the areas of one message. | 6 hours: a document is never modified (an update is a new document, with a new address), so this only bounds the memory. A document the server does not have (HTTP 404: every Italian one on 2026-10-20) is remembered 15 minutes, then asked again. |
| **NOAA National Weather Service**, `api.weather.gov/alerts/active?status=actual&severity=Extreme,Severe` | **The answer of the hazards**, read a second way: the same alerts as warnings (CAP fields, UGC and SAME codes, polygon when there is one, the forecast office's headline, description and instruction). | 120 s, as the hazards. **No request of its own** (see below). |
| **DWD** (Deutscher Wetterdienst), `opendata.dwd.de/weather/alerts/cap/DISTRICT_DWD_STAT/Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_DISTRICT_MUL.zip` ([open data help](https://www.dwd.de/DE/leistungen/opendata/hilfe.html): the CAP DWD profile 2.1.14, the product overview, the implementation notes) | One ZIP for all of Germany: the **status** (every warning in force at the last production) **by district** (the polygon of an area is the district, with its holes cut out), in the **multilingual** variant (one CAP document per warning, one `info` per language: German, English, French, Spanish, Arabic, Russian, Turkish, Polish). `LATEST` is a name that always points to the newest production. 8 to 16 KB on 2026-10-20 (4 documents, 7 areas), 80 KB at most over the three days of history read. A cancelled warning is no longer listed: cancellations and all-clears are only in the `DIFFERENCE` products, which are not read. Chosen over the `COMMUNEUNION` product (finer, smaller) because that one has no polygon for the sea areas. | 120 s. The DWD produces the status up to once a minute, and at least every ten minutes even if nothing changed (implementation notes); about every 10 minutes was observed on 2026-10-20. It sends no `Cache-Control` but `ETag` and `Last-Modified`, and answers `304` to `If-None-Match` and to `If-Modified-Since` (checked): a refresh that finds nothing new is a few hundred bytes. The delay for a redistributor is not published: **not verified**. |
| **ECCC** (Environment and Climate Change Canada), MSC GeoMet OGC API, `api.weather.gc.ca/collections/weather-alerts/items` ([docs](https://eccc-msc.github.io/open-data/msc-geomet/readme_en/)) | One request lists every alert in force in Canada (`limit=1000`; the next pages are asked only if a stormy day needs them, at most ten): colour, name, texts in English and French, times, province, polygon of the forecast area. 156 KB (31 KB with gzip) for 36 alerts on 2026-10-20. The Datamart CAP files are **not** read: see [`SOURCES.md`](../SOURCES.md). | 300 s. GeoMet sends no `Cache-Control`, no `ETag` and no `Last-Modified`, and ECCC documents no update rhythm for the list (the CAP files are posted as the alerts are issued): **not verified**. One request per refresh, a rate far under the usage policy's 86,400 a day. **Reachability, seen once:** from the author's PC on 2026-10-20 (12:50 to 13:20 UTC) about two connections in three to `api.weather.gc.ca` timed out (21 seconds, the system's limit) while the others were answered in 0.6 second with the whole list, so the answer is fast and the path is what is unreliable. The collector's 6-second timeout is kept: a failed look serves the last list, marked stale, and the next scheduler cycle asks again. It may not happen from another network. |
| **MetService** (New Zealand), `alerts.metservice.com/cap/rss` ([the system](https://about.metservice.com/about-severe-weather-warnings)) | The RSS feed, then the CAP document each item links to (each fetched once, forgotten when it leaves the feed; only addresses under `alerts.metservice.com` are fetched). The format is documented by the issuer ([user guide v1.2](https://www.metservice.com/files/documents/cap/MetService-CAP-Feed-User-Guide-v1.2.pdf)) but **the feed was empty on 2026-10-20: no real item has been seen** (see [`SOURCES.md`](../SOURCES.md), which also says where the licence stands). | 300 s: the feed sends `Cache-Control: max-age=300, must-revalidate` and a `Last-Modified` that answers `If-Modified-Since` with a `304` (checked). |
| **Vigicrues** (SCHAPI), `www.vigicrues.gouv.fr/services/InfoVigiCru.geojson` ([services v1.1](https://www.vigicrues.gouv.fr/services/v1.1)) | One request for all of France: 337 sections of rivers, a line each, with the colour (1 to 4) of the vigilance. Only the sections above green are returned (19 on 2026-10-20: 17 yellow, 2 orange). 2.2 MB, not compressed. No lighter endpoint carries the level: the others of the documentation give the referential, forecasts and observations. | A look at the publication image every 15 minutes (5 while a section is orange or red); the flow itself only when the image changed, at the latest after 12 hours. The [dataset](https://www.data.gouv.fr/datasets/troncons-de-cours-deau-vigicrues-simplifies-avec-niveau-de-vigilance-crues-2) says the flow follows the vigilance bulletins, twice a day (10 a.m. and 4 p.m.) outside floods and more often during them (the rhythm of the extra bulletins is **not verified**). The server sends `Cache-Control: max-age=120` but no `ETag` and no `Last-Modified`: **no conditional request** on the flow; measured on 2026-10-20 it is not compressed (`gzip, deflate, br` are ignored), a `Range` request is answered with the whole body and no filter is taken. Every read costs the 2.2 MB, so the reads are cut to the publications, using the validators of the national map image the same publication regenerates (`/ftp/img_france.png`, a `HEAD` of about 330 bytes; see [`SOURCES.md`](../SOURCES.md) for what is and is not verified). |

### Which feeds are asked

A feed is asked **only for a country that is needed**; 39 MeteoAlarm feeds exist, one request of a client never asks for them all. **One source answers for a country** (the reasons: [ADR 0003](decisions/0003-national-source-preferred-rivers-are-lines.md)):

| Country | Asked | Never asked |
| --- | --- | --- |
| Germany | DWD | MeteoAlarm's German feed, unless the DWD gave no answer (then it stands in, and the answer says so) |
| Canada | ECCC | |
| New Zealand | MetService | |
| France | MeteoAlarm, and Vigicrues | |
| the other 38 MeteoAlarm countries | MeteoAlarm | |
| the United States | nothing of its own (the NWS answer of the hazards) | |

- The country of a position is found **offline** (borders bundled in `app/data/`, see [`SOURCES.md`](../SOURCES.md)), without asking any service.
- A request for a position asks the feed of its country (two near a border); a request by `country=` asks that one.
- The scheduler (`VIGIE_SCHEDULER=1`) keeps fresh, every 90 s cycle, the feeds of: the countries of the watched zones, of the positions that clients follow on the WebSocket, and of the 8 countries asked for in the last hour. A feed older than its 180 s is asked again at the next cycle, that is every 270 s: **320 requests a day** per feed.
- A CAP document is asked only for a warning that an answer returns, once, and at most 40 new ones per request (the strongest warnings first); the rest come with the next request. The scheduler asks for none until a warning changes for a followed position.

Measured by `tests/test_vigilance_sobriety.py` (simulated providers serving the feeds recorded on 2026-10-20; run it with `pytest -s`):

| Scenario | MeteoAlarm requests |
| --- | ---: |
| A day of the scheduler on the 5 default zones (Paris: France; Reykjavik: Iceland; Tokyo and Pointe-à-Pitre: no source; San Francisco: the NWS answer of the hazards) | **640** feed requests (320 France + 320 Iceland), 0 CAP documents |
| 500 clients on 50 positions of four countries asking every 30 minutes for 6 hours (6,000 answers) | **320** feed requests (80 for each of four countries in 6 hours, i.e. 320 a day each), 4 CAP documents |
| The simulated day of 1,000 clients of `tests/test_call_sobriety.py`, with the warnings on | **640** feed requests, and NOAA NWS still **705** (the same as without the warnings: the second reading of the answer costs no request) |

The national sources, measured the same way by `tests/test_national_sobriety.py` (the answers recorded on 2026-10-20, one simulated day, the scheduler's 90 s cycle):

| Scenario | Requests a day |
| --- | ---: |
| The 5 default zones: DWD, ECCC, MetService | **0** each (no zone in those countries) |
| The 5 default zones: Vigicrues (Paris is in France), one simulated day with the two publications of 07:55 and 13:55 UTC | **4 reads of the flow and 240 looks at the image** while a section is orange (a look every 6 minutes: 5 minutes of cache looked at every 90 s), **4 and 88** on a calm day (a look every 16.5 minutes); the reads are the start, the two publications and one safety read after 12 hours. The flow weighs 2,245,691 bytes, a look 330: **9.1 MB** and **9.0 MB** a day. Once started a day is **3 reads and 6.8 MB**. Before: 138 reads (310 MB) and 46 (103 MB), the flow being asked every 10.5 or 31.5 minutes |
| A zone, a followed point or a client in **Germany** | **480** to the DWD, 479 of them `304` without a body (the first answer is 16 KB), and **0** to MeteoAlarm for Germany (320 a day before this work) |
| A zone in **Canada** | **240** to GeoMet, 31 KB each with gzip (about 7.5 MB a day) |
| A zone in **New Zealand** | **240** to MetService, 239 of them `304` |
| The same, with 1,000 reads of the warnings spread over the day (one every 86 s), or 10,000 (one every 9 s) | DWD 520 / 667, ECCC 249 / 278, MetService 247 / 278, Vigicrues 249 / 282 (looks at an image of 330 bytes, the flow being read on its publications only): **never more than one request per cache lifetime** (720, 288, 288 and 288 a day), whatever the number of readers |

For the 5 default zones the new sources add the Vigicrues flow only; a client in Germany costs the DWD instead of the German MeteoAlarm feed.

**A river section is a line.** A position is matched to a section when it lies within `river_radius_km` of the line of the flow, and the distance is returned (`distance_km`). 5 km by default, 50 at most. The reasons for 5 km: the lines of the flow are simplified, the flood plain of a large river is several kilometres wide, and the vigilance is a heads-up, not a flood map of the position; the client sees the distance and can ask for less. The distance is worked out on a plane where longitudes are scaled by the cosine of the latitude (right at the scale of a few tens of kilometres).

**The duplicates.** Two rules keep the same event from being said twice:

1. *A national source is preferred to MeteoAlarm's copy of it* (Germany: DWD). MeteoAlarm's German feed is a copy of the DWD's warnings, in `EMMA_ID` codes with no polygon; the DWD gives the same warnings with their polygons and eight languages. MeteoAlarm is used for Germany only if the DWD gives no answer, and then flagged as a stand-in (`coverage[].note`); its copies are in the answer, not in the pushes.
2. *France: a MeteoAlarm "flooding" warning is left out when a Vigicrues section of the same answer crosses its département at the same level or higher.* MeteoAlarm's "Flooding" (type 12) is Météo-France's "inondation" colour per département, which the data of 2026-10-20 show to follow the Vigicrues colours: every département still warned had a section at the same level or higher. If no section is in the answer (none within the radius, or Vigicrues does not answer), or only a lower one, the département's warning stays. "Rain-flood" (type 13) is another vigilance and always stays. That MeteoAlarm derives its flooding colour from Vigicrues is **deduced** from the data, not read in a current document.

### Levels, phenomena and status: the tables

The API derives three fields; they are in `derived` on every warning and the source's own words stay beside them. A word that is in no table gives `null`: it is never guessed. `tools/check_live_feeds.py` fails (weekly) when a feed of France carries a word that is not in a table.

**Level** (`level`, from `level_original`).

| MeteoAlarm word (CAP profile v2.0, `awareness_level`) | NWS word (CAP `severity`) | `level` |
| --- | --- | --- |
| Green (1, Minor): not published in the feeds, but can come in an update that ends a warning | Minor | `GREEN` |
| Yellow (2, Moderate) | Moderate | `YELLOW` |
| Orange (3, Severe) | Severe | `ORANGE` |
| Red (4, Extreme) | Extreme | `RED` |
| any other word | Unknown or any other word | `null` |

The national services, for the same field:

| Source and word | `level` |
| --- | --- |
| **DWD**, CAP `severity` (profile 2.1.14, 2.2.2.6): Minor (a *Wetterwarnung*) | `YELLOW` (**not** `GREEN` as for the NWS: a Minor warning of the DWD is a yellow warning) |
| DWD: Moderate (*markante Wetterwarnung*) | `ORANGE` |
| DWD: Severe (*Unwetterwarnung*) | `RED` |
| DWD: Extreme (*extreme Unwetterwarnung*, violet in the DWD's scale) | `RED` (our scale stops at red; the word `Extreme` stays in `level_original`) |
| **ECCC**, `risk_colour_en`: yellow, orange, red | `YELLOW`, `ORANGE`, `RED` (no other colour is in the table: a statement has none, and gets `null`; only yellow was seen on 2026-10-20) |
| **MetService**, the `ColourCode` parameter (always present, guide 3.2.14.2): Yellow / Orange / Red | `YELLOW` (the Watches and the Severe Thunderstorm Watch), `ORANGE` (the Orange Warnings and the Road Snowfall Warning), `RED` (the Red Warnings and the Severe Thunderstorm Warning). Any other value, or a colour the headline contradicts, gives `null` |
| MetService, no `ColourCode`: the words of the headline (the guide lists the twelve) | the colour word ("Heavy Rain Warning - Orange") if it is the only one; "Severe Thunderstorm Warning" `RED`; "Road Snowfall Warning" `ORANGE`; a "Watch" `YELLOW`; anything else `null`. The CAP `severity` is never used: a Severe Thunderstorm Watch is `Moderate` and yellow |
| **Vigicrues**, `NivInfViCr`: 1 / 2 / 3 / 4 | green (never returned: a green section is no warning) / `YELLOW` / `ORANGE` / `RED` (the colours of the official local bulletins, compared on 2026-10-20) |

**Phenomenon** (`phenomenon`, from `phenomenon_original`).

| `phenomenon` | MeteoAlarm type (CAP profile v2.0, `awareness_type`) | NWS event (the first rule whose words are all in the event name, in this order) |
| --- | --- | --- |
| `wind` | Wind (1) | tornado, hurricane, typhoon, tropical storm, high wind, extreme wind, wind, gale, storm warning |
| `rain` | Rain (10) | rain |
| `thunderstorm` | Thunderstorm (3) | thunderstorm |
| `snow-ice` | Snow-ice (2) | blizzard, winter storm, winter weather, ice storm, snow, freezing rain, sleet |
| `heat` | High-temperature (5) | heat |
| `cold` | Low-temperature (6) | wind chill, extreme cold, freeze, frost |
| `flood` | Flooding (12), Rain-flood (13) | flood (a "flash flood" is a flood) |
| `avalanche` | Avalanches (9) | avalanche |
| `coastal` | CoastalEvent (7), Marine-Hazard (14) | storm surge, coastal flood, lakeshore flood, tsunami, high surf, rip current, beach hazards |
| `fire` | Forest-fire (8) | red flag, fire weather, fire |
| `fog` | Fog (4) | freezing fog, fog |
| `other` | Drought (15) | an event no rule covers (a dust storm, a small craft advisory...) |
| `null` | a type that is not in the profile | no event name |

A "rain-flood" (pluvial flooding) is grouped with the floods and a "marine hazard" with the coastal events: these two choices are ours, the profile does not group the types. The MeteoAlarm word is the one of the entry's title; the CAP elements give the same level and type as `parameter`s.

The national services, for the same field (a word no rule covers is `other`, never a guess; `null` when the source gives no name or number at all):

| `phenomenon` | **DWD**, `eventCode` `II` (profile 2.1.14, section 3.1; the profile says the encoding may change) | **ECCC**, `alert_name_en` (the first rule whose words are all in the name, in this order) | **MetService** (the `<event>` word; the words of the headline only for a word the guide does not list) | **Vigicrues** |
| --- | --- | --- | --- | --- |
| `wind` | 11-16 (coast, sea), 51-58 (gusts, strong wind, storm) | tornado, hurricane, tropical storm, wind | `wind` (also a tornado, in the headline) | |
| `thunderstorm` | 31, 33, 34, 36, 38, 40-42, 44-46, 48, 49, 90-93, 95, 96 (thunderstorms, some with gusts, rain or hail) | thunderstorm | `thunderstorm` (the Severe Thunderstorm Watch and Warning) | |
| `rain` | 61-66 | rainfall, rain | `rain` | |
| `snow-ice` | 70-76, 84-87 (snow, drifts, black ice) | blizzard, blowing snow, snow squall, snowfall, winter storm, freezing rain, freezing drizzle, flash freeze, snow | `snow` (also the Road Snowfall Warning) | |
| `cold` | 22, 82, 83 (frost) | arctic outflow, wind chill, extreme cold, frost | | |
| `heat` | 247, 248 | heat | | |
| `fog` | 59 | fog | | |
| `flood` | | flood | | every section |
| `coastal` | | storm surge, tsunami | | |
| `other` | 79, 88, 89, 246 | any other name | `localStorm` (a local severe event that is not a thunderstorm), any other word | |
| `null` | a number not listed | no name | no word | |

The DWD numbers are the ones of the profile's event table; the rules of ECCC come from the names seen on 2026-10-20 (five kinds in force) and are **not verified** against a complete list of names (ECCC publishes none that was found). MetService's come from its own user guide (the five `<event>` words, the twelve headlines, the `ColourCode` table: sections 3.2.2, 3.2.10 and 3.2.14.2), and **no real item of the feed has been read against them**.

**Status** (`status`, from `message_type`, the CAP `msgType`; also the DWD and MetService): `Alert` is `new`, `Update` is `update`, `Cancel` is `cancel` (the MetService feed holds no cancelled alert, says its guide: the mapping is there in case one is ever served). ECCC (GeoMet has no `msgType`): `status_en` `issued` is `new` and `continued` is `update` (any other word gives `null`); `ended`, the third word seen on 2026-10-20, is an alert that is over but still listed for about an hour: it is skipped, not returned. Vigicrues has none. A cancelled warning, and one whose end (the later of `ends_utc` and `expires_utc`) is past, is not returned: the feeds keep listing expired warnings (the four orange floods of the French feed of 2026-10-20 had ended 12 hours before it was read).

## Call volume

A refresh happens only when someone asks after the TTL, so the calls per day of a cell are
`86400 / TTL` at most, whatever the number of clients. With the TTLs above, per cell and per day:
at most 96 "now" requests of the forecast (15 minutes) and 24 daily ones (1 hour), 24 air quality
calls (1 hour), 24 marine calls (1 hour) and 4 river discharge calls (6 hours). A GPS position adds
those calls (marine and river discharge when requested); two positions in the same cell share them.
The hazard feeds add one call per feed per TTL (at most 1,440 a day for USGS and EMSC, which have the
shortest TTL; the scheduler alone refreshes them every 90 s, 960 a day). The official warnings add one call per MeteoAlarm feed per 180 s, only for the countries that are needed
(320 a day per feed when the scheduler keeps it fresh; none for the NWS, whose answer is the hazards' own), and the national services one call per cache lifetime of their own for the countries that are needed (DWD 480 a day, mostly `304`; ECCC and MetService 240; Vigicrues 88 looks at an image and 4 reads of the flow on a calm day, 240 and 4 in a flood; see above). The README gives the calls
a day measured by a simulation of 1,000 clients on 50 points, before and after the TTLs, grids and
budget.

### Weight of the Open-Meteo calls

Open-Meteo counts "API calls", not HTTP requests: one that asks for more than 10 variables or more
than 2 weeks of data counts as several, with fractional counts
([pricing](https://open-meteo.com/en/pricing): "a request for 2 weeks of data with 15 weather variables
will be calculated as 1.5 API calls"; [blog](https://openmeteo.substack.com/p/weather-data-for-multiple-locations):
weight = locations x days / 14 x variables / 10, the variables being the sum of the hourly, daily and
current ones). The API applies `max(1, variables / 10) x max(1, days / 14)` per location
(`app/collectors/weights.py`; the 15-minute variables are counted too, which can only over-estimate).
The limit of 10,000 calls a day is a limit on this weight, not on the number of requests.

| Request | Variables | Days | Weight |
| --- | --- | --- |
| Forecast, as one request (before) | 10 current + 1 15-minute + 7 daily = 18 | 10 | **1.8** |
| Forecast, "now" part (after) | 10 current + 1 15-minute = 11 | default | **1.1** |
| Forecast, "daily" part (after) | 7 daily | 10 | **1.0** |
| Air quality | 6 current | default | 1.0 |
| River discharge | 3 daily | 7 | 1.0 |
| Marine | 8 current | default | 1.0 |

The forecast is the only request over 10 variables. Every one of its 18 variables is used (the
frame or the JSON routes), so none was dropped: bringing one request to weight 1.0 would have cost 8
fields. Splitting it does not lower the weight of one refresh of both parts (fractional counts: 1.1 + 1.0 =
2.1 instead of 1.8), but the two parts are refreshed at their own pace: the current
conditions and the 15-minute rain every 15 minutes (11 variables), the daily forecast every hour
(7 variables, 10 days). Per cell and per day: 96 x 1.8 = 173 before, 96 x 1.1 + 24 x 1.0 = 130
after (-25%) at the same freshness of the current conditions.

### Call budget

Every request the API sends is counted (calls, and weight for Open-Meteo) over a sliding window of 24
hours and reported by `GET /v1/providers` (`calls_24h`, `weight_24h`, `quota`). A provider that
**publishes a quota** also has a budget; the quotas are declared in one place,
`app/collectors/budget.py` (`QUOTAS`), and listed with their source page in [`SOURCES.md`](../SOURCES.md):
Open-Meteo (one quota for its four APIs: forecast, air quality, river discharge, marine), MET Norway,
RainViewer and ECCC (a usage-policy line of 86,400 requests a day: a threshold to tell ECCC about, kept as a limit all the same) have one. NOAA NWS has a limit that "is not public information", and USGS, EMSC, GDACS,
USGS Volcano, NOAA SWPC, MeteoAlarm, the DWD, MetService and Vigicrues publish none: no budget for them (**not verified**), their calls are only counted.

The budget lets through 90 % of each limit (`VIGIE_BUDGET_FRACTION`): a margin for another program on
the same IP address and for the difference between this count and the provider's. A request that
would exceed it is **not sent**: the collector serves its cache, even expired, and the responses that
carry that data say so (`weather_source` ends with `(stale cache)` and `weather_fallback_active` is
true; `stale: true` on `air_quality`, `flood_risk` and `marine`). With nothing cached the data is
unknown (or, for the weather, the MET Norway fallback is used). The budget frees itself as the window
slides, and a refused request is not a failure of the provider: the circuit breaker does not see it.
The monthly limit of Open-Meteo (300,000 calls) is the daily one over 30 days and is not tracked
separately. A redirect followed by the HTTP client is not counted.

## Credits

Every block of data carries the credit its source asks for (`attribution` in the JSON responses); the
texts are declared in `app/attribution.py` and quoted, with their wording and what is not verified, in
[`SOURCES.md`](../SOURCES.md). The Vigie API does not render a screen itself, except the supervision
dashboard (`/dashboard`), which ends with the credits of every source and the notice that GDACS alerts are
automatic and do not replace official warnings. The compact frame has no credit field (its contract is
fixed): crediting the sources on the displays is the work of the display repository; whether it does so
was not checked.
