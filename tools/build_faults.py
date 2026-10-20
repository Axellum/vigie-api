"""Rebuild the file of named active faults the API compares earthquakes with (``app/data/gem-active-faults.geojson.gz``).

The API says, under an earthquake, "6 km from the Azuero Fault Zone (active fault)" (``near_places``, ``docs/data-sources.md``).
That needs the traces of the active faults of the world; this script builds the file it reads. Nothing is read at run time from
the source::

    python tools/build_faults.py                  # download the pinned version, write app/data/gem-active-faults.geojson.gz
    python tools/build_faults.py --cache-dir DIR  # where the download is kept (default: a temp folder)
    python tools/build_faults.py --source FILE    # build from a copy you already have (it must be the pinned file)

Input: the **GEM Global Active Faults Database**, harmonized GeoJSON (https://github.com/GEMScienceTools/gem-global-active-faults),
**CC BY-SA 4.0** (``LICENSE.txt`` of the repository, read on 2026-10-20). Citation, from its README: Styron, Richard, and Marco
Pagani. "The GEM Global Active Faults Database." Earthquake Spectra, vol. 36, no. 1_suppl, Oct. 2020, pp. 160-180,
doi:10.1177/8755293020944182. The file is pinned to one commit and one content: the tool refuses any other bytes, so the
output is the same whoever builds it, and a newer upstream version is a decision (read what changed, move the pin).

What is kept, and what is changed (the licence asks that changes are said; the same text goes into the output file and into
``app/data/gem-active-faults.NOTICE.md``):

- **Only the traces that have a name.** The label is the fault system (``fs_name``) when the database gives one, else the fault
  (``name``). A trace without a name is dropped: the mention is "N km from <name>", and a fault the database does not name is not
  named here (nothing is made up). More than half of the traces of the file have no name (7,598 of 13,696); the Mediterranean, the
  Middle East and central Asia, which come from datasets that give catalogue numbers only, are mostly unnamed (the North
  Anatolian Fault is not named in this file). A name that holds a control character of the C1 range (U+0080 to U+009F: the source's text was damaged
  when it was converted) is treated as no name, never repaired by a guess.
- **Only the geometry and the label.** Slip rates, dips, references and notes are not kept.
- **Simplified** with the Douglas-Peucker tolerance ``TOLERANCE_DEG`` (0.005 degree, about 550 m: the simplified line stays within
  that distance of the original, so a distance measured to it is at most that far off), coordinates rounded to 3 decimals
  (about 110 m) and stripped of their elevation. ``docs/data-sources.md`` has the measured effect on the distances.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import LineString, shape

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.build_geometries import DATA_DIR, download, write_collection  # noqa: E402

GEM_REPOSITORY = "https://github.com/GEMScienceTools/gem-global-active-faults"
# The last commit of the repository (2021-06-24, "readme updates"), and the git blob id of the GeoJSON file at that commit.
GEM_COMMIT = "56816508ad92fd6846dad1163b1c8c01376a2cd1"
GEM_FILE = "geojson/gem_active_faults_harmonized.geojson"
GEM_URL = f"https://raw.githubusercontent.com/GEMScienceTools/gem-global-active-faults/{GEM_COMMIT}/{GEM_FILE}"
GEM_BLOB_ID = "fb164770b529695544fa864abe2cc9dd8aa5793d"

TOLERANCE_DEG = 0.005
DECIMALS = 3
OUTPUT_NAME = "gem-active-faults.geojson.gz"

LICENCE = "CC BY-SA 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
CITATION = (
    'Styron, Richard, and Marco Pagani. "The GEM Global Active Faults Database." Earthquake Spectra, vol. 36, no. 1_suppl, '
    "Oct. 2020, pp. 160-180, doi:10.1177/8755293020944182"
)
CHANGES = (
    "Only the traces that have a name were kept (label: the fault system name, else the fault name), with their geometry only; "
    f"the lines were simplified (Douglas-Peucker, {TOLERANCE_DEG} degree), stripped of their elevation and their coordinates "
    f"rounded to {DECIMALS} decimals. "
    "Changed by the Vigie API (https://github.com/Axellum/vigie-api); not endorsed by the GEM Foundation or the authors."
)

C1_CONTROLS = re.compile("[\u0080-\u009f]")


def git_blob_id(content: bytes) -> str:
    """The id git gives a file of this content (``git hash-object``): the proof that it is the pinned file."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def clean_name(value: Any) -> Optional[str]:
    """A name as the database wrote it, white space collapsed; ``None`` when it is missing, blank or damaged (C1 control characters)."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or text.lower() == "none" or C1_CONTROLS.search(text):
        return None
    return text


def fault_label(properties: dict[str, Any]) -> Optional[str]:
    """The name to show for a trace: the fault system (``fs_name``) when there is one, else the fault (``name``)."""
    for key in ("fs_name", "name"):
        label = clean_name(properties.get(key))
        if label is not None:
            return label
    return None


def simplified_coordinates(geometry: dict[str, Any]) -> Optional[list[list[float]]]:
    """The simplified, rounded, two-dimensional coordinates of a LineString; ``None`` when nothing is left of it."""
    if geometry.get("type") != "LineString":
        return None
    line = shape(geometry).simplify(TOLERANCE_DEG, preserve_topology=False)
    if not isinstance(line, LineString) or line.is_empty:
        return None
    points = [[round(x, DECIMALS), round(y, DECIMALS)] for x, y, *_ in line.coords]
    kept = [points[0]]
    for point in points[1:]:
        if point != kept[-1]:
            kept.append(point)
    return kept if len(kept) >= 2 else None


def build_faults(source: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The features of the output file, and the counts that say what was kept."""
    features: list[dict[str, Any]] = []
    counts = {"input": 0, "unnamed": 0, "no_line": 0, "kept": 0, "input_vertices": 0, "kept_vertices": 0}
    for item in source.get("features", []):
        counts["input"] += 1
        geometry = item.get("geometry") or {}
        counts["input_vertices"] += len(geometry.get("coordinates") or [])
        label = fault_label(item.get("properties") or {})
        if label is None:
            counts["unnamed"] += 1
            continue
        coordinates = simplified_coordinates(geometry)
        if coordinates is None:
            counts["no_line"] += 1
            continue
        counts["kept"] += 1
        counts["kept_vertices"] += len(coordinates)
        features.append({"type": "Feature", "properties": {"n": label}, "geometry": {"type": "LineString", "coordinates": coordinates}})
    return features, counts


