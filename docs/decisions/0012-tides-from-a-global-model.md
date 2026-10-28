# 0012. Tides computed from a global ocean tide model (EOT20), measured before they are served

- Status: accepted, with the limit the measure found: the criterion **holds in France** (9 of 10) and **fails in the United States** (5
  of 10, every failure inside a harbour, a bay or a sound). See "Result" at the end; the criterion above it was not changed.
- Date: 2026-10-28 (criterion committed in `acb9417`, before the measure; result the same day)

## Context

The owner asked for the tide on the map and in the detail of a coastal place: heights, high and low waters, the range, and the
French "coefficient". The tide of a place is predictable years ahead from its harmonic constants, but the constants of the ports of
France are SHOM's, under a paid licence (its tide predictions and its coefficients), and no open set of predictions covers the coasts
of the world. A **global ocean tide model** gives harmonic constants on a grid of the whole ocean, under an open licence:

- **EOT20** (DGFI-TUM, Hart-Davis et al. 2021): 17 constituents on a 1/8 degree grid, **CC BY 4.0**, downloaded with no registration
  (SEANOE, doi:10.17882/79489, an archive of 2.3 GB of which the ocean tide is 1.2 GB; read on 2026-10-28).
- **FES2022** (CNES, LEGOS, NOVELTIS, CLS, distributed by AVISO): 34 constituents on a 1/30 degree grid, "any purpose (scientific,
  commercial...)" with a credit, but behind an AVISO registration, and up to about 170 MB per constituent file.

A model is not a tide table. A cell of 1/8 degree (about 14 km by 9 km at 45 degrees north) cannot follow a narrow estuary or a lagoon, and
the shallow-water constituents that shape the tide in the Channel (MS4, MN4, M6...) are not in EOT20 (M4 is). How far the computed tide is
from the real one, on the coasts where people will read it, is therefore a thing to **measure**, and the measure only means something if
what counts as good enough is written before the figures are seen.

## Decision

1. **Compute the tide from EOT20**, with the method of pyTMD (MIT, version 3.0.9): the 17 constituents of the model, 17 minor ones
   inferred from them, the nodal corrections of the FES convention. pyTMD is used **to build** the data, not at run time: the build tool
   keeps the constants of the ocean cells near a coast and a table of the astronomical arguments per day, and the API sums the cosines with
   numpy. The build tool checks its result against pyTMD itself. FES2022 is the next step if EOT20 fails the criterion where it matters;
   its registration is an action of the owner.
2. **A point gets the tide of the nearest model cell**, within a radius the answer states (30 km); no cell near it, no tide (never a
   guessed one). The answer says which cell, how far, and that the heights are relative to the **mean sea level of the model**, not to the chart
   datum of a port.
