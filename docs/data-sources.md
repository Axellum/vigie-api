# Data sources

Every provider is public and needs no API key. The Vigie API is a **personal, non-commercial**
project. Checked on **2026-10-20** by opening the pages linked below; what could not be checked is
marked **not verified**. Re-check the terms before any change of use (sharing the data,
commercial use, a public service): terms change.

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

| Source | What is read | Cache (TTL) | Terms |
| --- | --- | --- | --- |
| **USGS Earthquake Hazards Program**, GeoJSON feed `summary/4.5_day.geojson` ([feed documentation](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)) | Earthquakes M4.5+ of the last 24 h: magnitude, place, time, depth, tsunami flag, PAGER level. | 60 s: the feed page says "Updated every minute", and the file is sent with `Cache-Control: max-age=60`. | The feed page gives no rate limit. Licence and credit: **not verified** (the usgs.gov copyright page refused automated access on 2026-10-20). |
| **EMSC / SeismicPortal**, FDSN event service ([service](https://www.seismicportal.eu/fdsn-wsevent.html)) | Earthquakes M4.5+, 40 latest. | 60 s: update rhythm **not verified** (the service page documents none; the server answers `max-age=15`). | [Terms](https://www.seismicportal.eu/terms.html): CC BY 4.0 with attribution ("EMSC-CSEM SeismicPortal"). Mirroring and bulk copying need written permission. No rate limit published. |
| **GDACS** (UN / European Commission JRC), `geteventlist/MAP` ([terms of use](https://data.gdacs.org/About/termofuse.aspx)) | Earthquakes, tropical cyclones, floods, volcanoes, wildfires, tsunamis; alert level; severity. | 180 s: update rhythm **not verified** (no page documents it). | The terms page states no licence, no attribution rule and no rate limit; it only says the data is provided "as is", must not replace official information, and may have downtime. Licence and attribution: **not verified**. |
| **NOAA National Weather Service**, `api.weather.gov/alerts/active` ([disclaimer](https://www.weather.gov/disclaimer), [API documentation](https://www.weather.gov/documentation/services-web-api)) | Severe and extreme alerts: tornado, hurricane, tsunami, flash flood, fire. | 120 s: update rhythm **not verified**. The API sends `Cache-Control: max-age=5` and documents only that its content "expires based upon the information life cycle". | Public domain unless noted; no credit required. The API requires an identifying `User-Agent` with a contact (website or e-mail); the rate limit is not published. |
| **USGS Volcano Hazards Program**, `volcanoApi/elevated` ([endpoint](https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated)) | Volcanoes under an elevated alert: aviation colour code, alert level, notice time, position. No VEI is given. | 600 s: update rhythm **not verified** (the endpoint documents none and sends no cache header). | No terms page for this API could be opened. Licence and credit: **not verified**. |
| **NOAA Space Weather Prediction Center**, `noaa-planetary-k-index.json` ([data access](https://www.spaceweather.gov/content/data-access)) | Planetary Kp index (3-hourly): latest value, and the G-scale derived from it. | 300 s: the [product page](https://www.spaceweather.gov/products/planetary-k-index) says the chart "updates every minute" and the file is sent with `max-age=60`; the index has 3-hour intervals, so 5 minutes loses nothing. | The data access page states no licence, credit or limit. Licence: **not verified**. (The NWS public-domain disclaimer applies to weather.gov, not necessarily to this server.) |

## Weather

| Source | What is read | Cache (TTL) | Terms |
| --- | --- | --- | --- |
| **Open-Meteo** forecast ([docs](https://open-meteo.com/en/docs)) | **Primary.** Current conditions, 4 x 15-minute precipitation (`minutely_15`, the only source of the frame's short-term rain; Open-Meteo documents that it is native only in Central Europe and North America and interpolated from hourly data elsewhere), daily forecast (weather code, min/max, precipitation sum and probability, wind, UV). | 15 minutes per cell (0.05 degree): the [docs](https://open-meteo.com/en/docs) say "Current conditions are based on 15-minutely weather model data", and list the model updates (GFS, HRRR, AROME every hour, ICON every 3 hours, IFS every 6 hours); they do not say how often `current` changes. The 4 x 15-minute rain of the frame is at most one TTL old. | **[Terms](https://open-meteo.com/en/terms): the free API may only be used for non-commercial purposes**, under 10,000 calls per day, 5,000 per hour and 600 per minute; data under CC BY 4.0. The [licence page](https://open-meteo.com/en/licence) asks for a link to Open-Meteo next to any location where its data is displayed. A commercial plan needs an API key. |
| **Open-Meteo Marine** ([docs](https://open-meteo.com/en/docs/marine-weather-api)) | Waves, swell, ocean current at a GPS position. | 1 hour per cell (0.1 degree): the [docs](https://open-meteo.com/en/docs/marine-weather-api) list hourly (some 3-hourly) data from models updated every 6 to 24 hours. | Same terms. The Marine API also asks to credit the DWD. |
| **Open-Meteo Flood API**, based on Copernicus GloFAS ([docs](https://open-meteo.com/en/docs/flood-api)) | Daily river discharge, ensemble mean and maximum. | 6 hours per cell (0.1 degree): the [docs](https://open-meteo.com/en/docs/flood-api) list the GloFAS forecast as updated daily. | Same terms. The flood level the API computes is a **heuristic** of its own (ratio of maximum to mean forecast discharge), not an official warning. |
| **Open-Meteo Air Quality**, based on Copernicus CAMS ([docs](https://open-meteo.com/en/docs/air-quality-api)) | European AQI, US AQI, PM2.5, PM10, UV, dust. | 1 hour per cell (0.1 degree): the [docs](https://open-meteo.com/en/docs/air-quality-api) list hourly values (Europe, updated every 24 hours) and 3-hourly ones (global, updated every 12 hours). | Same terms. The page requires an attribution to the CAMS ENSEMBLE data provider (Copernicus Atmosphere Monitoring Service) and to Open-Meteo. |
| **MET Norway Locationforecast 2.0**, compact ([terms of service](https://api.met.no/doc/TermsOfService), [licence](https://api.met.no/doc/License)) | **Fallback** when Open-Meteo fails: current conditions and hourly series. It has no gust, no apparent temperature, no UV and no 15-minute nowcast: those stay unknown. | Until the `Expires` of each answer (the terms: "don't repeat requests until the time indicated in the `Expires` response header"); 300 s only if an answer has none (**not verified**). | NLOD 2.0 and CC BY 4.0, credit "MET Norway". Requires an identifying `User-Agent` with a contact (a missing or fake one may be blocked), a cache, `If-Modified-Since` identical to the previous `Last-Modified`, and at most 4 decimals in the coordinates (5 or more get a 403). The API does all of this: a refresh is a conditional request and a `304 Not Modified` keeps the cached value. Above 20 requests per second per application a special agreement is needed. |
| **RainViewer**, `weather-maps.json` ([documentation](https://www.rainviewer.com/api/weather-maps-api.html), [changes of 2026](https://rainviewer.com/api/transition-faq.html)) | Latest observed radar frame timestamp and the tile URL template, sent in `/v1/point` only (past frames only; not used by the embedded frame). The API never downloads tiles. | 10 minutes: the [documentation](https://www.rainviewer.com/api/weather-maps-api.html) says the file holds the past 2 hours of radar data with 10-minute intervals. | Free API for personal and educational use; credit RainViewer with a link. Since 2026-01-01: no nowcast (future) frames, zoom limited to 7, 100 requests per IP per minute. |

## Call volume

A refresh happens only when someone asks after the TTL, so the calls per day of a cell are
`86400 / TTL` at most, whatever the number of clients. With the TTLs above, per cell and per day:
about 96 forecasts (15 minutes), 24 air quality calls (1 hour), 24 marine calls (1 hour) and 4
river discharge calls (6 hours). The 5 default zones, with the scheduler on, cost about 440
forecasts and 120 air quality calls a day. A GPS position adds a forecast, an air quality call and,
when requested, marine and river discharge calls; two positions in the same cell share them. The
hazard feeds add one call per feed per TTL (about 1,440 a day for USGS and EMSC, which have the
shortest TTL, and fewer with the scheduler's 90 s period).

## Attribution to show

A screen or page that displays this data should credit its sources (links and credits are listed in the
tables above). The Vigie API does not render a screen itself, except the supervision dashboard
(`/dashboard`), whose header lists the sources. Crediting the sources on the displays is the work of the
display repository; whether it does so was not checked.
