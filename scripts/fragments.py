"""Fragments for the two files every pull request used to edit: ``CHANGELOG.md`` and ``SOURCES.md``.

A pull request that adds a line to the same place of the same file as another one conflicts with it. So a pull request no
longer edits those files: it adds a small file of its own, and this script puts the fragments into the files when someone
wants it (at a release, or whenever the owner asks).

``changelog.d/<slug>.<type>.md``: one entry of the changelog. ``<type>`` is added, changed, deprecated, removed, fixed or
security. The file holds one bullet (``- ...``, its continuation lines indented by two spaces), no blank line.

``sources.d/<slug>.md``: one or more rows of a table of ``SOURCES.md``. Its first lines are comments:

    <!-- section: Hazards -->            the heading (without the #) whose table gets the row: the last table under it
    <!-- replaces: `usgs_eq` -->         optional: the text that picks the one existing row this one replaces

Without ``replaces:`` the row is added at the end of the table. Prose of ``SOURCES.md`` is edited by hand (rare).

    python scripts/fragments.py --check                    the fragments are valid and apply (what CI runs)
    python scripts/fragments.py --check --base origin/main also: this branch did not edit CHANGELOG.md by hand
    python scripts/fragments.py --write                    put them into CHANGELOG.md and SOURCES.md, delete them
    python scripts/fragments.py --write --release 1.2.0    also close [Unreleased] as the version 1.2.0

The tests read ``sources_page_text()``: SOURCES.md as it will be once the fragments are in.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_DIR = "changelog.d"
SOURCES_DIR = "sources.d"
# The order of the headings of Keep a Changelog.
TYPES = ("added", "changed", "deprecated", "removed", "fixed", "security")
UNRELEASED = "## [Unreleased]"


class FragmentError(Exception):
    """A fragment that cannot be applied, with the file and the reason."""


def _fragment_files(root: Path, folder: str) -> list[Path]:
    directory = root / folder
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.md") if path.name.lower() != "readme.md")


def read_changelog_fragments(root: Path = ROOT) -> list[tuple[str, str, Path]]:
    """(type, bullet text, file) of every ``changelog.d`` fragment, in the order of their names."""
    found = []
    for path in _fragment_files(root, CHANGELOG_DIR):
        parts = path.name.split(".")
        if len(parts) != 3 or parts[1] not in TYPES or not parts[0]:
            raise FragmentError(f"{path.name}: the name must be <slug>.<type>.md, type one of {', '.join(TYPES)}")
        body = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")
        if not body.startswith("- "):
            raise FragmentError(f"{path.name}: the entry must start with '- '")
        if "\n\n" in body or any(line.strip() == "" for line in body.split("\n")):
            raise FragmentError(f"{path.name}: no blank line inside an entry")
        found.append((parts[1], body.rstrip(), path))
    return found


def _end_of_block(lines: list[str], start: int, stops: tuple[str, ...]) -> int:
    """Index of the first line after ``start`` that begins with one of ``stops`` (or the end)."""
    for index in range(start + 1, len(lines)):
        if lines[index].startswith(stops):
            return index
    return len(lines)


def _after_last_content(lines: list[str], start: int, end: int) -> int:
    """Where to insert at the end of ``lines[start:end]``: after its last line that is not blank."""
    position = end
    while position > start + 1 and lines[position - 1].strip() == "":
        position -= 1
    return position


def apply_changelog(text: str, fragments: list[tuple[str, str, Path]]) -> str:
    """``text`` with the entries added at the end of their ``### Type`` of ``[Unreleased]`` (created where missing)."""
    lines = text.split("\n")
    try:
        start = lines.index(UNRELEASED)
    except ValueError:
        raise FragmentError(f"CHANGELOG.md has no '{UNRELEASED}' heading") from None
    for kind in TYPES:
        entries = [body for found, body, _ in fragments if found == kind]
        if not entries:
            continue
        heading = f"### {kind.capitalize()}"
        end = _end_of_block(lines, start, ("## ",))
        block = lines[start:end]
        entry_lines = [line for entry in entries for line in entry.split("\n")]
        if heading in block:
            index = start + block.index(heading)
            sub_end = _end_of_block(lines, index, ("### ", "## "))
            at = _after_last_content(lines, index, sub_end)
            lines[at:at] = entry_lines
            continue
        # A new subsection goes before the first existing one that comes later in the canonical order.
        at = None
        for index in range(start + 1, end):
            if lines[index].startswith("### ") and lines[index][4:].lower() in TYPES:
                if TYPES.index(lines[index][4:].lower()) > TYPES.index(kind):
                    at = index
                    break
        if at is None:
            at = _after_last_content(lines, start, end)
            lines[at:at] = ["", heading, "", *entry_lines]
        else:
            lines[at:at] = [heading, "", *entry_lines, ""]
    return "\n".join(lines)


