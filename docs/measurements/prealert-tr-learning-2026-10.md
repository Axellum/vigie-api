# The unofficial pre-alert for Turkey on the learning set (2024-2025): the criteria are not met

*Measured on 2026-10-25 with `tools/backtest_prealert_tr.py`, for [ADR 0011](../decisions/0011-unofficial-pre-alert-turkey-shadow-pilot.md).
Only the learning set was used (the press episodes of 2024 and 2025, [`prealert-tr-episodes.csv`](prealert-tr-episodes.csv)); the test set
of 2026 was **not** scored. No threshold table was frozen and the shadow phase was not started.*

## What was measured

- **Models**: NOAA GFS 0.25 (every day of 2024-2025, 747 runs of 00 UTC) and ECMWF IFS 0.25 open data (one day in two from
  2024-02-29, the first day of its 0.25 degree archive, 375 runs; two runs not read after S3 answered "503 Slow Down"). The ECMWF
  archive of 2024 holds no gust and no precipitation type: ECMWF has no wind and no snow before November 2024.
- **Pairs**: 122 (episode, province) of the learning set; 106 for ECMWF alone (the episodes before 2024-02-29 cannot be replayed).
- **Thresholds per province**: levels 1, 2, 3 at the percentiles (100 - 3k, 100 - k, 100 - k/5) of the province's own days for rain,
  snow and wind (k = 100 - p2), never under floors of ours (rain 10 / 20 / 50 mm, snow 2 / 5 / 15 mm of water, gust 40 / 55 / 80 km/h);
  one rare national value for heat and cold (no episode to learn from).
- **Cross-validation**: thresholds learned on the days 1 to 15 of the months, judged on the days 16 to the end, and the other way round
  (a split by year left the ECMWF thresholds without a winter). An episode belongs to the half of its first day.
- **The two numbers that fight**: criterion 1 of the ADR (a level 2 or more for at least **70 %** of the pairs at J0) and criterion 5
  (at most **3 %** of the province-days at level 2 or more, and **0.3 %** at level 3, all phenomena).

## Results

| Model | p2 | J0 pairs | J1 pairs | Province-days at level 2+ | at level 3 |
| --- | --- | --- | --- | --- | --- |
| GFS, one national threshold (rain, the first reading) | | 65 % at 3 % of the province-days, 73 % at 5 % | | | |
| GFS per province | 97 | 74 % | 78 % | 6.2 % | 0.99 % |
| GFS per province | 98 | 66 % | 71 % | 5.1 % | 0.88 % |
| GFS per province | 99 | 55 % | 59 % | 3.6 % | 0.74 % |
| ECMWF per province | 98 | 74 % | 81 % | 4.6 % | 0.77 % |
| ECMWF per province | 98.5 | 71 % | 79 % | 4.0 % | 0.76 % |
| ECMWF per province | 99 | 66 % | 76 % | 3.4 % | 0.74 % |
| GFS and ECMWF must agree | 97 | 71 % | 76 % | 3.9 % | 0.50 % |
| GFS and ECMWF must agree | 98 | 61 % | 69 % | 3.1 % | 0.44 % |

Counting only rain, snow and wind in the over-alerting (heat and cold would stay hidden: no episode judges them), the closest are
ECMWF at 98.5 (71 % and 79 %, 3.5 %, 0.49 %) and the two models agreeing at 97 (71 % and 76 %, 3.4 %, 0.32 %). A percentile per
phenomenon (rain 97 to 98, snow 99 to 99.5, wind 99.5 to 99.8) did not do better: 71 % at J0 with 3.45 % at best.

**No setting meets criteria 1 and 5 together on the learning set.** ECMWF is clearly better than GFS (about +5 points of detection
at the same over-alerting), and requiring both models to agree lowers the over-alerting more than the detection, but the best
results stay about half a point above the 3 % and more than twice the 0.3 % of level 3.

## Limits of the measure

- The over-alerting is counted against no warning at all: a day without a press article is not a day without an official warning,
  so some of the "over-alerting" may be warnings the press did not report (the list of episodes is not the MGM's archive).
- 122 pairs, mostly rain on the coasts; wind 10 pairs, no heat, no cold, no red.
- Days are local days from the run of 00 UTC; J0 misses the 3 hours before the run (21 to 24 local time of the evening before).

## What could still be tried (not done)

- Thresholds learned from a longer climatology (ERA5 reanalysis, 30 years) rather than two years of forecasts.
- The ECMWF ensemble (probabilities) rather than the single run.
- Criterion 5 counted on the phenomena that would be shown only, which is a change of the ADR: the owner's decision.
