# Notice: `gem-active-faults.geojson.gz`

This file is an **adaptation** of the GEM Global Active Faults Database. It is **not** under the MIT licence of the code of this
repository: it stays under the licence of the database it comes from, and any copy or adaptation of this file must too.

| | |
| --- | --- |
| **Licence** | Creative Commons Attribution-ShareAlike 4.0 International (**CC BY-SA 4.0**): <https://creativecommons.org/licenses/by-sa/4.0/>, legal code <https://creativecommons.org/licenses/by-sa/4.0/legalcode>. The licence of the repository is `LICENSE.txt` of <https://github.com/GEMScienceTools/gem-global-active-faults> (read on 2026-10-20) |
| **Credit** | Styron, Richard, and Marco Pagani. "The GEM Global Active Faults Database." *Earthquake Spectra*, vol. 36, no. 1_suppl, Oct. 2020, pp. 160-180, doi:10.1177/8755293020944182 (the citation the README of the database gives) |
| **Source** | `geojson/gem_active_faults_harmonized.geojson` at commit `56816508ad92fd6846dad1163b1c8c01376a2cd1` (2021-06-24, the last commit of the repository): <https://github.com/GEMScienceTools/gem-global-active-faults/blob/56816508ad92fd6846dad1163b1c8c01376a2cd1/geojson/gem_active_faults_harmonized.geojson> |
| **Changes** | Only the traces that have a name were kept (the label is the fault system name, `fs_name`, else the fault name, `name`; 6,090 of the 13,696 traces), with their geometry and that label only; the lines were simplified (Douglas-Peucker, 0.005 degree, about 550 m) and their coordinates rounded to 3 decimals (about 110 m) and stripped of their elevation. Made by `tools/build_faults.py`, which refuses any input but the pinned file. The same statement is in the file (`changes`) |
| **Endorsement** | None. The GEM Foundation and the authors did not make, check or endorse this file or the API that uses it |
| **Warranty** | None. The material is offered as is (section 5 of the legal code). It is a map of **mapped surface traces**, simplified: it says nothing on whether an earthquake happened on a fault, and it must not be used to decide anything about a building, a site or a person's safety |

## Share-alike, and what it means for this repository

- **This file** (and every file made from it) is CC BY-SA 4.0. Whoever receives it receives the licence, the credit and this notice.
- **The code** of the API that reads it is a separate work under the MIT licence. Reading a CC BY-SA file does not make the code
  that reads it share-alike; the *adapted data* does. That reading of the licence is the project's, **not a lawyer's advice**.
- **The responses**: a hazard whose `near_places` names a fault carries the credit in its `attribution`
  (`app/attribution.py`, key `gem_faults`). The name and the distance are facts about the database's content; they are credited
  and not claimed.

## What is not verified

- That every dataset the GEM database compiles (SHARE, UCERF3, HimaTibetMap, EMME, the national catalogues listed in its
  README) allows the compilation to be shared under CC BY-SA 4.0. The repository states the licence of the whole; the
  constituents were not checked one by one.
- That the `fs_name` column is the *fault system* name: the README of the database describes a `fz_name` column (the fault zone)
  that the file does not have. The reading comes from the content (`Denali fault` for the sections of that fault).
