"""The weather of the place (option "Weather and card", on by default): the weather entity and its three sensors.

The answers are real ones of the public instance for Biarritz (2026-11-22, around 13:11 UTC), so the clock is frozen just after
them: a gust or a high water "of the next hours" is read relative to now.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.components.weather import SERVICE_GET_FORECASTS
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.vigie.const import CONF_WEATHER_AND_CARD, DOMAIN
from custom_components.vigie.weather_data import condition_of, nearby_spots, rain_next_hour, rain_slots, strongest_gust

from .conftest import BIARRITZ, POINT, SPOTS, recorded, setup_entry
from .payloads import URL, hourly

NOW = "2026-11-22T13:12:00+00:00"  # just after the recorded answers
WEATHER = "weather.vigie_test"
RAIN = "sensor.vigie_test_rain_in_the_next_hour"
GUST = "sensor.vigie_test_strongest_gust_next_hours"
TIDE = "sensor.vigie_test_next_high_tide"


@pytest.fixture(autouse=True)
def _frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


def point_calls(aioclient_mock: AiohttpClientMocker, path: str = "/v1/point") -> list[Any]:
    return [call[1] for call in aioclient_mock.mock_calls if call[1].path == path]


async def forecasts(hass: HomeAssistant, kind: str) -> list[dict[str, Any]]:
    answer = await hass.services.async_call(
        WEATHER_DOMAIN, SERVICE_GET_FORECASTS, {"entity_id": WEATHER, "type": kind}, blocking=True, return_response=True
    )
    return answer[WEATHER]["forecast"]  # type: ignore[index]


async def test_the_weather_entity_shows_the_current_weather_of_the_answer(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    state = hass.states.get(WEATHER)
    assert state is not None
    assert state.state == "sunny"  # WMO code 1, by day at the place (not at the home of the test Home Assistant, in California)
    attributes = state.attributes
    assert attributes["temperature"] == 15.1 and attributes["apparent_temperature"] == 6.9 and attributes["humidity"] == 73
    assert attributes["pressure"] == 1022.5 and attributes["wind_speed"] == 52.6 and attributes["wind_gust_speed"] == 81.4
    assert attributes["wind_bearing"] == 270 and attributes["uv_index"] == 0.4
    assert attributes["attribution"].startswith("Source: Météo-France")
    query = point_calls(aioclient_mock)[-1].query
    assert query["compact"] == "false" and query["marine"] == "true" and query["lat"] == "45.00000"


async def test_the_days_are_the_days_of_the_answer_and_no_hours_when_the_instance_gives_none(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    days = await forecasts(hass, "daily")
    assert len(days) == 10
    first = days[0]
    assert first["datetime"] == "2026-11-22T00:00:00+01:00"  # the place's own day, at its UTC offset
    assert first["condition"] == "rainy" and first["temperature"] == 18.1 and first["templow"] == 13.3
    assert first["precipitation"] == 28.9 and first["precipitation_probability"] == 100 and first["wind_speed"] == 58.0
    with pytest.raises(HomeAssistantError):
        await forecasts(hass, "hourly")  # the instance gave no hour: the entity does not offer them


async def test_the_hours_when_the_instance_gives_them(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    point = recorded(POINT)
    point["hourly"] = hourly(point["server_utc"], [(14.9, 70.0, 0.4, 61, True), (14.2, 85.0, 1.2, 63, True), (12.0, None, None, 3, False)])
    mock_instance(point=point)
    await setup_entry(hass, mock_entry)
    hours = await forecasts(hass, "hourly")
    assert [hour["temperature"] for hour in hours] == [14.9, 14.2, 12.0]
    assert hours[0]["datetime"] == "2026-11-22T13:00:00+00:00" and hours[0]["condition"] == "rainy"
    assert hours[1]["wind_gust_speed"] == 85.0 and hours[2].get("wind_gust_speed") is None
    assert hours[2]["condition"] == "cloudy"
    # the gust sensor reads the hours first (6 hours), not the short-term forecast
    gust = hass.states.get(GUST)
    assert float(gust.state) == 85.0 and gust.attributes["forecast"] == "hourly" and gust.attributes["horizon_minutes"] == 360


async def test_the_three_sensors(hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    rain = hass.states.get(RAIN)
    assert float(rain.state) == pytest.approx(0.07) and rain.attributes["unit_of_measurement"] == "mm"
    assert rain.attributes["mm_per_step"] == [0.0, 0.0, 0.03, 0.04] and rain.attributes["interval_minutes"] == 15
    assert rain.attributes["rate_5m_mm_h"] == recorded(POINT)["rain_nowcast"]["rate_5m_mm_h"]
    assert [slot["mm_h"] for slot in rain.attributes["slots"]] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.36, 0.48]
    gust = hass.states.get(GUST)
    # no hourly forecast in this answer: the short-term forecast (AROME-PI), 85 km/h at 15:00 UTC
    assert float(gust.state) == 85.0
    assert gust.attributes["forecast"] == "short_term" and gust.attributes["at"] == "2026-11-22T15:00:00+00:00"
    assert gust.attributes["horizon_minutes"] == recorded(POINT)["short_term"]["horizon_minutes"] == 289
    assert "AROME-PI" in gust.attributes["attribution"]
    tide = hass.states.get(TIDE)
    assert tide.state == "2026-11-22T13:24:00+00:00"
    assert tide.attributes["height_m"] == 1.5 and tide.attributes["next_low_tide"] == "2026-11-22T19:32:00+00:00"
    assert "EOT20" in tide.attributes["attribution"]


async def test_inland_the_tide_is_unknown_and_not_asked_again_for_hours(
    hass: HomeAssistant,
    mock_instance: Callable[..., None],
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    no_stream: None,
) -> None:
    mock_instance(tides={"detail": "No tide here"}, tides_status=404)
    await setup_entry(hass, mock_entry)
    assert hass.states.get(TIDE).state == STATE_UNKNOWN
    assert len(point_calls(aioclient_mock, "/v1/tides")) == 1
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(point_calls(aioclient_mock)) == 2 and len(point_calls(aioclient_mock, "/v1/tides")) == 1  # the weather again, not the tide


async def test_an_instance_without_weather_leaves_the_warnings_alone(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(point={"detail": "boom"}, point_status=500)
    await setup_entry(hass, mock_entry)
    assert hass.states.get(WEATHER).state == STATE_UNAVAILABLE
    assert hass.states.get(RAIN).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.vigie_test_maximum_level").state == "none"  # read as usual


async def test_unticking_the_option_keeps_the_warnings_only(
    hass: HomeAssistant, mock_instance: Callable[..., None], aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Vigie Test",
        unique_id=f"{URL}|45.0000|5.0000",
        data={"url": URL, "latitude": 45.0, "longitude": 5.0},
        options={CONF_WEATHER_AND_CARD: False},
    )
    mock_instance()
    await setup_entry(hass, entry)
    assert hass.states.get(WEATHER) is None and hass.states.get(RAIN) is None
    assert hass.states.get("sensor.vigie_test_maximum_level") is not None
    assert point_calls(aioclient_mock) == [] and point_calls(aioclient_mock, "/v1/tides") == []


async def test_outside_france_no_measured_wind_is_asked(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    mock_instance(point=recorded("point_tokyo_2026-11-22.json"), normals=recorded("normals_tokyo_2026-11-22.json"))
    await setup_entry(hass, mock_entry)
    assert point_calls(aioclient_mock, "/v1/climate/normals") and point_calls(aioclient_mock, "/v1/stations/wind") == []
    assert mock_entry.runtime_data.weather.data.station_wind is None
    assert hass.states.get(WEATHER).attributes["attribution"].startswith("Weather data by Open-Meteo")


async def test_in_france_the_nearest_station_is_kept(
    hass: HomeAssistant, mock_instance: Callable[..., None], biarritz_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    station = biarritz_entry.runtime_data.weather.data.station_wind
    assert station["name"] == "BIARRITZ-PAYS-BASQUE" and station["distance_km"] == pytest.approx(2.4, abs=0.1)
    assert station["wind_speed_kmh"] == 43.6 and "Météo-France" in station["attribution"]
    bbox = point_calls(aioclient_mock, "/v1/stations/wind")[-1].query["bbox"]
    assert bbox == "-1.860,43.180,-1.260,43.780"


def test_the_conditions_of_the_codes() -> None:
    assert condition_of(0, True) == "sunny" and condition_of(0, False) == "clear-night" and condition_of(1, None) == "sunny"
    assert condition_of(65, True) == "pouring" and condition_of(95, False) == "lightning-rainy" and condition_of(45, True) == "fog"
    assert condition_of(4, True) is None and condition_of(None, True) is None and condition_of(True, True) is None


def test_a_missing_step_makes_the_rain_unknown_never_zero() -> None:
    point = deepcopy(recorded(POINT))
    point["rain_nowcast"]["mm_per_step"] = [0.0, None, 0.1, 0.0]
    assert rain_next_hour(point) is None
    point["rain_nowcast"]["mm_per_step"] = None
    assert rain_next_hour(point) is None
    assert rain_next_hour({"rain_nowcast": None}) is None


def test_no_gust_is_made_from_the_mean_wind() -> None:
    point = deepcopy(recorded(POINT))
    point["short_term"] = None
    assert strongest_gust(point, 1_795_353_120) is None


def test_the_spots_near_a_place_nearest_first() -> None:
    spots = nearby_spots(recorded(SPOTS), BIARRITZ["latitude"], BIARRITZ["longitude"])
    assert spots[0]["id"] == "biarritz-grande-plage" and spots[0]["distance_km"] == 0.5
    assert len(spots) == 12 and all(a["distance_km"] <= b["distance_km"] for a, b in zip(spots, spots[1:], strict=False))
    assert nearby_spots(recorded(SPOTS), 45.76, 4.84) == []  # Lyon: no spot within 60 km


def test_the_nine_slots_of_the_former_meteo_france_api() -> None:
    """Twelve 5-minute rates (PIAF, recorded in Biarritz) give 5-minute slots for the first half hour, then 10-minute slots
    that keep the stronger of their two rates; the first slot starts at the current 5 minutes of the answer's clock."""
    point = deepcopy(recorded(POINT))
    point["rain_nowcast"]["rate_5m_mm_h"] = [0.0, 1.2, 0.0, 0.0, 0.0, 0.0, 0.5, 2.0, 0.36, 0.0, 0.0, 0.48]
    slots = rain_slots(point)
    assert slots is not None and [(slot["minutes"], slot["mm_h"]) for slot in slots] == [
        (5, 0.0), (5, 1.2), (5, 0.0), (5, 0.0), (5, 0.0), (5, 0.0), (10, 2.0), (10, 0.36), (10, 0.48)
    ]
    first = point["server_utc"] // 300 * 300
    assert slots[0]["start"] == datetime.fromtimestamp(first, tz=UTC).isoformat()
    assert slots[6]["start"] == datetime.fromtimestamp(first + 1800, tz=UTC).isoformat()


def test_the_slots_start_at_the_start_the_api_gives() -> None:
    """With ``rain_nowcast.start_utc`` (the 5-minute boundary at which the API read the rain) the first slot starts there, even
    when ``server_utc`` has passed the next boundary since."""
    point = deepcopy(recorded(POINT))
    start = point["server_utc"] // 300 * 300 - 300
    point["rain_nowcast"]["start_utc"] = start
    slots = rain_slots(point)
    assert slots is not None
    assert slots[0]["start"] == datetime.fromtimestamp(start, tz=UTC).isoformat()
    assert slots[8]["start"] == datetime.fromtimestamp(start + 3000, tz=UTC).isoformat()


def test_no_slots_without_the_twelve_rates() -> None:
    """Outside metropolitan France (Tokyo) the API gives no 5-minute rates: no slots, never a dry hour made up."""
    assert rain_slots(recorded("point_tokyo_2026-11-22.json")) is None
    point = deepcopy(recorded(POINT))
    point["rain_nowcast"]["rate_5m_mm_h"][3] = None
    assert rain_slots(point) is None
    point["rain_nowcast"]["rate_5m_mm_h"] = [0.0] * 11
    assert rain_slots(point) is None
    assert rain_slots(None) is None
