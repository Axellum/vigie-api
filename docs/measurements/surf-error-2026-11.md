# Measure: the forecast swell of the surf spots against the CANDHIS buoys (2026-11)

Measured on 2026-11-09 by [`tools/measure_surf_error.py`](../../tools/measure_surf_error.py) (results: `surf-error-2026-11-results.json.gz`), for
[ADR 0022](../decisions/0022-forecast-swell-measured-against-buoys.md), whose protocol and criterion were committed before the tool was run.

**Sources.** Buoy measures: Candhis, Cerema (Licence Ouverte 2.0), the delayed-time records (`getCampTD.php`); the records are not republished here, only figures computed from them.
Forecasts: Weather data by Open-Meteo.com (https://open-meteo.com/), Marine API, Météo-France MFWAM model (Generated using E.U. Copernicus Marine Service Information;
https://doi.org/10.48670/moi-00017). The figures are Vigie's.

**What is measured.** The forecast of the swell that the surf pages serve (MFWAM at the coordinates of each spot, `wave_height`) against what the nearest CANDHIS buoy read, for the 20 spots that have a located
real-time buoy within 30 km (7 buoys), from 2025-12-01 to 2026-10-31 (UTC), hour by hour. The forecast is the value Open-Meteo says was predicted 24 h x N before (`wave_height_previous_dayN`, as in
[the measure of the forecast models](forecast-models-2026-10.md)) for N = 1, 3 and 5; "lead 0" is the unsuffixed values that Open-Meteo gives for a past date (how they are built is not verified). The buoy is
`HM0` of the records dated exactly on the hour whose `QUALITE` is 1 (nothing interpolated). Bias is forecast minus buoy; "forecast / buoy" is the ratio of the means. The correction tried is one factor per pair
(sum of the buoy's heights over the sum of the forecast's, on the three leads together), fitted on one half of the window and scored on the other (blocks of 14 days, even and odd, both ways).

## Reading

- **The forecast follows the sea closely, and reads low.** Over the 135,199 pair-hours of the 19 pairs that have data: root mean square error 0.38 m at 1 day (correlation 0.96, within 0.5 m for 85.7 % of the hours),
  0.41 m at 3 days (0.95, 83.9 %), 0.49 m at 5 days (0.93, 79.9 %). Against persistence (the height the buoy read N days before: 0.92, 1.18 and 1.28 m) it is worth far more at every lead.
  The forecast is about **10 % under the buoy** (0.90 at 1 day, 0.89 at 5 days), **under it in every month** (0.85 in June to 0.94 in March), and **more under as the sea grows**: forecast / buoy 0.97 when the buoy reads under 1 m,
  0.92 from 1 to 2 m, 0.90 from 2 to 3 m, 0.88 over 3 m (bias -0.50 m, RMSE 0.73 m).
- **It differs by buoy.** At 1 day, forecast / buoy: Les Pierres Noires 0.80 (RMSE 0.58 m), Plateau du Four 0.85 (0.32), Anglet 0.90 (0.38), Île de Batz 0.93 (0.35), Saint-Jean-de-Luz 0.93 (0.30), Cap Ferret 0.96 (0.29).
  Pooled figures weigh a buoy by the number of its spots (Anglet has 7 of the 19 pairs): the table by buoy is the fairer one.
- **The factor, held out.** 8 pairs meet the criterion of the ADR in both directions at the three leads: Moulin de la Rive; Minou and Pen Hat; Govelle; Ondres-Océan and Métro; Port Vieux and Marbella
  (5 distinct cell-and-buoy combinations of 13). Factors on the whole window 1.16 to 1.27. Held-out RMSE over those pairs: 0.47 to 0.29 m at 1 day (-38.5 %), 0.49 to 0.35 m at 3 days (-29.8 %), 0.57 to 0.43 m at 5 days (-25.2 %);
  on the hours of 2 m or more 0.73 to 0.42, 0.75 to 0.51 and 0.90 to 0.65 m. By pair, direction and lead the gain runs from 7.4 % (Govelle) to 47.1 % (Minou).
  The 11 other pairs with data fail it, 45 times by a gain under 5 % and 21 times by an error that rises on the hours of 2 m or more (a pair can fail both ways, at several leads); their factors are 0.97 to 1.11. Royan has no data (see below).
- **Period and direction (described only).** Against the buoy's mean zero-crossing period (`T02`) the model's period is 2.2 to 3.3 s longer, against its peak period (`TP`) 0.9 to 1.6 s shorter: they are different
  quantities, and nothing is concluded. The mean direction is within 3.8 to 13.8 degrees on average (smallest at Anglet, largest at Île de Batz) with a bias of -10 to +6 degrees.

**Verdict: the criterion is met for 8 pairs; nothing is served on it.** Whether to build the opt-in correction, and for which pairs, is the owner's (ADR 0022, "What follows").

## What the factor does not say

- It brings the forecast closer to **the buoy, not to the spot**. Four of the eight pairs have the buoy 25 to 29 km away; the four of Anglet are 7 to 11 km from theirs. The same model cell (43.458, -1.625) gets a factor of 1.23 from the Anglet buoy
  (retained) and 1.11 from the Saint-Jean-de-Luz buoy (not): the factor depends on the reference buoy, and the measure cannot say which is right for a beach.
- Pairs that share a cell and a buoy are identical (the table of pairs shows the cell), so the 8 retained pairs are 5 independent comparisons.
- The pairs are chosen on the hours that report their gain, so the gain is a little optimistic; the guard is that the criterion holds in both directions at every lead.

## Checks made after the run

These were added to the tool's output after its first run, to read the figures; none changes a figure of the protocol.

- **Royan** (01705): its delayed-time records hold only December 2025 (1,474 records), every one with `QUALITE` 5; none is scored (kept, their RMSE at 1 day would be 0.77 m over 737 hours, see the quality table).
  `QUALITE` is not explained by the guide. On every other buoy each on-the-hour record that has a height has `QUALITE` 1, so the filter changes nothing there.
- **The clock.** The best shift of the forecast against the buoy, from -3 to +3 hours, is -1 to 2 hours and of both signs, and it does not follow the change to summer time (2026-03-29): at Anglet it goes from 0 to 1 hour, at Saint-Jean-de-Luz from 1 to 0.
  A buoy dated in local time would shift all of them the same way. The shift at Plateau du Four (2 hours, 1 in summer) may be the travel time of the swell between the buoy and a cell 25 km nearer the coast: not verified.
- **Hours without a reading.** Cap Ferret has no value for 34 % of the hours, Plateau du Four 18 %, Île de Batz 13 %, Saint-Jean-de-Luz 8 %, Les Pierres Noires 5 %, Anglet 4 %. Those hours were calmer by the forecast
  (Cap Ferret: 1.1 m on average against 2.1 m where the buoy read; share of 2 m or more 9 % against 45 %), so the scored sample leans towards the rougher sea at those buoys.

## Limits of this measure

- 11 months of one year (December to October): no second winter. The 3 m and over class has 20,483 pair-hours but few distinct storms; the number of storms was not counted.
- Leads under 24 hours are not measured (the Previous Runs API has none under one day); the forecast archive of the instance, filled since 2026-11-08, will give them. The swell partitions (`swell_wave_*` against the systems S1 to S4 of a buoy)
  and the swell period were not compared.
- The buoy measures where it floats: how sheltered the spot is compared with it is not judged, and the distance of the buoy (2 to 29 km) is not part of the criterion.
- Whether Open-Meteo's `previous_dayN` values are exactly the forecast of N days before, and what its unsuffixed past values are, rests on its documentation, not on a check.

## Tables

Produced by the tool (`--offline` from the downloads of 2026-11-09). Heights in metres; hours are pair-hours.
### Pairs

| Spot | Buoy | km | Height | Cell | Hours scored (1 d) | Hours on the hour | QUALITE 1 | Shift (h): all / winter / summer | Records of the answer | QUALITE codes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Mogueric | 02922 Ile de Batz | 4.0 | HM0 | 48.792, -4.125 | 7028 | 7028 | 7028 | -1 / -1 / -1 | 14308 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 14059, 5: 249 |
| Plage du Moulin de la Rive | 02922 Ile de Batz | 29.3 | HM0 | 48.792, -3.708 | 7028 | 7028 | 7028 | 0 / 0 / 0 | 14308 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 14059, 5: 249 |
| Plage du Minou | 02911 Les Pierres Noires | 26.5 | HM0 | 48.292, -4.625 | 7620 | 7620 | 7620 | 0 / 0 / 0 | 15245 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15240, 5: 5 |
| Plage de Pen Hat | 02911 Les Pierres Noires | 26.0 | HM0 | 48.292, -4.625 | 7620 | 7620 | 7620 | 0 / 0 / 0 | 15245 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15240, 5: 5 |
| Plage de la Govelle | 04403 Plateau du Four | 25.1 | HM0 | 47.292, -2.458 | 6591 | 6591 | 6591 | 2 / 2 / 1 | 14487 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 13169, 5: 1318 |
| Plage de Pontaillac | 01705 Royan | 2.4 | HM0 | 45.625, -1.042 | 0 | 737 | 0 | None / None / None | 1474 (2025-12-01 00:00:00 to 2025-12-31 23:30:00) | 5: 1474 |
| Zone de Surf du Grand Crohot | 03302 Cap Ferret | 23.0 | HM0 | 44.792, -1.208 | 5283 | 5283 | 5283 | 0 / 0 / 0 | 15470 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 10571, 5: 4899 |
| Zone de surf du Truc Vert | 03302 Cap Ferret | 16.3 | HM0 | 44.708, -1.292 | 5283 | 5283 | 5283 | 0 / 0 / 0 | 15470 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 10571, 5: 4899 |
| La nord | 03302 Cap Ferret | 27.2 | HM0 | 44.458, -1.292 | 5283 | 5283 | 5283 | 1 / 1 / 1 | 15470 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 10571, 5: 4899 |
| Plage Sud (Point d'Or) | 06402 Anglet | 19.8 | HM0 | 43.625, -1.458 | 7701 | 7701 | 7701 | 0 / 0 / 1 | 15406 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15399, 2: 2, 4: 4, 5: 1 |
| Plage Centrale | 06402 Anglet | 19.0 | HM0 | 43.625, -1.458 | 7701 | 7701 | 7701 | 0 / 0 / 1 | 15406 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15399, 2: 2, 4: 4, 5: 1 |
| Plage Ondres-Océan | 06402 Anglet | 11.4 | HM0 | 43.542, -1.458 | 7701 | 7701 | 7701 | 1 / 0 / 1 | 15406 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15399, 2: 2, 4: 4, 5: 1 |
| Plage du Métro | 06402 Anglet | 10.1 | HM0 | 43.542, -1.458 | 7701 | 7701 | 7701 | 1 / 0 / 1 | 15406 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15399, 2: 2, 4: 4, 5: 1 |
| Plage de Marinella | 06402 Anglet | 6.8 | HM0 | 43.542, -1.542 | 7701 | 7701 | 7701 | 0 / 0 / 1 | 15406 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15399, 2: 2, 4: 4, 5: 1 |
| Plage du Port Vieux | 06402 Anglet | 6.7 | HM0 | 43.458, -1.625 | 7701 | 7701 | 7701 | 1 / 0 / 1 | 15406 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15399, 2: 2, 4: 4, 5: 1 |
| Plage de la Marbella | 06402 Anglet | 7.9 | HM0 | 43.458, -1.625 | 7701 | 7701 | 7701 | 1 / 0 / 1 | 15406 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 15399, 2: 2, 4: 4, 5: 1 |
| Uhabia | 06403 Saint-Jean-de-Luz | 7.1 | HM0 | 43.458, -1.625 | 7389 | 7389 | 7389 | 1 / 1 / 0 | 14972 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 14781, 2: 186, 5: 5 |
| Harotzen Kosta | 06403 Saint-Jean-de-Luz | 5.8 | HM0 | 43.458, -1.625 | 7389 | 7389 | 7389 | 1 / 1 / 0 | 14972 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 14781, 2: 186, 5: 5 |
| Grande Plage | 06403 Saint-Jean-de-Luz | 2.4 | HM0 | 43.458, -1.625 | 7389 | 7389 | 7389 | 1 / 1 / 0 | 14972 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 14781, 2: 186, 5: 5 |
| Ondarraitz | 06403 Saint-Jean-de-Luz | 8.4 | HM0 | 43.458, -1.792 | 7389 | 7389 | 7389 | 0 / 0 / 0 | 14972 (2025-12-01 00:00:00 to 2026-10-31 23:30:00) | 1: 14781, 2: 186, 5: 5 |

### All pairs together

Height of the sea (the forecast of the spot against the buoy)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 135199 | -0.17 | 0.25 | 0.38 | 0.96 | 0.91 | 86.1 % | - |
| 1 d | 135199 | -0.17 | 0.26 | 0.38 | 0.96 | 0.90 | 85.7 % | 0.92 |
| 3 d | 135199 | -0.18 | 0.28 | 0.41 | 0.95 | 0.90 | 83.9 % | 1.18 |
| 5 d | 135199 | -0.20 | 0.33 | 0.49 | 0.93 | 0.89 | 79.9 % | 1.28 |

#### By class of the height the buoy read

| Buoy read | Lead | Hours | Bias (m) | RMSE (m) | Forecast / buoy |
| --- | --- | --- | --- | --- | --- |
| 0 to 1 m | 1 d | 43422 | -0.02 | 0.13 | 0.97 |
| 1 to 2 m | 1 d | 45783 | -0.13 | 0.27 | 0.92 |
| 2 to 3 m | 1 d | 25511 | -0.24 | 0.42 | 0.90 |
| 3 m and over | 1 d | 20483 | -0.50 | 0.73 | 0.88 |
| 0 to 1 m | 3 d | 43422 | -0.05 | 0.15 | 0.93 |
| 1 to 2 m | 3 d | 45783 | -0.14 | 0.31 | 0.90 |
| 2 to 3 m | 3 d | 25511 | -0.24 | 0.46 | 0.90 |
| 3 m and over | 3 d | 20483 | -0.44 | 0.78 | 0.89 |
| 0 to 1 m | 5 d | 43422 | -0.05 | 0.16 | 0.93 |
| 1 to 2 m | 5 d | 45783 | -0.16 | 0.34 | 0.89 |
| 2 to 3 m | 5 d | 25511 | -0.27 | 0.53 | 0.89 |
| 3 m and over | 5 d | 20483 | -0.54 | 0.97 | 0.87 |

#### By month, lead 1 day

| Month | Hours | Bias (m) | RMSE (m) | Forecast / buoy |
| --- | --- | --- | --- | --- |
| 2025-12 | 13858 | -0.17 | 0.44 | 0.93 |
| 2026-01 | 13816 | -0.22 | 0.51 | 0.92 |
| 2026-02 | 12500 | -0.28 | 0.53 | 0.90 |
| 2026-03 | 12872 | -0.13 | 0.36 | 0.94 |
| 2026-04 | 13196 | -0.13 | 0.33 | 0.92 |
| 2026-05 | 11401 | -0.14 | 0.26 | 0.88 |
| 2026-06 | 10888 | -0.19 | 0.30 | 0.85 |
| 2026-07 | 11873 | -0.09 | 0.21 | 0.89 |
| 2026-08 | 11587 | -0.09 | 0.20 | 0.90 |
| 2026-09 | 11904 | -0.16 | 0.30 | 0.88 |
| 2026-10 | 11304 | -0.27 | 0.49 | 0.87 |

### By buoy (its spots together)

02922 Ile de Batz (2 spots)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 14056 | -0.08 | 0.25 | 0.35 | 0.91 | 0.95 | 86.7 % | - |
| 1 d | 14056 | -0.09 | 0.25 | 0.35 | 0.91 | 0.93 | 86.5 % | 0.74 |
| 3 d | 14056 | -0.09 | 0.27 | 0.37 | 0.90 | 0.93 | 85.0 % | 0.90 |
| 5 d | 14056 | -0.12 | 0.30 | 0.44 | 0.86 | 0.91 | 81.3 % | 0.96 |

02911 Les Pierres Noires (2 spots)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 15240 | -0.42 | 0.44 | 0.58 | 0.98 | 0.80 | 64.2 % | - |
| 1 d | 15240 | -0.42 | 0.44 | 0.58 | 0.97 | 0.80 | 64.4 % | 1.00 |
| 3 d | 15240 | -0.44 | 0.46 | 0.62 | 0.96 | 0.80 | 63.0 % | 1.31 |
| 5 d | 15240 | -0.49 | 0.53 | 0.74 | 0.94 | 0.77 | 59.3 % | 1.46 |

04403 Plateau du Four (1 spots)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 6591 | -0.22 | 0.24 | 0.35 | 0.97 | 0.84 | 88.5 % | - |
| 1 d | 6591 | -0.20 | 0.22 | 0.32 | 0.97 | 0.85 | 90.4 % | 0.66 |
| 3 d | 6591 | -0.21 | 0.25 | 0.37 | 0.95 | 0.84 | 88.8 % | 0.82 |
| 5 d | 6591 | -0.24 | 0.29 | 0.44 | 0.93 | 0.82 | 84.2 % | 0.95 |

01705 Royan (1 spots)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 0 | - | - | - | - | - | - | - |
| 1 d | 0 | - | - | - | - | - | - | - |
| 3 d | 0 | - | - | - | - | - | - | - |
| 5 d | 0 | - | - | - | - | - | - | - |

03302 Cap Ferret (3 spots)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 15849 | -0.09 | 0.20 | 0.30 | 0.98 | 0.96 | 92.1 % | - |
| 1 d | 15849 | -0.09 | 0.20 | 0.29 | 0.98 | 0.96 | 92.0 % | 1.12 |
| 3 d | 15849 | -0.08 | 0.23 | 0.34 | 0.97 | 0.96 | 89.0 % | 1.49 |
| 5 d | 15849 | -0.10 | 0.29 | 0.45 | 0.95 | 0.95 | 83.9 % | 1.58 |

06402 Anglet (7 spots)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 53907 | -0.17 | 0.25 | 0.38 | 0.97 | 0.91 | 86.7 % | - |
| 1 d | 53907 | -0.18 | 0.26 | 0.38 | 0.96 | 0.90 | 85.8 % | 0.95 |
| 3 d | 53907 | -0.19 | 0.28 | 0.41 | 0.96 | 0.90 | 84.6 % | 1.22 |
| 5 d | 53907 | -0.20 | 0.31 | 0.48 | 0.94 | 0.89 | 81.3 % | 1.33 |

06403 Saint-Jean-de-Luz (4 spots)

| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 (past values) | 29556 | -0.09 | 0.20 | 0.29 | 0.96 | 0.94 | 92.1 % | - |
| 1 d | 29556 | -0.10 | 0.20 | 0.30 | 0.96 | 0.93 | 91.8 % | 0.85 |
| 3 d | 29556 | -0.11 | 0.23 | 0.34 | 0.95 | 0.93 | 88.8 % | 1.01 |
| 5 d | 29556 | -0.12 | 0.28 | 0.41 | 0.92 | 0.92 | 84.2 % | 1.12 |

### Quality filter

| Spot | RMSE 1 d, QUALITE 1 only | RMSE 1 d, every record | Hours, QUALITE 1 only | Hours, every record |
| --- | --- | --- | --- | --- |
| Mogueric | 0.31 | 0.31 | 7028 | 7028 |
| Plage du Moulin de la Rive | 0.38 | 0.38 | 7028 | 7028 |
| Plage du Minou | 0.58 | 0.58 | 7620 | 7620 |
| Plage de Pen Hat | 0.58 | 0.58 | 7620 | 7620 |
| Plage de la Govelle | 0.32 | 0.32 | 6591 | 6591 |
| Plage de Pontaillac | - | 0.77 | 0 | 737 |
| Zone de Surf du Grand Crohot | 0.33 | 0.33 | 5283 | 5283 |
| Zone de surf du Truc Vert | 0.27 | 0.27 | 5283 | 5283 |
| La nord | 0.28 | 0.28 | 5283 | 5283 |
| Plage Sud (Point d'Or) | 0.26 | 0.26 | 7701 | 7701 |
| Plage Centrale | 0.26 | 0.26 | 7701 | 7701 |
| Plage Ondres-Océan | 0.39 | 0.39 | 7701 | 7701 |
| Plage du Métro | 0.39 | 0.39 | 7701 | 7701 |
| Plage de Marinella | 0.30 | 0.30 | 7701 | 7701 |
| Plage du Port Vieux | 0.50 | 0.50 | 7701 | 7701 |
| Plage de la Marbella | 0.50 | 0.50 | 7701 | 7701 |
| Uhabia | 0.31 | 0.31 | 7389 | 7389 |
| Harotzen Kosta | 0.31 | 0.31 | 7389 | 7389 |
| Grande Plage | 0.31 | 0.31 | 7389 | 7389 |
| Ondarraitz | 0.26 | 0.26 | 7389 | 7389 |

### Period and direction (described only; hours the buoy read 1 m or more)

| Spot | Lead | Hours | Period bias vs T02 (s) | vs TP (s) | Direction bias vs mean (deg) | MAE (deg) | vs peak (deg) | MAE (deg) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Mogueric | 0 d | 4355 | 2.86 | -1.23 | -10.1 | 13.8 | -8.1 | 13.4 |
| Mogueric | 3 d | 4355 | 2.78 | -1.31 | -9.2 | 13.2 | -7.2 | 13.0 |
| Plage du Moulin de la Rive | 0 d | 4355 | 2.51 | -1.57 | -7.8 | 11.2 | -5.8 | 11.0 |
| Plage du Moulin de la Rive | 3 d | 4355 | 2.42 | -1.67 | -7.0 | 10.8 | -5.1 | 10.9 |
| Plage du Minou | 0 d | 5541 | 2.24 | -1.55 | -2.7 | 7.6 | -7.8 | 12.3 |
| Plage du Minou | 3 d | 5541 | 2.23 | -1.57 | -1.4 | 7.5 | -6.3 | 11.8 |
| Plage de Pen Hat | 0 d | 5541 | 2.24 | -1.55 | -2.7 | 7.6 | -7.8 | 12.3 |
| Plage de Pen Hat | 3 d | 5541 | 2.23 | -1.57 | -1.4 | 7.5 | -6.3 | 11.8 |
| Plage de la Govelle | 0 d | 3616 | 3.11 | -1.07 | 3.0 | 7.8 | -3.0 | 10.1 |
| Plage de la Govelle | 3 d | 3616 | 3.19 | -0.99 | 3.1 | 9.1 | -2.6 | 11.0 |
| Plage de Pontaillac | 0 d | 0 | - | - | - | - | - | - |
| Plage de Pontaillac | 3 d | 0 | - | - | - | - | - | - |
| Zone de Surf du Grand Crohot | 0 d | 4238 | 3.34 | -1.07 | 2.7 | 5.5 | 2.4 | 5.8 |
| Zone de Surf du Grand Crohot | 3 d | 4238 | 3.37 | -1.03 | 3.6 | 5.8 | 3.3 | 6.2 |
| Zone de surf du Truc Vert | 0 d | 4238 | 3.31 | -1.10 | 2.8 | 5.5 | 2.5 | 5.7 |
| Zone de surf du Truc Vert | 3 d | 4238 | 3.33 | -1.08 | 3.7 | 5.7 | 3.4 | 6.1 |
| La nord | 0 d | 4238 | 3.34 | -1.06 | 4.9 | 6.8 | 4.7 | 6.3 |
| La nord | 3 d | 4238 | 3.36 | -1.04 | 5.7 | 7.1 | 5.4 | 6.8 |
| Plage Sud (Point d'Or) | 0 d | 5225 | 3.06 | -0.91 | -0.8 | 3.8 | 0.2 | 4.4 |
| Plage Sud (Point d'Or) | 3 d | 5225 | 2.99 | -0.98 | -0.3 | 3.7 | 0.7 | 4.6 |
| Plage Centrale | 0 d | 5225 | 3.06 | -0.91 | -0.8 | 3.8 | 0.2 | 4.4 |
| Plage Centrale | 3 d | 5225 | 2.99 | -0.98 | -0.3 | 3.7 | 0.7 | 4.6 |
| Plage Ondres-Océan | 0 d | 5225 | 2.99 | -0.98 | 1.1 | 3.9 | 2.1 | 4.6 |
| Plage Ondres-Océan | 3 d | 5225 | 2.92 | -1.05 | 1.5 | 3.9 | 2.5 | 4.8 |
| Plage du Métro | 0 d | 5225 | 2.99 | -0.98 | 1.1 | 3.9 | 2.1 | 4.6 |
| Plage du Métro | 3 d | 5225 | 2.92 | -1.05 | 1.5 | 3.9 | 2.5 | 4.8 |
| Plage de Marinella | 0 d | 5225 | 3.00 | -0.97 | 1.6 | 3.8 | 2.5 | 4.8 |
| Plage de Marinella | 3 d | 5225 | 2.92 | -1.05 | 2.0 | 3.9 | 2.9 | 5.1 |
| Plage du Port Vieux | 0 d | 5225 | 2.98 | -0.99 | 6.3 | 6.8 | 7.3 | 7.9 |
| Plage du Port Vieux | 3 d | 5225 | 2.89 | -1.08 | 6.6 | 7.1 | 7.6 | 8.2 |
| Plage de la Marbella | 0 d | 5225 | 2.98 | -0.99 | 6.3 | 6.8 | 7.3 | 7.9 |
| Plage de la Marbella | 3 d | 5225 | 2.89 | -1.08 | 6.6 | 7.1 | 7.6 | 8.2 |
| Uhabia | 0 d | 4770 | 2.73 | -1.40 | -3.6 | 5.9 | -3.4 | 7.2 |
| Uhabia | 3 d | 4770 | 2.65 | -1.48 | -3.2 | 5.7 | -3.1 | 7.2 |
| Harotzen Kosta | 0 d | 4770 | 2.73 | -1.40 | -3.6 | 5.9 | -3.4 | 7.2 |
| Harotzen Kosta | 3 d | 4770 | 2.65 | -1.48 | -3.2 | 5.7 | -3.1 | 7.2 |
| Grande Plage | 0 d | 4770 | 2.73 | -1.40 | -3.6 | 5.9 | -3.4 | 7.2 |
| Grande Plage | 3 d | 4770 | 2.65 | -1.48 | -3.2 | 5.7 | -3.1 | 7.2 |
| Ondarraitz | 0 d | 4770 | 2.76 | -1.37 | -4.1 | 6.1 | -4.0 | 7.4 |
| Ondarraitz | 3 d | 4770 | 2.67 | -1.46 | -3.7 | 5.9 | -3.6 | 7.3 |

### The factor and its held-out test

Held-out RMSE of the height (m) before -> after the factor, and the gain; the same at the hours the buoy read 2 m or more.

| Spot | Direction | Factor | Lead | Hours | RMSE before -> after | Gain | Hours 2 m+ | RMSE 2 m+ before -> after |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Mogueric | A_to_B | 0.961 | 1 d | 3585 | 0.31 -> 0.31 | 0.3 % | 839 | 0.42 -> 0.45 |
| Mogueric | A_to_B | 0.961 | 3 d | 3585 | 0.34 -> 0.34 | 1.7 % | 839 | 0.49 -> 0.51 |
| Mogueric | A_to_B | 0.961 | 5 d | 3585 | 0.41 -> 0.42 | -0.8 % | 839 | 0.63 -> 0.67 |
| Mogueric | B_to_A | 0.983 | 1 d | 3443 | 0.32 -> 0.31 | 1.9 % | 598 | 0.46 -> 0.46 |
| Mogueric | B_to_A | 0.983 | 3 d | 3443 | 0.34 -> 0.34 | 1.8 % | 598 | 0.48 -> 0.48 |
| Mogueric | B_to_A | 0.983 | 5 d | 3443 | 0.39 -> 0.39 | 1.3 % | 598 | 0.61 -> 0.62 |
| Plage du Moulin de la Rive | A_to_B | 1.192 | 1 d | 3585 | 0.41 -> 0.28 | 32.8 % | 839 | 0.71 -> 0.43 |
| Plage du Moulin de la Rive | A_to_B | 1.192 | 3 d | 3585 | 0.42 -> 0.31 | 26.5 % | 839 | 0.73 -> 0.48 |
| Plage du Moulin de la Rive | A_to_B | 1.192 | 5 d | 3585 | 0.51 -> 0.40 | 21.3 % | 839 | 0.90 -> 0.65 |
| Plage du Moulin de la Rive | B_to_A | 1.225 | 1 d | 3443 | 0.35 -> 0.26 | 23.5 % | 598 | 0.62 -> 0.39 |
| Plage du Moulin de la Rive | B_to_A | 1.225 | 3 d | 3443 | 0.36 -> 0.29 | 19.7 % | 598 | 0.63 -> 0.42 |
| Plage du Moulin de la Rive | B_to_A | 1.225 | 5 d | 3443 | 0.41 -> 0.35 | 14.6 % | 598 | 0.76 -> 0.56 |
| Plage du Minou | A_to_B | 1.257 | 1 d | 3703 | 0.63 -> 0.33 | 47.1 % | 1864 | 0.85 -> 0.43 |
| Plage du Minou | A_to_B | 1.257 | 3 d | 3703 | 0.67 -> 0.43 | 35.9 % | 1864 | 0.90 -> 0.57 |
| Plage du Minou | A_to_B | 1.257 | 5 d | 3703 | 0.83 -> 0.56 | 32.3 % | 1864 | 1.12 -> 0.75 |
| Plage du Minou | B_to_A | 1.276 | 1 d | 3917 | 0.53 -> 0.34 | 35.4 % | 1625 | 0.76 -> 0.48 |
| Plage du Minou | B_to_A | 1.276 | 3 d | 3917 | 0.57 -> 0.41 | 28.5 % | 1625 | 0.81 -> 0.58 |
| Plage du Minou | B_to_A | 1.276 | 5 d | 3917 | 0.65 -> 0.47 | 27.3 % | 1625 | 0.93 -> 0.67 |
| Plage de Pen Hat | A_to_B | 1.257 | 1 d | 3703 | 0.63 -> 0.33 | 47.1 % | 1864 | 0.85 -> 0.43 |
| Plage de Pen Hat | A_to_B | 1.257 | 3 d | 3703 | 0.67 -> 0.43 | 35.9 % | 1864 | 0.90 -> 0.57 |
| Plage de Pen Hat | A_to_B | 1.257 | 5 d | 3703 | 0.83 -> 0.56 | 32.3 % | 1864 | 1.12 -> 0.75 |
| Plage de Pen Hat | B_to_A | 1.276 | 1 d | 3917 | 0.53 -> 0.34 | 35.4 % | 1625 | 0.76 -> 0.48 |
| Plage de Pen Hat | B_to_A | 1.276 | 3 d | 3917 | 0.57 -> 0.41 | 28.5 % | 1625 | 0.81 -> 0.58 |
| Plage de Pen Hat | B_to_A | 1.276 | 5 d | 3917 | 0.65 -> 0.47 | 27.3 % | 1625 | 0.93 -> 0.67 |
| Plage de la Govelle | A_to_B | 1.191 | 1 d | 3271 | 0.32 -> 0.24 | 25.5 % | 737 | 0.57 -> 0.39 |
| Plage de la Govelle | A_to_B | 1.191 | 3 d | 3271 | 0.37 -> 0.34 | 7.4 % | 737 | 0.65 -> 0.63 |
| Plage de la Govelle | A_to_B | 1.191 | 5 d | 3271 | 0.47 -> 0.41 | 12.5 % | 737 | 0.84 -> 0.73 |
| Plage de la Govelle | B_to_A | 1.189 | 1 d | 3320 | 0.32 -> 0.22 | 29.4 % | 685 | 0.58 -> 0.37 |
| Plage de la Govelle | B_to_A | 1.189 | 3 d | 3320 | 0.37 -> 0.30 | 17.1 % | 685 | 0.66 -> 0.56 |
| Plage de la Govelle | B_to_A | 1.189 | 5 d | 3320 | 0.41 -> 0.36 | 13.1 % | 685 | 0.75 -> 0.64 |
| Plage de Pontaillac | A_to_B | - | 1 d | 0 | - -> - | - | 0 | - -> - |
| Plage de Pontaillac | A_to_B | - | 3 d | 0 | - -> - | - | 0 | - -> - |
| Plage de Pontaillac | A_to_B | - | 5 d | 0 | - -> - | - | 0 | - -> - |
| Plage de Pontaillac | B_to_A | - | 1 d | 0 | - -> - | - | 0 | - -> - |
| Plage de Pontaillac | B_to_A | - | 3 d | 0 | - -> - | - | 0 | - -> - |
| Plage de Pontaillac | B_to_A | - | 5 d | 0 | - -> - | - | 0 | - -> - |
| Zone de Surf du Grand Crohot | A_to_B | 1.085 | 1 d | 2645 | 0.36 -> 0.28 | 20.4 % | 1385 | 0.47 -> 0.34 |
| Zone de Surf du Grand Crohot | A_to_B | 1.085 | 3 d | 2645 | 0.39 -> 0.35 | 10.8 % | 1385 | 0.51 -> 0.44 |
| Zone de Surf du Grand Crohot | A_to_B | 1.085 | 5 d | 2645 | 0.50 -> 0.46 | 7.7 % | 1385 | 0.66 -> 0.60 |
| Zone de Surf du Grand Crohot | B_to_A | 1.075 | 1 d | 2638 | 0.30 -> 0.23 | 22.0 % | 1217 | 0.40 -> 0.30 |
| Zone de Surf du Grand Crohot | B_to_A | 1.075 | 3 d | 2638 | 0.34 -> 0.32 | 6.1 % | 1217 | 0.44 -> 0.42 |
| Zone de Surf du Grand Crohot | B_to_A | 1.075 | 5 d | 2638 | 0.45 -> 0.44 | 2.4 % | 1217 | 0.61 -> 0.60 |
| Zone de surf du Truc Vert | A_to_B | 1.023 | 1 d | 2645 | 0.29 -> 0.28 | 2.7 % | 1385 | 0.37 -> 0.35 |
| Zone de surf du Truc Vert | A_to_B | 1.023 | 3 d | 2645 | 0.35 -> 0.35 | 0.2 % | 1385 | 0.44 -> 0.44 |
| Zone de surf du Truc Vert | A_to_B | 1.023 | 5 d | 2645 | 0.46 -> 0.45 | 0.5 % | 1385 | 0.59 -> 0.59 |
| Zone de surf du Truc Vert | B_to_A | 1.016 | 1 d | 2638 | 0.23 -> 0.23 | 2.2 % | 1217 | 0.31 -> 0.30 |
| Zone de surf du Truc Vert | B_to_A | 1.016 | 3 d | 2638 | 0.31 -> 0.31 | -1.4 % | 1217 | 0.41 -> 0.42 |
| Zone de surf du Truc Vert | B_to_A | 1.016 | 5 d | 2638 | 0.43 -> 0.43 | -0.9 % | 1217 | 0.59 -> 0.59 |
| La nord | A_to_B | 1.035 | 1 d | 2645 | 0.31 -> 0.30 | 3.9 % | 1385 | 0.40 -> 0.37 |
| La nord | A_to_B | 1.035 | 3 d | 2645 | 0.36 -> 0.36 | 0.7 % | 1385 | 0.47 -> 0.46 |
| La nord | A_to_B | 1.035 | 5 d | 2645 | 0.46 -> 0.45 | 1.2 % | 1385 | 0.59 -> 0.58 |
| La nord | B_to_A | 1.024 | 1 d | 2638 | 0.25 -> 0.24 | 4.0 % | 1217 | 0.33 -> 0.31 |
| La nord | B_to_A | 1.024 | 3 d | 2638 | 0.31 -> 0.31 | -1.1 % | 1217 | 0.41 -> 0.42 |
| La nord | B_to_A | 1.024 | 5 d | 2638 | 0.42 -> 0.43 | -0.6 % | 1217 | 0.58 -> 0.59 |
| Plage Sud (Point d'Or) | A_to_B | 1.005 | 1 d | 3896 | 0.27 -> 0.27 | -0.1 % | 1462 | 0.38 -> 0.38 |
| Plage Sud (Point d'Or) | A_to_B | 1.005 | 3 d | 3896 | 0.32 -> 0.32 | -0.3 % | 1462 | 0.46 -> 0.46 |
| Plage Sud (Point d'Or) | A_to_B | 1.005 | 5 d | 3896 | 0.42 -> 0.42 | -0.0 % | 1462 | 0.61 -> 0.61 |
| Plage Sud (Point d'Or) | B_to_A | 1.004 | 1 d | 3805 | 0.24 -> 0.24 | -0.5 % | 1172 | 0.35 -> 0.35 |
| Plage Sud (Point d'Or) | B_to_A | 1.004 | 3 d | 3805 | 0.32 -> 0.32 | -0.6 % | 1172 | 0.48 -> 0.48 |
| Plage Sud (Point d'Or) | B_to_A | 1.004 | 5 d | 3805 | 0.39 -> 0.39 | -0.4 % | 1172 | 0.62 -> 0.62 |
| Plage Centrale | A_to_B | 1.005 | 1 d | 3896 | 0.27 -> 0.27 | -0.1 % | 1462 | 0.38 -> 0.38 |
| Plage Centrale | A_to_B | 1.005 | 3 d | 3896 | 0.32 -> 0.32 | -0.3 % | 1462 | 0.46 -> 0.46 |
| Plage Centrale | A_to_B | 1.005 | 5 d | 3896 | 0.42 -> 0.42 | -0.0 % | 1462 | 0.61 -> 0.61 |
| Plage Centrale | B_to_A | 1.004 | 1 d | 3805 | 0.24 -> 0.24 | -0.5 % | 1172 | 0.35 -> 0.35 |
| Plage Centrale | B_to_A | 1.004 | 3 d | 3805 | 0.32 -> 0.32 | -0.6 % | 1172 | 0.48 -> 0.48 |
| Plage Centrale | B_to_A | 1.004 | 5 d | 3805 | 0.39 -> 0.39 | -0.4 % | 1172 | 0.62 -> 0.62 |
| Plage Ondres-Océan | A_to_B | 1.161 | 1 d | 3896 | 0.42 -> 0.27 | 34.9 % | 1462 | 0.63 -> 0.38 |
| Plage Ondres-Océan | A_to_B | 1.161 | 3 d | 3896 | 0.44 -> 0.31 | 28.0 % | 1462 | 0.65 -> 0.44 |
| Plage Ondres-Océan | A_to_B | 1.161 | 5 d | 3896 | 0.52 -> 0.41 | 20.4 % | 1462 | 0.78 -> 0.60 |
| Plage Ondres-Océan | B_to_A | 1.157 | 1 d | 3805 | 0.35 -> 0.24 | 33.0 % | 1172 | 0.56 -> 0.34 |
| Plage Ondres-Océan | B_to_A | 1.157 | 3 d | 3805 | 0.37 -> 0.30 | 19.9 % | 1172 | 0.56 -> 0.44 |
| Plage Ondres-Océan | B_to_A | 1.157 | 5 d | 3805 | 0.44 -> 0.37 | 14.7 % | 1172 | 0.69 -> 0.59 |
| Plage du Métro | A_to_B | 1.161 | 1 d | 3896 | 0.42 -> 0.27 | 34.9 % | 1462 | 0.63 -> 0.38 |
| Plage du Métro | A_to_B | 1.161 | 3 d | 3896 | 0.44 -> 0.31 | 28.0 % | 1462 | 0.65 -> 0.44 |
| Plage du Métro | A_to_B | 1.161 | 5 d | 3896 | 0.52 -> 0.41 | 20.4 % | 1462 | 0.78 -> 0.60 |
| Plage du Métro | B_to_A | 1.157 | 1 d | 3805 | 0.35 -> 0.24 | 33.0 % | 1172 | 0.56 -> 0.34 |
| Plage du Métro | B_to_A | 1.157 | 3 d | 3805 | 0.37 -> 0.30 | 19.9 % | 1172 | 0.56 -> 0.44 |
| Plage du Métro | B_to_A | 1.157 | 5 d | 3805 | 0.44 -> 0.37 | 14.7 % | 1172 | 0.69 -> 0.59 |
| Plage de Marinella | A_to_B | 1.082 | 1 d | 3896 | 0.33 -> 0.28 | 14.9 % | 1462 | 0.48 -> 0.39 |
| Plage de Marinella | A_to_B | 1.082 | 3 d | 3896 | 0.35 -> 0.32 | 9.9 % | 1462 | 0.52 -> 0.45 |
| Plage de Marinella | A_to_B | 1.082 | 5 d | 3896 | 0.45 -> 0.41 | 7.3 % | 1462 | 0.67 -> 0.61 |
| Plage de Marinella | B_to_A | 1.080 | 1 d | 3805 | 0.27 -> 0.24 | 12.2 % | 1172 | 0.41 -> 0.34 |
| Plage de Marinella | B_to_A | 1.080 | 3 d | 3805 | 0.31 -> 0.30 | 3.3 % | 1172 | 0.46 -> 0.44 |
| Plage de Marinella | B_to_A | 1.080 | 5 d | 3805 | 0.38 -> 0.37 | 2.9 % | 1172 | 0.60 -> 0.59 |
| Plage du Port Vieux | A_to_B | 1.237 | 1 d | 3896 | 0.54 -> 0.31 | 42.3 % | 1462 | 0.81 -> 0.44 |
| Plage du Port Vieux | A_to_B | 1.237 | 3 d | 3896 | 0.55 -> 0.34 | 37.5 % | 1462 | 0.82 -> 0.49 |
| Plage du Port Vieux | A_to_B | 1.237 | 5 d | 3896 | 0.61 -> 0.43 | 29.3 % | 1462 | 0.92 -> 0.63 |
| Plage du Port Vieux | B_to_A | 1.232 | 1 d | 3805 | 0.46 -> 0.27 | 41.3 % | 1172 | 0.75 -> 0.40 |
| Plage du Port Vieux | B_to_A | 1.232 | 3 d | 3805 | 0.46 -> 0.31 | 33.6 % | 1172 | 0.71 -> 0.44 |
| Plage du Port Vieux | B_to_A | 1.232 | 5 d | 3805 | 0.51 -> 0.38 | 26.3 % | 1172 | 0.81 -> 0.59 |
| Plage de la Marbella | A_to_B | 1.237 | 1 d | 3896 | 0.54 -> 0.31 | 42.3 % | 1462 | 0.81 -> 0.44 |
| Plage de la Marbella | A_to_B | 1.237 | 3 d | 3896 | 0.55 -> 0.34 | 37.5 % | 1462 | 0.82 -> 0.49 |
| Plage de la Marbella | A_to_B | 1.237 | 5 d | 3896 | 0.61 -> 0.43 | 29.3 % | 1462 | 0.92 -> 0.63 |
| Plage de la Marbella | B_to_A | 1.232 | 1 d | 3805 | 0.46 -> 0.27 | 41.3 % | 1172 | 0.75 -> 0.40 |
| Plage de la Marbella | B_to_A | 1.232 | 3 d | 3805 | 0.46 -> 0.31 | 33.6 % | 1172 | 0.71 -> 0.44 |
| Plage de la Marbella | B_to_A | 1.232 | 5 d | 3805 | 0.51 -> 0.38 | 26.3 % | 1172 | 0.81 -> 0.59 |
| Uhabia | A_to_B | 1.107 | 1 d | 3731 | 0.33 -> 0.26 | 21.7 % | 1201 | 0.53 -> 0.39 |
| Uhabia | A_to_B | 1.107 | 3 d | 3731 | 0.37 -> 0.31 | 14.6 % | 1201 | 0.57 -> 0.48 |
| Uhabia | A_to_B | 1.107 | 5 d | 3731 | 0.43 -> 0.38 | 10.1 % | 1201 | 0.67 -> 0.59 |
| Uhabia | B_to_A | 1.118 | 1 d | 3658 | 0.28 -> 0.24 | 12.3 % | 918 | 0.45 -> 0.38 |
| Uhabia | B_to_A | 1.118 | 3 d | 3658 | 0.33 -> 0.32 | 2.6 % | 918 | 0.50 -> 0.50 |
| Uhabia | B_to_A | 1.118 | 5 d | 3658 | 0.40 -> 0.40 | 0.3 % | 918 | 0.65 -> 0.66 |
| Harotzen Kosta | A_to_B | 1.107 | 1 d | 3731 | 0.33 -> 0.26 | 21.7 % | 1201 | 0.53 -> 0.39 |
| Harotzen Kosta | A_to_B | 1.107 | 3 d | 3731 | 0.37 -> 0.31 | 14.6 % | 1201 | 0.57 -> 0.48 |
| Harotzen Kosta | A_to_B | 1.107 | 5 d | 3731 | 0.43 -> 0.38 | 10.1 % | 1201 | 0.67 -> 0.59 |
| Harotzen Kosta | B_to_A | 1.118 | 1 d | 3658 | 0.28 -> 0.24 | 12.3 % | 918 | 0.45 -> 0.38 |
| Harotzen Kosta | B_to_A | 1.118 | 3 d | 3658 | 0.33 -> 0.32 | 2.6 % | 918 | 0.50 -> 0.50 |
| Harotzen Kosta | B_to_A | 1.118 | 5 d | 3658 | 0.40 -> 0.40 | 0.3 % | 918 | 0.65 -> 0.66 |
| Grande Plage | A_to_B | 1.107 | 1 d | 3731 | 0.33 -> 0.26 | 21.7 % | 1201 | 0.53 -> 0.39 |
| Grande Plage | A_to_B | 1.107 | 3 d | 3731 | 0.37 -> 0.31 | 14.6 % | 1201 | 0.57 -> 0.48 |
| Grande Plage | A_to_B | 1.107 | 5 d | 3731 | 0.43 -> 0.38 | 10.1 % | 1201 | 0.67 -> 0.59 |
| Grande Plage | B_to_A | 1.118 | 1 d | 3658 | 0.28 -> 0.24 | 12.3 % | 918 | 0.45 -> 0.38 |
| Grande Plage | B_to_A | 1.118 | 3 d | 3658 | 0.33 -> 0.32 | 2.6 % | 918 | 0.50 -> 0.50 |
| Grande Plage | B_to_A | 1.118 | 5 d | 3658 | 0.40 -> 0.40 | 0.3 % | 918 | 0.65 -> 0.66 |
| Ondarraitz | A_to_B | 0.972 | 1 d | 3731 | 0.27 -> 0.26 | 1.3 % | 1201 | 0.39 -> 0.39 |
| Ondarraitz | A_to_B | 0.972 | 3 d | 3731 | 0.32 -> 0.31 | 2.1 % | 1201 | 0.48 -> 0.48 |
| Ondarraitz | A_to_B | 0.972 | 5 d | 3731 | 0.39 -> 0.38 | 1.5 % | 1201 | 0.59 -> 0.58 |
| Ondarraitz | B_to_A | 0.980 | 1 d | 3658 | 0.25 -> 0.24 | 3.9 % | 918 | 0.39 -> 0.37 |
| Ondarraitz | B_to_A | 0.980 | 3 d | 3658 | 0.33 -> 0.32 | 3.5 % | 918 | 0.52 -> 0.50 |
| Ondarraitz | B_to_A | 0.980 | 5 d | 3658 | 0.41 -> 0.40 | 2.5 % | 918 | 0.68 -> 0.66 |

### Verdict per pair (the criterion of ADR 0022)

| Spot | Kept | Factor on the whole window | Why not |
| --- | --- | --- | --- |
| Mogueric | no | 0.973 | A_to_B, 1 d: gain 0.3 %, under 5 %; A_to_B, 1 d: the error on the hours of 2 m or more rises; A_to_B, 3 d: gain 1.7 %, under 5 %; A_to_B, 3 d: the error on the hours of 2 m or more rises; A_to_B, 5 d: gain -0.8 %, under 5 %; A_to_B, 5 d: the error on the hours of 2 m or more rises; B_to_A, 1 d: gain 1.9 %, under 5 %; B_to_A, 1 d: the error on the hours of 2 m or more rises; B_to_A, 3 d: gain 1.8 %, under 5 %; B_to_A, 5 d: gain 1.3 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| Plage du Moulin de la Rive | yes | 1.210 | - |
| Plage du Minou | yes | 1.267 | - |
| Plage de Pen Hat | yes | 1.267 | - |
| Plage de la Govelle | yes | 1.190 | - |
| Plage de Pontaillac | no | - | A_to_B: no factor (the training hours have no forecast height); B_to_A: no factor (the training hours have no forecast height) |
| Zone de Surf du Grand Crohot | no | 1.080 | B_to_A, 5 d: gain 2.4 %, under 5 % |
| Zone de surf du Truc Vert | no | 1.019 | A_to_B, 1 d: gain 2.7 %, under 5 %; A_to_B, 3 d: gain 0.2 %, under 5 %; A_to_B, 5 d: gain 0.5 %, under 5 %; B_to_A, 1 d: gain 2.2 %, under 5 %; B_to_A, 3 d: gain -1.4 %, under 5 %; B_to_A, 3 d: the error on the hours of 2 m or more rises; B_to_A, 5 d: gain -0.9 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| La nord | no | 1.029 | A_to_B, 1 d: gain 3.9 %, under 5 %; A_to_B, 3 d: gain 0.7 %, under 5 %; A_to_B, 5 d: gain 1.2 %, under 5 %; B_to_A, 1 d: gain 4.0 %, under 5 %; B_to_A, 3 d: gain -1.1 %, under 5 %; B_to_A, 3 d: the error on the hours of 2 m or more rises; B_to_A, 5 d: gain -0.6 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| Plage Sud (Point d'Or) | no | 1.004 | A_to_B, 1 d: gain -0.1 %, under 5 %; A_to_B, 3 d: gain -0.3 %, under 5 %; A_to_B, 3 d: the error on the hours of 2 m or more rises; A_to_B, 5 d: gain -0.0 %, under 5 %; B_to_A, 1 d: gain -0.5 %, under 5 %; B_to_A, 1 d: the error on the hours of 2 m or more rises; B_to_A, 3 d: gain -0.6 %, under 5 %; B_to_A, 3 d: the error on the hours of 2 m or more rises; B_to_A, 5 d: gain -0.4 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| Plage Centrale | no | 1.004 | A_to_B, 1 d: gain -0.1 %, under 5 %; A_to_B, 3 d: gain -0.3 %, under 5 %; A_to_B, 3 d: the error on the hours of 2 m or more rises; A_to_B, 5 d: gain -0.0 %, under 5 %; B_to_A, 1 d: gain -0.5 %, under 5 %; B_to_A, 1 d: the error on the hours of 2 m or more rises; B_to_A, 3 d: gain -0.6 %, under 5 %; B_to_A, 3 d: the error on the hours of 2 m or more rises; B_to_A, 5 d: gain -0.4 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| Plage Ondres-Océan | yes | 1.159 | - |
| Plage du Métro | yes | 1.159 | - |
| Plage de Marinella | no | 1.081 | B_to_A, 3 d: gain 3.3 %, under 5 %; B_to_A, 5 d: gain 2.9 %, under 5 % |
| Plage du Port Vieux | yes | 1.234 | - |
| Plage de la Marbella | yes | 1.234 | - |
| Uhabia | no | 1.113 | B_to_A, 3 d: gain 2.6 %, under 5 %; B_to_A, 5 d: gain 0.3 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| Harotzen Kosta | no | 1.113 | B_to_A, 3 d: gain 2.6 %, under 5 %; B_to_A, 5 d: gain 0.3 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| Grande Plage | no | 1.113 | B_to_A, 3 d: gain 2.6 %, under 5 %; B_to_A, 5 d: gain 0.3 %, under 5 %; B_to_A, 5 d: the error on the hours of 2 m or more rises |
| Ondarraitz | no | 0.976 | A_to_B, 1 d: gain 1.3 %, under 5 %; A_to_B, 1 d: the error on the hours of 2 m or more rises; A_to_B, 3 d: gain 2.1 %, under 5 %; A_to_B, 5 d: gain 1.5 %, under 5 %; B_to_A, 1 d: gain 3.9 %, under 5 %; B_to_A, 3 d: gain 3.5 %, under 5 %; B_to_A, 5 d: gain 2.5 %, under 5 % |

Kept: 8 of 20 pairs.
