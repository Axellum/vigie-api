"""Probabilities of rain, snow, gusts, hail and storm (Météo-France PE-AROME and PE-ARPEGE ensembles), read from a vigie-meteo gateway.

The recorded answer is a real one of the gateway (``tests/fixtures/meteofrance_ensemble``, see its README); the summary is tested on made-up
windows. No test touches the network.
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
from app.collectors.meteofrance_ensemble import (
    LONG_KEYS,
    LONG_MODEL,
    SHORT_KEYS,
    SHORT_MODEL,
    MeteoFranceEnsembleCollector,
    compact,
    parse_gateway_probabilities,
    summarize,
)
from app.commercial import COMMERCIAL_USE
from app.models.schemas import DailyForecast, SeverityLevel
from tests.helpers import Provider, SimClock, make_service

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "meteofrance_ensemble" / "probabilities-2026-11-18.json"
VANNES = (47.66, -2.76)
OSLO = (59.91, 10.75)
BASE = int(SimClock.EPOCH) // 3600 * 3600  # a run time on the hour just before the simulated clock


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_window(end: int, rain_1mm: int = 0, **others: int) -> dict[str, Any]:
    values = {key: 0 for key in SHORT_KEYS}
    values["rain_1mm"] = rain_1mm
    values.update(others)
    return {"time": iso(end), "window_hours": 3, "probability_pct": values}


def long_window(end: int, rain_1mm: int = 0) -> dict[str, Any]:
    values = {key: 0 for key in LONG_KEYS}
    values["rain_1mm"] = rain_1mm
    return {"time": iso(end), "window_hours": 24, "probability_pct": values}


def short_block(windows: list[dict[str, Any]], model: str = SHORT_MODEL) -> dict[str, Any]:
    return {"source_model": model, "attribution": "Source : Météo-France", "run": iso(BASE), "members": 35, "windows": windows}


def long_block(windows: list[dict[str, Any]], model: str = LONG_MODEL) -> dict[str, Any]:
    return {"source_model": model, "attribution": "Source : Météo-France", "run": iso(BASE), "members": 35, "windows": windows}


def quiet_short() -> list[dict[str, Any]]:
    return [short_window(BASE + 3600 * 3 * k) for k in range(1, 18)]


def quiet_long() -> list[dict[str, Any]]:
    return [long_window(BASE + 86400 * k) for k in range(1, 5)]


def payload(short: Optional[list[dict[str, Any]]] = None, long: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    return {
        "arome": short_block(short if short is not None else quiet_short()),
        "arpege": long_block(long if long is not None else quiet_long()),
    }


# ------------------------------------------------------------------------------------------------ parsing and summary


def test_the_recorded_answer_is_read_and_summarized() -> None:
    answer = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = parse_gateway_probabilities(answer)
    assert raw.short is not None and raw.short.members == 35 and len(raw.short.windows) >= 1
    run = raw.short.run_utc
    summary = summarize(raw, run)  # judged at the run's own time: every window is still ahead
    assert summary is not None and summary.short_range is not None
    assert all(0 <= w.rain_1mm_pct <= 100 and 0 <= w.storm_pct <= 100 for w in summary.short_range.windows)
    assert summary.short_range.windows == sorted(summary.short_range.windows, key=lambda w: w.to_utc)
    if raw.long is not None:
        assert summary.long_range is not None and summary.long_range.members == 35
        assert all(w.to_utc > run for w in summary.long_range.windows)
    assert credit_text("meteofrance_ensemble") in summary.attribution
    assert summary.rain_peak_next_24h is not None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(arome=short_block(quiet_short(), model="meteofrance-arome-pi")),  # not PE-AROME
        lambda p: p.update(arpege=long_block(quiet_long(), model=SHORT_MODEL)),  # PE-AROME given as PE-ARPEGE
        lambda p: p["arome"]["windows"][0]["probability_pct"].update(rain_1mm=101),
        lambda p: p["arome"]["windows"][0]["probability_pct"].update(rain_1mm=-1),
        lambda p: p["arome"]["windows"][0]["probability_pct"].update(rain_1mm=None),
        lambda p: p["arome"]["windows"][0]["probability_pct"].update(rain_1mm=True),
        lambda p: p["arome"]["windows"][0]["probability_pct"].pop("storm"),
        lambda p: p["arome"]["windows"][0].update(window_hours=6),
        lambda p: p["arome"]["windows"][0].update(time="2026-11-18T12:00:00"),  # no time zone
        lambda p: p["arome"].update(members=0),
        lambda p: p["arpege"]["windows"][0]["probability_pct"].pop("rain_20mm"),
    ],
)
def test_an_answer_that_is_not_the_ensembles_or_has_an_impossible_value_is_refused(mutate: Any) -> None:
    answer = payload()
    mutate(answer)
    with pytest.raises(ValueError):
        parse_gateway_probabilities(answer)


def test_a_non_object_answer_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_gateway_probabilities([])


def test_windows_are_sorted_and_a_repeated_end_keeps_one() -> None:
    windows = [short_window(BASE + 7200, rain_1mm=30), short_window(BASE + 3600, rain_1mm=10), short_window(BASE + 3600, rain_1mm=20)]
    raw = parse_gateway_probabilities({"arome": short_block(windows), "arpege": None})
    assert raw.short is not None and [(end, v["rain_1mm"]) for end, v in raw.short.windows] == [(BASE + 3600, 20), (BASE + 7200, 30)]


def test_an_answer_with_a_single_ensemble_is_read() -> None:
    only_long = parse_gateway_probabilities({"arome": None, "arpege": long_block(quiet_long())})
    assert only_long.short is None and only_long.long is not None
    summary = summarize(only_long, BASE)
    assert summary is not None and summary.short_range is None and summary.long_range is not None and summary.rain_peak_next_24h is None


def test_an_answer_with_only_pe_arome_or_with_one_block_wholly_past_is_summarized() -> None:
    only_short = parse_gateway_probabilities({"arome": short_block(quiet_short()), "arpege": None})
    assert only_short.short is not None and only_short.long is None
    summary = summarize(only_short, BASE)
    assert summary is not None and summary.short_range is not None and summary.long_range is None
    assert summary.rain_peak_next_24h is not None
    assert compact(summary, BASE).long_range is None
    # PE-AROME is wholly past while PE-ARPEGE is not: the answer keeps PE-ARPEGE and has no peak
    both = parse_gateway_probabilities(payload())
    assert both.short is not None and both.long is not None
    last_short = max(end for end, _ in both.short.windows)
    last_long = max(end for end, _ in both.long.windows)
    assert last_short < last_long
    late = summarize(both, last_short + 1)
    assert late is not None and late.short_range is None and late.long_range is not None and late.rain_peak_next_24h is None
    assert compact(late, last_short + 1).short_range is None


def test_windows_that_are_past_are_dropped_and_none_left_is_none() -> None:
    raw = parse_gateway_probabilities(payload())
    summary = summarize(raw, BASE + 3 * 3600 * 5 + 1)  # five windows are past
    assert summary is not None and summary.short_range is not None and len(summary.short_range.windows) == 17 - 5
    assert summary.short_range.windows[0].to_utc == BASE + 3 * 3600 * 6
    assert summarize(raw, BASE + 86400 * 5) is None


def test_the_peak_is_the_largest_window_of_the_next_24_hours_the_soonest_when_tied() -> None:
    windows = [short_window(BASE + 3 * 3600 * k, rain_1mm=v) for k, v in enumerate([10, 40, 70, 70, 20, 10, 5, 0, 99, 99, 0, 0, 0, 0, 0, 0, 0], start=1)]
    summary = summarize(parse_gateway_probabilities({"arome": short_block(windows), "arpege": None}), BASE)
    assert summary is not None and summary.rain_peak_next_24h is not None
    assert (summary.rain_peak_next_24h.rain_1mm_pct, summary.rain_peak_next_24h.to_utc) == (70, BASE + 3 * 3600 * 3)  # the 99 % windows are after 24 hours


def test_the_compact_profile_keeps_the_next_24_hours_of_short_range_and_all_the_days() -> None:
    summary = summarize(parse_gateway_probabilities(payload()), BASE)
    assert summary is not None and summary.short_range is not None and summary.long_range is not None
    small = compact(summary, BASE)
    assert small.short_range is not None and len(small.short_range.windows) == 8  # 3 h to 24 h
    assert small.long_range is not None and len(small.long_range.windows) == 4
    assert len(summary.short_range.windows) == 17  # the full one is untouched


# ------------------------------------------------------------------------------------------------ the collector


def gateway(body: Optional[dict[str, Any]] = None, status: int = 200) -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/probabilities"
        if status != 200:
            return httpx.Response(status, json={"title": "x"})
        return httpx.Response(200, json=body if body is not None else payload())

    return Provider(answer=answer)


def collector(provider: Provider) -> MeteoFranceEnsembleCollector:
    return MeteoFranceEnsembleCollector("http://gateway.test", http_client=provider.client())


async def test_two_nearby_points_share_one_gateway_request(sim_clock: SimClock) -> None:
    provider = gateway()
    ensemble = collector(provider)
    first, second = await asyncio.gather(ensemble.probabilities(*VANNES), ensemble.probabilities(VANNES[0] + 0.004, VANNES[1] - 0.003))
    assert first is not None and second is not None and provider.count == 1
    assert provider.requests[0].url.params["lat"] and provider.requests[0].url.params["lon"]


async def test_the_answer_is_kept_ten_minutes_and_an_old_one_loses_its_past_windows(sim_clock: SimClock) -> None:
    sim_clock.now = BASE - SimClock.EPOCH + 10
    state = {"status": 200}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(state["status"], json=payload())

    provider = Provider(answer=answer)
    ensemble = collector(provider)
    first = await ensemble.probabilities(*VANNES)
    assert first is not None and first.short_range is not None and len(first.short_range.windows) == 17
    sim_clock.advance(590)
    await ensemble.probabilities(*VANNES)
    assert provider.count == 1  # still inside the ten minutes
    sim_clock.advance(20)
    await ensemble.probabilities(*VANNES)
    assert provider.count == 2
    # the gateway goes down: the entry that expired is still served, but only for the windows the clock has not passed
    state["status"] = 503
    sim_clock.advance(3 * 3600)
    stale = await ensemble.probabilities(*VANNES)
    assert stale is not None and stale.short_range is not None and len(stale.short_range.windows) == 17 - 1


@pytest.mark.parametrize(
    "provider",
    [
        gateway(status=404),
        gateway(status=503),
        gateway(status=500),
        gateway(body={"arome": short_block(quiet_short(), model="meteofrance-piaf"), "arpege": None}),
        gateway(body={"arome": None, "arpege": None}),
    ],
)
async def test_a_point_the_gateway_cannot_give_is_none_and_never_an_error(sim_clock: SimClock, provider: Provider) -> None:
    assert await collector(provider).probabilities(*VANNES) is None


async def test_a_gateway_that_answers_404_does_not_open_the_circuit(sim_clock: SimClock) -> None:
    provider = gateway(status=404)
    ensemble = collector(provider)
    for k in range(6):
        assert await ensemble.probabilities(47.0 + k, 2.0) is None  # six different cells, all "outside the maps"
    assert provider.count == 6
    assert await ensemble.probabilities(47.0, 2.0) is None and provider.count == 6  # cached: "outside the maps" is an answer


# ------------------------------------------------------------------------------------------------ the point


def _service(tmp_path: Path, ensemble: Optional[MeteoFranceEnsembleCollector]) -> Any:
    extra: dict[str, Any] = {}
    if ensemble is not None:
        extra["meteofrance_ensemble"] = ensemble
    return make_service(tmp_path / "ensemble.db", **extra)


async def test_a_point_in_france_carries_the_probabilities_and_their_credit(sim_clock: SimClock, tmp_path: Path) -> None:
    sim_clock.now = BASE - SimClock.EPOCH + 10
    short = [short_window(BASE + 3 * 3600 * k, rain_1mm=80 if k == 3 else 5, storm=35 if k == 3 else 0) for k in range(1, 18)]
    service = _service(tmp_path, collector(gateway(payload(short=short))))
    point = await service.build_point_response(*VANNES, compact=False)
    probabilities = point.probabilities
    assert probabilities is not None and probabilities.short_range is not None and probabilities.long_range is not None
    assert len(probabilities.short_range.windows) == 17 and len(probabilities.long_range.windows) == 4
    assert probabilities.rain_peak_next_24h is not None and probabilities.rain_peak_next_24h.rain_1mm_pct == 80
    assert probabilities.rain_peak_next_24h.to_utc == BASE + 3 * 3600 * 3
    assert credit_text("meteofrance_ensemble") in probabilities.attribution
    # informative only: a model forecast is not a warning, so the level of the position does not move
    assert point.local_alert_level == SeverityLevel.GREEN
    dumped = point.model_dump(mode="json")
    assert dumped["probabilities"]["short_range"]["windows"][2]["storm_pct"] == 35
    # the daily probability of the weather source is left as it came: nothing merged
    assert "rain_probability_pct" in DailyForecast.model_fields and "rain_probability_pct" not in dumped["probabilities"]


async def test_the_compact_point_keeps_the_next_24_hours(sim_clock: SimClock, tmp_path: Path) -> None:
    sim_clock.now = BASE - SimClock.EPOCH + 10
    service = _service(tmp_path, collector(gateway()))
    small = (await service.build_point_response(*VANNES)).probabilities  # compact is the default
    assert small is not None and small.short_range is not None and len(small.short_range.windows) == 8
    assert small.long_range is not None and len(small.long_range.windows) == 4
    full = (await service.build_point_response(*VANNES, compact=False)).probabilities
    assert full is not None and full.short_range is not None and len(full.short_range.windows) == 17


async def test_a_point_outside_france_never_asks_the_gateway(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = gateway()
    service = _service(tmp_path, collector(provider))
    point = await service.build_point_response(*OSLO)
    assert point.probabilities is None and provider.count == 0


async def test_a_failing_gateway_leaves_the_point_without_the_probabilities(sim_clock: SimClock, tmp_path: Path) -> None:
    service = _service(tmp_path, collector(gateway(status=503)))
    point = await service.build_point_response(*VANNES)
    assert point.probabilities is None and point.rain_nowcast is not None


async def test_without_a_gateway_there_are_no_probabilities(sim_clock: SimClock, tmp_path: Path) -> None:
    point = await _service(tmp_path, None).build_point_response(*VANNES)
    assert point.probabilities is None


def test_the_source_is_listed_as_off_without_a_gateway(tmp_path: Path) -> None:
    service = _service(tmp_path, None)
    off = {item["provider_id"]: item["reason"] for item in service.disabled_sources()}
    assert "VIGIE_METEO_GATEWAY_URL" in off["meteofrance_ensemble"]
    assert "meteofrance_ensemble" not in {c.provider_id for c in service.get_all_collectors()}

    on = _service(tmp_path, collector(gateway()))
    assert "meteofrance_ensemble" in {c.provider_id for c in on.get_all_collectors()}
    assert "meteofrance_ensemble" not in {item["provider_id"] for item in on.disabled_sources()}


def test_the_licence_is_the_one_the_apis_own_document_states() -> None:
    use = COMMERCIAL_USE["meteofrance_ensemble"]
    assert use.status == "allowed" and "Licence Ouverte 2.0" in use.reason
