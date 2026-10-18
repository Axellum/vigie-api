# Kit de travail — Vigie Mondiale API (`alerte_mondiale_api`)

`maj: 2026-10-18` · Un rôle par fichier. Dépôt frère (Terminal 7" Waveshare) : [`../alerte_mondiale_7b/`](../../alerte_mondiale_7b/README.md).

| Fichier | Rôle |
| :--- | :--- |
| [../audits/AUDIT_CONCEPTION_API_MONDIALE.md](../audits/AUDIT_CONCEPTION_API_MONDIALE.md) | Audit d'architecture multi-providers, standards RFC 7946 / OASIS CAP 1.2 et résilience Circuit Breaker |
| [OBJECTIFS.md](OBJECTIFS.md) | Objectifs du backend mondial (Agrégation multi-providers, Météo GPS/Marine, Standards, Résilience) |
| [PLAN.md](PLAN.md) | Plan directeur par lots (A1 à A5) et architecture FastAPI |
| [HISTORIQUE_DEV.md](HISTORIQUE_DEV.md) | Journal chronologique de développement de l'API |
| [SOURCES_API_MONDIALES.md](SOURCES_API_MONDIALES.md) | Référentiel des sources officielles gratuites (USGS, EMSC, GDACS, NOAA, Open-Meteo, MET Norway, RainViewer, SWPC) |
| [CONTRAT_PAYLOAD.md](CONTRAT_PAYLOAD.md) | Contrat d'interface JSON (`/v1/snapshot`, `/v1/point`, `/v1/geojson/hazards`, `/v1/cap/alerts`) |
| [TACHES.md](TACHES.md) | Board des tâches ouvertes et terminées de l'API |

État transversal dans l'écosystème : [`../../contexte_ia/04_Projets/etat_alerte_mondiale.md`](../../contexte_ia/04_Projets/etat_alerte_mondiale.md).
