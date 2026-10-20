"""The event entity: `new`, `upgraded` and `ended`, from comparing each read with the one before."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import setup_entry
from .payloads import coverage, response, vigilance

EVENT = "event.vigie_test_vigilance"


class Fired:
    """Collects the state changes of the event entity that carry an event."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.events: list[dict[str, Any]] = []
        hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_change)

    @callback
    def _on_change(self, event: Event) -> None:
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        if event.data["entity_id"] == EVENT and new_state and new_state.attributes.get("event_type") and (
            old_state is None or old_state.state != new_state.state
        ):
            self.events.append(dict(new_state.attributes))

    def take(self) -> list[dict[str, Any]]:
        taken, self.events = self.events, []
        return taken


async def refresh(hass: HomeAssistant, entry: MockConfigEntry, mock_instance: Callable[..., None], payload: dict[str, Any]) -> None:
    mock_instance(payload)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_what_is_in_force_at_the_start_fires_nothing(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, haute_corse: dict, no_stream: None
) -> None:
    fired = Fired(hass)
    mock_instance(haute_corse)
    await setup_entry(hass, mock_entry)
    await refresh(hass, mock_entry, mock_instance, haute_corse)  # the same answer again
    assert fired.take() == []


async def test_a_new_warning_an_upgrade_and_an_end(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    fired = Fired(hass)
    mock_instance(response([]))
    await setup_entry(hass, mock_entry)

    await refresh(hass, mock_entry, mock_instance, response([vigilance("doc-1", level="YELLOW")]))
    (new,) = fired.take()
    assert new["event_type"] == "new"
    assert new["level"] == "yellow" and new["phenomenon"] == "wind" and new["source"] == "meteoalarm"
    assert new["id"] == "doc-1" and new["area"] == "Haute-Corse" and new["headline"] == "Yellow wind warning"
    assert new["onset"].endswith("+00:00") and new["expires"].endswith("+00:00")
    assert "previous_level" not in new

    # The source re-issues the warning under a new identifier when its level changes: still the same warning.
    await refresh(hass, mock_entry, mock_instance, response([vigilance("doc-2", level="ORANGE")]))
    (upgraded,) = fired.take()
    assert upgraded["event_type"] == "upgraded" and upgraded["level"] == "orange" and upgraded["previous_level"] == "yellow"

    await refresh(hass, mock_entry, mock_instance, response([vigilance("doc-3", level="YELLOW")]))  # a downgrade is not an event
    assert fired.take() == []

    await refresh(hass, mock_entry, mock_instance, response([]))
    (ended,) = fired.take()
    assert ended["event_type"] == "ended" and ended["level"] == "yellow" and ended["id"] == "doc-3"


async def test_every_change_of_one_read_is_delivered_as_its_own_event(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    fired = Fired(hass)
    mock_instance(response([vigilance("old", phenomenon="rain", original="Rain", level="RED")]))
    await setup_entry(hass, mock_entry)
    await refresh(
        hass,
        mock_entry,
        mock_instance,
        response([vigilance("a", phenomenon="wind", level="ORANGE"), vigilance("b", phenomenon="fire", original="Fire", level="YELLOW")]),
    )
    events = fired.take()
    assert sorted((e["event_type"], e["phenomenon"]) for e in events) == [("ended", "rain"), ("new", "fire"), ("new", "wind")]


async def test_a_failed_read_fires_nothing_and_the_next_one_compares_with_the_last_good_one(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    fired = Fired(hass)
    mock_instance(response([vigilance("a", level="ORANGE")]))
    await setup_entry(hass, mock_entry)
    mock_instance(status=500)
    await mock_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert fired.take() == []
    await refresh(hass, mock_entry, mock_instance, response([vigilance("a", level="ORANGE")]))  # back, unchanged
    assert fired.take() == []


async def test_a_source_that_is_down_does_not_end_its_warnings(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    fired = Fired(hass)
    river = vigilance("GA32", source="vigicrues", level="ORANGE", phenomenon="flood", original="3", codes=(("VIGICRUES_SECTION", "GA32"),))
    both = [coverage(), coverage("vigicrues")]
    mock_instance(response([river], sources=both))
    await setup_entry(hass, mock_entry)
    await refresh(hass, mock_entry, mock_instance, response([], sources=[coverage(), coverage("vigicrues", status="unavailable")]))
    assert fired.take() == []
    await refresh(hass, mock_entry, mock_instance, response([], sources=both))
    (ended,) = fired.take()
    assert ended["event_type"] == "ended" and ended["source"] == "vigicrues" and ended["id"] == "GA32"
