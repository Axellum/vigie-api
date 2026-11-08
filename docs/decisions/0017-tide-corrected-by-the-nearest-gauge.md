# 0017. The modelled tide corrected with the nearest tide gauge

- Status: accepted **as an option, off by default**: the measure found it lowers the error of the heights of the curve, but not that of the high and low waters at the two ports checked, nor their times. Measure: [`docs/measurements/tide-correction-2026-11.md`](../measurements/tide-correction-2026-11.md).
- Date: 2026-11-08
- Number: reserved with `scripts/prochain_adr.py` (0016 was taken by an open pull request) and in the state file of the sessions.

## Context

[ADR 0015](0015-tides-from-a-global-model.md) computes the tide from EOT20, a tide without weather, and keeps the coefficient an estimate of ours (SHOM sells its
predictions; there is no licence to reuse them). The measure of ADR 0015 found the model within a quarter of an hour on the open coasts of France, with a
root mean square of 7 to 48 cm at the French ports (2026-10, ordinary weather). The owner wants the tides of the weather screen to be closer to the sea. The measures of
REFMAR (SHOM, **Licence Ouverte 2.0**) are open, one a minute, and `tools/measure_tides.py` already reads them.

## Decision

1. **Correct the model with the gap that the nearest REFMAR tide gauge measured**, in one rule: the gap is the mean, over the last 745 minutes (one cycle of M2), of
   *observed - modelled - datum offset of the gauge*; the corrected height is the modelled one plus the gap times `exp(-t / 12 h)`, `t` counted from the last measure.
   Heights before the last measure, and the estimated coefficient, are the model's. The window and the decay were chosen on the first half of 30 days and scored on the second;
   a short window (an hour) followed the error of the tide itself and made the forecast worse from + 3 h.
2. **A gauge is used within 50 km of the point**, among 11 (the ten ports of ADR 0015's criterion and Lorient, REFMAR 6349). The gap of one Atlantic gauge helped
   another one 44 to 111 km away, and hurt between Le Havre and Cherbourg (127 km): 50 km is inside what was seen to help.
3. **Never silent, and not the default.** `GET /v1/tides` always carries a `correction` object: `applied`, or `reason` (`no_station_near`, `measure_unavailable`, `no_recent_measure`,
   `implausible_gap`, `not_asked`), the gauge and its distance, the time of the last measure, the gap, the decay, the window, the credit. Without a gauge
   near, without a measure from the last 60 minutes (or fewer than half the minutes of the window), or when REFMAR cannot be read, the answer is the model's, as before.
   It is applied only for `correct=true`, or on an instance that sets `VIGIE_TIDE_CORRECTION=on` (then `correct=false` asks for the model alone); REFMAR is not asked otherwise.
   A gap over 1.5 m is a gauge fault, not weather: not applied (a guard of ours, not measured).
4. **REFMAR is asked sparingly and credited.** One request per gauge every 15 minutes whatever the number of clients (`app/collectors/refmar.py`, through the cache,
   the circuit breaker and the counted `_get`), the last 14 hours only, an identifying User-Agent. The credit "SHOM, REFMAR (Licence Ouverte 2.0)" is in `correction.attribution`
   and in `SOURCES.md`; the correction is Vigie's and says so. Not verified: the exact mention REFMAR asks for.
5. **The datum offset of each gauge** (its chart datum against the model's mean sea level) is a bundled constant (`app/data/refmar-stations.json`), the mean of 30 days
   of measure minus model, built by `tools/measure_tide_correction.py`.
6. The coefficient stays "estimated" and keeps its name; `GET /v1/tides/coast` is the model alone (it would ask a gauge for every point of a map).

## Result (measured on 2026-11-08, second half of the window, forecast made at *t* and compared with *t* + 1 h, + 3 h, + 6 h)

Against the model as the API serves it (at Brest, calibrated by [ADR 0019](0019-tide-calibration-per-port.md)):

- **Heights of the series**: better at the 11 gauges and the 3 leads. Mean 22.1 → 18.8 / 19.8 / 20.7 cm. Brest 17.4 → 14.9 / 16.1 / 17.1 cm, Lorient 14.8 → 11.9 / 13.1 / 14.3 cm.
- **Heights of the high and low waters**: mean absolute error of the 11 gauges 16.8 → 13.3 / 14.2 / 15.0 cm, but by the median **Brest 9.0 → 10.0 / 9.3 / 9.6 cm and Lorient 5.9 → 5.7 / 7.4 / 7.7 cm are worse**.
- **Times of the high and low waters: no gain** (median of the gauges 10.6 → 10.8 / 10.8 / 10.9 min; Brest 7.7 → 7.2 / 7.2 / 7.4; Lorient 6.6 → 6.6 / 6.5 / 6.4).

The sibling criterion of ADR 0019 (the median errors of the extremes must not rise) is not met at Brest and Lorient, so the correction is **not the default**; it is there for whoever wants the curve closer to the sea
(a surge, a day of weather), and the choice of the default is the owner's.

## Consequences

- `GET /v1/tides` has a new field, `correction`, and a parameter, `correct`. The embedded frame and the Home Assistant integration do not read `/v1/tides`.
- One more collector in `/v1/health` (`shom_refmar`) and one more source in `SOURCES.md` and `app/commercial.py` (allowed, with the source named).
- **Not measured, so not claimed**: the behaviour in a storm surge (the weeks measured were ordinary), the points far from the 11 gauges (the French coast has more gauges
  that could be added with their offset), and the effect of a gauge that is in an estuary or a harbour for a point in the open.
- Further, if the times are to be better: a harmonic fit of each gauge's own constants (a different method, not this one).
