# Tâches — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · **Convention** : `[statut] #id — lot — prio — description — dépend`
**Statuts** : `⏳ todo` · `🔄 wip` · `🧪 test` · `✅ done` · `🧊 gelé`

---

## Lot A1 — Socle FastAPI, Modèles Standards & Simulateur
- ✅ `#A01` — lotA1 — **P0** — Créer le dépôt `alerte_mondiale_api`, l'arborescence et les spécifications (`OBJECTIFS`, `PLAN`, `SOURCES_API_MONDIALES`, `CONTRAT_PAYLOAD`, `AUDIT_CONCEPTION_API_MONDIALE`).
- ✅ `#A02` — lotA1 — **P0** — Implémenter les modèles Pydantic (`app/models/schemas.py`) et le serveur FastAPI (`app/main.py`) avec `/v1/health`, `/v1/providers`, `/v1/point`, `/v1/snapshot`, `/v1/geojson/hazards`, `/v1/cap/alerts` et `/v1/simulate` — dépend `#A01`.

## Lot A2 & A3 — Collecteurs Mondiaux Multi-Providers & Fallback (12 Collecteurs)
- ✅ `#A03` — lotA2 — **P0** — Coder les collecteurs sismiques et catastrophes (`seismic.py` : USGS + EMSC ; `disasters.py` : GDACS + NOAA NWS) avec Circuit Breaker (`base.py`) — dépend `#A02`.
- ✅ `#A04` — lotA3 — **P0** — Coder les collecteurs météo hyper-locale (`weather.py` : Open-Meteo `minutely_15` + Fallback `MET Norway` + radar `RainViewer`), marine (`marine.py` : Open-Meteo Marine) et météo spatiale (`space_weather.py` : NOAA SWPC Kp) — dépend `#A02`.
- ✅ `#A05` — lotA2/A3 — **P0** — Ajouter les collecteurs complémentaires dans `app/collectors/flood_air.py` : Crues fluviales (`OpenMeteoFloodCollector` GloFAS Copernicus), Qualité de l'Air / Poussières / Cendres & UV (`OpenMeteoAirQualityCollector`) et Volcans (`USGSVolcanoCollector` codes aviation OACI + VEI) — dépend `#A03`, `#A04`.

## Lot A4 & A5 — Géodésie Marine, Déduplication, SQLite WAL, Trame C++, Scheduler & Dashboard
- ✅ `#A06` — lotA4 — **P0** — Implémenter le moteur géodésique (`app/services/geodesy.py` : Haversine, cap initial/cardinal, ETA tsunami, déduplication tri-providers `USGS+EMSC+GDACS`) et l'orchestrateur (`app/services/aggregator.py`) — dépend `#A03`, `#A04`.
- ✅ `#A07` — lotA4 — **P0** — Implémenter la persistance SQLite WAL (`app/services/storage_wal.py`) avec gestion dynamique de la Watchlist (`GET/POST/DELETE /v1/zones`), historique filtrable des catastrophes (`GET /v1/history`) et cache de snapshot à froid — dépend `#A06`.
- ✅ `#A08` — lotA4 — **P0** — Aligner 100 % le générateur de trame compacte embarquée (`app/services/embedded_frame.py` + `GET /v1/embedded/frame`) avec le décodeur C++ `vigie_decoder_trame_api` (`HEAD`, `ZONE`, `RAIN5M`, `DAY`, `HAZ`, `END|CRC32`) — dépend `#A07`.
- ✅ `#A09` — lotA5 — **P0** — Implémenter le scheduler temps réel à détection différentielle (`app/services/scheduler.py`), le Dashboard Web Pro de Supervision (`app/routers/dashboard.py` sur `GET /dashboard`) et la suite de 19 tests automatisés (`pytest`, 19/19 verts) — dépend `#A08`.
