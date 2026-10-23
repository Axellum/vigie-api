# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- **A number JSON cannot carry is a 422, not a 500.** Python's parser accepts `NaN` and `Infinity`; the validation error that quoted the value
  could not be encoded, so any route with a number (`POST /v1/zones`, the new `POST /v1/watched-points`...) answered 500. The value is now
  written as text in the error.

- **No flood level is drawn from river discharge any more.** `flood_risk.flood_alert_level` of `/v1/point` is always
  null and no longer raises `local_alert_level` (points, zones, dashboard). It came from `anomaly_ratio`, the highest
  GloFAS ensemble member over the ensemble mean, which measures how far the forecast scenarios spread, not how high the
  river is: on 2026-10-20 it said "yellow" for the Seine in Paris while the day's discharge (32.65 m3/s) was below the
  mean forecast (55.63 m3/s). Open-Meteo gives no flood threshold. The discharges and the ratio stay, described for what
  they are; official flood warnings are in `/v1/vigilances`. The embedded frame and the Home Assistant integration did
  not use this level.

### Added

- **Active fires, from NASA FIRMS** (`nasa_firms`), **with no key**. The detections of the VIIRS instruments of NOAA-20 and NOAA-21
  (375 m pixels, near real-time product) are read from the **static world files** of FIRMS (the last 24 or 48 hours of each source,
  `VIGIE_FIRMS_WINDOW_HOURS`, 48 by default), checked every 60 minutes when someone asks (`VIGIE_FIRMS_REFRESH_MINUTES`,
  `VIGIE_FIRMS_SOURCES`), kept as typed columns and served without ever making a request wait for FIRMS once something was read. On by
  default, off with `VIGIE_ENABLE_FIRMS=0`. A free key (`VIGIE_FIRMS_MAP_KEY`) is optional: it only lets a source whose file could not be
  read be read from the area API instead.
  - `GET /v1/fires?bbox=w,s,e,n`: a GeoJSON FeatureCollection of the detections of a box and of the last hours (24 by default, up to the
    window of the files, a 422 beyond), with the fields of the FIRMS file; `min_confidence`, `min_frp`, `limit` (1,000 by default,
    5,000 at most, the strongest first, `metadata.truncated` when cut). A west greater than the east crosses the antimeridian.
  - `GET /v1/fires/grid`: the same detections counted per cell of 0.25 to 10 degrees (`count`, `max_frp_mw`, `latest_utc`), for small
    zooms.
  - `fires_nearby` on `GET /v1/point` (`fire_radius_km`, 50 by default): count in 24 hours, nearest distance, highest power, named in
    `derived`, never part of `local_alert_level`.
  - The fires are not in `GET /v1/geojson/hazards`. A detection is a satellite pixel, not an alert and not a confirmed fire; every
    answer carries NASA's acknowledgment and disclaimer word for word. With `VIGIE_ENABLE_FIRMS=0` the routes answer 404, `fires_nearby`
    is null and `GET /v1/health` lists the source in `disabled_sources`.
  - **A server that stalls does not stop the answers.** FIRMS stalls (14 % of 160 checks sent from one PC over four hours got no answer
    in 40 seconds). A read ends when no byte comes for 25 seconds or after 90 in all; a download cut in the middle is continued with
    `Range`; a source that failed is asked again after 1, 2, 5, then every 10 minutes, it alone, while its last reading keeps being
    served with `stale` true. `metadata.sources[]` says for each source when it was confirmed current (`read_utc`, `null` while never
    read), `ok`, `via` (`static` or `area`) and `published_utc` (when FIRMS wrote the file); `GET /v1/providers` gives the error of its
    last read.
  - **Few requests and bytes.** The static files have an `ETag` (the MD5 of the whole content) and `Last-Modified`, are rewritten once an
    hour and only grow within a UTC day. A check is one request closed after its headers when the file is the one held; a file that grew
    is read for the added bytes only (`Range`), and accepted only if the old bytes plus the new ones have the announced `ETag`, else read
    whole. A day with 1,000 clients is about **86 requests and 56 MB, no key and no transaction** by simulation (36 MB with the files of
    24 hours), against **60 reads, 2,160 transactions and about 170 MB** with the area API of the first version
    ([ADR 0007](docs/decisions/0007-active-fires-from-nasa-firms.md), `docs/data-sources.md`).
  - **A loss, said:** the static files hold the near real-time version only, so the ultra real-time detections of the United States and
    Canada (within minutes) that the area API returns are not read.
  - **The key, when used, is in the URL of the area API requests and is never shown**: it is redacted in every error, in `last_error` and
    in the logs of `httpx`, and `tests/test_firms_secret.py` looks for it everywhere.
  - `tools/check_live_feeds.py` reads one static file of FIRMS (8 to 17 MB, no key); with the key it also reads one world day of the area
    API.
  - Tests: the fire API tests (`tests/test_fires_api.py`) no longer leave the shared collectors of the application failing, which had made
    eight tests of the watched points fail when the whole suite ran.
