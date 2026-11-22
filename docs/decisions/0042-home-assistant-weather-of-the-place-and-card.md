# 0042. Home Assistant: the weather of the place behind an option, and a card fed over Home Assistant's WebSocket

- Status: accepted
- Date: 2026-11-22
- Number: given by `scripts/prochain_adr.py` on 2026-11-22.

## Context

The integration (`custom_components/vigie`, [ADR 0004](0004-home-assistant-integration-same-repository.md)) read the official
warnings and the earthquakes of one place, and created no weather entity on purpose: "keep the weather integration you already
use". The wish now is a dashboard card in the spirit of the Météo-France card of the HACF community, brought up to date with what
the API serves: the weather of the place, the rain of the next hour, the warnings with their end, the tide and the state of the
sea, the verdicts of a spot, and the normals of the month.

What the card needs is large: one answer of `/v1/point?compact=false` is about 17 KB, the tides of a day about 10 KB, the verdicts
of one spot about 37 KB (recorded answers of 2026-11-22). Home Assistant's recorder keeps 16 KiB of attributes per state.

Three ways to bring the data to the card were weighed:

1. **The card asks the instance itself** from the browser. Every open screen is then a client of the instance, the position
   leaves from each browser, and the instance must allow the dashboard's origin (CORS).
2. **The card reads the attributes of entities.** The answers do not fit in 16 KiB; cut down, they lose what the card shows.
3. **The integration reads the instance once and the card asks Home Assistant** over the WebSocket the dashboard already has
   open.

## Decision

1. A new option of the entry, **"Weather of the place and dashboard card"** (`weather_and_card`), **on by default**. When it is
   on, the entry reads `/v1/point` (with `compact=false` and `marine=true`) every 10 minutes, the tides and the normals of the
   month far less often (6 and 12 hours; a 404 inland or outside France is kept as "nothing there" for the same time), and the
   wind measured by the nearest Météo-France station only where the normals answer (France and its overseas territories).
2. With it come a **weather entity** (current weather, the days, and the hours once the API gives them, ADR 0040) and three
   sensors: **rain in the next hour** (the sum of the 15-minute steps; unknown when one step is unknown, never 0), **strongest
   gust of the next hours** (the hours first, else the short-term forecast; never made from the mean wind) and **next high
   tide**. An entry that unticks the option keeps the warnings and the earthquakes only, and reads none of these routes: someone
   who prefers another weather integration keeps it.
3. The card does not call the instance (way 3). The integration answers three **WebSocket commands**: `vigie/card/entries` and
   `vigie/card/spots` for the card's editor, `vigie/card/data` for what the card shows. The data are those the entry has already
   read; only the verdicts of a spot (`/v1/activities/{spot_id}`) are read on the card's demand, for a spot within 60 km of the
   place, and kept 30 minutes. A part the instance did not give is `null`, never filled in.
4. The card is **shipped inside the integration** and registered by it, so one install (HACS or by hand) gives both, at the same
   version. It comes in a change of its own.

## Consequences

- One Home Assistant reads the instance once for every screen open on it.
- The cost for the instance, per place with the option on: 144 reads of `/v1/point` a day, 144 of `/v1/stations/wind` in France,
  4 of `/v1/tides`, 2 of `/v1/climate/normals`, 1 of `/v1/surf/spots`, and at most 48 of `/v1/activities` per spot a card shows.
  The answers are cached by the instance, so the providers are not asked more often.
- The weather entity and the card show the API's values as they are: in France the models of Météo-France, elsewhere Open-Meteo,
  said by the credit of the answer. Outside France there is no measured wind and no normals.
- The integration now depends on `websocket_api`. The tests check the recorded answers it reads against the API's models
  (`tests/test_home_assistant_contract.py`), so a change of these routes shows there.
- Without the hours of ADR 0040, the weather entity offers the days only and the gust sensor reads the short-term forecast.
