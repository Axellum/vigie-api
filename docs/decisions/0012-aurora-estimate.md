# 0012. The aurora estimate: a chance of our own, measured on past nights before it is shown, and SWPC's data as SWPC gives it

- Status: accepted (the measure of 2026-10-28 meets the criterion on all four points; see "Measured" below)
- Date: 2026-10-28

## Context

A visitor of the site who reads "Kp index 1.3 · G0" learns nothing about the question they have: will I see an aurora tonight from
here? The API already reads the planetary Kp of NOAA's Space Weather Prediction Center (SWPC). SWPC publishes more (its own index,
<https://services.swpc.noaa.gov/json/> and `/products/`, read on 2026-10-28):

- the **OVATION** model, "a 30 to 90 minute forecast of the location and intensity of the aurora" on a one-degree grid
  (`json/ovation_aurora_latest.json`). SWPC: "An estimate of aurora viewing probability can be derived by assuming a linear
  relationship to the intensity of the aurora", and "The aurora does not need to be directly overhead but can be observed from as
  much as a 1000 km away when the aurora is bright and if conditions are right"
  (<https://www.swpc.noaa.gov/products/aurora-30-minute-forecast>);
- the **Kp index by 3-hour interval**, observed, estimated, then predicted for three days (`products/noaa-planetary-k-index-forecast.json`);
- the **solar wind** measured at L1 over the last hour (`products/geospace/propagated-solar-wind-1-hour.json`);
- its **alerts, watches and warnings**, as text messages (`products/alerts.json`).

No SWPC product answers "tonight, from here". OVATION looks 30 to 90 minutes ahead; the predicted Kp is a number for the whole planet.
SWPC's scale of geomagnetic storms says where aurora "has been seen as low as" for each level ("typically 55° geomagnetic lat." at
Kp 6, 50° at Kp 7, 45° at Kp 8, 40° at Kp 9, <https://www.swpc.noaa.gov/noaa-scales-explanation>), which is the extreme, not a chance.
SWPC keeps no public archive of the OVATION grids (none was found on 2026-10-28), so OVATION cannot be measured on past nights.

A past night can be measured, though. The citizen-science project Aurorasaurus published its database: "Aurorasaurus Web Observations
(2014-2025)" (MacDonald et al. 2026, <https://doi.org/10.5281/zenodo.16783265>, CC BY-NC 4.0), 22,280 reports, each a place, a time
span and whether its author saw an aurora (16,368) or looked and did not (5,912). GFZ Potsdam publishes the definitive Kp of every
3-hour interval (Matzka et al. 2021, <https://doi.org/10.5880/Kp.0001>, CC BY 4.0).

## Decision

### What the API serves (`GET /v1/sky/aurora`, `GET /v1/sky/aurora.geojson`)

1. **SWPC's data as SWPC gives it**, each with its time and its credit: the OVATION grid drawn as bands of its own values for the map
   (the levels are ours; the values are SWPC's), the Kp intervals with their `observed` / `estimated` / `predicted` mark and the G
   level SWPC wrote, the latest solar wind row, and the geomagnetic messages of the last 72 hours with their text untouched.
2. **For a place, an estimate of ours**, hour by hour over the coming night, named `estimate` in the answer and listed in `derived`,
   with the factors it is made of and the best window. It is **not** SWPC's, it is not an official forecast, and the answer says so.
   For the next hour the answer also gives what OVATION says around the place (the strongest value within 1,000 km and how far it is),
   as SWPC's data, outside the estimate.

### The estimate (`app/services/aurora_method.py`)

The chance at a place and an hour is the product of four factors between 0 and 1:

| Factor | Rule | Where it comes from |
| --- | --- | --- |
| Activity | 1 under the aurora; falling linearly to 0 at 1,000 km on the equator side of its edge. The edge is a magnetic latitude, `EDGE_AT_KP0 - EDGE_PER_KP x Kp`, with the Kp of that 3-hour interval (observed, else estimated, else predicted). | The 1,000 km is SWPC's sentence; the straight line is ours; the two settings of the edge are **fitted** on the reports of 2014 to 2022 only (best mean log-likelihood on a grid of 55 to 75 degrees and 0.5 to 5 degrees a Kp unit). The magnetic latitude is the modified apex latitude of IGRF-14 (below). |
| Darkness | 1 with the Sun 18 degrees or more below the horizon (the dark night); 0 above 6 degrees below; linear between. | Ours: the dark night is the usual astronomical definition; the ramp says that a bright aurora is seen in nautical twilight. |
| Moon | 1 with the Moon down; above the horizon, `1 - 0.5 x` its illuminated fraction. | Ours, not fitted. The measure reports the estimate with and without it. |
| Clouds | `1 - cloud cover / 100`, the cloud cover of that hour from the weather forecast the instance already reads (Open-Meteo, MET Norway when it fails). | Ours. Not measured (a report does not say how cloudy it was); unknown clouds leave the factor out and the answer says so. |

**The magnetic latitude.** A centred dipole was tried first and dropped before any score: against the `mlat` column of the
Aurorasaurus reports (which follows the real field), it puts the United Kingdom 5.7 degrees too far north, France 7.0 and Spain 10.3 (the
United States and Canada 0.2 too far south), so a rule fitted on reports that come mostly from North America would have been far too
hopeful in Europe. The API uses the **modified apex latitude** (Richmond 1995) of the full IGRF-14 field (degree 13): the field line
through the place at 110 km is followed to its top, offline, on a grid of 2 degrees (`tools/build_magnetic_latitudes.py`,
`app/data/apex-latitudes.json.gz`, epoch 2026.5), interpolated between the points. The same grid is used by the measure, whatever the
year of a report (the field moves by a fraction of a degree over the years of the test). Checked before the fit: on 300 reports drawn
at random, the traced latitude minus the `mlat` of the report has a median of -0.18 degree (10th percentile -0.23, 90th +0.03).

**The fitted settings** (`tools/measure_aurora.py fit`, 6,443 reports of 2014 to 2022, 2026-10-28): `EDGE_AT_KP0 = 62.5` degrees and
`EDGE_PER_KP = 1.9` degrees a Kp unit (mean log-likelihood -0.502). The edge is then at 62.5 degrees at Kp 0, 53.0 at Kp 5 and 45.4 at
Kp 9; with the 1,000 km reach (9.0 degrees), an aurora can be counted down to 36.4 degrees of magnetic latitude at Kp 9.

The levels the answers name are ours: `likely` from 50 %, `possible` from 20 %, `unlikely` from 5 %, `very_unlikely` below (named `no`
until the measure: see "Measured"). The best window is the
run of hours whose chance is at least 80 % of the best hour's, around the best hour.

### The criterion (fixed on 2026-10-28, before the test set was scored)

The measure is `tools/measure_aurora.py` on the reports from **2023-01-01** (the test set; the storms of 10-11 May and 10-11 October
2024 are in it), with the Kp GFZ published afterwards (a perfect Kp forecast) and without the cloud factor. The estimate is served
only if **all four** hold:

1. **Ranking**: the area under the ROC curve of the chance against seen / not seen is at least **0.75** on the test set.
2. **Calibration**: over the bins 0-5 %, 5-20 %, 20-50 % and 50-100 % (bins of at least 30 reports), the share of reports that saw an
   aurora **grows** from bin to bin.
3. **The big nights**: on the nights of 10-11 May 2024 and 10-11 October 2024 (UTC noon to noon), of the reports that saw an aurora
   with the Sun at -12 degrees or lower, at least **70 %** get a chance of 20 % or more, in the whole set and in Europe alone.
4. **Better than the sourced line**: its mean log-likelihood on the test set beats the simplest rule drawn from SWPC's own words (the
   G-scale latitudes, extended by 5 degrees a Kp unit, reached 5 degrees poleward of the line, with the same darkness and Moon).

If one fails, the route serves SWPC's data and no estimate (`estimate` is null with the reason), and this ADR is amended with what
changed and a new measure on the same split.

## Consequences

- Four new SWPC collectors (`noaa_swpc_ovation`, `noaa_swpc_kp_forecast`, `noaa_swpc_solar_wind`, `noaa_swpc_alerts`), each asked with
  `If-Modified-Since`; their requests do not grow with the number of clients (one copy each for the whole instance).
- The clouds cost nothing more: the hourly cloud cover of the next 48 hours rides in the daily forecast request the instance already
  sends for a place (one more variable, the weight of the request stays 1.0, see `docs/data-sources.md`).
- `astronomy-engine` (MIT) becomes a dependency of the API (Sun and Moon here, the eclipses next).
- The estimate is measured with a perfect Kp; what SWPC's 3-day forecast adds as error is not measured. The cloud factor is not
  measured. Both are said in the answer's description and in the measure.
- The reports of Aurorasaurus are under CC BY-NC 4.0: they are used for this measure only, never copied into the repository or served;
  the figures computed from them are. Whether a paid app may rely on a method measured on them is an open point for the owner.

## Measured (2026-10-28)

[`docs/measurements/aurora-2026-10.md`](../measurements/aurora-2026-10.md): on the 15,573 reports of 2023 to 2025, area under the curve
**0.785** (0.750 in Europe), the share seen grows over the bins (35, 45, 63, 87 %), **97.6 % and 96.4 %** of the reports that saw an aurora
in the dark on 10-11 May 2024 (world, Europe) got "possible" or better and **93.8 % and 91.4 %** on 10-11 October 2024, and the
log-likelihood beats the G-scale line (-0.646 against -1.659). **The criterion holds: the estimate is served, marked as ours.**

What the measure also shows, and what changed for it:

- Of the reports put under 5 %, 35 % saw an aurora: the lowest level is named `very_unlikely`, not `no` (a word, not a setting: the method is
  unchanged).
- Half the reports that saw nothing got 20 % or more, 58 % of those under a clear sky: the answer and the site say "estimate", never a promise.
- Within one night the ranking of places is weak (0.42 to 0.79): the Kp is one number for the planet.
- The Moon factor does not help (same ranking, a better log-likelihood without it). It stays as committed; see the open points.

## Open points

- Measuring OVATION itself: an archive would have to be kept by the instance (the grid every 5 minutes), then compared with reports.
- Measuring SWPC's Kp forecast as the input: its archive of 3-day forecasts would be read for the same nights.
- The Moon factor: drop it or weigh it again, in a new measure on a new split (the test set of this one is spent).
- The apex grid is for one epoch (2026.5); it should be rebuilt every few years (the tool takes the epoch).
