# Vigie API

*[Read this document in English](README.md)*

Un projet personnel, fait avec l'aide de l'IA et partagé au cas où il servirait à quelqu'un. Il est fourni
tel quel, sans aucune promesse de support, de disponibilité ni d'exactitude.

Il rassemble des dangers naturels dans le monde (séismes, tsunamis, volcans, cyclones, tornades,
inondations, incendies, orages géomagnétiques) et de la météo (conditions actuelles, pluie à 15 minutes,
prévisions, mer, débit des rivières, qualité de l'air) à partir de sources publiques, puis les sert :

- aux écrans ESP32-P4 du projet Vigie (Waveshare 7B, M5Stack Tab5), par une trame texte compacte :
  [`docs/embedded-frame.md`](docs/embedded-frame.md) ;
- à tout autre client, en JSON, en GeoJSON (IETF RFC 7946) et en OASIS CAP 1.2.

> **Ce n'est pas un service d'alerte officiel.** Les alertes sont relayées depuis des flux publics. Elles
> peuvent arriver en retard, être incomplètes ou fausses, et ne doivent pas être la seule base d'une
> décision. **Les alertes de GDACS sont automatiques et ne remplacent pas les alertes officielles.**
> Suivez les avertissements des autorités de votre pays.

> **Aucune donnée inventée.** Quand une source ne fournit pas une valeur, l'API la déclare inconnue
> (`null` en JSON, champ vide dans la trame). Elle n'affiche jamais une valeur par défaut qui aurait l'air
> plausible. Voir l'[ADR 0001](docs/decisions/0001-two-repos-no-fabricated-data-frame-format.md)
> (en anglais).

## Licence, données et crédits

Le code est sous [licence MIT](LICENSE). **Les données n'en font pas partie** : elles appartiennent à
chaque fournisseur et restent soumises à ses propres conditions. [`SOURCES.md`](SOURCES.md) (en anglais)
donne, pour chaque source, la licence, les conditions, le crédit demandé, le quota, et ce qui n'a pas pu
être vérifié.

- Chaque bloc de données des réponses JSON porte un champ `attribution` avec le crédit que sa source
  demande (la trame compacte n'a pas ce champ). Les textes sont déclarés dans
  [`app/attribution.py`](app/attribution.py).
- **L'API gratuite d'Open-Meteo ne peut être utilisée qu'à des fins non commerciales**
  ([conditions](https://open-meteo.com/en/terms)), dans la limite de 10 000 appels par jour, avec un lien
  vers Open-Meteo partout où ses données sont affichées. Ce projet est personnel et non commercial.
  **Ne l'utilisez pas à des fins commerciales sans abonnement payant à Open-Meteo**, et vérifiez les
  conditions des autres fournisseurs.
- L'API gratuite de RainViewer est réservée à un usage personnel et pédagogique. Désactivez-la sur une
  instance qui relaie ses données à d'autres personnes (`VIGIE_ENABLE_RAINVIEWER=0` ; l'image Docker le
  fait).

## Points d'accès

| Point d'accès | Rôle |
| --- | --- |
| `GET /v1/embedded/frame` | Trame texte compacte pour les écrans ([contrat](docs/embedded-frame.md)). |
| `GET /v1/snapshot` | Zones surveillées (météo) et dangers mondiaux, JSON compact. |
| `GET /v1/point?lat=&lon=` | Météo en un point GPS, mer (`marine=true`), débit des rivières, qualité de l'air, météo spatiale, dangers proches. |
| `GET /v1/geojson/hazards` | Dangers actifs en GeoJSON (RFC 7946). |
| `GET /v1/cap/alerts` | Dangers actifs en OASIS CAP 1.2. |
| `GET/POST /v1/zones`, `DELETE /v1/zones/{id}` | Zones surveillées (la liste de suivi). Une zone doit donner son `utc_offset_s`. |
| `GET /v1/history` | Historique des dangers, stocké dans SQLite. |
| `GET /v1/health`, `GET /v1/providers` | État, disjoncteur et cache de chaque source, avec ses appels, son poids et son quota sur 24 heures. |
| `GET/POST /v1/scheduler/...` | État du planificateur en arrière-plan (facultatif), lancement d'un cycle immédiat. |
| `POST /v1/simulate` | Injecte un scénario **simulé** pour les essais (toujours étiqueté comme simulation). **Désactivé par défaut** : il répond 403 sauf si `VIGIE_ENABLE_SIMULATION=1`. |
| `WS /v1/stream` | WebSocket avec l'état initial et les alertes poussées. |
| `GET /dashboard` | Page web de supervision. |
| `GET /docs` | Documentation OpenAPI interactive. |

**L'API n'a aucune authentification.** Utilisez-la sur un réseau de confiance. Ne l'exposez pas telle quelle
sur Internet : quiconque peut l'atteindre peut ajouter ou supprimer des zones surveillées et déclencher des
cycles de collecte. Si vous devez l'exposer, placez devant elle un proxy inverse qui authentifie.

## Lancer

Python 3.12 ou plus récent (la CI couvre 3.12 et 3.13).

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     (Windows)
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Sous Windows, `scripts\run.ps1` fait tout cela (voir plus bas). Ouvrez ensuite
`http://localhost:8080/dashboard` ou `http://localhost:8080/v1/embedded/frame`.

### Avec Docker

```
docker build -t vigie-api .
docker run --rm -p 8080:8080 -v vigie-data:/data -e VIGIE_CONTACT=vous@example.org vigie-api
```

L'image (`python:3.13-slim`, construite pour linux/amd64 par la CI) tourne sous un utilisateur non
administrateur, garde sa base dans le volume `/data` et a un contrôle de santé sur `/v1/health`. RainViewer
est désactivé dans l'image ; ajoutez `-e VIGIE_ENABLE_RAINVIEWER=1` pour une instance personnelle. Le
workflow de publication pousse `ghcr.io/axellum/vigie-api` quand une étiquette de version (`v*`) est créée ;
tant qu'il n'y en a pas, construisez l'image vous-même.

### Configuration

Tous les réglages sont des variables d'environnement facultatives.

| Variable | Valeur par défaut | Rôle |
| --- | --- | --- |
| `VIGIE_DB_PATH` | `vigie.db` à la racine du dépôt (ignoré par git) ; `/data/vigie.db` dans l'image Docker | Fichier SQLite (zones surveillées, historique, dernier instantané). |
| `VIGIE_SCHEDULER` | `0` | `1` démarre le planificateur en arrière-plan (rafraîchissement toutes les 90 s, envoi par WebSocket). |
| `VIGIE_ENABLE_SIMULATION` | `0` | `1` active `POST /v1/simulate` (et les boutons du tableau de bord). La route n'a pas d'authentification et écrit les alertes simulées dans l'historique des dangers : ne l'activez que sur une instance que seuls des clients de confiance peuvent atteindre. |
| `VIGIE_ENABLE_RAINVIEWER` | `1` ; `0` dans l'image Docker | `0` cesse d'interroger RainViewer (les champs radar de `/v1/point` restent inconnus). Son API gratuite est réservée à un usage personnel et pédagogique : mettez `0` sur une instance qui sert d'autres personnes. |
| `VIGIE_CONTACT` | aucune | Adresse e-mail ou URL ajoutée au `User-Agent` envoyé aux sources. MET Norway et NOAA exigent un contact identifiable : renseignez-la. |
| `VIGIE_CAP_SENDER` | `vigie-api` | `sender` des alertes CAP. |
| `VIGIE_GRID_FORECAST_DEG` | `0.05` | Grille (en degrés, environ 5 km) sur laquelle la position est arrondie avant d'interroger la prévision Open-Meteo et MET Norway. |
| `VIGIE_GRID_AIR_QUALITY_DEG` | `0.1` | Idem pour Open-Meteo Air Quality (environ 11 km). |
| `VIGIE_GRID_FLOOD_DEG` | `0.1` | Idem pour Open-Meteo Flood (GloFAS). |
| `VIGIE_GRID_MARINE_DEG` | `0.1` | Idem pour Open-Meteo Marine. |
| `VIGIE_BUDGET_FRACTION` | `0.9` | Part du quota publié de chaque source (Open-Meteo 10 000 appels par jour, MET Norway, RainViewer) que l'API laisse passer. Au-delà, le cache est servi, marqué périmé. |

Aucune source ne demande de clé d'API. Une valeur de grille hors de 0,0001 à 10 degrés est ignorée (la
valeur par défaut est utilisée). Seules la requête envoyée à la source et la clé du cache utilisent le point
arrondi : la réponse donne toujours la position exacte du client, et les distances aux dangers l'utilisent.

### Appels aux sources

Les appels aux sources ne devraient pas augmenter avec le nombre de clients : ils dépendent du nombre de
cases de 5 km distinctes demandées et de la vitesse à laquelle chaque source change vraiment (grilles, une
entrée de cache par case, une seule requête pour des appelants simultanés, durées de cache calées sur le
rythme de chaque source, un budget d'appels ; voir [`docs/architecture.md`](docs/architecture.md) et
[`docs/data-sources.md`](docs/data-sources.md), en anglais). `tests/test_call_sobriety.py` rejoue une journée
simulée avec le planificateur actif et ses 5 zones par défaut, et 1 000 clients sur 50 points (20 chacun,
qui interrogent toutes les 30 minutes ; 10 points sont des ports qui demandent aussi la mer ; deux points ne
partagent jamais une case, donc les grilles n'aident pas), contre une source simulée, et compte les requêtes
que reçoit chaque source :

| Source | Appels par jour, avant | après |
| --- | ---: | ---: |
| Open-Meteo prévision | 17 212 | 6 167 |
| Open-Meteo qualité de l'air | 7 182 | 1 320 |
| Open-Meteo débit des rivières | 6 122 | 200 |
| Open-Meteo mer | 2 216 | 240 |
| **Open-Meteo, pondéré** (le quota gratuit est de 10 000 par jour) | **46 502** | **8 412** |
| Séismes USGS, EMSC | 1 980 chacun | 1 393 chacun |
| Alertes NOAA NWS | 1 069 | 705 |
| GDACS | 676 | 474 |
| RainViewer | 662 | 144 |
| NOAA SWPC | 403 | 286 |
| Volcans USGS | 212 | 144 |

« Avant » est `main` au commit 1864e25 (même simulation, mesurée une fois) ; « après » est ce code. L'heure
la plus chargée d'Open-Meteo pèse 2 305 avant et 412 après (quota de 5 000), la minute la plus chargée 497 et
231 (quota de 600). Avec des clients qui interrogent toutes les 15 minutes, « avant » passe à 54 766 et
« après » à 8 667 : la charge des clients change à peine le « après ». Les 55 cases distinctes de ce scénario
consomment 84 % du quota gratuit ; au-delà, le budget arrête les requêtes et sert le cache, marqué périmé.
À rejouer soi-même : `python -m tests.simulation` (une vingtaine de secondes).

### Lancer sous Windows (un PC qui alimente l'écran)

L'écran récupère `http://<adresse du PC>:8080/v1/embedded/frame`. Donnez au PC une adresse fixe dans la box
ou le routeur pour que cette URL ne change pas. Ci-dessous, `<dossier du dépôt>` est le dossier où vous avez
cloné ce dépôt.

1. **Démarrer l'API**, depuis n'importe quel dossier, dans un PowerShell normal (pas administrateur) :

   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File <dossier du dépôt>\scripts\run.ps1
   ```

   Le script crée `.venv` dans le dépôt au premier lancement, installe `requirements.txt` (de nouveau
   seulement si le fichier change) et démarre `uvicorn app.main:app --host 0.0.0.0 --port 8080`.
   Options : `-Port`, `-BindAddress`, `-LogFile <fichier>`, `-NoInstall`. Arrêt avec Ctrl+C.

2. **Autoriser l'écran à l'atteindre.** Le pare-feu Windows bloque les connexions entrantes par défaut.
   Lancez cette ligne **une seule fois, dans un PowerShell ouvert en administrateur** (elle ouvre le port
   TCP 8080 sur le profil réseau Privé, pour le sous-réseau local seulement) :

   ```
   New-NetFirewallRule -DisplayName "Vigie API 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private -RemoteAddress LocalSubnet
   ```

   Le réseau de la maison doit être déclaré *Privé* dans Windows (Paramètres, Réseau et Internet). Pour
   retirer la règle plus tard : `Remove-NetFirewallRule -DisplayName "Vigie API 8080"`.

3. **Vérifier depuis un autre appareil** du même réseau :

   ```
   Invoke-WebRequest http://<adresse du PC>:8080/v1/health -UseBasicParsing
   ```

4. **Démarrer à l'ouverture de session** avec le Planificateur de tâches (le dépôt ne crée rien).
   Planificateur de tâches, *Créer une tâche...* (pas *Créer une tâche de base*) :

   - *Général* : nom `Vigie API` ; « Exécuter uniquement si l'utilisateur est connecté ».
   - *Déclencheurs* : Nouveau, « À l'ouverture de session », utilisateur précis : vous.
   - *Actions* : Nouvelle, « Démarrer un programme ». Programme : `powershell.exe`. Arguments :
     `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<dossier du dépôt>\scripts\run.ps1" -LogFile "<dossier du dépôt>\logs\vigie-api.log"`.
     Commencer dans : `<dossier du dépôt>`.
   - *Paramètres* : « Si la tâche échoue, la redémarrer toutes les » 1 minute, 3 tentatives ; décochez
     « Arrêter la tâche si elle s'exécute plus de ». Si le dépôt est sur un autre disque, ce disque doit être
     disponible à l'ouverture de session. Si le dossier du dépôt est renommé ou déplacé, mettez les chemins à
     jour. La sortie du serveur va dans `logs\vigie-api.log` (ignoré par git) ; arrêtez le serveur en
     terminant la tâche dans le Planificateur de tâches.

## Développer

```
python -m pytest              # aucun test ne touche le réseau
python -m ruff check .        # analyse statique
python tools/capture_frame.py frame.txt     # écrit une VRAIE trame (utilise les vraies sources)
python tools/check_live_feeds.py            # interroge chaque vraie source une fois, vérifie que les analyseurs la lisent encore
```

`tools/capture_frame.py` démarre l'application dans le processus, récupère
`/v1/embedded/frame?device_type=waveshare-7b` auprès des vraies sources et l'écrit dans le fichier donné.
Il sert à produire le jeu d'essai du dépôt de l'écran.

CI (`.github/workflows/`) :

- `ci.yml`, à chaque envoi sur `main` et chaque pull request : ruff et pytest sur Python 3.12 et 3.13, et une
  tâche qui construit l'image Docker, la lance et interroge `/v1/health` et `/v1/embedded/frame`.
- `secret-scan.yml` : gitleaks sur tout l'historique git, à chaque pull request et envoi sur `main`.
- `live-feeds.yml` : chaque lundi et à la demande, jamais dans une pull request, `tools/check_live_feeds.py`
  interroge chaque vraie source une fois. Une exécution rouge veut dire qu'une source a changé de format ou
  retiré un point d'accès.
- `publish-image.yml` : pousse l'image Docker vers `ghcr.io/axellum/vigie-api` quand une étiquette `v*` est
  poussée.

## Sources de données

| Domaine | Source |
| --- | --- |
| Séismes | USGS, EMSC / SeismicPortal, GDACS |
| Catastrophes | GDACS, NOAA NWS, USGS Volcano Hazards Program |
| Météo spatiale | NOAA SWPC |
| Météo | Open-Meteo (principale), MET Norway (secours), RainViewer (radar) |
| Mer, débit des rivières, qualité de l'air | Open-Meteo Marine, Flood (GloFAS), Air Quality (CAMS) |

Licence, conditions, crédit, quota et clauses de chaque source, et ce qui n'est pas vérifié :
[`SOURCES.md`](SOURCES.md). Comment chacune est mise en cache et ce qu'elle coûte en appels :
[`docs/data-sources.md`](docs/data-sources.md).

## Documentation

La documentation du dépôt est en anglais.

- [`SOURCES.md`](SOURCES.md) : les sources, leurs licences, leurs crédits et leurs quotas.
- [`docs/architecture.md`](docs/architecture.md) : composants, flux de données, limites.
- [`docs/embedded-frame.md`](docs/embedded-frame.md) : le contrat de la trame (source unique de vérité).
- [`docs/data-sources.md`](docs/data-sources.md) : comment chaque source est utilisée, cache et volume d'appels.
- [`docs/decisions/`](docs/decisions) : décisions d'architecture.
- [`CHANGELOG.md`](CHANGELOG.md), [`AGENTS.md`](AGENTS.md) (guide pour les agents de code IA).