def release_changelog(text: str, version: str, date: str) -> str:
    """Close ``[Unreleased]`` as ``[version] - date`` and open an empty ``[Unreleased]`` above it."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", version):
        raise FragmentError(f"'{version}' is not a version (1.2.3)")
    if f"## [{version}]" in text:
        raise FragmentError(f"CHANGELOG.md already has a section for {version}")
    return text.replace(UNRELEASED, f"{UNRELEASED}\n\n## [{version}] - {date}", 1)


def _heading_text(line: str) -> str | None:
    match = re.match(r"#{1,6}\s+(.*?)\s*$", line)
    return match.group(1) if match else None


def read_sources_fragments(root: Path = ROOT) -> list[tuple[str, str | None, list[str], Path]]:
    """(section, replaces, rows, file) of every ``sources.d`` fragment."""
    found = []
    for path in _fragment_files(root, SOURCES_DIR):
        section = replaces = None
        rows: list[str] = []
        for raw in path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
            line = raw.strip()
            if not line:
                continue
            directive = re.fullmatch(r"<!--\s*(section|replaces):\s*(.*?)\s*-->", line)
            if directive:
                if directive.group(1) == "section":
                    section = directive.group(2)
                else:
                    replaces = directive.group(2)
            elif line.startswith("|"):
                rows.append(line)
            else:
                raise FragmentError(f"{path.name}: only comments and table rows ('| ... |') are allowed, got: {line[:60]}")
        if not section:
            raise FragmentError(f"{path.name}: the first lines must say <!-- section: Heading -->")
        if not rows:
            raise FragmentError(f"{path.name}: no table row")
        if replaces and len(rows) != 1:
            raise FragmentError(f"{path.name}: a fragment that replaces a row holds exactly that row")
        found.append((section, replaces, rows, path))
    return found


def apply_sources(text: str, fragments: list[tuple[str, str | None, list[str], Path]]) -> str:
    lines = text.split("\n")
    for section, replaces, rows, path in fragments:
        starts = [i for i, line in enumerate(lines) if _heading_text(line) == section]
        if len(starts) != 1:
            raise FragmentError(f"{path.name}: {len(starts)} headings named '{section}' in SOURCES.md (exactly one is needed)")
        start = starts[0]
        end = _end_of_block(lines, start, ("#",))
        table = [i for i in range(start + 1, end) if lines[i].startswith("|")]
        if not table:
            raise FragmentError(f"{path.name}: no table under '{section}'")
        if replaces:
            hits = [i for i in table if replaces in lines[i]]
            if len(hits) != 1:
                raise FragmentError(f"{path.name}: {len(hits)} rows of '{section}' contain {replaces!r} (exactly one is needed)")
            lines[hits[0]] = rows[0]
        else:
            lines[table[-1] + 1 : table[-1] + 1] = rows
    return "\n".join(lines)


def sources_page_text(root: Path = ROOT) -> str:
    """SOURCES.md as it will be once the fragments of ``sources.d`` are in it (what the tests read)."""
    text = (root / "SOURCES.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    return apply_sources(text, read_sources_fragments(root))


def changelog_text(root: Path = ROOT) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    return apply_changelog(text, read_changelog_fragments(root))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def check_not_edited_by_hand(base: str) -> list[str]:
    """Problems if this branch edited CHANGELOG.md (an error) or SOURCES.md (a warning) since it left ``base``."""
    changed = set(_git("diff", "--name-only", f"{base}...HEAD", "--", "CHANGELOG.md", "SOURCES.md").split())
    problems = []
    if "CHANGELOG.md" in changed:
        problems.append(
            "error: CHANGELOG.md was edited by hand. Add a file to changelog.d/ instead "
            "(<slug>.<added|changed|fixed|...>.md, one bullet); only a release or assemble branch edits CHANGELOG.md."
        )
    if "SOURCES.md" in changed:
        problems.append(
            "warning: SOURCES.md was edited by hand. A new row or a changed row of a table belongs in sources.d/; "
            "a change of prose is fine."
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="validate the fragments and that they apply")
    parser.add_argument("--base", help="with --check: the branch this one leaves (e.g. origin/main); CHANGELOG.md must be unchanged")
    parser.add_argument("--write", action="store_true", help="put the fragments into the files and delete them")
    parser.add_argument("--release", metavar="X.Y.Z", help="with --write: close [Unreleased] as this version")
    parser.add_argument("--date", default=datetime.date.today().isoformat(), help="date of the release (default: today)")
    args = parser.parse_args(argv)
    if not (args.check or args.write):
        parser.error("say --check or --write")

    try:
        changelog = changelog_text()
        sources = sources_page_text()
        if args.release:
            changelog = release_changelog(changelog, args.release, args.date)
    except FragmentError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    status = 0
    if args.check and args.base:
        for problem in check_not_edited_by_hand(args.base):
            print(problem, file=sys.stderr)
            if problem.startswith("error"):
                status = 1
    if args.check:
        print(
            f"{len(read_changelog_fragments())} changelog fragment(s) and {len(read_sources_fragments())} sources fragment(s) apply cleanly"
            if status == 0
            else "fragments apply, but see above"
        )
    if args.write and status == 0:
        (ROOT / "CHANGELOG.md").write_text(changelog, encoding="utf-8", newline="\n")
        (ROOT / "SOURCES.md").write_text(sources, encoding="utf-8", newline="\n")
        for path in [p for *_, p in read_changelog_fragments()] + [p for *_, p in read_sources_fragments()]:
            path.unlink()
        print("fragments put into CHANGELOG.md and SOURCES.md and deleted; review `git diff`, then commit on a release/ or assemble/ branch")
    return status


if __name__ == "__main__":
    sys.exit(main())
