# 0022. The forecast swell measured against the CANDHIS buoys, and a correction only if it holds on data it never saw

- Status: accepted. The protocol and the criterion were written before any figure of error was computed (first commit of its pull request). **The criterion is met for 8 of the 19 pairs that had data (5 of 13 distinct cell-and-buoy combinations): a factor of 1.16 to 1.27 lowers the held-out error of the height by 17.5 to 35 % on average at those pairs.** Nothing is built by this decision: whether to build the opt-in correction, and for which pairs, is the owner's. Measure: [`docs/measurements/surf-error-2026-11.md`](../measurements/surf-error-2026-11.md).
- Date: 2026-11-09
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

The surf pages serve the wave model of Météo-France (MFWAM, through Open-Meteo Marine, `models=meteofrance_wave`) at the coordinates of each of the 51 spots
(`app/collectors/surf.py`). Nothing says yet how close that is to the sea. The CANDHIS network of Cerema measures it with wave buoys, and publishes the
records under the Licence Ouverte 2.0 to whoever asks for a key; `GET /v1/surf/buoys` already says which buoy is nearest to each spot (the list of stations is `app/data/candhis-stations.json`).

Read on 2026-11-09, before this was written, only to know what exists (no error was computed):

- CANDHIS `getCampTD.php` ("temps différé") answers up to 12 months per request, one record every 30 minutes on the buoy of Anglet, with `HM0`, `TP`, `T02`, `THETAP`,
  `THETAM` and a `QUALITE` column the guide does not explain; `getCampDispo.php` lists the months it holds (Anglet: up to October 2026). The records of
  `getCampTR.php` ("temps réel") carry fewer columns and no quality column.
- Open-Meteo Marine answers past dates for `meteofrance_wave`, and the variables `wave_height_previous_dayN` that the Previous Runs API defines as the value predicted
  24 h × N before (the same definition as [`docs/measurements/forecast-models-2026-10.md`](../measurements/forecast-models-2026-10.md)). On the cell of Anglet they were filled from
  mid-November 2025, completely from 2025-12-01.
- 20 of the 51 spots have a located real-time buoy within 30 km (`/v1/surf/buoys`, `comparable`), served by 7 buoys.

The owner asked for the measure to be used to refine the surf data. A forecast cannot be said to be "refined" without a measure of its error on data the refinement
never saw, and the first thing that measure gives, whatever its outcome, is a statement of how far the forecast is from the sea.

## Decision

1. **What is compared.** For every pair (surf spot, nearest located real-time buoy within 30 km), the hours of 2025-12-01 to 2026-10-31 (UTC):
   - the **forecast**: `wave_height` of MFWAM at the spot, as the API asks for it (same model, same coordinates), taken from `wave_height_previous_dayN` for N = 1, 3 and 5
     (forecasts made 24, 72 and 120 hours before), and, for information, the unsuffixed values of the past dates ("lead 0": how Open-Meteo builds them is not verified);
   - the **measure**: `HM0` of CANDHIS `getCampTD.php` (`H13D` for a buoy that has no `HM0`, said in the result), the records dated exactly on the hour, only those whose
     `QUALITE` is 1 (99.7 % of the rows of Anglet over one month; the guide does not say what the codes mean, so the figures without the filter are given too).
   Nothing is interpolated or filled: an hour without both values is left out. The buoy is where it floats, not at the spot: how sheltered the spot is compared with it is not judged.
2. **What is scored**, per pair, per lead and per class of the buoy's height (under 1 m, 1 to 2 m, 2 to 3 m, 3 m and over): the number of hours, the bias (forecast minus buoy), the
   mean absolute error, the root mean square error, the correlation, the ratio of the means and the share of hours within 0.5 m. A reference: persistence, the value the buoy read N × 24 h
   before the hour. The period (`wave_period` against `T02` and `TP`) and the direction (`wave_direction` against `THETAM` and `THETAP`) are described only: their definitions differ between a
   model and a buoy, and no correction of them is decided here.
3. **Data it never saw.** The window is cut in blocks of 14 days from 2025-12-01; the even blocks are fold A, the odd blocks fold B. A correction is fitted on one fold and scored on the other,
   both ways (A to B and B to A). Every figure that judges a correction is a held-out figure.
4. **The one correction tried**: a factor per pair, `corrected = a × forecast`, with `a` = the sum of the buoy's heights over the sum of the forecast's, on the training fold, the leads 1, 3 and 5 together
   (a forecast is served at every lead of 7 days; the same `a` is applied to all of them). No other form (an offset, a height-dependent factor, a different factor per lead) is tried here; if the
   measure shows a bias that depends on the height, that is a reason for a further decision, not for a quiet extension of this one.
5. **The criterion.** A pair's factor is kept only if, **in each direction of the cross-fit and at each of the leads 1, 3 and 5 days**:
   - the held-out root mean square error of the height falls by at least **5 %** (a gain of ours: the smallest worth a constant per spot; not measured);
   - and the held-out root mean square error over the hours the buoy read 2 m or more **does not rise**;
   - with at least 1,000 held-out hours at that lead, of which at least 100 with 2 m or more. A pair with fewer is "not enough data" and is not corrected.
   A spot that does not meet it keeps the forecast as it is served. The pairs that pass are chosen on the very hours that report their gain, so the gain of the retained pairs is
   a little optimistic; asking for both directions is the guard, and the factors that would be served are refitted on the whole window, which is not scored again.
