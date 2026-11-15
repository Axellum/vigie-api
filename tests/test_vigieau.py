"""Unit and route tests for VigiEau drought restrictions (recorded fixtures, zero network calls)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.attribution import DROUGHT, credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.vigieau import (
    VigiEauCollector,
    parse_departments_payload,
    parse_zones_v2_payload,
    to_point_status,
)
from app.models.drought import DroughtDepartmentsResponse, DroughtResponse
from app.models.schemas import CircuitState, PointResponse, SeverityLevel
from tests.conftest import World
from tests.helpers import Provider, SimClock, make_service

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vigieau"


def _load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_paris_fixture_preserves_native_levels_and_availability() -> None:
    parsed = parse_zones_v2_payload(_load_fixture("zones_v2_paris.json"), snapped_lat=48.86, snapped_lon=2.36)
    assert parsed.status == "available"
    assert len(parsed.zones) == 3
    assert {z.water_type for z in parsed.zones} == {"AEP", "SUP", "SOU"}
    assert all(z.level == "vigilance" for z in parsed.zones)
    assert all(z.department == "75" for z in parsed.zones)
    assert parsed.availability["AEP"].freshness == "updating"
    assert parsed.zones[0].decree is not None and parsed.zones[0].decree.pdf_url is not None

    status = to_point_status(parsed)
    assert status.status == "available"
    assert status.max_level == "vigilance"
    # Vigilance is awareness only (no mandatory restriction).
    assert status.restrictions_in_force is False
    assert status.attribution == credit_text(*DROUGHT)


def test_parse_toulouse_and_nantes_fixtures_handle_nulls_and_distinct_availability() -> None:
    toulouse = parse_zones_v2_payload(_load_fixture("zones_v2_toulouse.json"))
    assert toulouse.status == "available"
    assert len(toulouse.zones) == 2
    assert toulouse.zones[0].code is None
    assert toulouse.zones[0].sandre_id is None
    assert toulouse.availability["SOU"].status == "unavailable"
    toulouse_status = to_point_status(toulouse)
    assert toulouse_status.max_level == "alerte_renforcee"
    assert toulouse_status.restrictions_in_force is True

    nantes = parse_zones_v2_payload(_load_fixture("zones_v2_nantes.json"))
    assert nantes.availability["SOU"].status == "confirmed_none"
    assert to_point_status(nantes).max_level == "alerte"
    assert to_point_status(nantes).restrictions_in_force is True


def test_parse_unavailable_vs_confirmed_none() -> None:
    london = parse_zones_v2_payload(_load_fixture("zones_v2_unavailable_london.json"))
    assert london.status == "unavailable"
    assert london.zones == ()
    london_status = to_point_status(london)
    assert london_status.restrictions_in_force is None
    assert london_status.as_of is None

    confirmed_none = parse_zones_v2_payload(
        {
            "zones": [],
            "availability": {
                wt: {"status": "confirmed_none", "asOf": "2026-11-12T14:08:00.484Z", "sourceRevision": "169352", "officialUrl": None}
                for wt in ("AEP", "SUP", "SOU")
            },
        }
    )
    assert confirmed_none.status == "confirmed_none"
    status = to_point_status(confirmed_none)
    assert status.restrictions_in_force is False
    assert status.max_level is None


def test_parse_departments_fixture_covers_all_101_departments() -> None:
    deps = parse_departments_payload(_load_fixture("departements.json"))
    assert len(deps) == 101
    codes = {d.code for d in deps}
    assert {"75", "31", "44", "971", "972", "973", "974", "976"}.issubset(codes)
    ain = next(d for d in deps if d.code == "01")
    assert ain.max_level == "crise"
    assert ain.restrictions_in_force is True


@pytest.mark.asyncio
async def test_collector_grid_cache_coalescing_and_local_filtering(tmp_path: Path, sim_clock: SimClock) -> None:
    paris_payload = _load_fixture("zones_v2_paris.json")
    deps_payload = _load_fixture("departements.json")

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/departements"):
            return httpx.Response(200, json=deps_payload)
        return httpx.Response(200, json=paris_payload)

    provider = Provider(answer=answer)
    collector = VigiEauCollector(http_client=provider.client())
    service = make_service(tmp_path / "vigieau.db", vigieau=collector)

    # Two nearby points in the same 0.02° cell + concurrent callers coalesce into 1 upstream GET.
    res1, res2, res3 = await asyncio.gather(
        service.get_drought_zones(lat=48.8566, lon=2.3522, water_type="AEP", profile="particulier"),
        service.get_drought_zones(lat=48.8580, lon=2.3510, water_type="SUP", profile="exploitation"),
        service.get_drought_zones(lat=48.8566, lon=2.3522),
    )
    assert provider.count == 1
    assert provider.query(0) == {"lat": "48.86", "lon": "2.36"}  # no profil or zoneType sent upstream
    assert len(res1.zones) == 1 and res1.zones[0].water_type == "AEP"
    assert all(u.concerns_individual is True for u in res1.zones[0].usages)
    assert len(res2.zones) == 1 and res2.zones[0].water_type == "SUP"
    assert all(u.concerns_farm is True for u in res2.zones[0].usages)
    assert len(res3.zones) == 3

    # Departmental queries also share a single cached upstream call while fresh.
    d_all = await service.get_drought_departments()
    d_crise = await service.get_drought_departments(min_level="crise")
    d_ain = await service.get_drought_departments(department="01")
    assert provider.count == 2
    assert d_all.count == 101
    assert d_crise.count >= 1 and all(d.max_level == "crise" for d in d_crise.departments)
    assert d_ain.count == 1 and d_ain.departments[0].code == "01"

    # After TTL expires (3600 s), the next request refreshes once.
    sim_clock.advance(3601)
    await service.get_drought_zones(lat=48.8566, lon=2.3522)
    assert provider.count == 3


@pytest.mark.asyncio
async def test_outside_coverage_makes_zero_upstream_calls_and_french_point_keeps_alert_level(
    tmp_path: Path,
) -> None:
    paris_payload = _load_fixture("zones_v2_paris.json")
    provider = Provider(payload=paris_payload)
    service = make_service(tmp_path / "coverage.db", vigieau=VigiEauCollector(http_client=provider.client()))

    outside = await service.get_drought_zones(lat=51.5074, lon=-0.1278)
    assert outside.status == "outside_coverage"
    assert provider.count == 0

    pt_outside = await service.build_point_response(lat=35.6762, lon=139.6503)
    assert pt_outside.drought is None
    assert provider.count == 0

    pt_paris = await service.build_point_response(lat=48.8566, lon=2.3522)
    assert pt_paris.drought is not None
    assert pt_paris.drought.max_level == "vigilance"
    assert pt_paris.drought.restrictions_in_force is False
    assert pt_paris.local_alert_level == SeverityLevel.GREEN
    assert provider.count == 1


@pytest.mark.asyncio
async def test_commune_409_400_and_circuit_breaker_handling(tmp_path: Path, sim_clock: SimClock) -> None:
    ambiguous_body = _load_fixture("zones_v2_commune_ambiguous.json")
    paris_payload = _load_fixture("zones_v2_paris.json")
    mode = {"behavior": "409"}

    def answer(request: httpx.Request) -> httpx.Response:
        if mode["behavior"] == "409":
            return httpx.Response(409, json=ambiguous_body)
        if mode["behavior"] == "400":
            return httpx.Response(400, json={"statusCode": 400, "message": "Unknown commune"})
        if mode["behavior"] == "503":
            return httpx.Response(503)
        return httpx.Response(200, json=paris_payload)

    collector = VigiEauCollector(http_client=Provider(answer=answer).client())
    service = make_service(tmp_path / "errors.db", vigieau=collector)

    amb = await service.get_drought_zones(commune="31555")
    assert amb.status == "ambiguous_commune"
    assert amb.ambiguous_water_types == ["SUP"]
    assert collector.consecutive_failures == 0
    assert collector.circuit_state == CircuitState.CLOSED

    mode["behavior"] = "400"
    bad = await service.get_drought_zones(commune="00000")
    assert bad.status == "invalid_query"
    assert collector.consecutive_failures == 0
    assert collector.circuit_state == CircuitState.CLOSED

    # Populate point cache, expire it, then verify 503 serves stale cache gracefully.
    mode["behavior"] = "200"
    fresh = await service.get_drought_zones(lat=48.8566, lon=2.3522)
    assert not fresh.stale
    sim_clock.advance(3601)
    mode["behavior"] = "503"
    stale = await service.get_drought_zones(lat=48.8566, lon=2.3522)
    assert stale.stale is True
    assert stale.max_level == "vigilance"

    # Uncached points fail until circuit opens.
    for lat in (45.76, 43.29, 47.21):
        with pytest.raises(CollectorUnavailableError):
            await collector.fetch_point(lat, 4.83)
    assert collector.circuit_state == CircuitState.OPEN


def test_drought_routes_and_validation(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = world.client

    # /v1/point includes compact drought summary for Paris
    point = PointResponse.model_validate(client.get("/v1/point?lat=48.8566&lon=2.3522").json())
    assert point.drought is not None
    assert point.drought.max_level == "vigilance"
    assert point.drought.attribution == credit_text(*DROUGHT)

    # /v1/drought parameter validation
    assert client.get("/v1/drought").status_code == 422
    assert client.get("/v1/drought?lat=48.85").status_code == 422
    assert client.get("/v1/drought?lat=48.85&lon=2.35&commune=75056").status_code == 422
    assert client.get("/v1/drought?commune=ABC").status_code == 422

    # /v1/drought?lat=...&lon=... filtered locally
    resp = client.get("/v1/drought?lat=48.8566&lon=2.3522&water_type=AEP&profile=particulier")
    assert resp.status_code == 200
    body = DroughtResponse.model_validate(resp.json())
    assert body.status == "available"
    assert len(body.zones) == 1
    assert body.zones[0].water_type == "AEP"

    # /v1/drought outside French coverage
    outside = DroughtResponse.model_validate(client.get("/v1/drought?lat=51.5074&lon=-0.1278").json())
    assert outside.status == "outside_coverage"

    # /v1/drought/departments backed by a local MockTransport
    deps_payload = _load_fixture("departements.json")
    mock_collector = VigiEauCollector(http_client=Provider(payload=deps_payload).client())
    monkeypatch.setattr(app_main.aggregator, "vigieau", mock_collector)

    dep_resp = client.get("/v1/drought/departments?min_level=crise&region=Auvergne")
    assert dep_resp.status_code == 200
    dep_body = DroughtDepartmentsResponse.model_validate(dep_resp.json())
    assert dep_body.count >= 1
    assert all(d.max_level == "crise" for d in dep_body.departments)
    assert all("Auvergne" in (d.region or "") for d in dep_body.departments)

    # /v1/drought upstream failure surfaces HTTP 503
    down_collector = VigiEauCollector(http_client=Provider(answer=lambda _: httpx.Response(503)).client())
    monkeypatch.setattr(app_main.aggregator, "vigieau", down_collector)
    assert client.get("/v1/drought?lat=48.8566&lon=2.3522").status_code == 503
    assert client.get("/v1/drought/departments").status_code == 503
