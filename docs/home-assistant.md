# Home Assistant integration

A personal project, made with the help of AI and shared in case it helps. It comes as is, with no promise of support
or availability. **This is not an official warning service**: the integration shows the warnings that a Vigie API
instance relays from the national weather services. They can be late, incomplete or wrong. Follow the warnings of your
national authorities, and do not let an automation be the only thing a decision rests on.

The integration (`custom_components/vigie`) reads the official weather warnings of one place from an instance of this
API (`GET /v1/vigilances`, see [the README](../README.md#official-weather-warnings)) and gives Home Assistant:

- a **maximum level** sensor (`none`, `yellow`, `orange`, `red`) with the list of the warnings in its attributes;
- a **warning active** binary sensor (yellow or above);
- a **vigilance** event entity that fires `new`, `upgraded` or `ended`, to start automations.

It needs no screen, no key and no package beyond what Home Assistant ships. It does not issue anything: you run the
API yourself ([README, "Run"](../README.md#run)), on a network you trust (the API has no authentication).

Its tests run against Home Assistant 2026.10.2. **It has not been tried on a running Home Assistant installation yet**: the
first real run is the proof the tests cannot give (see "Develop" for what the tests do and do not cover).

## Install

The repository is private for now, and HACS only adds public GitHub repositories as custom repositories
([HACS, private repositories](https://www.hacs.xyz/docs/faq/private_repositories/)). Until it is public:

1. Copy the folder `custom_components/vigie` of this repository into the `custom_components` folder of your Home
   Assistant configuration (create `custom_components` if it does not exist). Home Assistant's configuration folder is the
   one that holds `configuration.yaml`. The result is `custom_components/vigie/manifest.json`.
2. **Restart Home Assistant** (a new custom integration is only found at start).
3. Settings, Devices & services, Add integration, "Vigie".

Once the repository is public, it can be added to HACS as a custom repository (category *Integration*) with its address;
HACS then installs and updates the same folder. `hacs.json` at the root of the repository is for that.

## Configure

| Field | Meaning |
| --- | --- |
| Name | Names the device and its entities. Prefilled with "Vigie" and the name of your home. |
| Address of the instance | For example `http://vigie.local:8080`. It is checked by a read of `/v1/health` (it must answer like a Vigie API); a missing `http://` is added. |
| Place | The point whose warnings are read: a warning counts when its area contains it. Prefilled with the `zone.home` coordinates; pick another point on the map for a holiday home or a boat. |

**One entry per place.** The same instance can be added again for another place, and each entry is a device of its
own. The same address and the same place (to about 11 m) is refused as already configured.

**Options** (the cog of the entry):

| Option | Default | Meaning |
| --- | --- | --- |
| Poll interval | 5 min (1 to 60) | How often the instance is read. It is also the fallback of the push (below). |
| River radius | 5 km (1 to 50) | In France, how far from the place a river section (Vigicrues) still counts. The API's own default is 5 and its maximum 50. |

Changing an option reloads the entry. **Reconfigure** (the three dots of the entry) changes the address, the place or
the name without losing the entities.

The screens are in English and French (`translations/`), following the language of Home Assistant. The entity ids are
built from the translated names: with an entry named "Vigie Home" and English, `sensor.vigie_home_maximum_level`,
`binary_sensor.vigie_home_warning_active` and `event.vigie_home_vigilance`.

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
- **HACS** reads public repositories only (see "Install").

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
refuses a private repository ("can only validate public repositories"), so that job is skipped until the repository is
public; `brands` and `topics` are then ignored (no brand image is published, the repository has no topics). Home
Assistant is not built for Windows: its test plugin needs the Unix modules `fcntl` and `resource`, and pytest-socket
refuses the socket pair that the Windows event loop opens. For a run on Windows only, `tests_ha/windows/` holds three
tiny stubs for them:

```
$env:PYTHONPATH = "tests_ha\windows"
.venv-ha\Scripts\python.exe -m pytest tests_ha -p winshim
```

The Linux run of the CI is the one that counts.
