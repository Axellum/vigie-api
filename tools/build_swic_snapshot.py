"""Write the snapshot of the WMO registry that is bundled with the code (``app/data/swic_registry.json.gz``).

The registry of the national services of the WMO Severe Weather Information Centre is read live (once a day, see
``app/collectors/swic_registry.py``); this snapshot is what an instance knows before the first read and when the WMO does not
answer, and what the tests of the countries a configuration serves read::

    python tools/build_swic_snapshot.py                  # download the registry and write the snapshot
    python tools/build_swic_snapshot.py --from FILE      # write it from a registry answer already saved

The snapshot keeps the fields this API reads (id, country, feeds, name, flags), and drops the names, e-mail addresses and the
registration details of the people in the records. The same input gives the same bytes. Rewrite it when the live registry changes
(``tests/test_swic_snapshot.py`` fails when the snapshot and the code that reads it disagree; the weekly live check says when the
registry has services the snapshot does not know).
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.collectors.swic_registry import REGISTRY_URL, SNAPSHOT_PATH, parse_registry, snapshot_payload  # noqa: E402


def write_snapshot(payload: dict, path: Path = SNAPSHOT_PATH) -> int:
    """Write the trimmed registry; returns how many services it holds."""
    parse_registry(payload)  # refuse to write something the code cannot read
    trimmed = snapshot_payload(payload)
    body = json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # No time and no file name in the gzip header: the same input gives the same bytes.
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as handle:
        handle.write(body)
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    print(f"wrote {shown}: {len(trimmed['sources'])} services, {path.stat().st_size / 1024:.1f} KiB gzipped")
    return len(trimmed["sources"])


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Write the bundled snapshot of the WMO SWIC registry.")
    parser.add_argument("--from", dest="source", type=Path, help="a registry answer saved earlier (JSON), instead of downloading it")
    args = parser.parse_args(argv)
    if args.source is not None:
        payload = json.loads(args.source.read_text(encoding="utf-8"))
    else:
        headers = {"User-Agent": "vigie-api build_swic_snapshot (personal project)"}
        response = httpx.get(REGISTRY_URL, headers=headers, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
    write_snapshot(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
