"""Pins the embedded frame contract described in ``docs/embedded-frame.md``.

If one of these tests has to change, the contract changes: update the document in the same
commit and tell the display repository.
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

from app.models.schemas import GlobalStatus, SnapshotResponse
from app.services.embedded_frame import serialize_embedded_frame, verify_embedded_frame_crc
from tests import frame_fixture
from tests.conftest import World

DOC = Path(__file__).resolve().parents[1] / "docs" / "embedded-frame.md"

# Number of fields of each line type, tag included.
COLUMN_COUNTS = {"HEAD": 13, "ZONE": 24, "RAIN5M": 3, "DAY": 10, "HAZ": 12, "END": 2}


def _lines(frame: str) -> list[list[str]]:
    return [line.split("|") for line in frame.split("\n") if line]


def _by_tag(frame: str, tag: str) -> list[list[str]]:
    return [cols for cols in _lines(frame) if cols[0] == tag]


def test_every_line_has_its_exact_column_count() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot(), frame_fixture.example_point())
    for cols in _lines(frame):
        assert len(cols) == COLUMN_COUNTS[cols[0]], cols


def test_line_order_and_endings() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot(), frame_fixture.example_point())
    assert frame.endswith("\n") and "\r" not in frame
    tags = [cols[0] for cols in _lines(frame)]
    assert tags[0] == "HEAD" and tags[-1] == "END"
    assert tags.count("HEAD") == 1 and tags.count("END") == 1
    # The GPS zone comes first, then the watched zones; each ZONE is followed by its RAIN5M, then its DAY lines.
    zone_ids = [cols[1] for cols in _by_tag(frame, "ZONE")]
    assert zone_ids == ["GPS", "z0", "z1", "z2"]
    first_zone = tags.index("ZONE")
    assert tags[first_zone + 1] == "RAIN5M"
    assert [cols[1] for cols in _by_tag(frame, "RAIN5M")] == zone_ids
    assert tags.index("HAZ") > max(i for i, t in enumerate(tags) if t == "DAY")


def test_day_lines_carry_iso_dates() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot(), frame_fixture.example_point())
    days = _by_tag(frame, "DAY")
    assert days
    for cols in days:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cols[2]), cols  # not a weekday abbreviation
    assert _by_tag(frame, "DAY")[0][2] == "2026-10-18"


def test_day_line_columns() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    day = _by_tag(frame, "DAY")[0]
    # DAY|zone_id|date|weather_code|tmin|tmax|rain_mm|rain_probability_pct|wind_max_kmh|uv_max
    assert day == ["DAY", "z0", "2026-10-18", "61", "12.0", "19.0", "2.4", "80", "32.0", "3.5"]
    uv_per_day = [cols[9] for cols in _by_tag(frame, "DAY") if cols[1] == "z0"]
    assert uv_per_day == ["3.5", "4.0", "4.2"]  # one value per day, not the current UV repeated


def test_unknown_day_values_are_empty_fields() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    day = [cols for cols in _by_tag(frame, "DAY") if cols[1] == "z2"][0]
    # no rain probability and no UV from this provider
    assert day == ["DAY", "z2", "2026-10-20", "63", "1.0", "4.5", "3.2", "", "38.0", ""]


def test_zone_columns_with_full_weather() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    zone = [cols for cols in _by_tag(frame, "ZONE") if cols[1] == "z0"][0]
    assert zone == [
        "ZONE", "z0", "Paris, FR", "48.8566", "2.3522", "7200",
        "18.2", "17.6", "68", "1016.4", "19.5", "31.0", "245", "61", "1.7", "31",  # columns 6-15
        "15", "2.0", "0.00,0.20,0.50,0.10",                                      # columns 16-18: rain
        "", "", "", "", "",                                                      # columns 19-23: marine
    ]  # fmt: skip


def test_zone_without_weather_has_every_weather_column_empty() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    zone = [cols for cols in _by_tag(frame, "ZONE") if cols[1] == "z1"][0]
    assert zone[:6] == ["ZONE", "z1", "Tokyo, JP", "35.6762", "139.6503", "32400"]
    assert zone[6:] == [""] * 18  # temp_c ... current_kt
    rain = [cols for cols in _by_tag(frame, "RAIN5M") if cols[1] == "z1"][0]
    assert rain == ["RAIN5M", "z1", ""]
    assert not [cols for cols in _by_tag(frame, "DAY") if cols[1] == "z1"]


def test_partial_weather_leaves_only_the_unknown_columns_empty() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    zone = [cols for cols in _by_tag(frame, "ZONE") if cols[1] == "z2"][0]
    # temp, (no feels_like), humidity, pressure, wind, (no gust), wind_deg, code, (no uv), (no aqi), (no rain)
    assert zone[6:16] == ["3.5", "", "88", "998.2", "30.2", "", "200", "63", "", ""]
    assert zone[16:19] == ["", "", ""]  # the 15-minute nowcast is unknown
    rain = [cols for cols in _by_tag(frame, "RAIN5M") if cols[1] == "z2"][0]
    assert rain[2] == ""


def test_rain_columns_and_five_minute_steps() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    zone = [cols for cols in _by_tag(frame, "ZONE") if cols[1] == "z0"][0]
    assert zone[16] == "15"  # rain starts in 15 minutes (-1 would mean a dry hour)
    assert zone[17] == "2.0"  # peak mm/h
    rain = [cols for cols in _by_tag(frame, "RAIN5M") if cols[1] == "z0"][0]
    steps = rain[2].split(",")
    assert len(steps) == 12
    # each 15-minute amount (mm) becomes a rate (mm/h) repeated for its three 5-minute steps
    assert steps == ["0.00"] * 3 + ["0.80"] * 3 + ["2.00"] * 3 + ["0.40"] * 3


def test_dry_hour_is_minus_one_not_empty() -> None:
    snapshot = frame_fixture.example_snapshot()
    snapshot.zones[0].rain_next_60m = [0.0, 0.0, 0.0, 0.0]
    zone = [cols for cols in _by_tag(serialize_embedded_frame(snapshot), "ZONE") if cols[1] == "z0"][0]
    assert zone[16:19] == ["-1", "0.0", "0.00,0.00,0.00,0.00"]


def test_gps_zone_carries_marine_columns() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot(), frame_fixture.example_point())
    gps = _by_tag(frame, "ZONE")[0]
    assert gps[1:6] == ["GPS", "GPS position", "47.7500", "-3.3600", "7200"]
    # wave_m, wave_period_s, wave_dir_deg, swell_m, current_kt (3.7 km/h = 2.00 kn)
    assert gps[19:24] == ["4.20", "11.5", "275", "3.80", "2.00"]
    assert gps[15] == ""  # no air quality for the GPS position in this fixture: unknown, not zero


def test_head_columns() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    # HEAD|server_utc|solar_kp|0|version|device_type|max_severity|red|orange|yellow|gps_degraded|hf_risk|simulation
    assert _by_tag(frame, "HEAD")[0] == [
        "HEAD", str(frame_fixture.SERVER_UTC), "2.3", "0", "1.1", "waveshare-7b", "RED", "1", "1", "0", "0", "0", "0",
    ]  # fmt: skip


def test_head_unknown_values_are_empty_fields() -> None:
    snapshot = SnapshotResponse(server_utc=1792334600, global_status=GlobalStatus(), zones=[], hazards=[])
    head = _by_tag(serialize_embedded_frame(snapshot), "HEAD")[0]
    assert head == ["HEAD", "1792334600", "", "0", "1.1", "waveshare-7b", "", "", "", "", "", "", "0"]


def test_hazard_columns_and_unknown_values() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    quake, tornado = _by_tag(frame, "HAZ")
    # HAZ|id|category|severity|lat|lon|value|depth_km|tsunami_0_1|timestamp_utc|sources|title
    assert quake == [
        "HAZ", "usgs_us7000test", "EARTHQUAKE", "ORANGE", "35.1500", "25.2000", "6.5", "18.0", "1",
        "1792334500", "USGS+EMSC", "M 6.5 - Crete, Greece",
    ]  # fmt: skip
    assert tornado[6] == "" and tornado[7] == ""  # a NOAA alert has no magnitude and no depth
    assert tornado[8] == "0"


def test_separator_and_line_breaks_in_text_are_neutralised() -> None:
    snapshot = frame_fixture.example_snapshot()
    snapshot.hazards[0].title = "Evil|title\nwith\r\nbreaks"
    frame = serialize_embedded_frame(snapshot)
    quake = _by_tag(frame, "HAZ")[0]
    assert len(quake) == 12
    assert quake[11] == "Evil/title with  breaks"


def test_crc_covers_every_byte_before_the_end_line() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot(), frame_fixture.example_point())
    body, end_line = frame.rsplit("END|", 1)
    assert body.endswith("\n")
    expected = zlib.crc32(body.encode("utf-8")) & 0xFFFFFFFF
    assert end_line == f"{expected:08X}\n"
    assert re.fullmatch(r"[0-9A-F]{8}\n", end_line)
    assert verify_embedded_frame_crc(frame) is True


def test_crc_detects_tampering_and_truncation() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot())
    assert verify_embedded_frame_crc(frame.replace("18.2", "81.2", 1)) is False
    assert verify_embedded_frame_crc(frame.split("END|")[0]) is False  # END line missing
    assert verify_embedded_frame_crc("") is False


def test_size_budget_of_a_realistic_frame() -> None:
    """A GPS zone, 5 zones of 5 days and 12 hazards stay under the 8 KiB budget."""
    base = frame_fixture.example_snapshot()
    zone = base.zones[0]
    zones = [zone.model_copy(update={"id": f"z{i}", "daily_10d": zone.daily_10d + zone.daily_10d[:2]}) for i in range(5)]
    snapshot = base.model_copy(update={"zones": zones, "hazards": [base.hazards[0]] * 12})
    point = frame_fixture.example_point().model_copy(update={"nearby_hazards": [base.hazards[0]] * 12})
    frame = serialize_embedded_frame(snapshot, point)
    assert len(frame.encode("utf-8")) < 8192
    longest = max(len(line.encode("utf-8")) for line in frame.split("\n"))
    assert longest < 512  # the display decoder reads a line into a 512-byte buffer


def test_documented_example_is_the_generated_frame() -> None:
    """The example in docs/embedded-frame.md is exactly what the generator produces."""
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"<!-- example:start -->\s*```text\n(.*?)```\s*<!-- example:end -->", text, re.S)
    assert match, "docs/embedded-frame.md must hold the example between the example:start / example:end markers"
    assert match.group(1) == serialize_embedded_frame(frame_fixture.example_snapshot())


def test_documented_column_counts_match_the_contract() -> None:
    text = DOC.read_text(encoding="utf-8")
    for tag, count in COLUMN_COUNTS.items():
        assert f"`{tag}` has {count} fields" in text, f"docs/embedded-frame.md must state that `{tag}` has {count} fields"


def test_endpoint_serves_the_same_contract(world: World) -> None:
    resp = world.client.get("/v1/embedded/frame?device_type=waveshare-7b&lat=48.3904&lon=-4.4861&marine=true")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    frame = resp.text
    assert verify_embedded_frame_crc(frame) is True
    for cols in _lines(frame):
        assert len(cols) == COLUMN_COUNTS[cols[0]], cols
    assert len(_by_tag(frame, "ZONE")) == 6  # GPS + 5 watched zones
    assert len(_by_tag(frame, "RAIN5M")) == 6
    assert len(_by_tag(frame, "DAY")) == 30  # 6 zones x 5 days
    gps = _by_tag(frame, "ZONE")[0]
    assert gps[1] == "GPS" and gps[3] == "48.3904" and gps[4] == "-4.4861"
    assert gps[19] == "4.20"  # marine data from the (mocked) Marine API
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", cols[2]) for cols in _by_tag(frame, "DAY"))
    # the GPS position gets its own forecast, not the first watched zone's
    assert _by_tag(frame, "DAY")[0][1] == "GPS"
