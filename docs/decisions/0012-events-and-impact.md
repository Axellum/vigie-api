# 0012. Events instead of lines, an impact score, and the families of phenomena

- Status: accepted (one point is a question for the owner: see "Open points")
- Date: 2026-10-28

## Context

The website's home page piled the data up (audit of 2026-10-28, in the owner's product plan): 34 hazards of which 28 earthquakes of
magnitude 4.5 to 5.5, four times "Reykjanes Ridge"; in France, 29 yellow lines, among them a "ground frost" warning repeated valley by
valley in Switzerland. The plan the owner validated asks the API to **group**: one event for the warnings of one phenomenon, level and
country or region whose periods overlap, and one for the earthquakes of one sequence (same sector, a few hours). The site, the display
and Home Assistant read the same route. The plan also asks to **rank by impact** (in a world view, only what counts: official red and
orange, GDACS orange and red, a strong earthquake, felt or with a tsunami alert, a cyclone) and to expose **about fourteen families of
phenomena** for the filters (wind, rain and floods, thunderstorms, snow and ice, heat, cold, fires, coast and waves, earthquakes,
volcanoes, cyclones, tsunamis, air, space).

The constraint is the rest of this API's: the words of the issuer are never changed, a value nobody supplied is unknown, and what Vigie
works out is said to be Vigie's.

## Decision

1. **An event is ours; what is inside is not.** `GET /v1/events` returns events: a kind, the phenomenon (warnings) or the category
   (hazards), a level, a short English title, the number and names of the areas, a period, a box, the sources, and the warnings
   (`alerts`, exactly as `GET /v1/vigilances` serves them) or hazards (`hazards`, as `GET /v1/geojson/hazards` describes them) it groups.
   Every field Vigie works out is named in `derived`. The title is built from our labels and the counts (`Wind · Poland · 12 areas`): a
   client writes its own in its language from the other fields, and the source's words for the phenomenon are in `phenomenon_words`, as
   given. Nothing official is translated or shortened.
2. **How warnings are grouped.** Same country, same phenomenon (the API's own; the source's word, case and spaces aside, when the
   phenomenon is `other` or unknown, so that two unrelated "other" warnings stay apart; a tsunami message is never grouped with a
   coastal warning), same level, periods that overlap (a warning with neither end nor expiry overlaps everything after its start), and
   areas that are close: each area within **300 km** of another of the event (single linkage between the centres of the boxes around the
   areas). The distance splits the warnings of a large country (a flood in Texas and one in Washington) and keeps those of one region
   together; 300 km is a choice of ours, not taken from a source. An area whose shape this instance does not know cannot be placed: those areas make an event of their
   own, with no box, rather than a guessed place; for the UGC zones of the NWS, whose code begins with the state, they are split by state.
   The source is not part of the key: a river section of Vigicrues and a département of MeteoAlarm at the same level and time are one
   flood, and `sources` lists both.
3. **How hazards are grouped.** Earthquakes are one sequence when each lies within **100 km and 6 hours** of another of the sequence
   (single linkage in time order; "the same sector, a few hours" of the plan, put in numbers by us, not taken from a source). A tsunami of
   the warning centres is one event with the official messages that carry its VTEC code, and its level is the higher of the two; a
   message without a matching hazard stays with the warnings. Any other hazard (a cyclone, a volcano, a GDACS flood or fire, a solar
   storm) is an event of its own. When the warnings of the United States are in the answer, the hazards that only the NWS reports are
   left out: they are the same alerts as the NWS warnings.
