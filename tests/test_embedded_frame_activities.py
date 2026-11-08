"""Pins the ``SEA``, ``ACT`` and ``CRIT`` lines of the embedded frame (``docs/embedded-frame.md``, "Activities of a surf spot").

If one of these tests has to change, the contract changes: update the document in the same commit and tell the display repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app import clock
from app.collectors.surf import SurfForecastCollector, parse_swell, parse_wind
from app.models.activities import ActivitiesForecast, ActivityAlert, ActivityTide, WindRange
from app.models.tides import TideCoefficient, TideExtreme
from app.services import activities, surf
from app.services.embedded_frame import (
    ACTIVITY_HOURS,
    MAX_COEFFICIENTS,
    MAX_TIDE_EXTREMES,
    TIDE_AFTER_S,
    activities_lines,
    serialize_embedded_frame,
    verify_embedded_frame_crc,
)
from tests import frame_fixture
from tests.conftest import World
from tests.test_activities import (
    HOUR,
    NOW,
    NOW_HOUR,
    SPOT_FILE,
    SPOT_ID,
    SWELL,
    WIND,
    alert,
    calm_build,
    hossegor,
    make_hour,
    provider,
    response,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "embedded-frame.md"
COLUMN_COUNTS = {"SEA": 18, "ACT": 12, "CRIT": 9}
ORDER = ["surf", "kite", "boat", "shore_fishing"]


@pytest.fixture(autouse=True)
def spots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)


def orange() -> ActivityAlert:
    return alert("ORANGE", onset=NOW_HOUR - HOUR, ends=NOW_HOUR + 8 * HOUR)


def example() -> ActivitiesForecast:
    """The recorded swell and wind of Hossegor, one day, and an orange coastal warning: what the documented example is made of."""
    return hossegor([orange()], days=1)[1]


def split(lines: list[str]) -> list[list[str]]:
    return [line.split("|") for line in lines]


def block(forecast: ActivitiesForecast) -> list[list[str]]:
    return split(activities_lines(forecast))


def activity_line(forecast: ActivitiesForecast, name: str) -> list[str]:
    return next(cols for cols in block(forecast) if cols[0] == "ACT" and cols[1] == name)


def sea_line(forecast: ActivitiesForecast) -> list[str]:
    return block(forecast)[0]


# ------------------------------------------------------------------------------------------------------------- the lines


def test_every_line_of_the_block_has_its_exact_column_count() -> None:
    for forecast in (example(), hossegor(with_tide=False, known=False)[1], calm_build([orange()])):
        for cols in block(forecast):
            assert len(cols) == COLUMN_COUNTS[cols[0]], cols


def test_the_block_is_the_sea_line_then_each_activity_followed_by_its_criteria() -> None:
    lines = block(example())
    assert lines[0][0] == "SEA"
    assert [cols[1] for cols in lines if cols[0] == "ACT"] == ORDER
    owner = None
    for cols in lines[1:]:
        if cols[0] == "ACT":
            owner = cols[1]
        else:
            assert cols[0] == "CRIT" and cols[1] == owner, cols


def test_the_sea_line_says_the_spot_the_time_frame_the_sun_and_the_tide() -> None:
    forecast = example()
    sea = sea_line(forecast)
    # SEA|name|place|generated|first_hour|stale|wind_min|wind_max|sunrise|sunset|sunrise2|sunset2|extremes|coefficients|status|level|until|source
    assert sea[1:8] == ["Plage Centrale", "Hossegor", str(NOW), str(NOW_HOUR), "0", "12.0", "25.0"]
    assert sea[8:12] == [str(t) for day in forecast.sun[:2] for t in (day.sunrise_utc, day.sunset_utc)]
    assert forecast.tide is not None
    wanted = [e for e in forecast.tide.extremes if e.time_utc <= NOW_HOUR + TIDE_AFTER_S]
    assert sea[12].split(",") == [f"{'H' if e.kind == 'high' else 'L'}:{e.time_utc}:{e.height_m:.2f}" for e in wanted]
    assert sea[13].split(",") == [f"{c.brest_high_water_utc}:{c.value}" for c in forecast.tide.coefficients_estimated if c.brest_high_water_utc <= NOW_HOUR + TIDE_AFTER_S]
    assert sea[14:18] == ["ok", "ORANGE", str(NOW_HOUR + 8 * HOUR), "meteoalarm"]


def test_the_tide_is_sent_from_the_hours_before_to_thirty_hours_after_and_never_more_than_the_caps() -> None:
    forecast = example()
    assert forecast.tide is not None
    far = TideExtreme(time_utc=NOW_HOUR + TIDE_AFTER_S + HOUR, kind="high", height_m=1.0)
    many = [TideExtreme(time_utc=NOW_HOUR + i * HOUR, kind="low" if i % 2 else "high", height_m=float(i)) for i in range(12)]
    coefficients = [TideCoefficient(brest_high_water_utc=NOW_HOUR + i * HOUR, value=90 + i) for i in range(6)]
    wide = forecast.model_copy(update={"tide": ActivityTide(extremes=[*many, far], coefficients_estimated=coefficients, note="x")})
    sea = sea_line(wide)
    assert (MAX_TIDE_EXTREMES, MAX_COEFFICIENTS) == (8, 4)  # what the document says: at most 8 high and low waters and 4 coefficients
    assert len(sea[12].split(",")) == 8 and sea[12].startswith(f"H:{NOW_HOUR}:0.00,L:{NOW_HOUR + HOUR}:1.00")
    assert len(sea[13].split(",")) == 4
    only_far = forecast.model_copy(update={"tide": ActivityTide(extremes=[far], coefficients_estimated=[], note="x")})
    assert sea_line(only_far)[12:14] == ["", ""]  # nothing within the window: empty fields


def test_the_activity_lines_follow_the_model() -> None:
    forecast = example()
    for activity in forecast.activities:
        cols = activity_line(forecast, activity.id)
        slot, detail = activity.best_slot, activity.detail
        assert cols[2] == "1"
        assert cols[3] == (activity.verdict or "") and cols[4] == (activity.reason or "")
        assert cols[5:7] == (["", ""] if slot is None else [str(slot.start_utc), str(slot.end_utc)])
        assert cols[7] == "".join({"good": "G", "possible": "P", "poor": "X", None: "-"}[h.verdict] for h in activity.hours[:ACTIVITY_HOURS])
        assert cols[8] == "".join("-" if h.daylight is None else "1" if h.daylight else "0" for h in activity.hours[:ACTIVITY_HOURS])
        assert len(cols[7]) == len(cols[8]) == ACTIVITY_HOURS
        assert cols[9] == ("" if detail is None else str(detail.time_utc))
        assert cols[10:12] == (["", ""] if detail is None or detail.points is None else [str(detail.points), str(detail.out_of)])


def test_the_criteria_of_an_activity_are_those_of_its_detail_hour_in_order() -> None:
    forecast = example()
    lines = block(forecast)
    for activity in forecast.activities:
        assert activity.detail is not None, activity.id  # the recorded day gives every activity an hour to detail
        sent = [cols for cols in lines if cols[0] == "CRIT" and cols[1] == activity.id]
        assert [cols[2:] for cols in sent] == [
            [
                c.name,
                "" if c.value is None else f"{c.value:.1f}",
                c.unit,
                c.state or "",
                c.verdict or "",
                "" if c.points is None else str(c.points),
                "" if c.max_points is None else str(c.max_points),
            ]
            for c in activity.detail.criteria
        ]


def test_the_surf_criteria_carry_their_points() -> None:
    sent = [cols for cols in block(example()) if cols[0] == "CRIT" and cols[1] == "surf"]
    assert [cols[2] for cols in sent] == ["swell_height", "swell_period", "swell_direction", "wind", "tide"]
    assert all(cols[8] == "2" for cols in sent)  # every surf criterion is worth 2 points at most
    tide = sent[-1]
    assert tide[3] != "" and tide[5:8] == ["", "", ""]  # a value but no state, verdict nor points: Vigie does not know the best stage
    assert activity_line(example(), "surf")[10:12] == ["6", "8"]


def test_the_orange_warning_is_the_reason_where_it_caps_a_good_verdict() -> None:
    plain = calm_build([])
    capped = calm_build([alert("ORANGE", onset=NOW_HOUR - 100 * HOUR, ends=NOW_HOUR + 100 * HOUR)])
    assert activity_line(plain, "shore_fishing")[3:5] == ["good", ""]
    assert activity_line(capped, "shore_fishing")[3:5] == ["possible", "vigilance.orange"]
    assert sea_line(capped)[14:16] == ["ok", "ORANGE"]


# ------------------------------------------------------------------------------------------------------------ what is unknown


def test_an_activity_that_cannot_be_judged_here_is_a_line_with_nothing_else() -> None:
    forecast = hossegor(with_tide=False)[1]
    assert activity_line(forecast, "shore_fishing") == ["ACT", "shore_fishing", "0", "", "", "", "", "", "", "", "", ""]
    assert not [cols for cols in block(forecast) if cols[0] == "CRIT" and cols[1] == "shore_fishing"]
    assert sea_line(forecast)[12:14] == ["", ""]  # no tide model near: no tide, not an empty sea


def test_without_the_wind_every_wind_dependent_field_is_unknown_not_zero() -> None:
    spot = surf.find_spot(SPOT_ID)
    base = surf.build_forecast(spot, parse_swell(SWELL), None, 1, NOW)
    forecast = activities.build(base, None, [], True, [], WindRange(min_kn=12.0, max_kn=25.0), NOW)
    sea = sea_line(forecast)
    assert sea[8:14] == [""] * 6
    for name in ("kite", "boat"):
        assert activity_line(forecast, name)[3:] == ["", "", "", "", "-" * 24, "-" * 24, "", "", ""]
    assert not [cols for cols in block(forecast) if cols[0] == "CRIT" and cols[1] in ("kite", "boat")]


def test_warnings_that_could_not_be_read_are_unknown_and_not_an_empty_all_clear() -> None:
    unknown = sea_line(hossegor(known=False, days=1)[1])
    clear = sea_line(hossegor(known=True, days=1)[1])
    assert unknown[14:18] == ["unknown", "", "", ""]
    assert clear[14:18] == ["ok", "", "", ""]


def test_a_stale_answer_is_flagged() -> None:
    forecast = example().model_copy(update={"stale": True})
    assert sea_line(forecast)[5] == "1"


def test_a_missing_hour_is_a_dash_in_both_strips() -> None:
    forecast = example()
    surf_activity = forecast.activities[0]
    gappy = surf_activity.model_copy(update={"hours": surf_activity.hours[:3] + surf_activity.hours[4:]})
    cols = activity_line(forecast.model_copy(update={"activities": [gappy, *forecast.activities[1:]]}), "surf")
    original = activity_line(forecast, "surf")
    assert cols[7][3] == "-" and cols[8][3] == "-"  # no hour at +3 h: a dash, and the other hours keep their place
    assert cols[7][:3] + cols[7][4:] == original[7][:3] + original[7][4:]
    assert cols[8][:3] + cols[8][4:] == original[8][:3] + original[8][4:]


def test_the_text_fields_hold_no_separator_nor_line_break() -> None:
    forecast = example()
    spot = forecast.spot.model_copy(update={"name": "Evil|name\nwith\r\nbreaks", "place": "P" * 60})
    sea = sea_line(forecast.model_copy(update={"spot": spot}))
    assert sea[1] == "Evil/name with  breaks" and sea[2] == "P" * 32


# ------------------------------------------------------------------------------------------ the strongest coastal warning


def warning(*alerts: ActivityAlert, known: bool = True) -> list[str]:
    return sea_line(hossegor(list(alerts), known=known, days=1)[1])[14:18]


def test_the_strongest_warning_of_the_next_24_hours_gives_the_level_the_end_and_the_source() -> None:
    yellow = alert("YELLOW", onset=NOW_HOUR, ends=NOW_HOUR + 20 * HOUR)
    orange_one = alert("ORANGE", onset=NOW_HOUR, ends=NOW_HOUR + 8 * HOUR)
    red_early = alert("RED", onset=NOW_HOUR + 2 * HOUR, ends=NOW_HOUR + 5 * HOUR)
    red_late = alert("RED", onset=NOW_HOUR + 3 * HOUR, ends=NOW_HOUR + 9 * HOUR)
    assert warning(yellow) == ["ok", "YELLOW", str(NOW_HOUR + 20 * HOUR), "meteoalarm"]
    assert warning(yellow, orange_one) == ["ok", "ORANGE", str(NOW_HOUR + 8 * HOUR), "meteoalarm"]
    assert warning(yellow, orange_one, red_early, red_late) == ["ok", "RED", str(NOW_HOUR + 9 * HOUR), "meteoalarm"]  # the latest end at that level


def test_a_warning_without_an_end_leaves_the_end_empty_and_the_expiry_stands_in_for_it() -> None:
    open_ended = alert("RED", onset=NOW_HOUR)
    ended = alert("RED", onset=NOW_HOUR, ends=NOW_HOUR + 4 * HOUR)
    expiring = alert("RED", onset=NOW_HOUR, expires=NOW_HOUR + 6 * HOUR)
    assert warning(open_ended)[1:3] == ["RED", ""]
    assert warning(open_ended, ended)[1:3] == ["RED", ""]  # one of them has no end: the display cannot say "until"
    assert warning(ended, expiring)[1:3] == ["RED", str(NOW_HOUR + 6 * HOUR)]


def test_only_the_warnings_that_stand_in_the_next_24_hours_and_have_a_known_level_count() -> None:
    over = alert("RED", onset=NOW_HOUR - 10 * HOUR, ends=NOW_HOUR)  # ended as the first hour starts
    later = alert("RED", onset=NOW_HOUR + 24 * HOUR, ends=NOW_HOUR + 30 * HOUR)
    unknown_level = alert("VIOLET", onset=NOW_HOUR, ends=NOW_HOUR + 5 * HOUR)
    no_level = alert(None, onset=NOW_HOUR, ends=NOW_HOUR + 5 * HOUR)
    assert warning(over, later, unknown_level, no_level) == ["ok", "", "", ""]
    last_hour = alert("ORANGE", onset=NOW_HOUR + 23 * HOUR, ends=NOW_HOUR + 40 * HOUR)
    assert warning(over, later, last_hour)[1] == "ORANGE"


def test_warnings_that_could_not_be_read_give_no_level() -> None:
    assert warning(orange(), known=False) == ["unknown", "", "", ""]  # the status wins: nothing is read from warnings that were not


# ------------------------------------------------------------------------------------------------------------- size


def worst_sea() -> ActivitiesForecast:
    forecast = example()
    spot = forecast.spot.model_copy(update={"name": "N" * 40, "place": "P" * 40})
    extremes = [TideExtreme(time_utc=NOW_HOUR + i * 3 * HOUR, kind="high" if i % 2 else "low", height_m=-12.34) for i in range(12)]
    coefficients = [TideCoefficient(brest_high_water_utc=NOW_HOUR + i * 5 * HOUR, value=120) for i in range(6)]
    tide = ActivityTide(extremes=extremes, coefficients_estimated=coefficients, note="x")
    return forecast.model_copy(update={"spot": spot, "tide": tide})


def test_no_line_of_the_block_reaches_the_512_bytes_the_display_reads_a_line_into() -> None:
    for forecast in (example(), worst_sea(), calm_build([orange()])):
        assert max(len(line.encode("utf-8")) for line in activities_lines(forecast)) < 512
    assert len("|".join(sea_line(worst_sea())).encode("utf-8")) < 480  # the SEA line is the one that grows: it keeps a margin of its own


def test_the_block_stays_under_2_kib_and_the_whole_frame_under_8_kib() -> None:
    assert len("\n".join(activities_lines(example())).encode("utf-8")) < 2048
    base = frame_fixture.example_snapshot()
    zone = base.zones[0]
    zones = [zone.model_copy(update={"id": f"z{i}", "daily_10d": zone.daily_10d + zone.daily_10d[:2]}) for i in range(5)]
    snapshot = base.model_copy(update={"zones": zones, "hazards": [base.hazards[0]] * 12})
    point = frame_fixture.example_point().model_copy(update={"nearby_hazards": [base.hazards[0]] * 12})
    frame = serialize_embedded_frame(snapshot, point, worst_sea())
    assert len(frame.encode("utf-8")) < 8192
    assert verify_embedded_frame_crc(frame)


def test_the_block_goes_after_the_hazards_and_before_the_end_and_the_crc_covers_it() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot(), frame_fixture.example_point(), example())
    tags = [line.split("|")[0] for line in frame.split("\n") if line]
    assert tags[0] == "HEAD" and tags[-1] == "END"
    assert tags.index("SEA") > max(i for i, t in enumerate(tags) if t == "HAZ")
    assert tags.index("SEA") == min(i for i, t in enumerate(tags) if t in ("SEA", "ACT", "CRIT"))
    assert verify_embedded_frame_crc(frame) is True
    assert verify_embedded_frame_crc(frame.replace("Plage Centrale", "Plage Centrale!", 1)) is False


def test_without_activities_the_frame_is_what_it_was() -> None:
    frame = serialize_embedded_frame(frame_fixture.example_snapshot(), frame_fixture.example_point())
    assert not re.search(r"^(SEA|ACT|CRIT)\|", frame, re.M)


# ------------------------------------------------------------------------------------------ the document says what the code sends


def documented(section_title: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(section_title)
    return text[start:]


def test_the_documented_example_is_the_generated_block() -> None:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"<!-- example-activities:start -->\s*```text\n(.*?)```\s*<!-- example-activities:end -->", text, re.S)
    assert match, "docs/embedded-frame.md must hold the example between the example-activities:start / end markers"
    assert match.group(1) == "\n".join(activities_lines(example())) + "\n"


def test_the_documented_column_counts_match_the_contract() -> None:
    text = DOC.read_text(encoding="utf-8")
    for tag, count in COLUMN_COUNTS.items():
        assert f"`{tag}` has {count} fields" in text, f"docs/embedded-frame.md must state that `{tag}` has {count} fields"


def documented_vocabulary() -> dict[tuple[str, str], tuple[str, set[str]]]:
    rows = re.findall(r"^\| `(\w+)` \| `(\w+)` \| ([^|]+?) \| ((?:`\w+`(?:, )?)+) \|$", DOC.read_text(encoding="utf-8"), re.M)
    return {(a, n): ("" if unit == "(empty)" else unit, set(re.findall(r"`(\w+)`", states))) for a, n, unit, states in rows}


def observed_vocabulary() -> dict[tuple[str, str], tuple[set[str], set[str]]]:
    """Every (activity, criterion) -> units and states the rules produce over a grid of values, by the criteria builders themselves."""
    seen: dict[tuple[str, str], tuple[set[str], set[str]]] = {}

    def keep(activity: str, criteria: list[Any]) -> None:
        for c in criteria:
            units, states = seen.setdefault((activity, c.name), (set(), set()))
            units.add(c.unit)
            if c.state is not None:
                states.add(c.state)

    speeds = [None, *[k * 0.5 for k in range(0, 150)]]  # knots, 0 to 74.5
    for relative in ("onshore", "offshore", "cross-shore", None):
        for gap in (0.0, 6.0, 9.0):
            for speed in speeds:
                hour = make_hour(speed_kn=speed, gust_kn=None if speed is None else speed + gap, relative=relative, sea_m=None)
                keep("kite", activities._kite_criteria(hour, 12.0, 25.0))
    for speed in speeds:
        for sea_height in (None, 0.4, 1.5, 3.0):
            for gust in (None, 3.0, 20.0, 30.0, 45.0, 70.0):
                keep("boat", activities._boat_criteria(make_hour(speed_kn=speed, gust_kn=gust, sea_m=sea_height)))
            hour = make_hour(speed_kn=speed, gust_kn=None, sea_m=sea_height)
            for coefficient in (None, 30, 60, 85, 110):
                for low in (None, hour.time_utc + 1800, hour.time_utc + 5 * HOUR):
                    ctx = activities.TideContext(
                        extremes=[], coefficients=[], low_waters=[] if low is None else [low], coefficient_of_low={} if low is None else {low: coefficient}
                    )
                    keep("shore_fishing", activities._fishing_criteria(hour, ctx))
    states = {name: set(values) for name, values in activities._SURF_STATES.items()}
    states["wind"] = {"light", "offshore", "cross_shore", "onshore", "strong"}  # the states of ``_surf_state`` that depend on the rating's points
    states["tide"] = {"right_stage", "wrong_stage"}
    units = {"swell_height": "m", "swell_period": "s", "swell_direction": "deg", "wind": "km/h", "tide": "m"}
    for name in states:
        seen[("surf", name)] = ({units[name]}, states[name])
    return seen


def test_the_documented_vocabulary_is_exactly_what_the_rules_produce() -> None:
    documented_rows, observed = documented_vocabulary(), observed_vocabulary()
    assert set(documented_rows) == set(observed), set(documented_rows) ^ set(observed)
    for key, (unit, states) in documented_rows.items():
        seen_units, seen_states = observed[key]
        assert seen_units == {unit}, key
        assert seen_states == states, (key, seen_states ^ states)


# ------------------------------------------------------------------------------------------------------------- the route


@pytest.fixture
def sea(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    aggregator = world.aggregator
    monkeypatch.setattr(aggregator.surf, "fetch_swell", AsyncMock(return_value=(parse_swell(SWELL), False)))
    monkeypatch.setattr(aggregator.surf, "fetch_wind", AsyncMock(return_value=(parse_wind(WIND), False)))
    monkeypatch.setattr(aggregator.vigilances, "for_point", AsyncMock(return_value=response([], ["ok"])))
    return world


def frame_block(frame: str) -> list[str]:
    return [line for line in frame.split("\n") if line.split("|")[0] in ("SEA", "ACT", "CRIT")]


def test_the_endpoint_adds_the_block_of_the_spot_asked_for(sea: World) -> None:
    reply = sea.client.get("/v1/embedded/frame", params={"spot": SPOT_ID})
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/plain")
    assert verify_embedded_frame_crc(reply.text) is True
    assert frame_block(reply.text) == activities_lines(hossegor(days=1)[1])  # the same lines as the pure build of the same answers
    tags = [line.split("|")[0] for line in reply.text.split("\n") if line]
    assert tags[0] == "HEAD" and tags[-1] == "END" and tags.index("SEA") > max(i for i, t in enumerate(tags) if t in ("ZONE", "DAY", "HAZ"))


def test_the_endpoint_gives_no_block_without_a_spot(sea: World) -> None:
    reply = sea.client.get("/v1/embedded/frame")
    assert reply.status_code == 200 and frame_block(reply.text) == []


def test_the_endpoint_passes_the_wind_range_of_the_kite(sea: World) -> None:
    reply = sea.client.get("/v1/embedded/frame", params={"spot": SPOT_ID, "wind_min_kn": 15, "wind_max_kn": 30})
    assert frame_block(reply.text)[0].split("|")[6:8] == ["15.0", "30.0"]


@pytest.mark.parametrize("params", [{"wind_min_kn": 25, "wind_max_kn": 12}, {"wind_min_kn": 20, "wind_max_kn": 20}, {"wind_min_kn": 0}, {"wind_max_kn": 61}, {"wind_min_kn": "x"}])
def test_the_endpoint_refuses_what_is_not_a_range(sea: World, params: dict[str, Any]) -> None:
    assert sea.client.get("/v1/embedded/frame", params={"spot": SPOT_ID, **params}).status_code == 422


def test_the_endpoint_says_when_the_spot_is_unknown(sea: World) -> None:
    reply = sea.client.get("/v1/embedded/frame", params={"spot": "nowhere"})
    assert reply.status_code == 404 and "nowhere" in reply.json()["detail"]


def test_the_frame_goes_out_without_the_block_when_the_swell_cannot_be_had(sea: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sea.aggregator.surf, "fetch_swell", AsyncMock(side_effect=RuntimeError("down")))
    reply = sea.client.get("/v1/embedded/frame", params={"spot": SPOT_ID})
    assert reply.status_code == 200 and verify_embedded_frame_crc(reply.text) is True
    assert frame_block(reply.text) == [] and reply.text.startswith("HEAD|")


def test_the_endpoint_is_documented_with_its_errors_and_its_parameters(sea: World) -> None:
    operation = sea.client.get("/openapi.json").json()["paths"]["/v1/embedded/frame"]["get"]
    assert set(operation["responses"]) >= {"200", "404", "422"}
    assert {"spot", "wind_min_kn", "wind_max_kn"} <= {p["name"] for p in operation["parameters"]}


@pytest.mark.asyncio
async def test_the_frame_costs_the_providers_nothing_more_than_the_surf_page(sea: World, monkeypatch: pytest.MonkeyPatch) -> None:
    mine = provider()
    monkeypatch.setattr(sea.aggregator, "surf", SurfForecastCollector(http_client=mine.client()))
    first = await sea.aggregator.build_embedded_frame(spot_id=SPOT_ID)
    second = await sea.aggregator.build_embedded_frame(spot_id=SPOT_ID, wind_min_kn=15.0, wind_max_kn=30.0)
    assert mine.count == 2  # one swell, one wind (the sun is read from it), then the cache
    assert frame_block(first) and frame_block(second)
