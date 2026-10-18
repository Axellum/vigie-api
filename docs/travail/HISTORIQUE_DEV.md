# Historique de Développement — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Journal chronologique (le plus récent en haut).

## 📅 2026-10-18 — Création du dépôt & Architecture du Backend Mondial
- Décision d'isoler l'agrégation des APIs météo et catastrophes naturelles dans un dépôt serveur indépendant du firmware de l'écran Waveshare 7B (`alerte_mondiale_7b`).
- Création de l'arborescence (`app/collectors`, `app/models`, `app/routers`, `app/services`, `docs/travail`, `tests`).
- Rédaction du catalogue des APIs institutionnelles gratuites ([`SOURCES_API_MONDIALES.md`](SOURCES_API_MONDIALES.md)) et du contrat d'échange JSON/WebSocket ([`CONTRAT_PAYLOAD.md`](CONTRAT_PAYLOAD.md)).