- **What a hazard is close to: `near_places`** on every hazard of `/v1/geojson/hazards`, `/v1/history`, the stream and the full profile of
  `/v1/snapshot` and `/v1/point` (`compact=false`): `[{name, kind, distance_km, sources}]`, nearest first, at most 5, named in the new `derived`
  list of the hazard and credited in its `attribution`. **Calculated by Vigie, never a severity.** Two sets of reference places: the **named
  active faults** of the GEM Global Active Faults Database (earthquakes and volcanic events, at most 70 km deep, within 10 km) and the
  **volcanoes that the hazard sources themselves report** (an earthquake within 20 km of a USGS elevated alert or a GDACS eruption; no list
  of the world's volcanoes is bundled, the Smithsonian one has an unclear licence). **The watched points are not in `near_places`**: they are
  personal and these outputs are shared with every client (see "Watched points" below). `[]` is "nothing is near", `null` is "not worked
  out" (a hazard that is compared with nothing, such as a storm or a flood; the fault file is unreadable; a simulated alert; the compact
  profile, which carries none to keep to 16 KiB, and so the stream). The radii are **settings, not sourced facts** (`VIGIE_NEAR_FAULT_KM`, `VIGIE_NEAR_FAULT_MAX_DEPTH_KM`,
  `VIGIE_NEAR_VOLCANO_KM`, `VIGIE_NEAR_MAX_PLACES`). The distance is to the mapped surface trace; it was checked against Vincenty on the
  ellipsoid to a dense sampling of the original traces: the M 7.7 earthquake of Panama (2026-10-09) is 4.3 km from the Azuero Fault Zone
  (4.19 km by the oracle), and over 656 real earthquakes of 30 days the two agree to -0.47 / +0.46 km. Cost: 31-36 ms and 4 MB to load,
  2.5 ms for the 56 hazards of a request.
- **The fault file** `app/data/gem-active-faults.geojson.gz` (189 KiB, 6,090 named traces of 13,696) is **CC BY-SA 4.0**, share-alike, with its
  notice (`app/data/gem-active-faults.NOTICE.md`, `app/data/README.md`); the code stays MIT. `tools/build_faults.py` downloads the pinned
  commit and refuses any other content. **The traces without a name are not kept** (7,598): the Mediterranean and Asia are mostly unnamed,
  and the North Anatolian Fault is not named in the file.
- **Watched points**: `GET/POST /v1/watched-points`, `DELETE /v1/watched-points/{id}` (a name, a kind `place` / `fault` / `volcano` / `custom`, a
  position, a radius; at most 50, `VIGIE_WATCHED_POINTS_MAX`; 409 beyond). **A watched point is personal: it is listed by these routes and their
  status only, never in the hazards that every client shares.** And **`GET /v1/watched-points/status?days=7`**: for each point the
  events within its radius (an earthquake by the time it happened, any other hazard by the last time it was seen active), and a `state`:
  `events`, `quiet` or `unknown`. **`quiet` is proved, not assumed**: every one of the 7 hazard sources must have answered at the last
  reading (an answer served from the cache after a failure does not count) and have been read without a gap longer than
  `VIGIE_QUIET_MAX_GAP_S` (15 minutes) since `quiet_since_utc`; an instance started an hour ago says so (`window_covered: false`) instead of
  "nothing in the last week"; otherwise `unknown` and `unreachable_sources`. The runs of readings are kept in SQLite (`source_runs`).
  The routes have no authentication, like `/v1/zones`, and the list and the status are readable by anyone who reaches the API.
- **`POST` and `DELETE /v1/watched-points` are off by default** (new setting `VIGIE_ENABLE_WATCHED_POINTS_WRITE`, `1` to enable, the pattern of
  `VIGIE_ENABLE_SIMULATION`): they answer 403 with a message and change nothing, before the body is read; the reads always work; `GET /v1/health`
  reports `watched_points_write_enabled`. **On a public instance these two routes must not be reachable from the Internet** (like `POST /v1/zones`
  and `POST /v1/simulate`): put the instance behind a reverse proxy that blocks them. Documented in both READMEs, the architecture page and the ADR.
