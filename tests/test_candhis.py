"""CANDHIS (Cerema): the latest record of the wave buoys, read from the three answers recorded on 2026-11-09 (``tests/fixtures/candhis``)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors.base import CollectorUnavailableError
from app.collectors.candhis import BASE_URL, KEY_ENV, KINDS, CandhisCollector, candhis_key_from_env, parse_latest
from app.main import aggregator, app
from app.services import buoys

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "candhis"
KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # made up, in the shape of a key (low entropy on purpose: the secret scan reads a key-like literal as a leak)
NOW = datetime(2026, 11, 9, 19, 8, tzinfo=timezone.utc).timestamp()  # the minute after the answers were recorded
HOSSEGOR = "soorts-hossegor-plage-sud-point-d-or"  # Anglet (06402), 20 km away, answered a record dated 19:00


def recorded(kind: int) -> dict[str, Any]:
    return json.loads((FIXTURES / f"list-type{kind}-2026-11-09.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def candhis_host(sent: list[httpx.Request], fail: tuple[int, ...] = ()) -> Callable[[httpx.Request], httpx.Response]:
    """The Candhis API as it answered on 2026-11-09: the recorded answer of the kind asked, an error for the kinds in ``fail``."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        kind = int(request.url.params["type"])
        if kind in fail:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=recorded(kind))

    return handler


def collector_with(handler: Callable[[httpx.Request], httpx.Response]) -> CandhisCollector:
    return CandhisCollector(KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


# ------------------------------------------------------------------------------------------------------------------ the parser


def test_a_directional_h13_buoy_is_read_as_candhis_gave_it() -> None:
    readings = parse_latest(recorded(2))
    assert len(readings) == 28
    pierres_noires = readings["02911"]
    assert pierres_noires.measured_utc == int(datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc).timestamp())  # "Heure (TU)": UTC
    assert (pierres_noires.height_m, pierres_noires.height_kind, pierres_noires.hmax_m) == (1.5, "H1/3", 2.5)
    assert (pierres_noires.period_s, pierres_noires.period_kind, pierres_noires.peak_period_s) == (11.3, "TH1/3", None)
    assert (pierres_noires.direction_deg, pierres_noires.spread_deg, pierres_noires.sea_temp_c) == (229.0, 22.0, 14.1)


def test_a_value_candhis_has_not_got_is_none_never_999() -> None:
    bonifacio = parse_latest(recorded(2))["02A01"]  # its direction is 999.9999 in the answer
    assert bonifacio.direction_deg is None and bonifacio.height_m == 0.5
    ile_d_yeu = parse_latest(recorded(0))["08504"]  # its sea temperature is 999.9999
    assert ile_d_yeu.sea_temp_c is None and ile_d_yeu.peak_period_s == 13.3 and ile_d_yeu.direction_deg is None
    for kind in KINDS:
        for reading in parse_latest(recorded(kind)).values():
            assert all(v is None or v < 999 for v in (reading.hmax_m, reading.period_s, reading.direction_deg, reading.sea_temp_c))


def test_the_three_kinds_have_their_own_height_and_period() -> None:
    hm0 = parse_latest(recorded(1))["01102"]
    assert (hm0.height_kind, hm0.period_kind, hm0.height_m, hm0.period_s, hm0.hmax_m) == ("Hm0", "T02", 2.4, 6.0, None)
    non_directional = parse_latest(recorded(0))["01705"]
    assert (non_directional.height_kind, non_directional.period_kind, non_directional.direction_deg) == ("H1/3", "TH1/3", None)


