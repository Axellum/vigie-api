"""The unofficial pre-alert for Turkey (ADR 0011): reading GFS, local days, provinces, levels, and the list of episodes."""

from __future__ import annotations

import csv
import gzip
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from app.prealert.gfs import (  # noqa: E402
    Box,
    DayValues,
    Message,
    decode,
    local_day_of_interval_end,
    run_days,
    run_time,
    split_messages,
)
from app.prealert.levels import (  # noqa: E402
    MIN_HOURS_FOR_TOTALS,
    PHENOMENA,
    THRESHOLDS,
    Threshold,
    province_levels,
    province_quantities,
)
from app.prealert.provinces import PROVINCES_PATH, Province, provinces_on_grid  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gfs_nomads"
EPISODES = ROOT / "docs" / "measurements" / "prealert-tr-episodes.csv"
RUN = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)


def _fixture(lead: int) -> bytes:
    return (FIXTURES / f"gfs.t00z.20261025.f{lead:03d}.grib2").read_bytes()


# ---------------------------------------------------------------- GRIB2


def test_split_messages_counts_the_messages_of_a_nomads_answer() -> None:
    # Nine: the six fields, the precipitation twice (its two accumulation windows are the same at these leads), the categorical
    # snow twice (instantaneous and averaged), and the temperature at the surface (TMP at the level "surface", not read).
    assert len(split_messages(_fixture(3))) == 9
    assert len(split_messages(_fixture(6))) == 9


def test_split_messages_refuses_a_truncated_file_and_grib1() -> None:
    data = _fixture(3)
    with pytest.raises(ValueError, match="truncated"):
        split_messages(data[:-10])
    grib1 = bytearray(data[:16])
    grib1[7] = 1
    with pytest.raises(ValueError, match="edition 1"):
        split_messages(bytes(grib1))


def test_decode_reads_the_six_fields_on_the_turkish_grid() -> None:
    pytest.importorskip("eccodes")
    messages = decode(_fixture(6))
    names = sorted({m.short_name for m in messages})
    assert names == ["2t", "csnow", "gust", "tmax", "tmin", "tp"]
    tp = [m for m in messages if m.short_name == "tp"]
    assert {(m.step_type, m.start_h, m.end_h) for m in tp} == {("accum", 0, 6)}
    first = messages[0]
    assert first.values.shape == (29, 79)
    assert (first.lats.min(), first.lats.max(), first.lons.min(), first.lons.max()) == (35.5, 42.5, 25.5, 45.0)
    temperature = next(m for m in messages if m.short_name == "2t")
    assert 230.0 < float(np.nanmin(temperature.values)) < float(np.nanmax(temperature.values)) < 320.0  # kelvin, late October


def test_decode_cuts_to_a_box() -> None:
    pytest.importorskip("eccodes")
    messages = decode(_fixture(3), Box(south=40.0, north=41.0, west=28.0, east=30.0))
    assert messages[0].values.shape == (5, 9)
    assert messages[0].lats.min() == 40.0 and messages[0].lons.max() == 30.0


# ---------------------------------------------------------------- local days


def test_a_local_day_runs_from_21_to_21_utc() -> None:
    run = run_time(date(2026, 10, 25), 18)
    assert local_day_of_interval_end(run, 3) == date(2026, 10, 25)  # 18-21 UTC = 21:00-24:00 local
    assert local_day_of_interval_end(run, 6) == date(2026, 10, 26)  # 21-24 UTC = 00:00-03:00 local, the next day


def test_run_time_refuses_a_cycle_that_does_not_exist() -> None:
    with pytest.raises(ValueError):
        run_time(date(2026, 10, 25), 3)


def _field(name: str, step_type: str, start: int, end: int, value: float) -> Message:
    lats = np.array([40.0, 40.25])
    lons = np.array([30.0, 30.25])
    return Message(name, step_type, start, end, lats, lons, np.full((2, 2), value))


