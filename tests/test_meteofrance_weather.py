"""Weather of France from the Météo-France models through a vigie-meteo gateway, and the Météo-France UV index.

The gateway answers are shaped as the ones read on 2026-11-18 at Vannes (``GET /v1/forecast?models=meteofrance_seamless``, Open-Meteo's
format): the pressure and the weather code come from ARPEGE, the UV index and the rain probability are ``null``, and only four days are
complete. The UV file is a crop of the ``UVQ`` GRIB2 of the run of 2026-11-18 03:00 UTC (Brittany, 47 to 48 N, 3.5 to 2 W). No test touches
the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import numpy as np
import pytest

from app.attribution import credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.meteofrance_uv import (
    PACKAGE_URL,
    MeteoFranceUvCollector,
    decode_uvq,
    meteofrance_uv_key_from_env,
    uv_now,
)
from app.collectors.meteofrance_weather import (
    MeteoFranceWeatherCollector,
    in_france_box,
    merge_france_bundle,
    meteo_weather_enabled_from_env,
)
from app.collectors.weather import OpenMeteoCollector
from tests import samples
from tests.helpers import Provider, SimClock, forecast_provider, make_service

VANNES = (47.66, -2.76)
OSLO = (59.91, 10.75)
BARCELONA = (41.39, 2.17)  # inside the box: the Météo-France models cover it
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "meteofrance_uv" / "uvq-brittany-2026-11-18.grib2"
SAMPLE_KEY = "A" * 40
REAL_HOST = "https://public-api.meteofrance.fr/previnum/DPPaquetENVIRONNEMENT"
NULLS = [None] * 6


def gateway_now() -> dict[str, Any]:
    """The ``current`` and ``minutely_15`` blocks the gateway answered at Vannes on 2026-11-18 at 07:45 (local)."""
    return {
        "utc_offset_seconds": 3600,
        "current": {
            "time": "2026-11-18T07:45",
            "temperature_2m": 13.3,
            "apparent_temperature": 9.2,
            "relative_humidity_2m": 76,
            "pressure_msl": 995.6,
            "wind_speed_10m": 28.8,
            "wind_gusts_10m": 51.1,
            "wind_direction_10m": 254,
            "precipitation": 0.0,
            "weather_code": 1,
        },
        "minutely_15": {"time": ["2026-11-18T07:45"] * 4, "precipitation": [0.0, 0.0, 0.1, 0.0]},
    }


def gateway_daily() -> dict[str, Any]:
    """The ``daily`` block of the same answer: four complete days, then ``null``."""
    return {
        "utc_offset_seconds": 3600,
        "daily": {
            "time": [f"2026-11-{day}" for day in range(18, 28)],
            "weather_code": [61, 51, 51, 63, *NULLS],
            "temperature_2m_max": [15.5, 13.9, 13.5, 12.3, *NULLS],
            "temperature_2m_min": [12.2, 11.5, 8.0, 7.2, *NULLS],
            "precipitation_sum": [4.7, 17.4, 2.25, 13.6, *NULLS],
            "wind_speed_10m_max": [29.6, 33.1, 40.0, 26.7, *NULLS],
        },
    }


def gateway_provider(*, now: Optional[dict[str, Any]] = None, daily: Optional[dict[str, Any]] = None, status: int = 200) -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json=(now or gateway_now()) if "current" in request.url.params else (daily or gateway_daily()))

    return Provider(answer=answer)


def public_days() -> dict[str, Any]:
    """Open-Meteo's public daily part: ten days with a rain probability and a UV maximum, and the hourly cloud cover."""
    parsed = OpenMeteoCollector.parse_daily(
        {
            "utc_offset_seconds": 3600,
            "daily": {
                "time": [f"2026-11-{day}" for day in range(18, 28)],
                "weather_code": [3] * 10,
                "temperature_2m_max": [14.0 + i for i in range(10)],
                "temperature_2m_min": [9.0 + i for i in range(10)],
                "precipitation_sum": [1.0] * 10,
                "precipitation_probability_max": [10 * (i + 1) for i in range(10)],
                "wind_speed_10m_max": [20.0] * 10,
                "uv_index_max": [0.8 + i / 10 for i in range(10)],
            },
            "hourly": {"time": ["2026-11-18T08:00"], "cloud_cover": [40]},
        }
    )
    return parsed


