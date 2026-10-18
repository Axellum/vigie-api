# Vigie Mondiale API (`alerte_mondiale_api`) — Backend d'Agrégation Météo & Catastrophes Mondiales

> **Statut :** Initialisation architecture & spécifications des connecteurs mondiaux (`2026-10-18`)
> **Stack Technique :** Python 3.12+ · FastAPI · `asyncio` · `httpx` · WebSockets (`WSS`) · SQLite WAL / Cache mémoire.
> **Client Matériel Principal :** Terminal 7" Waveshare ESP32-P4 ([`alerte_mondiale_7b`](../alerte_mondiale_7b/README.md)) + Clients Web / Mobile / B2B.

---

## 🎯 Rôle du Dépôt (Pourquoi une API dédiée ?)

Un écran embarqué ESP32-P4 (ou une flotte de milliers d'écrans vendus dans le monde) ne doit **jamais** interroger directement une dizaine d'agences scientifiques internationales (USGS, NOAA, GDACS, NASA, Météo-France...) pour 4 raisons industrielles :
1. **Protection contre le Rate-Limiting / Bannissement IP** : Ce backend interroge les agences mondiales **une seule fois** toutes les 1 à 5 minutes pour l'ensemble des terminaux connectés.
2. **Normalisation & Compression** : Il convertit des flux lourds et hétérogènes (`GeoJSON` de plusieurs Mo, `XML CAP`, `RSS`, `CSV` satellite) en une **trame ultra-compacte et unifiée** décodable en `< 2 ms` sans allocation dynamique sur l'ESP32-P4.
3. **Moteur de Geofencing & Scoring de Sévérité (DEFCON)** : Il calcule la distance entre chaque catastrophe (épicentre d'un séisme, cône d'un ouragan, éruption volcanique, alerte tsunami) et les **zones d'intérêt surveillées** par l'utilisateur pour déclencher instantanément le bon niveau d'alerte (`VERT`, `JAUNE`, `ORANGE`, `ROUGE`).
4. **Valeur Commerciale SaaS (Outil Pro Mondial)** : Permet de gérer les abonnements, les listes de zones par client et d'alimenter aussi bien l'écran 7" Waveshare qu'un grand mur d'écrans de salle de crise.

---

## 📂 Structure du Dépôt

| Dossier / Fichier | Rôle |
| :--- | :--- |
| [`docs/travail/INDEX.md`](docs/travail/INDEX.md) | Point d'entrée du kit documentaire de l'API |
| [`docs/travail/OBJECTIFS.md`](docs/travail/OBJECTIFS.md) | Objectifs de latence, disponibilité, couverture mondiale et preuves |
| [`docs/travail/PLAN.md`](docs/travail/PLAN.md) | Architecture FastAPI asynchrone et découpage en lots (Lots A1 à A5) |
| [`docs/travail/HISTORIQUE_DEV.md`](docs/travail/HISTORIQUE_DEV.md) | Journal chronologique de développement de l'API |
| [`docs/travail/SOURCES_API_MONDIALES.md`](docs/travail/SOURCES_API_MONDIALES.md) | Catalogue technique des APIs mondiales (URLs exactes, fréquences, formats) |
| [`docs/travail/CONTRAT_PAYLOAD.md`](docs/travail/CONTRAT_PAYLOAD.md) | Spécification du contrat JSON / Compact entre `alerte_mondiale_api` et `alerte_mondiale_7b` |
| [`docs/travail/TACHES.md`](docs/travail/TACHES.md) | Board des tâches ouvertes du backend |
| `app/collectors/` | Connecteurs asynchrones (`usgs.py`, `gdacs.py`, `noaa_nws.py`, `open_meteo.py`, `nasa_firms.py`, `swpc_solar.py`) |
| `app/services/` | Normaliseur, moteur de calcul géodésique (Haversine / Geofencing), diffuseur WebSocket |
| `app/routers/` | Routes REST (`/v1/snapshot`, `/v1/zones`, `/v1/alerts`) et WebSocket (`/v1/stream`) |
| `tests/` | Tests unitaires et banc de rejeu de catastrophes historiques |
