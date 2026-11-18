# 0029. Météo-France's wave model of France (MFWAM 0.025 degrees) measured against buoys, and not used for the surf spots

- Status: accepted. The model is **not adopted**: it was measured less close to the buoys than the source Vigie serves today, offshore and nearshore. Nothing the API serves changes. Measure: [`docs/measurements/mfwam-france-2026-11.md`](../measurements/mfwam-france-2026-11.md), reproduced by [`tools/measure_mfwam_france.py`](../../tools/measure_mfwam_france.py).
- Date: 2026-11-18
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

The owner decided on 2026-11-18 to plug the Météo-France APIs the project subscribes to, a lot at a time. This is the lot of the wave model. The surf pages (`/v1/surf/...`, `/v1/activities/overview`) serve today the hourly waves, the primary and secondary swell and the wind waves of **MFWAM global** through Open-Meteo Marine (`models=meteofrance_wave`, `app/collectors/surf.py`), 7 days, at the coordinates of each of the 171 spots. [ADR 0022](0022-forecast-swell-measured-against-buoys.md) measured that forecast against the CANDHIS buoys.

What Météo-France offers on the portal (read on 2026-11-18, with the project's key):

- `DPPaquetWAVESMODELS`, model `MFWAM`, grid `0.025` ("coupled with AROME"), package `SP1`: one GRIB2 file per run and per hour of lead, steps 1 to 51 hours, runs at 00, 06, 12 and 18 UTC, the 00:00 run inserted at 04:25 UTC. About 5 MB a file (10 MB at the 48-hour step), compressed with CCSDS (decoded with ecCodes, which the API already depends on). 801 x 601 points from 53 N to 38 N and 8 W to 12 E, 18 parameters (height, period and direction of the waves, of the wind waves, of the total, primary and secondary swell, the peak period, the 10 m wind). A grid `0.1` coupled with ARPEGE exists (not looked at).
- `WavesModels`: the same model as a WCS (`MF-NWP-HIGHRES-MFWAM-0025-FRANCE-WCS`), and WW3 and HYCOM2D models.
- Only about **three days of runs** stay published (13 runs listed on 2026-11-18). A measure over a longer time needs the runs to be kept as they come.
- A run covers **51 hours**; the spots serve 7 days. The model could replace the first two days at most, and the later days would stay Open-Meteo's: a seam between two models in one series.

## Decision

1. **Protocol and criterion**, written down here before the nearshore figures were computed (the offshore figures had been computed once to see what the data was; the threshold is the one of ADR 0022):
   - **Offshore**: the Météo-France moored buoys inside the grid (nine of them, eight with a wave height on these days; `DPObs/v2/bouees`, the records on the hour of the last five days, `haut_vag`, `per_moy_vag`, `dir_vag`) against the hour of MFWAM 0.025 at the nearest water cell (the runs of 2026-11-15 06Z to 2026-11-18 00Z, steps 6, 24 and 48 hours) and against what Vigie serves (Open-Meteo: past values for 6 hours, `wave_height_previous_day1` and `_day2` for 24 and 48 hours).
   - **Nearshore**: one snapshot of the CANDHIS real-time buoys inside the grid (the latest hourly record of each, the only ones the API gives for the days of the runs: the delayed-time records of November do not exist yet), against the newest run interpolated to the minute of the record and against Open-Meteo at the same time.
   - **Criterion**: the model replaces the current source for the spots if its root mean square error of the height is at least 5 % lower, with at least 30 paired hours at the lead (the 21 buoys of the snapshot), **and** does not rise over the hours the buoys read 2 m or more.
2. **Result.** Offshore, at every lead, MFWAM 0.025 reads high: bias +0.26 to +0.35 m against +0.02 to -0.03 m for the current source, root mean square error 0.40 to 0.53 m against 0.24 to 0.26 m; its mean period is 1.1 to 1.3 s too long (0.7 to 0.8 s for the current source), its direction as close or farther. Over the hours of 2 m and more it does not do better. Nearshore, on the one storm morning of the snapshot, both read high (+0.62 and +0.48 m), MFWAM 0.025 more (root mean square error 0.83 m against 0.71 m; 1.02 against 0.79 over the 11 buoys of 2 m and more). The criterion is met by neither measure.
3. **The surf is unchanged.** The 171 spots, `/v1/surf/...`, `/v1/activities/overview`, the archive `surf_forecast_archive`, the correction of ADR 0023 (off), Home Assistant's and the embedded frame's fields keep their source, their credit and their values. No collector is added to the API, nothing to the gateway, nothing is deployed.
4. **To reopen it**, there must be a nearshore series that this snapshot is not: the runs kept as they come (a file at the steps 6, 24 and 48 of each run is 12 files and 12 requests a day of the 50 a minute of the subscription), the values at the 171 spots and the buoys stored, and compared with the CANDHIS delayed-time records of the same weeks when they are published, and with Open-Meteo's `previous_dayN` of the same hours. Whether to run that archive is the owner's call; this decision does not start it.

## Not measured, so not claimed

- The swell partitions (`swell_wave_*`, `secondary_swell_wave_*`): no buoy of the two networks gives them. The spots' primary and secondary swell are served as before.
- The 171 spots themselves: the buoys are where they float. At the beach, where a 0.025 degree grid may well beat a 0.083 degree one, these measures neither show it nor refute it.
- More than three days and one weather episode (a storm in the Bay of Biscay on 2026-11-17, 3 to 6 m at the Gascogne buoy, and a Mediterranean sea of 1 to 3 m); no calm sea in the nearshore snapshot, which is one run at one hour.
- Leads over 48 hours, the `0.1` grid, the WW3 models, and any de-biasing of MFWAM 0.025 (a constant would be a different model, fitted on three days).
- How Open-Meteo builds its past values and its `previous_dayN` for this model (ADR 0022 says the same): the current source's low bias at 6 hours may be partly an analysis.
- That the buoys are the truth: `haut_vag` does not say whether it is a Hm0 or an H1/3 (see `app/collectors/meteofrance_buoys.py`), and CANDHIS gives H1/3 on most of its buoys.
