# Measure: the tide corrected with the nearest REFMAR tide gauge (2026-11)

Measured on 2026-11-08 by [`tools/measure_tide_correction.py`](../../tools/measure_tide_correction.py) (results:
`tide-correction-2026-11-results.json.gz`), for [ADR 0017](../decisions/0017-tide-corrected-by-the-nearest-gauge.md).

**What is measured.** A forecast made at a time *t* from the measures up to *t* minus 20 minutes (REFMAR posted its latest value 3 to 20 minutes late
when read on 2026-11-08), compared with what the gauge measured at *t* + 1 h, + 3 h and + 6 h. Model alone against model corrected: the model is what
`GET /v1/tides` serves (EOT20, the cell nearest to the gauge) and the correction is the code of the API itself. The reference is the 61-minute mean of
the raw REFMAR measures, as in [ADR 0015](../decisions/0015-tides-from-a-global-model.md) (the weather is kept, the waves are not). The window is
the one of ADR 0015, 2026-09-27 to 2026-10-27, 11 gauges: the ten ports of ADR 0015's criterion and Lorient (REFMAR 6349, the gauge nearest to Lorient
in REFMAR's list of 2026-11-08).

**What is fitted, and on what.** The datum offset of each gauge, the window of the mean and the decay were chosen on the **first half** (up to
2026-10-12). Every figure of the result is the **second half**, which the choice never saw: 14 days of forecasts every 30 minutes (648 origins), and every
significant high and low water of the reference (about 52 per gauge) forecast from 1, 3 and 6 hours before it, with and without the correction, matched with the
extreme of the same kind nearest in time (within 3 hours).

**Chosen:** window 745 minutes (one cycle of M2, 12.42 h), decay 12 hours, radius 50 km.

## Reading

- **Heights of the series: better at every gauge and every lead.** Mean of the 11 gauges 22.1 → 18.8 cm at + 1 h (-15 %), 22.1 → 19.8 at + 3 h (-10 %),
  22.2 → 20.7 at + 6 h (-7 %). Brest 17.4 → 14.9 / 16.1 / 17.1 cm; Lorient 14.8 → 11.9 / 13.1 / 14.3 cm. The correction is closer to the sea than the model
  in 60 to 62 % of the forecasts.
- **Heights of the high and low waters: better on average, not at the two ports checked by the median.** Mean absolute error of the 11 gauges 16.8 → 13.3 / 14.2 / 15.0 cm
  at + 1 / 3 / 6 h (median 13.8 → 11.4 / 12.1 / 13.1). But **Brest (median) 9.0 → 10.0 / 9.3 / 9.6 cm** and **Lorient (median) 5.9 → 5.7 / 7.4 / 7.7 cm are worse** (by the mean they are better:
  Brest 13.5 → 11.4 / 12.4 / 13.2, Lorient 10.4 → 8.0 / 9.0 / 10.0).
- **Times of the high and low waters: no gain.** Median of the 11 gauges 10.6 min without the correction, 10.8 to 10.9 with it. Brest 7.7 → 7.2 / 7.2 / 7.4 min, Lorient 6.6 → 6.6 / 6.5 / 6.4. The correction lifts or lowers
  the curve; it does not move it in time.
- **Brest is measured against the model as the API serves it**, calibrated by [ADR 0019](../decisions/0019-tide-calibration-per-port.md) (amplitude 1.021, +5 minutes): the model alone is then 17.4 cm
  instead of 18.3, and the correction adds less to the heights of the extremes. Lorient is not calibrated.
- **The gain shrinks with the lead, and the decay of 12 hours is the best of those tried** (table of the tuning): a gap measured now is worth most in the next hours.
- **The weakest places are where the model is weakest** (Le Havre 47 cm, Saint-Malo 37 cm, Calais 33 cm of error before the correction): it removes 3 to 6 cm of them, not the rest. The error that is left is the tide's own
  (phase and amplitude of the constituents the model does not carry in these shallow seas), which a gap measured over one tidal cycle averages out by construction.

