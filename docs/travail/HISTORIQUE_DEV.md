# Historique de Développement — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Journal chronologique (le plus récent en haut).

## 📅 2026-10-18 — Vague 3 : Alignement 100 % Décodeur C++ (`vigie_decoder_trame_api`), Scheduler Temps Réel & Dashboard Web Pro
- **Alignement 100 % Trame C++ (`app/services/embedded_frame.py`)** :
  - Adaptation exacte de `serialize_embedded_frame()` au format attendu par `vigie_decoder_trame_api` de `alerte_mondiale_7b` : `HEAD|<server_utc>|<solar_kp:.1f>|0|...`, insertion automatique de la zone `GPS` en tête lorsque `lat`/`lon` sont fournis, blocs `ZONE` (24 colonnes incluant houle/courants en nœuds et pluie 15m), `RAIN5M` (12 valeurs sur 60 min), `DAY` (prévisions journalières avec probabilité de pluie et UV), `HAZ` (12 colonnes) et `END|<CRC32_HEX8>`.
- **Scheduler Temps Réel & Détection Différentielle (`app/services/scheduler.py`)** :
  - Implémentation de `BackgroundAlertScheduler` et `detect_new_or_upgraded_hazards()` détectant toute nouvelle catastrophe ou aggravation (`JAUNE -> ORANGE -> ROUGE`) et poussant en temps réel l'alerte + la trame C++ mise à jour sur tous les clients WebSocket (`/v1/stream`).
  - Exposition de `GET /v1/scheduler/status` et `POST /v1/scheduler/trigger`.
- **Dashboard Web Pro de Supervision & Salle de Crise (`app/routers/dashboard.py`)** :
  - Interface HTML5/CSS/JS autonome en Mode Sombre Tactique sur `GET /dashboard` (santé des 12 providers, Watchlist SQLite WAL, alertes actives, simulateur 1-clic et inspecteur en direct de la trame embarquée `/v1/embedded/frame`).
- **Tests Automatisés (`tests/test_scheduler_dashboard_and_wire_contract.py`)** : `19 passed` sur l'ensemble de la suite (`pytest`).

## 📅 2026-10-18 — Vague 2 : Persistance SQLite WAL, Crues GloFAS, Air/Cendres, Volcans USGS & Trame C++ (`/v1/embedded/frame`)
- **Nouveaux Collecteurs Mondiaux (`app/collectors/flood_air.py`)** :
  - `OpenMeteoFloodCollector` (Copernicus GloFAS), `OpenMeteoAirQualityCollector` (AQI, UV, poussières/cendres) et `USGSVolcanoCollector` (codes aviation OACI + VEI).
- **Persistance SQLite WAL (`app/services/storage_wal.py`)** :
  - Gestion dynamique de la Watchlist (`GET/POST/DELETE /v1/zones`), historique dédupliqué (`GET /v1/history`) et cache de snapshot à froid (`snapshot_cache`).

## 📅 2026-10-18 — Implémentation du Socle Multi-Providers, Géodésie Marine & Standards (GeoJSON / CAP 1.2)
- **Audit & Conception** : Rédaction de [`docs/audits/AUDIT_CONCEPTION_API_MONDIALE.md`](../audits/AUDIT_CONCEPTION_API_MONDIALE.md).
- **Moteur Géodésique (`app/services/geodesy.py`)** & **Collecteurs Asynchrones Résilients (`app/collectors/`)** : `USGS`, `EMSC`, `GDACS`, `NOAA NWS`, `Open-Meteo HD`, `MET Norway Fallback`, `RainViewer`, `Open-Meteo Marine`, `NOAA SWPC`.

## 📅 2026-10-18 — Création du dépôt & Architecture du Backend Mondial
- Création du dépôt autonome dédié au backend d'agrégation des APIs météo et catastrophes naturelles.
