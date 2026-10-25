"""The earthquakes near the place: the strongest-earthquake sensor and the `new_earthquake` event, a layer of their own.

Each earthquake is announced once: not at each read, not again when a second source reports it, never under the minimum
magnitude, and what had already happened when Home Assistant started is the baseline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vigie.const import CONF_QUAKE_MIN_MAGNITUDE
from custom_components.vigie.quakes import QuakeTracker, parse_earthquakes

from .conftest import FIXTURES, setup_entry
from .payloads import EMSC_CREDIT, SERVER_UTC, USGS_CREDIT, earthquake, earthquakes_response, response

SENSOR = "sensor.vigie_test_strongest_earthquake"
EVENT = "event.vigie_test_earthquake"


class Fired:
    """Collects the state changes of the earthquake event entity that carry an event."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.events: list[dict[str, Any]] = []
        hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_change)

    @callback
    def _on_change(self, event: Event) -> None:
        new_state, old_state = event.data["new_state"], event.data["old_state"]
        if event.data["entity_id"] == EVENT and new_state and new_state.attributes.get("event_type") and (
            old_state is None or old_state.state != new_state.state
        ):
            self.events.append(dict(new_state.attributes))

    def take(self) -> list[dict[str, Any]]:
        taken, self.events = self.events, []
        return taken


async def read(hass: HomeAssistant, entry: MockConfigEntry, mock_instance: Callable[..., None], *quakes: dict[str, Any], **kwargs: Any) -> None:
    mock_instance(quakes=earthquakes_response(list(quakes), **kwargs))
    await entry.runtime_data.quakes.async_refresh()
    await hass.async_block_till_done()


