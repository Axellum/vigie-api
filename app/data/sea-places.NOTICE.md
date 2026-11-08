# Notice: `sea-places.json.gz`

This file is a **database made from OpenStreetMap, completed with two French open datasets**. It is **not** under the MIT licence of
the code of this repository: the part made from OpenStreetMap is under the licence of the database it comes from, and any copy or
adaptation of it must be too. The other two sources are under the Licence Ouverte and need only their credit.

| | |
| --- | --- |
| **Licence** | Open Data Commons Open Database License (**ODbL 1.0**): <https://opendatacommons.org/licenses/odbl/1-0/>. The terms of OpenStreetMap: <https://www.openstreetmap.org/copyright> |
| **Credit** | © OpenStreetMap contributors (with a link to <https://www.openstreetmap.org/copyright>); then the credit of each other source, as `app/attribution.py` writes it (keys `osm_places`, `sandre_ports`, `qsb_bathing`) |
| **Source 1: OpenStreetMap** | Read through the Overpass API by `tools/build_places.py`; the date of the data is the file's `osm_data_utc` (the oldest of the answers). Taken: `leisure=marina` (ports), `leisure=slipway`, `seamark:type=anchorage` and `seamark:type=mooring` (authorised moorings), `scuba_diving:divespot=yes`, the names of the localities (`place=city/town/village/suburb/hamlet`) and the coastline (`natural=coastline`) |
| **Source 2: Sandre** | "Ports - Espace maritime français", Système d'Information sur l'Eau, **Licence Ouverte** (`fr-lo` on data.gouv.fr): <https://www.data.gouv.fr/datasets/ports-espace-maritime-francais>. File `Port_Maritime_FRA.geojson.zip` of 2023-12-19. The version of the licence is not stated on the dataset page |
| **Source 3: bathing sites** | "Qualité des sites de baignade", TACCT (Trajectoires d'Adaptation au Changement Climatique des Territoires) on data.gouv.fr, **Licence Ouverte 2.0** (`lov2`): <https://www.data.gouv.fr/datasets/qualite-des-sites-de-baignade-1>. File of 2026-07-09 (`qualite-sites-baignade.csv`), classes calculated by TACCT from the results of the Ministère des Solidarités et de la Santé over 2021 to 2024. It is a republication by a third party, not the ministry's own file |
| **What was taken** | The Sandre ports with a "Plaisance" activity, in metropolitan France and within 2 km of the coastline; the OpenStreetMap elements above under the same two conditions; the coastal bathing sites of metropolitan France with a class |
| **What was added** | The choice and the merge of the places (a slipway or an anchorage closer than a few hundred metres to another is one; a marina next to a Sandre port is that port), the distance to the coastline test, the name of the nearest locality (`near`), the identifier (`id`) and the tidy lower-case writing of the names of the bathing sites: named in `derived` or explained in `tools/build_places.py` |
| **Endorsement** | None. The OpenStreetMap Foundation and its contributors, the Sandre, TACCT and the ministry did not make, check or endorse this file or the API that uses it |
| **Warranty** | None. It says where places are, not whether they can be used or whether the water is safe today |

## Share-alike, and what it means for this repository

- **This file** (and every database made from it) is ODbL 1.0 for what comes from OpenStreetMap; the Sandre and bathing-site rows keep
  their Licence Ouverte, which asks for the source and the date and, as the project reads it, lets them travel in the same file. Whoever
  receives the file receives this notice.
- **The code** of the API that reads it is a separate work under the MIT licence; the answers of `GET /v1/sea/places` carry the credits
  of the sources of the places they hold. That reading of the licences is the project's, **not a lawyer's advice**.

## What is not in the file

The depth of a port, the number of berths, the fee and the state of a slipway, whether an anchorage is allowed, the state of a dive site,
the waves, the current and the water of today. OpenStreetMap tags for these are too rare and too uneven to say, and no open source here
gives them. A slipway that OpenStreetMap does not tag as private, closed, for customers or for destination only is listed, and may
still be closed in fact. The class of a bathing site is the class of the water over the four years 2021 to 2024: not a statement that
swimming is safe today, and nothing about currents, waves or jellyfish.
