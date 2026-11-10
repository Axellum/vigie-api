# 0023. The sea height corrected with one factor per spot, at the eight spots that met the criterion of ADR 0022, and only if asked

- Status: accepted **as an option, off by default**. The owner chose the eight pairs on 2026-11-10, after reading the limits below; whether an instance applies it by default is also his choice (`VIGIE_SURF_CORRECTION`).
- Date: 2026-11-10
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

[ADR 0022](0022-forecast-swell-measured-against-buoys.md) measured the sea height that the surf pages serve (MFWAM, `wave_height`, at the coordinates of each spot) against the CANDHIS wave buoys over
11 months. The forecast follows the sea (correlation 0.93 to 0.96) and reads about 10 % low, and 12 % low over 3 m. A factor per spot, fitted on one half of the window and scored on the other,
met the criterion written before any figure was computed at 8 of the 19 pairs that had data (5 distinct cell-and-buoy combinations): held-out error -38.5 % at 1 day, -29.8 % at 3 days, -25.2 % at 5 days.
ADR 0022 built nothing and left two choices to the owner: whether to build the opt-in correction, and for which pairs. On 2026-11-10 he chose to build it, for all eight.

He knew these limits (ADR 0022, "What the criterion does not say"), which stay true and are repeated in the answer:

- The factor brings the forecast closer to **the buoy, not to the spot**. Four of the eight pairs have their buoy 25 to 29 km away.
- The same model cell (43.458, -1.625) gets 1.234 from the Anglet buoy (Port Vieux and Marbella, corrected) and 1.113 from the Saint-Jean-de-Luz buoy (Guéthary and Saint-Jean-de-Luz, which did not meet the criterion, so
  are **not** corrected): two spots served by one cell are treated differently, because the measure chose different reference buoys.
- The pairs were kept on the hours that report their gain, so the gain is a little optimistic.

## Decision

1. **Which spots.** The eight spots kept by the measure of ADR 0022, no other, no radius of ours:

   | Spot | Reference buoy (CANDHIS) | Distance | Factor |
   | --- | --- | --- | --- |
   | `locquirec-plage-du-moulin-de-la-rive` | 02922 Ile de Batz | 29.3 km | 1.210 |
   | `locmaria-plouzane-plage-du-minou` | 02911 Les Pierres Noires | 26.5 km | 1.267 |
   | `camaret-sur-mer-plage-de-pen-hat` | 02911 Les Pierres Noires | 26.0 km | 1.267 |
   | `le-pouliguen-plage-de-la-govelle` | 04403 Plateau du Four | 25.1 km | 1.190 |
   | `ondres-plage-ondres-ocean` | 06402 Anglet | 11.4 km | 1.159 |
   | `ondres-plage-du-metro` | 06402 Anglet | 10.1 km | 1.159 |
   | `biarritz-plage-du-port-vieux` | 06402 Anglet | 6.7 km | 1.234 |
   | `biarritz-plage-de-la-marbella` | 06402 Anglet | 7.9 km | 1.234 |

   (The factors are the ones of the whole window, as the tool computes them; the table is a copy, `app/data/surf-calibration.json` is the source.)
