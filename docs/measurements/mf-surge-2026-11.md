# Measure: Météo-France HYCOM2D storm surge vs SHOM REFMAR gauges (2026-11)

Measured with [`tools/measure_mf_surge.py`](../../tools/measure_mf_surge.py) (raw stats: `mf-surge-2026-11-results.json.gz`), for [ADR 0025](../decisions/0025-hycom2d-surge-measured-and-ml-archive.md).

**Setup.** 14 days of live OVH public S3 HYCOM2D GRIB2 runs (6-hourly cycles `00/06/12/18Z`, `HYCOM2D-WARP`/`WARO` in Atlantic-Channel, `HYCOM2D-MARP`/`MARO` in Mediterranean) scored against SHOM REFMAR 61-minute centered-mean sea level across 12 gauges (11 Atlantic/Channel gauges of ADR 0017 + Marseille).
- **Calibration window (chart datum + mean surge offset)**: `2026-11-01 00Z` to `2026-11-08 00Z`.
- **Held-out test window**: `2026-11-08 00Z` to `2026-11-15 12Z` (`<= 2026-11-14 18Z` origins; REFMAR lag = 20 min for ADR 0017 and hybrid).

## Atlantic & Channel (11 gauges, mean across stations)

| Lead | n (total) | EOT20 RMS | ADR0017 RMS | HYCOM ARPEGE (deb) RMS | HYCOM AROME (deb) RMS | Hybrid RMS | HYCOM ARPEGE (raw) RMS | corr r(ARPEGE, obs) | corr r(AROME, obs) | corr r(ADR0017, obs) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +1 h | 308 | 28.6 | 13.2 | 13.6 | 13.7 | 12.5 | 20.2 | 0.59 | 0.58 | 0.47 |
| +3 h | 308 | 30.0 | 16.2 | 13.7 | 13.8 | 13.2 | 20.6 | 0.70 | 0.71 | 0.61 |
| +6 h | 308 | 28.2 | 17.1 | 15.5 | 15.6 | 13.8 | 20.2 | 0.47 | 0.47 | 0.38 |
| +12 h | 308 | 28.8 | 21.7 | 15.8 | 15.7 | 14.6 | 20.3 | 0.48 | 0.48 | 0.31 |
| +24 h | 297 | 30.1 | 27.5 | 15.9 | 15.8 | 15.4 | 20.4 | 0.52 | 0.52 | 0.08 |
| +36 h | 275 | 30.7 | 29.7 | 15.9 | 15.9 | 15.8 | 20.7 | 0.52 | 0.52 | -0.12 |

## Highest-surge hours (top 20% |non-tidal anomaly|, Atlantic & Channel RMS cm)

| Lead | EOT20 | ADR0017 | HYCOM ARPEGE (deb) | HYCOM AROME (deb) | Hybrid |
| --- | ---: | ---: | ---: | ---: | ---: |
| +1 h | 42.8 | 18.7 | 19.4 | 19.5 | 16.2 |
| +3 h | 44.7 | 23.4 | 18.9 | 19.2 | 16.6 |
| +6 h | 43.1 | 27.9 | 21.3 | 21.2 | 18.7 |
| +12 h | 44.2 | 35.0 | 23.1 | 22.8 | 21.3 |
| +24 h | 45.9 | 42.7 | 22.5 | 22.2 | 21.8 |
| +36 h | 47.3 | 46.2 | 21.6 | 21.6 | 21.3 |

## Mediterranean — Marseille (REFMAR 524, HYCOM2D-MARP/MARO, RMS cm)

| Lead | n | EOT20 | ADR0017 | HYCOM MARP (deb) | HYCOM MARO (deb) | Hybrid | r(MARP) | r(ADR0017) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +1 h | 28 | 12.8 | 3.8 | 3.8 | 3.8 | 3.4 | 0.92 | 0.90 |
| +3 h | 28 | 12.6 | 4.8 | 3.4 | 3.2 | 3.1 | 0.95 | 0.89 |
| +6 h | 28 | 12.8 | 6.9 | 3.8 | 3.7 | 3.7 | 0.93 | 0.79 |
| +12 h | 28 | 13.1 | 9.6 | 3.8 | 4.0 | 4.0 | 0.93 | 0.66 |
| +24 h | 27 | 13.6 | 12.3 | 3.7 | 4.1 | 3.7 | 0.91 | 0.39 |
| +36 h | 25 | 14.1 | 13.6 | 3.7 | 4.2 | 3.7 | 0.89 | 0.22 |

## Per-station breakdown at +24 h lead (RMS cm, r(ARPEGE, obs))

| Station | Basin | Train ARPEGE bias (cm) | EOT20 | ADR0017 | HYCOM ARPEGE (deb) | HYCOM AROME (deb) | Hybrid | r(ARPEGE) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Calais | ATL | -13.3 | 49.8 | 46.7 | 30.9 | 30.2 | 30.3 | 0.49 |
| Le Havre | ATL | -24.2 | 22.1 | 20.6 | 26.3 | 26.9 | 25.9 | 0.59 |
| Cherbourg | ATL | -13.7 | 29.5 | 27.0 | 13.2 | 13.2 | 13.2 | 0.47 |
| Saint-Malo | ATL | -3.8 | 52.7 | 49.9 | 41.4 | 41.3 | 39.9 | 0.21 |
| Roscoff | ATL | -11.6 | 28.3 | 25.5 | 12.7 | 12.7 | 12.3 | 0.32 |
| Brest | ATL | -9.7 | 29.0 | 26.0 | 10.2 | 10.1 | 9.8 | 0.55 |
| Concarneau | ATL | -9.0 | 26.4 | 23.5 | 7.3 | 7.7 | 6.9 | 0.66 |
| Les Sables-d'Olonne | ATL | -8.4 | 24.3 | 21.7 | 7.6 | 7.7 | 7.2 | 0.64 |
| La Rochelle-La Pallice | ATL | -10.0 | 20.8 | 18.5 | 8.2 | 8.6 | 8.2 | 0.57 |
| Saint-Jean-de-Luz Socoa | ATL | -3.3 | 23.1 | 20.7 | 10.0 | 9.8 | 9.5 | 0.60 |
| Lorient | ATL | -9.5 | 25.3 | 22.4 | 6.8 | 5.7 | 6.4 | 0.65 |
| Marseille | MED | -4.9 | 13.6 | 12.3 | 3.7 | 4.1 | 3.7 | 0.91 |

## Attribution

- REFMAR 1-minute tide gauge observations: SHOM (Licence Ouverte Etalab 2.0).
- HYCOM2D storm surge fields (`HYCOM2D-WARP`, `HYCOM2D-WARO`, `HYCOM2D-MARP`, `HYCOM2D-MARO`): Météo-France / SHOM HOMONIM (Licence Ouverte Etalab 2.0, `meteofrance-pnt` OVH S3).
- Astronomical tide baseline: EOT20 (Hart-Davis et al. 2021, CC BY 4.0) with ADR 0019 Brest calibration.