- [ADR 0008](docs/decisions/0008-sensitive-points.md).
- **More countries, through the registry of the WMO Severe Weather Information Centre** (`wmo_swic_registry`, `wmo_swic`).
  The registry lists the national services that publish CAP warnings (140 services in 135 countries on 2026-10-20). For a
  country that has no source of its own, the API reads the service's own feed (and the WMO's copy of it, for a service on a
  national host), merges the two by CAP `sender` and `identifier`, follows `references` (an `Update` or `Cancel` removes what
  it replaces), reads each CAP document once, and answers `/v1/vigilances` with the issuer's words untouched in every
  language. The shapes are polygons, circles (drawn as 48-point polygons, cut at the date line) or codes only; a warning that
  names only codes (the Philippines, India) is counted in `warnings_not_matchable`, never placed. `status`, `level` and
  `phenomenon` are derived by tables (a colour in the issuer's parameter, else the CAP severity: `Minor` gives no level; the
  event words in English, Spanish, Portuguese and French) and listed in `derived`; anything else is `null`.
- **Only the terms that were read.** `VIGIE_WMO_SOURCES` is `verified` by default: the 7 services whose own terms page, or an
  open licence named in their own feed (CC BY 4.0), was read on 2026-10-20 (Algeria, Argentina, Barbados, Jamaica, Mexico, the
  Philippines, Spain). `all` reads every service that is not excluded (95 services in 94 countries; for an instance only its
  owner uses), `off` none. Hong Kong and China are never read. `SOURCES.md` has one row for each of the 28 services whose terms
  were looked at: the address read, the credit, what is not verified. A bare "public domain" line is not enough (15 of the 25
  feeds outside the WMO's bucket that answered carry it, four with site terms that restrict copying).
- **Spain is answered by AEMET** instead of MeteoAlarm: AEMET's feed had 34 items with polygons on 2026-10-20, MeteoAlarm's 216
  entries had none. MeteoAlarm stands in, flagged, if AEMET does not answer (the Germany rule). A second exception needs the
  same kind of evidence ([ADR 0006](docs/decisions/0006-wmo-registry-services.md)).
- **`GET /v1/vigilances/countries`**: the countries the instance serves with its settings, the sources that answer for each
  (the tsunami source under the United States and Canada included), and whether their licence is verified. A country nobody
  serves is a 404 that points to it. The borders of every country a service answers for (135) are in
  `app/data/countries.geojson.gz` (Natural Earth, public domain; a test checks it).
- **A service that does not answer is never "nothing in force"**: each service is its own line of `coverage` (`source: swic`,
  `source_id`; `ok`, `stale`, `unavailable`), a circuit per service keeps one bad feed from closing the others, and one that never
  answered is silent for the push (it ends nothing).
- **Sober on the national servers**: a service is asked at most every 5 minutes (6 with the scheduler's 90 s cycle: 240 a day),
  with `If-None-Match` / `If-Modified-Since`; a feed with no validator waits 5 minutes per 64 KiB it weighs, up to 30; items older
  than 14 days, more than 60 new documents a read, and error pages are not read; the WMO's copy (it exists for 1 of 260 addresses)
  is paused after three misses; a ceiling of 50,000 requests a day covers the set (`wmo_swic`, ours). A simulated day
  (`tests/test_swic_sobriety.py`): **2,010 requests and 10.6 MB** for the 7 verified services, **36,924 requests and 51 MB**
  for `all` with a followed point in each of the 94 countries.
- **The registry and a sample of its services in the weekly check** (`tools/check_live_feeds.py`): fails when the registry loses
  services, when a verified service's feed, country or licence line is gone, or when documents cannot be read.
- [ADR 0006](docs/decisions/0006-wmo-registry-services.md).
- **Tropical cyclones of the National Hurricane Center** (`nhc_storms`). Each active storm is a hazard that is a point (category
  `HURRICANE`, at its current position, the wind in km/h, a level from the Saffir-Simpson thresholds: 34 kt yellow, 64 orange,
  96 red), and `GET /v1/geojson/hazards/{id}/forecast` serves its **cone, track, forecast positions and coastal watches and
  warnings** from the ZIP of shapefiles of its latest advisory, as an RFC 7946 FeatureCollection (`properties.kind` `cone`,
  `track`, `position`, `watch_warning`). A strict shapefile reader (`app/shapefile.py`), coordinates rounded to 3 decimals,
  counter-clockwise rings, a cone across the date line cut in two; a pressure the NHC writes as 9999 is `null`. Hurricane
  Isaias's advisory 13A: 22.7 kB of GeoJSON. A bundle that cannot be read leaves the hazard without forecast and is asked for
  again after ten minutes, also when the status file has not changed.
- **Tsunami messages of the U.S. Tsunami Warning Centers** (`tsunami_warning_centers`: the NTWC and the PTWC). A message in
  force is a hazard that is a point (category `TSUNAMI`, at the epicentre) with a level from the centre's own words (warning
  red, advisory orange, watch yellow; *Tsunami >3.0 meters* red, *1-3 meters* or *potential threat* orange, *0.3-1 meters*
  yellow), a vigilance in `GET /v1/vigilances?source=tsunami` (and `country=US` or `CA` for the zones that name them) with the
  centre's words untouched, and a forecast route with the epicentre and the estimated arrival time at each place (`eta`,
  `eta_utc`). **An information statement is not an alert**: it is counted and never read, as are a cancellation, a test, a
  threat class the tables do not know, and the "final" message of an event (a CAP with no `info`). The list of the latest
  bulletins is read, not the per-centre file (it would hide a threat behind a later information statement). Tested on the
  real messages of the Panama earthquake of 2026-10-09, replayed as the list grew, and on the 2012 Haida Gwaii messages.
