# Data sources

Every provider is public and needs no API key. The Vigie API is a **personal, non-commercial**
project. Checked on **2026-10-20** by opening the pages linked below; what could not be checked is
marked **not verified**. Re-check the terms before any change of use (sharing the data,
commercial use, a public service): terms change.

How a source is used: each collector caches its answer for a TTL, wraps the calls in a circuit
breaker (3 failures open it for 60 s) and falls back to its last cached value when allowed.
Requests carry the `User-Agent` `vigie-api (<VIGIE_CONTACT>)`; set `VIGIE_CONTACT` to an e-mail
address or a URL (see the README).

## Hazards

| Source | What is read | Cache (TTL) | Terms |
| --- | --- | --- | --- |
| **USGS Earthquake Hazards Program**, GeoJSON feed `summary/4.5_day.geojson` ([feed documentation](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)) | Earthquakes M4.5+ of the last 24 h: magnitude, place, time, depth, tsunami flag, PAGER level. | 60 s | The feed page states it is updated every minute and gives no rate limit. Licence and credit: **not verified** (the usgs.gov copyright page refused automated access on 2026-10-20). |
| **EMSC / SeismicPortal**, FDSN event service ([service](https://www.seismicportal.eu/fdsn-wsevent.html)) | Earthquakes M4.5+, 40 latest. | 60 s | [Terms](https://www.seismicportal.eu/terms.html): CC BY 4.0 with attribution ("EMSC-CSEM SeismicPortal"). Mirroring and bulk copying need written permission. No rate limit published. |
| **GDACS** (UN / European Commission JRC), `geteventlist/MAP` ([terms of use](https://data.gdacs.org/About/termofuse.aspx)) | Earthquakes, tropical cyclones, floods, volcanoes, wildfires, tsunamis; alert level; severity. | 180 s | The terms page states no licence, no attribution rule and no rate limit; it only says the data is provided "as is", must not replace official information, and may have downtime. Licence and attribution: **not verified**. |
| **NOAA National Weather Service**, `api.weather.gov/alerts/active` ([disclaimer](https://www.weather.gov/disclaimer), [API documentation](https://www.weather.gov/documentation/services-web-api)) | Severe and extreme alerts: tornado, hurricane, tsunami, flash flood, fire. | 120 s | Public domain unless noted; no credit required. The API requires an identifying `User-Agent` with a contact (website or e-mail); the rate limit is not published. |
| **USGS Volcano Hazards Program**, `volcanoApi/elevated` ([endpoint](https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated)) | Volcanoes under an elevated alert: aviation colour code, alert level, notice time, position. No VEI is given. | 600 s | No terms page for this API could be opened. Licence and credit: **not verified**. |
| **NOAA Space Weather Prediction Center**, `noaa-planetary-k-index.json` ([data access](https://www.spaceweather.gov/content/data-access)) | Planetary Kp index (3-hourly): latest value, and the G-scale derived from it. | 300 s | The data access page states no licence, credit or limit. Licence: **not verified**. (The NWS public-domain disclaimer applies to weather.gov, not necessarily to this server.) |

## Weather

| Source | What is read | Cache (TTL) | Terms |
| --- | --- | --- | --- |
| **Open-Meteo** forecast ([docs](https://open-meteo.com/en/docs)) | **Primary.** Current conditions, 4 x 15-minute precipitation, daily forecast (weather code, min/max, precipitation sum and probability, wind, UV). | 300 s per position | **[Terms](https://open-meteo.com/en/terms): the free API may only be used for non-commercial purposes**, under 10,000 calls per day, 5,000 per hour and 600 per minute; data under CC BY 4.0. The [licence page](https://open-meteo.com/en/licence) asks for a link to Open-Meteo next to any location where its data is displayed. A commercial plan needs an API key. |
| **Open-Meteo Marine** ([docs](https://open-meteo.com/en/docs/marine-weather-api)) | Waves, swell, ocean current at a GPS position. | 300 s | Same terms. The Marine API also asks to credit the DWD. |
| **Open-Meteo Flood API**, based on Copernicus GloFAS ([docs](https://open-meteo.com/en/docs/flood-api)) | Daily river discharge, ensemble mean and maximum. | 600 s | Same terms. The flood level the API computes is a **heuristic** of its own (ratio of maximum to mean forecast discharge), not an official warning. |
| **Open-Meteo Air Quality**, based on Copernicus CAMS ([docs](https://open-meteo.com/en/docs/air-quality-api)) | European AQI, US AQI, PM2.5, PM10, UV, dust. | 600 s per position | Same terms. The page requires an attribution to the CAMS ENSEMBLE data provider (Copernicus Atmosphere Monitoring Service) and to Open-Meteo. |
| **MET Norway Locationforecast 2.0**, compact ([terms of service](https://api.met.no/doc/TermsOfService), [licence](https://api.met.no/doc/License)) | **Fallback** when Open-Meteo fails: current conditions and hourly series. It has no gust, no apparent temperature, no UV and no 15-minute nowcast: those stay unknown. | 300 s | NLOD 2.0 and CC BY 4.0, credit "MET Norway". Requires an identifying `User-Agent` with a contact (a missing or fake one may be blocked), a cache, and `If-Modified-Since` with the previous `Last-Modified`; the API does **not** send `If-Modified-Since` yet (it is only a fallback, cached 5 minutes). Above 20 requests per second per application a special agreement is needed. |
| **RainViewer**, `weather-maps.json` ([documentation](https://www.rainviewer.com/api/weather-maps-api.html), [changes of 2026](https://rainviewer.com/api/transition-faq.html)) | Latest radar frame timestamp and the tile URL template, sent in `/v1/point`. The API never downloads tiles. | 180 s | Free API for personal and educational use; credit RainViewer with a link. Since 2026-01-01: no nowcast (future) frames, zoom limited to 7, 100 requests per IP per minute. |

## Call volume

With the 5 default zones and something requesting the frame continuously, the API makes at most:
5 forecasts every 5 minutes (1,440 per day) and 5 air quality calls every 10 minutes (720 per day),
that is about 2,200 Open-Meteo calls per day, well below the 10,000 per day limit. A GPS position
adds a forecast, an air quality call and, when requested, marine and river discharge calls. The
hazard feeds add one call per feed per TTL.

## Attribution to show

A screen or page that displays this data should credit its sources (links and credits are listed in the
tables above). The Vigie API does not render a screen itself, except the supervision dashboard
(`/dashboard`), whose header lists the sources. Crediting the sources on the displays is the work of the
display repository; whether it does so was not checked.
