# Changelog fragments

A pull request does not edit `CHANGELOG.md`: it adds one file here, so that two pull requests never touch the same line.

- Name: `<short-slug>.<type>.md`, where type is `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`
  (for example `surf-overview.added.md`).
- Content: one bullet, as it would be written in the changelog (`- **Surf**: ...`), its continuation lines indented by two
  spaces, no blank line.
- `python scripts/fragments.py --check` verifies them (CI runs it); `python scripts/fragments.py --write --release X.Y.Z`
  puts them into `CHANGELOG.md` at a release and deletes them.

Details: the head of [`scripts/fragments.py`](../scripts/fragments.py).
