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
