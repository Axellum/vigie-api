# AGENTS.md — Instructions Partagées (Vigie Mondiale API)

> Ce fichier est la source unique de consignes pour tous les agents IA travaillant sur `alerte_mondiale_api`.

## 1. Identité du Dépôt
- **Rôle** : Backend d'agrégation asynchrone (FastAPI / `asyncio`) des alertes catastrophes naturelles mondiales (Séismes, Tsunamis, Volcans, Ouragans, Tornades, Inondations, Feux, Tempêtes Solaires) et de la météo multi-zones pour le terminal Waveshare 7B ([`../alerte_mondiale_7b`](../alerte_mondiale_7b/README.md)).
- **Kit documentaire** : Toujours consulter [`docs/travail/INDEX.md`](docs/travail/INDEX.md), [`docs/travail/CONTRAT_PAYLOAD.md`](docs/travail/CONTRAT_PAYLOAD.md) et [`docs/travail/SOURCES_API_MONDIALES.md`](docs/travail/SOURCES_API_MONDIALES.md).

## 2. Invariants Techniques
1. **Résilience des Collecteurs** : Un collecteur externe en timeout (USGS, GDACS, NOAA, Open-Meteo) ne doit **jamais** faire échouer `/v1/snapshot` ni bloquer la boucle d'événements `asyncio`. Toujours conserver le dernier état valide en cache.
2. **Taille du Payload** : Le JSON de `/v1/snapshot` doit rester `< 12 Ko` pour être décodé sans allocation dynamique sur l'ESP32-P4.
3. **Zéro clé en dur** : Toute clé optionnelle (ex. NASA FIRMS) passe par `.env` (non versionné).