- **`has_forecast`** on each feature of `GET /v1/geojson/hazards`, and **`GET /v1/geojson/hazards/{id}/forecast`**: the
  forecast of a hazard (empty with a `reason` when it has none, 404 for an unknown id). The hazards stay points.
- **The storms and tsunamis in the weekly check** (`tools/check_live_feeds.py`): the list of bulletins and the CAP of the newest
  one, the events in force; the file of the active storms and, when one is active, its bundle and its cone.
- [ADR 0005](docs/decisions/0005-tsunamis-and-storms.md), `docs/data-sources.md` ("Storms and tsunamis"), `SOURCES.md` and both
  READMEs. Requests a day (simulated, `tests/test_storm_sobriety.py`): tsunami.gov 960 and the NHC 240, all but the first a `304` of
  239 and 484 bytes; 230 kB and 116 kB on a quiet day; the Panama day 253 kB, a day with a storm and an advisory every three hours 387 kB.
- **A Home Assistant integration** (`custom_components/vigie`, `hacs.json`), configured in the interface: the address of
  an instance (checked by `/v1/health`), the place (the home zone by default), several places allowed, and options for
  the poll interval (5 minutes, 1 to 60) and the river radius (5 km, 1 to 50). It gives a **maximum level** sensor
  (`none`, `yellow`, `orange`, `red`, with the warnings, the level of each phenomenon and the state of the sources in
  bounded attributes), a **warning active** binary sensor and a **vigilance** event entity that fires `new`, `upgraded`
  and `ended` by comparing each read with the one before (a warning is recognised by its source, phenomenon and area, not
  by its identifier, which MeteoAlarm changes at every update). **Unknown is never `none`**: no source for the place,
  every source unavailable, a warning of unreadable level, or warnings that cannot be placed on a map give an unknown
  state; an unreachable instance makes the entities unavailable. When the instance runs its scheduler, `WS /v1/stream`
  is followed too and a push makes the integration read again at once; the poll stays as the fallback and the whole
  mechanism otherwise. English and French, diagnostics with the coordinates redacted. See
  [`docs/home-assistant.md`](docs/home-assistant.md) and [ADR 0004](docs/decisions/0004-home-assistant-integration-same-repository.md).
  Tried on a running Home Assistant 2026.10.2 on 2026-10-20: the entities against a real instance, `new`, `upgraded` and
  `ended` from the fake instance, and the entities unavailable when the instance stops answering.
- **`tests_ha/`**: 132 tests of the integration (`pytest-homeassistant-custom-component` 0.13.373, which pins Home
  Assistant 2026.10.2 and needs Python 3.14.2 or newer, in an environment of its own), and
  `tests/test_home_assistant_contract.py` in the API's CI, which checks that the answers they use (a real recorded one,
  the builders, the fake instance) are answers of the API's own models.
- **`.github/workflows/home-assistant.yml`**: the integration's tests, hassfest and the HACS validation (skipped while the
  repository is private: the action refuses it), only when the integration changes and weekly. `ci.yml` is unchanged. The two actions that have no moving version tag are pinned to a
  commit (`test_every_action_is_pinned_to_a_version_or_a_commit`).
- **`tools/fake_instance.py`**: a fake instance (standard library only) that serves `/v1/health` and `/v1/vigilances`
  with warnings added and removed by hand, all labelled `SIMULATION`, to try the integration's entities and events.
  `POST /v1/simulate` cannot do it: it injects hazards, not official warnings.

### Changed

- **One storm or one tsunami is one hazard, whoever reports it.** A hazard can carry a private event key: a storm's name and season
  (the NHC and GDACS, whose event name ends with the year) or the VTEC code of a tsunami message (the centre and the NWS alert
  that relays it). Two hazards with a key are one event when the keys are equal and two when they are not, whatever their times
  and positions (GDACS dates a storm by its first day, the NWS places a tsunami warning in a zone); without a key the rule is
  unchanged (120 s and 100 km). The source that observes the event (the NHC, the tsunami centres, USGS) gives the title and the
  position, and for a keyed event **its level and wind, not the highest of the two**: GDACS's level is the storm's peak. An NWS
  alert that carries the VTEC code of a tsunami message is left out of the warnings of the same answer.
- `GET /v1/vigilances` takes `source=tsunami` (asked alone) and `query` has a `source` field for it only; the `source` of a
  vigilance can be `tsunami`; a position in the United States or Canada counts the tsunami source in `coverage` (its zones are not
  matched to a point).
