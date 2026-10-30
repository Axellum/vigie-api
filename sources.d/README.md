# Fragments of SOURCES.md

A pull request that adds or changes the row of a source in a table of `SOURCES.md` does not edit that file: it adds one file
here (`<short-slug>.md`) holding the row:

```
<!-- section: Hazards -->
<!-- replaces: (`usgs_eq`) -->
| **USGS Earthquake Hazards Program** (`usgs_eq`) | ... |
```

- `section` is the heading (without the `#`) of the page whose table gets the row, at the end of the last table under it.
- `replaces` (optional) is a text that picks the one existing row the new one takes the place of; without it, the row is added.
- The tests read `SOURCES.md` with the fragments applied (`sources_page_text()` in `scripts/fragments.py`), so a pull request is
  checked before they are put into the page. `python scripts/fragments.py --write` puts them in at a release.
- A change of prose is edited in `SOURCES.md` directly.
