# <img src="docs/logo.svg" alt="" width="40" height="40" align="top"> Vigie API

*[Lire ce document en français](README.fr.md)*

**Live demo: [vigie.auxfilsdesidees.fr](https://vigie.auxfilsdesidees.fr)**, the Vigie website, runs on this API
([vigie-web](https://github.com/Axellum/vigie-web)).

A personal project, made with the help of AI and shared in case it helps someone. It comes as is, with
no promise of support, availability or accuracy.

It gathers world natural hazards (earthquakes, tsunamis, volcanoes, cyclones, tornadoes, floods, fires,
geomagnetic storms), weather (current conditions, 15-minute rain, forecast, marine, river discharge,
air quality) and the official weather warnings of the national services (MeteoAlarm in Europe, the Deutscher
Wetterdienst in Germany, Environment and Climate Change Canada, MetService in New Zealand, the National Weather
Service in the United States, and Vigicrues for the rivers of France) from public sources, and serves them to:

- **a standalone weather and alert screen**, no Home Assistant needed: an ESP32-P4 display fetches a compact text
  frame ([`docs/embedded-frame.md`](docs/embedded-frame.md)), see
  [vigie-display](https://github.com/Axellum/vigie-display) (Waveshare 7B);
- **a home automation screen tied to Home Assistant**, such as
  [M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL) (M5Stack Tab5, Waveshare 7B);
- **Home Assistant itself**, through its integration (`custom_components/vigie`, see [Home Assistant](#home-assistant)):
  sensors and events to start automations;
- any other client, website or script.

It sticks to the international standards: GeoJSON (IETF RFC 7946), OASIS CAP 1.2 for the warnings, the levels and
words of the issuing services left as they are, the national services listed in the WMO registry, UTC timestamps.

> **This is not an official warning service.** The alerts are relayed from public feeds. They can be
> late, incomplete or wrong, and must not be the only thing a decision rests on. **GDACS alerts are
> automatic and do not replace official warnings.** Follow the warnings of your national authorities.

> **No fabricated data.** When a provider does not supply a value, the API reports it as unknown
> (`null` in JSON, an empty field in the frame). It never shows a plausible default. See
> [ADR 0001](docs/decisions/0001-two-repos-no-fabricated-data-frame-format.md).

## Licence, data and credits

The code is under the [MIT licence](LICENSE). **The data is not covered by it**: it belongs to each
provider and stays under that provider's own terms. [`SOURCES.md`](SOURCES.md) lists, for each source,
the licence, the terms, the credit it asks for, its quota, and what could not be verified.

- Every block of data in the JSON responses carries an `attribution` field with the credit its source
  asks for (the compact frame has no such field). The texts are declared in
  [`app/attribution.py`](app/attribution.py).
- Whether each source's terms allow **commercial use** (for the paid app to come: the website and this API stay
  free) is declared once in [`app/commercial.py`](app/commercial.py) and listed in
  [`SOURCES.md`](SOURCES.md#commercial-use-for-the-paid-app-to-come): allowed, forbidden or not verified. A new source
  cannot be added without its line (a test fails).
- **Open-Meteo's free API may only be used for non-commercial purposes**
  ([terms](https://open-meteo.com/en/terms)), within 10,000 calls a day, with a link to Open-Meteo wherever
  its data is shown. This project is personal and non-commercial. **Do not use it commercially without
  a paid Open-Meteo plan**, and check the terms of the other providers.
- RainViewer's free API is for personal and educational use only. Turn it off on an instance that relays
  its data to others (`VIGIE_ENABLE_RAINVIEWER=0`; the Docker image does).
- MetService (New Zealand) publishes its warnings as CC BY 4.0 under a Code of Practice, but the website terms it points
  to allow personal use only and forbid resupply. Fine for one home; ask MetService before relaying its warnings to
  other people, or turn it off on an instance that serves them (`VIGIE_ENABLE_METSERVICE=0`; on by default, the Docker
  image keeps it on) ([`SOURCES.md`](SOURCES.md)).
- The file of named active faults (`app/data/gem-active-faults.geojson.gz`, used for `near_places`) is **CC BY-SA 4.0**
  (GEM Global Active Faults Database, Styron and Pagani 2020), not MIT: share-alike, with its notice beside it
  ([`app/data/README.md`](app/data/README.md), [`SOURCES.md`](SOURCES.md)).
- The national services of the WMO registry (`VIGIE_WMO_SOURCES`): the terms of each issuer count, and most were not found or
  restrict copying. The default reads only the 7 whose own terms were read and allow relaying them; `all` is for an instance
  that only its owner uses. Hong Kong and China are never read ([`SOURCES.md`](SOURCES.md)).

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/embedded/frame` | Compact text frame for the displays ([contract](docs/embedded-frame.md)). |
| `GET /v1/snapshot` | Watched zones (weather) and world hazards, compact JSON. |
| `GET /v1/point?lat=&lon=` | Weather at one GPS position, marine (`marine=true`), river discharge, air quality, space weather, nearby hazards. |
| `GET /v1/geojson/hazards` | Active hazards as GeoJSON (RFC 7946): one point each. `has_forecast` says whether a storm or a tsunami has a forecast; `near_places` and `derived` say what an earthquake or a volcanic event is close to (calculated by Vigie). See "Places near a hazard" below. |
| `GET /v1/geojson/hazards/{id}/forecast` | The forecast of a hazard as GeoJSON: the cone, track and positions of a tropical cyclone, the epicentre and arrival times of a tsunami. See "Storms and tsunamis" below. |
| `GET /v1/earthquakes?lat=&lon=` | The earthquakes (USGS and EMSC, merged) within `radius_km` of a position over the last `hours`, the strongest first, with their distance; `min_magnitude` filters them. No weather provider is asked. Not an official warning. |
| `GET /v1/fires?bbox=w,s,e,n`, `GET /v1/fires/grid` | The active fires seen by NASA FIRMS (VIIRS, NOAA-20 and NOAA-21): the detections of a box as GeoJSON, or counted per cell for small zooms. No key needed (404 only on an instance that turned the source off with `VIGIE_ENABLE_FIRMS=0`). See "Active fires" below. |
| `GET /v1/cap/alerts` | Active hazards as OASIS CAP 1.2. |
| `GET /v1/vigilances?lat=&lon=` or `?country=FR` or `?source=tsunami` | Official weather warnings that cover a point, or all those of a country (MeteoAlarm, DWD, ECCC, MetService, NWS, Vigicrues, and the national services of the WMO registry), or the tsunami messages in force, the issuer's words untouched. See below. |
| `GET /v1/vigilances/countries` | The countries this instance serves with its settings, the sources that answer for each, and whether their licence is verified. With `?uncovered=true`, every other country of the world too, each with the reason nothing is read for it. |
| `GET /v1/vigilances.geojson` | The same warnings as GeoJSON, the shape of each area as geometry. |
| `GET /v1/vigilances/archive`, `GET /v1/vigilances/archive/stats` | Every official warning this instance saw, kept as it was served, filtered by country, area, source, phenomenon, level and period, with the hours each source was read; and what the archive weighs per day. See "The archive of the official warnings" below. |
| `GET/POST /v1/zones`, `DELETE /v1/zones/{id}` | Watched zones (the watchlist). A zone needs its `utc_offset_s`. |
| `GET/POST /v1/watched-points`, `DELETE /v1/watched-points/{id}` | The points you watch (a place, a fault, a volcano...), each with a radius. **Personal**: listed here and in the status, never in the hazards. `POST` and `DELETE` are **off** (403) unless `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1`. |
| `GET /v1/watched-points/status?days=7` | For each watched point, the events within its radius over the last days, and `quiet` only when every hazard source answered and was read without a gap. |
| `GET /v1/history` | Hazard history stored in SQLite. |
| `GET /v1/health`, `GET /v1/providers` | Health, circuit breaker and cache state of each provider, and its calls, weight and quota over 24 hours. |
| `GET/POST /v1/scheduler/...` | State of the optional background scheduler, run one cycle now. |
| `POST /v1/simulate` | Inject a **simulated** scenario for testing (always labelled as a simulation). **Off by default**: it answers 403 unless `VIGIE_ENABLE_SIMULATION=1`. |
| `WS /v1/stream` | WebSocket with the initial state and alert pushes; a client can also follow a point and be told when its official warnings change. |
| `GET /dashboard` | Web supervision page. |
| `GET /docs` | Interactive OpenAPI documentation. |

**The API has no authentication by default.** Run it on a trusted network. Do not expose it to the Internet as is:
anyone who can reach it can add or delete watched zones and watched points and trigger collection cycles. Put a reverse proxy
that authenticates in front of it if you must. **`VIGIE_ADMIN_TOKEN`** is a first step: when it is set, every route that changes something
(`POST`, `PUT`, `PATCH`, `DELETE`: today `/v1/zones`, `/v1/watched-points`, `/v1/scheduler/trigger` and `/v1/simulate`) needs
`Authorization: Bearer <token>` and answers 401 without it; the reads never need it. Not set, nothing changes. The token is a shared secret in
a header: put TLS in front of an instance that is not on a trusted network ([`docs/architecture.md`](docs/architecture.md), [ADR 0009](docs/decisions/0009-every-border-a-reason-per-country-and-the-admin-token.md)).

**On a public instance, `POST` and `DELETE` of `/v1/watched-points` must not be reachable from the Internet**, like `POST /v1/zones` and
`POST /v1/simulate`: put the instance behind a reverse proxy that blocks them (the reads can stay open). Because a watched point is personal
(a home, for example), these two routes are also **off by default**: they answer 403 until the instance sets `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1`
(`GET /v1/health` reports `watched_points_write_enabled`), which is for an instance that trusted clients alone can reach.

## Official weather warnings

`GET /v1/vigilances` gives the warnings that the national weather services publish: **MeteoAlarm** (EUMETNET) for
39 European countries, the **Deutscher Wetterdienst** for Germany, **Environment and Climate Change Canada**,
**MetService** for New Zealand, the **National Weather Service** for the United States, and **Vigicrues** for the
river floods of France, and, for more countries, the national services of the WMO registry (below). It relays them; it does not issue any. The words of the issuer (headline, description and
instruction, in every language it wrote) are returned untouched.

**One source answers for a country.** Germany is the DWD (MeteoAlarm's copy of the German warnings is not asked for,
unless the DWD gives no answer); Canada and New Zealand have their own service; the other European countries are
MeteoAlarm's. France has two, which say two things: MeteoAlarm a colour per département, Vigicrues a colour per
**section of river**. A MeteoAlarm "flooding" warning that a Vigicrues section of the same answer says at the same
level or higher is left out; otherwise it stays ([`docs/data-sources.md`](docs/data-sources.md)).

```
GET /v1/vigilances?lat=44.9334&lon=4.8924      the warnings whose area contains this exact point
GET /v1/vigilances?country=FR&lang=fr          every warning of a country, the texts in French only
GET /v1/vigilances.geojson?lat=..&lon=..       the same, as GeoJSON (the shape of each area is the geometry)
GET /v1/vigilances?lat=..&lon=..&river_radius_km=10   how far a river section still counts (default 5, at most 50)
```

Real answer for a point in the Drôme (France), cut: the feeds of 2026-10-20 played back by the tests.

```json
{
  "query": {"lat": 44.9334, "lon": 4.8924, "country": null, "river_radius_km": 5.0},
  "countries": ["FR"],
  "vigilances": [{
    "id": "meteoalarm:b77d3bc0-76a2-4886-8da8-bac8d99f57fc:6-0-1",
    "source": "meteoalarm", "country": "FR",
    "attribution": "Source: EUMETNET – MeteoAlarm (https://meteoalarm.org/en/live/), ... | Issued by METEO-FRANCE",
    "identifier": "2.49.0.0.250.1.FR.20261020114209.041024",
    "sent_utc": 1792489329, "onset_utc": 1792533600, "expires_utc": 1792620000,
    "message_type": "Alert", "status": "new",
    "level": "YELLOW", "level_original": "Yellow",
    "phenomenon": "flood", "phenomenon_original": "Flooding", "event": "Moderate flooding warning",
    "issuer": "METEO-FRANCE",
    "texts": [{"language": "fr-FR", "headline": "Vigilance jaune inondations", "description": "...", "instruction": "..."}],
    "area": {"description": "Drôme", "geocodes": [{"scheme": "NUTS3", "value": "FR713"}, ...], "has_polygon": false, "localization": "geocode"},
    "link": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-france/b77d3bc0-...",
    "derived": ["status", "level", "phenomenon"]
  }],
  "coverage": [{"source": "meteoalarm", "country": "FR", "status": "ok", "warnings_in_feed": 15, "warnings_not_matchable": 0}],
  "attribution": "...", "disclaimer": "Time delays between this website and the www.meteoalarm.org website are possible. ..."
}
```

A river section is a **line**: the same route for Nîmes (Gard), 3.9 km from the Vistre, which Vigicrues had in
orange that day (cut; the official bulletin of the territory said the same: "Vistre et Vidourle restent en
vigilance orange"). The département's own MeteoAlarm "flooding", also orange, is left out of this answer because the
section says it, finer.

```json
{
  "vigilances": [{
    "id": "vigicrues:GA32", "source": "vigicrues", "country": "FR",
    "attribution": "Source: Vigicrues, Service central d'hydrométéorologie et d'appui à la prévision des inondations (SCHAPI), https://www.vigicrues.gouv.fr. Licence Ouverte 2.0 | Last update of the flow: 2026-10-20T07:55:15+00:00",
    "level": "ORANGE", "level_original": "3", "phenomenon": "flood",
    "area": {"description": "Vistre", "geocodes": [{"scheme": "VIGICRUES_SECTION", "value": "GA32"}, ...], "has_polygon": false, "localization": "line"},
    "distance_km": 3.93,
    "web": "https://www.vigicrues.gouv.fr/bulletin_local/20#GA32",
    "derived": ["level", "phenomenon"]
  }],
  "coverage": [{"source": "meteoalarm", "country": "FR", "status": "ok", ...}, {"source": "vigicrues", "country": "FR", "status": "ok", ...}]
}
```

- **The source's words and the API's fields are told apart.** `level` (GREEN, YELLOW, ORANGE, RED), `phenomenon`
  (wind, rain, thunderstorm, snow-ice, heat, cold, flood, avalanche, coastal, fire, fog, other) and `status`
  (new, update, cancel) are the API's own and are named in `derived`; the tables are in
  [`docs/data-sources.md`](docs/data-sources.md). A word no table covers gives `null`. `level_original`,
  `phenomenon_original`, `event` and `texts` are the source's.
- **Show the credit and the notice.** Every warning has its `attribution`, and the answer has the `disclaimer`
  that the terms of MeteoAlarm oblige a redistributor to publish. This is not an official warning service. Only the
  sources that answered are credited.
- **A warning that cannot be matched to a point says so.** A point is matched by the polygon of the warning, by the
  shape of its area code, or, for a river section, by its distance to the line (`river_radius_km`, `distance_km` in
  the answer). Warnings of France, Belgium, Germany, Canada and of the countries that send polygons (for example
  Switzerland and Norway) are matched. Warnings that name only MeteoAlarm `EMMA_ID` codes (Italy,
  Austria, Poland...) are listed by `country=` but **not matched to a point**: the shapes of these codes have no stated
  licence and are not bundled ([`SOURCES.md`](SOURCES.md)). For a point, `coverage[].warnings_not_matchable` counts them, so an empty
  list is not read as "nothing to fear". The same goes for the NWS alerts that name zones instead of a polygon.
  An operator can build the `EMMA_ID` shapes for his own instance (`tools/build_geometries.py --emma`, then
  `VIGIE_EMMA_GEOMETRIES`).
- **Push.** On `WS /v1/stream`, send `{"action": "follow", "lat": 44.9334, "lon": 4.8924}`: the answer is a
  `vigilance_state` (what the route would give), then, with `VIGIE_SCHEDULER=1`, a `vigilance_push` each time a
  warning for that point `appeared`, was `upgraded` (`previous_level` says from what) or `ended`.
  `{"action": "unfollow", ...}` stops it. The watched zones are followed by the scheduler too and their changes go to
  every client. Nothing is pushed for what was already there when a point is first followed.
- **Few requests.** Only the sources of the countries that are needed are asked (the country of a point is found
  offline), each cached as long as its rhythm allows (MeteoAlarm 180 s, DWD 120 s and asked conditionally, ECCC and
  MetService 300 s, Vigicrues a look at a 330-byte image every 15 minutes and the 2.2 MB flow only when it changed);
  a CAP document, which holds the issuer's words, is asked once and only for a
  warning that an answer returns; the NWS needs no request of its own. See "Calls to the providers" below.
- The embedded frame of the displays does **not** change.

### More countries, through the WMO registry

The World Meteorological Organization keeps a registry of the national services that publish CAP warnings (the Severe
Weather Information Centre: 140 services in 135 countries on 2026-10-20). For a country that has **no source of its own**
the API reads the service of that registry, with the same rules (one source answers, the issuer's words untouched, the
fields of the API named in `derived`). `VIGIE_WMO_SOURCES` picks which services are read, because each issuer's own terms
count and most were not found or restrict copying ([`SOURCES.md`](SOURCES.md), one row for each of the 28 services that were
looked at):

- `verified` (the default): only the **7 services whose own terms were read and allow relaying them**: Algeria, Argentina,
  Barbados, Jamaica, Mexico, the Philippines, and Spain (AEMET instead of MeteoAlarm, because AEMET's feed gives polygons
  where MeteoAlarm's gives codes: 34 against 0 on 2026-10-20, [ADR 0006](docs/decisions/0006-wmo-registry-services.md)).
- `all`: every service that is not excluded, 95 services in 94 countries. Only for an instance that only its owner uses: 88 of
  them have terms that are unknown or restrict copying. Hong Kong and China are never read.
- `off`: none.

```
GET /v1/vigilances/countries                   the countries this instance serves with its settings: the sources, and whether the licence is verified
GET /v1/vigilances?country=MX                  the warnings of Mexico (SMN), polygons, the texts in Spanish as written
GET /v1/vigilances?lat=38.9067&lon=1.4206      Ibiza: the AEMET warnings whose polygon contains the point
```

Real answer for Ibiza at 18:34 UTC on 2026-10-20 (the feeds were read live; cut):

```json
{
  "query": {"lat": 38.9067, "lon": 1.4206, "country": null, "river_radius_km": 5.0},
  "countries": ["ES"],
  "vigilances": [{
    "id": "swic:es-aemet-es:2.49.0.0.724.0.ES.20261020172954.645301PRP120201792517394:0.0",
    "source": "swic", "country": "ES",
    "attribution": "Source: AEMET (Agencia Estatal de Meteorología), Spain. Información elaborada por la Agencia Estatal de Meteorología. © AEMET | Warnings of a national meteorological service, from the registry of the WMO Severe Weather Information Centre (https://severeweather.wmo.int). The issuer's own terms apply to each warning",
    "identifier": "2.49.0.0.724.0.ES.20261020172954.645301PRP120201792517394",
    "onset_utc": 1792515600, "expires_utc": 1792526399, "message_type": "Alert", "status": "new",
    "level": "YELLOW", "level_original": "amarillo",
    "phenomenon": "rain", "phenomenon_original": "Aviso de lluvias de nivel amarillo",
    "event": "Aviso de lluvias de nivel amarillo", "issuer": "AEMET. Agencia Estatal de Meteorología",
    "texts": [{"language": "es-ES", "headline": "Aviso de lluvias de nivel amarillo. Ibiza y Formentera", "description": "Precipitación acumulada en una hora: 20 mm. ...", "instruction": "..."},
              {"language": "en-GB", "headline": "Moderate rain warning. Ibiza y Formentera", "description": "...", "instruction": "..."}],
    "area": {"description": "Ibiza y Formentera", "geocodes": [{"scheme": "AEMET-Meteoalerta zona", "value": "645301"}], "has_polygon": true, "localization": "polygon"},
    "derived": ["status", "level", "phenomenon"]
  }],
  "coverage": [{"source": "swic", "source_id": "es-aemet-es", "country": "ES", "status": "ok", "warnings_in_feed": 19, "warnings_not_matchable": 0}]
}
```

- **A service that does not answer is not "nothing in force".** Each service is its own line of `coverage` (`source: "swic"`,
  `source_id`): `ok`, `stale` (its last answer, marked), or `unavailable`. On the same day the Philippines' feed did not
  answer in time from the test PC, and the API said `unavailable` for it, never an empty list.
- **Warnings that name only codes** (the Philippines, India) are listed by `country=` and counted in
  `coverage[].warnings_not_matchable`; they are not placed on a point. A warning with a polygon or a circle is matched.
- **`level` and `phenomenon` are derived** from a colour the issuer writes (`amarillo`, `Alerta Roja`...) or the CAP
  severity (`Minor` gives no level), and from the event words in English, Spanish, Portuguese and French; anything else is
  `null`. The issuer's `event`, `texts` in every language and `level_original` stay as written.
- **What it costs**: a service is asked every 6 minutes at most, with conditional requests (`304`) where the server gives a
  validator, and each CAP document is read once. A simulated day of the scheduler (`tests/test_swic_sobriety.py`): **2,010
  requests and 10.6 MB** for the 7 services of the default, **36,924 requests and 51 MB** for `all` with a followed point
  in each of the 94 countries (the worst case; the number of clients adds nothing). A ceiling of 50,000 requests a day
  covers the whole set. [`docs/data-sources.md`](docs/data-sources.md) has the rhythm and its reasons.

### A country with no source says why

The borders hold every country (Natural Earth, 244 of them), so a position in Japan, Australia or China finds its country. The answer names it
and says why nothing is read there, in `uncovered` and in `note`, with a stable `reason_code` that a client can translate:

```
GET /v1/vigilances?lat=35.6762&lon=139.6503      Tokyo (cut)
{
  "countries": [], "vigilances": [], "coverage": [],
  "uncovered": [{"country": "JP", "name": "Japan", "covered": false, "reason_code": "law_restricts",
                 "reason": "Japanese law reserves the giving of warnings to the Japan Meteorological Agency (Meteorological Service Act, article 23): this instance reads none of its warnings."}],
  "note": "Japan (JP): Japanese law reserves ... The countries this instance reads: GET /v1/vigilances/countries."
}
GET /v1/vigilances/countries?uncovered=true      every country, the ones with a source unchanged
```

The codes: `no_source` (no official warning feed is read for the country, and nothing more is known), `licence_unverified`, `terms_restrict`,
`terms_contradictory`, `redistribution_forbidden`, `law_restricts` and `source_off`. **A reason is written only when it was read**: where, and what is
not verified, is in [`SOURCES.md`](SOURCES.md), "Countries with no source". A position at sea, further than about 25 km from a coast, is in no country and
says so. [ADR 0009](docs/decisions/0009-every-border-a-reason-per-country-and-the-admin-token.md).

### The archive of the official warnings

Every warning a source lists when the instance reads it is **archived**, in force or not (a cancellation, a warning past its end), so
that the warnings of the past can be read again. The archive keeps the warning **as `GET /v1/vigilances` served it**: the issuer's
words untouched in every language, `level` and `phenomenon` the API's own (named in `derived`), and when the instance first and last
saw it. One item per message and per area: a warning seen again is the same item, an update of the source is a new one. It sends
**no request of its own** to any provider.

```
GET /v1/vigilances/archive?country=ME&min_level=ORANGE          the orange and red warnings of Montenegro the instance saw
GET /v1/vigilances/archive?area=FR813&since=1792400000           the warnings of the Hérault (NUTS3 code) since a date
GET /v1/vigilances/archive?phenomenon=wind&source=dwd&limit=50   the DWD's wind warnings, 50 at a time (`offset` for the next page)
GET /v1/vigilances/archive/stats?days=30                          what the archive took in per day, and what it weighs on disk
```

Filters: `country`, `area` (a code, `NUTS3:FR101` or `FR101`, or a part of the area's name), `source` (`swic:<service>` for one service
of the WMO registry), `phenomenon`, `level`, `min_level`, and `since` / `until` (UTC epoch seconds: the warnings whose period, from
the onset to the later of end and expiry, overlaps them). The newest first seen come first. A real item, archived on 2026-10-28 (cut):

```json
{
  "first_seen_utc": 1793220818, "last_seen_utc": 1793220818,
  "vigilance": {
    "id": "meteoalarm:cb8fa408-3c1c-4166-87d4-8bc4419bf9f3:0-0-1", "source": "meteoalarm", "country": "ME",
    "identifier": "2.49.0.1.499.0.1793173001904", "onset_utc": 1793170800, "expires_utc": 1793228340,
    "level": "ORANGE", "level_original": "Orange", "phenomenon": "wind", "phenomenon_original": "Wind",
    "issuer": "Zavod za hidrometeorologiju i seizmologiju",
    "texts": [{"language": "cnr", "headline": "Upozorenje na vjetar", "description": "Udari vjetra >17 m/s (>50km/h ili oko 8 Bofora)"},
              {"language": "en-GB", "headline": "Wind Warning", "description": "Wind gusts >17 m/s (>50km/h or about 8 Beauforts)"}],
    "area": {"description": "Adriatic coast", "geocodes": [{"scheme": "EMMA_ID", "value": "ME002"}]},
    "derived": ["status", "level", "phenomenon"]
  }
}
```

- **A period without a warning is not a period without danger** unless the source was read then: an instance reads a source only
  for a country that is needed (a watched zone, a followed point, a recent request). `reads` says, per UTC day, in which hours each
  source was read with success for the country asked.
- **A MeteoAlarm warning has its words when the instance had read its CAP document**, which it does only for a warning an answer
  returns (at most 40 new ones per request): the archived item is rewritten when the document comes. A warning that ended before
  anyone asked for its country keeps its fields without its words.
- `last_seen_utc` is right to within ten minutes. The shape of an area, when the source gives one, is kept once whatever the number
  of warnings that share it.
- **How long**: `VIGIE_ARCHIVE_KEEP_DAYS` (0, the default, keeps everything); `keep_days` in the answers. `GET /v1/health` reports
  `vigilance_archive` (how many warnings, since when, the last write or error).

## Storms and tsunamis

`GET /v1/geojson/hazards` also lists the **tropical cyclones** of the National Hurricane Center (NHC) and the **tsunami
messages** of the U.S. Tsunami Warning Centers (the NTWC for the United States and Canada, the PTWC for the Pacific and
the Caribbean). Each is one **point** like every other hazard (a storm at its current position, a tsunami at the
epicentre), with the usual properties and `has_forecast`. The forecast is another route, so that a map fetches it only for
the storm a visitor opens:

```
GET /v1/geojson/hazards/nhc_al092026/forecast             the cone, track and forecast positions of a storm
GET /v1/geojson/hazards/tsunami_pheb_26282000/forecast    the epicentre and the arrival times of a tsunami
GET /v1/vigilances?source=tsunami                         every tsunami message in force, the centre's words untouched
```

- **The forecast is a FeatureCollection** (RFC 7946) with `source`, `issued_utc`, `advisory`, `advisory_url` and `attribution`;
  each feature has `properties.kind`: `cone` (Polygon), `track` (LineString), `position` (Point with `valid_utc`, `wind_kt`,
  `pressure_hpa`...) and `watch_warning` for a storm; `epicentre` and `eta` (Point with `eta_utc`) for a tsunami. A cone across
  the date line is cut in two as RFC 7946 asks. Coordinates have 3 decimals; a pressure the NHC does not give is `null`.
  Hurricane Isaias's advisory 13A weighs 22.7 kB. A hazard with no forecast answers an empty collection and a `reason`; an
  unknown id is a 404.
- **An information statement is not an alert.** The tsunami centres issue many that say there is no threat: they are never shown.
  A cancellation, a test, a threat class the tables do not know, and the "final" message of an event are none either.
- **The levels are ours** (the words are the centres'): a storm by its wind (34 kt yellow, 64 orange, 96 red); a domestic
  tsunami message by its category (watch yellow, advisory orange, warning red); a PTWC threat message by its wave-height
  class (0.3-1 m yellow, 1-3 m or a potential threat orange, over 3 m red). The tables: [`docs/data-sources.md`](docs/data-sources.md).
- **One event once.** GDACS reports the same storms and the NWS relays the domestic tsunami messages: they are told to be one
  event by the storm's name and season or the message's VTEC code, and the source that observes it gives the level.
- **Not an official warning.** The PTWC says its threat messages are for national authorities, who decide the alert level
  for each country. A tsunami message names its coasts by zone code or country, with no shape: it is listed by `country=US`
  or `CA`, or by `source=tsunami`, and never matched to a point.
- **Few requests.** tsunami.gov and the NHC are asked conditionally: 960 and 240 requests a day (all but the first a `304` of a few
  hundred bytes), 230 kB and 116 kB on a quiet day; a message once, a bundle once per advisory.
  [ADR 0005](docs/decisions/0005-tsunamis-and-storms.md).

## Active fires

`GET /v1/fires` and `GET /v1/fires/grid` serve the active fires seen by the VIIRS instruments of NOAA-20 and NOAA-21 (the near
real-time product of [NASA FIRMS](https://www.earthdata.nasa.gov/data/tools/firms), 375 m pixels). **They need no key**: the
instance reads the static files that FIRMS publishes. An instance that turned the source off (`VIGIE_ENABLE_FIRMS=0`, see "Configuration")
answers 404 and `GET /v1/health` lists the source in `disabled_sources`. The fires are not in `GET /v1/geojson/hazards`, which a map loads
whole.

```
GET /v1/fires?bbox=28,-22,38,-12                          the detections of a box, the strongest first (at most 1,000)
GET /v1/fires?bbox=170,-25,-170,-10&hours=12&min_confidence=nominal   across the antimeridian, the last 12 hours
GET /v1/fires/grid?cell_deg=5                             the world counted per cell of 5 degrees
GET /v1/point?lat=-15.4&lon=28.3&fire_radius_km=50        fires_nearby: count, nearest distance, highest power
```

- **The fields are the file's**: `acq_utc`, `satellite`, `instrument`, `confidence` (`low`, `nominal`, `high`), `frp_mw` (fire radiative
  power), `bright_ti4_k`, `bright_ti5_k`, `scan_km`, `track_km`, `daynight`, `version`. A value the file does not give is `null`.
- **The limits**: `bbox` is `west,south,east,north` (a west greater than the east crosses the antimeridian; latitudes within -90
  and 90, `south < north`); `hours` up to the window of the files the instance reads (48 by default, `VIGIE_FIRMS_WINDOW_HOURS`; more is a 422); `limit` 1,000 by default and 5,000 at most (the grid: 5,000 and 20,000); `cell_deg` 0.25,
  0.5, 1, 2, 5 or 10. Anything else is a 422. `metadata` says how many detections matched and whether the answer was cut.
- **A detection is not an alert.** It is a satellite pixel that looks like a fire: no detection is not the absence of fire (a satellite
  passes a few times a day, clouds hide the ground), and NASA does not advise these data for tactical decisions or local conditions.
  The data are published about 3 hours after the observation; the static files hold the near real-time version only, so the
  ultra real-time detections of the United States and Canada (within minutes) are not in them. `fires_nearby` is named in `derived` and
  never raises `local_alert_level`.
- **The credit** NASA asks for (`attribution`) and its disclaimer (`disclaimer`) are in every answer.
- **Few requests, and a server that stalls.** FIRMS writes each file once an hour, and its server stalls at times (14 % of the
  checks sent from one PC got no answer in 40 seconds). The instance checks a file at most every 60 minutes
  (`VIGIE_FIRMS_REFRESH_MINUTES`), only when someone asks (and once at start-up), with one request that is closed after its headers when
  nothing changed, and reads only the bytes added to a file that grew (checked against its `ETag`). A day with 1,000 clients is about 86
  requests and 56 MB by simulation, with no key and no transaction (the first version: 60 reads, 2,160 transactions, 170 MB). A read that
  gets no byte for 25 seconds fails; that source is asked again after 1, 2, 5, then every 10 minutes, and its last reading is still served,
  with `stale` true. A request never waits for FIRMS once something was read.
  [ADR 0007](docs/decisions/0007-active-fires-from-nasa-firms.md).
- **The key is optional, and never shown.** With `VIGIE_FIRMS_MAP_KEY`, a source whose file could not be read is read from the area API
  instead. The key is in the URL of those requests: it is redacted from every error, from `last_error` and from the logs. Keep it out of
  the repository and of the commands you share.

## Places near a hazard, and watched points

An earthquake or a volcanic event says what it is close to. Each hazard of `/v1/geojson/hazards` (and of `/v1/history`, and of
`/v1/snapshot` and `/v1/point` with `compact=false`) carries `near_places`, nearest first, and names it in `derived`:

```json
"near_places": [
  {"name": "Azuero Fault Zone", "kind": "fault", "distance_km": 4.3, "sources": ["GEM"]}
],
"derived": ["near_places"]
```

M 7.7 of Panama, 9 October 2026, USGS `us6000u18k`: the distance to the mapped surface trace, checked against a second computation
(Vincenty on the ellipsoid, to the original trace: 4.19 km).

- **It is calculated by Vigie**, not reported by a source, and it **never changes a `severity`**. Show it as "calculated by Vigie".
  `[]` means nothing is near; `null` means it was not worked out (a hazard that is compared with nothing, such as a storm or a flood; the fault
  file cannot be read; a simulated alert; or the compact profile of `/v1/snapshot` and `/v1/point`, which keeps to 16 KiB, and so the
  hazards pushed on `/v1/stream`).
- **Two sets of reference places.** The active faults that have a name, from the GEM Global Active Faults Database (earthquakes and volcanic
  events, at most 70 km deep, within 10 km); and the volcanoes that the hazard sources themselves report (an earthquake within 20 km of one).
  **No list of the world's volcanoes is bundled** (the Smithsonian list has an unclear licence): Etna is named only while GDACS or the USGS
  report it.
- **The points you watch are never in `near_places`.** The hazards are shared with every client, a public website included, and a watched point
  is personal (a home, for example): its name would leak, and anyone who could write a point could make any text appear in everyone's hazards.
  A watched point is listed by `/v1/watched-points` and its status, and nowhere else.
- **The radii are settings, not facts any source publishes** (`VIGIE_NEAR_*` below).
- **A fault is named only if the database names it.** More than half of its traces have no name, and they are the Mediterranean and Asia: the North
  Anatolian Fault is not named in the file. The mention is the distance to the *mapped surface trace*, not a statement that the earthquake happened on that fault.
- **The faults are CC BY-SA 4.0** (Styron and Pagani, 2020), a file of their own with its notice: [`app/data/README.md`](app/data/README.md),
  [`SOURCES.md`](SOURCES.md). The credit is in the `attribution` of the hazard that names one.

```
POST /v1/watched-points   {"name": "Etna", "kind": "volcano", "lat": 37.751, "lon": 14.994, "radius_km": 30}
GET  /v1/watched-points/status?days=7
```

`status` lists, for each point, the events within its radius over the last days (an earthquake by the time it happened, any other hazard by the last
time the API saw it active) and a `state`: `events`; `quiet` (nothing happened, **and every hazard source answered and has been read without a gap since
`quiet_since_utc`**); or `unknown`, with `unreachable_sources`. The API only knows what it saw: an instance started an hour ago says so
(`window_covered: false`) instead of "nothing in the last week".

Like `/v1/zones`, the watched-point routes have no authentication, and the list and the status are readable by anyone who can reach the API:
do not watch your home on an instance that others can read. **`POST` and `DELETE` are off unless `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1`** (403
with a message otherwise; the reads always work), and on a public instance they must be blocked by a reverse proxy, as for `/v1/zones` and
`/v1/simulate`. Details, numbers and what is not verified: [`docs/data-sources.md`](docs/data-sources.md) and
[ADR 0008](docs/decisions/0008-sensitive-points.md).

## Home Assistant

`custom_components/vigie` is a Home Assistant integration that reads these warnings for a place and gives a
maximum-level sensor, a "warning active" binary sensor and an event entity (`new`, `upgraded`, `ended`) to start
automations. It needs an instance of this API that Home Assistant can reach, and nothing else. Install, options, the
rules of "unknown is not none", an automation example and the limits: [`docs/home-assistant.md`](docs/home-assistant.md).
It comes as is, like the rest of the project, and it is not an official warning service either.

## Run

Python 3.12 or newer (the CI covers 3.12 and 3.13).

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     (Windows)
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

On Windows, `scripts\run.ps1` does all of this (see below). Then open
`http://localhost:8080/dashboard` or `http://localhost:8080/v1/embedded/frame`.

### With Docker

```
docker build -t vigie-api .
docker run --rm -p 8080:8080 -v vigie-data:/data -e VIGIE_CONTACT=you@example.org vigie-api
```

The image (`python:3.13-slim`, built for linux/amd64 by the CI) runs as a non-root user, keeps its
database in the `/data` volume and has a health check on `/v1/health`. RainViewer is off in the image; add
`-e VIGIE_ENABLE_RAINVIEWER=1` for a personal instance. NASA FIRMS (the active fires) is on, also in the image:
it downloads about 56 MB a day when someone asks the fire routes, and `-e VIGIE_ENABLE_FIRMS=0` turns it off. The publication workflow pushes
`ghcr.io/axellum/vigie-api` when a version tag (`v*`) is created; until a tag exists, build the image
yourself.

### Configuration

All settings are optional environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VIGIE_DB_PATH` | `vigie.db` in the repository root (git-ignored); `/data/vigie.db` in the Docker image | SQLite file (watchlist, history, last snapshot). |
| `VIGIE_SCHEDULER` | `0` | `1` starts the background scheduler (refresh every 90 s, WebSocket push). |
| `VIGIE_ARCHIVE_KEEP_DAYS` | `0` | How many days an archived official warning is kept after the instance last saw it (`GET /v1/vigilances/archive`); `0` keeps everything. Older warnings, the shapes no warning uses any more and the older reads are deleted once a day. |
| `VIGIE_ENABLE_SIMULATION` | `0` | `1` enables `POST /v1/simulate` (and the dashboard buttons). The route has no authentication and writes the simulated alerts into the hazard history: enable it only on an instance that trusted clients alone can reach. |
| `VIGIE_ENABLE_WATCHED_POINTS_WRITE` | `0` | `1` enables `POST` and `DELETE` of `/v1/watched-points` (403 otherwise; the reads always work). The routes have no authentication and a watched point is personal: enable it only on an instance that trusted clients alone can reach, and block the two routes in the reverse proxy of a public instance. |
| `VIGIE_ADMIN_TOKEN` | none | When set, every request that is not a read (`POST`, `PUT`, `PATCH`, `DELETE`) must carry `Authorization: Bearer <token>`, else the answer is 401; the reads never need it. Unset or blank: nothing changes (the network is trusted). It stacks with the two switches above: a disabled route stays 403. Compared in constant time; it appears in no log and no answer (`GET /v1/health` only says `admin_token_set`). A shared secret in a header: put TLS in front of any instance that is not on a trusted network. |
| `VIGIE_ENABLE_RAINVIEWER` | `1`; `0` in the Docker image | `0` stops asking RainViewer (the radar fields of `/v1/point` stay unknown). Its free API is for personal and educational use: set `0` on an instance that serves other people. |
| `VIGIE_ENABLE_METSERVICE` | `1` (also in the Docker image) | `0` stops asking MetService (New Zealand): a position there then gets no source, and the answer says it is turned off. Its feed says CC BY 4.0 but the website terms it points to allow personal use only and forbid resupply, and which one governs is not verified: set `0` on an instance that serves other people unless MetService agrees. |
| `VIGIE_WMO_SOURCES` | `verified` | Which national services of the WMO registry are read for the countries that have no source of their own: `verified` (only those whose own terms were read and allow relaying them), `all` (every service that is not excluded, for an instance only its owner uses) or `off`. An unknown value means `verified`. See "More countries, through the WMO registry". |
| `VIGIE_CONTACT` | none | E-mail address or URL added to the `User-Agent` sent to the providers. MET Norway and NOAA require an identifying contact: set it. |
| `VIGIE_CAP_SENDER` | `vigie-api` | `sender` of the CAP alerts. |
| `VIGIE_GRID_FORECAST_DEG` | `0.05` | Grid (degrees, about 5 km) the position is snapped to before the Open-Meteo forecast and MET Norway are asked. |
| `VIGIE_GRID_AIR_QUALITY_DEG` | `0.1` | Same, for Open-Meteo Air Quality (about 11 km). |
| `VIGIE_GRID_FLOOD_DEG` | `0.1` | Same, for Open-Meteo Flood (GloFAS). |
| `VIGIE_GRID_MARINE_DEG` | `0.1` | Same, for Open-Meteo Marine. |
| `VIGIE_EMMA_GEOMETRIES` | none | Path of a gzipped GeoJSON of MeteoAlarm `EMMA_ID` shapes that you built yourself (`python tools/build_geometries.py --emma`; by default `app/data/local/emma_id.geojson.gz` is read when it exists). It lets warnings that name only `EMMA_ID` codes be matched to a point. Not bundled: see [`SOURCES.md`](SOURCES.md). |
| `VIGIE_NEAR_FAULT_KM` | `10` | An earthquake or volcanic event within this distance (km) of the mapped trace of a named active fault is near it (0.1 to 100). A setting, not a published fact. |
| `VIGIE_NEAR_FAULT_MAX_DEPTH_KM` | `70` | An earthquake deeper than this (km) is not compared with the faults (1 to 1000). |
| `VIGIE_NEAR_VOLCANO_KM` | `20` | An earthquake within this distance (km) of a volcano that a hazard source reports is near it (0.1 to 500). |
| `VIGIE_NEAR_MAX_PLACES` | `5` | At most this many places under a hazard, the nearest first (1 to 20). |
| `VIGIE_WATCHED_POINTS_MAX` | `50` | The most watched points the instance keeps (1 to 500). A new one over the limit is answered 409. |
| `VIGIE_QUIET_MAX_GAP_S` | `900` | A hazard source not read for longer than this (seconds, 60 to 86,400) breaks the run of readings that a watched point's `quiet` rests on. |
| `VIGIE_BUDGET_FRACTION` | `0.9` | Part of each published provider quota (Open-Meteo 10,000 calls a day, MET Norway, RainViewer) the API lets through. Beyond it the cache is served, flagged stale. |
| `VIGIE_ENABLE_FIRMS` | `1` | `0` turns the active fires off: `GET /v1/fires` and `GET /v1/fires/grid` answer 404, `fires_nearby` of `/v1/point` is null and `GET /v1/health` lists the source in `disabled_sources`. On by default: the instance then downloads about 56 MB a day when someone asks the fire routes (`docs/data-sources.md`). |
| `VIGIE_FIRMS_MAP_KEY` | none | Optional. The free `MAP_KEY` of [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/api/map_key/). The static files need no key, so without it nothing is missing; with it, a source whose file could not be read is read from the area API instead (today and yesterday, 72 of the 5,000 transactions the key is allowed in 10 minutes). The key is in the URL of every such request: the API never prints it (errors, `last_error`, logs), but keep it out of the repository, of your shell history and of the commands you share. A value that is not letters and digits is not used, and the log says so. |
| `VIGIE_FIRMS_SOURCES` | `VIIRS_NOAA20_NRT,VIIRS_NOAA21_NRT` | The FIRMS products read: one of them, or both (one halves the volume). Anything else is ignored (Suomi NPP stops on 2026-11-02, MODIS has another layout). |
| `VIGIE_FIRMS_WINDOW_HOURS` | `48` | The file read: `24` or `48` hours. It is also how far back `hours` can go on this instance (more is a 422). The files of 24 hours cost about two thirds of the bytes (36 MB a day against 56). Anything else is ignored. |
| `VIGIE_PREALERT_TR` | `0` | `1` runs the **unofficial pre-alert pilot for Turkey** ([ADR 0011](docs/decisions/0011-unofficial-pre-alert-turkey-shadow-pilot.md) and its amendment): each new ECMWF open data run (2 a day, 144 requests to ECMWF's mirror on AWS, about 110 MB) is turned into a level per province, stored in the database, and answered by the **experimental** route `GET /v1/experimental/prealert?lat=&lon=` (a test for testers, **not an official warning**; 404 when off). Off, nothing is downloaded. Needs the optional `eccodes` (in the Docker image; `pip install .[prealert]` otherwise). |
| `VIGIE_FIRMS_REFRESH_MINUTES` | `60` | How long a reading of the fires is kept before the next check, 10 to 360. FIRMS writes its files once an hour, so 30 minutes cost more requests (134 a day against 86) and no more bytes (`docs/data-sources.md`). Once the start-up reading is done, nobody asking costs nothing. |

No API key is needed by any provider (NASA FIRMS accepts an optional one: `VIGIE_FIRMS_MAP_KEY`). A grid value outside 0.0001 to 10 degrees is ignored (the
default is used). Only the request sent to the provider and the cache key use the snapped point: the
response still reports the exact position of the client, and the distances to the hazards use it.

### Calls to the providers

The calls to the providers should not grow with the number of clients: they depend on the number of
distinct 5 km cells asked for and on how fast each source really changes (grids, one cache entry per
cell, one request for simultaneous callers, TTLs from each source's rhythm, a call budget; see
[`docs/architecture.md`](docs/architecture.md) and [`docs/data-sources.md`](docs/data-sources.md)).
`tests/test_call_sobriety.py` plays a simulated day with the scheduler on and its 5 default zones,
and 1,000 clients on 50 points (20 each, polling every 30 minutes, 10 points are harbours that also ask
for the marine conditions; no two points share a cell, so the grids do not help), against a simulated
upstream, and counts the requests each source receives:

| Source | Calls a day, before | after |
| --- | ---: | ---: |
| Open-Meteo forecast | 17,212 | 6,167 |
| Open-Meteo air quality | 7,182 | 1,320 |
| Open-Meteo river discharge | 6,122 | 200 |
| Open-Meteo marine | 2,216 | 240 |
| **Open-Meteo, weighted** (the free quota is 10,000 a day) | **46,502** | **8,412** |
| USGS earthquakes, EMSC | 1,980 each | 1,393 each |
| NOAA NWS alerts | 1,069 | 705 |
| GDACS | 676 | 474 |
| RainViewer | 662 | 144 |
| NOAA SWPC | 403 | 286 |
| tsunami.gov (list of bulletins, conditional) | n/a | 1,393 (330 kB) |
| NOAA National Hurricane Center (active storms, conditional) | n/a | 286 (140 kB) |
| USGS volcanoes | 212 | 144 |
| MeteoAlarm feeds (France and Iceland: the countries of two of the five zones) | n/a | 640 |
| MeteoAlarm CAP documents | n/a | 0 |
| Vigicrues (Paris is in France): reads of the flow, 2.2 MB each, only when a publication changed it (was 138, 310 MB, while a section is orange; 46, 103 MB, on a calm day) | n/a | 4 (9.1 MB) |
| Vigicrues: looks at the publication image, about 330 bytes each (a section orange: every 5 minutes; calm: 15) | n/a | 240 (88 on a calm day) |
| DWD, ECCC, MetService (no zone in Germany, Canada or New Zealand) | n/a | 0 |

"Before" is `main` at commit 1864e25 (same simulation, measured once); "after" is this code. The
heaviest Open-Meteo hour weighs 2,305 before and 412 after (quota 5,000), the heaviest minute 497
and 231 (quota 600). With clients polling every 15 minutes instead, before becomes 54,766 and after
8,667: the load of the clients hardly changes the "after". The 55 distinct cells of this scenario use
84 % of the free quota; beyond that the budget stops the requests and serves the cache, flagged stale.
Play it yourself: `python -m tests.simulation` (about 20 seconds).

The official warnings add one MeteoAlarm feed per needed country, 320 requests a day when the scheduler keeps it
fresh (a 180 s cache, asked again at the next 90 s cycle), and nothing for the NWS: its answer is the hazards' own,
which the same simulated day confirms (705 calls with or without the warnings).
`tests/test_vigilance_sobriety.py` plays 500 clients on 50 positions of four countries for 6 hours: 6,000 answers cost
320 feed requests (80 per country) and 4 CAP documents.

A zone, a followed point or a client in **Germany** costs the DWD 480 requests a day (479 of them `304`, a few
hundred bytes) and no longer the German MeteoAlarm feed (320 a day); in **Canada** ECCC 240 (31 KB each with
gzip); in **New Zealand** MetService 240 (239 `304`). However many people read the warnings, a source is asked at
most once per cache lifetime (720, 288, 288 and 288 a day for DWD, ECCC, MetService and Vigicrues, whose requests are
330-byte looks at an image). `tests/test_national_sobriety.py` plays these days.

**Vigicrues** is the heavy one: its flow weighs 2.2 MB, is not compressed, has no validator, ignores `Range` and takes
no filter, and no lighter endpoint carries the level. So the collector asks (`HEAD`) the national map image that the
same publication regenerates, every 15 minutes (5 while a section is orange or red), and reads the flow only when the
image changed, and at the latest after 12 hours. On the simulated day of the five default zones that is 4 reads and
9.1 MB (9.0 MB on a calm day; 6.8 MB a day once started: the two publications, at 10 a.m. and 4 p.m., and one safety
read) against 138 reads and 310 MB (46 and 103 MB calm) before. A flood published at 4 p.m. is served at the next
look: 16.5 minutes later at most when calm, 6 in a flood (`test_a_flood_published_in_the_afternoon_is_served_within_one_look_and_nothing_is_lost`).
[`docs/data-sources.md`](docs/data-sources.md) has the measures.

### Run on Windows (a PC that serves the display)

The display fetches `http://<PC address>:8080/v1/embedded/frame`. Give the PC a fixed address in the
router so this URL does not change. Below, `<repository folder>` is the folder where you cloned this
repository.

1. **Start the API**, from any folder, in a normal (not administrator) PowerShell:

   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File <repository folder>\scripts\run.ps1
   ```

   The script creates `.venv` in the repository on the first run, installs `requirements.txt`
   (again only when the file changes) and starts `uvicorn app.main:app --host 0.0.0.0 --port 8080`.
   Options: `-Port`, `-BindAddress`, `-LogFile <file>`, `-NoInstall`. Stop it with Ctrl+C.

2. **Allow the display to reach it.** Windows Firewall blocks inbound connections by default. Run
   this one line **once, in PowerShell opened as administrator** (it opens TCP 8080 on the
   Private network profile, for the local subnet only):

   ```
   New-NetFirewallRule -DisplayName "Vigie API 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private -RemoteAddress LocalSubnet
   ```

   The home network must be set as *Private* in Windows (Settings, Network and Internet). To
   remove the rule later: `Remove-NetFirewallRule -DisplayName "Vigie API 8080"`.

3. **Check from another device** on the same network:

   ```
   Invoke-WebRequest http://<PC address>:8080/v1/health -UseBasicParsing
   ```

4. **Start it at logon** with Task Scheduler (nothing is created by the repository). Task
   Scheduler, *Create Task...* (not *Basic Task*):

   - *General*: name `Vigie API`; "Run only when user is logged on".
   - *Triggers*: New, "At log on", specific user: you.
   - *Actions*: New, "Start a program". Program: `powershell.exe`. Arguments:
     `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<repository folder>\scripts\run.ps1" -LogFile "<repository folder>\logs\vigie-api.log"`.
     Start in: `<repository folder>`.
   - *Settings*: "If the task fails, restart every" 1 minute, 3 attempts; untick "Stop the task if
     it runs longer than". If the repository folder is on another drive, that drive must be available
     at logon. If the repository folder is renamed or moved, update the paths above. The server output
     goes to `logs\vigie-api.log` (git-ignored); stop the server by ending the task in Task Scheduler.

## Develop

```
python -m pytest              # no test touches the network
python -m ruff check .        # lint
python tools/capture_frame.py frame.txt     # write a REAL frame (uses the live providers)
python tools/fake_instance.py               # a fake instance whose warnings you choose (to try the Home Assistant integration)
python tools/check_live_feeds.py            # ask each real provider once, check the parsers still read it
python tools/build_geometries.py            # rebuild app/data (borders and NUTS shapes) from Natural Earth and Wikidata
```

`tools/capture_frame.py` starts the application in-process, fetches
`/v1/embedded/frame?device_type=waveshare-7b` from the real providers and writes it to the given
file. It is used to produce the test fixture of the display repository.

CI (`.github/workflows/`):

- `ci.yml`, on every push to `main` and every pull request: ruff and pytest on Python 3.12 and 3.13, and a
  job that builds the Docker image, runs it and asks `/v1/health` and `/v1/embedded/frame`.
- `secret-scan.yml`: gitleaks over the whole git history, on every pull request and push to `main`.
- `live-feeds.yml`: every Monday and on demand, never in a pull request, `tools/check_live_feeds.py` asks
  each real provider once (MeteoAlarm, the NWS warnings, the DWD, ECCC, MetService, Vigicrues, the NHC and the tsunami centres included). A red run means a provider changed its
  format, removed an endpoint, or sent a level or phenomenon word the tables do not know.
- `home-assistant.yml`: the Home Assistant integration (`custom_components/vigie`), only when it changes, and weekly:
  its tests (`tests_ha/`, in an environment of their own: Python 3.14 and the Home Assistant release they pin), hassfest
  and the HACS validation.
- `publish-image.yml`: pushes the Docker image to `ghcr.io/axellum/vigie-api` when a `v*` tag is pushed.

## Data sources

| Domain | Source |
| --- | --- |
| Earthquakes | USGS, EMSC / SeismicPortal, GDACS |
| Disasters | GDACS, NOAA NWS, USGS Volcano Hazards Program, NOAA National Hurricane Center (tropical cyclones), NOAA U.S. Tsunami Warning Centers (tsunami messages) |
| Official weather warnings | MeteoAlarm (EUMETNET, 39 European countries), Deutscher Wetterdienst (Germany), Environment and Climate Change Canada, MetService (New Zealand), NOAA NWS (United States), Vigicrues (river floods of France), the national services of the WMO Severe Weather Information Centre registry (7 read by default: Algeria, Argentina, Barbados, Jamaica, Mexico, the Philippines, Spain) |
| Active faults (`near_places`) | GEM Global Active Faults Database, the traces that have a name (CC BY-SA 4.0, bundled) |
| Space weather | NOAA SWPC |
| Weather | Open-Meteo (primary), MET Norway (fallback), RainViewer (radar) |
| Marine, river discharge, air quality | Open-Meteo Marine, Flood (GloFAS), Air Quality (CAMS) |

Licence, terms, credit, quota and clauses of each source, and what is not verified: [`SOURCES.md`](SOURCES.md).
How each one is cached and what it costs in calls: [`docs/data-sources.md`](docs/data-sources.md).

## Documentation

- [`SOURCES.md`](SOURCES.md): the sources, their licences, credits and quotas.
- [`docs/architecture.md`](docs/architecture.md): components, data flow, limits.
- [`docs/embedded-frame.md`](docs/embedded-frame.md): the frame contract (single source of truth).
- [`docs/data-sources.md`](docs/data-sources.md): how each source is used, cache and call volume.
- [`docs/home-assistant.md`](docs/home-assistant.md): the Home Assistant integration.
- [`docs/decisions/`](docs/decisions): architecture decision records.
- [`CHANGELOG.md`](CHANGELOG.md), [`AGENTS.md`](AGENTS.md) (guide for AI coding agents).