- **Vigicrues is read at its publications, not on a timer: 310 MB a day become 9 MB.** The flow is 2.2 MB, not
  compressed, without `ETag` or `Last-Modified`, ignores `Range` and takes no filter, and no lighter endpoint carries the
  level (measured on 2026-10-20). The collector now asks, with a `HEAD` of about 330 bytes, the national map image that
  the same publication regenerates (`Last-Modified` 21 seconds after the flow), every 15 minutes (5 while a section is
  orange or red), and reads the flow only when the image changed, or after 12 hours. On the simulated day of the five
  default zones: 4 reads and 9.1 MB while a section is orange (before: 138 reads, 310 MB), 4 reads and 9.0 MB on a calm
  day (before: 46 reads, 103 MB); 3 reads and 6.8 MB once started. A flood published at 4 p.m. is served at the next look
  (16.5 minutes at most when calm, 6 in a flood). The image is an inference from the site, not a contract: the weekly check fails if it has no
  validator or no longer follows the flow, and without a usable image the flow is read every hour (20 minutes in a flood).
- **`VIGIE_ENABLE_METSERVICE`** (default on, also in the Docker image): `0` stops asking MetService. Its feed says CC BY
  4.0 under a Code of Practice, but the website terms it points to allow personal use only and forbid resupply, and
  which one governs is not verified: a public instance can switch it off. A position in New Zealand then gets no source
  and the answer says why; the source is no longer in `GET /v1/providers`.
- **Push of the official warnings, source by source.** A source of a followed position that does not answer no longer
  silences the others (before, the whole position was left as it was): what it had said stands and ends nothing; a
  source that answers for the first time after the baseline sets its own baseline. The credit of an answer names only
  the sources that answered.
- **README rewritten in a modest tone** ("a personal project, made with AI, shared in case it helps", no
  promise), English first, with a French version (`README.fr.md`) linked from the top. It says that the
  code is MIT and the data is not, carries the notice that this is not an official warning service and that
  GDACS alerts are automatic and do not replace official warnings, and adds Docker, the new settings and the
  CI workflows. The address of a home network and the author's folder names are replaced by
  placeholders (`<PC address>`, `<repository folder>`); the Windows start script, firewall rule and Task
  Scheduler instructions are unchanged otherwise (`-LogFile`, `-Port`, `-NoInstall` still work).
  `tests/test_public_text.py` keeps promises, marketing words and private details out of the public texts.
- **The simulator is opt-in.** `POST /v1/simulate` has no authentication and writes simulated alerts into
  the hazard history, so it now answers 403 unless the instance sets `VIGIE_ENABLE_SIMULATION=1`
  (default off). `GET /v1/health` reports `simulation_enabled`; the dashboard hides the simulation buttons
  when it is off. Nothing else uses the route.
- **Call sobriety, point grids.** The position sent to Open-Meteo (forecast, air quality, flood,
  marine) and to MET Norway, and the cache key, are snapped to a grid: 0.05 degree for the forecast
  and MET Norway, 0.1 degree for air quality, flood and marine (`VIGIE_GRID_FORECAST_DEG`,
  `VIGIE_GRID_AIR_QUALITY_DEG`, `VIGIE_GRID_FLOOD_DEG`, `VIGIE_GRID_MARINE_DEG`). It used to be
  0.01 degree, finer than the weather models. The response still reports the exact position and the
  distances to the hazards still use it.
- **Call sobriety, cache key without `days`.** The forecast is always requested for 10 days and
  cached once per position; the compact profiles slice it. A frame (5 days) and a full page
  (10 days) used to cost one Open-Meteo call each per position. `fetch_weather_with_fallback` no
  longer takes `days`.
- **Call sobriety, call budget.** Each collector counts the requests it really sends, and their
  weight for Open-Meteo, over 24 hours; `GET /v1/providers` (and `/v1/health`) report `calls_24h`,
  `weight_24h`, `quota` and `budget_refusals`. A provider with a published quota (Open-Meteo, MET
  Norway, RainViewer; `app/collectors/budget.py`) is never sent a request that would exceed 90 % of
  it (`VIGIE_BUDGET_FRACTION`): its expired cache is served instead and marked stale (`(stale cache)`
  in the weather source, `stale` on `air_quality`, `flood_risk` and `marine`). The four Open-Meteo
  APIs share one budget.
- **Call sobriety, weight of the Open-Meteo calls.** Open-Meteo counts a request of more than 10
  variables as several calls; the forecast (18 variables, weight 1.8) is now two requests: the
  current conditions and the 15-minute rain (weight 1.1, every 15 minutes) and the daily forecast
  (weight 1.0, every hour). No variable is dropped. The weight of each request is computed from the
  published rule (`app/collectors/weights.py`).
