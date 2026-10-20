"""The file of named active faults (``app/data/gem-active-faults.geojson.gz``) and the tool that builds it (``tools/build_faults.py``).

The tool downloads the GEM Global Active Faults Database (10.6 MB); here the download is replaced by small inputs the test
writes, so the tests check the *logic* (which traces are kept, how a name is chosen, the simplification, the refusal of any file but
the pinned one) without the network. The bundled file itself is checked for what it says (licence, source, changes) and for what
it holds.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import LineString, shape

from app.services.geoindex import DATA_DIR
from app.services.places import FAULTS_FILE, FaultIndex
from tools import build_faults as tool
from tools.build_geometries import write_collection

DATA_FILE = DATA_DIR / FAULTS_FILE


def trace(coordinates: list[list[float]], **properties: Any) -> dict[str, Any]:
    return {"type": "Feature", "properties": properties, "geometry": {"type": "LineString", "coordinates": coordinates}}


# ---------------------------------------------------------------- the name of a trace


def test_the_label_is_the_fault_system_when_there_is_one_else_the_fault() -> None:
    assert tool.fault_label({"fs_name": "Denali fault", "name": "Susitna section"}) == "Denali fault"
    assert tool.fault_label({"fs_name": "", "name": "Azuero Fault Zone"}) == "Azuero Fault Zone"
    assert tool.fault_label({"name": "Mendocino"}) == "Mendocino"
    assert tool.fault_label({"fs_name": None, "name": None}) is None
    assert tool.fault_label({}) is None


def test_white_space_is_collapsed_and_nothing_else_is_changed() -> None:
    assert tool.clean_name("  Main   Pamir\tThrust ") == "Main Pamir Thrust"
    assert tool.clean_name("ROMERAL_FAULT") == "ROMERAL_FAULT"  # the database's own spelling, underscore and capitals included
    assert tool.clean_name("Río Guárico - Norte") == "Río Guárico - Norte"


@pytest.mark.parametrize("value", [None, 12, "", "   ", "None", "none", "Ca\x96asgordas", "Su\x87arez", "Murr\x92"])
def test_a_missing_blank_or_damaged_name_is_no_name(value: Any) -> None:
    """The names with a control character of the C1 range (U+0080 to U+009F) are text damaged by a conversion: not repaired by a guess."""
    assert tool.clean_name(value) is None


def test_a_damaged_fault_system_name_falls_back_to_the_fault_name() -> None:
    assert tool.fault_label({"fs_name": "Zuluaga-Garz\x97n", "name": "Zuluaga"}) == "Zuluaga"


# ---------------------------------------------------------------- the build


def test_only_named_traces_are_kept_with_their_geometry_and_their_label() -> None:
    source = {
        "type": "FeatureCollection",
        "features": [
            trace([[-80.0, 8.0], [-80.0, 8.5]], name="Azuero Fault Zone", slip_type="Sinistral-Normal", net_slip_rate="(1.0,0.5,2.0)"),
            trace([[10.0, 40.0], [10.1, 40.1]], catalog_id="ME_TRCS003"),  # no name: the Mediterranean and the Middle East
            trace([[20.0, 30.0], [20.2, 30.1]], fs_name="Denali fault", name="Susitna section"),
        ],
    }
    features, counts = tool.build_faults(source)
    assert [f["properties"] for f in features] == [{"n": "Azuero Fault Zone"}, {"n": "Denali fault"}]
    assert counts == {"input": 3, "unnamed": 1, "no_line": 0, "kept": 2, "input_vertices": 6, "kept_vertices": 4}


def test_a_trace_is_simplified_within_the_tolerance_and_its_elevation_dropped() -> None:
    zigzag = [[-80.0 + 0.0004 * i, 8.0 + 0.1 * i + (0.0003 if i % 2 else 0.0)] for i in range(40)]  # wiggles under the tolerance
    with_z = [[x, y, 120.0] for x, y in zigzag]
    [feature], _ = tool.build_faults({"features": [trace(with_z, name="Wiggly fault")]})
    kept = feature["geometry"]["coordinates"]
    assert len(kept) < len(zigzag)
    assert all(len(vertex) == 2 for vertex in kept)  # no elevation
    assert all(round(c, 3) == c for vertex in kept for c in vertex)  # rounded to 3 decimals
    # the simplified line stays within the tolerance (plus the rounding) of the original, in both directions
    original, simplified = LineString(zigzag), LineString(kept)
    assert original.hausdorff_distance(simplified) <= tool.TOLERANCE_DEG + 0.001


def test_a_trace_reduced_to_a_point_by_the_rounding_is_dropped_and_counted() -> None:
    tiny = trace([[5.00001, 5.00001], [5.00002, 5.00002]], name="Tiny")
    features, counts = tool.build_faults({"features": [tiny]})
    assert features == [] and counts["no_line"] == 1


def test_other_geometries_are_not_traces() -> None:
    odd = {"type": "Feature", "properties": {"name": "A point"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}
    features, counts = tool.build_faults({"features": [odd]})
    assert features == [] and counts["no_line"] == 1


def test_the_same_input_gives_the_same_bytes(tmp_path: Path) -> None:
    features, _ = tool.build_faults({"features": [trace([[-80.0, 8.0], [-80.0, 8.5]], name="Azuero Fault Zone")]})
    first, second = tmp_path / "a" / "faults.geojson.gz", tmp_path / "b" / "other-name.geojson.gz"
    write_collection(first, features, "note", extra={"license": tool.LICENCE})
    write_collection(second, features, "note", extra={"license": tool.LICENCE})
    assert first.read_bytes() == second.read_bytes()


# ---------------------------------------------------------------- the pinned file


def test_the_blob_id_is_the_one_git_gives() -> None:
    assert tool.git_blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"  # git hash-object
    assert tool.git_blob_id(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_the_tool_refuses_any_file_but_the_pinned_one(tmp_path: Path) -> None:
    other = tmp_path / "other.geojson"
    other.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="not the pinned file"):
        tool.run(["--source", str(other), "--out-dir", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()  # nothing was written


def test_the_pin_names_one_commit_and_one_file() -> None:
    assert len(tool.GEM_COMMIT) == 40 and tool.GEM_COMMIT in tool.GEM_URL
    assert len(tool.GEM_BLOB_ID) == 40


# ---------------------------------------------------------------- the bundled file


def read_collection() -> dict[str, Any]:
    with gzip.open(DATA_FILE, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def test_the_bundled_file_says_its_licence_its_source_and_what_was_changed() -> None:
    """CC BY-SA 4.0 asks for the credit, the licence, a link to the material and a statement of the changes: all in the file."""
    collection = read_collection()
    assert "CC BY-SA 4.0" in collection["license"] and "creativecommons.org/licenses/by-sa/4.0" in collection["license"]
    assert "Styron" in collection["attribution"] and "Pagani" in collection["attribution"]
    assert "doi:10.1177/8755293020944182" in collection["attribution"]
    assert collection["source"].startswith("https://github.com/GEMScienceTools/gem-global-active-faults/blob/")
    assert tool.GEM_COMMIT in collection["source"]
    assert "simplified" in collection["changes"] and "have a name" in collection["changes"] and "not endorsed" in collection["changes"]


def test_the_counts_of_the_file_are_the_ones_of_its_content() -> None:
    collection = read_collection()
    counts = collection["counts"]
    assert counts["kept"] == len(collection["features"])
    assert counts["input"] == counts["unnamed"] + counts["no_line"] + counts["kept"]
    assert counts["kept_vertices"] == sum(len(f["geometry"]["coordinates"]) for f in collection["features"])
    assert counts["input"] == 13696  # the file at the pinned commit


def test_every_trace_of_the_file_has_a_name_and_lies_on_the_globe() -> None:
    for item in read_collection()["features"]:
        name = item["properties"]["n"]
        assert name == name.strip() and "  " not in name and not tool.C1_CONTROLS.search(name)
        coordinates = item["geometry"]["coordinates"]
        assert len(coordinates) >= 2 and shape(item["geometry"]).is_valid
        assert all(len(vertex) == 2 and -180.0 <= vertex[0] <= 180.0 and -90.0 <= vertex[1] <= 90.0 for vertex in coordinates)


def test_the_file_is_small_and_loads_fast() -> None:
    assert DATA_FILE.stat().st_size < 300 * 1024
    started = time.perf_counter()
    index = FaultIndex.from_file()
    assert time.perf_counter() - started < 2.0  # 0.03 s measured; the margin is for a slow runner
    assert len(index) == read_collection()["counts"]["kept"]
    assert index.info["license"].startswith("CC BY-SA 4.0")


def test_the_licence_notice_and_the_readme_of_the_data_are_next_to_it() -> None:
    notice = (DATA_DIR / "gem-active-faults.NOTICE.md").read_text(encoding="utf-8")
    assert "CC BY-SA 4.0" in notice and "ShareAlike" in notice and "MIT" in notice
    assert "Styron" in notice and tool.GEM_COMMIT in notice
    readme = (DATA_DIR / "README.md").read_text(encoding="utf-8")
    for path in DATA_DIR.iterdir():
        if path.is_file() and path.name != "README.md":
            assert path.name in readme, f"app/data/README.md must say what {path.name} is and under which licence"
