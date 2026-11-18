# Measure: Météo-France MFWAM 0.025 degrees (France) vs buoys, next to Open-Meteo's MFWAM (2026-11)

Measured on 2026-11-18 with [`tools/measure_mfwam_france.py`](../../tools/measure_mfwam_france.py), for [ADR 0029](../decisions/0029-mfwam-france-measured-against-buoys.md). Statistics only; the records of the buoys are not published here.
Sources: Météo-France (`DPPaquetWAVESMODELS`, `DPObs/v2/bouees`), Cerema (CANDHIS, real time), Open-Meteo Marine (`models=meteofrance_wave`).

**Setup.** Bias is forecast minus buoy (metres). "Open-Meteo" is what Vigie serves for the surf today. Leads: MFWAM 0.025 steps 6, 24 and 48 hours of the runs of 2026-11-15 06Z to 2026-11-18 00Z (the run of 06Z on the 18th was not published yet): 12 runs at +6 h, 9 at +24 h and 5 at +48 h had a buoy hour inside the five days the API keeps. Open-Meteo past values for 6 hours, `wave_height_previous_day1` and `_day2` for 24 and 48 hours. Eight buoys with a wave height: Azur, Lion, Ajaccio, Vecchio, Calvi, Provence, PACA, Gascogne (Sardaigne gave no wave height on these days; Antilles is outside the grid).

## Offshore (Météo-France buoys, hourly)

| lead | source | hours | bias | MAE | RMSE | r |
|---|---|---:|---:|---:|---:|---:|
| +6 h | MFWAM 0.025 | 89 | +0.26 | 0.33 | 0.40 | 0.94 |
| +6 h | Open-Meteo | 89 | +0.02 | 0.19 | 0.24 | 0.96 |
| +24 h | MFWAM 0.025 | 65 | +0.29 | 0.32 | 0.46 | 0.93 |
| +24 h | Open-Meteo | 65 | +0.01 | 0.18 | 0.24 | 0.96 |
| +48 h | MFWAM 0.025 | 35 | +0.35 | 0.41 | 0.53 | 0.92 |
| +48 h | Open-Meteo | 35 | -0.03 | 0.21 | 0.26 | 0.96 |

Hours the buoy read 2 m or more:

| lead | source | hours | bias | MAE | RMSE | r |
|---|---|---:|---:|---:|---:|---:|
| +6 h | MFWAM 0.025 | 41 | +0.28 | 0.37 | 0.45 | 0.91 |
| +6 h | Open-Meteo | 41 | -0.05 | 0.22 | 0.28 | 0.96 |
| +24 h | MFWAM 0.025 | 33 | +0.35 | 0.38 | 0.53 | 0.91 |
| +24 h | Open-Meteo | 33 | -0.02 | 0.20 | 0.26 | 0.97 |
| +48 h | MFWAM 0.025 | 24 | +0.35 | 0.42 | 0.56 | 0.91 |
| +48 h | Open-Meteo | 24 | -0.09 | 0.22 | 0.29 | 0.97 |

Mean period (bias, seconds) and direction (mean absolute error, degrees), same hours:

| lead | MFWAM 0.025 period | Open-Meteo period | MFWAM 0.025 direction | Open-Meteo direction |
|---|---:|---:|---:|---:|
| +6 h | +1.08 | +0.76 | 15.8 | 12.2 |
| +24 h | +1.09 | +0.67 | 7.5 | 7.1 |
| +48 h | +1.29 | +0.75 | 7.8 | 6.7 |

## Nearshore (CANDHIS real-time buoys, one record each, run 2026-11-18 00Z)

21 buoys inside the grid with a height (Cherbourg to Bonifacio; Anglet had none). The cell of the model was at the buoy for 19 of 21 (2.8 km for Monaco and Royan). Only one buoy read 5 m or more (Les Pierres Noires, 5.5 m).

| source | buoys | bias | MAE | RMSE | r |
|---|---:|---:|---:|---:|---:|
| MFWAM 0.025 | 21 | +0.62 | 0.63 | 0.83 | 0.94 |
| Open-Meteo | 21 | +0.48 | 0.53 | 0.71 | 0.95 |
| MFWAM 0.025, buoy >= 2 m | 11 | +0.83 | 0.83 | 1.02 | 0.84 |
| Open-Meteo, buoy >= 2 m | 11 | +0.64 | 0.67 | 0.79 | 0.93 |

**Reading.** On these days MFWAM 0.025 has the right shape (r 0.91 to 0.94 offshore) with a height read about 0.3 m too high offshore and a period about 1 s too long; the source served today is closer on every figure. This is three days and one storm: it shows that the case for switching is not made, not that the 0.025 degree model is worse in a small shallow bay. The raw hours are not published; `python tools/measure_mfwam_france.py --cache-dir DIR` rebuilds the tables from the runs published at the time it is run.
