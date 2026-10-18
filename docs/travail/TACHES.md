# Tâches — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · **Convention** : `[statut] #id — lot — prio — description — dépend`
**Statuts** : `⏳ todo` · `🔄 wip` · `🧪 test` · `✅ done` · `🧊 gelé`

---

## Lot A1 — Socle FastAPI & Simulateur
- ✅ `#A01` — lotA1 — **P0** — Créer le dépôt `alerte_mondiale_api`, l'arborescence et les spécifications (`OBJECTIFS`, `PLAN`, `SOURCES_API_MONDIALES`, `CONTRAT_PAYLOAD`).
- ⏳ `#A02` — lotA1 — **P0** — Implémenter les modèles Pydantic (`app/models/payload.py`) et le serveur FastAPI (`app/main.py`) avec `/v1/health`, `/v1/snapshot` et `/v1/simulate` — dépend `#A01`.

## Lot A2 & A3 — Collecteurs Mondiaux Temps Réel
- ⏳ `#A03` — lotA2 — **P0** — Coder les collecteurs `usgs.py` (Séismes + flag Tsunami) et `gdacs.py` (Volcans, Ouragans, Tsunamis, Inondations, Feux) avec tests sur flux réels — dépend `#A02`.
- ⏳ `#A04` — lotA3 — **P0** — Coder le collecteur `open_meteo.py` (météo actuelle, pluie 60 min et prévisions 10 jours multi-zones mondiales) + `swpc_solar.py` (indice Kp) — dépend `#A02`.
- ⏳ `#A05` — lotA3 — **P1** — Coder le collecteur `noaa_nws.py` (alertes Tornades, Ouragans, Tsunami Warnings) — dépend `#A03`.

## Lot A4 & A5 — Geofencing, Déduplication & Push WebSocket
- ⏳ `#A06` — lotA4 — **P1** — Implémenter le moteur de calcul géodésique (distance Haversine aux zones surveillées) et la déduplication inter-agences — dépend `#A03`, `#A04`.
- ⏳ `#A07` — lotA5 — **P1** — Implémenter le hub WebSocket `/v1/stream` pour pousser les alertes `ORANGE`/`ROUGE` en `< 500 ms` vers l'écran Waveshare 7B — dépend `#A06`.
