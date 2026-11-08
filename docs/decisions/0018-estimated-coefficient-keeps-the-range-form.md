# 0018. The estimated coefficient keeps its range form, and says how it is calculated

- Status: accepted. A measure, a decision not to change the formula, and one added field.
- Date: 2026-11-08

## Context

[ADR 0015](0015-tides-from-a-global-model.md) gives an **estimated** coefficient: 100 x (high water at Brest - mean of the two low waters around it) / 6.10 m, on the
model's tide. SHOM's tide prediction data sheet on Refmar (May 2024, `refmar.shom.fr`, read on 2026-11-08) defines the coefficient as
C = (H - N0) / U x 100, H the high water above the chart datum, N0 the mid-tide level, U the unit of height (after Laplace: the mean
amplitude of the largest tide that follows the syzygy of an equinox by about a day and a half), says it is worked out **at Brest only**
and applies to the whole coast of metropolitan France, with a time shift for the port, and gives 20 (extreme neap) to 120 (extreme spring)
as the span. **It gives no value of U or N0**: 6.10 m for the range (3.05 m for the amplitude) is still the figure of secondary sources.

Two differences with ours: SHOM measures the high water from a fixed mid-level, we measure the range between the neighbouring waters;
and SHOM's comes from the harmonic prediction (no weather), ours from the model.

## Measure

`tools/measure_coefficient.py`, 2026-04-26 to 2026-10-28, 354 high waters of Brest matched (within 3 h) between the model's cell and the
observed tide (REFMAR station 3, Licence Ouverte 2.0). Difference model - observed, in coefficient points, each form on both sides:

| Form | mean | mean abs | max abs | rms |
|---|---|---|---|---|
| range / 6.10 (current) | -2.01 | 2.62 | 10.3 | 3.20 |
| SHOM form, N0 = mean of the series | -1.60 | 3.36 | 23.0 | 4.25 |
| SHOM form, N0 = mean of the 24 h 50 min around | -1.36 | 2.43 | 14.9 | 3.08 |

## Decision

1. **The formula does not change.** The best SHOM-form variant gains 0.2 points on the mean error and loses 4.6 on the maximum; the
   others are worse. The observed side carries the weather (surge, a few tenths of a metre, 10 points or more), which the range form
   cancels and the fixed-level form does not: the observed tide is not a fair reference for SHOM's form, and no SHOM prediction (paid) was used.
   A change that does not measurably help is not made.
2. The error that remains is the model's: its amplitude at Brest is about 3 % low (median ratio observed/model 1.029, mean -2 points). A
   correction belongs to the tide itself (nearest gauge, calibration), not to the coefficient's formula.
3. **New field `coefficient_method`** on `GET /v1/tides`: `formula`, `unit_m`, `place` and `tide` (`model`: the bundled model, uncorrected;
   a corrected tide would say so). The name stays "estimated" everywhere.

## Consequences

- No SHOM data (predictions, constants) is copied; only a published definition was read.
- If SHOM states U and N0 for Brest, or a surge-free reference is available, the measure is run again with `tools/measure_coefficient.py`.
