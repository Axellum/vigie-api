- **Changelog, sources and ADR numbers without conflicts**: a pull request adds a file to `changelog.d/` (an entry) or `sources.d/`
  (a row of a table of `SOURCES.md`) instead of editing those files, which every parallel pull request used to conflict on.
  `scripts/fragments.py` checks them (CI) and puts them in at a release; the tests read `SOURCES.md` with the fragments applied.
  `scripts/prochain_adr.py` gives the next ADR number from `origin/main` and the open pull requests, and a test fails on two ADRs
  with one number.
