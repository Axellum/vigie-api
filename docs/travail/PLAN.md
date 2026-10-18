# Plan & Architecture — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Plan de développement par lots et architecture modulaire.

---

## 1. Découpage en Lots (Feuille de Route Backend — 100 % Livré)

### Lot A1 — Socle FastAPI, Modèles Standards (Compact, GeoJSON, CAP 1.2) & Géodésie *(Priorité P0 — Livré)*
- Serveur `app/main.py` (FastAPI OpenAPI 3.1 + CORS + WebSocket Hub).
- Schémas Pydantic dans `app/models/schemas.py` conformes à [`CONTRAT_PAYLOAD.md`](CONTRAT_PAYLOAD.md).
- Moteur géodésique `app/services/geodesy.py` (`haversine_km`, `initial_bearing_deg`, `bearing_to_cardinal`, `estimate_tsunami_eta_minutes`, `deduplicate_hazards`).

### Lot A2 — Collecteurs Sismologie, Volcans & Catastrophes avec Circuit Breaker *(Priorité P0 — Livré)*
- `app/collectors/base.py` : Circuit Breaker (`CLOSED`, `OPEN`, `HALF_OPEN`), cache mémoire TTL et `ProviderHealth`.
- `app/collectors/seismic.py` : `USGSCollector` + `EMSCCollector`.
- `app/collectors/disasters.py` : `GDACSCollector` + `NOAANWSCollector`.
- `app/collectors/flood_air.py` : `USGSVolcanoCollector` + `OpenMeteoFloodCollector` (GloFAS).

### Lot A3 — Collecteurs Météo GPS Hyper-Locale, Fallback, Radar Pluie, Air/Cendres, Marine & Solaire *(Priorité P0 — Livré)*
- `app/collectors/weather.py` : `OpenMeteoCollector` (`minutely_15`), `MetNorwayCollector` (Fallback 100 % gratuit mondial) et `RainViewerCollector`.
- `app/collectors/flood_air.py` : `OpenMeteoAirQualityCollector` (EAQI, US AQI, PM2.5, PM10, UV, poussières/cendres).
- `app/collectors/marine.py` : `OpenMeteoMarineCollector` (vagues, houle, courants).
- `app/collectors/space_weather.py` : `NOAASWPCCollector` (indice Kp planétaire, G0–G5, dégradation GPS/HF).

### Lot A4 — Orchestrateur Multi-Providers, Persistance SQLite WAL & Trame Embarquée C++ *(Priorité P0 — Livré)*
- `app/services/storage_wal.py` : Base SQLite en mode `WAL` (`/v1/zones`, `/v1/history`, `snapshot_cache`).
- `app/services/embedded_frame.py` : Générateur de trame texte aligné à 100 % avec `vigie_decoder_trame_api` (`GET /v1/embedded/frame` : `HEAD|...`, `ZONE|...`, `RAIN5M|...`, `DAY|...`, `HAZ|...`, `END|<CRC32>`).
- `app/services/aggregator.py` : Orchestration asynchrone des 12 collecteurs, déduplication inter-agences et bascule météo automatique `Open-Meteo -> MET Norway`.

### Lot A5 — Scheduler Temps Réel, Push Différentiel WebSocket & Dashboard Salle de Crise *(Priorité P0 — Livré)*
- `app/services/scheduler.py` : `BackgroundAlertScheduler` + `detect_new_or_upgraded_hazards()` poussant automatiquement les nouvelles alertes et aggravations + la trame C++ sur `WS /v1/stream` (`/v1/scheduler/status`, `/v1/scheduler/trigger`).
- `app/routers/dashboard.py` : Dashboard Web Pro de Supervision & Salle de Crise (`GET /dashboard`).
