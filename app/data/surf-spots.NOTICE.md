# Notice: `surf-spots.json`

This file is a **database made from OpenStreetMap**. It is **not** under the MIT licence of the code of this repository: it is
under the licence of the database it comes from, and any copy or adaptation of it must be too.

| | |
| --- | --- |
| **Licence** | Open Data Commons Open Database License (**ODbL 1.0**): <https://opendatacommons.org/licenses/odbl/1-0/>. The terms of OpenStreetMap: <https://www.openstreetmap.org/copyright> (read on 2026-10-28) |
| **Credit** | © OpenStreetMap contributors (the credit OpenStreetMap asks for, with a link to <https://www.openstreetmap.org/copyright>) |
| **Source** | The OpenStreetMap database, read through the Overpass API by `tools/build_spots.py`; the date of the read is the file's `built_utc` |
| **What was taken** | Elements tagged `sport=surfing` that are places, not businesses; named beaches (`natural=beach`) within 1 km of a surf school or club; city, town, village and suburb nodes to name the place; the coastline (`natural=coastline`) near each spot. Each spot lists the elements that make it one (`osm`, `evidence`) |
| **What was added** | The direction each beach faces (`facing_deg`), worked out by Vigie from the coastline, the place chosen (`place`) and the identifier (`id`): they are named in `derived`. A name OpenStreetMap misspells is corrected by the table `tools/surf-spot-corrections.json` (with the evidence); OpenStreetMap's name is then kept in `osm_name` |
| **Endorsement** | None. The OpenStreetMap Foundation and its contributors did not make, check or endorse this file or the API that uses it |
| **Warranty** | None. It says where people surf, not whether the sea is safe there |

## Share-alike, and what it means for this repository

- **This file** (and every database made from it) is ODbL 1.0. Whoever receives it receives the licence, the credit and this notice.
- **The code** of the API that reads it is a separate work under the MIT licence; the answers of `GET /v1/surf/spots` and
  `GET /v1/surf/spots/{id}` carry the credit (`app/attribution.py`, key `osm_spots`). That reading of the licence is the project's,
  **not a lawyer's advice**.

## What is not in the file

The bottom of a spot (sand, reef, rocks), the tide and the swell it works best with: no open source gives them. They stay `null` until
someone who knows the spot gives them with a source. `beach_surface` is the `surface` tag of the beach (what the beach is made of), not
the bottom under the waves.