def test_run_days_differences_the_accumulation_and_keeps_extremes() -> None:
    by_lead = {
        3: [_field("tp", "accum", 0, 3, 2.0), _field("gust", "instant", 3, 3, 10.0), _field("csnow", "avg", 0, 3, 0.0)],
        6: [_field("tp", "accum", 0, 6, 5.0), _field("gust", "instant", 6, 6, 20.0), _field("csnow", "avg", 0, 6, 0.5)],
    }
    days = run_days(RUN, by_lead)
    day = days[date(2026, 10, 25)]
    assert day.hours == 6 and day.leads_h == [3, 6]
    assert float(day.rain_mm[0, 0]) == pytest.approx(5.0)
    assert float(day.gust_ms[0, 0]) == pytest.approx(20.0)
    # csnow averaged 0 over 0-3 and 0.5 over 0-6: the share of 3-6 is 1, so the 3 mm of 3-6 fell as snow.
    assert float(day.snow_mm[0, 0]) == pytest.approx(3.0)


def test_run_days_takes_the_snow_share_from_the_precipitation_type_when_there_is_no_categorical_snow() -> None:
    # ECMWF: the type at the end of the 3 hours (5 snow, 7 a mix counted half, 1 rain).
    def ptype(lead: int, code: float) -> Message:
        return _field("ptype", "instant", lead, lead, code)

    by_lead = {
        3: [_field("tp", "accum", 0, 3, 2.0), ptype(3, 5.0)],
        6: [_field("tp", "accum", 0, 6, 6.0), ptype(6, 7.0)],
        9: [_field("tp", "accum", 0, 9, 7.0), ptype(9, 1.0)],
    }
    day = run_days(RUN, by_lead)[date(2026, 10, 25)]
    assert float(day.rain_mm[0, 0]) == pytest.approx(7.0)
    assert float(day.snow_mm[0, 0]) == pytest.approx(2.0 + 0.5 * 4.0)


def test_run_days_stops_at_a_gap_and_never_guesses_an_interval() -> None:
    by_lead = {3: [_field("tp", "accum", 0, 3, 1.0)], 9: [_field("tp", "accum", 0, 9, 9.0)]}
    days = run_days(RUN, by_lead)
    assert sum(d.hours for d in days.values()) == 3


def test_run_days_leaves_out_an_extreme_that_straddles_the_end_of_the_day() -> None:
    # A run of 12 UTC: lead 9 is 21 UTC, lead 12 is 00 UTC; the TMAX of 6-12 (18-24 UTC) spans two local days.
    run = run_time(date(2026, 10, 25), 12)
    by_lead = {
        3: [_field("tp", "accum", 0, 3, 0.0), _field("tmax", "max", 0, 3, 290.0)],
        6: [_field("tp", "accum", 0, 6, 0.0), _field("tmax", "max", 0, 6, 291.0)],
        9: [_field("tp", "accum", 0, 9, 0.0), _field("tmax", "max", 6, 9, 289.0)],
        12: [_field("tp", "accum", 0, 12, 0.0), _field("tmax", "max", 6, 12, 300.0)],
    }
    days = run_days(run, by_lead)
    assert float(days[date(2026, 10, 25)].tmax_k[0, 0]) == pytest.approx(291.0)
    assert days[date(2026, 10, 26)].tmax_k is None


def test_run_days_on_the_recorded_nomads_files() -> None:
    pytest.importorskip("eccodes")
    days = run_days(RUN, {3: decode(_fixture(3)), 6: decode(_fixture(6))})
    day = days[date(2026, 10, 25)]
    assert day.hours == 6
    assert day.rain_mm.shape == (29, 79) and float(np.nanmin(day.rain_mm)) >= 0.0
    assert day.snow_mm is not None and day.gust_ms is not None and day.tmax_k is not None and day.tmin_k is not None
    assert bool(np.all(day.tmax_k >= day.tmin_k))


def test_run_days_wants_an_aware_utc_time() -> None:
    with pytest.raises(ValueError):
        run_days(datetime(2026, 10, 25), {})


# ---------------------------------------------------------------- levels


