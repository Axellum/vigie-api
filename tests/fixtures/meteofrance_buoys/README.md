# Météo-France buoys, recorded answer

- `bouees-2026-11-16.json`: `https://public-api.meteofrance.fr/public/DPObs/v2/bouees?format=json&date_debut=2026-11-16T18:00:00Z` as answered on 2026-11-16 at about 22:30 UTC (15,255 bytes, `Content-Type: application/json`), unchanged. Public data of Météo-France (see `SOURCES.md` for what is known of the licence).

What it holds that tests rely on: 41 rows, 10 buoys (`geo_id_wmo`), records dated from 18:00 to 22:00 UTC; `6200001`
(`BOUEE_GASCOGNE`, 45.24, -5.01) at 22:00 with `haut_vag` 4.0, `per_moy_vag` 7, `dir_vag` 257, `tmer` 290.15, `t` 292.65,
`ff` 16.1, `rafper` 21.1, `dd` 220, `pmer` 100050; `6101035` (`BOUEE_SARDAIGNE`) and `4100230` (`BOUEE_ANTILLES`) last at 20:00,
Sardinia with no wave field. Used by `tests/test_meteofrance_buoys.py`.
