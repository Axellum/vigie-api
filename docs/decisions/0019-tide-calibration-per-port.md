# 0019. EOT20 calibrated port by port against REFMAR, applied only where a held-out period says it helps

- Status: **criterion written before any figure was seen** (first commit of its pull request). The result is added at the end.
- Date: 2026-11-08

## Context

[ADR 0015](0015-tides-from-a-global-model.md) serves a tide computed from EOT20 and measured it against REFMAR over one month: the high and
low waters are within minutes and a few centimetres at most French ports, but a model cell of 1/8 degree has a systematic error at a given
port (a range a little too large or small, a tide a few minutes early or late). That error repeats every day, so months of measurements
can estimate it. The REFMAR observations (SHOM, Licence Ouverte 2.0) are open; the SHOM's predictions and coefficients are not, and are
not used.

## Decision

1. **A correction of two numbers per station**, found offline by `tools/calibrate_tides.py`: `corrected(t) = amplitude * model(t - delay)`
   (heights relative to the model's mean sea level, as before). `delay > 0` means the real tide comes later than the model's. Nothing else is
   fitted (no constituent, no offset).
2. **Data**: the raw minute observations of REFMAR (`sources=1`), smoothed by the centred 61-minute mean of ADR 0015, from 2026-03-01 to
   2026-10-27, fetched in requests of 16 days (the service allows 31), one station after another, kept in a cache folder so that a second run asks
   nothing. The stations are the one at Brest and the nearest REFMAR station to Lorient in the list the service itself gives
   (`/maregraphie/service/tidegauges`); the file lists any other that the tool is run for.
3. **Fit**: by least squares on the 10-minute series (a free offset removes the datum difference), delay searched from -45 to +45 minutes by 1
   minute, on the **fit period** 2026-03-01 to 2026-09-26.
4. **Held-out checks, two kinds, both before a correction is applied**:
   - **Cross-validation**: the fit period is cut in 4 contiguous blocks; each block is predicted with a correction fitted on the other 3.
   - **Final test**: 2026-09-27 to 2026-10-27 (the window of ADR 0015), never used for the stored fit.
   For each, the same four figures are computed with and without the correction: RMS of the 10-minute series, median absolute time error and
   median absolute height error of the significant high and low waters (matched as in ADR 0015), and the number of unmatched ones.
5. **A correction is applied only when all of these hold**: on the cross-validated blocks pooled **and** on the final test, the RMS falls by
   at least **10 %**; the median time error and the median height error do not rise; the RMS falls in at least 3 of the 4 blocks; the
   amplitude is within 0.85 to 1.15 and the delay within 30 minutes (outside, something is wrong with the data, not the model). Otherwise the
   station is kept in the file with its figures and `applied: false`, and the API serves the plain model.
6. **Where it applies**: a point whose nearest calibrated station is within **15 km** and that station is applied. The radius is a setting of
   ours, not a finding: the correction was measured at the station only. The coastal map (`/v1/tides/coast`) and the estimated coefficient are
   not changed.
7. **The answer says it**: `calibration` is `null` when none is applied, otherwise it names the station, the two numbers, the period and the
   source, and `derived` lists it. The credit of the answer adds REFMAR when a calibration is applied.
8. If the gain cannot be measured at a station, it is not applied, and if it is measured at none, **nothing is delivered as a default**.
