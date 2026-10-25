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
  official warnings and never change the three entities above.

It needs no screen, no key and no package beyond what Home Assistant ships. It does not issue anything: it reads the public
instance, or one you run yourself ([README, "Run"](../README.md#run)).

It creates no weather entity: keep the weather integration you already use in Home Assistant (Open-Meteo, Met.no); Vigie
brings the warnings.

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
| Experimental pre-alert for Turkey (unofficial) | off | **A test, being improved, not an official warning.** Adds the sensor below for a place in Turkey, read from an instance that runs the pre-alert (`VIGIE_PREALERT_TR=1`; the public instance may or may not). |

Changing an option reloads the entry. **Reconfigure** (the three dots of the entry) changes the address, the place or
the name without losing the entities.

The screens are in English and French (`translations/`), following the language of Home Assistant. The entity ids are
built from the translated names: with an entry named "Vigie Home" and English, `sensor.vigie_home_maximum_level`,
`binary_sensor.vigie_home_warning_active`, `event.vigie_home_vigilance`, `sensor.vigie_home_strongest_earthquake` and
`event.vigie_home_earthquake` (in French: `..._niveau_maximal`, `..._vigilance_active`, `..._vigilance`,
`..._seisme_le_plus_fort`, `..._seisme`).

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

## Push and poll

The integration is declared `local_push`. `WS /v1/stream` of the API tells a client that follows a place when a warning
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
authentication: run it on a trusted network.

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
private; it runs since the repository is public. `brands` is ignored (no brand image is published); the topics are
checked. Home
Assistant is not built for Windows: its test plugin needs the Unix modules `fcntl` and `resource`, and pytest-socket
refuses the socket pair that the Windows event loop opens. For a run on Windows only, `tests_ha/windows/` holds three
tiny stubs for them:

```
$env:PYTHONPATH = "tests_ha\windows"
.venv-ha\Scripts\python.exe -m pytest tests_ha -p winshim
```

The Linux run of the CI is the one that counts.