**Verdict: not served by default.** The heights of the curve are better everywhere; the high and low waters, which the screen prints, are not better at the two ports named for the check
(the median of their height error rises by 0.3 to 1.8 cm at Brest and Lorient for a lead of 3 hours or more, and at Brest at 1 hour too) and their times do not improve. ADR 0017 makes it opt-in
(`correct=true`, or `VIGIE_TIDE_CORRECTION=on` for an instance).

## Limits of this measure

- 14 days of one season (autumn 2026), with the weather of those weeks: not a statement about storm surges, which are rarer and larger and are what the correction is for.
  The tide gauge measures them; the figures above are those of ordinary weather.
- The datum offset of each gauge is the mean of 30 days (its first 15 for the held-out figures): a few centimetres of uncertainty of its own. It is not a chart datum of SHOM.
- Brest and Lorient are among the better gauges; a point between two gauges, or in an estuary, can have a gap that differs from the gauge's.
- Seen live on 2026-11-08 (the API code against the real service, outside the tests): the gap was +0.1 cm at Brest and +1.7 cm at Lorient, a calm day; a point at Vannes (45 km) used the gauge of Lorient. The correction was applied at both gauges, and the answer said so.
- The latency of 20 minutes is the worst seen on two gauges on one morning.

### Tuning, first half (2026-09-28 to 2026-10-12)

RMS error of the series, mean of the 11 gauges, for each window of the mean and each decay.

| Window (min) | Decay (h) | RMS +1 h (cm) | RMS +3 h (cm) | RMS +6 h (cm) | Mean of the three |
| --- | --- | --- | --- | --- | --- |
| 60 | 1 | 18.3 | 19.2 | 19.1 | 18.9 |
| 60 | 3 | 19.3 | 20.0 | 18.8 | 19.3 |
| 60 | 6 | 20.4 | 21.7 | 18.9 | 20.3 |
| 60 | 12 | 21.3 | 23.5 | 20.0 | 21.6 |
| 60 | 24 | 21.8 | 24.9 | 21.4 | 22.7 |
| 60 | 48 | 22.1 | 25.6 | 22.4 | 23.4 |
| 180 | 1 | 19.1 | 19.1 | 19.1 | 19.1 |
| 180 | 3 | 20.4 | 18.9 | 19.1 | 19.5 |
| 180 | 6 | 21.4 | 19.6 | 19.4 | 20.1 |
| 180 | 12 | 22.1 | 20.6 | 20.5 | 21.1 |
| 180 | 24 | 22.6 | 21.3 | 21.7 | 21.9 |
| 180 | 48 | 22.8 | 21.8 | 22.5 | 22.4 |
| 745 | 1 | 18.1 | 19.0 | 19.1 | 18.8 |
| 745 | 3 | 17.2 | 18.0 | 18.7 | 18.0 |
| 745 | 6 | 17.0 | 17.5 | 18.1 | 17.5 |
| 745 | 12 | 17.0 | 17.4 | 17.7 | 17.3 |
| 745 | 24 | 17.0 | 17.4 | 17.6 | 17.3 |
| 745 | 48 | 17.1 | 17.4 | 17.6 | 17.4 |
| 1490 | 1 | 18.3 | 19.0 | 19.1 | 18.8 |
| 1490 | 3 | 17.6 | 18.2 | 18.8 | 18.2 |
| 1490 | 6 | 17.5 | 17.9 | 18.3 | 17.9 |
| 1490 | 12 | 17.6 | 17.8 | 18.0 | 17.8 |
| 1490 | 24 | 17.6 | 17.9 | 18.1 | 17.8 |
| 1490 | 48 | 17.6 | 17.9 | 18.1 | 17.9 |

### Transfer from one gauge to another (the evidence of the 50 km radius)

The gap measured at the first gauge is applied at the second, forecast at +3 h, second half. Only the pairs under 150 km.