def now_epoch(hour_utc: int = 11) -> float:
    return datetime(2026, 11, 18, hour_utc, 0, tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------- the settings


def test_the_gateway_weather_is_on_unless_switched_off() -> None:
    assert meteo_weather_enabled_from_env({}) is True
    assert meteo_weather_enabled_from_env({"VIGIE_METEO_GATEWAY_WEATHER": "1"}) is True
    for raw in ("0", "false", "No", " off "):
        assert meteo_weather_enabled_from_env({"VIGIE_METEO_GATEWAY_WEATHER": raw}) is False


def test_the_box_is_france_and_its_neighbours_in_the_models_domain() -> None:
    assert in_france_box(*VANNES) and in_france_box(42.0, 9.0) and in_france_box(*BARCELONA)
    assert not in_france_box(*OSLO)
    assert not in_france_box(40.71, -74.0)  # New York
    assert not in_france_box(16.2, -61.5)  # Guadeloupe: the models are asked for the mainland only


def test_the_uv_key_comes_from_the_environment_and_a_wrong_one_is_not_used() -> None:
    assert meteofrance_uv_key_from_env({}) == (None, None)
    assert meteofrance_uv_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}) == (SAMPLE_KEY, None)
    assert meteofrance_uv_key_from_env({"VIGIE_METEOFRANCE_UV_API_KEY": "B" * 40, "VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}) == ("B" * 40, None)
    key, problem = meteofrance_uv_key_from_env({"VIGIE_METEOFRANCE_API_KEY": "short"})
    assert key is None and problem is not None and "short" not in problem


# ---------------------------------------------------------------- the gateway


async def test_the_gateway_is_asked_for_the_blend_of_the_models_without_the_fields_they_lack() -> None:
    gateway = gateway_provider()
    collector = MeteoFranceWeatherCollector("http://gateway.test/", http_client=gateway.client())
    await collector.fetch_point(*VANNES)

    assert gateway.count == 2
    assert all(str(request.url).startswith("http://gateway.test/v1/forecast?") for request in gateway.requests)
    asked = "".join(request.url.params["models"] for request in gateway.requests)
    assert asked == "meteofrance_seamless" * 2
    variables = ",".join(request.url.params.get("current", "") + request.url.params.get("daily", "") for request in gateway.requests)
    assert "uv_index" not in variables and "probability" not in variables  # the models have neither: not asked for


async def test_a_point_is_read_once_per_cell_and_the_incomplete_days_are_left_out() -> None:
    gateway = gateway_provider()
    collector = MeteoFranceWeatherCollector("http://gateway.test", http_client=gateway.client())
    first = await collector.fetch_point(*VANNES)
    again = await collector.fetch_point(VANNES[0] + 0.01, VANNES[1] - 0.01)  # the same 0.05-degree cell

    assert gateway.count == 2 and again == first
    assert first["utc_offset_s"] == 3600
    current = first["current"]
    assert (current.temp_c, current.pressure_hpa, current.weather_code, current.wind_kmh) == (13.3, 995.6, 1, 28.8)
    assert current.uv_index is None  # not the models' to give
    assert first["rain_next_60m"] == [0.0, 0.0, 0.1, 0.0]
    assert [day.date for day in first["daily"]] == ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"]
    assert first["daily"][0].rain_probability_pct is None and first["daily"][0].uv_max is None
    assert (first["daily"][0].tmax, first["daily"][0].code, first["daily"][3].rain_mm) == (15.5, 61, 13.6)


async def test_an_answer_without_a_current_temperature_or_a_complete_day_is_refused() -> None:
    no_temperature = gateway_now()
    no_temperature["current"]["temperature_2m"] = None
    no_days = gateway_daily()
    no_days["daily"] = {key: ([None] * 10 if key != "time" else value) for key, value in no_days["daily"].items()}
    for gateway in (gateway_provider(now=no_temperature), gateway_provider(daily=no_days), gateway_provider(status=503)):
        collector = MeteoFranceWeatherCollector("http://gateway.test", http_client=gateway.client())
        with pytest.raises(CollectorUnavailableError):
            await collector.fetch_point(*VANNES)


async def test_an_answer_from_the_gateways_expired_cache_is_not_shown_as_current() -> None:
    stale = Provider(answer=lambda request: httpx.Response(200, json=gateway_now(), headers={"X-Cache-Status": "stale", "X-Cache-Age": "7200"}))
    collector = MeteoFranceWeatherCollector("http://gateway.test", http_client=stale.client())
    with pytest.raises(CollectorUnavailableError, match="expired cache"):
        await collector.fetch_point(*VANNES)

    fresh = Provider(answer=lambda request: httpx.Response(200, json=gateway_now() if "current" in request.url.params else gateway_daily(), headers={"X-Cache-Status": "fresh"}))
    assert (await MeteoFranceWeatherCollector("http://gateway.test", http_client=fresh.client()).fetch_point(*VANNES))["current"].temp_c == 13.3


# ---------------------------------------------------------------- the UV grid


def uv_grids() -> Any:
    return decode_uvq(FIXTURE.read_bytes())


def test_the_uv_grid_holds_today_and_tomorrow_for_the_real_sky_and_the_clear_sky() -> None:
    grids = uv_grids()
    assert sorted(grids.days) == ["2026-11-18", "2026-11-19"]
    assert grids.run_utc == int(datetime(2026, 11, 18, 3, tzinfo=timezone.utc).timestamp())
    assert grids.sample(*VANNES, "2026-11-18") == (1.18, 1.18)  # what the file gave at Vannes (the real file, whole grid)
    assert grids.sample(*VANNES, "2026-11-19") == (1.01, 1.15)  # a cloud tomorrow: the real sky is under the clear sky
    assert grids.sample(*VANNES, "2026-11-20") is None  # a day the file does not cover
    assert grids.sample(*OSLO, "2026-11-18") is None  # outside the box
    assert grids.sample(45.0, 3.0, "2026-11-18") is None  # inside the box, outside this crop


def test_a_missing_point_takes_its_neighbour_and_gives_up_after_one_ring() -> None:
    grids = uv_grids()
    day = grids.days["2026-11-18"]
    i = int(np.abs(grids.lats - VANNES[0]).argmin())
    j = int(np.abs(grids.lons - VANNES[1]).argmin())
    day.sky[i, j] = np.nan
    assert grids.sample(*VANNES, "2026-11-18") is not None  # the neighbour answers
    day.sky[i - 1 : i + 2, j - 1 : j + 2] = np.nan
    assert grids.sample(*VANNES, "2026-11-18") is None  # the whole ring is missing


def test_a_file_that_is_not_grib2_is_refused() -> None:
    with pytest.raises(ValueError):
        decode_uvq(b"<html>not a grid</html>")
    with pytest.raises(ValueError):
        decode_uvq(FIXTURE.read_bytes()[:40])
    with pytest.raises(ValueError, match="no daily UV index"):
        decode_uvq(b"")  # no message at all: nothing was cut, nothing is returned


def test_the_uv_index_of_the_hour_follows_the_sun_and_never_exceeds_the_maximum() -> None:
    day = datetime(2026, 11, 18, tzinfo=timezone.utc)
    hours = [uv_now(1.2, *VANNES, day.replace(hour=hour)) for hour in range(0, 24)]
    assert hours[0] == hours[3] == hours[21] == 0.0  # night
    assert max(hours) == hours[12] and max(hours) <= 1.2  # the highest Sun of the day at Vannes is around 13:00 local, 12:00 UTC
    assert hours[8] < hours[10] < hours[12] and hours[12] > hours[14] > hours[16]
    assert uv_now(0.0, *VANNES, day.replace(hour=12)) == 0.0


def test_at_solar_noon_the_index_is_the_maximum_of_the_day() -> None:
    noon = datetime(2026, 6, 21, 12, 10, tzinfo=timezone.utc)  # solar noon at 2.76 W is about 12:11 UTC
    assert uv_now(8.0, *VANNES, noon) == pytest.approx(8.0, abs=0.1)


class UvServer:
    """The two requests and the file of the UV API: the runs, the description of a run, and its GRIB2."""

    def __init__(self, runs: Optional[list[str]] = None, with_file: Optional[set[str]] = None) -> None:
        self.runs = runs if runs is not None else ["2026-11-18T00:00:00Z", "2026-11-18T03:00:00Z"]
        self.with_file = with_file if with_file is not None else set(self.runs)
        self.downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == SAMPLE_KEY and "apikey" not in str(request.url)  # the key goes in the header only
        url = str(request.url)
        if "productPHEALTH" in url:
            assert url.startswith(PACKAGE_URL + "/productPHEALTH?") and "time=001H051H" in url and "format=grib2" in url
            self.downloads += 1
            return httpx.Response(200, content=FIXTURE.read_bytes())
        run = request.url.params.get("referencetime")
        if run is None:
            return httpx.Response(200, json={"links": [{"href": f"{REAL_HOST}/models/PHEALTH/grids/EURW1S40/packages/UVQ?&referencetime={r}"} for r in self.runs]})
        links = [{"href": f"{REAL_HOST}/models/PHEALTH/grids/EURW1S40/packages/UVQ?&referencetime={run}"}]
        if run in self.with_file:
            # as the API writes it: the host of the API, no ``/v1`` in the path
            links.append({"href": f"{REAL_HOST}/models/PHEALTH/grids/EURW1S40/packages/UVQ/productPHEALTH?&referencetime={run}&time=001H051H&format=grib2"})
        return httpx.Response(200, json={"links": links})


def uv_collector(server: UvServer) -> MeteoFranceUvCollector:
    return MeteoFranceUvCollector(SAMPLE_KEY, http_client=Provider(answer=server).client())


async def test_the_newest_run_is_read_and_its_file_only_when_the_run_is_new(sim_clock: SimClock) -> None:
    server = UvServer()
    collector = uv_collector(server)
    grids = await collector.uv_grids()
    assert grids is not None and server.downloads == 1

    sim_clock.advance(3601)  # the list is read again, the run is the same: no new file
    assert await collector.uv_grids() is grids and server.downloads == 1

    server.runs.append("2026-11-18T06:00:00Z")
    server.with_file.add("2026-11-18T06:00:00Z")
    sim_clock.advance(3601)
    assert await collector.uv_grids() is not grids and server.downloads == 2


async def test_a_run_without_its_file_yet_gives_way_to_the_one_before(sim_clock: SimClock) -> None:
    server = UvServer(with_file={"2026-11-18T00:00:00Z"})  # the 03:00 run is listed, its file is not published
    grids = await uv_collector(server).uv_grids()
    assert grids is not None and server.downloads == 1


async def test_a_failing_uv_api_leaves_the_index_unknown_instead_of_raising(sim_clock: SimClock) -> None:
    collector = MeteoFranceUvCollector(SAMPLE_KEY, http_client=Provider(answer=lambda request: httpx.Response(503)).client())
    assert await collector.uv_grids() is None
    assert SAMPLE_KEY not in (collector.last_error or "")


# ---------------------------------------------------------------- the merge


def france(daily: bool = True) -> dict[str, Any]:
    now = OpenMeteoCollector.parse_now(gateway_now())
    days = [d for d in OpenMeteoCollector.parse_daily(gateway_daily())["daily_10d"] if d.tmax is not None]
    return {"utc_offset_s": 3600, "current": now["current"], "rain_next_60m": now["rain_next_60m"], "daily": days if daily else []}


def test_every_field_the_public_api_gave_is_still_there() -> None:
    public = public_days()
    bundle = merge_france_bundle(france(), public, uv_grids(), *VANNES, now_epoch(11))
    reference = OpenMeteoCollector.parse_response(
        {**gateway_now(), "daily": {**gateway_daily()["daily"], "precipitation_probability_max": NULLS * 2, "uv_index_max": NULLS * 2}}
    )

    assert set(bundle) >= set(reference) | {"daily_10d"}
    current = bundle["current"]
    assert current.pressure_hpa == 995.6 and current.weather_code == 1 and current.temp_c == 13.3  # ARPEGE fills the pressure and the code
    assert current.uv_index is not None and 0.0 < current.uv_index <= 1.18  # the UV maximum of the day scaled by the Sun at 11:00 UTC
    assert bundle["rain_next_60m"] == [0.0, 0.0, 0.1, 0.0]
    assert bundle["hourly_cloud_cover"] == [] or isinstance(bundle["hourly_cloud_cover"], list)


def test_the_days_the_models_cover_keep_their_values_and_the_rest_comes_from_the_public_api() -> None:
    public = {**public_days(), "hourly_cloud_cover": [(1_794_990_000, 40.0)]}
    bundle = merge_france_bundle(france(), public, uv_grids(), *VANNES, now_epoch(11))
    days = bundle["daily_10d"]

    assert [d.date for d in days] == [f"2026-11-{day}" for day in range(18, 28)]
    first, third, fifth = days[0], days[2], days[4]
    assert (first.tmax, first.tmin, first.code, first.rain_mm, first.wind_max) == (15.5, 12.2, 61, 4.7, 29.6)  # the models'
    assert first.rain_probability_pct == 10 and third.rain_probability_pct == 30  # the probability stays the public API's
    assert first.uv_max == 1.2 and days[1].uv_max == 1.0  # Météo-France's UV maxima of today and tomorrow (1.18 and 1.01, one decimal)
    assert third.uv_max == pytest.approx(1.0)  # the day after tomorrow: the public API's
    assert (fifth.tmax, fifth.code) == (18.0, 3)  # the fifth day: the public API's
    assert bundle["hourly_cloud_cover"] == [(1_794_990_000, 40.0)]


def test_the_credits_name_what_gave_the_data() -> None:
    both = merge_france_bundle(france(), public_days(), uv_grids(), *VANNES, now_epoch())
    assert both["provider_name"] == "Météo-France (AROME, ARPEGE)"
    for key in ("meteofrance_weather", "meteofrance_uv", "open_meteo"):
        assert credit_text(key) in both["attribution"]

    alone = merge_france_bundle(france(), None, None, *VANNES, now_epoch())
    assert credit_text("meteofrance_weather") in alone["attribution"]
    assert credit_text("meteofrance_uv") not in alone["attribution"] and credit_text("open_meteo") not in alone["attribution"]


def test_without_the_public_api_only_the_days_the_models_cover_are_returned() -> None:
    bundle = merge_france_bundle(france(), None, uv_grids(), *VANNES, now_epoch())
    assert [d.date for d in bundle["daily_10d"]] == ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"]
    assert all(d.rain_probability_pct is None for d in bundle["daily_10d"])
    assert bundle["hourly_cloud_cover"] == []
    assert bundle["daily_10d"][0].uv_max == 1.2 and bundle["daily_10d"][2].uv_max is None  # nothing public to complete day three


def test_without_the_uv_grid_the_current_index_is_unknown_and_the_days_keep_the_public_maximum() -> None:
    bundle = merge_france_bundle(france(), public_days(), None, *VANNES, now_epoch())
    assert bundle["current"].uv_index is None
    assert bundle["daily_10d"][0].uv_max == 0.8 and bundle["daily_10d"][5].uv_max == pytest.approx(1.3)


# ---------------------------------------------------------------- in the service


def november_public() -> Provider:
    """Open-Meteo's public sample (tests/samples.py), dated as the gateway answers."""
    payload = samples.open_meteo_payload()
    payload["daily"]["time"] = [f"2026-11-{day}" for day in range(18, 23)]
    return forecast_provider(payload)


def _service(tmp_path: Path, gateway: Optional[Provider], uv: Optional[UvServer] = None, public: Optional[Provider] = None) -> Any:
    extra: dict[str, Any] = {"open_meteo": OpenMeteoCollector(http_client=(public or forecast_provider()).client())}
    if gateway is not None:
        extra["meteofrance_weather"] = MeteoFranceWeatherCollector("http://gateway.test", http_client=gateway.client())
    if uv is not None:
        extra["meteofrance_uv"] = uv_collector(uv)
    return make_service(tmp_path / "mf.db", **extra)


async def test_a_point_in_france_is_served_by_the_models_and_credited(sim_clock: SimClock, tmp_path: Path) -> None:
    service = _service(tmp_path, gateway_provider(), UvServer(), november_public())
    point = await service.build_point_response(*VANNES)

    assert point.weather_provider == "Météo-France (AROME, ARPEGE)"
    assert point.current.temp_c == 13.3 and point.current.pressure_hpa == 995.6 and point.current.weather_code == 1
    assert point.weather_attribution is not None
    for key in ("meteofrance_weather", "meteofrance_uv"):
        assert credit_text(key) in point.weather_attribution
    assert [d.date for d in point.daily] == [f"2026-11-{day}" for day in range(18, 23)]
    assert point.daily[0].tmax == 15.5 and point.daily[0].rain_probability_pct == 80 and point.daily[4].tmax == 17.0  # day five: the public sample


async def test_a_point_outside_the_box_keeps_the_public_api_and_never_asks_the_gateway(sim_clock: SimClock, tmp_path: Path) -> None:
    gateway = gateway_provider()
    service = _service(tmp_path, gateway, UvServer(), forecast_provider())
    point = await service.build_point_response(*OSLO)

    assert point.weather_provider == "Open-Meteo" and point.current.temp_c == 18.2  # tests/samples.py
    assert gateway.count == 0


async def test_a_gateway_that_fails_leaves_the_public_api_as_before(sim_clock: SimClock, tmp_path: Path) -> None:
    service = _service(tmp_path, gateway_provider(status=503), UvServer(), forecast_provider())
    point = await service.build_point_response(*VANNES)
    assert point.weather_provider == "Open-Meteo" and point.current.temp_c == 18.2
    assert point.weather_attribution is not None and "Météo-France" not in point.weather_attribution


async def test_the_uv_api_failing_does_not_stop_the_weather(sim_clock: SimClock, tmp_path: Path) -> None:
    down = UvServer(runs=[])
    service = _service(tmp_path, gateway_provider(), down, forecast_provider())
    point = await service.build_point_response(*VANNES)
    assert point.weather_provider == "Météo-France (AROME, ARPEGE)" and point.current.uv_index is None
    assert point.weather_attribution is not None and credit_text("meteofrance_uv") not in point.weather_attribution


def test_the_sources_are_listed_as_off_without_a_gateway_or_a_key(tmp_path: Path) -> None:
    service = _service(tmp_path, None)
    off = {item["provider_id"]: item["reason"] for item in service.disabled_sources()}
    assert "VIGIE_METEO_GATEWAY_URL" in off["meteofrance_weather"] and "VIGIE_METEOFRANCE_API_KEY" in off["meteofrance_uv"]
    ids = {c.provider_id for c in service.get_all_collectors()}
    assert not ids & {"meteofrance_weather", "meteofrance_uv"}

    on = _service(tmp_path, gateway_provider(), UvServer())
    assert {"meteofrance_weather", "meteofrance_uv"} <= {c.provider_id for c in on.get_all_collectors()}
    assert not {"meteofrance_weather", "meteofrance_uv"} & {item["provider_id"] for item in on.disabled_sources()}


def test_an_expired_public_entry_older_than_the_models_first_day_is_not_added() -> None:
    old = {"daily_10d": [d.model_copy(update={"date": "2026-11-16"}) for d in public_days()["daily_10d"][:1]], "hourly_cloud_cover": []}
    bundle = merge_france_bundle(france(), old, None, *VANNES, now_epoch())
    assert [d.date for d in bundle["daily_10d"]][0] == "2026-11-18"