def test_a_date_ahead_of_the_servers_clock_is_kept_as_given() -> None:
    four = parse_latest(recorded(2))["04403"]  # dated 19:30 in an answer of 19:07 UTC (2026-11-09)
    assert four.measured_utc == int(datetime(2026, 11, 9, 19, 30, tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize(
    "broken",
    [
        None,
        [],
        {"success": False, "message": "Aucune donnée", "entete": None, "results": None},
        {"success": True, "entete": None, "results": []},
        {"success": True, "entete": ["Campagne", "Date"], "results": []},  # no height column
        {"success": True, "entete": ["Date", "H1/3 (m)"], "results": []},  # no station column
    ],
)
def test_an_answer_that_is_not_candhis_shape_raises(broken: Any) -> None:
    with pytest.raises(ValueError):
        parse_latest(broken)


def test_success_is_read_as_a_boolean_or_as_the_text_the_guide_shows() -> None:
    payload = recorded(1)
    assert parse_latest({**payload, "success": "True"}) and parse_latest({**payload, "success": True})
    with pytest.raises(ValueError):
        parse_latest({**payload, "success": "False"})


def test_a_record_without_a_height_a_station_or_a_date_is_skipped() -> None:
    header = ["Campagne", "Date", "H1/3 (m)", "Hmax (m)"]
    rows = [
        ["00001", "2026-11-09 19:00", "999.9999", "1.0"],  # no height
        ["", "2026-11-09 19:00", "1.0", "2.0"],  # no station
        ["00002", "not a date", "1.0", "2.0"],
        ["00003", "2026-11-09 19:00", "1.0", "2.0"],
    ]
    assert list(parse_latest({"success": True, "entete": header, "results": rows})) == ["00003"]


# ------------------------------------------------------------------------------------------------------------------ the key


def test_the_key_comes_from_the_environment_and_is_never_quoted_when_it_is_wrong() -> None:
    assert candhis_key_from_env({KEY_ENV: f"  {KEY}  "}) == (KEY, None)
    assert candhis_key_from_env({}) == (None, None)
    assert candhis_key_from_env({KEY_ENV: "  "}) == (None, None)
    key, problem = candhis_key_from_env({KEY_ENV: "not-a-key-0000"})
    assert key is None and problem is not None and "not-a-key-0000" not in problem and KEY_ENV in problem


def test_the_key_is_sent_in_the_authorization_header_and_never_in_the_url() -> None:
    sent: list[httpx.Request] = []
    collector = collector_with(candhis_host(sent))
    asyncio.run(collector.fetch_latest())
    assert len(sent) == 3 and {int(r.url.params["type"]) for r in sent} == set(KINDS)
    for request in sent:
        assert request.headers["Authorization"] == KEY  # as it is: the guide shows no "Bearer"
        assert KEY not in str(request.url) and str(request.url).startswith(BASE_URL + "?type=")
        assert request.method == "GET"


# ------------------------------------------------------------------------------------------------------------------ the collector


def test_the_three_kinds_are_read_once_an_hour_whatever_the_number_of_clients() -> None:
    sent: list[httpx.Request] = []
    collector = collector_with(candhis_host(sent))

    async def run() -> None:
        for _ in range(5):
            latest = await collector.fetch_latest()
            assert len(latest.readings) == 28 + 2 + 1 and not latest.stale and latest.failed_kinds == ()
        await asyncio.gather(*(collector.fetch_latest() for _ in range(20)))

    asyncio.run(run())
    assert len(sent) == 3  # one request a kind, for 25 calls
    health = collector.get_health()
    assert health.provider_id == "candhis_tr" and health.calls_24h == 3 and health.quota is not None


def test_a_kind_that_fails_leaves_the_others_and_says_which() -> None:
    sent: list[httpx.Request] = []
    latest = asyncio.run(collector_with(candhis_host(sent, fail=(1,))).fetch_latest())
    assert latest.failed_kinds == (1,) and "01102" not in latest.readings and "02911" in latest.readings and not latest.stale


def test_when_every_kind_fails_the_collector_says_so_and_no_message_carries_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with {KEY}")

    collector = collector_with(handler)
    with pytest.raises(CollectorUnavailableError) as raised:
        asyncio.run(collector.fetch_latest())
    assert KEY not in str(raised.value) and KEY not in (collector.last_error or "")


def test_a_quota_of_150_requests_a_day_is_enforced() -> None:
    collector = collector_with(candhis_host([]))
    quota = collector.budget.status(0.0)
    assert quota is not None and [(limit.window_s, limit.limit, limit.budget) for limit in quota.limits] == [(86_400, 150.0, 135.0)]


# ------------------------------------------------------------------------------------------------------------------ the service


def reading_at(now: float, collector: CandhisCollector | None, spot: str = HOSSEGOR) -> Any:
    return asyncio.run(buoys.spot_reading(collector, spot, now))


def test_a_spot_gets_the_latest_record_of_the_nearest_current_buoy() -> None:
    answer = reading_at(NOW, collector_with(candhis_host([])))
    assert answer.status == "ok" and answer.buoy is not None and answer.measure is not None
    assert answer.buoy.id == "06402" and answer.buoy.name == "Anglet"  # the nearest located buoy that has a current record
    assert 15 < (answer.distance_km or 0) < 30 and answer.comparable
    assert (answer.measure.height_m, answer.measure.period_s, answer.measure.direction_deg) == (0.4, 3.5, 246.0)
    assert answer.measure.measured_at == "2026-11-09T19:00:00Z" and answer.measure.age_minutes == 8
    assert "Candhis" in answer.attribution and answer.max_age_hours == 3 and answer.max_comparable_km == buoys.MAX_COMPARABLE_KM


def test_a_buoy_that_has_stopped_is_passed_over_for_the_next_one() -> None:
    # Royan (01705, the nearest buoy of Pontaillac) still answers, with a record dated 2026-11-08 00:00: it is not "the sea now".
    answer = reading_at(NOW, collector_with(candhis_host([])), spot="royan-plage-de-pontaillac")
    assert answer.status == "ok" and answer.buoy is not None and answer.buoy.id != "01705"
    assert answer.measure is not None and answer.measure.age_minutes < 3 * 60


def test_when_no_record_is_current_nothing_is_said_rather_than_a_stale_sea() -> None:
    # The latest record of the answers is 19:30 UTC: three hours and a minute later none is current.
    late = datetime(2026, 11, 9, 22, 31, tzinfo=timezone.utc).timestamp()
    answer = reading_at(late, collector_with(candhis_host([])))
    assert answer.status == "no_current_reading" and answer.buoy is None and answer.measure is None and answer.distance_km is None


def test_a_record_dated_far_ahead_of_our_clock_is_not_believed() -> None:
    # 2026-11-09 19:30 is 22 minutes ahead of the clock: still believed; at 18:50 it is 40 minutes ahead: not.
    early = datetime(2026, 11, 9, 18, 50, tzinfo=timezone.utc).timestamp()
    answer = reading_at(early, collector_with(candhis_host([])), spot="saint-jean-de-luz-grande-plage")
    # every record of 19:00 is 10 minutes ahead (believed), so a buoy answers; but Plateau du Four (19:30) never does
    assert answer.status == "ok" and answer.buoy is not None and answer.buoy.id != "04403"


def test_the_age_is_never_below_zero() -> None:
    answer = reading_at(datetime(2026, 11, 9, 18, 55, tzinfo=timezone.utc).timestamp(), collector_with(candhis_host([])))
    assert answer.status == "ok" and answer.measure is not None and answer.measure.age_minutes == 0


def test_a_spot_far_from_every_current_buoy_says_it_is_not_comparable() -> None:
    answer = reading_at(NOW, collector_with(candhis_host([])), spot="hendaye-ondarraitz")
    assert answer.status == "ok" and answer.buoy is not None and answer.buoy.id == "06403"  # Saint-Jean-de-Luz, 8 km
    far = reading_at(NOW, collector_with(candhis_host([])), spot="moliets-et-maa-plage-de-la-balise")
    assert far.status == "ok" and far.distance_km is not None and far.distance_km > buoys.MAX_COMPARABLE_KM and not far.comparable


def test_a_kind_that_could_not_be_read_makes_the_answer_incomplete() -> None:
    """Seen on 2026-11-09 against the real API: one of the three requests failed once, and Hossegor was given a buoy 359 km away as the
    nearest, with nothing to say that the directional buoys (Anglet, 20 km) had not been read."""
    answer = reading_at(NOW, collector_with(candhis_host([], fail=(2,))))
    assert answer.status == "ok" and answer.complete is False
    assert answer.buoy is not None and answer.buoy.id == "08504" and not answer.comparable  # Ile d'Yeu Nord, kind 0
    whole = reading_at(NOW, collector_with(candhis_host([])))
    assert whole.complete is True and whole.buoy is not None and whole.buoy.id == "06402"
    nothing = reading_at(NOW + 6 * 3600, collector_with(candhis_host([], fail=(2,))))
    assert nothing.status == "no_current_reading" and nothing.complete is False


def test_without_a_key_or_when_candhis_fails_the_answer_says_why_and_never_fails() -> None:
    assert reading_at(NOW, None).status == "not_configured"
    failing = collector_with(candhis_host([], fail=KINDS))
    broken = reading_at(NOW, failing)
    assert broken.status == "unavailable" and broken.measure is None and broken.attribution


def test_an_unknown_spot_is_an_error() -> None:
    with pytest.raises(buoys.surf.UnknownSpot):
        reading_at(NOW, None, spot="nowhere")


# ------------------------------------------------------------------------------------------------------------------ the route


def test_the_route_serves_the_record_and_says_when_the_instance_has_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "_wall", lambda: NOW)
    client = TestClient(app)
    monkeypatch.setattr(aggregator, "candhis", None)
    off = client.get(f"/v1/surf/spots/{HOSSEGOR}/buoy")
    assert off.status_code == 200 and off.json()["status"] == "not_configured" and off.json()["measure"] is None
    monkeypatch.setattr(aggregator, "candhis", collector_with(candhis_host([])))
    on = client.get(f"/v1/surf/spots/{HOSSEGOR}/buoy")
    body = on.json()
    assert on.status_code == 200 and body["status"] == "ok" and body["buoy"]["id"] == "06402"
    assert body["measure"]["height_m"] == 0.4 and body["measure"]["age_minutes"] == 8 and "Candhis" in body["attribution"]
    assert on.headers["Cache-Control"] == "public, max-age=600"
    assert client.get("/v1/surf/spots/nowhere/buoy").status_code == 404


def test_without_a_key_the_source_is_listed_as_disabled_and_with_one_it_is_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(aggregator, "candhis", None)
    health = client.get("/v1/health").json()
    assert "candhis_tr" in [s["provider_id"] for s in health["disabled_sources"]]
    assert "candhis_tr" not in [p["provider_id"] for p in health["providers"]]
    monkeypatch.setattr(aggregator, "candhis", collector_with(candhis_host([])))
    health = client.get("/v1/health").json()
    assert "candhis_tr" in [p["provider_id"] for p in health["providers"]]
    assert "candhis_tr" not in [s["provider_id"] for s in health["disabled_sources"]]
