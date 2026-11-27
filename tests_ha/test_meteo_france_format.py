"""The two sensors in the format of Météo-France's integration (ADR 0046): what the cards written for it read.

Those cards (the Météo-France weather card of HACF, Weather Plus Card) find a "next rain" sensor by its entity id and its
``1_hour_forecast`` attribute, a "weather alert" sensor by its entity id or its translation key, a level for its state and one
attribute per phenomenon; they read Météo-France's words, not values. These tests hold that format.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vigie.const import DOMAIN
from custom_components.vigie.meteo_france_format import (
    alert_attributes,
    alert_state,
    next_rain_at,
    one_hour_forecast,
    rain_word,
)
from custom_components.vigie.models import parse_response
from custom_components.vigie.weather_data import rain_slots

from .conftest import POINT, recorded, setup_entry
from .payloads import coverage, response, vigilance

NOW = "2026-11-22T13:12:00+00:00"  # just after the recorded answers of Biarritz
NEXT_RAIN = "sensor.vigie_test_next_rain"
ALERT = "sensor.vigie_test_weather_alert"

# The words and the names of Météo-France's integration (meteofrance-api: ALERT_TYPE_DICTIONARY_FR, ALERT_COLOR_LIST_FR; the
# words of its rain forecast), which the cards compare with.
RAIN_WORDS = {"Temps sec", "Pluie faible", "Pluie modérée", "Pluie forte"}
PHENOMENA = {
    "Vent violent",
    "Pluie-inondation",
    "Orages",
    "Inondation",
    "Neige-verglas",
    "Canicule",
    "Grand-froid",
    "Avalanches",
    "Vagues-submersion",
}
COLOURS = {"Vert", "Jaune", "Orange", "Rouge"}


@pytest.fixture(autouse=True)
def _frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


async def test_the_next_rain_of_biarritz_in_the_slots_of_meteo_france(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    """The nine PIAF slots recorded in Biarritz: dry for 40 minutes, then light rain (0.36 and 0.48 mm/h)."""
    mock_instance()
    await setup_entry(hass, mock_entry)
    state = hass.states.get(NEXT_RAIN)
    slots = rain_slots(recorded(POINT))
    assert slots is not None
    assert state.attributes["forecast_time_ref"] == slots[0]["start"]
    assert state.attributes["1_hour_forecast"] == {
        "0 min": "Temps sec",
        "5 min": "Temps sec",
        "10 min": "Temps sec",
        "15 min": "Temps sec",
        "20 min": "Temps sec",
        "25 min": "Temps sec",
        "30 min": "Temps sec",
        "40 min": "Pluie faible",
        "50 min": "Pluie faible",
    }
    assert state.state == datetime.fromisoformat(slots[7]["start"]).isoformat()
    assert state.attributes["device_class"] == "timestamp"
    assert "Météo-France" in state.attributes["attribution"]


async def test_the_weather_alert_of_haute_corse_in_the_words_of_meteo_france(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, haute_corse: dict, no_stream: None
) -> None:
    """MeteoAlarm's red "Rain-flood" is Météo-France's "Pluie-inondation", its yellow "Flooding" the river flood "Inondation"."""
    mock_instance(haute_corse)
    await setup_entry(hass, mock_entry)
    state = hass.states.get(ALERT)
    assert state.state == "red"
    assert state.attributes["options"] == ["green", "yellow", "orange", "red"]
    assert {key: value for key, value in state.attributes.items() if key in PHENOMENA} == {
        "Inondation": "Jaune",
        "Orages": "Jaune",
        "Pluie-inondation": "Rouge",
        "Vent violent": "Jaune",
    }


async def test_nothing_found_is_green_and_no_source_is_unknown(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([]))
    await setup_entry(hass, mock_entry)
    state = hass.states.get(ALERT)
    assert state.state == "green"
    assert not PHENOMENA & set(state.attributes)


async def test_no_source_covering_the_place_leaves_the_alert_unknown(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([], sources=[], note="No official warning source covers this point.", attribution=None))
    await setup_entry(hass, mock_entry)
    assert hass.states.get(ALERT).state == STATE_UNKNOWN


async def test_the_entity_ids_the_cards_look_for_in_french(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    """Weather Plus Card finds them by their entity id: `next_rain` or `prochaine_pluie`, `weather_alert` or `vigilance_meteo`."""
    hass.config.language = "fr"
    mock_instance()
    await setup_entry(hass, mock_entry)
    registry = er.async_get(hass)
    rain = registry.async_get_entity_id("sensor", DOMAIN, f"{mock_entry.entry_id}_next_rain")
    alert = registry.async_get_entity_id("sensor", DOMAIN, f"{mock_entry.entry_id}_weather_alert")
    assert rain == "sensor.vigie_test_prochaine_pluie"
    assert alert == "sensor.vigie_test_vigilance_meteo"
    assert registry.async_get(alert).translation_key == "weather_alert"  # the key Weather Plus Card also looks for


def test_the_words_of_the_rates() -> None:
    """The bounds of the gateway: below 0.2 mm/h dry, below 1.6 light, below 6 moderate, then heavy."""
    assert [rain_word(rate) for rate in (0.0, 0.19, 0.2, 1.59, 1.6, 5.99, 6.0, 40.0)] == [
        "Temps sec",
        "Temps sec",
        "Pluie faible",
        "Pluie faible",
        "Pluie modérée",
        "Pluie modérée",
        "Pluie forte",
        "Pluie forte",
    ]


def test_outside_france_the_four_quarter_hours_of_the_model() -> None:
    """Tokyo has no PIAF slots: the four 15-minute steps (mm per step, so ×4 for a rate) from the start the API gives."""
    point = deepcopy(recorded("point_tokyo_2026-11-22.json"))
    start = 1795784400
    point["rain_nowcast"].update({"start_utc": start, "mm_per_step": [0.0, 0.1, 0.5, 2.0]})
    assert rain_slots(point) is None
    hour = one_hour_forecast(point, None)
    assert hour == (
        start,
        {"0 min": "Temps sec", "15 min": "Pluie faible", "30 min": "Pluie modérée", "45 min": "Pluie forte"},
    )
    assert next_rain_at(*hour) == datetime.fromtimestamp(start + 15 * 60, tz=UTC)


def test_a_step_unknown_or_no_start_makes_no_hour() -> None:
    point = deepcopy(recorded("point_tokyo_2026-11-22.json"))
    assert one_hour_forecast(point, None) is None  # an answer of before `start_utc`: the start of its hour is not known
    point["rain_nowcast"].update({"start_utc": 1795784400, "mm_per_step": [0.0, None, 0.0, 0.0]})
    assert one_hour_forecast(point, None) is None
    assert one_hour_forecast(None, None) is None


def test_a_dry_hour_has_no_next_rain() -> None:
    assert next_rain_at(1795784400, {"0 min": "Temps sec", "15 min": "Temps sec"}) is None


def test_the_phenomena_and_their_colours() -> None:
    data = parse_response(
        response(
            [
                vigilance("a", phenomenon="snow-ice", original="Snow/Ice", level="ORANGE"),
                vigilance("b", phenomenon="snow-ice", original="Snow/Ice", level="YELLOW"),
                vigilance("c", phenomenon="coastal", original="Vagues-submersion", level="YELLOW"),
                vigilance("d", phenomenon="fog", original="Fog", level="YELLOW"),  # no word at Météo-France: only in the state
                vigilance("e", phenomenon="heat", original="Canicule", level=None),  # no level: left out
            ],
            [coverage()],
        )
    )
    assert alert_attributes(data.vigilances) == {"Neige-verglas": "Orange", "Vagues-submersion": "Jaune"}
    assert alert_state(data.max_level) == "orange"
    assert alert_state("none") == "green" and alert_state(None) is None


async def test_the_format_the_cards_read(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, haute_corse: dict, no_stream: None
) -> None:
    """Keys `<n> min` and Météo-France's rain words; the alert's level words and phenomenon names, nothing else of theirs."""
    mock_instance(haute_corse)
    await setup_entry(hass, mock_entry)
    forecast = hass.states.get(NEXT_RAIN).attributes["1_hour_forecast"]
    assert all(re.fullmatch(r"\d+ min", key) for key in forecast) and set(forecast.values()) <= RAIN_WORDS
    alert = hass.states.get(ALERT)
    standard = {"options", "device_class", "friendly_name", "icon", "attribution"}
    assert set(alert.attributes) - standard <= PHENOMENA
    assert set(alert.attributes[key] for key in PHENOMENA & set(alert.attributes)) <= COLOURS
