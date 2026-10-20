# Recorded answers for the places near a hazard

Real answers, recorded on **2026-10-20** and played back by the tests (no test reaches the network). Nothing in them was edited,
except where a line below says so.

| File | From | What it is |
| --- | --- | --- |
| `usgs_earthquakes_2026-10-20.geojson` | `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson` | Seven of the 656 earthquakes of that feed, features unmodified: **us6000u18k** (M 7.7, Panama, 9 October 2026, depth 12.6 km) and us6000u19w (M 6.6, Panama), which lie on the Azuero Fault Zone; us6000tzth (M 4.6, Assam, India, 10 km deep); us6000u25i (M 4.8, Colombia) and us6000u0uq (M 4.6, Nicaragua), both **deep** (162 and 206 km) although a mapped trace passes a few kilometres from their epicentres; us6000u1gs (M 5.3, open ocean southeast of Easter Island), far from every trace; us6000tzsc (M 5.0, 179.863 east, next to the date line) |
| `gem_raw_excerpt_2026-10-20.geojson` | the GEM Global Active Faults Database, `geojson/gem_active_faults_harmonized.geojson` at commit `56816508ad92fd6846dad1163b1c8c01376a2cd1` (<https://github.com/GEMScienceTools/gem-global-active-faults>) | The six traces of that file (properties and every vertex, **unmodified, not simplified**) that pass within 30 km of the three shallow earthquakes above. Used as the reference the bundled, simplified file is checked against (`tests/oracle_geodesy.py`). **CC BY-SA 4.0**: Styron, Richard, and Marco Pagani. "The GEM Global Active Faults Database." Earthquake Spectra, vol. 36, no. 1_suppl, Oct. 2020, pp. 160-180, doi:10.1177/8755293020944182. This excerpt stays under the same licence |
| `usgs_volcano_elevated_2026-10-20.json` | `https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated` | The four volcanoes the USGS had in alert: Great Sitkin (orange), Shishaldin, Kilauea, Ahyi Seamount (yellow) |
| `gdacs_volcano_2026-10-20.json` | `https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=EQ;TC;FL;VO;WF&alertlevel=Orange;Red&fromDate=2026-09-20&pageSize=100&pageNumber=1` | The two volcano features (`eventtype` `VO`) of that answer: Kanlaon and Taal (Philippines), orange. `name` is `Eruption  Kanlaon` (two spaces), `eventname` is `Kanlaon`: the volcano's name is read from `eventname` |

GDACS had stopped following the two eruptions (`iscurrent` `"false"`) when they were recorded: `tests/places_samples.py` sets the flag
back to `"true"` before the real parser reads them, and says so.

None of these earthquakes happened near the two volcanoes the tests use: where a test needs an earthquake close to a volcano, it
puts one at a stated distance from the volcano's real position, and says so.