- **Call sobriety, TTLs from the sources' rhythm.** Open-Meteo forecast 5 to 15 minutes, air quality
  10 to 60 minutes, marine 5 to 60 minutes, river discharge 10 minutes to 6 hours, RainViewer 3 to
  10 minutes (the evidence and what is **not verified** are in `docs/data-sources.md`).
  MET Norway is now asked as its terms require: `If-Modified-Since` with the previous
  `Last-Modified`, `304 Not Modified` handled, the answer kept until its `Expires` header.
- **Call sobriety, request coalescing.** Simultaneous requests for the same cache key now send one
  upstream request (they used to send one each, and each failure counted in the circuit breaker).
- **Embedded frame contract** ([`docs/embedded-frame.md`](docs/embedded-frame.md)), same column
  order and counts:
  - `DAY` field 2 is the ISO date (`2026-10-20`) instead of a French weekday abbreviation.
  - `DAY` field 7 is the real daily maximum precipitation probability (Open-Meteo
    `precipitation_probability_max`); field 9 is that day's maximum UV, no longer the current UV
    repeated.
  - Unknown values are empty fields. A zone without weather has `ZONE` fields 6 to 23 empty;
    `HEAD` fields 2 and 6 to 11 can be empty (Kp unknown, no hazard source answered).
  - `RAIN5M` repeats each 15-minute rate three times instead of smoothing it; the GPS zone has
    its own forecast and local UTC offset and is named `GPS position`.
- The documentation, comments, log and error messages and the dashboard are in English; the
  dashboard drops the "DEFCON" wording and shows unknown values as `n/a`.
- `DailyForecast.d` (weekday) is replaced by `date` (ISO 8601); new `rain_probability_pct` and
  `uv_max`. All measurement fields of the weather, marine, air quality and space weather models
  are optional (`null` = unknown). `GET /v1/point` has a `daily` forecast and `utc_offset_s`.
- `POST /v1/zones` requires `utc_offset_s` (there is no silent default of zero).
- Open-Meteo is asked for the sea-level pressure (`pressure_msl`) and the current UV index.
- The RainViewer radar frame is the latest **observed** one (past frames only); the free
  RainViewer API has had no nowcast since 2026-01-01. The short-term rain of the frame comes only
  from Open-Meteo `minutely_15` (interpolated from hourly data outside Central Europe and North
  America, as Open-Meteo documents).
- The CAP `area.circle` radius is `0` (the affected radius is not known), the CAP `language` is
  `en-US`, `sender` is `vigie-api` or `VIGIE_CAP_SENDER`, and `web` is no longer set.
- Default database file is `vigie.db` (an existing `vigie_mondiale.db` is still used).
- The background scheduler starts only with `VIGIE_SCHEDULER=1` (it was never started before).
- Hazard sources that return no magnitude (NOAA NWS alerts, volcanoes without a VEI) keep the
  value unknown; the hazard history column is nullable (older databases are migrated).

### Fixed

- **GDACS sent nothing.** Its `MAP` endpoint now needs one request per event type and answered
  HTTP 400 ("Eventtype is required.") to the request without one, so no cyclone, flood or wildfire
  reached the API. The collector now uses the documented search endpoint: one request for every
  type, the orange and red events of the last 30 days that GDACS still marks current (green events,
  low humanitarian impact, are no longer collected); an empty answer (HTTP 204) means no event.
- No fabricated data. Removed the invented offline weather ("MET Norway / Offline Safety Cache"
  computed from the latitude), the air quality index fixed at `22` / `20`, the Kp index defaulting
  to `2.0` (the live NOAA file was never read correctly), the default temperature, humidity,
  pressure, wind, minimum and maximum temperatures, the gusts and UV estimated for MET Norway,
  the zero rain forecast, the invented marine conditions, the invented radar timestamp, the
  invented NOAA wind speeds and magnitudes, the volcano VEI derived from a colour code, and the
  default depth of 10 km.
- MET Norway: the daily weather code comes from each day's forecast symbols and days are
  grouped in the zone's local days.
- The GPS zone of the frame no longer repeats the first watched zone's forecast.
- The USGS volcano feed was never parsed (its real fields are `vName`, `long`, `colorCode`,
  `sentUtc`).
- Hazards are no longer reported as "all clear" (`GREEN`) when no hazard source answered.
- The dashboard escapes the text coming from the providers.
- Started with the latest FastAPI, the application crashed on the removed `openapi_version`
  argument.

### Added

