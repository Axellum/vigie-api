# 0022. The forecast swell measured against the CANDHIS buoys, and a correction only if it holds on data it never saw

- Status: proposed. The protocol and the criterion below were written before any figure of error was computed (first commit of its pull request). The result is added at the end, in a later commit, and the status then says whether the criterion was met.
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
