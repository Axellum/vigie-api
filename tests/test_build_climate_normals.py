"""Offline tests of tools/build_climate_normals.py: the reading of the climate sheets of Météo-France."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tools.build_climate_normals import SheetError, build_payload, parse_sheet, write_bundle_gz

FIXTURES = Path(__file__).parent / "fixtures" / "climate"


def _sheet(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_the_station_line_gives_the_name_altitude_and_coordinates() -> None:
    station = parse_sheet(_sheet("FICHECLIM_34107006.data"))
    assert station["id"] == "34107006"
    assert station["name"] == "MURAT SUR VEBRE (34)"
    assert station["alt"] == 1022
    assert station["lat"] == pytest.approx(43 + 37 / 60 + 59 / 3600, abs=1e-4)
    assert station["lon"] == pytest.approx(2 + 49 / 60 + 31 / 3600, abs=1e-4)
    assert station["edited"] == "2026-11-06"
    assert station["period"] == ["1991", "2020"]


def test_southern_and_western_coordinates_are_negative() -> None:
    station = parse_sheet(_sheet("FICHECLIM_98715002.data"))
    assert station["lat"] < 0 and station["lon"] < 0
    assert station["lat"] == pytest.approx(-(17 + 33 / 60 + 18 / 3600), abs=1e-4)


def test_a_record_keeps_its_values_its_days_and_its_period() -> None:
    blocks = parse_sheet(_sheet("FICHECLIM_34107006.data"))["blocks"]
    record = blocks["tmax_record"]
    assert record["p"] == ["2002-06-01", "2026-11-02"]
    assert record["v"][7] == 35.2 and record["d"][7] == "2023-08-23"
    # January "01-2022" is the 1st of January 2022; the year column holds the year alone.
    assert record["d"][0] == "2022-01-01"
    assert record["v"][12] == 35.2 and record["d"][12] == "2023"
    assert blocks["tmin_record"]["v"][1] == -16.0 and blocks["tmin_record"]["d"][1] == "2012-02-08"


def test_a_normal_keeps_the_period_its_block_names() -> None:
    blocks = parse_sheet(_sheet("FICHECLIM_34107006.data"))["blocks"]
    assert blocks["tmax_mean"] == {
        "p": ["2002", "2020"],
        "v": [4.4, 4.5, 7.8, 11.5, 14.9, 19.7, 21.8, 21.8, 17.9, 13.3, 8.1, 5.4, 12.6],
    }
    assert blocks["rain_mean"]["v"][9] == 217.0


def test_a_dot_is_zero_and_the_counts_of_days_are_read_by_their_label() -> None:
    blocks = parse_sheet(_sheet("FICHECLIM_34107006.data"))["blocks"]
    assert blocks["hot_days"]["v"][:5] == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert blocks["hot_days"]["v"][7] == 1.1
    assert blocks["frost_days"]["v"][0] == 16.2
    assert blocks["rain_days"]["v"][12] == 112.7


def test_a_block_with_no_data_never_takes_the_rows_of_the_next_block() -> None:
    """The sunshine block says "Données non disponibles": the ETP row after it is not sunshine."""
    blocks = parse_sheet(_sheet("FICHECLIM_34107006.data"))["blocks"]
    assert "sunshine_mean" not in blocks
    faaa = parse_sheet(_sheet("FICHECLIM_98715002.data"))["blocks"]
    assert "frost_days" not in faaa and "sunshine_mean" in faaa


def test_a_missing_value_stays_missing() -> None:
    text = _sheet("FICHECLIM_34107006.data").replace(
        "            ;       4.4;       4.5;", "            ;         -;       4.5;", 1
    )
    assert parse_sheet(text)["blocks"]["tmax_mean"]["v"][0] is None


def test_windows_and_unix_line_ends_read_the_same() -> None:
    text = _sheet("FICHECLIM_34107006.data").replace("\r\n", "\n")
    assert parse_sheet(text) == parse_sheet(text.replace("\n", "\r\n"))


def test_a_sheet_without_a_station_line_is_refused() -> None:
    with pytest.raises(SheetError):
        parse_sheet("FICHE CLIMATOLOGIQUE;\n;\n")


def test_the_bundle_is_sorted_dated_and_reproducible(tmp_path: Path) -> None:
    stations = [parse_sheet(_sheet("FICHECLIM_98715002.data")), parse_sheet(_sheet("FICHECLIM_34107006.data"))]
    payload = build_payload(stations, built="2026-11-17")
    assert [s["id"] for s in payload["stations"]] == ["34107006", "98715002"]
    assert payload["dataset_updated"] == "2026-11-06"
    first, second = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    write_bundle_gz(payload, first)
    write_bundle_gz(payload, second)
    assert first.read_bytes() == second.read_bytes()
    with gzip.open(first, "rt", encoding="utf-8") as fh:
        assert json.load(fh) == json.loads(json.dumps(payload))