6. **What follows.** This decision changes no value the API serves. If at least one pair meets the criterion, a later lot builds an opt-in correction on the pattern of [ADR 0017](0017-tide-corrected-by-the-nearest-gauge.md)
   and [ADR 0019](0019-tide-calibration-per-port.md): constants of the retained spots bundled as data and built by the tool, a `correction` object that is never silent, off by default (the choice of the default
   is the owner's). If none does, nothing is built and the measure stands as the statement of the forecast's error.
7. **The requests.** `tools/measure_surf_error.py` asks CANDHIS once per buoy (7 of the 150 requests a day of the key, kept in a cache folder so that a second run asks nothing) and Open-Meteo once per spot
   (about 29 calls each under the rule of `app/collectors/weights.py`: 12 variables, 335 days). The report credits both sources and publishes statistics only, not the buoys' records.

## Not measured, so not claimed

- Leads under 24 hours: the Previous Runs API has none. The forecast archive of the instance (`surf_forecast_archive`, filled since 2026-11-08) will, in a later measure.
- The sea at the spot: the buoy is up to 30 km away and offshore.
- The swell partitions (`swell_wave_*` against the systems S1 to S4 of the buoy), the period and the direction.
- More than 11 months of one year: no second winter, and few storms, whose hours are where an error matters most.
- That the `QUALITE` codes mean what 1 suggests; that Open-Meteo's unsuffixed past values are an analysis.

## Consequences

- One tool, `tools/measure_surf_error.py`, its tests (no network), and a report, `docs/measurements/surf-error-2026-11.md`, with its results file.
- No change to the API, its contract, its sources list or its quotas (the tool runs from a workstation, not from the instance).

## Result (measured on 2026-11-09; the tool and its tests were committed before it was run)

19 of the 20 pairs had data; **Royan has none**: its delayed-time records hold only December 2025, all with `QUALITE` 5, so no hour is scored there. Over the 135,199 pair-hours of the 19 others:

- **The forecast is close to the buoy, and low.** At 1 day the root mean square error is 0.38 m, the correlation 0.96, the forecast is within 0.5 m of the buoy 85.7 % of the hours; at 3 days 0.41 m (83.9 %), at 5 days 0.49 m (79.9 %).
  Persistence (the height the buoy read N days before) is 0.92, 1.18 and 1.28 m: the forecast is worth far more than that at every lead.
  **It reads about 10 % under the buoy** (forecast over buoy 0.90 at 1 day, 0.89 at 5 days) **and more as the sea grows**: 0.97 when the buoy reads under 1 m, 0.92, 0.90, **0.88 over 3 m (bias -0.50 m, RMSE 0.73 m)**. It is under the buoy in every month (0.85 in June to 0.94 in March).
  By buoy at 1 day: Les Pierres Noires 0.80 (RMSE 0.58 m), Plateau du Four 0.85, Anglet 0.90, Île de Batz and Saint-Jean-de-Luz 0.93, Cap Ferret 0.96.
- **The factor, held out.** 8 pairs meet the criterion in both directions at the three leads (the 11 others with data fail it, mostly by a gain under 5 %: their factors on the whole window are 0.97 to 1.11, so their forecast is already close to the buoy):
  Plage du Moulin de la Rive (Île de Batz), Plage du Minou and Plage de Pen Hat (Les Pierres Noires), Plage de la Govelle (Plateau du Four), Plage Ondres-Océan and Plage du Métro,
  Plage du Port Vieux and Plage de la Marbella (Anglet). Factors on the whole window 1.16 to 1.27. Over the 8 pairs the held-out error falls from 0.47 to 0.29 m at 1 day (-38.5 %), 0.49 to 0.35 m at 3 days (-29.8 %),
  0.57 to 0.43 m at 5 days (-25.2 %), and on the hours of 2 m or more from 0.73 to 0.42, 0.75 to 0.51 and 0.90 to 0.65 m. The smallest gain of any pair, direction and lead is 7.4 %, the largest 47.1 %.
- The period and the direction were only described: against the buoy's mean zero-crossing period the model's period is 2.2 to 3.3 s longer and against its peak period 0.9 to 1.6 s shorter (two different quantities, so nothing is decided);
  the mean direction is within 3.8 to 13.8 degrees on average.

## What the criterion does not say

- **It says the factor brings the forecast closer to the buoy, not to the spot.** Four of the eight pairs have the buoy 25 to 29 km away (Moulin de la Rive, Minou, Pen Hat, Govelle), where the sea can differ for reasons the model has and the buoy does not
  (shelter, depth, an island between them); the four others are at Anglet, 7 to 11 km from its buoy. The same model cell (43.458, -1.625, which serves Biarritz, Guéthary and Saint-Jean-de-Luz) gets a factor of 1.23 from the Anglet buoy (retained) and 1.11 from the Saint-Jean-de-Luz one (not):
  the factor depends on which buoy is the reference, and the measure cannot say which of them is right for a beach.
- The pairs that share a cell and a buoy are counted once each in the figures above; the distinct cell-and-buoy combinations are 13, and 5 are retained.
- The pairs retained are chosen on the hours that report their gain: the gain is a little optimistic (asking for both directions of the cross-fit is the guard; the gains are large against that).
- Some buoys have no reading for a share of the hours (Cap Ferret 34 %, Plateau du Four 18 %, Île de Batz 13 %), and those hours were calmer by the forecast (at Cap Ferret 1.1 m on average against 2.1 m where it read): the sample leans towards the rougher sea.
  On every buoy but Royan every on-the-hour record that has a height has `QUALITE` 1, so the quality filter changes nothing but Royan.

## What follows

The criterion of point 5 is met, so point 6 applies: a later lot may build the opt-in correction, off by default. **That lot needs the owner's decision first** on whether it is all 8 pairs, or only those whose buoy is near
(the four of Anglet, within 12 km), a choice this measure cannot make: no distance was part of the criterion, and choosing one now would be choosing on the results. Until then the API serves the forecast as it is, and the measure
is the statement of its error: low by about a tenth, close in shape (correlation 0.93 to 0.96), and by far better than persistence.
