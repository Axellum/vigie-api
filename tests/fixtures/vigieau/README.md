# Recorded VigiEau answers

Real responses from the public VigiEau API (`https://api.vigieau.gouv.fr`), recorded on **2026-11-15** and played back by the tests (no test reaches the network).

| File | Endpoint & Query | What it covers |
| --- | --- | --- |
| `zones_v2_paris.json` | `GET /api/zones/v2?lat=48.8566&lon=2.3522` | HTTP 200: 3 active `vigilance` zones (`AEP`, `SUP`, `SOU`) in department `75`, all `availability` entries `available` with `freshness: "updating"` |
| `zones_v2_toulouse.json` | `GET /api/zones/v2?lat=43.6047&lon=1.4442` | HTTP 200: `alerte_renforcee` (`AEP`, `SUP`) in department `31`, nullable `code`/`idSandre`/`gid`/`CdZAS` (`null` on `8420726`), and `SOU` `availability.status = "unavailable"` |
| `zones_v2_nantes.json` | `GET /api/zones/v2?lat=47.2184&lon=-1.5536` | HTTP 200: `alerte` (`AEP`, `SUP`) in department `44`, and `SOU` `availability.status = "confirmed_none"` |
| `zones_v2_unavailable_london.json` | `GET /api/zones/v2?lat=51.5074&lon=-0.1278` | HTTP 200: outside coverage, `zones: []` and all 3 water types `unavailable` with `asOf: null` |
| `zones_v2_commune_ambiguous.json` | `GET /api/zones/v2?commune=31555` | HTTP 409: `ZONE_LOCATION_AMBIGUOUS` with `zoneTypes: ["SUP"]` |
| `departements.json` | `GET /api/departements` | HTTP 200: 101 French departments (96 metropolitan + 5 overseas: `971`–`974`, `976`), spanning `null`, `vigilance`, `alerte`, `alerte_renforcee`, and `crise` |
| `publication.json` | `GET /api/zones/publication` | HTTP 200: publication metadata (shows full bulk GeoJSON exists; Vigie does not download the 315 MB file) |

Licence of the content: Licence Ouverte 2.0 (Etalab), Ministère de la Transition écologique (`VigiEau`). Provided for information only; prefectural decrees published in the Recueil des Actes Administratifs (RAA) remain legally authoritative. See `SOURCES.md`.
