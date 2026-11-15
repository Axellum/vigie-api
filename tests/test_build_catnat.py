"""Offline unit tests for tools/build_catnat.py."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tools.build_catnat import HAZARD_TYPES, build_bundle_payload, parse_catnat_csv, write_bundle_gz

SAMPLE_COMMUNES = [
    {
        "code": "75056",
        "nom": "Paris",
        "codeDepartement": "75",
        "centre": {"type": "Point", "coordinates": [2.347, 48.859]},
    },
    {
        "code": "01010",
        "nom": "Anglefort",
        "codeDepartement": "01",
        "centre": {"type": "Point", "coordinates": [5.805, 45.915]},
    },
    {
        "code": "97501",
        "nom": "Miquelon-Langlade",
        "codeDepartement": "975",
        "centre": {"type": "Point", "coordinates": [-56.378, 47.099]},
    },
]


def _csv_row(
    idx: int,
    insee: str,
    name: str,
    dept: str,
    label: str,
    start_ts: str,
    end_ts: str,
    decree_ts: str,
) -> str:
    cols = [
        str(idx),
        insee,
        name,
        "200054781",
        "EPCI",
        dept,
        "Dept",
        "11",
        "",
        "",
        "",
        "",
        label,
        start_ts,
        end_ts,
        decree_ts,
    ]
    return ";".join(cols)


def test_parse_headerless_csv_all_hazard_types() -> None:
    rows = [
        _csv_row(
            i + 1,
            "75056",
            "Paris",
            "75",
            item["label"],
            f"200{i}-01-01 00:00:00.000",
            f"200{i}-01-03 00:00:00.000",
            f"200{i}-02-01 00:00:00.000",
        )
        for i, item in enumerate(HAZARD_TYPES)
    ]
    # Add one row from department 01 so 01010 can also appear with 0 decrees if desired, or test 01 separately.
    events, names, depts = parse_catnat_csv("\n".join(rows))
    assert len(events["75056"]) == len(HAZARD_TYPES)
    assert names["75056"] == "Paris"
    assert depts == {"75"}


def test_build_payload_keeps_zero_decree_covered_commune_and_excludes_uncovered_com() -> None:
    rows = [
        _csv_row(1, "75056", "Paris", "75", "Inondations", "1999-12-25 00:00:00.000", "1999-12-29 00:00:00.000", "1999-12-30 00:00:00.000"),
        _csv_row(2, "75056", "Paris", "75", "Sécheresse", "2022-06-01 00:00:00.000", "2022-09-30 00:00:00.000", "2023-04-03 00:00:00.000"),
        _csv_row(3, "01001", "L'Abergement-Clémenciat", "01", "Inondations", "2010-05-01 00:00:00.000", "2010-05-02 00:00:00.000", "2010-08-01 00:00:00.000"),
    ]
    communes = [
        *SAMPLE_COMMUNES,
        {
            "code": "01001",
            "nom": "L'Abergement-Clémenciat",
            "codeDepartement": "01",
            "centre": {"type": "Point", "coordinates": [4.920, 46.151]},
        },
    ]

    payload = build_bundle_payload("\n".join(rows), communes, dataset_updated="2026-07-20")
    by_code = {c["c"]: c for c in payload["communes"]}

    assert payload["covered_departments"] == ["01", "75"]
    assert "97501" not in by_code
    assert by_code["01010"]["e"] == ""
    # Sorted descending by start date (2022 before 1999)
    assert by_code["75056"]["e"].startswith("1,2022-06-01,")
    assert payload["earliest_start_date"] == "1999-12-25"
    assert payload["latest_decree_date"] == "2023-04-03"


def test_write_bundle_gz_is_byte_deterministic(tmp_path: Path) -> None:
    csv_text = _csv_row(
        1,
        "75056",
        "Paris",
        "75",
        "Inondations",
        "2020-01-01 00:00:00.000",
        "2020-01-02 00:00:00.000",
        "2020-03-01 00:00:00.000",
    )
    payload = build_bundle_payload(csv_text, SAMPLE_COMMUNES)
    out_a = tmp_path / "a.json.gz"
    out_b = tmp_path / "b.json.gz"
    write_bundle_gz(payload, out_a)
    write_bundle_gz(payload, out_b)

    assert out_a.read_bytes() == out_b.read_bytes()
    decoded = json.load(gzip.open(out_a, "rt", encoding="utf-8"))
    assert len(decoded["communes"]) == 1


def test_parse_rejects_bad_column_count_or_unknown_label() -> None:
    with pytest.raises(ValueError, match="expected 16"):
        parse_catnat_csv("1;75056;Paris")

    bad_label = _csv_row(1, "75056", "Paris", "75", "Tremblement inconnu", "2020-01-01", "2020-01-02", "2020-03-01")
    with pytest.raises(ValueError, match="unknown GASPAR hazard label"):
        parse_catnat_csv(bad_label)
