# Data sources

Every provider is public and needs no API key, except NASA FIRMS (the active fires), which needs a free one. The Vigie API is a **personal, non-commercial**
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
| **NOAA National Hurricane Center (NHC)**, `www.nhc.noaa.gov/CurrentStorms.json` and the ZIP of each storm's advisory ([GIS data](https://www.nhc.noaa.gov/gis/), [status file reference](https://www.nhc.noaa.gov/productexamples/NHC_Tropical_Cyclone_Status_JSON_File_Reference.pdf)) | The tropical cyclones with an advisory now (Atlantic, eastern and central Pacific): name, classification, position, wind, pressure, movement, advisory number; and from the bundle of the advisory the cone, the track, the forecast positions and the coastal watches and warnings. See "Storms and tsunamis" below. | 300 s: the file is sent with `Cache-Control: max-age=300` and an `ETag`, and the NHC issues an advisory every three to six hours. Asked conditionally; a bundle once per advisory. |
| **NOAA U.S. Tsunami Warning Centers** (NTWC, PTWC), `www.tsunami.gov/events/js/previous.js` and the CAP message of each bulletin ([tsunami.gov](https://www.tsunami.gov/), [CAP page](https://www.tsunami.gov/?page=cap)) | The list of the latest bulletins (about forty: of each event, each product, the newest), then the CAP message of the newest bulletin of each alert or threat product, and the text bulletin of a threat message (its table of arrival times). See "Storms and tsunamis" below. | 60 s: update rhythm **not verified** (nothing is published; on 2026-10-20 the list was modified 3 seconds after a bulletin was stored). Asked conditionally (`Last-Modified`); a document once. |
| **NOAA Space Weather Prediction Center**, `noaa-planetary-k-index.json` ([data access](https://www.spaceweather.gov/content/data-access)) | Planetary Kp index (3-hourly): latest value, and the G-scale derived from it. | 300 s: the [product page](https://www.spaceweather.gov/products/planetary-k-index) says the chart "updates every minute" and the file is sent with `max-age=60`; the index has 3-hour intervals, so 5 minutes loses nothing. |

## Storms and tsunamis

A storm or a tsunami is a hazard that is a **point** in `GET /v1/geojson/hazards` (and in the frame, the snapshot and the CAP feed
like any other), and has a forecast beside it in `GET /v1/geojson/hazards/{id}/forecast`. The decisions, and what is not
verified: [ADR 0005](decisions/0005-tsunamis-and-storms.md). Licence and credit: [`SOURCES.md`](../SOURCES.md).

| | Storm (NHC) | Tsunami (NTWC, PTWC) |
| --- | --- | --- |
| Hazard `id` | `nhc_<storm id>` (`nhc_al092026`) | `tsunami_<centre>_<event>` (`tsunami_pheb_26282000`) |
| Category, position | `HURRICANE`, the current position | `TSUNAMI`, the epicentre of the earthquake |
| Time | the advisory | the earthquake |
| Value | the maximum wind in km/h (the NHC gives knots) | none |
| Title | `Hurricane Isaias` (the NHC's classification and name) | `Tsunami 1-3 meters - PANAMA` (the centre's event and the place) |
| `has_forecast` | the bundle of the advisory was read | a message gave an arrival time |
| Forecast `kind` | `cone`, `track`, `position`, `watch_warning` | `epicentre`, `eta` |
| Also | | a vigilance (`source` `tsunami`) in `GET /v1/vigilances` |

The levels are ours; the words are the sources':

| Message | Words of the source | Level |
| --- | --- | --- |
| Storm | maximum wind 96 kt or more (category 3 and above) | red |
| | 64 to 95 kt (hurricane) | orange |
| | 34 to 63 kt (tropical storm) | yellow |
| | less | green |
| Tsunami, domestic (NTWC) | Warning | red |
| | Advisory | orange |
| | Watch | yellow |
| Tsunami, international threat (PTWC) | Tsunami >3.0 meters | red |
| | Tsunami 1-3 meters, Tsunami potential threat | orange |
| | Tsunami 0.3-1 meters | yellow |

**An information statement is not an alert**, nor is a cancellation, a test or an exercise (only `Actual` messages count), a threat
class that is in no table, or the "final" PTWC message (a CAP with no `info`), which ends the event. A threat message has no end of
its own and counts for three hours (ours). The 36 information statements of the 40 bulletins of 2026-10-20 are counted and never
downloaded. The list is read, not the per-centre "latest" file: it holds only the newest bulletin of any product, so an
information statement could hide a threat.

**The forecast.** `GET /v1/geojson/hazards/{id}/forecast` is a FeatureCollection (RFC 7946) with `source`, `issued_utc`,
`advisory`, `advisory_url`, `attribution`, `bbox` and, when there is nothing to draw, `reason`. Every feature has
`properties.kind`: `cone` (Polygon, MultiPolygon across the date line), `track` (LineString, MultiLineString across it),
`position` (Point: `tau_h`, `valid_utc`, `wind_kt`, `wind_kmh`, `gust_kt`, `pressure_hpa`, `stage`, `category`) and `watch_warning`
(LineString: `code`, `label`) for a storm; `epicentre` (Point: magnitude, depth, origin time, the messages in force) and `eta`
(Point: `place`, `region`, `eta_utc`; a place with no coordinates has a null geometry) for a tsunami. The coordinates are rounded
to 3 decimals; a pressure the NHC writes as 9999 is `null`. Hurricane Isaias's advisory 13A: a ZIP of 29.5 kB, an answer of 22.7 kB
of which the cone is 21 kB (`tests/test_storm_api.py`).

**One event, once.** GDACS reports the same storms, and the NWS relays the domestic tsunami messages. A storm is recognised by its
name and season (`ISAIAS-26` is `cyclone:isaias:2026`), a tsunami message by its VTEC code; the hazard of the source that observes
the event (the NHC, the tsunami centres) is kept, with **its** level and wind. An NWS alert that carries the VTEC code of a message
of the centres is left out of the warnings of the same answer.

| Day (simulated, `tests/test_storm_sobriety.py`) | Requests | Bytes |
| --- | ---: | ---: |
| tsunami.gov, a quiet day (the scheduler alone; 1,393 with 1,000 clients) | 960, all but the first a `304` | 230 kB |
| tsunami.gov, the day of the Panama earthquake (2026-10-09, four threat messages) | 967: 960 looks at the list (15 read in full), 7 documents | 253 kB |
| NHC, no storm (286 with 1,000 clients) | 240, all but the first a `304` | 116 kB |
| NHC, Hurricane Isaias with a new advisory every three hours | 248: 240 looks and 8 bundles | 387 kB |

Neither provider publishes a rate limit (**not verified** that none exists): the calls are counted, not limited. A request that
finds nothing new is a `304` of 239 bytes (tsunami.gov) or 484 bytes (the NHC); the weekly check (`tools/check_live_feeds.py`)
reads each once.

## Places near a hazard, and watched points

An earthquake or a volcanic event says what it is close to: `near_places` (a list of `name`, `kind`, `distance_km`, `sources`) on every hazard,
named in the hazard's `derived` list. **It is computed by Vigie from reference data and from settings**, not reported by a source, it
never changes a `severity`, and a client should show it as such ("calculated by Vigie"). The decisions, and what is not verified:
[ADR 0008](decisions/0008-sensitive-points.md). Licence and credit: [`SOURCES.md`](../SOURCES.md).

`near_places` is a list when it was worked out (**empty: nothing is near**, an answer, not a gap) and `null` when it was not: the hazard is
compared with nothing (a storm, a flood...), the bundled fault file could not be read, the alert is simulated, or the profile is the compact one
(see "Cost" below).

**It holds reference places only: the named active faults and the volcanoes that the hazard sources name.** The points a user watches are never
in it. `near_places` is in the outputs every client shares (`/v1/geojson/hazards`, `/v1/history`, the full `/v1/snapshot` and `/v1/point`; and the
stream and the compact profiles, with `null`), a public website included, and a watched point is personal (a home, for example): its name and
position would leak through the hazards, and anyone who could `POST` a point could make any text appear in everyone's hazards. The watched
points are listed by `/v1/watched-points` and `/v1/watched-points/status` only (below).

### What each hazard is compared with

| Hazard | Active faults | Volcanoes in alert |
| --- | --- | --- |
| Earthquake | yes, if its depth is at most 70 km or unknown | yes |
| Volcanic event | yes | no |
| Tsunami, storm, flood, fire, any other | no (`near_places` is `null`) | no |

- **Active faults**: the traces that have a name in the GEM Global Active Faults Database, bundled in `app/data/gem-active-faults.geojson.gz`
  (below). The distance is from the hazard's position to the nearest point of the trace; a fault with several traces is said once, at its nearest.
  **It is the distance to the mapped surface trace.** The depth is not used, and it does not say that the earthquake happened on that fault: an
  earthquake deeper than 70 km is left out for that reason (a trace 4 km from the epicentre of an earthquake 162 km down means nothing).
- **Volcanoes in alert**: only those that a hazard source of the moment names: a USGS elevated alert (`vName`; 4 volcanoes on 2026-10-20: Great
  Sitkin, Shishaldin, Kilauea, Ahyi Seamount) or a GDACS eruption (`eventname`; 2 on that day, Kanlaon and Taal, both no longer current). **There is
  no bundled list of the world's volcanoes**: the Smithsonian Global Volcanism Program's list has an unclear licence, and its owner has not been asked
  yet (a question for the project's owner). So Etna is named only while a source reports it (a user can still watch it as a point of kind
  `volcano`, which is personal and shows in the status only). A volcano the feed gives no name is not named.

### The settings

The radii are **settings, not sourced facts**: no agency publishes the distance at which an epicentre is "near" a fault, and the positions of
the catalogues are uncertain (by how much, for these feeds: not verified). They say how far this instance looks.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VIGIE_NEAR_FAULT_KM` | 10 | An earthquake or volcanic event within this distance of a mapped trace is near the fault (0.1 to 100). A round figure, about 18 times the most the simplification of the traces can move them (0.56 km) |
| `VIGIE_NEAR_FAULT_MAX_DEPTH_KM` | 70 | Deeper earthquakes are not compared with the faults (1 to 1000). 70 km is the usual limit between shallow and intermediate-depth earthquakes (**not verified** against a source in this project) |
| `VIGIE_NEAR_VOLCANO_KM` | 20 | An earthquake within this distance of a volcano in alert is near it (0.1 to 500) |
| `VIGIE_NEAR_MAX_PLACES` | 5 | At most this many places under a hazard, the nearest first (1 to 20) |
| `VIGIE_WATCHED_POINTS_MAX` | 50 | The most watched points an instance keeps (1 to 500); a new one over the limit is a 409 |
| `VIGIE_ENABLE_WATCHED_POINTS_WRITE` | 0 (off) | `1` enables `POST` and `DELETE` of `/v1/watched-points` (403 otherwise; the reads always work). Not a radius: a switch, listed here with its siblings |
| `VIGIE_QUIET_MAX_GAP_S` | 900 | A source not read for longer than this breaks the run of readings that "nothing happened" rests on (60 to 86,400) |

A value that is not a number, or is outside its range, means the default. The radius of a watched point is its own: up to 500 km, 50 by default.

### The fault file

| | |
| --- | --- |
| Source | GEM Global Active Faults Database, `geojson/gem_active_faults_harmonized.geojson`, commit `56816508` of 2021-06-24 (the last of the repository: the database has not changed since) |
| Licence | **CC BY-SA 4.0** (share-alike): the file stays under it, the code stays MIT. Notice and statement of the changes: `app/data/gem-active-faults.NOTICE.md` |
| Citation | Styron, Richard, and Marco Pagani. "The GEM Global Active Faults Database." *Earthquake Spectra*, vol. 36, no. 1_suppl, Oct. 2020, pp. 160-180, doi:10.1177/8755293020944182 |
| Built by | `python tools/build_faults.py`: downloads the pinned commit, refuses any other content (git blob id), keeps the traces that have a name |
| Kept | 6,090 traces of 13,696 (26,360 vertices of 154,994); 189 KiB gzipped, 1,083 KiB raw |
| Simplified | Douglas-Peucker, 0.005 degree (the line stays within 0.56 km of the original), coordinates rounded to 3 decimals (110 m), elevation dropped |
| Load, memory | 31 to 36 ms at start, +4.2 MB of memory (Windows, Python 3.13, 5 loads) |

**The traces without a name are not kept** (7,598 of 13,696): the mention is "N km from <name>", and a name the database does not give is not
made up. The consequence is a hole in the Mediterranean and Asia: the traces there come from datasets that give catalogue numbers only. In the file, the
74 traces whose centre is around the North Anatolian Fault (27 to 41 degrees east, 39.5 to 41.5 north) are all unnamed (EMME, `ME_TRCS003`...), and so are the
198 around Greece (19 to 28 east, 36 to 41 north), while 550 of the 629 around Japan are named. **The North Anatolian Fault is not named in
this file**, so an earthquake on it gets no fault under it. The names are the database's, spelled as it spells them (`ROMERAL_FAULT`), with the fault system (`fs_name`)
preferred to the fault (`name`) when both exist. A name damaged by an encoding error (6 names) is treated as no name, not repaired.

### How well the distance is measured

The distance is worked out on a flat map scaled to the latitude of the hazard, then as a great circle (`line_distance_km`, the one used for river sections).
`tests/test_near_places.py` checks it against **a second, independent computation**: Vincenty's formula on the WGS84 ellipsoid, to a dense sampling (100 m) of the
original unsimplified traces (`tests/oracle_geodesy.py`, itself checked against Geoscience Australia's published example, 54,972.271 m). Two real earthquakes:
the M 7.7 of Panama of 9 October 2026 (USGS `us6000u18k`, depth 12.6 km) is **4.3 km from the Azuero Fault Zone** (the oracle: 4.194 km; the API's method on the original trace:
4.210 km; on the simplified bundled trace: 4.26 km, shown as 4.3); the M 4.6 of Assam (`us6000tzth`, depth 10 km) is 3.2 km from the Barapani - Tyrsad Fault (oracle 3.213 km, API 3.210 km on the original trace).

Over the **656 earthquakes of 30 days** (the USGS M4.5+ feed, to 2026-10-20), the same 47 earthquakes have a named fault within 10 km by both methods (before the depth rule),
55 (earthquake, fault) pairs, none found by only one of the two; the bundled file differs from the oracle by **-0.47 to +0.46 km, mean +0.08 km, median +0.05 km**, inside the simplification.
After the depth rule 43 of the 656 have a fault under them (132 of the 656 are deeper than 70 km), most of them aftershocks of the Panama earthquake. Without the name
rule 114 would have one: 67 are near only unnamed traces.

### Cost

Measured on the author's PC (Windows, Python 3.13), not on a server.

| | |
| --- | --- |
| One earthquake against the faults | 15 microseconds (656 real earthquakes in 10 ms) |
| All the hazards of a request, 56 hazards (the merged feeds of 2026-10-20) | 2.5 ms (median of 200 runs). **Measured with up to 50 watched points before they were taken out of `near_places`: 2.7 ms with 5, 4.3 ms with 50. The points no longer cost anything per request here** |
| 639 hazards | about 30 ms (measured with 50 points before they were taken out: 32 ms) |
| The compact `GET /v1/snapshot` and `GET /v1/point` | **carry no `near_places`** (null): a place and the credit of its source on each of 15 hazards would not fit in 16 KiB. The snapshot of the realistic test grows by 480 bytes (the null and the empty `derived` of 15 hazards: 14,457 to 14,937 of 16,384). Ask `compact=false`, `/v1/geojson/hazards`, `/v1/history` or the stream for the places |

### Watched points and their status

`GET/POST /v1/watched-points`, `DELETE /v1/watched-points/{id}`: a name, a kind (`place`, `fault`, `volcano`, `custom`), a position and a radius, stored in SQLite
with the zones. **A watched point is personal and is listed here only**: by `GET /v1/watched-points` and the status below, never in the hazards (see above).
Like `/v1/zones`, the routes have no authentication, and the list and the status are readable by anyone who can reach the API: do not watch your home on an
instance that others can read.

**Writing is off by default.** `POST /v1/watched-points` and `DELETE /v1/watched-points/{id}` answer 403 with a message until the instance sets
`VIGIE_ENABLE_WATCHED_POINTS_WRITE=1` (`1`, `true`, `yes` or `on`; read at each request; `GET /v1/health` reports `watched_points_write_enabled`). The reads
always work. The switch is the same kind as `VIGIE_ENABLE_SIMULATION`, and it is not enough on a public instance: the two write routes must not be reachable
from the Internet, like `POST /v1/zones` and `POST /v1/simulate`, so put the instance behind a reverse proxy that blocks them.

`GET /v1/watched-points/status?days=7` gives, for each point, the events within its radius over the last `days` days (1 to 30) and a `state`:

| `state` | Meaning |
| --- | --- |
| `events` | Something happened within the radius: `events` lists the most recent (20 at most; `events_count` is the total), each with its distance. `unreachable_sources` says which source did not answer: the list may be incomplete |
| `quiet` | Nothing happened, **and the sources vouch for it**: `quiet_since_utc` is the start of the quiet period and `window_covered` says whether it is the whole window |
| `unknown` | Nothing was found, but the sources do not vouch for it: one did not answer (`unreachable_sources`), or none has been read yet |

- **The events** come from the history of hazards the API keeps and from the hazards active now. An earthquake counts by the time it happened; any other hazard (a
  storm, a flood, a volcano in alert) by the last time the API saw it active, because it lasts. A simulated alert never counts. A hazard is compared by its position: the extent of a storm
  or a flood is not known, so one whose centre is farther than the radius is not listed.
- **"Nothing happened" is proved, not assumed.** A point is `quiet` only if every one of the 7 hazard sources (USGS, EMSC, GDACS, the NWS, the USGS volcanoes, the NHC, the tsunami centres) answered at the last
  reading (a stale answer served after a failure does not count), **and** each has been read without a gap longer than `VIGIE_QUIET_MAX_GAP_S` since `quiet_since_utc`. The history holds only what the API saw: an instance
  started an hour ago says "nothing in the last hour" (`window_covered: false`), never "nothing in the last week". The runs are kept in SQLite (`source_runs`), so a restart does not forget them and a long stop breaks them.
- **No part of the world is excused.** The NWS covers the United States and the NHC some oceans, but the API keeps no verified map of what each source could report where, so a source that is down stops
  the claim everywhere: the safe side, not the most precise one.
- The status is computed by Vigie, not reported by a source, and is not an official warning.

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
| **NOAA U.S. Tsunami Warning Centers**, the messages the hazards already read | The tsunami messages in force as warnings: the CAP fields as given, the UGC codes of the domestic messages, the countries of the threat messages, no polygon. Listed by `country=US` or `CA`, or all together by `source=tsunami`; an NWS alert with the VTEC code of one of them is left out. | 60 s, as the hazards. **No request of its own.** |
| **DWD** (Deutscher Wetterdienst), `opendata.dwd.de/weather/alerts/cap/DISTRICT_DWD_STAT/Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_DISTRICT_MUL.zip` ([open data help](https://www.dwd.de/DE/leistungen/opendata/hilfe.html): the CAP DWD profile 2.1.14, the product overview, the implementation notes) | One ZIP for all of Germany: the **status** (every warning in force at the last production) **by district** (the polygon of an area is the district, with its holes cut out), in the **multilingual** variant (one CAP document per warning, one `info` per language: German, English, French, Spanish, Arabic, Russian, Turkish, Polish). `LATEST` is a name that always points to the newest production. 8 to 16 KB on 2026-10-20 (4 documents, 7 areas), 80 KB at most over the three days of history read. A cancelled warning is no longer listed: cancellations and all-clears are only in the `DIFFERENCE` products, which are not read. Chosen over the `COMMUNEUNION` product (finer, smaller) because that one has no polygon for the sea areas. | 120 s. The DWD produces the status up to once a minute, and at least every ten minutes even if nothing changed (implementation notes); about every 10 minutes was observed on 2026-10-20. It sends no `Cache-Control` but `ETag` and `Last-Modified`, and answers `304` to `If-None-Match` and to `If-Modified-Since` (checked): a refresh that finds nothing new is a few hundred bytes. The delay for a redistributor is not published: **not verified**. |
| **ECCC** (Environment and Climate Change Canada), MSC GeoMet OGC API, `api.weather.gc.ca/collections/weather-alerts/items` ([docs](https://eccc-msc.github.io/open-data/msc-geomet/readme_en/)) | One request lists every alert in force in Canada (`limit=1000`; the next pages are asked only if a stormy day needs them, at most ten): colour, name, texts in English and French, times, province, polygon of the forecast area. 156 KB (31 KB with gzip) for 36 alerts on 2026-10-20. The Datamart CAP files are **not** read: see [`SOURCES.md`](../SOURCES.md). | 300 s. GeoMet sends no `Cache-Control`, no `ETag` and no `Last-Modified`, and ECCC documents no update rhythm for the list (the CAP files are posted as the alerts are issued): **not verified**. One request per refresh, a rate far under the usage policy's 86,400 a day. **Reachability, seen once:** from the author's PC on 2026-10-20 (12:50 to 13:20 UTC) about two connections in three to `api.weather.gc.ca` timed out (21 seconds, the system's limit) while the others were answered in 0.6 second with the whole list, so the answer is fast and the path is what is unreliable. The collector's 6-second timeout is kept: a failed look serves the last list, marked stale, and the next scheduler cycle asks again. It may not happen from another network. |
| **MetService** (New Zealand), `alerts.metservice.com/cap/rss` ([the system](https://about.metservice.com/about-severe-weather-warnings)) | The RSS feed, then the CAP document each item links to (each fetched once, forgotten when it leaves the feed; only addresses under `alerts.metservice.com` are fetched). The format is documented by the issuer ([user guide v1.2](https://www.metservice.com/files/documents/cap/MetService-CAP-Feed-User-Guide-v1.2.pdf)) but **the feed was empty on 2026-10-20: no real item has been seen** (see [`SOURCES.md`](../SOURCES.md), which also says where the licence stands). | 300 s: the feed sends `Cache-Control: max-age=300, must-revalidate` and a `Last-Modified` that answers `If-Modified-Since` with a `304` (checked). |
| **Vigicrues** (SCHAPI), `www.vigicrues.gouv.fr/services/InfoVigiCru.geojson` ([services v1.1](https://www.vigicrues.gouv.fr/services/v1.1)) | One request for all of France: 337 sections of rivers, a line each, with the colour (1 to 4) of the vigilance. Only the sections above green are returned (19 on 2026-10-20: 17 yellow, 2 orange). 2.2 MB, not compressed. No lighter endpoint carries the level: the others of the documentation give the referential, forecasts and observations. | A look at the publication image every 15 minutes (5 while a section is orange or red); the flow itself only when the image changed, at the latest after 12 hours. The [dataset](https://www.data.gouv.fr/datasets/troncons-de-cours-deau-vigicrues-simplifies-avec-niveau-de-vigilance-crues-2) says the flow follows the vigilance bulletins, twice a day (10 a.m. and 4 p.m.) outside floods and more often during them (the rhythm of the extra bulletins is **not verified**). The server sends `Cache-Control: max-age=120` but no `ETag` and no `Last-Modified`: **no conditional request** on the flow; measured on 2026-10-20 it is not compressed (`gzip, deflate, br` are ignored), a `Range` request is answered with the whole body and no filter is taken. Every read costs the 2.2 MB, so the reads are cut to the publications, using the validators of the national map image the same publication regenerates (`/ftp/img_france.png`, a `HEAD` of about 330 bytes; see [`SOURCES.md`](../SOURCES.md) for what is and is not verified). |

| **WMO Severe Weather Information Centre, registry of services** (`wmo_swic_registry`), `severeweather.wmo.int/v2/json/sources.json` ([the centre](https://severeweather.wmo.int)) | The list of the national services that publish CAP warnings: id, country, name, feed addresses. 140 records on 2026-10-20 (156 KB). Used to know which countries can be served, not as a filter (all are `operating` and `official`). | A day, with `If-Modified-Since`. The registry changed last on 2024-03-18. A snapshot is bundled; if the WMO does not answer it is asked again in an hour. |
| **The services of the registry** (`wmo_swic`), the feeds of `capAlertFeed` and the CAP document of each item | See "The services of the WMO registry" below. | 5 minutes at most per service, longer for a feed that cannot be asked conditionally. |

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
| Spain | AEMET's own feed, through the registry | MeteoAlarm's Spanish feed, unless AEMET gave no answer (then it stands in, and the answer says so) |
| any other country with a verified service of the registry (Algeria, Argentina, Barbados, Jamaica, Mexico, the Philippines) | that service | |
| any other country with a service of the registry, `VIGIE_WMO_SOURCES=all` only | that service | |

- The country of a position is found **offline** (borders bundled in `app/data/`, see [`SOURCES.md`](../SOURCES.md)), without asking any service.
  Every country has a border, also the ones the instance reads nothing for: a position there is answered with the country and why nothing is read
  (`uncovered`, `note`; a stable `reason_code` and a sentence, from the table "Countries with no source" of [`SOURCES.md`](../SOURCES.md)), not with a bare "nothing".
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

### The services of the WMO registry

The registry of the WMO's Severe Weather Information Centre lists the national services that publish CAP warnings (140 on
2026-10-20 in 135 countries). A country that has a source of its own keeps it (the rule of ADR 0003); a service of the registry
answers for a country that has none. The decisions and their evidence: [ADR 0006](decisions/0006-wmo-registry-services.md); the
terms of each service: [`SOURCES.md`](../SOURCES.md). **Which services an instance reads** is `VIGIE_WMO_SOURCES`:

| Setting | Services read | Countries (on 2026-10-20) |
| --- | --- | --- |
| `verified` (the default) | the 7 whose reuse terms were read and allow it | 7: Algeria, Argentina, Barbados, Jamaica, Mexico, the Philippines, and Spain (AEMET instead of MeteoAlarm) |
| `all` | the 95 that are not excluded and not shadowed by a collector | 94 |
| `off` | none | none |

Of the 140 records, 43 are for countries that already have a collector (the MeteoAlarm countries, the United States, Germany,
Canada, New Zealand), and 2 are never read (Hong Kong, China). `GET /v1/vigilances/countries` lists what the instance serves
with its setting.

**How a service is read.**

1. Its feeds: the addresses of `capAlertFeed` (205 for the 140 services; 51 services have several, one per language). RSS or Atom;
   an item is read only if it is less than 14 days old, or the index says it is still in force. Links are followed only on the
   feed's own site (on an S3 bucket, on its own host), never to an IP address or `localhost`.
2. For a service on a national host, the WMO's own copy of its feed (`severeweather.wmo.int/v2/cap-alerts/<id>/rss.xml`), to add what
   the feed lacks. On 2026-10-20 only 1 of 260 such addresses answered (the 259 others: HTTP 404), so a miss is remembered for a
   day and three misses in a row pause the copy for an hour. A service whose feed is in the WMO's bucket is not asked twice.
3. The two are merged by CAP `sender` and `identifier`. An `Update` or a `Cancel` removes the alerts its `references` name
   (Argentina chains dozens). Each CAP document is read **once**, whichever feed lists it, and at most 60 new ones per read of a
   service (the rest at the next). The XML is parsed without a DTD or entity.
4. A CAP alert with status other than `Actual`, scope other than `Public`, urgency `Past` or certainty `Unlikely` is not a warning.
   The alerts of the same event in two languages under two identifiers (Russia, Kazakhstan) are one warning with two texts.

**Placing a warning.** A polygon (`lat,lon` pairs, with any white space) is used as it is; a circle is drawn as a 48-point
polygon (cut at the date line, none near a pole); codes only (the Philippines' SAME codes, India's district codes, Thailand's
ISO 3166-2) are kept as `geocodes` and the warning is **counted in `warnings_not_matchable`**, never placed. The identifier
and the geocodes are the issuer's, unchanged, in `/v1/vigilances.geojson` as in the JSON.

**The three derived fields** (in `derived`; a word in no table gives `null`):

| Field | From | Table |
| --- | --- | --- |
| `status` | `msgType` | `Alert` is `new`, `Update` is `update`, `Cancel` is `cancel` |
| `level` | a colour the issuer writes in its own parameter (`awareness_level`, `AEMET-Meteoalerta nivel`, `nivel`: exactly one colour word), else the CAP `severity` | green / verde, yellow / amarillo / amarilla, orange / naranja, red / rojo / roja give `GREEN`, `YELLOW`, `ORANGE`, `RED`. Severity: `Moderate` `YELLOW`, `Severe` `ORANGE`, `Extreme` `RED`; **`Minor` and `Unknown` give `null`** (the services do not agree on what a minor alert is: the DWD calls it yellow, the NWS green, the Philippines' signal 1 is "Minor") |
| `phenomenon` | the `event` words, every language of the alert (they must agree), accents and case ignored, a keyword matches the start of a word, the first rule in this order wins | `avalanche`; `wind` (tropical cyclone, hurricane, typhoon, tornado...); `coastal` (storm surge, tsunami, swell, high seas, marine...); `fire`; `flood`; `other` (sandstorm); `snow-ice`; `thunderstorm`; `cold`; `heat`; `fog`; `rain`; `wind` (wind, gale, gust); `other` (drought, dust). English, Spanish, Portuguese and French only; the full lists are `PHENOMENON_RULES` in `app/collectors/swic_cap.py`. An event in none, or in two languages that disagree, gives `null` |

The issuer's own words (`event`, `texts` in every language, `level_original`, `phenomenon_original`) are passed unchanged.

**Coverage.** Each service is its own line of `coverage[]`, with `source: swic` and its `source_id`: `ok`, `stale` (the last good answer
is served, marked), or `unavailable` (nothing was ever read; never read as "nothing in force"), with a note when only part of its
feeds answered, when its warnings came from the WMO's copy only, or when documents were left for the next read. A service
that is `unavailable` is silent for the push: it ends nothing. Spain's MeteoAlarm copies appear only if AEMET is `unavailable`.

**The rhythm of a service**, and why:

| Rule | Value | Why |
| --- | --- | --- |
| Cache | 5 minutes (`SOURCE_TTL_S`); with the scheduler's 90 s cycle a service is asked every 6 minutes, **240 times a day** | Of 149 feeds asked for their headers on 2026-10-20, 17 sent a `Cache-Control` and 10 an `Expires`. The shortest lifetime announced was 60 s (Spain, Kuwait, Singapore, Thailand, Uruguay; Argentina 60 to 180 s): 5 minutes is five times that, and no feed asked for more. The others announced nothing, `no-store`, or a server default (Mexico: a year) |
| Conditional requests | `If-None-Match` / `If-Modified-Since` when the last answer gave a validator | 138 of the 150 feeds that answered on 2026-10-20 gave one. A `304` weighs a few hundred bytes (Algeria and Spain: 237 of 240) |
| A feed with no validator | 5 minutes per 64 KiB (rounded up) of what it weighs on the wire, 30 minutes at most | It is downloaded whole each time: Trinidad and Tobago's feed is 641 KB of XML (67 KB sent), so every 10 minutes |
| Size caps | an index 2 MB and 1,500 items, a CAP document 1 MB; an error page is not read | China's feed (never read) lists 1,000 items, 1 MB, and each document weighs 89 KiB |
| A service that fails | the third failure in a row waits 60 s, then twice as long up to an hour; an HTTP 4xx waits an hour | One bad feed must not close the others: every service has its own circuit |
| The set | a ceiling of 50,000 requests a day (`wmo_swic`, ours: no service publishes one) | So that a loop cannot hammer a hundred servers; the real use is far below it |

**What it costs** (`tests/test_swic_sobriety.py`, run it with `pytest -s`: a simulated day of the scheduler against the registry
and the feeds as measured on 2026-10-20, `tests/fixtures/swic/profile.json`; nothing touches the network). One followed point in
each country, the worst case a personal instance can ask of this part; the number of clients adds nothing:

| Scenario | Requests a day | Bytes a day |
| --- | ---: | ---: |
| `VIGIE_WMO_SOURCES=verified` (the default): 7 services in 7 countries | **2,010** (feeds 1,680, documents 322, the WMO's copy 7, registry 1) | **10.6 MB** (Philippines 5.2, Mexico 3.4, Argentina 0.8, the other four 1.1 together) |
| `all`: 95 services in 94 countries, one point in each | **36,924** (feeds 35,073, documents 1,824, the WMO's copy 26, registry 1) | **51.1 MB** (Kazakhstan 12.6, Trinidad and Tobago 9.4, Philippines 5.2, Mexico 3.4, Thailand 2.7, Russia 1.9...) |
| The simulated day of 1,000 clients (`python -m tests.simulation`) | the registry once (**1**); no service is asked without a followed point or a watched zone in its country | 21 kB |

The Philippines and Mexico are heavy because their feeds give no validator; a validator would cut them to a few hundred bytes a
request. Nothing is sent to anyone: the services are asked read-only, with the `User-Agent` of the API.

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

## Active fires (NASA FIRMS)

The only source that needs a key. The detections of the VIIRS instruments of NOAA-20 and NOAA-21 (375 m pixels, the near
real-time product) are read from the area API of FIRMS, `firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/world/1/{DATE}`,
and served by `GET /v1/fires`, `GET /v1/fires/grid` and the `fires_nearby` block of `GET /v1/point`. They are not in
`GET /v1/geojson/hazards`. Without `VIGIE_FIRMS_MAP_KEY` nothing is read (the routes answer 404 and `GET /v1/health` lists the
source in `disabled_sources`). The decisions: [ADR 0007](decisions/0007-active-fires-from-nasa-firms.md). Licence, credit and
what is not verified: [`SOURCES.md`](../SOURCES.md).

**What is read.** One world file per source and UTC day: today at each refresh (60 minutes by default,
`VIGIE_FIRMS_REFRESH_MINUTES`), yesterday until it has settled (4 hours after midnight) and once more. A detection is a satellite
pixel that looks like a fire: it is not an alert, and no detection is not the absence of fire. Suomi NPP (deliveries cease on
2026-11-02) and MODIS (another layout, 1 km) are not read.

**What a world file is** (reads of 2026-10-20 through the HA host, the PC's own path to FIRMS being unreliable; the CSV is not
compressed and is sent without `ETag`, `Last-Modified` or a size on a `HEAD`):

| File | Size | Rows | Taken at |
| --- | ---: | ---: | --- |
| NOAA-20, today | 4,622,753 bytes | 55,239 | 18:10 UTC |
| NOAA-20, today | 4,748,580 bytes | 56,730 | 18:42 UTC |
| NOAA-21, today | 3,944,231 bytes | 47,048 | 18:42 UTC |
| NOAA-20, 2026-10-19 (whole day) | 4,969,942 bytes | 59,246 | 19:21 UTC |
| NOAA-21, 2026-10-19 (whole day) | 4,662,308 bytes | 55,517 | 18:35 UTC |

A refresh reads two such files (one per source): about 8.5 to 10 MB.

**Parsing and memory** (the development PC, a Ryzen 9800X3D; the Steam Deck or a Raspberry Pi **not measured**): 0.13 to 0.16 s (best of 5) to parse a
world file of 47,000 to 59,000 rows with the `csv` module, run in a thread so that the event loop is not held. The rows are kept as typed
columns sorted by latitude, about 43 bytes a row, so 2.4 MB a file and about 10 MB for the four files kept (two sources, two days);
the peak while parsing is 35 to 42 MB. A request is answered from these columns: a whole-world selection of the strongest 1,000
detections takes 33 ms, a whole-world grid 60 ms, and a selection in Europe 1.2 ms.

**When the data arrive.** NASA says up to 3 hours after the observation (the United States and Canada within minutes). Between
two reads 46 minutes apart (18:10 and 18:56 UTC) the NOAA-20 file gained 1,493 detections: 1,425 acquired between 16:05 and 16:30
over South America and the Atlantic, published between 18:10 and 18:42 (the file grew by 292 detections by 18:30 and by 1,199
more by 18:42), so 2 to 2.5 hours after the pass; and 68 acquired in the last minutes over the Americas. The delay over the other
regions was **not measured**. A new refresh each hour is therefore not slower than the data. A file has no validator, so each
refresh is a full read; this is why the default is 60 minutes and not 30.

**Transactions.** FIRMS allows 5,000 transactions in 10 minutes per key and weighs a request by its size ("larger transactions may
count as multiple requests"). A world read of one day was measured at **36** with `mapkey_status`: with the counter at 0
(`current_transactions`, nothing sent for 20 minutes), one read of NOAA-20 for 2026-10-19 (4,969,942 bytes) left it at 36
(2026-10-20 19:21 UTC; a read that failed before the answer left it at 0). `app/collectors/budget.py` knows it (the quota `nasa-firms`, 90 % of 5,000, so 125 reads in 10 minutes). A refresh is 2 reads (4 when
yesterday is read again).

**What it costs a day** (`tests/test_fires_sobriety.py`: a simulated day with the sizes above and the arrival of the detections taken
from the recorded files):

| Day | Reads | Transactions | At most in 10 minutes | Bytes |
| --- | ---: | ---: | ---: | ---: |
| Nobody asks | 0 | 0 | 0 | 0 |
| 1,000 clients (or 100: the same), refresh of 60 minutes | 60 | 2,160 | 144 | about 170 MB (289 MB at most) |
| 1,000 clients, refresh of 30 minutes | 116 | 4,176 | 144 | about 320 MB (559 MB at most) |
| FIRMS down all day (503) | 284 requests, all refused | 10,224 as the budget counts them (a refused request may cost nothing at FIRMS: **not verified**) | 216 (6 requests) | none |

The reading at start-up (4 reads, about 18 MB) comes on top, when the instance has a key. A failing source is not asked again for
its other day in the same refresh, and the circuit breaker opens after 3 failures for 10 minutes.

**When FIRMS fails.** FIRMS loses connections and answers slowly (many of the requests of the day of the measures timed out).
The connect timeout is 12 s, with one more try on a connection error; a request never waits for FIRMS once something was read:
the previous reading is served, `stale` is true if the last attempt failed, and the next attempt is made in 5 minutes. A read
that ends cleanly but holds under 90 % of the rows of the previous one of the same day (a cut connection) is refused. A row with
no readable position or time is skipped and counted (`skipped_rows`).

**The key.** It is in the URL of every request. It is never printed: not in an error, in `last_error` of `GET /v1/providers`, in
the logs of `httpx`, or in any answer. `tests/test_firms_secret.py` looks for it in all of them.

**The routes.**

| | `GET /v1/fires` | `GET /v1/fires/grid` |
| --- | --- | --- |
| `bbox` | `west,south,east,north`, required | the same, the whole globe by default |
| Window | `hours`, 24 by default, up to 48 | the same |
| Filters | `min_confidence` (`low`, `nominal`, `high`), `min_frp` (MW) | the same |
| Cap | `limit`, 1,000 by default, 5,000 at most; the strongest first | `limit` cells, 5,000 by default, 20,000 at most; the busiest first |
| Cell | | `cell_deg`: 0.25, 0.5, 1, 2, 5 or 10 |
| Answer | a FeatureCollection of points with the fields of the FIRMS file | a FeatureCollection of cell centres: `count`, `max_frp_mw`, `latest_utc` |

A `bbox` must have `-90 <= south < north <= 90` and longitudes within -540 and 540; a west greater than the east crosses the
antimeridian, and a span of 360 degrees or more is the whole globe. Anything else is a 422. The metadata of every answer says how
many detections matched (`total_matching`) and whether the answer was cut (`truncated`), when FIRMS was read (`data_read_utc`),
whether the last attempt failed (`stale`), and carries NASA's credit (`attribution`) and disclaimer (`disclaimer`). The answers
are sent with `Cache-Control: public, max-age=60`.

`fires_nearby` of `GET /v1/point` (`fire_radius_km`, 50 by default and 500 at most) gives `count_24h`, `nearest_km`,
`max_frp_mw` and `latest_utc` of the detections of the last 24 hours within that radius. It is listed in `derived`, it is ours, and
it never raises `local_alert_level`. It is `null` when the instance has no key or nothing has been read yet (the point never waits
for FIRMS).

## Call volume

NASA FIRMS adds nothing when nobody asks the fire routes, and with someone asking 60 world reads a day (2,160 of its transactions, about 170 MB) at the refresh of an hour (see "Active fires").

The services of the WMO registry add, per service that is read, 240 requests a day (a conditional request each 6 minutes), documents
once each and the registry once a day: 2,010 requests and 10.6 MB a day for the 7 services read by default, 36,924 and 51 MB with
`VIGIE_WMO_SOURCES=all` and a followed point in each of the 94 countries (see "The services of the WMO registry").

A refresh happens only when someone asks after the TTL, so the calls per day of a cell are
`86400 / TTL` at most, whatever the number of clients. With the TTLs above, per cell and per day:
at most 96 "now" requests of the forecast (15 minutes) and 24 daily ones (1 hour), 24 air quality
calls (1 hour), 24 marine calls (1 hour) and 4 river discharge calls (6 hours). A GPS position adds
those calls (marine and river discharge when requested); two positions in the same cell share them.
The hazard feeds add one call per feed per TTL (at most 1,440 a day for USGS and EMSC, which have the
shortest TTL; the scheduler alone refreshes them every 90 s, 960 a day). The storm and tsunami sources add one conditional call per cache lifetime: tsunami.gov 960 a day from the scheduler (1,393 with 1,000 clients; all but the first a `304` of 239 bytes) and the NHC 240 (286), plus a document per new bulletin and a bundle per new advisory (see "Storms and tsunamis"). The official warnings add one call per MeteoAlarm feed per 180 s, only for the countries that are needed
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
RainViewer and ECCC (a usage-policy line of 86,400 requests a day: a threshold to tell ECCC about, kept as a limit all the same) have one. NASA FIRMS has one too, in transactions: 5,000 in 10 minutes, and a world read of a day weighs 36. NOAA NWS has a limit that "is not public information", and USGS, EMSC, GDACS,
USGS Volcano, NOAA SWPC, the NHC, the tsunami centres, MeteoAlarm, the DWD, MetService and Vigicrues publish none: no budget for them (**not verified**), their calls are only counted. The services of the WMO registry publish none either; the set has a ceiling of 50,000 requests a day (`wmo_swic`), which is ours and not a published limit.

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