3. **"Estimated coefficient", ours and unofficial.** The French tide coefficient is SHOM's (its predictions at Brest, paid licence): it is
   not used. The API computes an estimate from the model's tide at Brest: 100 times the range of each semi-diurnal tide at Brest (the high
   water minus the mean of the low waters on either side) divided by 6.10 m, the unit that secondary sources give for Brest (the mean range
   of equinoctial spring tides; SHOM's own text could not be read on 2026-10-28: **not verified**). It is named an estimate in the field and in
   `derived`, and it is never presented as the official coefficient.
4. **"Not for navigation"** in every answer: a global model is not suitable as a tidal datum.

## The criterion, fixed before the measure

**Window**: 30 days, 2026-09-27 00:00 to 2026-10-27 00:00 UTC (two spring-neap cycles). **Model**: what the API serves, through the
same code (nearest cell, the radius of the API), every 10 minutes.

**References**

- **France, observations**: REFMAR (SHOM, Licence Ouverte 2.0), `services.data.shom.fr/maregraphie/observation/json/{id}`, raw
  high-frequency data (`sources=1`, one value a minute, UTC). The observed sea level includes the weather (surge, seiches): it is smoothed
  by a centred 61-minute mean before its extremes are read. Ten ports: **Calais (55), Le Havre (4), Cherbourg (13), Saint-Malo (410),
  Roscoff (54), Brest (3), Concarneau (160), Les Sables-d'Olonne (62), La Rochelle-La Pallice (34), Saint-Jean-de-Luz Socoa (95)**.
- **United States, predictions**: NOAA CO-OPS (`api.tidesandcurrents.noaa.gov`, `product=predictions`, `datum=MSL`, `time_zone=gmt`,
  `units=metric`; the series at `interval=6` and the extremes at `interval=hilo`). These are NOAA's harmonic predictions, a tide without
  weather. Ten stations: **Boston (8443970), The Battery (8518750), Charleston (8665530), Virginia Key (8723214), Pensacola (8729840), Bob
  Hall Pier (8775870), San Diego (9410170), San Francisco (9414290), Seattle (9447130), Honolulu (1612340)**.
- **Reported, outside the criterion** (chosen now, to show the limits): Arcachon Eyrac (190, inside the lagoon), Bayonne Boucau (94, a river
  mouth), Marseille (524, the Mediterranean, a tide of a few decimetres) and Anchorage (9455920, the head of Cook Inlet).

**Per station.** Each series is taken relative to its own mean over the window. The *mean range* is the mean of the differences between
consecutive high and low waters of the reference. A *significant* reference extreme differs by at least 25 % of the mean range from the
extremes on either side (the small extra high and low waters of a mixed tide are not counted). Each significant reference extreme is matched
with the model extreme of the same kind nearest in time, within 3 hours. A station **passes** when all four hold:

| Measure | Threshold |
| --- | --- |
| Median of the absolute time error of the matched high and low waters | at most **20 minutes** |
| Median of the absolute height error of the matched high and low waters | at most **10 % of the mean range** |
| Root mean square of the difference of the two series, every 10 minutes | at most **10 % of the mean range** |
| Significant reference extremes with no match | at most **5 %** |

**Per set.** The criterion holds when **at least 8 of the 10 French ports and at least 8 of the 10 American stations pass**. A station
with less than 70 % of its 10-minute values is reported as not measured, and the set then needs 80 % of the stations measured, with at
least 7 measured. Whatever the result, every station's figures are published with the measure, and a failure is written as one.

**Reported, outside the criterion**: the estimated coefficient at Brest, compared with the same formula applied to the observed tide of
Brest (it measures the model at Brest, not SHOM's coefficient); and the minimum and maximum of the estimated coefficient over 2026-2044,
which should stay near the 20 and 120 that the coefficient scale is said to span.

## Consequences

- `GET /v1/tides?lat=&lon=&days=`: computed locally, no request to any provider and no quota; the data files are built by
  `tools/build_tides.py` and bundled in `app/data/` (EOT20, CC BY 4.0: credit in every answer and a row in `SOURCES.md`).
- The measure is `tools/measure_tides.py` and its report in `docs/measurements/`; this record gets its result and its status then.

## Result (measured on 2026-10-28)

[`docs/measurements/tides-2026-10.md`](../measurements/tides-2026-10.md), by `tools/measure_tides.py`, with the criterion above unchanged:

- **France: holds, 9 of 10.** Le Havre fails on the times only (23 minutes, 20 allowed). Elsewhere the high and low waters are within 3 to
  18 minutes, their heights within 2 to 5 % of the range.
- **United States: fails, 5 of 10.** Boston, Bob Hall Pier, San Diego, San Francisco and Honolulu pass. The Battery (New York), Charleston,
  Virginia Key and Pensacola are inside a harbour or a bay that a 1/8 degree cell does not enter: the model gives the earlier tide of the
  open coast (21 minutes to 2 h 22 early). At Seattle (Puget Sound) EOT20's constants are wrong (M2 phase about four hours off).
- Estimated coefficient: 3 points from the same formula on the observed tide of Brest (median), 25 to 118 over 2026-2044.

What follows from it:

1. The tide is served wherever the model has a cell, as decided, and **every answer says its limit in words**: the tide of the open coast
   near the point; inside a bay, a harbour, an estuary, a lagoon or a sound the real tide can come an hour or more later and differ, and the
   model can be wrong there. The API cannot tell whether a point is in a bay, so it does not pretend to flag one.
2. **FES2022 is the way to do better there**, and it needs the owner's AVISO registration (the report says where). It will be measured
   with the same tool and the same criterion before it replaces EOT20.