| Gap measured at | Applied at | Distance (km) | RMS +3 h before (cm) | after (cm) |
| --- | --- | --- | --- | --- |
| Concarneau | Lorient | 44 | 14.8 | 12.5 |
| Lorient | Concarneau | 44 | 13.9 | 13.4 |
| Roscoff | Brest | 54 | 17.4 | 16.4 |
| Brest | Roscoff | 54 | 19.3 | 18.2 |
| Les Sables-d'Olonne | La Rochelle-La Pallice | 58 | 18.0 | 15.6 |
| La Rochelle-La Pallice | Les Sables-d'Olonne | 58 | 14.0 | 12.8 |
| Brest | Concarneau | 71 | 13.9 | 13.8 |
| Concarneau | Brest | 71 | 17.4 | 15.6 |
| Roscoff | Concarneau | 94 | 13.9 | 14.2 |
| Concarneau | Roscoff | 94 | 19.3 | 18.0 |
| Brest | Lorient | 111 | 14.8 | 13.3 |
| Lorient | Brest | 111 | 17.4 | 16.0 |
| Cherbourg | Saint-Malo | 116 | 36.8 | 34.6 |
| Saint-Malo | Cherbourg | 116 | 20.5 | 17.8 |
| Roscoff | Lorient | 118 | 14.8 | 13.7 |
| Lorient | Roscoff | 118 | 19.3 | 18.2 |
| Le Havre | Cherbourg | 127 | 20.5 | 23.0 |
| Cherbourg | Le Havre | 127 | 47.6 | 42.6 |
| Saint-Malo | Lorient | 141 | 14.8 | 18.6 |
| Lorient | Saint-Malo | 141 | 36.8 | 34.6 |
| Saint-Malo | Roscoff | 143 | 19.3 | 21.4 |
| Roscoff | Saint-Malo | 143 | 36.8 | 34.2 |

## Result, second half (2026-10-12 12:00 to 2026-10-26)

### Heights: RMS error of the series (cm), forecast at t, compared with the measure at t + lead

| Station | +1 h before | +1 h after | +3 h before | +3 h after | +6 h before | +6 h after |
| --- | --- | --- | --- | --- | --- | --- |
| Calais | 33.2 | 27.1 | 33.3 | 28.5 | 34.0 | 30.2 |
| Le Havre | 47.5 | 44.0 | 47.6 | 44.5 | 47.7 | 44.9 |
| Cherbourg | 20.5 | 15.3 | 20.5 | 16.4 | 20.6 | 17.4 |
| Saint-Malo | 36.8 | 33.9 | 36.8 | 34.8 | 36.9 | 35.4 |
| Roscoff | 19.3 | 17.4 | 19.3 | 18.2 | 19.3 | 18.8 |
| Brest | 17.4 | 14.9 | 17.4 | 16.1 | 17.4 | 17.1 |
| Concarneau | 13.9 | 11.4 | 13.9 | 12.6 | 13.9 | 13.6 |
| Les Sables-d'Olonne | 14.0 | 11.0 | 14.0 | 11.9 | 14.0 | 12.9 |
| La Rochelle-La Pallice | 18.0 | 15.4 | 18.0 | 16.1 | 18.0 | 16.8 |
| Saint-Jean-de-Luz Socoa | 7.3 | 5.1 | 7.3 | 5.5 | 7.3 | 5.8 |
| Lorient | 14.8 | 11.9 | 14.8 | 13.1 | 14.8 | 14.3 |
| **mean of the stations** | 22.1 | 18.8 | 22.1 | 19.8 | 22.2 | 20.7 |

### High and low waters: median absolute error of the time (min) and of the height (cm), forecast at extreme - lead

