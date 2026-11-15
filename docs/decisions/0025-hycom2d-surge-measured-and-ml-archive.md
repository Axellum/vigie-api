# 0025. HYCOM2D storm surge measured against REFMAR before integration, and full MFWAM archive schema for ML

- Status: accepted (measure first; runtime API responses and `SOURCES.md` unchanged in this branch).
- Date: 2026-11-15
- Numbering note: `0024` is intentionally left free for the concurrent coastal-shelter branch.

## Context

`GET /v1/tides` computes astronomical tides from EOT20 (with [ADR 0019](0019-tide-calibration-per-port.md) calibration at Brest) and extends short-term water level anomalies using persistence from live SHOM REFMAR tide gauges ([ADR 0017](0017-tide-corrected-by-the-nearest-gauge.md), 12-hour exponential decay). Over horizons beyond ~6 hours, persistence decays toward zero while atmospheric pressure and wind stress keep driving non-tidal surges of several decimetres.

Météo-France and SHOM publish hourly barotropic storm surge (`surcote`) 2D GRIB2 fields from operational `HYCOM2D` configurations on public OVH object storage under Licence Ouverte Etalab 2.0 (`meteofrance-pnt`):

- `HYCOM2D-WARP` (forced by ARPEGE) and `HYCOM2D-WARO` (forced by AROME) over Northeast Atlantic, English Channel and North Sea (`1/24` degree, ~4.6 km).
- `HYCOM2D-MARP` (forced by ARPEGE) and `HYCOM2D-MARO` (forced by AROME) over the Western Mediterranean (`1/24` degree).

In parallel, our silent forecast archive (`surf_forecast_archive`) stores MFWAM swell predictions at matched hours to measure error ([ADR 0022](0022-forecast-swell-measured-against-buoys.md)), and an offline residual ML experiment needs the full sea-state partition (`secondary_swell_*`, `wind_wave_height`) plus a placeholder for `surge_m` without making any extra upstream API calls.

Because two concurrent branches are actively modifying shared runtime entrypoints (`app/main.py`, `app/attribution.py`, `app/commercial.py`, `app/collectors/budget.py`, `app/collectors/surf.py`, `app/services/surf.py`, `app/models/surf.py`, `app/data/surf-spots.json`), this step delivers the standalone GRIB2 decoder, held-out surge benchmark, and idempotent SQLite archive schema extension while leaving all runtime endpoints untouched.

## Decision

1. **HYCOM2D GRIB2 decoder (`app/prealert/hycom2d.py`)**:
   - Add `grib_url`, `SurgeGrid`, `SurgeSample`, and `decode_surge` reusing `_DECODE_LOCK` and `split_messages` from `app/prealert/gfs.py`.
   - Select only instantaneous storm surge fields (`parameterCategory == 3`, `parameterNumber == 192`, `stepType == "instant"`), ignoring 3-hourly / 24-hourly interval summaries (`parameterNumber == 193`).
   - Use sea-aware sampling (`SurgeGrid.sample`): bilinear interpolation across the four surrounding grid nodes when all four are valid wet sea cells, falling back to the nearest valid wet sea cell within 15 km when a coastal tide gauge sits adjacent to masked land (`missingValue = 9999`).
2. **Held-out benchmark before runtime wiring (`tools/measure_mf_surge.py`)**:
   - Measure 14 full days present on public OVH S3 (`2026-11-01 00Z` to `2026-11-15 12Z`, 6-hourly `00/06/12/18Z` origins) against SHOM REFMAR 61-minute centred-mean water level across 12 gauges (the 11 Atlantic/Channel gauges of ADR 0017 plus Marseille `524` in the Mediterranean).
   - Strictly split into calibration (`2026-11-01 00Z` to `2026-11-08 00Z`, estimating chart datum + mean surge bias per station) and held-out test (`2026-11-08 00Z` to `2026-11-15 12Z`, origins `<= 2026-11-14 18Z`, REFMAR 20-minute observation lag).
   - Compare five predictors: `EOT20` baseline, `ADR 0017` gauge persistence, `HYCOM2D` forced by ARPEGE (`WARP`/`MARP`) debiased, `HYCOM2D` forced by AROME (`WARO`/`MARO`) debiased, and `Hybrid` (`EOT20 + HYCOM2D + decayed initial error(t0 - 20m)`) with ADR 0017's 12 h decay. Raw full report: [`docs/measurements/mf-surge-2026-11.md`](../measurements/mf-surge-2026-11.md).
