# Tâches — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · **Convention** : `[statut] #id — lot — prio — description — dépend`
**Statuts** : `⏳ todo` · `🔄 wip` · `🧪 test` · `✅ done` · `🧊 gelé`

---

## Lot A1 — Socle FastAPI, Modèles Standards & Simulateur
- ✅ `#A01` — lotA1 — **P0** — Créer le dépôt `alerte_mondiale_api`, l'arborescence et les spécifications (`OBJECTIFS`, `PLAN`, `SOURCES_API_MONDIALES`, `CONTRAT_PAYLOAD`, `AUDIT_CONCEPTION_API_MONDIALE`).
- ✅ `#A02` — lotA1 — **P0** — Implémenter les modèles Pydantic (`app/models/schemas.py`) et le serveur FastAPI (`app/main.py`) avec `/v1/health`, `/v1/providers`, `/v1/point`, `/v1/snapshot`, `/v1/geojson/hazards`, `/v1/cap/alerts` et `/v1/simulate` — dépend `#A01`.

## Lot A2 & A3 — Collecteurs Mondiaux Multi-Providers & Fallback
- ✅ `#A03` — lotA2 — **P0** — Coder les collecteurs sismiques et catastrophes (`seismic.py` : USGS + EMSC ; `disasters.py` : GDACS + NOAA NWS) avec Circuit Breaker (`base.py`) — dépend `#A02`.
- ✅ `#A04` — lotA3 — **P0** — Coder les collecteurs météo hyper-locale (`weather.py` : Open-Meteo `minutely_15` + Fallback `MET Norway` + radar `RainViewer`), marine (`marine.py` : Open-Meteo Marine) et météo spatiale (`space_weather.py` : NOAA SWPC Kp) — dépend `#A02`.

## Lot A4 & A5 — Géodésie Marine, Déduplication & Push WebSocket
- ✅ `#A05` — lotA4 — **P0** — Implémenter le moteur géodésique (`app/services/geodesy.py` : Haversine, cap initial/cardinal, ETA tsunami, déduplication tri-providers `USGS+EMSC+GDACS`) et l'orchestrateur (`app/services/aggregator.py`) — dépend `#A03`, `#A04`.
- ✅ `#A06` — lotA5 — **P0** — Implémenter le hub WebSocket `/v1/stream` et la suite de tests automatisés (`tests/test_api_and_fusion.py`) vérifiant la fusion, le fallback météo et la contrainte `< 12 Ko` — dépend `#A05`.
- ⏳ `#A07` — lotA5 — **P1** — Ajouter la persistance SQLite WAL d'historisation des catastrophes sur 30 jours et le Dockerfile de déploiement Cloud — dépend `#A06`.