| Station | Extremes | Lead | Time before | Time after | Height before | Height after | Not found before / after |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Calais | 53 of 53 | +1 h | 14.9 | 14.8 | 21.2 | 14.1 | 0 / 0 |
| Calais | 53 of 53 | +3 h | 14.9 | 14.5 | 21.2 | 16.2 | 0 / 0 |
| Calais | 53 of 53 | +6 h | 14.9 | 15.4 | 21.2 | 19.1 | 0 / 0 |
| Le Havre | 52 of 53 | +1 h | 23.7 | 23.9 | 25.9 | 20.2 | 1 / 1 |
| Le Havre | 52 of 53 | +3 h | 23.7 | 23.8 | 25.9 | 21.4 | 1 / 1 |
| Le Havre | 52 of 53 | +6 h | 23.7 | 23.9 | 25.9 | 24.3 | 1 / 1 |
| Cherbourg | 52 of 52 | +1 h | 8.0 | 10.3 | 14.0 | 10.4 | 0 / 0 |
| Cherbourg | 52 of 52 | +3 h | 8.0 | 10.2 | 14.0 | 10.4 | 0 / 0 |
| Cherbourg | 52 of 52 | +6 h | 8.0 | 10.0 | 14.0 | 10.8 | 0 / 0 |
| Saint-Malo | 52 of 52 | +1 h | 11.7 | 12.5 | 27.5 | 22.0 | 0 / 0 |
| Saint-Malo | 52 of 52 | +3 h | 11.7 | 12.5 | 27.5 | 22.5 | 0 / 0 |
| Saint-Malo | 52 of 52 | +6 h | 11.7 | 12.4 | 27.5 | 24.1 | 0 / 0 |
| Roscoff | 53 of 53 | +1 h | 7.9 | 8.0 | 12.0 | 13.1 | 0 / 0 |
| Roscoff | 53 of 53 | +3 h | 7.9 | 8.3 | 12.0 | 14.8 | 0 / 0 |
| Roscoff | 53 of 53 | +6 h | 7.9 | 8.5 | 12.0 | 16.7 | 0 / 0 |
| Brest | 52 of 52 | +1 h | 7.7 | 7.2 | 9.0 | 10.0 | 0 / 0 |
| Brest | 52 of 52 | +3 h | 7.7 | 7.2 | 9.0 | 9.3 | 0 / 0 |
| Brest | 52 of 52 | +6 h | 7.7 | 7.4 | 9.0 | 9.6 | 0 / 0 |
| Concarneau | 52 of 52 | +1 h | 7.3 | 6.8 | 9.1 | 5.0 | 0 / 0 |
| Concarneau | 52 of 52 | +3 h | 7.3 | 6.7 | 9.1 | 5.6 | 0 / 0 |
| Concarneau | 52 of 52 | +6 h | 7.3 | 7.0 | 9.1 | 6.7 | 0 / 0 |
| Les Sables-d'Olonne | 52 of 52 | +1 h | 9.1 | 8.4 | 7.9 | 6.7 | 0 / 0 |
| Les Sables-d'Olonne | 52 of 52 | +3 h | 9.1 | 8.7 | 7.9 | 7.1 | 0 / 0 |
| Les Sables-d'Olonne | 52 of 52 | +6 h | 9.1 | 8.8 | 7.9 | 7.9 | 0 / 0 |
| La Rochelle-La Pallice | 52 of 52 | +1 h | 16.0 | 15.6 | 15.3 | 13.6 | 0 / 0 |
| La Rochelle-La Pallice | 52 of 52 | +3 h | 16.0 | 15.6 | 15.3 | 13.2 | 0 / 0 |
| La Rochelle-La Pallice | 52 of 52 | +6 h | 16.0 | 15.6 | 15.3 | 12.1 | 0 / 0 |
| Saint-Jean-de-Luz Socoa | 52 of 52 | +1 h | 4.0 | 4.4 | 4.0 | 4.6 | 0 / 0 |
| Saint-Jean-de-Luz Socoa | 52 of 52 | +3 h | 4.0 | 4.3 | 4.0 | 4.8 | 0 / 0 |
| Saint-Jean-de-Luz Socoa | 52 of 52 | +6 h | 4.0 | 4.2 | 4.0 | 4.7 | 0 / 0 |
| Lorient | 52 of 52 | +1 h | 6.6 | 6.6 | 5.9 | 5.7 | 0 / 0 |
| Lorient | 52 of 52 | +3 h | 6.6 | 6.5 | 5.9 | 7.4 | 0 / 0 |
| Lorient | 52 of 52 | +6 h | 6.6 | 6.4 | 5.9 | 7.7 | 0 / 0 |