3. **Extended ML archive schema (`app/services/forecast_archive.py`, `app/services/storage_wal.py`)**:
   - Extend `COLUMNS["swell"]` from 6 to 10 fields using safe positional access (`_value_at`), adding `secondary_swell_height`, `secondary_swell_period`, `secondary_swell_direction`, and `wind_wave_height`.
   - Add those 4 nullable `REAL` columns plus `surge_m REAL` to `surf_forecast_archive`, backed by an idempotent `PRAGMA table_info` / `ALTER TABLE` migration helper (`_ensure_surf_archive_columns`). No extra upstream HTTP requests are made; `app/collectors/surf.py` is unchanged.
4. **Defer runtime `GET /v1/tides` wiring to a follow-up PR**:
   - Once concurrent branches land and a multi-month REFMAR calibration confirms per-station acceptance criteria (analogous to ADR 0019), a follow-up change will wire the hybrid predictor, attribution, and `SOURCES.md` entry cleanly.

## Measured result (2026-11-01 to 2026-11-15, held-out second week)

### Atlantic & Channel (11 gauges, mean across stations, RMS in cm)

| Lead | n (total) | EOT20 | ADR 0017 | HYCOM ARPEGE (deb) | HYCOM AROME (deb) | Hybrid | HYCOM ARPEGE (raw) | r(ARPEGE) | r(AROME) | r(ADR 0017) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +1 h | 308 | 28.6 | 13.2 | 13.6 | 13.7 | 12.5 | 20.2 | 0.59 | 0.58 | 0.47 |
| +3 h | 308 | 30.0 | 16.2 | 13.7 | 13.8 | 13.2 | 20.6 | 0.70 | 0.71 | 0.61 |
| +6 h | 308 | 28.2 | 17.1 | 15.5 | 15.6 | 13.8 | 20.2 | 0.47 | 0.47 | 0.38 |
| +12 h | 308 | 28.8 | 21.7 | 15.8 | 15.7 | 14.6 | 20.3 | 0.48 | 0.48 | 0.31 |
| +24 h | 297 | 30.1 | 27.5 | 15.9 | 15.8 | 15.4 | 20.4 | 0.52 | 0.52 | 0.08 |
| +36 h | 275 | 30.7 | 29.7 | 15.9 | 15.9 | 15.8 | 20.7 | 0.52 | 0.52 | -0.12 |

### Highest-surge hours (top 20% |non-tidal anomaly|, Atlantic & Channel RMS cm)

| Lead | EOT20 | ADR 0017 | HYCOM ARPEGE (deb) | HYCOM AROME (deb) | Hybrid |
| --- | ---: | ---: | ---: | ---: | ---: |
| +1 h | 42.8 | 18.7 | 19.4 | 19.5 | 16.2 |
| +3 h | 44.7 | 23.4 | 18.9 | 19.2 | 16.6 |
| +6 h | 43.1 | 27.9 | 21.3 | 21.2 | 18.7 |
| +12 h | 44.2 | 35.0 | 23.1 | 22.8 | 21.3 |
| +24 h | 45.9 | 42.7 | 22.5 | 22.2 | 21.8 |
| +36 h | 47.3 | 46.2 | 21.6 | 21.6 | 21.3 |

### Mediterranean — Marseille (`REFMAR 524`, `HYCOM2D-MARP`/`MARO`, RMS cm)

