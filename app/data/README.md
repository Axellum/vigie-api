# `app/data`: the reference data bundled with the API

Built by the tools of `tools/` (their docstrings say where every input comes from and under which licence); nothing in this
folder is fetched at run time. **The code of this repository is under the MIT licence; the data here is not always.**

| File | What it is | Built by | Licence of the data |
| --- | --- | --- | --- |
| `countries.geojson.gz` | Borders of the countries the API has a warning source for (to find the country of a point without a request) | `tools/build_geometries.py` | Natural Earth: public domain |
| `geocodes-nuts.geojson.gz` | Shapes of the NUTS regions MeteoAlarm uses as geocodes (the French departments, the Belgian provinces) | `tools/build_geometries.py` | Natural Earth: public domain; codes from Wikidata: CC0 |
| `swic_registry.json.gz` | Snapshot of the WMO registry of national warning services (without the people's names and e-mail addresses) | `tools/build_swic_snapshot.py` | The registry of the WMO: see `SOURCES.md` |
| `gem-active-faults.geojson.gz` | **The traces of the named active faults of the world**, simplified, to say how far an earthquake is from one (`near_places`) | `tools/build_faults.py` | **CC BY-SA 4.0** (GEM Global Active Faults Database, Styron and Pagani 2020): share-alike, credit required. Notice and statement of the changes: [`gem-active-faults.NOTICE.md`](gem-active-faults.NOTICE.md) |

A shape that has no stated licence is not added here (`SOURCES.md` says which, and why). The Smithsonian list of the world's
volcanoes is **not** here: its licence is unclear and its owner has not been asked yet.
