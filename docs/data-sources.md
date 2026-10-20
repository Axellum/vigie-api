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

### Which feeds are asked

A MeteoAlarm feed is asked **only for a country that is needed**; 39 feeds exist, one request of a client never asks for them all.

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

**Status** (`status`, from `message_type`, the CAP `msgType`): `Alert` is `new`, `Update` is `update`, `Cancel` is `cancel`. A cancelled warning, and one whose end (the later of `ends_utc` and `expires_utc`) is past, is not returned: the feeds keep listing expired warnings (the four orange floods of the French feed of 2026-10-20 had ended 12 hours before it was read).

## Call volume

A refresh happens only when someone asks after the TTL, so the calls per day of a cell are
`86400 / TTL` at most, whatever the number of clients. With the TTLs above, per cell and per day:
at most 96 "now" requests of the forecast (15 minutes) and 24 daily ones (1 hour), 24 air quality
calls (1 hour), 24 marine calls (1 hour) and 4 river discharge calls (6 hours). A GPS position adds
those calls (marine and river discharge when requested); two positions in the same cell share them.
The hazard feeds add one call per feed per TTL (at most 1,440 a day for USGS and EMSC, which have the
shortest TTL; the scheduler alone refreshes them every 90 s, 960 a day). The official warnings add one call per MeteoAlarm feed per 180 s, only for the countries that are needed
(320 a day per feed when the scheduler keeps it fresh; none for the NWS, whose answer is the hazards' own). The README gives the calls
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
Open-Meteo (one quota for its four APIs: forecast, air quality, river discharge, marine), MET Norway and
RainViewer have one. NOAA NWS has a limit that "is not public information", and USGS, EMSC, GDACS,
USGS Volcano, NOAA SWPC and MeteoAlarm publish none: no budget for them (**not verified**), their calls are only counted.

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