async def test_the_sensor_shows_the_strongest_earthquake_and_credits_the_sources(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(
        quakes=earthquakes_response(
            [earthquake("usgs_a", magnitude=6.2, sources=("USGS", "EMSC")), earthquake("emsc_b", magnitude=4.8, sources=("EMSC",), time_utc=SERVER_UTC - 60)]
        )
    )
    await setup_entry(hass, mock_entry)
    state = hass.states.get(SENSOR)
    assert state.state == "6.2"
    attributes = state.attributes
    assert attributes["title"] == "M 6.2 - 20 km N of Testville, France" and attributes["magnitude_type"] == "mww"
    assert attributes["time"] == datetime.fromtimestamp(SERVER_UTC - 1800, UTC).isoformat() and attributes["distance_km"] == 68.0 and attributes["bearing"] == "NE"
    assert attributes["latitude"] == 45.5 and attributes["longitude"] == 5.5  # on the map
    assert attributes["reported_by"] == ["USGS", "EMSC"]
    assert attributes["count"] == 2 and [q["id"] for q in attributes["earthquakes"]] == ["usgs_a", "emsc_b"] and attributes["omitted"] == 0
    assert attributes["radius_km"] == 300 and attributes["min_magnitude"] == 4.5 and attributes["hours"] == 24
    assert USGS_CREDIT in attributes["attribution"] and EMSC_CREDIT in attributes["attribution"]


async def test_no_earthquake_is_unknown_with_a_count_of_zero_and_no_source_is_an_unknown_count(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    state = hass.states.get(SENSOR)
    assert state.state == STATE_UNKNOWN and state.attributes["count"] == 0  # a magnitude nobody reported is not 0
    await read(hass, mock_entry, mock_instance, answered=(False, False))
    assert hass.states.get(SENSOR).attributes["count"] is None  # nothing answered: nothing can be said


async def test_each_earthquake_is_announced_once(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    fired = Fired(hass)
    before = earthquake("usgs_old", magnitude=5.5, time_utc=SERVER_UTC - 7200)
    mock_instance(quakes=earthquakes_response([before]))
    await setup_entry(hass, mock_entry)
    assert fired.take() == []  # what had happened before Home Assistant started is the baseline

    quake = earthquake("emsc_new", magnitude=5.0, sources=("EMSC",))
    await read(hass, mock_entry, mock_instance, before, quake)
    (new,) = fired.take()
    assert new["event_type"] == "new_earthquake" and new["id"] == "emsc_new" and new["magnitude"] == 5.0
    assert new["title"] == "M 5.0 - 20 km N of Testville, France" and new["distance_km"] == 68.0 and new["sources"] == ["EMSC"]

    await read(hass, mock_entry, mock_instance, before, quake)  # read again: nothing
    assert fired.take() == []
    # USGS reports it a little later, under its own id and a slightly different time and place: the same earthquake.
    merged = earthquake("usgs_same", magnitude=5.2, sources=("USGS", "EMSC"), time_utc=quake["time_utc"] + 20, lat=45.6)
    await read(hass, mock_entry, mock_instance, before, merged)
    assert fired.take() == []
    assert hass.states.get(SENSOR).state == "5.5"


async def test_two_earthquakes_of_one_read_are_two_events(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    fired = Fired(hass)
    mock_instance()
    await setup_entry(hass, mock_entry)
    await read(hass, mock_entry, mock_instance, earthquake("usgs_a", lat=40.0), earthquake("usgs_b", lat=44.0, time_utc=SERVER_UTC - 600))
    assert sorted(event["id"] for event in fired.take()) == ["usgs_a", "usgs_b"]


async def test_nothing_under_the_minimum_magnitude_is_shown_or_announced(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    fired = Fired(hass)
    mock_instance()
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_QUAKE_MIN_MAGNITUDE: 5.0})
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    # An instance that ignored the parameter sends a M 4.6: it is left out.
    await read(hass, mock_entry, mock_instance, earthquake("usgs_small", magnitude=4.6), min_magnitude=5.0)
    assert fired.take() == []
    assert hass.states.get(SENSOR).state == STATE_UNKNOWN and hass.states.get(SENSOR).attributes["earthquakes"] == []
    await read(hass, mock_entry, mock_instance, earthquake("usgs_small", magnitude=4.6), earthquake("usgs_big", magnitude=5.0, lat=44.0), min_magnitude=5.0)
    assert [event["id"] for event in fired.take()] == ["usgs_big"]


async def test_the_earthquakes_never_touch_the_warnings(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([]), quakes=earthquakes_response([earthquake(magnitude=7.4)]))
    await setup_entry(hass, mock_entry)
    assert hass.states.get(SENSOR).state == "7.4"
    assert hass.states.get("sensor.vigie_test_maximum_level").state == "none"  # an earthquake is not an official warning
    assert hass.states.get("binary_sensor.vigie_test_warning_active").state == "off"


async def test_an_instance_without_the_route_leaves_the_warnings_working(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([]), quakes_status=404)
    await setup_entry(hass, mock_entry)
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE and hass.states.get(EVENT).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.vigie_test_maximum_level").state == "none"
    await read(hass, mock_entry, mock_instance, earthquake(magnitude=5.0))  # the instance was updated
    assert hass.states.get(SENSOR).state == "5.0"


async def test_the_attributes_stay_bounded(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    many = [earthquake(f"usgs_{i}", magnitude=6.0, lat=40 + i / 10, title="M 6.0 - " + "x" * 400) for i in range(100)]
    mock_instance(quakes=earthquakes_response(many))
    await setup_entry(hass, mock_entry)
    attributes = dict(hass.states.get(SENSOR).attributes)
    assert len(json.dumps(attributes, ensure_ascii=False).encode()) < 16 * 1024  # what the recorder keeps
    assert len(attributes["earthquakes"]) <= 10 and attributes["omitted"] == 100 - len(attributes["earthquakes"])


def test_the_tracker_forgets_what_the_window_can_no_longer_bring_back() -> None:
    tracker = QuakeTracker(window_s=86400)
    first = parse_earthquakes(earthquakes_response([earthquake("a", time_utc=SERVER_UTC - 600)]), 4.5)
    assert tracker.update(first) == ()
    later = SERVER_UTC + 2 * 86400
    again = parse_earthquakes(earthquakes_response([earthquake("b", time_utc=later - 60)], server_utc=later), 4.5)
    assert [q.id for q in tracker.update(again)] == ["b"]
    assert [q.id for q in tracker._known] == ["b"]  # "a" is gone: no list grows without end


def test_the_recorded_answer_is_read() -> None:
    [path] = sorted(FIXTURES.glob("earthquakes_*.json"))
    data = parse_earthquakes(json.loads(path.read_text(encoding="utf-8")), 4.5)
    assert data.quakes and data.skipped == 0 and data.any_source_answered and data.attribution
    assert all(q.magnitude >= 4.5 and q.distance_km is not None for q in data.quakes)
