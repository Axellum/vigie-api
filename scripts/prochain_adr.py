"""The next free ADR number, looked up where two sessions would both look: ``main``, the open pull requests, the reserved ones.

Each parallel session used to take "the next free number" of its own checkout: four ADRs were merged with two numbers on
2026-10-28 and renumbered by hand. This reads the numbers that exist on ``origin/main`` and in every open pull request, and
those somebody reserved (``--also 17 --also 18``), then prints the next one and who holds the others.

    python scripts/prochain_adr.py                  # needs git and an authenticated gh
    python scripts/prochain_adr.py --also 17        # 17 is reserved elsewhere (the coordination notes)

Exit 0: the number is safe against main and the open pull requests. Exit 3: the pull requests could not be read (gh missing or
offline): the number printed is only a lower bound, do not use it. Reserve the number (in the coordination notes) before writing
the file, and give the file this number from the start: a pull request that renumbers later breaks the references to it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = "docs/decisions"
ADR_FILE = re.compile(rf"^{re.escape(DECISIONS)}/(\d{{4}})-[^/]+\.md$")


def adr_numbers(paths: list[str]) -> list[int]:
    """The numbers of the ADR files among ``paths`` (``docs/decisions/0016-slug.md`` gives 16)."""
    return [int(match.group(1)) for path in paths if (match := ADR_FILE.match(path.replace("\\", "/")))]


def next_number(taken: set[int]) -> int:
    return max(taken, default=0) + 1


def _run(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--also", type=int, action="append", default=[], metavar="N", help="a number reserved elsewhere")
    parser.add_argument("--base", default="origin/main", help="the branch whose ADRs count (default: origin/main)")
    args = parser.parse_args(argv)

    holders: dict[int, list[str]] = {}

    def hold(number: int, who: str) -> None:
        holders.setdefault(number, []).append(who)

    complete = True
    try:
        _run("git", "fetch", "-q", "origin")
        for number in adr_numbers(_run("git", "ls-tree", "-r", "--name-only", args.base, DECISIONS).split("\n")):
            hold(number, args.base)
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        print(f"cannot read {args.base}: {error}", file=sys.stderr)
        complete = False
    for number in adr_numbers([str(p.relative_to(ROOT)) for p in (ROOT / DECISIONS).glob("*.md")]):
        hold(number, "this checkout")
    try:
        prs = json.loads(_run("gh", "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,files"))
        for pr in prs:
            for number in adr_numbers([entry["path"] for entry in pr["files"]]):
                hold(number, f"open PR #{pr['number']} ({pr['headRefName']})")
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as error:
        print(f"cannot read the open pull requests: {error}", file=sys.stderr)
        complete = False
    for number in args.also:
        hold(number, "reserved (--also)")

    taken = set(holders)
    duplicated = {n: who for n, who in holders.items() if len([w for w in who if w != "this checkout"]) > 1}
    for number in sorted(duplicated):
        print(f"WARNING: ADR {number:04d} is held twice: {', '.join(duplicated[number])}", file=sys.stderr)
    top = sorted(taken)[-3:]
    print("last numbers taken: " + "; ".join(f"{n:04d} = {', '.join(holders[n])}" for n in top))
    print(f"next free number: {next_number(taken):04d}")
    if not complete:
        print("NOT SAFE: the list above is incomplete (see the messages above)", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
