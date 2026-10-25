"""The experimental, unofficial pre-alert for Turkey: read only when its option is ticked (off by default), and never mixed with the
official warnings."""

from __future__ import annotations

import json
from collections.abc import Callable

from homeassistant.const import ATTR_ATTRIBUTION, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.vigie.const import CONF_EXPERIMENTAL_PREALERT, PREALERT_LEVEL_STATES
from custom_components.vigie.diagnostics import async_get_config_entry_diagnostics
from custom_components.vigie.prealert import parse_prealert

from .conftest import FIXTURES, setup_entry
from .payloads import ECMWF_CREDIT, URL, prealert_day, prealert_phenomenon, prealert_response, response

SENSOR = "sensor.vigie_test_pre_alert_experimental_unofficial"


def ticked(entry: MockConfigEntry) -> MockConfigEntry:
    return MockConfigEntry(domain=entry.domain, title=entry.title, unique_id=entry.unique_id, data=dict(entry.data), options={CONF_EXPERIMENTAL_PREALERT: True})


def asked(aioclient_mock: AiohttpClientMocker) -> list[str]:
    return [str(call[1]) for call in aioclient_mock.mock_calls if "/v1/experimental/prealert" in str(call[1])]


async def test_off_by_default_nothing_is_asked_and_there_is_no_sensor(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    assert hass.states.get(SENSOR) is None
    assert asked(aioclient_mock) == []
    assert mock_entry.runtime_data.prealert is None


async def test_ticked_the_sensor_shows_todays_level_labelled_experimental_and_unofficial(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    days = [
        prealert_day("2026-10-25", 2, rain=prealert_phenomenon(61.0, 2), wind=prealert_phenomenon(None, None, "km/h (strongest gust)")),
        prealert_day("2026-10-26", 0, rain=prealert_phenomenon(3.0, 0)),
    ]
    mock_instance(prealert=prealert_response(days))
    entry = ticked(mock_entry)
    await setup_entry(hass, entry)
    state = hass.states.get(SENSOR)
    assert state.state == "strong"
    attributes = state.attributes
    assert attributes["experimental"] is True and attributes["official"] is False
    assert attributes["notice"].startswith("EXPERIMENTAL and UNOFFICIAL")
    assert attributes["province"] == "İstanbul" and attributes["province_code"] == "TR-34" and attributes["day"] == "2026-10-25"
    assert attributes["official_warnings_url"] == "https://www.mgm.gov.tr/meteouyari/turkiye.aspx"
    assert attributes[ATTR_ATTRIBUTION] == ECMWF_CREDIT
    assert [d["level"] for d in attributes["days"]] == ["strong", "none"]
    assert attributes["days"][0]["phenomena"]["wind"] == {"unit": "km/h (strongest gust)", "thresholds": [20.0, 40.0, 100.0]}  # no value, no level: not 0
    assert asked(aioclient_mock) == [f"{URL}/v1/experimental/prealert?lat=45.00000&lon=5.00000"]
    # The official warnings are untouched.
    assert hass.states.get("sensor.vigie_test_maximum_level").state == "none"


async def test_no_level_for_today_is_unknown(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(prealert=prealert_response([], run_utc=None))  # before the instance stored its first run
    await setup_entry(hass, ticked(mock_entry))
    assert hass.states.get(SENSOR).state == STATE_UNKNOWN


async def test_an_instance_without_the_pre_alert_leaves_it_unavailable_and_the_rest_working(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([]), prealert_status=404)
    await setup_entry(hass, ticked(mock_entry))
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.vigie_test_maximum_level").state == "none"


async def test_an_answer_that_does_not_say_it_is_unofficial_is_refused(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(prealert={**prealert_response([prealert_day("2026-10-25", 3)]), "official": True})
    await setup_entry(hass, ticked(mock_entry))
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE


async def test_the_diagnostics_keep_the_province_not_the_place(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(prealert=prealert_response([prealert_day("2026-10-25", 1, rain=prealert_phenomenon(25.0, 1))]))
    entry = ticked(mock_entry)
    await setup_entry(hass, entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["experimental_prealert"] == {
        "last_update_success": True,
        "province_code": "TR-34",
        "run_utc": "2026-10-25T00:00Z",
        "days": [{"day": "2026-10-25", "max_level": 1}],
    }
    assert "45.0" not in json.dumps(diagnostics)


def test_the_recorded_answer_is_read() -> None:
    recorded = sorted(FIXTURES.glob("prealert_*.json"))
    assert recorded
    for path in recorded:
        data = parse_prealert(json.loads(path.read_text(encoding="utf-8")))
        assert data.province_code.startswith("TR-") and data.days and data.notice and data.attribution
        assert all(day.level_word in (*PREALERT_LEVEL_STATES, None) for day in data.days)