| Lead | n | EOT20 | ADR 0017 | HYCOM MARP (deb) | HYCOM MARO (deb) | Hybrid | r(MARP) | r(ADR 0017) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +1 h | 28 | 12.8 | 3.8 | 3.8 | 3.8 | 3.4 | 0.92 | 0.90 |
| +3 h | 28 | 12.6 | 4.8 | 3.4 | 3.2 | 3.1 | 0.95 | 0.89 |
| +6 h | 28 | 12.8 | 6.9 | 3.8 | 3.7 | 3.7 | 0.93 | 0.79 |
| +12 h | 28 | 13.1 | 9.6 | 3.8 | 4.0 | 4.0 | 0.93 | 0.66 |
| +24 h | 27 | 13.6 | 12.3 | 3.7 | 4.1 | 3.7 | 0.91 | 0.39 |
| +36 h | 25 | 14.1 | 13.6 | 3.7 | 4.2 | 3.7 | 0.89 | 0.22 |

### Reading and caveats

1. **Hybrid wins at every measured lead**: `Hybrid` (`HYCOM2D + decayed gauge residual`) drops RMS error across Atlantic/Channel from `28.6–30.7 cm` (EOT20) and `13.2–29.7 cm` (ADR 0017 persistence) down to `12.5–15.8 cm`. In the top 20% highest-surge hours at `+36 h`, Hybrid (`21.3 cm`) cuts RMS by more than half vs ADR 0017 (`46.2 cm`). At Marseille, HYCOM2D correlation stays at `r = 0.89–0.95` and RMS at `3.1–4.0 cm` across `+1 h` to `+36 h` while ADR 0017 decays from `3.8` to `13.6 cm`.
2. **ARPEGE vs AROME forcing**: Over Atlantic/Channel, `HYCOM2D-WARP` (ARPEGE) and `HYCOM2D-WARO` (AROME) score virtually identically (`15.9` vs `15.8 cm` RMS at `+24 h`, `r = 0.52` for both). ARPEGE-forced runs reach `+60..114 h` compared to AROME's `+36..42 h`, making `WARP`/`MARP` sufficient for a unified multi-day horizon.
3. **Debiasing is necessary, and two station caveats forbid blind rollout**:
   - Without station datum/model mean calibration, Atlantic/Channel `HYCOM ARPEGE (raw)` averages `20.2–20.7 cm` RMS vs `13.6–15.9 cm` debiased.
   - **Le Havre** (`train ARPEGE bias = -24.2 cm`): applying the 7-day calibration offset worsened held-out week-2 RMS from `22.1 cm` (EOT20) and `20.6 cm` (ADR 0017) to `26.3 cm` (`HYCOM ARPEGE deb`), even though correlation was moderate (`r = 0.59`). Any production debiasing needs a multi-month baseline and an explicit held-out improvement gate.
   - **Saint-Malo** (`r = 0.21`, `41.4 cm` RMS vs `52.7 cm` EOT20) retains large estuarine / shallow-water tidal error outside barotropic storm surge. By contrast, Brittany and Bay of Biscay gauges (`Concarneau` `7.3 cm`, `Les Sables-d'Olonne` `7.6 cm`, `Lorient` `6.8 cm`, `La Rochelle-La Pallice` `8.2 cm`, `Brest` `10.2 cm`, `Saint-Jean-de-Luz Socoa` `10.0 cm` at `+24 h`) gain strongly.

## Consequences

- `vigie_api` gains a tested `HYCOM2D` GRIB2 decoder, a repeatable public S3 vs REFMAR benchmark tool, verified empirical baselines in `docs/measurements/mf-surge-2026-11.md`, and ML-ready archive columns in `surf_forecast_archive` — all without touching shared endpoints, collectors, or quotas during parallel branch work.
- Future offline training and runtime storm-surge integration start from verified held-out metrics rather than unmeasured assumptions.
