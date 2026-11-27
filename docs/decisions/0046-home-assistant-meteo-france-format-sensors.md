# 0046. Home Assistant: two sensors in the format of Météo-France's integration, for the cards of others

- Status: accepted
- Date: 2026-11-27
- Number: given by `scripts/prochain_adr.py` on 2026-11-27.
- Adds to [ADR 0042](0042-home-assistant-weather-of-the-place-and-card.md); nothing of it changes.

## Context

Several dashboard cards were written for Home Assistant's own Météo-France integration and read its two richest sensors: the
"next rain" sensor and the "weather alert" sensor. Two of them were read on 2026-11-27:

- the Météo-France weather card of HACF (`hacf-fr/lovelace-meteofrance-weather-card`) reads a `rainForecastEntity` with the
  attributes `1_hour_forecast` and `forecast_time_ref`, and an `alertEntity` with one attribute per phenomenon. It compares
  words: `"Temps sec"`, `"Pluie faible"`, `"Pluie modérée"`, `"Pluie forte"`; `"Vent violent"`, `"Pluie-inondation"`, ...;
  `"Vert"`, `"Jaune"`, `"Orange"`, `"Rouge"`.
- Weather Plus Card (`juju35/ha-weather-card`, 1.16.7) finds them by itself: a sensor whose entity id holds `next_rain` or
  `prochaine_pluie` and that has `1_hour_forecast`; a sensor with the translation key `weather_alert` (or an entity id with
  `weather_alert` or `vigilance_meteo`), a level for its state and phenomena for attributes (`src/shared/discovery.js`).

Vigie already gives the same knowledge, from the open APIs of Météo-France and from the official warnings of other countries,
but in its own shapes: the rain of the next hour in mm with its steps and the nine slots of PIAF (`slots`), the maximum level
`none` / `yellow` / `orange` / `red` with `by_phenomenon` in the API's groups. None of these cards sees them. Changing those
two sensors would break the automations written on them (`none` is not `green`), and Météo-France's words in English
attributes would be a contract of two meanings.

## Decision

1. **Two more sensors, in the exact format of Météo-France's integration** (its `sensor.py` and `meteofrance-api`, read on
   2026-11-27):
   - **Next rain** (`next_rain`, with the weather of the place): a timestamp, the start of the first part of the hour that is
     not dry, unknown when the whole hour is; `forecast_time_ref` (the start of the hour) and `1_hour_forecast`
     (`"<minutes> min"` → word). In metropolitan France the nine slots of PIAF (5 minutes for the first half hour, then 10;
     their keys are their real starts, 30, 40 and 50 for the last three); elsewhere the four 15-minute steps of the forecast
     model, from the start the API gives. The words come from the rate by the bounds of the gateway (`vigie-meteo`,
     `rain_slice_from_mm_5min`): below 0.2 mm/h dry, below 1.6 light, below 6 moderate, then heavy.
   - **Weather alert** (`weather_alert`, always): an enum `green` / `yellow` / `orange` / `red` (`green` where the maximum
     level says `none`, unknown where it is unknown); one attribute per phenomenon, Météo-France's word for it and the colour
     word of its highest level. The API's group `flood` is split by the issuer's own word: a rain that floods is
     `Pluie-inondation`, a river flood `Inondation`. Groups without a word at Météo-France (fire, fog, other) only count in the
     state.
2. **Météo-France's words stay in French whatever the language of Home Assistant**, as in Météo-France's integration: they
   are values the cards compare, not text. The names of the two entities are translated like the others, so their entity ids
   are `next_rain` / `weather_alert` in English and `prochaine_pluie` / `vigilance_meteo` in French, the ones the cards look for.
3. **Nothing else in their attributes**: a card that reads every attribute as a phenomenon finds nothing it does not know.
   The rich data stays on the maximum level and the rain of the next hour, unchanged.

## Consequences

- Those cards show Vigie's rain of the hour and warnings without any change on their side; in the overseas territories and
  abroad, the warnings of Vigie, which Météo-France's integration does not have (metropolitan departments only).
- Two entities more per place (one without the weather option). Their unique ids end in `_next_rain` and `_weather_alert`.
- Abroad the hour is a forecast model's, not a radar's, as for the rain of the next hour; the documentation says so.
- If Météo-France's integration changes its format, these two sensors follow it, not the other way round: the tests
  (`tests_ha/test_meteo_france_format.py`) hold the words and keys of its version of 2026-11-27.
