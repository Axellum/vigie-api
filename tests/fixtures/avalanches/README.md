# Recorded avalanche bulletin fixtures (CAAML v6)

Genuine responses recorded verbatim from official open-data endpoints on `2026-11-15`:

- `slf_caaml_en.geojson`: `GET https://aws.slf.ch/api/bulletin/caaml/en/geojson`
  (WSL Institute for Snow and Avalanche Research SLF, CC BY 4.0, unscheduled bulletin
  valid `2026-11-15T16:00:00Z` to `2026-11-16T16:00:00Z`).
- `euregio_caaml_en.json`: `GET https://static.avalanche.report/bulletins/latest/EUREGIO_en_CAAMLv6.json`
  (Avalanche.report — Tirol, Südtirol, Trentino, CC BY 4.0 DEED, off-season snapshot
  whose last bulletin expired `2026-05-03T15:00:00Z`).
- `euregio_winter_caaml_en.json`: `GET https://static.avalanche.report/bulletins/2026-03-15/2026-03-15_EUREGIO_en_CAAMLv6.json`
  (Avalanche.report archived winter bulletin valid `2026-03-14T16:00:00Z` to
  `2026-03-15T16:00:00Z`, used to exercise active danger ratings, elevation bands,
  problems, and micro-region matching offline).

Recorded on `2026-11-16` from the Météo-France `DPBRA/v1` API (Licence Ouverte 2.0; the files hold no key):

- `meteofrance_bra_mont_blanc.xml`: `GET https://public-api.meteofrance.fr/public/DPBRA/v1/massif/BRA?id-massif=3&format=xml`
  (massif 3, Mont-Blanc; danger 2 below and 3 above 2,700 m, typical situations 2 and 4, every aspect marked).
- `meteofrance_bra_haute_bigorre.xml`: same call with `id-massif=66` (Haute-Bigorre; danger 1, one band, typical situation 6 =
  "pas de situation prédominante", no aspect marked).

Written by Météo-France with Paris wall-clock dates and CRLF line endings (converted to LF here, as everywhere in the repository).
