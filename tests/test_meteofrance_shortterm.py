"""Hail, gusts, reduced visibility and type of precipitation of the next hours (Météo-France AROME-PI), read from a vigie-meteo gateway.

The recorded answer is a real one of the gateway (``tests/fixtures/meteofrance_shortterm``, see its README); the levels are tested on
made-up steps at each threshold. No test touches the network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from app.attribution import credit_text
from app.collectors.meteofrance_shortterm import (
    GUST_STRONG_KMH,
    GUST_VERY_STRONG_KMH,
    HAIL_KG_M2,
    HAIL_SMALL_KG_M2,
    VISIBILITY_REDUCED_M,
    VISIBILITY_VERY_REDUCED_M,
    MeteoFranceShortTermCollector,
    parse_gateway_shortterm,
    precipitation_kind,
    summarize,
)
from app.models.schemas import SeverityLevel
from tests.helpers import Provider, SimClock, make_service

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "meteofrance_shortterm" / "shortterm-2026-11-18.json"
VANNES = (47.66, -2.76)
OSLO = (59.91, 10.75)
BASE = int(SimClock.EPOCH) // 3600 * 3600  # a run time on the hour just before the simulated clock


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def step(k: int, gust: int = 20, hail: float = 0.0, visibility: int = 20000, code: int = 0) -> dict[str, Any]:
    """The step that ends ``k`` quarters of an hour after the run."""
    return {"time": iso(BASE + 900 * k), "wind_gust_kmh": gust, "hail_kg_m2": hail, "visibility_m": visibility, "precip_type": code}


def payload(steps: list[dict[str, Any]], model: str = "meteofrance-arome-pi") -> dict[str, Any]:
    return {"source_model": model, "attribution": "x", "run": iso(BASE), "interval_minutes": 15, "steps": steps}


def quiet(n: int = 24) -> list[dict[str, Any]]:
    return [step(k) for k in range(1, n + 1)]


# ------------------------------------------------------------------------------------------------ the answer


def test_the_recorded_answer_is_read_and_summarized() -> None:
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = parse_gateway_shortterm(body)
    assert raw.steps and all(row[0] % 900 == 0 for row in raw.steps)
    assert body["source_model"] == "meteofrance-arome-pi" and body["interval_minutes"] == 15
    result = summarize(raw, float(raw.steps[0][0]))
    assert result is not None and len(result.steps) == len(raw.steps)
    assert result.run_utc == raw.run_utc and result.attribution == credit_text("meteofrance_shortterm")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(source_model="meteofrance-piaf"),
        lambda p: p.pop("source_model"),
        lambda p: p.update(steps=[]),
        lambda p: p.update(steps="x"),
        lambda p: p.update(run="2026-11-18T09:00:00"),  # no time zone
        lambda p: p.pop("run"),
        lambda p: p["steps"][0].update(time="2026-11-18T09:15:00"),
        lambda p: p["steps"][0].update(wind_gust_kmh=-1),
        lambda p: p["steps"][0].update(wind_gust_kmh=401),
        lambda p: p["steps"][0].update(wind_gust_kmh=True),
        lambda p: p["steps"][0].update(wind_gust_kmh=None),
        lambda p: p["steps"][0].update(hail_kg_m2=-0.1),
        lambda p: p["steps"][0].update(hail_kg_m2=float("nan")),
        lambda p: p["steps"][0].update(hail_kg_m2="8"),
        lambda p: p["steps"][0].update(visibility_m=-5),
        lambda p: p["steps"][0].update(visibility_m=None),
        lambda p: p["steps"][0].update(precip_type=None),
        lambda p: p["steps"][0].update(precip_type=10000),
        lambda p: p["steps"].__setitem__(0, "x"),
    ],
)
def test_an_answer_that_is_not_arome_pi_or_has_an_impossible_value_is_refused(mutate: Any) -> None:
    body = payload(quiet(3))
    mutate(body)
    with pytest.raises(ValueError):
        parse_gateway_shortterm(body)


def test_a_non_object_answer_is_refused() -> None:
    for body in ([], "x", None, 3):
        with pytest.raises(ValueError):
            parse_gateway_shortterm(body)


def test_steps_are_sorted_and_a_repeated_end_keeps_one() -> None:
    raw = parse_gateway_shortterm(payload([step(3), step(1), step(2), step(2, gust=33)]))
    assert [row[0] for row in raw.steps] == [BASE + 900, BASE + 1800, BASE + 2700]
    assert raw.steps[1][1] == 33


# ------------------------------------------------------------------------------------------------ the levels


def levels(steps: list[dict[str, Any]], now: Optional[float] = None) -> Any:
    raw = parse_gateway_shortterm(payload(steps))
    result = summarize(raw, float(BASE) if now is None else now)
    assert result is not None
    return result


def test_a_quiet_sky_has_no_level_and_no_precipitation() -> None:
    result = levels(quiet())
    assert (result.gusts.level, result.hail.level, result.visibility.level) == ("none", "none", "none")
    assert result.gusts.at_utc is None and result.hail.first_at_utc is None and result.visibility.first_at_utc is None
    assert result.precipitation == [] and result.horizon_minutes == 360 and len(result.steps) == 24
    assert result.interval_minutes == 15 and result.source_model == "meteofrance-arome-pi"
    assert "not an official warning" in result.note


@pytest.mark.parametrize(
    ("gust", "level"),
    [(GUST_STRONG_KMH - 1, "none"), (GUST_STRONG_KMH, "strong"), (GUST_VERY_STRONG_KMH - 1, "strong"), (GUST_VERY_STRONG_KMH, "very_strong")],
)
def test_gust_thresholds(gust: int, level: str) -> None:
    result = levels([step(1, gust=30), step(2, gust=gust), step(3, gust=25)])
    assert result.gusts.level == level and result.gusts.max_kmh == max(gust, 30)
    assert result.gusts.at_utc == (BASE + 1800 if level != "none" else None)


@pytest.mark.parametrize(
    ("hail", "level"),
    [(HAIL_SMALL_KG_M2 - 0.1, "none"), (HAIL_SMALL_KG_M2, "small_hail"), (HAIL_KG_M2 - 0.1, "small_hail"), (HAIL_KG_M2, "hail")],
)
def test_hail_thresholds_are_meteo_frances(hail: float, level: str) -> None:
    result = levels([step(1), step(2), step(3, hail=hail)])
    assert result.hail.level == level and result.hail.max_kg_m2 == round(hail, 1)
    assert result.hail.first_at_utc == (BASE + 2700 if level != "none" else None)


@pytest.mark.parametrize(
    ("visibility", "level"),
    [
        (VISIBILITY_REDUCED_M, "none"),
        (VISIBILITY_REDUCED_M - 1, "reduced"),
        (VISIBILITY_VERY_REDUCED_M, "reduced"),
        (VISIBILITY_VERY_REDUCED_M - 1, "very_reduced"),
        (0, "very_reduced"),
    ],
)
def test_visibility_thresholds(visibility: int, level: str) -> None:
    result = levels([step(1), step(2, visibility=visibility), step(3, visibility=15000)])
    assert result.visibility.level == level and result.visibility.min_m == min(visibility, 15000)
    assert result.visibility.first_at_utc == (BASE + 1800 if level != "none" else None)


def test_the_kinds_of_precipitation_come_in_their_order_with_their_first_and_last_step() -> None:
    codes = [0, 1, 201, 0, 5, 5, 0, 3, 11, 1]
    result = levels([step(k + 1, code=code) for k, code in enumerate(codes)])
    spans = [(s.kind, s.first_at_utc, s.last_at_utc) for s in result.precipitation]
    assert spans == [
        ("rain", BASE + 900 * 2, BASE + 900 * 10),  # code 201 is the same kind: intermittent rain
        ("snow", BASE + 900 * 5, BASE + 900 * 6),
        ("freezing", BASE + 900 * 8, BASE + 900 * 8),
        ("drizzle", BASE + 900 * 9, BASE + 900 * 9),
    ]
    assert [s.precipitation for s in result.steps][:3] == ["none", "rain", "rain"]


def test_the_codes_of_meteo_france_and_an_unknown_one() -> None:
    assert {c: precipitation_kind(c) for c in (0, 1, 3, 5, 6, 7, 8, 9, 10, 11, 12)} == {
        0: "none", 1: "rain", 3: "freezing", 5: "snow", 6: "wet_snow", 7: "rain_snow", 8: "ice_pellets",
        9: "snow_pellets", 10: "hail", 11: "drizzle", 12: "freezing",
    }  # fmt: skip
    assert [precipitation_kind(c) for c in (193, 201, 205, 206, 207, 213)] == ["wet_snow", "rain", "snow", "wet_snow", "rain_snow", "wet_snow"]
    assert precipitation_kind(4) == "unknown" and precipitation_kind(999) == "unknown"  # never guessed


def test_steps_that_are_past_are_dropped_and_none_left_is_none() -> None:
    raw = parse_gateway_shortterm(payload(quiet(8)))
    now = BASE + 900 * 3 + 1.0  # the third step ended a second ago
    result = summarize(raw, now)
    assert result is not None and [s.time_utc for s in result.steps][0] == BASE + 900 * 4 and len(result.steps) == 5
    assert result.horizon_minutes == round((BASE + 900 * 8 - now) / 60)
    assert summarize(raw, BASE + 900 * 8 + 1.0) is None
    assert summarize(raw, BASE + 900 * 8) is not None  # the last step ends exactly now: still shown


def test_a_strong_event_far_in_the_past_does_not_count() -> None:
    raw = parse_gateway_shortterm(payload([step(1, gust=120, hail=20.0, visibility=50), step(2), step(3)]))
    result = summarize(raw, BASE + 900 * 1 + 1.0)
    assert result is not None
    assert (result.gusts.level, result.hail.level, result.visibility.level) == ("none", "none", "none")


# ------------------------------------------------------------------------------------------------ the collector


def gateway(steps: Optional[list[dict[str, Any]]] = None, status: int = 200, model: str = "meteofrance-arome-pi") -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/shortterm"
        if status != 200:
            return httpx.Response(status, json={"title": "x"})
        return httpx.Response(200, json=payload(steps if steps is not None else quiet(), model=model))

    return Provider(answer=answer)


def collector(provider: Provider) -> MeteoFranceShortTermCollector:
    return MeteoFranceShortTermCollector("http://gateway.test", http_client=provider.client())


async def test_two_nearby_points_share_one_gateway_request(sim_clock: SimClock) -> None:
    provider = gateway()
    short = collector(provider)
    first, second = await asyncio.gather(short.short_term(*VANNES), short.short_term(VANNES[0] + 0.004, VANNES[1] - 0.003))
    assert first is not None and second is not None and provider.count == 1
    assert provider.requests[0].url.params["lat"] and provider.requests[0].url.params["lon"]


async def test_the_answer_is_kept_five_minutes_and_an_old_one_loses_its_past_steps(sim_clock: SimClock) -> None:
    sim_clock.now = BASE - SimClock.EPOCH + 10  # ten seconds after the run's hour
    state = {"status": 200}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(state["status"], json=payload(quiet()))

    provider = Provider(answer=answer)
    short = collector(provider)
    first = await short.short_term(*VANNES)
    assert first is not None and len(first.steps) == 24
    sim_clock.advance(240)
    await short.short_term(*VANNES)
    assert provider.count == 1  # still inside the five minutes
    sim_clock.advance(61)
    await short.short_term(*VANNES)
    assert provider.count == 2
    # the gateway goes down: the entry that expired is still served, but only for the steps the clock has not passed
    state["status"] = 503
    sim_clock.advance(600)
    stale = await short.short_term(*VANNES)
    assert stale is not None and len(stale.steps) == 23


@pytest.mark.parametrize("provider", [gateway(status=404), gateway(status=503), gateway(status=500), gateway(model="meteofrance-piaf"), gateway(steps=[])])
async def test_a_point_the_gateway_cannot_give_is_none_and_never_an_error(sim_clock: SimClock, provider: Provider) -> None:
    assert await collector(provider).short_term(*VANNES) is None


async def test_a_gateway_that_answers_404_does_not_open_the_circuit(sim_clock: SimClock) -> None:
    provider = gateway(status=404)
    short = collector(provider)
    for k in range(6):
        assert await short.short_term(47.0 + k, 2.0) is None  # six different cells, all "outside the map"
    assert provider.count == 6
    assert await short.short_term(47.0, 2.0) is None and provider.count == 6  # cached: "outside the map" is an answer


# ------------------------------------------------------------------------------------------------ the point


def _service(tmp_path: Path, short: Optional[MeteoFranceShortTermCollector]) -> Any:
    extra: dict[str, Any] = {}
    if short is not None:
        extra["meteofrance_shortterm"] = short
    return make_service(tmp_path / "shortterm.db", **extra)


async def test_a_point_in_france_carries_the_forecast_and_its_credit(sim_clock: SimClock, tmp_path: Path) -> None:
    sim_clock.now = BASE - SimClock.EPOCH + 10
    steps = [step(k, gust=95 if k == 4 else 20, hail=17.0 if k == 6 else 0.0, visibility=150 if k == 8 else 20000, code=1 if k in (5, 6) else 0) for k in range(1, 25)]
    service = _service(tmp_path, collector(gateway(steps=steps)))
    point = await service.build_point_response(*VANNES, compact=False)
    forecast = point.short_term
    assert forecast is not None
    assert (forecast.gusts.level, forecast.hail.level, forecast.visibility.level) == ("very_strong", "hail", "very_reduced")
    assert [(s.kind, s.first_at_utc, s.last_at_utc) for s in forecast.precipitation] == [("rain", BASE + 900 * 5, BASE + 900 * 6)]
    assert credit_text("meteofrance_shortterm") in forecast.attribution
    # informative only: a forecast of a model is not a warning, so the level of the position does not move
    assert point.local_alert_level == SeverityLevel.GREEN
    dumped = point.model_dump(mode="json")
    assert dumped["short_term"]["gusts"]["level"] == "very_strong" and len(dumped["short_term"]["steps"]) == 24


async def test_the_compact_point_keeps_the_levels_and_leaves_out_the_steps(sim_clock: SimClock, tmp_path: Path) -> None:
    sim_clock.now = BASE - SimClock.EPOCH + 10
    steps = [step(k, gust=95 if k == 4 else 20) for k in range(1, 25)]
    service = _service(tmp_path, collector(gateway(steps=steps)))
    compact = (await service.build_point_response(*VANNES)).short_term  # compact is the default
    assert compact is not None and compact.steps == [] and compact.gusts.level == "very_strong" and compact.gusts.max_kmh == 95
    full = (await service.build_point_response(*VANNES, compact=False)).short_term
    assert full is not None and len(full.steps) == 24


async def test_a_point_outside_france_never_asks_the_gateway(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = gateway()
    service = _service(tmp_path, collector(provider))
    point = await service.build_point_response(*OSLO)
    assert point.short_term is None and provider.count == 0


async def test_a_failing_gateway_leaves_the_point_without_the_forecast(sim_clock: SimClock, tmp_path: Path) -> None:
    service = _service(tmp_path, collector(gateway(status=503)))
    point = await service.build_point_response(*VANNES)
    assert point.short_term is None and point.rain_nowcast is not None


async def test_without_a_gateway_there_is_no_forecast(sim_clock: SimClock, tmp_path: Path) -> None:
    point = await _service(tmp_path, None).build_point_response(*VANNES)
    assert point.short_term is None


def test_the_source_is_listed_as_off_without_a_gateway(tmp_path: Path) -> None:
    service = _service(tmp_path, None)
    off = {item["provider_id"]: item["reason"] for item in service.disabled_sources()}
    assert "VIGIE_METEO_GATEWAY_URL" in off["meteofrance_shortterm"]
    assert "meteofrance_shortterm" not in {c.provider_id for c in service.get_all_collectors()}

    on = _service(tmp_path, collector(gateway()))
    assert "meteofrance_shortterm" in {c.provider_id for c in on.get_all_collectors()}
    assert "meteofrance_shortterm" not in {item["provider_id"] for item in on.disabled_sources()}