- **Official weather warnings** (`GET /v1/vigilances?lat=&lon=`, `?country=FR`, `GET /v1/vigilances.geojson`). The warnings of
  the national services, with the issuer's words untouched: MeteoAlarm (EUMETNET) for 39 European countries (the Atom
  feed of a country, and the CAP document of a warning for its texts in every language) and the NOAA NWS alerts, read
  from the answer the hazards already ask for (no second request). One `Vigilance` model for both: identifier, times,
  status, level (GREEN, YELLOW, ORANGE, RED), phenomenon (wind, rain, thunderstorm, snow-ice, heat, cold, flood,
  avalanche, coastal, fire, fog, other), area, link. The three fields the API derives (`status`, `level`,
  `phenomenon`) are named in `derived` and sit beside the source's own words (`level_original`,
  `phenomenon_original`, `event`, `texts`); a word no table covers is `null`. Every warning carries its credit, and the
  answer carries the notice MeteoAlarm obliges redistributors to publish. The tables, the licences and what is not
  verified are in `docs/data-sources.md` and `SOURCES.md`; the embedded frame does not change.
- **Localization of a warning to a point** with shapely: the polygon of the source, or the shape of the code of its
  area. Two small files are bundled (`app/data/`, 381 KiB): the borders of the 42 countries (Natural Earth, public
  domain) to find the country of a point offline, and the French departments and Belgian provinces (Natural Earth,
  public domain, with NUTS codes from Wikidata, CC0). `tools/build_geometries.py` rebuilds them. The MeteoAlarm
  `EMMA_ID` shapes and the Eurostat NUTS shapes are **not** bundled (no licence stated, and non-commercial use only):
  the warnings that name their area with those codes are listed by country but not matched to a point, and the answer
  says so (`coverage`, `area.localization`). `VIGIE_EMMA_GEOMETRIES` lets an operator add his own file.
- **Push of the changes of the official warnings** on `WS /v1/stream`: `{"action": "follow", "lat": .., "lon": ..}`
  answers with the current warnings (`vigilance_state`), then the scheduler pushes `vigilance_push` messages when a
  warning appears, goes up or ends for the followed point. The watched zones are followed too.
- **`meteoalarm_atom` and `meteoalarm_cap` collectors** in `GET /v1/providers`, counted like the others (no published
  quota). Only the feeds of the needed countries are asked, cached 180 s; a CAP document once, only for a warning an
  answer returns. The weekly live-feeds check covers MeteoAlarm and the NWS warnings.
- **National warning services: the DWD (Germany), Environment and Climate Change Canada, MetService (New Zealand).**
  Three collectors (`dwd_cap`, `eccc_alerts`, `metservice_cap` in `GET /v1/providers`) that read the national service
  itself, with its polygons and its words untouched (the DWD in eight languages, ECCC in English and French).
  **The national source answers for its country and MeteoAlarm's copy of it is not asked for** (Germany); MeteoAlarm
  stands in only if the DWD does not answer, and the answer says so. Germany's warnings can now be matched to a
  position. The DWD is read as its status ZIP by district (one conditional request, `304` most of the time, cached
  120 s); ECCC through MSC GeoMet (one request for all of Canada, cached 300 s; an alert it still lists after it ended, `status_en`
  `ended`, is not served) and not through the Datamart, whose
  usage policy forbids what finding new CAP files there would need; MetService through its RSS feed (cached 300 s,
  conditional), **whose items have not been seen yet** (no warning in force on 2026-10-20) but whose format MetService
  documents in its CAP user guide: the level is the `ColourCode` parameter, the phenomenon the `<event>` word, and the
  tests use a feed written after the guide's examples, labelled synthetic. Credit lines: `Source: Deutscher Wetterdienst`, the
  `Data Source: Environment and Climate Change Canada` line of the ECCC licence, and a CC BY 4.0 line for MetService.
  ECCC's usage-policy line of 86,400 requests a day is in the quotas. Canada and New Zealand are added to the bundled
  borders (196 to 335 KiB). `tests/test_national_sobriety.py` counts the requests of a simulated day.
- **Vigicrues, the river flood vigilance of France** (`vigicrues_flow`). One request reads the colour (yellow,
  orange, red) of the 337 sections of river (Licence Ouverte 2.0, credit and date of the last update in the warning);
  the flow offers no validator, is not compressed and weighs 2.2 MB, so it is read on its publications only (see
  "Vigicrues is read at its publications" below). A section
  is a **line**: `GET /v1/vigilances` has a `river_radius_km` (5 km by default, 50 at most) and returns the
  `distance_km` of each section it matches. A MeteoAlarm "Flooding" warning of a département is left out of an answer
  when a Vigicrues section of the same answer crosses it at the same level or higher ("Rain-flood" always stays),
  and the pushes follow the same rule. The official bulletins are linked, not relayed.
- **The checks of the weekly live-feeds run** cover `dwd_cap`, `eccc_alerts`, `metservice_cap` and `vigicrues_flow`: a
  severity, an event number, a colour or a status word the tables do not know, a polygon missing, a MetService item
  that cannot be read or has no `ColourCode` or an event word the guide does not list, or a flow cut short makes the
  run red.