2. **What is corrected: one field.** `hours[].waves.height_m`, the significant height of the whole sea state, the quantity that was measured (against the buoy's `HM0`). The value as the model gave it stays in
   `hours[].waves_model_height_m`. **Nothing else is touched**: the primary and secondary swell, the wind waves, the periods and directions, the swell power, the rating of the spot, the best slot, the tide and the wind
   were not scored against the buoys (ADR 0022 only described the period and the direction, and did not compare the swell partitions) and a factor found on the whole sea is not assumed to hold for them. The answer lists them (`correction.left_as_modelled`).
3. **The factor.** The one of ADR 0022 refitted on the whole window: the sum of the buoy's heights over the sum of the forecast's, the leads of 1, 3 and 5 days together, applied alike to every hour of the 7 days.
   The constants are bundled in `app/data/surf-calibration.json` and written by `tools/measure_surf_error.py --write-calibration` from the same measure (the downloads are cached, so it replays offline). The file lists **every pair
   measured**, with `applied` and the figures that decided, as `tides-calibration.json` does for the tide ([ADR 0019](0019-tide-calibration-per-port.md)); only `applied: true` entries are used.
4. **Only on the cell that was measured.** The file keeps the model cell the forecast of each spot came from. The factor is applied only when the cell that answers now (`swell_cell`) is that one; otherwise the answer is
   the model's and `correction.reason` says `other_cell` (or `cell_unknown` when the provider gave no cell).
5. **Asked for, not the default.** `GET /v1/surf/spots/{spot_id}?correct=true` applies it. An instance that sets `VIGIE_SURF_CORRECTION=on` applies it unless the request says `correct=false`, as for the tide
   ([ADR 0017](0017-tide-corrected-by-the-nearest-gauge.md)); this switch concerns that route **only**.
6. **Never silent.** The forecast always carries a `correction` object: `applied`, or `reason` (`not_asked`, `not_calibrated`, `other_cell`, `cell_unknown`); when applied, the factor, the buoy (id, name, distance),
   the window, the held-out gain at 1, 3 and 5 days, what is left as modelled, a note that says the factor is the buoy's, and the credit. The two cases of a spot that is not corrected are told apart: `not_calibrated`
   is the spot that did not meet the criterion, or has no buoy within 30 km, or no usable measure.
7. **Where it does not reach.** `GET /v1/surf/overview`, the activity verdicts (`GET /v1/activities/…`, which read the sea height of the same hours), the embedded frame, the Home Assistant integration and the site
   do not ask for it and are unchanged. Whether the verdicts should use the corrected height is a separate decision (their thresholds were chosen on the model's heights).
8. **Credit.** The constants are Vigie's, calculated from the measures of CANDHIS (Cerema, Licence Ouverte 2.0) and the forecast of MFWAM through Open-Meteo; the credit says so in `correction.attribution` and `SOURCES.md`.
   Nothing is asked of CANDHIS at run time: the key is not needed to serve the correction.

## Checked (2026-11-10)

- `app/data/surf-calibration.json` is `calibration_of` of the published result of ADR 0022 (a test pins it), and the offline replay of the measure from its downloads gives the same result as the published one.
- The 20 pairs of the file are the 20 spots with a located real-time buoy within 30 km, with the buoy and the distance `GET /v1/surf/buoys` gives; the eight applied are the eight of the table above.
- With nothing asked, the answer is the model's (`not_asked`); asked, only `waves.height_m` and its companions change (the swell, the periods, the rating, the best slot and the tide are equal to the uncorrected answer, hour by hour).
- Against the real Open-Meteo, from an instance started on the branch: at `ondres-plage-ondres-ocean` the cell that answers is the cell of the measure (43.541664, -1.4583282), and the six hours looked at read the model's height times 1.159 (0.48 → 0.56, 0.50 → 0.58, 0.52 → 0.60) with the model's value kept beside it;
  at `soorts-hossegor-plage-sud-point-d-or` (not kept by the measure) the answer is the model's and says `not_calibrated`; the overview still lists the 51 spots without a correction.
  One spot of eight was looked at; the other seven rest on the same code and on the tests.

## Not measured, so not claimed

- That a spot sheltered from the buoy's sea, or exposed to what the buoy does not see, is better served: the measure compares the forecast with the buoy.
- The swell, the period, the direction, the rating and the verdicts after correction.
- A second winter: 11 months of one year.
- Leads under 24 hours.

## Consequences

- `GET /v1/surf/spots/{spot_id}` has a new parameter (`correct`), a new field per hour (`waves_model_height_m`, null unless corrected) and a new object (`correction`). The fields that exist keep their meaning and their value when the correction is not asked.
- One more bundled data file (`app/data/surf-calibration.json`) and one more option on the measure tool. No new request to any provider, no new quota, no new collector.
- The correction is only as good as the dated measure behind it: the file records the window, and a new measure replaces it through the same tool.
