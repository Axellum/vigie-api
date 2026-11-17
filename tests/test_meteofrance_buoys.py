"""Offline tests for the moored buoys of Météo-France (``DPObs/v2/bouees``) and ``GET /v1/sea/buoys``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.collectors.meteofrance_buoys import (
    BUOYS_URL,
    MeteoFranceBuoysCollector,
    display_name,
    meteofrance_buoys_key_from_env,
    parse_buoys,
)
from app.services.sea_buoys import sea_buoys
from tests.conftest import World
from tests.helpers import SimClock, make_service

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "meteofrance_buoys" / "bouees-2026-11-16.json"
PAYLOAD = json.loads(FIXTURE.read_text(encoding="utf-8"))
SAMPLE_KEY = "mf_buoys_secret_token_1234567890abcdef=="
# 2026-11-16 22:30 UTC: after the 22:00 records were published (22:08), 4.5 hours after the first record of the recording.
NOW = datetime(2026, 11, 16, 22, 30, tzinfo=timezone.utc)
MARSEILLE = (43.3, 5.4)


def _set_now(sim_clock: SimClock, when: datetime) -> None:
    sim_clock.now = when.timestamp() - SimClock.EPOCH


def _client(seen: list[httpx.Request], status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=PAYLOAD) if status == 200 else httpx.Response(status, text=f"error for {SAMPLE_KEY}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_key_shape_and_redaction() -> None:
    assert meteofrance_buoys_key_from_env({}) == (None, None)
    key, problem = meteofrance_buoys_key_from_env({"VIGIE_METEOFRANCE_API_KEY": "short"})
    assert key is None and "short" not in (problem or "")
    assert meteofrance_buoys_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}) == (SAMPLE_KEY, None)
    assert SAMPLE_KEY not in MeteoFranceBuoysCollector(SAMPLE_KEY).redact(f"401 for {SAMPLE_KEY}")


def test_names() -> None:
    assert display_name("BOUEE_GASCOGNE") == "Gascogne"
    assert display_name("BOUEE_PACA") == "PACA"
    assert display_name("BOUEE_VECCHIO") == "Porto-Vecchio"
    assert display_name("Some buoy") == "Some buoy"


def test_the_recording_keeps_the_latest_record_of_each_buoy_converted() -> None:
    records = parse_buoys(PAYLOAD)
    assert len(PAYLOAD) == 41 and len(records) == 10
    gascogne = records["6200001"]
    assert gascogne.name == "Gascogne" and (gascogne.lat, gascogne.lon) == (45.24, -5.01)
    assert datetime.fromtimestamp(gascogne.measured_utc, tz=timezone.utc) == datetime(2026, 11, 16, 22, tzinfo=timezone.utc)
    assert (gascogne.wave_height_m, gascogne.wave_period_s, gascogne.wave_from_deg) == (4.0, 7.0, 257)
    assert (gascogne.sea_temp_c, gascogne.air_temp_c) == (17.0, 19.5)  # 290.15 K and 292.65 K
    assert (gascogne.wind_speed_kmh, gascogne.wind_gust_kmh, gascogne.wind_from_deg) == (58.0, 76.0, 220)  # 16.1 and 21.1 m/s
    assert gascogne.pressure_hpa == 1000.5
    # Sardinia gave no wave: null, never a stand-in; its last record is the one of 20:00.
    sardaigne = records["6101035"]
    assert sardaigne.wave_height_m is None and sardaigne.wave_period_s is None and sardaigne.wave_from_deg is None
    assert sardaigne.sea_temp_c == 20.5


def test_a_row_without_an_id_a_place_or_a_date_is_skipped_and_another_shape_is_refused() -> None:
    good = dict(PAYLOAD[0])
    rows = [good, {**good, "geo_id_wmo": ""}, {**good, "geo_id_wmo": "1", "lat": None}, {**good, "geo_id_wmo": "2", "validity_time": "x"}]
    assert list(parse_buoys(rows)) == [good["geo_id_wmo"]]
    calm = parse_buoys([{**good, "ff": 0, "dd": 0, "tmer": 400.0, "haut_vag": -1, "per_moy_vag": 0}])[good["geo_id_wmo"]]
    assert calm.wind_from_deg is None and calm.sea_temp_c is None and calm.wave_height_m is None and calm.wave_period_s is None
    with pytest.raises(ValueError):
        parse_buoys({"features": []})


@pytest.mark.asyncio
async def test_one_cached_request_with_the_key_in_the_header_and_six_hours_asked(sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    seen: list[httpx.Request] = []
    collector = MeteoFranceBuoysCollector(SAMPLE_KEY, http_client=_client(seen))
    assert len((await collector.fetch_latest()).records) == 10
    request = seen[0]
    assert str(request.url).split("?")[0] == BUOYS_URL
    assert dict(request.url.params) == {"format": "json", "date_debut": "2026-11-16T16:00:00Z"}
    assert request.headers["apikey"] == SAMPLE_KEY and SAMPLE_KEY not in str(request.url)
    await collector.fetch_latest()  # inside the 30 minutes: no new request
    assert len(seen) == 1
    sim_clock.advance(1801)
    await collector.fetch_latest()
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_nearest_first_old_records_left_out_and_the_radius(sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    collector = MeteoFranceBuoysCollector(SAMPLE_KEY, http_client=_client([]))
    now = NOW.timestamp()
    near = await sea_buoys(collector, now, MARSEILLE)
    assert near.status == "ok" and near.count == 10
    assert [b.name for b in near.buoys[:3]] == ["Lion", "PACA", "Provence"]
    assert near.buoys[0].distance_km == 145.0 and near.buoys[0].age_minutes == 30
    assert near.buoys[-1].name == "Antilles" and near.attribution.startswith("Source: Météo-France")

    within = await sea_buoys(collector, now, MARSEILLE, radius_km=200.0)
    assert [b.name for b in within.buoys] == ["Lion", "PACA", "Provence", "Azur"]

    plain = await sea_buoys(collector, now)
    assert plain.buoys[0].name == "Gascogne" and all(b.distance_km is None for b in plain.buoys)  # north to south

    # Three and a half hours later every record is older than `max_age_hours`: none is listed.
    later = await sea_buoys(collector, now + 3 * 3600 + 1801)
    assert later.status == "ok" and later.count == 0


@pytest.mark.asyncio
async def test_without_a_key_or_with_an_error_the_answer_says_so(sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    assert (await sea_buoys(None, NOW.timestamp())).status == "not_configured"
    failing = MeteoFranceBuoysCollector(SAMPLE_KEY, http_client=_client([], status=503))
    answer = await sea_buoys(failing, NOW.timestamp())
    assert answer.status == "unavailable" and answer.buoys == []


def test_the_service_lists_the_collector_only_with_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIGIE_METEOFRANCE_API_KEY", raising=False)
    service = make_service(tmp_path / "nokey.db")
    assert service.meteofrance_buoys is None
    off = {s["provider_id"]: s for s in service.disabled_sources()}
    assert off["meteofrance_buoys"]["domain"] == "marine" and "VIGIE_METEOFRANCE_API_KEY" in off["meteofrance_buoys"]["reason"]

    monkeypatch.setenv("VIGIE_METEOFRANCE_API_KEY", SAMPLE_KEY)
    keyed = make_service(tmp_path / "key.db")
    assert isinstance(keyed.meteofrance_buoys, MeteoFranceBuoysCollector)
    assert "meteofrance_buoys" in {c.provider_id for c in keyed.get_all_collectors()}
    assert SAMPLE_KEY not in str(keyed.get_providers_health())


def test_http_route(world: World, sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    world.aggregator.meteofrance_buoys = MeteoFranceBuoysCollector(SAMPLE_KEY, http_client=_client([]))
    r = world.client.get(f"/v1/sea/buoys?near_lat={MARSEILLE[0]}&near_lon={MARSEILLE[1]}&radius_km=160&limit=5")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "public, max-age=600"
    body = r.json()
    assert (body["status"], body["count"], body["max_age_hours"]) == ("ok", 2, 3.0)
    assert [b["name"] for b in body["buoys"]] == ["Lion", "PACA"]
    assert body["buoys"][0]["measured_at"] == "2026-11-16T22:00:00Z" and body["buoys"][0]["source"] == "meteofrance"
    assert world.client.get("/v1/sea/buoys?near_lat=43").status_code == 422
    assert world.client.get("/v1/sea/buoys?radius_km=10").status_code == 422

    world.aggregator.meteofrance_buoys = None
    assert world.client.get("/v1/sea/buoys").json()["status"] == "not_configured"