def run(argv: Optional[list[str]] = None, fetch: Callable[..., Path] = download) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the file of named active faults (GEM Global Active Faults Database).")
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "vigie-geometries")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--source", type=Path, default=None, help="a copy of the pinned GeoJSON file (nothing is downloaded)")
    args = parser.parse_args(argv)

    path: Path = args.source or fetch(GEM_URL, args.cache_dir / f"gem_active_faults_harmonized.{GEM_COMMIT[:8]}.geojson")
    content = path.read_bytes()
    if git_blob_id(content) != GEM_BLOB_ID:
        raise SystemExit(
            f"{path} is not the pinned file (git blob {git_blob_id(content)}, expected {GEM_BLOB_ID}): "
            "read what changed upstream, then move the pin (GEM_COMMIT, GEM_BLOB_ID) on purpose"
        )
    features, counts = build_faults(json.loads(content.decode("utf-8")))
    write_collection(
        args.out_dir / OUTPUT_NAME,
        features,
        "Named active fault traces from the GEM Global Active Faults Database (CC BY-SA 4.0), simplified; to say how far an earthquake is from one",
        extra={
            "license": f"{LICENCE} ({LICENCE_URL})",
            "attribution": CITATION,
            "source": f"{GEM_REPOSITORY}/blob/{GEM_COMMIT}/{GEM_FILE}",
            "changes": CHANGES,
            "counts": counts,
        },
    )
    print(
        f"input {counts['input']} traces ({counts['input_vertices']} vertices): "
        f"{counts['unnamed']} without a usable name dropped, {counts['no_line']} reduced to nothing, "
        f"{counts['kept']} kept ({counts['kept_vertices']} vertices)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
