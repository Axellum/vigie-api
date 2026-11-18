# 0035. The forest fire danger of the department, from Météo-France's "Météo des forêts"

- Status: accepted
- Date: 2026-11-18
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

Part of the plan of 2026-11-18 to plug every Météo-France API the project subscribes to. `DPMeteoForets/v1` gives, for each metropolitan department, an estimate of the forest fire danger in four levels (1 low, 2 moderate, 3 high, 4 very high) for the two days after the publication. Read on 2026-11-18:

- The flux is **seasonal**: published every day at about 17:00 local time **from June to September**, with one-off re-issues when the situation requires. The documentation says that out of season an access to the files is a 404 (it is: `{"detail":"no matching blob"}` on `/carte/encours`, and `{"error":"InvalidNameSpace: mdf_departement"}` on the per-department route), and that the per-department route may return an empty list.
- Licence: the licence document of the API names the Licence Ouverte (Etalab), commercial reuse included, with the source and the date named (read on 2026-11-18).
- **The format.** The portal had no file to give in November. The sample of the product published by Météo-France on data.gouv.fr ("Archives de la Météo des forêts", `DONNEES_EXPERTISEES_MeteoDesForets_echantillons.zip`, Licence Ouverte 2.0, file of 22/05/2024, described there as a sample of the products of this API) holds `tableau-meteo-foret.csv`: header `reference_time;dep_code;niveau_j1;niveau_j2;dep_nom`, `;` separator, 96 rows, UTF-8. It matches the documentation's own example (the Aude, on 22 May 2024: level 2, "moderate"). The yearly archives of the same dataset (`mdf_2025.csv.gz`) use another header (`date,num_dep,niveau_j1,niveau_j2,dep_nom`, `,` separator): the parser reads both.

## Decision

1. **The gateway reads it** ([vigie-meteo#13](https://github.com/Axellum/vigie-meteo/pull/13), `GET /v1/forests?department=`): `carte/encours` every 30 minutes in season and every 6 hours otherwise (a handful of requests a day); a 404 is the state `out_of_season`, **not a failure**: the route answers 200 with `status: out_of_season` and no department, the health stays `fresh` and shows no error. A file that cannot be read (no department found) **is** an error (health `stale`, then `unavailable`, message in `last_error`): the gateway never serves an empty map.
2. **This API gives the department of a position** (`GET /v1/forest-fire?lat=&lon=`, `app/services/forest_fire.py`): the department is found offline from the bundled NUTS3 shapes of the French departments (the shapes that place a vigilance, with a 2 km margin on the coast), only for metropolitan France and Corsica. `status`: `available` (with `day1`, `day2`: level 1 to 4 and its word, `reference_time` of the publication), `out_of_season`, `outside_coverage`, `not_configured`, `unavailable`. Cached ten minutes.
3. It is **a danger for a department**, not a warning (the vigilances stay in `nearby_hazards`) and not a fire (`fires_nearby` of `GET /v1/point` are the detections); the answer's note says so.
4. The credit is Météo-France's, "Licence Ouverte 2.0" (document read).

## Checked

- The parser reads the real sample (96 departments, the Aude at 2 then 1, the Ain at 1 and 1) and the comma-separated archive format; a level of 9 or an unreadable file is refused.
- Against the real portal on 2026-11-18: `carte/encours` answers 404, the gateway records `out_of_season`, health `fresh`, `last_error` empty, and this API answers `out_of_season` for a point in the Aude.
- The department lookup places Carcassonne (11), Bourg-en-Bresse (01) and Ajaccio (2A), and refuses Fort-de-France and Madrid.

## Not verified

- **The file the portal serves in season, today.** Only the 2024 sample could be read; if Météo-France changed the columns since, the gateway raises the error described above in June instead of showing a wrong map. This is the part to watch on the first day of publication (June 2027), and the first thing to read in `/v1/health`.
- The JSON of `carte/departement/encours` (no sample found): it is not used.
- The level words (`low`, `moderate`, `high`, `very high`) are the API's own four levels as its documentation names them ("faible, modéré, élevé, très élevé"), translated by us.
