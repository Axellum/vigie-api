"""The aurora: SWPC's four products (recorded answers in ``tests/fixtures/swpc/``), the estimate of ADR 0013, the two routes."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.collectors.aurora import (
    KpInterval,
    SWPCAlertsCollector,
    SWPCKpForecastCollector,
    SWPCOvationCollector,
    SWPCSolarWindCollector,
)
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector
from app.services import aurora_method as method
from app.services.aurora import best_window, estimate_night, oval_bands, ovation_nearby
from app.services.magnetic import magnetic_latitude
from tests.helpers import Provider, make_service

FIXTURES = Path(__file__).parent / "fixtures" / "swpc"


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def ovation_payload() -> dict[str, Any]:
    with gzip.open(FIXTURES / "ovation_aurora_latest.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------------------------------------
# The four SWPC files, as recorded on 2026-10-28
# ---------------------------------------------------------------------------------------------------------------------------


def test_the_ovation_grid_is_read_cell_by_cell_with_its_times() -> None:
    grid = SWPCOvationCollector.parse(ovation_payload())

    assert grid.observation_utc == utc("2026-10-28T20:17:00")
    assert grid.forecast_utc == utc("2026-10-28T21:26:00")
    assert grid.max_value == 27
    assert grid.value_at(-90, 0) == 6 and grid.value_at(-88, 0) == 8  # the first rows of the file: [0, -90, 6], [0, -88, 8]
    assert grid.value_at(48.85, 2.35) == 0  # nothing over Paris on a quiet evening
    assert len(grid.cells_at_least(10)) == 5700


def test_an_ovation_file_without_times_or_cells_is_refused() -> None:
    payload = ovation_payload()
    with pytest.raises(ValueError):
        SWPCOvationCollector.parse({**payload, "Forecast Time": None})
    with pytest.raises(ValueError):
        SWPCOvationCollector.parse({**payload, "coordinates": payload["coordinates"][:1000]})


def test_the_kp_file_keeps_the_mark_swpc_gives_each_interval() -> None:
    intervals = SWPCKpForecastCollector.parse(fixture("noaa-planetary-k-index-forecast.json"))

    assert len(intervals) == 81
    assert intervals[0] == KpInterval(utc("2026-10-21T00:00:00"), 3.67, "observed", None)
    assert intervals[-1] == KpInterval(utc("2026-10-31T00:00:00"), 5.0, "predicted", "G1")
    assert {i.kind for i in intervals} == {"observed", "estimated", "predicted"}


def test_the_older_list_of_lists_layout_of_the_kp_file_is_read_too() -> None:
    payload = [["time_tag", "kp", "observed", "noaa_scale"], ["2026-10-31 00:00:00", "5.00", "predicted", "G1"]]
    assert SWPCKpForecastCollector.parse(payload) == [KpInterval(utc("2026-10-31T00:00:00"), 5.0, "predicted", "G1")]
    with pytest.raises(ValueError):
        SWPCKpForecastCollector.parse([["time_tag", "kp", "observed", "noaa_scale"], ["2026-10-31 00:00:00", None, "predicted", None]])


def test_the_solar_wind_is_the_latest_row_with_a_speed() -> None:
    wind = SWPCSolarWindCollector.parse(fixture("propagated-solar-wind-1-hour.json"))
    rows = fixture("propagated-solar-wind-1-hour.json")
    last = rows[-1]

    assert wind.measured_utc == utc(last[0].replace("Z", ""))
    assert wind.speed_km_s == round(last[1], 1) and wind.density_cm3 == last[2] and wind.bz_nt == last[6] and wind.bt_nt == last[7]
    assert wind.propagated_utc == utc(last[11].replace("Z", ""))


def test_the_messages_keep_swpc_s_words() -> None:
    messages = SWPCAlertsCollector.parse(fixture("alerts.json")).messages
    raw = fixture("alerts.json")

    assert len(messages) == len(raw) == 89
    watch = next(m for m in messages if m.product_id == "A20F" and m.serial == "1133")
    assert watch.code == "WATA20"
    assert watch.headline == "WATCH: Geomagnetic Storm Category G1 Predicted"
    assert watch.message == raw[0]["message"].replace("\r\n", "\n").replace("\r", "\n").strip()
    assert messages == sorted(messages, key=lambda m: m.issued_utc, reverse=True)


@pytest.mark.asyncio
async def test_a_file_that_did_not_change_is_not_downloaded_again() -> None:
    body = (FIXTURES / "noaa-planetary-k-index-forecast.json").read_bytes()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-Modified-Since") == "Wed, 28 Oct 2026 20:32:04 GMT":
            return httpx.Response(304)
        return httpx.Response(200, content=body, headers={"Last-Modified": "Wed, 28 Oct 2026 20:32:04 GMT"})

    provider = Provider(answer=answer)
    collector = SWPCKpForecastCollector(http_client=provider.client())
    first = await collector.fetch()
    again = await collector.fetch(force_refresh=True)

    assert again == first and provider.count == 2
    assert provider.requests[1].headers["If-Modified-Since"] == "Wed, 28 Oct 2026 20:32:04 GMT"


# ---------------------------------------------------------------------------------------------------------------------------
# The estimate (ADR 0013)
# ---------------------------------------------------------------------------------------------------------------------------


def test_the_magnetic_latitude_follows_the_field_not_the_dipole() -> None:
    # The apex latitudes traced by tools/build_magnetic_latitudes.py, interpolated; the dipole would say about 50 for Paris.
    assert 42.5 < magnetic_latitude(48.85, 2.35) < 44.5
    assert 65.0 < magnetic_latitude(69.65, 18.96) < 69.0  # Tromso
    assert 54.0 < magnetic_latitude(46.9, -80.1) < 57.0  # Ontario, where the Aurorasaurus report says 56.6
    assert magnetic_latitude(-43.5, 172.6) < -45.0  # Christchurch, southern magnetic hemisphere


def test_the_factors_of_the_estimate() -> None:
    assert method.darkness(-20.0) == 1.0 and method.darkness(-6.0) == 0.0 and method.darkness(-12.0) == pytest.approx(0.5)
    assert method.moon_factor(-5.0, 1.0) == 1.0 and method.moon_factor(30.0, 1.0) == pytest.approx(1 - method.MOON_WEIGHT)
    assert method.cloud_factor(None) is None and method.cloud_factor(75.0) == pytest.approx(0.25)
    edge = method.edge_latitude(5.0)
    assert method.activity(edge + 1, 5.0) == 1.0
    assert method.activity(edge - method.REACH_KM / method.KM_PER_DEGREE - 0.1, 5.0) == 0.0
    assert method.activity(edge - 0.5 * method.REACH_KM / method.KM_PER_DEGREE, 5.0) == pytest.approx(0.5)
    assert [method.level_of(x) for x in (0.0, 0.01, 0.05, 0.2, 0.5, 1.0)] == ["very_unlikely", "very_unlikely", "unlikely", "possible", "likely", "likely"]


def kp_all(kp: float, start: str = "2026-12-14T00:00:00", days: int = 3, kind: str = "predicted") -> list[KpInterval]:
    return [KpInterval(utc(start) + 3 * 3600 * i, kp, kind, None) for i in range(days * 8)]


def test_a_winter_night_under_the_oval_is_likely_and_a_quiet_one_in_paris_is_not() -> None:
    now = utc("2026-12-14T15:00:00")
    tromso, _ = estimate_night(69.65, 18.96, now, kp_all(4.0), clouds=[])
    paris, _ = estimate_night(48.85, 2.35, now, kp_all(2.0), clouds=[])

    assert tromso is not None and tromso.level == "likely" and tromso.best is not None
    assert paris is not None and paris.level == "very_unlikely" and paris.best is None
    assert not tromso.clouds_known and all(h.chance is None and h.level is None for h in tromso.hours)
    assert all(h.darkness > 0 for h in tromso.hours)


def test_the_clouds_and_the_window() -> None:
    now = utc("2026-12-14T15:00:00")
    hours = [now - now % 3600 + 3600 * i for i in range(40)]
    clouds = [(t, 100.0 if datetime.fromtimestamp(t, timezone.utc).hour < 22 else 0.0) for t in hours]  # clears at 22 UTC
    night, _ = estimate_night(69.65, 18.96, now, kp_all(4.0), clouds=clouds)

    assert night is not None and night.clouds_known and night.best is not None and night.best.with_clouds
    assert datetime.fromtimestamp(night.best.start_utc, timezone.utc).hour >= 22
    cloudy = [h for h in night.hours if h.cloud_cover_pct == 100.0]
    assert cloudy and all(h.chance == 0.0 and h.level == "very_unlikely" for h in cloudy)


def test_no_estimate_without_a_dark_night_or_without_kp() -> None:
    midsummer = utc("2026-06-21T10:00:00")
    estimate, reason = estimate_night(78.2, 15.6, midsummer, kp_all(6.0, "2026-06-21T00:00:00"), clouds=[])  # Svalbard
    assert estimate is None and reason is not None and "Sun" in reason
    estimate, reason = estimate_night(48.85, 2.35, utc("2026-12-14T15:00:00"), None, clouds=[])
    assert estimate is None and reason == "SWPC's Kp could not be read"


def test_the_best_window_is_none_below_five_percent() -> None:
    now = utc("2026-12-14T15:00:00")
    night, _ = estimate_night(36.7, -4.4, now, kp_all(1.0), clouds=[])  # Malaga, quiet
    assert night is not None and best_window(night.hours, with_clouds=False) is None


def test_the_ovation_bands_cover_the_cells_and_stay_on_the_map() -> None:
    grid = SWPCOvationCollector.parse(ovation_payload())
    features = oval_bands(grid)

    assert [f.properties["min"] for f in features] == [5, 10, 20]  # the grid tops at 27: no band of 40 or more
    def positions(value: Any) -> list[list[float]]:
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            return [value]
        return [p for item in (value or []) for p in positions(item)]

    points = [p for f in features for p in positions(f.geometry["coordinates"])]
    assert points and all(-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0 for lon, lat in points)
    nearby = ovation_nearby(grid, 69.65, 18.96)
    assert nearby.value_here == grid.value_at(69.65, 18.96) and nearby.strongest_within_1000_km is not None
    assert nearby.strongest_distance_km is not None and nearby.strongest_distance_km <= 1000


# ---------------------------------------------------------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------------------------------------------------------


def sky_service(tmp_path: Path, clouds: bool = True) -> Any:
    ovation_body = gzip.decompress((FIXTURES / "ovation_aurora_latest.json.gz").read_bytes())
    files = {
        "ovation_aurora_latest.json": ovation_body,
        "noaa-planetary-k-index-forecast.json": (FIXTURES / "noaa-planetary-k-index-forecast.json").read_bytes(),
        "propagated-solar-wind-1-hour.json": (FIXTURES / "propagated-solar-wind-1-hour.json").read_bytes(),
        "alerts.json": (FIXTURES / "alerts.json").read_bytes(),
    }
    swpc = Provider(answer=lambda request: httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[-1]]))
    start = utc("2026-10-28T20:00:00")
    weather = Provider(
        {
            "utc_offset_seconds": 3600,
            "daily": {"time": ["2026-10-28"]},
            "hourly": {
                "time": [datetime.fromtimestamp(start + 3600 * (i + 1), timezone.utc).strftime("%Y-%m-%dT%H:%M") for i in range(48)],
                "cloud_cover": [20.0] * 48,
            },
        }
    )
    extra: dict[str, Any] = {
        "swpc_ovation": SWPCOvationCollector(http_client=swpc.client()),
        "swpc_kp_forecast": SWPCKpForecastCollector(http_client=swpc.client()),
        "swpc_solar_wind": SWPCSolarWindCollector(http_client=swpc.client()),
        "swpc_alerts": SWPCAlertsCollector(http_client=swpc.client()),
    }
    if clouds:
        extra["open_meteo"] = OpenMeteoCollector(http_client=weather.client())
    service = make_service(tmp_path / "sky.db", **extra)
    return service, swpc, weather


@pytest.fixture
def sky_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.main as main
    from app import clock

    service, swpc, weather = sky_service(tmp_path)
    monkeypatch.setattr(main, "aggregator", service)
    monkeypatch.setattr(clock, "_wall", lambda: float(utc("2026-10-28T21:00:00")))
    return TestClient(main.app), swpc, weather


def test_the_route_without_a_place_gives_swpc_s_data(sky_client: Any) -> None:
    client, _, weather = sky_client
    body = client.get("/v1/sky/aurora").json()

    assert body["oval"] == {"observation_utc": utc("2026-10-28T20:17:00"), "forecast_utc": utc("2026-10-28T21:26:00"), "max_value": 27}
    assert body["kp_now"]["kind"] == "estimated" and body["kp_max_next_24h"]["kp"] >= body["kp_now"]["kp"]
    assert all("Geomagnetic" in m["headline"] for m in body["messages"])
    assert body["place"] is None and body["unavailable"] == []
    assert "NOAA Space Weather Prediction Center" in body["attribution"]
    assert weather.count == 0  # no weather request without a place


def test_the_route_with_a_place_gives_the_estimate_and_says_it_is_ours(sky_client: Any) -> None:
    client, _, weather = sky_client
    body = client.get("/v1/sky/aurora", params={"lat": 69.65, "lon": 18.96}).json()
    place = body["place"]

    assert "place.estimate" in body["derived"]
    assert place["estimate"]["method"].startswith("Vigie estimate (ADR 0013")
    assert place["estimate"]["clouds_known"] is True
    assert all(h["cloud_cover_pct"] == 20.0 for h in place["estimate"]["hours"])
    assert place["ovation"]["forecast_utc"] == utc("2026-10-28T21:26:00")
    assert "Open-Meteo" in body["attribution"]
    assert weather.count == 1 and "hourly" in weather.query() and "current" not in weather.query()


def test_half_a_position_is_refused(sky_client: Any) -> None:
    client, _, _ = sky_client
    assert client.get("/v1/sky/aurora", params={"lat": 48.85}).status_code == 422


def test_the_map_route_gives_bands(sky_client: Any) -> None:
    client, _, _ = sky_client
    body = client.get("/v1/sky/aurora.geojson").json()

    assert body["type"] == "FeatureCollection" and body["levels"] == [5, 10, 20, 40, 60]
    assert [f["properties"]["min"] for f in body["features"]] == [5, 10, 20]
    assert body["forecast_utc"] == utc("2026-10-28T21:26:00")


def test_nothing_from_swpc_is_said_as_such(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main

    monkeypatch.setattr(main, "aggregator", make_service(tmp_path / "down.db", met_norway=MetNorwayCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))))))
    body = TestClient(main.app).get("/v1/sky/aurora", params={"lat": 48.85, "lon": 2.35}).json()

    assert body["oval"] is None and body["kp"] == [] and body["solar_wind"] is None and body["messages"] == []
    assert sorted(body["unavailable"]) == ["noaa_swpc_alerts", "noaa_swpc_kp_forecast", "noaa_swpc_ovation", "noaa_swpc_solar_wind"]
    assert body["place"]["estimate"] is None and body["place"]["no_estimate_reason"] == "SWPC's Kp could not be read"
    assert body["attribution"] is None