- **Credit of the source in every response.** Each block of data carries an `attribution` field with the
  text its source asks for: hazard alerts (`/v1/snapshot`, `/v1/point`, `/v1/history`, GeoJSON features and
  `metadata`, CAP parameters and description, WebSocket pushes), the zones of the snapshot, and the
  `rain_nowcast`, `marine`, `flood_risk`, `air_quality` and `space_weather` blocks of `/v1/point`;
  `weather_attribution` (`/v1/point`) and `global_status.space_weather_attribution` (snapshot) credit the
  fields of a response that mixes blocks. The texts are declared once, in `app/attribution.py`, and the
  dashboard ends with them. A GDACS alert is credited with "Global Disaster Alert and Coordination System,
  GDACS" and the notice that its alerts are automatic and do not replace official warnings; the
  dashboard and the OpenAPI description carry the notice too. A block with no data has `attribution: null`;
  a simulated alert or block is credited to no provider. The compact frame is unchanged: it has no credit
  field. The compact snapshot is larger by about 2.5 KB with 15 hazards and 5 zones (measured on live data:
  12.3 KB before, 14.8 KB after): **its documented budget is raised from 12 KiB to 16 KiB**, with the credit
  kept on every item (the display reads only the frame, whose 8 KiB budget is unchanged).
  `tests/test_snapshot_budget.py` builds a compact snapshot from realistic samples of the five hazard sources
  (15 hazards, 5 zones with weather and air quality) and keeps every device profile under 16 KiB. About 18
  hazards fit; the profiles carry 15 at most (12 for `marine`), but the `web` profile, whose default limit is
  50 hazards, is not bound by the budget.
- **`SOURCES.md`**, the single list of the licence, terms, required credit, quota and clauses of each
  source, with what is **not verified** (checked on 2026-10-20). `docs/data-sources.md` keeps how each
  source is used (what is read, TTL, call volume, budget) and no longer repeats the terms. A test checks
  that `SOURCES.md` quotes the credit texts of the code and has a row for every collector.
- **Secret scan of the whole history.** `.github/workflows/secret-scan.yml` runs gitleaks 8.30.1 (the
  release binary, SHA-256 verified, no third-party action) over every commit of every branch on each pull
  request and push to `main`.
- **Weekly live-feeds check.** `tools/check_live_feeds.py` asks each of the 12 real providers once, with the
  real collectors, and checks that the parsers still read the answer (at least one item parsed, or a valid
  empty answer such as GDACS's HTTP 204); `.github/workflows/live-feeds.yml` runs it every Monday and on
  demand, never in a pull request. A format change or a removed endpoint fails the run; a provider that
  does not answer (timeout, HTTP 403 / 429 / 5xx) is only reported. The tests stay offline
  (`tests/test_live_feeds_tool.py` plays the tool against simulated providers).
- **Docker.** `Dockerfile` (`python:3.13-slim`, non-root user, `/data` volume, health check on
  `/v1/health`, `uvicorn app.main:app --host 0.0.0.0 --port 8080`; RainViewer off in the image),
  `.dockerignore`, and a `docker` job in `ci.yml` that builds the image, runs it and asks `/v1/health`
  and `/v1/embedded/frame` (`scripts/docker_smoke.sh`). `.github/workflows/publish-image.yml` pushes
  `ghcr.io/axellum/vigie-api` only when a `v*` tag is pushed, after the same smoke test.
- `LICENSE` (MIT, code only: the data belongs to each provider under its own terms).
- `VIGIE_ENABLE_RAINVIEWER` (default on, unchanged): `0` stops asking RainViewer, whose free API is for
  personal and educational use only. `/v1/point` then leaves the radar fields unknown and `/v1/providers`
  lists 11 providers.
- `tests/test_call_sobriety.py` and `tests/simulation.py`: a simulated day (1,000 clients on 50
  points, the scheduler on, a simulated upstream and clock, no network) that counts the requests
  each provider receives and checks the Open-Meteo quota; `python -m tests.simulation` prints the
  table. Open-Meteo, per day: 32,732 calls (weight 46,502) before, 7,927 calls (weight 8,412) after.
- `docs/` : architecture, frame contract, data sources and terms, decision records.
- `tests/test_embedded_frame_contract.py` pins the frame contract; no test reaches the network.
- Air quality (EAQI) for every watched zone.
- `scripts/run.ps1` (Windows start script), `tools/capture_frame.py` (capture a real frame),
  a GitHub Actions workflow (ruff and pytest on Python 3.12 and 3.13), ruff configuration.
- Environment variables `VIGIE_CONTACT`, `VIGIE_CAP_SENDER`, `VIGIE_SCHEDULER`.

### Removed

- The French work kit `docs/travail/` and the audit `docs/audits/`.
