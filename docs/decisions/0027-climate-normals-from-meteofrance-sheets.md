# 0027. Climate normals and records from the Météo-France station sheets

- Status: accepted
- Date: 2026-11-17

## Context

"Is it normal?" (lot E3 of the product plan) needs, for a place, what a month usually looks like and how far a day can go.
The plan first named ERA5 (through Open-Meteo or Copernicus). In France, Météo-France publishes the answer itself: the
"Fiches climatologiques" on data.gouv.fr, one sheet per station (1,578 on 2026-11-17, metropolitan France and overseas),
with the monthly normals 1991-2020 (or a shorter period, named block by block) and the records with their day, under
Licence Ouverte 2.0 (commercial use allowed, source and date named). The sheets are refreshed monthly; each is a small
semicolon text file (about 6 KB).

## Decision

1. **The sheets are bundled, not fetched per request.** `tools/build_climate_normals.py` reads every sheet (the list of
   stations and the resources of the dataset, since the list misses a few sheets) and writes
   `app/data/meteofrance-climate-normals.json.gz` (about 650 KB). `GET /v1/climate/normals` asks no provider. The bundle is
   rebuilt by hand when a fresher edition is wanted, as for the other bundled data.
2. **Only the sheet's values are served, with their period.** Mean maximum, minimum and mean temperature, rain of the month,
   average days of rain (1 mm), frost (Tn <= 0 °C) and heat (Tx >= 30 °C), sunshine; records of heat, cold, rain in one day and
   gust, with their day and the span of years they cover. A value the sheet marks missing is `null`; a `.` is the sheet's zero.
3. **What Vigie adds is named in `derived`:**
   - the station: the nearest one **that still measures** within `max_distance_km` (50 km by default), else the nearest one
     there. A station "still measures" when its records run to within 45 days of the latest edition of the sheets (about 330
     stations stopped earlier: their normals stay valid, their records do not move any more);
   - its distance;
   - `comparison`, when the caller gives a value (`tmax`, `tmin`, `rain_mm`): the difference with the normal of the month and
     whether the value goes past the record of the month. Rain of one day is set only against the record: the normal is the
     rain of a whole month. `beats_record` is `null` for a station that stopped measuring.
4. **The route does not fetch today's value itself.** The client already has it (`/v1/point`, its own sensors, Home
   Assistant) and passes it; this keeps the route free of any provider call and of any guess about which value of the day is
   meant (observed, forecast, at which hour).
5. **Outside the sheets nothing is estimated**: farther than `max_distance_km` from any sheet, `status` is
   `outside_coverage` with the distance to the nearest one. ERA5 for the rest of the world stays an open option of lot E3.

## Consequences

- A normal is the mean of the month, not of one day: a mild day in early November against the November mean reads warmer than
  it is against the first days of the month. The answer says so (`note`).
- The station can stand at another altitude than the place (`station.altitude_m`); in the mountains the difference can be
  several degrees. Choosing by altitude as well as distance is left for a measured follow-up.
- The bundle ages by one month at most behind Météo-France when rebuilt monthly; `dataset_updated` and `sheet_edited` say which
  edition answers.
