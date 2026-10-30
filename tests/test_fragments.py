"""``scripts/fragments.py`` and ``scripts/prochain_adr.py``: the files that replaced the hand edits of CHANGELOG.md and SOURCES.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fragments import (
    FragmentError,
    apply_changelog,
    apply_sources,
    read_changelog_fragments,
    read_sources_fragments,
    release_changelog,
    sources_page_text,
)
from scripts.prochain_adr import adr_numbers, next_number

ROOT = Path(__file__).resolve().parents[1]

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- first
  continued

### Fixed

- a fix

## [1.0.0] - 2026-01-01

### Added

- old
"""


def entry(kind: str, body: str) -> tuple[str, str, Path]:
    return (kind, body, Path(f"x.{kind}.md"))


def test_an_entry_goes_at_the_end_of_its_subsection_of_unreleased_only() -> None:
    out = apply_changelog(CHANGELOG, [entry("added", "- second")])
    assert "- first\n  continued\n- second\n\n### Fixed" in out
    assert out.count("- second") == 1 and out.endswith("- old\n")


def test_a_missing_subsection_is_created_in_the_canonical_order() -> None:
    out = apply_changelog(CHANGELOG, [entry("changed", "- c"), entry("security", "- s")])
    assert out.index("### Added") < out.index("### Changed") < out.index("### Fixed") < out.index("### Security") < out.index("## [1.0.0]")
    assert "### Security\n\n- s\n\n## [1.0.0]" in out


def test_entries_of_one_subsection_keep_the_order_of_the_names() -> None:
    out = apply_changelog(CHANGELOG, [entry("fixed", "- f1"), entry("fixed", "- f2")])
    assert "- a fix\n- f1\n- f2\n\n## [1.0.0]" in out


def test_no_unreleased_heading_is_an_error() -> None:
    with pytest.raises(FragmentError):
        apply_changelog("# Changelog\n", [entry("added", "- x")])


def test_a_release_closes_unreleased_and_opens_a_new_one() -> None:
    out = release_changelog(CHANGELOG, "1.2.0", "2026-11-01")
    assert "## [Unreleased]\n\n## [1.2.0] - 2026-11-01\n\n### Added\n\n- first" in out
    with pytest.raises(FragmentError):
        release_changelog(CHANGELOG, "1.0.0", "2026-11-01")
    with pytest.raises(FragmentError):
        release_changelog(CHANGELOG, "v1", "2026-11-01")


PAGE = """# Sources

## Hazards

Prose.

| Source | Licence |
| --- | --- |
| **A** (`a`) | one |
| **B** (`b`) | two |

## Weather

| Source | Licence |
| --- | --- |
| **W** (`w`) | three |
"""


def sources_fragment(section: str, replaces: str | None, *rows: str) -> tuple[str, str | None, list[str], Path]:
    return (section, replaces, list(rows), Path("s.md"))


def test_a_source_row_is_added_under_the_last_row_of_the_table_of_its_section() -> None:
    out = apply_sources(PAGE, [sources_fragment("Hazards", None, "| **C** (`c`) | new |")])
    assert "| **B** (`b`) | two |\n| **C** (`c`) | new |\n\n## Weather" in out


def test_a_source_row_can_replace_one_row_and_only_one() -> None:
    out = apply_sources(PAGE, [sources_fragment("Weather", "(`w`)", "| **W** (`w`) | changed |")])
    assert "| **W** (`w`) | changed |" in out and "three" not in out
    with pytest.raises(FragmentError):
        apply_sources(PAGE, [sources_fragment("Hazards", "**", "| x |")])  # two rows match
    with pytest.raises(FragmentError):
        apply_sources(PAGE, [sources_fragment("Nowhere", None, "| x |")])


def test_the_real_fragments_apply_to_the_real_files() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    apply_changelog(changelog, read_changelog_fragments(ROOT))
    assert "not an official warning service" in sources_page_text(ROOT).lower()
    assert read_sources_fragments(ROOT) is not None


def test_a_badly_named_or_badly_written_fragment_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "changelog.d"
    folder.mkdir()
    (folder / "x.wrong.md").write_text("- a\n", encoding="utf-8")
    with pytest.raises(FragmentError):
        read_changelog_fragments(tmp_path)
    (folder / "x.wrong.md").unlink()
    (folder / "y.added.md").write_text("not a bullet\n", encoding="utf-8")
    with pytest.raises(FragmentError):
        read_changelog_fragments(tmp_path)
    (folder / "y.added.md").write_text("- a\n\n- b\n", encoding="utf-8")
    with pytest.raises(FragmentError):
        read_changelog_fragments(tmp_path)


def test_adr_numbers_come_from_the_file_names_and_the_next_one_follows_the_highest() -> None:
    paths = ["docs/decisions/0015-tides.md", "docs/decisions" "\\0016-x.md", "docs/decisions/README.md", "docs/other/0099-x.md"]
    assert adr_numbers(paths) == [15, 16]
    assert next_number({15, 16, 18}) == 19 and next_number(set()) == 1


def test_no_two_adr_files_share_a_number() -> None:
    numbers = adr_numbers([f"docs/decisions/{path.name}" for path in (ROOT / "docs" / "decisions").glob("*.md")])
    assert len(numbers) == len(set(numbers)), "two ADRs with one number: run scripts/prochain_adr.py before writing one"