def test_threshold_levels_above_and_below() -> None:
    rain = Threshold("mm", (20.0, 50.0, 100.0))
    assert [rain.level_of(v) for v in (0.0, 20.0, 49.9, 50.0, 150.0)] == [0, 1, 1, 2, 3]
    cold = Threshold("degC", (-15.0, -20.0, -25.0), "below")
    assert [cold.level_of(v) for v in (0.0, -15.0, -21.0, -30.0)] == [0, 1, 2, 3]
    assert rain.level_of(None) is None and rain.level_of(float("nan")) is None


def test_every_phenomenon_has_a_threshold_in_order() -> None:
    for name in PHENOMENA:
        a, b, c = THRESHOLDS[name].levels
        assert (a < b < c) if THRESHOLDS[name].direction == "above" else (a > b > c)


def test_province_quantities_units_and_partial_days() -> None:
    province = Province("TR-00", "test", (0, 1), (0, 1), False)
    day = DayValues(
        date(2026, 10, 25),
        hours=24,
        rain_mm=np.array([[10.0, 0.0], [0.0, 30.0]]),
        snow_mm=np.zeros((2, 2)),
        gust_ms=np.array([[10.0, 0.0], [0.0, 25.0]]),
        tmax_k=np.full((2, 2), 273.15 + 40.0),
        tmin_k=np.full((2, 2), 273.15 - 10.0),
    )
    q = province_quantities(day, province)
    assert q["rain"] == pytest.approx(28.0)  # 90 % quantile of 10 and 30
    assert q["wind"] == pytest.approx((10.0 + 0.9 * 15.0) * 3.6)
    assert q["heat"] == pytest.approx(40.0) and q["cold"] == pytest.approx(-10.0)
    assert province_levels(q)["heat"] == THRESHOLDS["heat"].level_of(40.0)
    day.hours = MIN_HOURS_FOR_TOTALS - 3
    partial = province_quantities(day, province)
    assert partial["rain"] is None and partial["snow"] is None and partial["wind"] is not None


# ---------------------------------------------------------------- provinces and episodes


def _province_file() -> list[dict]:
    with gzip.open(PROVINCES_PATH, "rt", encoding="utf-8") as handle:
        return json.load(handle)["features"]


def test_the_province_file_holds_the_81_provinces() -> None:
    features = _province_file()
    codes = [f["properties"]["code"] for f in features]
    assert codes == [f"TR-{k:02d}" for k in range(1, 82)]
    names = {f["properties"]["code"]: f["properties"]["name"] for f in features}
    assert names["TR-34"] == "İstanbul" and names["TR-06"] == "Ankara" and names["TR-81"] == "Düzce"


def test_every_province_gets_grid_points() -> None:
    lats = np.arange(35.5, 42.5001, 0.25)
    lons = np.arange(25.5, 45.0001, 0.25)
    provinces = provinces_on_grid(lats, lons)
    assert len(provinces) == 81
    assert all(len(p.rows) == len(p.cols) >= 1 for p in provinces)
    ankara = next(p for p in provinces if p.code == "TR-06")
    assert not ankara.nearest_only and len(ankara.rows) > 20
    # The point at 40.0 N, 32.75 E is in Ankara and in no other province.
    j, i = int(np.argmin(np.abs(lats - 40.0))), int(np.argmin(np.abs(lons - 32.75)))
    holders = [p.code for p in provinces if (j, i) in zip(p.rows, p.cols, strict=True) and not p.nearest_only]
    assert holders == ["TR-06"]


def test_the_episode_list_is_consistent() -> None:
    features = {f["properties"]["code"]: f["properties"]["name"] for f in _province_file()}
    with open(EPISODES, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)) == 36
    for row in rows:
        assert date.fromisoformat(row["start"]) <= date.fromisoformat(row["end"])
        assert row["level"] in ("orange", "red")
        assert set(row["phenomena"].split(";")) <= {"rain", "wind", "snow", "heat", "cold"}
        codes, names = row["provinces"].split(";"), row["province_names"].split(";")
        assert len(codes) == len(names) == len(set(codes))
        assert [features[c] for c in codes] == names
        assert row["source"].startswith("https://")
        assert row["flag"] in ("", "disputed") or (row["flag"].startswith("revision_of:") and row["flag"].split(":")[1] in ids)
