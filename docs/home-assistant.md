# Home Assistant integration

A personal project, made with the help of AI and shared in case it helps. It comes as is, with no promise of support
or availability. **This is not an official warning service**: the integration shows the warnings that a Vigie API
instance relays from the national weather services. They can be late, incomplete or wrong. Follow the warnings of your
national authorities, and do not let an automation be the only thing a decision rests on.

The integration (`custom_components/vigie`) reads the official weather warnings of one place from an instance of this
API (`GET /v1/vigilances`, see [the README](../README.md#official-weather-warnings)) and gives Home Assistant:

- a **maximum level** sensor (`none`, `yellow`, `orange`, `red`) with the list of the warnings in its attributes;
- a **warning active** binary sensor (yellow or above);
- a **vigilance** event entity that fires `new`, `upgraded` or `ended`, to start automations;
- and, as a layer of their own, the **earthquakes** near the place (`GET /v1/earthquakes`, USGS and EMSC): a **strongest
  earthquake** sensor and an **earthquake** event entity that fires `new_earthquake` once per earthquake. They are not
  official warnings and never change the three entities above;
- and, with the option **Weather of the place and dashboard card** (on by default), the **weather** of the place
  (`GET /v1/point`, a weather entity) and three sensors: **rain in the next hour**, **strongest gust of the next hours** and
  **next high tide**. The same option feeds the **dashboard card** that ships with the integration (below, ["The dashboard
  card"](#the-dashboard-card), [ADR 0042](decisions/0042-home-assistant-weather-of-the-place-and-card.md)).

It needs no screen, no key and no package beyond what Home Assistant ships. It does not issue anything: it reads the public
instance, or one you run yourself ([README, "Run"](../README.md#run)).

If you prefer the weather integration you already use in Home Assistant (Météo-France, Open-Meteo, Met.no), untick the option:
the entry then keeps the warnings and the earthquakes only.

Its tests run against Home Assistant 2026.10.2. The warnings were tried on one running Home Assistant installation on
2026-10-20; see "Develop" for what the tests do and do not cover.

## Install

**With HACS** (the repository is public): HACS, the three dots, *Custom repositories*, the address of this repository
(`https://github.com/Axellum/vigie-api`), category *Integration*. Then find "Vigie" in HACS, download it, and **restart
Home Assistant**. HACS installs and updates the folder `custom_components/vigie`; `hacs.json` at the root of the
repository is for that.

**By hand**:

1. Copy the folder `custom_components/vigie` of this repository into the `custom_components` folder of your Home
   Assistant configuration (create `custom_components` if it does not exist). Home Assistant's configuration folder is the
   one that holds `configuration.yaml`. The result is `custom_components/vigie/manifest.json`.
2. **Restart Home Assistant** (a new custom integration is only found at start).

Then: Settings, Devices & services, Add integration, "Vigie".

## Configure

| Field | Meaning |
| --- | --- |
| Name | Names the device and its entities. Prefilled with "Vigie" and the name of your home. |
| Address of the instance | Prefilled with the public instance, `https://vigie.auxfilsdesidees.fr`; or your own, for example `http://vigie.local:8080`. It is checked by a read of `/v1/health` (it must answer like a Vigie API); a missing `http://` is added. |
| Place | The point whose warnings are read: a warning counts when its area contains it. Prefilled with the `zone.home` coordinates; pick another point on the map for a holiday home or a boat. |

**One entry per place.** The same instance can be added again for another place, and each entry is a device of its
own. The same address and the same place (to about 11 m) is refused as already configured.

**Options** (the cog of the entry):

| Option | Default | Meaning |
| --- | --- | --- |
| Poll interval | 5 min (1 to 60) | How often the instance is read. It is also the fallback of the push (below). |
| River radius | 5 km (1 to 50) | In France, how far from the place a river section (Vigicrues) still counts. The API's own default is 5 and its maximum 50. |
| Earthquake radius | 300 km (10 to 3000) | How far from the place an earthquake counts. This value is a setting, not a measure of danger: a strong earthquake far away can be felt, a small one nearby may not be. |
| Earthquake minimum magnitude | 4.5 (4.5 to 9) | The smallest magnitude shown and announced. The feeds the API reads list nothing below 4.5. |
| Weather of the place and dashboard card | on | Adds the weather entity and the three sensors of "Weather of the place" below, and the data of the dashboard card. Untick it to keep the warnings and the earthquakes only: the weather routes are then not read at all. |
| Experimental pre-alert for Turkey (unofficial) | off | **A test, being improved, not an official warning.** Adds the sensor below for a place in Turkey, read from an instance that runs the pre-alert (`VIGIE_PREALERT_TR=1`; the public instance may or may not). |

Changing an option reloads the entry. **Reconfigure** (the three dots of the entry) changes the address, the place or
the name without losing the entities.

The screens are in English and French (`translations/`), following the language of Home Assistant. The entity ids are
built from the translated names: with an entry named "Vigie Home" and English, `sensor.vigie_home_maximum_level`,
`binary_sensor.vigie_home_warning_active`, `event.vigie_home_vigilance`, `sensor.vigie_home_strongest_earthquake` and
`event.vigie_home_earthquake` (in French: `..._niveau_maximal`, `..._vigilance_active`, `..._vigilance`,
`..._seisme_le_plus_fort`, `..._seisme`). With the weather option: `weather.vigie_home` (named after the entry),
`sensor.vigie_home_rain_in_the_next_hour`, `sensor.vigie_home_strongest_gust_next_hours` and `sensor.vigie_home_next_high_tide`
(in French: `..._pluie_dans_l_heure`, `..._rafale_la_plus_forte_prochaines_heures`, `..._prochaine_pleine_mer`).

## Entities

### Maximum level (sensor)

An `enum` sensor: `none`, `yellow`, `orange`, `red`. It is the highest level among the warnings that cover the place and
are **in force or announced** (the API lists both; each warning has its `onset`, so a template can tell them apart).

**Unknown is not "none".** The state is `none` only when a source answers for the place, nothing is found and nothing
is hidden. It is *unknown* (`unknown`, no state) when:

- no source covers the place (the attribute `note` says so), for example a country with no source;
- every source that covers the place is unavailable (`sources` shows each status);
- a warning exists but its level cannot be read (the API's table does not cover the source's word);
- a source lists warnings that the API cannot place on a map (`not_matchable` counts them; see "Limits").

A level that was found is always shown, even when something else could not be graded or placed: it is then a lower bound.
When the instance cannot be reached at all, the entities are *unavailable*, which is a third, different thing.

Attributes (the credit of the sources that answered is the standard `attribution` attribute):

| Attribute | Content |
| --- | --- |
| `count` | Number of warnings in force or announced. |
| `by_phenomenon` | The highest level of each phenomenon the warnings name: `{"flood": "red", "thunderstorm": "yellow", "wind": "yellow"}`. The groups are the API's: wind, rain, thunderstorm, snow-ice, heat, cold, flood, avalanche, coastal, fire, fog, other. A warning with no phenomenon is not in it. |
| `by_family` | The highest level of each family of phenomena of the API (`GET /v1/families`): `{"rain-flood": "red", "thunderstorm": "yellow", "wind": "yellow"}`. Rain and floods are one family, as are snow, ice and avalanches; fog is its own; a warning whose phenomenon is unknown is `other`; a tsunami message is `tsunami`. A warning without a level is not in it. |
| `vigilances` | The warnings, highest level first, at most 20: `id`, `source`, `country`, `level`, `phenomenon`, `headline`, `event`, `area`, `onset`, `expires`, `issuer`, `web`, `distance_km` (a river section). A value the source did not give is left out. |
| `omitted` | How many warnings the list left out. The list is cut by count (20) and by size (12,000 bytes of attributes, well under the 16 KiB the recorder keeps), so the attributes can never make the state unrecordable. |
| `sources` | For each source asked: `source`, `country`, `status` (`ok`, `stale` when the last answer is served, `unavailable`). |
| `not_matchable` | Warnings listed by the sources that cannot be matched to a point. |
| `note`, `disclaimer` | The API's own notice when no source covers the place, and the notice the sources ask to publish (MeteoAlarm). |
| `server_time`, `unreadable` | The instance's clock at the read, and how many items of the answer could not be read (absent when 0). |

`headline` is the issuer's own headline in the language of Home Assistant, else in English, else in the first language the
issuer gave. It is never translated here. It is cut to 300 characters for display only. The long texts (description and
instruction) are not kept in the attributes: ask the API (`/v1/vigilances`) or follow `web`.

**Why attributes and not one sensor per phenomenon?** There are twelve groups and a place sees two or three of them at a
time, so twelve sensors would be mostly `none`. One attribute keeps the entity list short, is read the same way by a
template (`state_attr('sensor.vigie_home_maximum_level', 'by_phenomenon').wind`), and bounds the size.

### Warning active (binary sensor)

`on` when the maximum level is yellow or above, `off` when it is `none`, unknown when the level is unknown (the same rules).

### Vigilance (event)

Fires `new`, `upgraded` or `ended`. The event data are those of one item of `vigilances` (`level`, `phenomenon`,
`headline`, `area`, `onset`, `expires`, `source`...), plus `previous_level` for `upgraded`.

The events come from comparing each read of the instance with the one before. The rules are the API's own for its pushes:

- the first read after the start of Home Assistant (or after a change of the entry) is the **baseline**: what was
  already in force fires nothing;
- the same warning is recognised by its source, country, phenomenon, the source's own word for it and its area codes,
  **not by its identifier**: MeteoAlarm re-issues a warning as a new document, with a new identifier, when its level
  changes, so an upgrade would otherwise look like one warning ending and another one starting. Two warnings of the
  same kind for the same area (one in force, one tomorrow) are one warning, the stronger;
- `upgraded` is a higher level, both known; a lower level is not an event (the sensor shows it);
- a source that does not answer ends nothing: what it said before stands until it answers again, and a source that
  answers for the first time after the baseline sets its own baseline;
- when one read brings several changes, each one is delivered as its own event.

Example, verified by the tests (`tests_ha/test_documentation.py` plays it): a notification when a warning of at least
orange appears or goes up. The entity id is the one of an entry named "Vigie Home".

<!-- example:automation -->
```yaml
alias: Vigie, orange or red warning
triggers:
  - trigger: state
    entity_id: event.vigie_home_vigilance
conditions:
  - condition: template
    value_template: >-
      {{ trigger.to_state.attributes.event_type in ['new', 'upgraded']
         and trigger.to_state.attributes.level in ['orange', 'red'] }}
actions:
  - action: notify.notify
    data:
      title: "Vigie: {{ trigger.to_state.attributes.level }}"
      message: >-
        {{ trigger.to_state.attributes.headline }}
        ({{ trigger.to_state.attributes.area }})
```

### Strongest earthquake (sensor)

The magnitude of the strongest earthquake within the radius in the last 24 hours, as its sources reported it (USGS and
EMSC; one earthquake when both report it). Its attributes: `title` (the source's words, magnitude and region), `time`,
`distance_km` and `bearing` from the place (calculated by Vigie), `depth_km`, `magnitude_type`, `latitude` and `longitude`
of the epicentre (so the map card shows it), `reported_by`, then `count`, at most ten `earthquakes` (the strongest first;
`omitted` says how many are left out), the settings (`radius_km`, `min_magnitude`, `hours`), `sources` (whether USGS and
EMSC answered) and `attribution`, the credit of the sources.

It is **unknown when no earthquake matches**, not 0: a magnitude nobody reported is not a number. `count` is then 0 when a
source answered, and null when none did (nothing can be said).

### Earthquake (event)

Fires `new_earthquake` with the earthquake's data (`id`, `title`, `magnitude`, `time`, `distance_km`, `bearing`, `depth_km`,
`latitude`, `longitude`, `sources`), **once per earthquake**:

- the first read after the start of Home Assistant is the **baseline**: an earthquake of before fires nothing;
- an earthquake read again fires nothing, nor does the same earthquake reported later by the other source under its own
  identifier (within two minutes and 100 km, the rule the API merges them by);
- nothing fires under the minimum magnitude.

An example: a notification for an earthquake of magnitude 5.5 or more.

<!-- example:earthquake-automation -->
```yaml
alias: Vigie, strong earthquake nearby
triggers:
  - trigger: state
    entity_id: event.vigie_home_earthquake
conditions:
  - condition: template
    value_template: >-
      {{ trigger.to_state.attributes.event_type == 'new_earthquake'
         and trigger.to_state.attributes.magnitude | float(0) >= 5.5 }}
actions:
  - action: notify.notify
    data:
      title: "Vigie: earthquake"
      message: >-
        {{ trigger.to_state.attributes.title }}
        ({{ trigger.to_state.attributes.distance_km }} km)
```

### Pre-alert, experimental and unofficial (sensor, only when its option is ticked)

**Not an official warning.** Levels computed by Vigie from the ECMWF forecast for the province of Turkey the place is in,
with thresholds of its own ([ADR 0011](decisions/0011-unofficial-pre-alert-turkey-shadow-pilot.md) and its amendment): a test
being improved, for testers who are told what it is. Measured on 2026-10-25 against the orange warnings the press reported, it
reached level 2 on the day for 63 % of the warned provinces of 2026 (71 % the day before) and missed every wind warning of the
test. The official warnings of Turkey are the Turkish State Meteorological Service's (MGM): follow them.

The state is today's highest level, in Vigie's words: `none`, `watch`, `strong` or `severe` (never the MGM's colours). Its
attributes: `experimental` (true), `official` (false), `notice`, `province`, `province_code`, `run_utc` (the ECMWF run),
`day`, `days` (each local day with each phenomenon's value, unit, level and the province's three thresholds), `quality`,
`official_warnings_url` and `attribution`. It is unknown when the run gave no level for today, and **unavailable** when the
instance does not run the pre-alert (404) or the place is not in Turkey (422). It reads the instance every 30 minutes (two
runs a day) and never changes the warning entities. Its entity id ends in `_pre_alert_experimental_unofficial`.

### Weather of the place (weather entity and three sensors, only when its option is ticked)

The values are those of `GET /v1/point` as the instance gives them: in France the models of Météo-France (AROME, ARPEGE, AROME
Outre-mer), elsewhere Open-Meteo, said by the standard `attribution` attribute. A value the answer does not give is unknown,
never filled in. The entry reads `/v1/point` every 10 minutes, the tides every 6 hours and the normals of the month every 12
hours (`/v1/tides` and `/v1/climate/normals`; their 404, inland or outside France, is kept as "nothing there" for the same time).

- **Weather** (`weather`): the current weather (condition, temperature, apparent temperature, humidity, pressure, wind, gust,
  bearing, UV index) and the **days** of the answer, dated at midnight of the place's own day. The **hours** are offered only when
  the instance gives them (`hourly`, ADR 0040); an instance without them offers the days only. The condition comes from the WMO
  code of the answer; "clear night" is decided by the sun at the place, not at the home of Home Assistant.
- **Rain in the next hour** (mm): the sum of the four 15-minute steps of the answer's nowcast (`rain_nowcast`), with the steps,
  `rain_expected_60m` and `max_mm_h` in the attributes. **Unknown when one step is unknown**, never 0.
- **Strongest gust of the next hours** (km/h): the strongest gust of the next 6 hours of `hourly` when the instance gives them,
  else of the short-term forecast (`short_term`, AROME-PI in France, its own horizon); attributes `at`, `forecast` (`hourly` or
  `short_term`) and `horizon_minutes`. Unknown when neither gives a gust: it is never made from the mean wind.
- **Next high tide** (a time): the next high water of `/v1/tides`, its `height_m`, the next low water and its height, and the
  credit of the tide model. Unknown inland.

In France and its overseas territories (where the normals answer), the entry also reads the wind measured now by the nearest
Météo-France station (`/v1/stations/wind`, in a box of 0.3° on each side of the place, some 30 km). That measure, the normals and the verdicts of a
spot (`/v1/activities/{spot_id}`, read only when a card asks for a spot within 60 km, and kept 30 minutes) go to the dashboard
card, not to entities: they are too large for the attributes of a state.

**For the card**, the integration answers three commands on Home Assistant's own WebSocket: `vigie/card/entries` and
`vigie/card/spots` (for the card's editor) and `vigie/card/data` (what the card shows for one entry, with the verdicts of
`spot`, an identifier or `auto` for the nearest spot within 20 km). The card never calls the instance itself.

## The dashboard card

The weather, the warnings, the sea and the normals of the place, on pages one swipes or picks in a bar, in the spirit of the
community's Météo-France card. It ships with the integration (`custom_components/vigie/frontend/vigie-card.js`): Home
Assistant serves it and loads it on every page of its frontend, so there is **no resource to add**, and it always matches the
integration that feeds it. After an update of the integration, a reload of the page is enough.

Add it like any card: edit a dashboard, *Add card*, "Vigie". Its editor sets everything below; in YAML:

```yaml
type: custom:vigie-card
entry_id: 0123456789abcdef   # optional: the Vigie entry (the first one with the weather option when left out)
name: Home                   # optional: the name shown (else the name of the entry, without "Vigie")
pages: [home, details, alerts, sea, normal]   # optional: the pages shown, in this order
start_page: home             # optional: the page it opens on
idle: return                 # return: back to the opening page after a minute untouched; cycle: the next page every 20 s; none
spot: auto                   # auto: the nearest spot within 20 km; none; or the id of a spot (the editor lists the spots within 60 km)
weather_entity: weather.home # optional, outside France: the weather entity of the home page
```

| Page | What it shows | Where |
| --- | --- | --- |
| Home | Now (icon, temperature in its colour, feels like, wind and gusts), the next 12 hours (a temperature curve with the rain of each hour, once the instance gives the hours, API ADR 0040), the rain of the next hour (radar nowcast) and the first warning (each opens its page), the days. | Everywhere; outside France see below |
| Details | A sentence for the rain (within the hour the same nowcast as the home page, AROME-PI after it: the two can disagree, the card says one thing) and one for the gusts, then the next hours by 15 minutes (AROME-PI: precipitation, gusts), hail and visibility, and the chances per 3 hours of rain and of gusts of 56 km/h or more (PE-AROME). | France |
| Alerts | The official level, one row per phenomenon with its end on a timeline, the headline of the issuer as it was issued, the link to its bulletin; in France the river sections under warning nearby (Vigicrues); the earthquakes nearby. | Everywhere |
| Sea | The tide of the next 24 hours (high and low waters; in France the estimated coefficient), the waves, and the verdicts of the spot for surf, kite, boat and shore fishing, with the reason when it is not "good". | Near the coast |
| Normal? | Today's highest and lowest against the normals of the month (nearest Météo-France station), the forecast gusts against the record of the month, the wind measured now nearby. | France |

**A page with nothing for the place is not shown**, even ticked: inland there is no Sea page, outside France no Details nor
Normal?. Outside France, the home page shows the weather of the weather entity chosen in the editor (the weather integration you
already use), said as such; without one, it shows the warnings, the earthquakes and the tide.

It follows the language of Home Assistant (French or English) and its dark mode (two themes of the Vigie family); its
background is the dashboard's. From 640 px wide it takes the layout of a wall screen (the bar at the top right, the hours
beside "now"). Swipe left or right, or use the arrow keys, to change pages. Every page credits the providers of what it
shows, relayed by the instance.

Its limits are those of the data: it shows the **official** level only; the tide is the open sea's, computed by the instance
(in a bay the real tide can come an hour or more later, and it is not for navigation); the verdicts of a spot are rules of
Vigie, not a safety advice; "milder" or "cooler than normal" is a rule of the card (more than 1 °C from the normals on average),
not of Météo-France. A value the data do not give is not drawn.

## Push and poll

The integration is declared `cloud_polling`: by default it reads the public instance over the Internet, and polls it. A
push comes on top only from an instance that runs its scheduler. `WS /v1/stream` of the API tells a client that follows a place when a warning
for it appeared, went up or ended, **but only when the instance runs its scheduler** (`VIGIE_SCHEDULER=1`, see the
README). So:

- The integration asks `/v1/health`; if `scheduler.running` is true, it opens the stream, follows the place, and each
  `vigilance_push` for that place makes it **read `/v1/vigilances` again at once** (with your river radius). The push is a
  hint to read, not the data: there is one place that computes the states and the events.
- It reconnects with a growing delay (5 s up to 5 minutes) when the stream breaks, and asks `/v1/health` again every 15
  minutes when the instance has no scheduler.
- **The poll never stops**, at the interval you set. It is the fallback, and the whole mechanism for an instance
  without a scheduler (the instance is then read every 5 minutes by default, and the stream is not even opened:
  opening it would make the instance build its whole snapshot for nothing).

The cost for the instance is one cached read per entry and interval; the sources are asked as rarely as their own
rhythm allows (see "Calls to the providers" in the README).

The weather of the place has no push: it is read every 10 minutes. With its option ticked, one place costs the instance about
144 reads of `/v1/point` a day, 144 of `/v1/stations/wind` in France, 4 of `/v1/tides`, 2 of `/v1/climate/normals`, 1 of
`/v1/surf/spots`, and at most 48 of `/v1/activities` per spot a card shows (ADR 0042). These answers are cached by the instance too.

## Try it without waiting for a warning

`POST /v1/simulate` of the API injects hazards (earthquakes, tsunamis...), not official warnings: it cannot make
`/v1/vigilances` say anything, and it is off unless the instance sets `VIGIE_ENABLE_SIMULATION=1`. To see the entities and
the events work, this repository has a fake instance that serves the two routes the integration reads, with the warnings
you choose, **all labelled `SIMULATION`**, and no contact with any provider:

```
python tools/fake_instance.py --port 8099
```

Then add a second entry of the integration (another name, any place) with the address of the machine that runs it, for
example `http://<PC address>:8099`, and change the warnings (PowerShell 5.1):

```
Invoke-RestMethod -Method Post "http://<PC address>:8099/fake/set?phenomenon=wind&level=YELLOW"
Invoke-RestMethod -Method Post "http://<PC address>:8099/fake/set?phenomenon=wind&level=ORANGE"
Invoke-RestMethod -Method Post "http://<PC address>:8099/fake/remove?phenomenon=wind"
```

At the next poll (up to the interval; lower it to 1 minute in the options) the sensor goes to yellow and the event entity
fires `new`, then `upgraded` (with `previous_level`), then `ended`. (`tests_ha/test_fake_instance.py` plays exactly this
over real HTTP.) Delete the entry when done. The fake instance has no
authentication: run it on a trusted network. It does not serve the weather: for its entry, the weather entity and its three
sensors stay unavailable (or untick the option), and the warnings work as described.

**To see the card without the public instance**, start it with `--replay tests_ha/fixtures`: it then also serves the weather
routes, from the real answers recorded for Biarritz on 2026-11-22 (and the warnings in force then), every time in them moved to
now. Give the entry the place of Biarritz. It is a replay of an old read, not today's weather: say so wherever you show it.
The `card` job of the workflow does exactly this in a real Home Assistant (`tools/card_in_home_assistant.py`).

## Limits

- **You need an instance**, and it must be reachable from Home Assistant. The integration does not start one.
- **It only reads.** It asks `GET` routes and follows its place on the stream; it sends no `POST`, `PUT`, `PATCH` or `DELETE` (a test checks it), so an
  instance that sets `VIGIE_ADMIN_TOKEN` (every write then asks for it) needs nothing in the integration.
- **Some warnings cannot be matched to a point.** Warnings that name only MeteoAlarm `EMMA_ID` codes (Italy, Spain,
  Austria, Poland...) have no bundled shape ([`SOURCES.md`](../SOURCES.md)), and the NWS alerts that name zones (UGC) instead of a
  polygon are not matched either. For such a place the sensor is *unknown* while the sources list some (`not_matchable`
  says how many), never `none`: an empty list is not "nothing to fear".
- **Canada (ECCC)** is read through MSC GeoMet, which did not answer during one check on 2026-10-20. When a source does not
  answer, the sensor is unknown (or the other sources' level, if there are any), and `sources` says `unavailable`.
- **France has two sources** that say two things: MeteoAlarm, a colour per département, and Vigicrues, a colour per river
  section. When a river section says a flood that the département's MeteoAlarm "flooding" warning already said, the API
  drops the département's warning from its answer, so you can see an `ended` for it next to a `new` for the section.
- **Announced warnings count.** A warning for tomorrow is in the level and the list; compare its `onset` with the time
  to tell it from one in force.
- **The words are the issuer's.** Only the headline is kept in the attributes, in one language; the API has all of them.
- **The first read is the baseline.** After a restart of Home Assistant, a warning already in force does not fire `new`.
- **Diagnostics** (the three dots of the entry, Download diagnostics) redact the coordinates and the address of the
  instance, and keep the warnings as read; their area is the name of a region.
- **Earthquakes are not warnings.** A magnitude says how strong an earthquake was, not what it did where you are. The feeds
  list nothing below magnitude 4.5, USGS keeps the last day and EMSC its last 40 events; an earthquake they no longer list
  comes from the history the instance kept, so an instance started recently knows less. They are read at the poll
  interval: there is no push for them.
- **An older instance** without `/v1/earthquakes` leaves the two earthquake entities unavailable; the warnings work as before.
- **The weather is a forecast, not a warning.** The weather entity and its sensors never change the warning entities, and the
  rain of the next hour or a gust are not official thresholds. When `/v1/point` cannot be read, the weather entity and its
  sensors are unavailable and the warnings are read as usual.

## Develop

The integration is in this repository on purpose: one version, and the answers its tests use are checked against the
API's own models (`tests/test_home_assistant_contract.py`, in the API's CI). See
[ADR 0004](decisions/0004-home-assistant-integration-same-repository.md).

```
python -m pytest                       # the API's tests (Python 3.12 or newer); never installs Home Assistant
python -m ruff check .                 # lint of everything, the integration included
python -m venv .venv-ha                # the integration's tests: their own environment, Python 3.14.2 or newer
.venv-ha/bin/pip install -r tests_ha/requirements.txt
.venv-ha/bin/python -m pytest tests_ha
```

`tests_ha/requirements.txt` pins `pytest-homeassistant-custom-component` to the version that tracks one Home Assistant
release (now 0.13.373 for 2026.10.2, which needs Python 3.14.2 or newer; this is why the CI has its own job). The
workflow `.github/workflows/home-assistant.yml` runs the tests, **hassfest** and the **HACS validation**. The HACS action
refuses a private repository ("can only validate public repositories"), so that job is skipped while the repository is
private; it runs since the repository is public. `brands` is ignored (no image in Home Assistant's brands repository; the
integration carries its own, `custom_components/vigie/brand/`, which Home Assistant 2026.10 and later shows); the topics are
checked.

The workflow's **`card` job** tries the dashboard card in a real Home Assistant: its official container (the version is pinned
in the workflow), the integration mounted, the fake instance replaying the recorded answers, and Playwright going through every
page at a phone width and a wall width, in both themes. It fails when the card does not load, shows no page or logs an error;
its captures are kept as the artifact `card-captures`, to be looked at (a green run does not say the card looks right). Locally,
with a Home Assistant of your own on port 8123 and the fake instance on 8099:

```
pip install playwright && python -m playwright install chromium
python tools/card_in_home_assistant.py --ha http://127.0.0.1:8123 --instance http://127.0.0.1:8099 --out captures
```

It finishes the onboarding of a fresh Home Assistant: run it on a throwaway one, never on your own installation. Home
Assistant is not built for Windows: its test plugin needs the Unix modules `fcntl` and `resource`, and pytest-socket
refuses the socket pair that the Windows event loop opens. For a run on Windows only, `tests_ha/windows/` holds three
tiny stubs for them:

```
$env:PYTHONPATH = "tests_ha\windows"
.venv-ha\Scripts\python.exe -m pytest tests_ha -p winshim
```

The Linux run of the CI is the one that counts.