4. **The impact score, 0 to 100.** It ranks; it is not a probability and not an official level. `impact_reasons` says, in English,
   what it rests on.

   | What | Red | Orange | Yellow | Green | Level unknown |
   | --- | --- | --- | --- | --- | --- |
   | Official warning | 90 | 60 | 20 | 0 | 10 |
   | Official tsunami message, or the hazard of a tsunami warning centre | 100 | 80 | 60 | 30 | 60 |
   | Earthquake (the API's severity: PAGER, magnitude, tsunami flag) | 85 | 60 | 30 | 5 | |
   | Tropical cyclone (NHC, GDACS, NWS) | 90 | 75 | 60 | 60 | |
   | Volcano (the alert level of the source) | 85 | 60 | 25 | 0 | |
   | Solar storm (NOAA SWPC) | 70 | 50 | 20 | 0 | |
   | Other GDACS alert (flood, fire, drought) | 90 | 65 | 30 | 10 | |
   | Any other hazard (an NWS alert outside the US warnings) | 90 | 60 | 20 | 0 | |

   The breadth of a group of warnings adds one point per doubling of its areas, up to 9, so that an orange never passes a red; green
   stays at 0. A sequence of earthquakes adds one per earthquake after the first, up to 5. A tsunami takes the higher of its hazard and
   its messages. **`min_impact=60` is the world view**: the official orange and red warnings, GDACS orange and red, an earthquake the API
   rates orange or red (magnitude 6 or more, a PAGER orange or red, or the source's tsunami flag), every tropical cyclone, a volcano at
   watch or warning, every tsunami message. "Felt" (the number of reports USGS collects) is not read by this API today, so it does not
   count; adding it would change the hazards, not this table. Every number of the table is ours.
5. **The order.** The strongest impact first, then the highest level, then the broadest (the most areas or earthquakes), then the
   earliest. `counts.by_level` counts the green apart, so that a client leaves it out of its counters, and `counts.by_family` per family.
6. **Fifteen families.** `wind`, `rain-flood`, `thunderstorm`, `snow-ice`, `heat`, `cold`, `fire`, `coastal`, `earthquake`, `volcano`,
   `cyclone`, `tsunami`, `air`, `space`, and `other` (fog, and the warnings whose phenomenon no table covers). They group the API's
   phenomena (the tables of `docs/data-sources.md`) and hazard categories, each in exactly one family (a test checks it); a tornado is in
   `thunderstorm`, an avalanche in `snow-ice`, a tsunami message in `tsunami` whatever its phenomenon. `air` is empty: no source of
   air-quality warnings is read. The family of a warning follows from its `phenomenon`, never from its words. `GET /v1/families` lists
   them and what each holds; `families=wind,rain-flood` filters the events.
7. **The scope.** With `country`, the warnings of that country (as `GET /v1/vigilances?country=` reads them) and the hazards whose
   position is in it. Without, the hazards of the world and the warnings of every country the instance serves, read through the same
   caches; the countries are not remembered, so a world view does not make the scheduler keep every feed fresh.

## Consequences

- The site, the display and Home Assistant can show "Wind · Poland · 12 areas" instead of twelve lines. On 2026-10-28 at 21:15 UTC the
  world view held 95 events instead of 528 warnings in force and 46 hazards (the 13 Swiss "Low-temperature" areas one event, the 7
  earthquakes of the Reykjanes Ridge one sequence).
- Every number above (300 km, 100 km, 6 hours, the table of the impact) is a choice of this project, written here and in
  `app/services/events.py`, not a published fact. `tests/test_events.py` and `tests/test_event_impact.py` pin each of them.
- A world view needs the warnings of every country (see "Open points").

## Open points

- **What a world view costs the providers** (a decision for the owner). Until now one request never asked for every country; a world view
  does, through the caches: a feed is asked at most once per its cache lifetime (180 s for MeteoAlarm) whatever the number of clients, and
  at most 40 new CAP documents per request. On the PC on 2026-10-28 a cold world view asked 37 MeteoAlarm feeds, 179 requests of the 7
  registry services and 40 CAP documents, and took 66 s (7 to 18 s afterwards). Whether a public instance should keep a world view fresh
  on its own clock (which would also archive every country) or only when someone asks is the owner's call; today it is only when someone
  asks.
