# Notice: `surf-spots.json`

This file is a bundled coastal database derived from **OpenStreetMap**, **GIP Littoral**, and **Shom**. The OpenStreetMap extract and attributes are shared under **ODbL 1.0**, and the derived file as a whole preserves share-alike under **ODbL 1.0** alongside **Licence Ouverte 2.0** attributions for GIP Littoral and Shom. The Python code in this repository stays under MIT.

| | |
| --- | --- |
| **Licences** | Open Data Commons Open Database License (**ODbL 1.0**): <https://opendatacommons.org/licenses/odbl/1-0/> and **Licence Ouverte / Open Licence 2.0 (Etalab)**: <https://www.etalab.gouv.fr/licence-ouverte-open-licence/> |
| **Credits** | `© OpenStreetMap contributors` (<https://www.openstreetmap.org/copyright>) · `GIP Littoral, "Plages de Nouvelle-Aquitaine" (PIGMA / data.gouv.fr, read on 2026-11-15), Licence Ouverte 2.0` · `Shom, 2025. Natures de fond au 1/50 000, https://dx.doi.org/10.17183/HOM_GEOL_NATURES_FOND_50 (not for navigation)` |
| **Sources** | OpenStreetMap (Overpass API), GIP Littoral Nouvelle-Aquitaine (<https://www.data.gouv.fr/datasets/plages-de-nouvelle-aquitaine-1>), and Shom WFS INSPIRE `NDF_BDD_WLD_WGS84G_WFS:natures_fond_50000` (`HOM_GEOL_NATURES_FOND_50`, revised 2025-06-23); read offline by `tools/build_spots.py` on `built_utc` |
| **What was taken** | OpenStreetMap `sport=surfing` places, named beaches (`natural=beach`) within 1 km of a surf school/club, coastlines (`natural=coastline`), harbour breakwaters/groins (`man_made=breakwater\|groin\|groyne`), and place names; GIP Littoral oceanic supervised beaches (`nature_baignade=Océanique`); Shom 1/50 000 geological seabed sediment polygons near each spot |
| **What was added** | Coastline facing (`facing_deg`), 360-degree unobstructed open-water sectors over 20 km (`open_water_sectors`, `open_water_width_deg`), normalized seabed category (`bottom` + `seafloor_shom`), `lifeguarded`, nearest locality (`place`), and stable `id` (preserving legacy spot slugs). Overpass typos fixed by `tools/surf-spot-corrections.json` keep `osm_name` alongside `name` |
| **Endorsement** | None. OpenStreetMap contributors, GIP Littoral, and Shom did not build, verify, or endorse this file or this API |
| **Warranty** | **None. Not for navigation or safety.** Seafloor categories are 1/50 000 regional geological layers, and open-water sectors do not model shallow-water wave refraction or local currents |

## What stays `null`

`best_tide` and `best_swell_from_deg` stay `null`: no open-data source with compatible licensing publishes verified per-spot tide or swell preferences. `bottom` and `seafloor_shom` stay `null` (35 of 171 spots) where a spot falls outside Shom's 1/50 000 coastal polygons.
