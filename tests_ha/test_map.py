"""The card's Map page (ADR 0048): every spot with its verdicts and the figures of its hour (``vigie/card/map``), the places of
the French coast (``vigie/card/places``), the beaches kept with a star (``vigie/card/favorites``), and MapLibre GL JS served
beside the card.

The overviews are real answers of the public instance, recorded on 2026-11-28 at about 11:05 UTC, and the catalogue of places
is its answer for a box around Biarritz (see fixtures/README.md)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.vigie import async_remove_entry
from custom_components.vigie.card import MAPLIBRE_FOLDER, MAPLIBRE_VERSION
from custom_components.vigie.const import (
    CONF_WEATHER_AND_CARD,
    DOMAIN,
    FAVORITES_STORAGE_KEY,
    MAX_BEACHES,
    MAX_PLACES,
    PLACES_PAGE,
)
from custom_components.vigie.weather_data import MAP_ACTIVITIES, PLACE_FIELDS, map_place, map_spots

from .conftest import SPOT, SPOTS, recorded, setup_entry
from .payloads import URL

OVERVIEW = recorded("activitiesoverview_2026-11-28.json")
SURF = recorded("surfoverview_2026-11-28.json")
PLACES = recorded("seaplaces_catalogue_basque_2026-11-28.json")
FAR = "trevou-treguignec-plage-de-trestel"  # a spot in Brittany, far from Biarritz


async def ask(hass_ws_client: WebSocketGenerator, hass: HomeAssistant, message: dict[str, Any]) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(message)
    return await client.receive_json()


def calls(aioclient_mock: AiohttpClientMocker, path: str) -> list[Any]:
    return [call[1] for call in aioclient_mock.mock_calls if call[1].path == path]


def overviews(aioclient_mock: AiohttpClientMocker, *, overview: Any = None, status: int = 200) -> None:
    aioclient_mock.get(f"{URL}/v1/activities/overview", json=OVERVIEW if overview is None else overview, status=status)
    aioclient_mock.get(f"{URL}/v1/surf/overview", json=SURF, status=status)


@pytest.fixture
async def biarritz(hass: HomeAssistant, mock_instance: Callable[..., None], biarritz_entry: MockConfigEntry, no_stream: None) -> MockConfigEntry:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    return biarritz_entry


# ----------------------------------------------------------------------------------------------------------------- the spots
async def test_every_spot_with_its_verdicts_and_its_hour_then_kept_30_minutes(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, aioclient_mock: AiohttpClientMocker, biarritz: MockConfigEntry
) -> None:
    overviews(aioclient_mock)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/map"})
    assert answer["success"], answer
    data = answer["result"]
    assert data["language"] == hass.config.language and data["verdicts_read"] and data["hour_read"]
    # Every spot of the instance, far from the place too: the map shows the whole coast.
    assert len(data["spots"]) == len(recorded(SPOTS)["spots"]) == 171
    spot = next(item for item in data["spots"] if item["id"] == SPOT)
    assert spot["name"] == "Plage des Corsaires" and spot["place"] == "Biarritz" and spot["facing_deg"] == 296
    assert set(spot["verdicts"]) == set(MAP_ACTIVITIES)
    # The verdict of each activity is the overview's (the best of the next 24 hours, as on the website's pins).
    recorded_spot = next(item for item in OVERVIEW["spots"] if item["id"] == SPOT)
    for activity in recorded_spot["activities"]:
        assert spot["verdicts"][activity["id"]] == (activity["verdict"] if activity["available"] else None)
    # The hour under way of the surf overview, as it gave it.
    now = next(item for item in SURF["spots"] if item["id"] == SPOT)["now"]
    assert spot["now"]["swell_m"] == round(now["swell"]["height_m"], 1) and spot["now"]["time_utc"] == now["time_utc"]
    assert spot["now"]["wind_from_deg"] == round(now["wind"]["from_deg"]) and spot["now"]["tide_trend"] == now["tide"]["trend"]
    # Its credits and its warnings travel with it.
    assert data["attribution"] == OVERVIEW["attribution"] and data["hour_attribution"] == SURF["attribution"]
    assert data["disclaimer"] == OVERVIEW["disclaimer"] and data["generated_utc"] == OVERVIEW["generated_utc"]
    # Another screen within 30 minutes: nothing read again; later, the two overviews (the list of spots is kept a day).
    await ask(hass_ws_client, hass, {"type": "vigie/card/map"})
    assert len(calls(aioclient_mock, "/v1/activities/overview")) == 1 and len(calls(aioclient_mock, "/v1/surf/overview")) == 1
    weather = biarritz.runtime_data.weather
    for part in (weather._overview, weather._surf_overview):
        part.read_at -= 1801
    await ask(hass_ws_client, hass, {"type": "vigie/card/map"})
    assert len(calls(aioclient_mock, "/v1/activities/overview")) == 2 and len(calls(aioclient_mock, "/v1/surf/overview")) == 2


async def test_an_instance_without_the_overviews_gives_the_spots_without_verdicts(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, aioclient_mock: AiohttpClientMocker, biarritz: MockConfigEntry
) -> None:
    overviews(aioclient_mock, overview={"detail": "Not Found"}, status=404)
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/map"}))["result"]
    assert len(data["spots"]) == 171 and not data["verdicts_read"] and not data["hour_read"]
    assert all(set(spot["verdicts"].values()) == {None} and spot["now"] is None for spot in data["spots"])


def test_a_verdict_or_a_figure_the_api_did_not_give_stays_unknown() -> None:
    spots = {"spots": [{"id": "a", "name": "A", "lat": 43.5, "lon": -1.5}, {"id": "no-position", "lat": None, "lon": 1}]}
    overview = {"spots": [{"id": "a", "activities": [{"id": "surf", "available": False, "verdict": "good"}, {"id": "kite", "verdict": "excellent"}, {"id": "boat", "verdict": "poor"}]}]}
    surf = {"spots": [{"id": "a", "now": {"time_utc": 1, "swell": {"height_m": "1"}, "wind": {"speed_kmh": 12.4}, "tide": None}}]}
    (spot,) = map_spots(spots, overview, surf)["spots"]  # a spot without a position has no place on the map
    assert spot["verdicts"] == {"surf": None, "kite": None, "boat": "poor", "shore_fishing": None}  # unavailable, unknown word
    assert spot["now"]["swell_m"] is None and spot["now"]["wind_kmh"] == 12 and spot["now"]["tide_stage"] is None
    assert map_spots(None, None, None)["spots"] == []


# ---------------------------------------------------------------------------------------------------------------- the places
async def test_the_places_of_the_coast_are_read_once_a_day(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, aioclient_mock: AiohttpClientMocker, biarritz: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{URL}/v1/sea/places", json=PLACES)
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/places"}))["result"]
    assert data["fields"] == list(PLACE_FIELDS) and len(data["places"]) == PLACES["count"] == 93
    assert data["by_type"] == PLACES["by_type"] and data["attribution"] == PLACES["attribution"] and data["note"] == PLACES["note"]
    first = PLACES["places"][0]
    assert data["places"][0] == [first["id"], first["type"], round(first["lat"], 5), round(first["lon"], 5), first["name"], first["near"], first["quality"]]
    # The whole catalogue, not the places around a point; one page was enough. Asked again: nothing read again.
    (url,) = calls(aioclient_mock, "/v1/sea/places")
    assert dict(url.query) == {"limit": str(PLACES_PAGE), "offset": "0"}
    await ask(hass_ws_client, hass, {"type": "vigie/card/places"})
    assert len(calls(aioclient_mock, "/v1/sea/places")) == 1
    biarritz.runtime_data.weather._places.read_at -= 86401
    await ask(hass_ws_client, hass, {"type": "vigie/card/places"})
    assert len(calls(aioclient_mock, "/v1/sea/places")) == 2


def page(start: int, count: int, total: int) -> dict[str, Any]:
    return {**PLACES, "count": total, "returned": count, "places": [{**PLACES["places"][0], "id": f"place-{n}"} for n in range(start, start + count)]}


async def test_the_catalogue_is_read_page_by_page(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, aioclient_mock: AiohttpClientMocker, biarritz: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{URL}/v1/sea/places", params={"offset": "1000"}, json=page(1000, 71, 1071))
    aioclient_mock.get(f"{URL}/v1/sea/places", params={"offset": "500"}, json=page(500, 500, 1071))
    aioclient_mock.get(f"{URL}/v1/sea/places", params={"offset": "0"}, json=page(0, 500, 1071))
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/places"}))["result"]
    assert [place[0] for place in data["places"]] == [f"place-{n}" for n in range(1071)]
    assert [url.query["offset"] for url in calls(aioclient_mock, "/v1/sea/places")] == ["0", "500", "1000"]


async def test_an_instance_that_ignores_the_offset_is_not_read_forever(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, aioclient_mock: AiohttpClientMocker, biarritz: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{URL}/v1/sea/places", json=page(0, 500, 4071))
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/places"}))["result"]
    assert len(data["places"]) == 500  # the same page each time: kept once
    assert len(calls(aioclient_mock, "/v1/sea/places")) == 9  # 4 071 places by pages of 500, then it stops
    assert MAX_PLACES >= 4071


async def test_an_instance_without_the_places_gives_none(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, aioclient_mock: AiohttpClientMocker, biarritz: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{URL}/v1/sea/places", json={"detail": "Not Found"}, status=404)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/places"})
    assert answer["success"] and answer["result"] is None


def test_a_place_without_a_position_or_a_kind_is_left_out() -> None:
    assert map_place({"id": "a", "type": "port", "lat": 43.0, "lon": None}) is None
    assert map_place({"id": "a", "lat": 43.0, "lon": -1.0}) is None
    assert map_place({"id": "a", "type": "dive", "lat": 43.123456, "lon": -1.0, "name": 3}) == ["a", "dive", 43.12346, -1.0, None, None, None]


# ------------------------------------------------------------------------------------------------------------ the favourites
async def test_the_kept_beaches_are_written_read_and_sent_to_every_open_card(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, hass_storage: dict[str, Any], biarritz: MockConfigEntry
) -> None:
    # None kept yet: the card keeps the beaches of its own configuration.
    assert (await ask(hass_ws_client, hass, {"type": "vigie/card/favorites"}))["result"] == {"entry_id": biarritz.entry_id, "spots": None}
    # A screen that follows them: the list at once, then each change.
    follower = await hass_ws_client(hass)
    await follower.send_json_auto_id({"type": "vigie/card/favorites/subscribe"})
    assert (await follower.receive_json())["success"]
    assert (await follower.receive_json())["event"] == {"spots": None}
    # A star on another screen, on a beach far from the place: kept, in this order, and written to the storage.
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/favorites/set", "spots": [SPOT, FAR]})
    assert answer["success"] and answer["result"] == {"entry_id": biarritz.entry_id, "spots": [SPOT, FAR]}
    assert (await follower.receive_json())["event"] == {"spots": [SPOT, FAR]}
    await hass.async_block_till_done()
    assert hass_storage[FAVORITES_STORAGE_KEY]["data"] == {"entries": {biarritz.entry_id: [SPOT, FAR]}}
    assert (await ask(hass_ws_client, hass, {"type": "vigie/card/favorites"}))["result"]["spots"] == [SPOT, FAR]
    # Every star taken away: an empty list, not "none kept" (the card's configuration does not come back).
    assert (await ask(hass_ws_client, hass, {"type": "vigie/card/favorites/set", "spots": []}))["result"]["spots"] == []
    assert (await follower.receive_json())["event"] == {"spots": []}


@pytest.mark.parametrize(
    ("spots", "code"),
    [
        (["nowhere-plage"], "not_found"),  # not a spot of the instance
        ([SPOT, SPOT], "invalid_format"),
        ([f"spot-{n}" for n in range(MAX_BEACHES + 1)], "invalid_format"),  # more than the tabs of the Sea page
        ([""], "invalid_format"),
    ],
)
async def test_a_list_that_cannot_be_kept_is_refused_and_nothing_changes(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, biarritz: MockConfigEntry, spots: list[str], code: str
) -> None:
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/favorites/set", "spots": spots})
    assert not answer["success"] and answer["error"]["code"] == code
    assert (await ask(hass_ws_client, hass, {"type": "vigie/card/favorites"}))["result"]["spots"] is None


async def test_the_kept_beaches_are_read_back_after_a_restart(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, hass_storage: dict[str, Any], mock_instance: Callable[..., None], biarritz_entry: MockConfigEntry, no_stream: None
) -> None:
    hass_storage[FAVORITES_STORAGE_KEY] = {"version": 1, "minor_version": 1, "key": FAVORITES_STORAGE_KEY, "data": {"entries": {biarritz_entry.entry_id: [FAR], "gone": [SPOT]}}}
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    assert (await ask(hass_ws_client, hass, {"type": "vigie/card/favorites"}))["result"]["spots"] == [FAR]


async def test_a_removed_entry_takes_its_beaches_with_it(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, hass_storage: dict[str, Any], biarritz: MockConfigEntry
) -> None:
    await ask(hass_ws_client, hass, {"type": "vigie/card/favorites/set", "spots": [SPOT]})
    await async_remove_entry(hass, biarritz)
    await hass.async_block_till_done()
    assert hass_storage[FAVORITES_STORAGE_KEY]["data"] == {"entries": {}}


async def test_without_the_weather_option_there_is_no_map(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mock_instance: Callable[..., None], no_stream: None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Vigie Test", unique_id=f"{URL}|45.0000|5.0000", data={"url": URL, "latitude": 45.0, "longitude": 5.0},
        options={CONF_WEATHER_AND_CARD: False},
    )  # fmt: skip
    mock_instance()
    await setup_entry(hass, entry)
    for kind in ("vigie/card/map", "vigie/card/places", "vigie/card/favorites", "vigie/card/favorites/subscribe"):
        answer = await ask(hass_ws_client, hass, {"type": kind, "entry_id": entry.entry_id})
        assert not answer["success"] and answer["error"]["code"] == "not_found", kind
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/favorites/set", "entry_id": entry.entry_id, "spots": [SPOT]})
    assert not answer["success"] and answer["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------------------------------------------- MapLibre
@pytest.mark.parametrize(
    ("name", "digest"),
    [
        ("maplibre-gl.mjs", "244d36122c1398da3a8259817cc6d71e04321afe6404f32606b4b6c6cc4f26fb"),
        ("maplibre-gl-worker.mjs", "5f1e2f0e1624082b7e88a06e4c86085992b0f8c02929801e552f3f7a119856a9"),
        ("maplibre-gl.css", "bacba6a85a867fcf602f2ff88331b8acf7edf3fbd063a519888cfbff39970193"),
        ("LICENSE.txt", "ee5fc05a0677eaf69601d2c7db0d9ecd6cc27c3abc1d0733bc9ed34707cf8ef2"),
    ],
)
def test_maplibre_is_the_package_unchanged(name: str, digest: str) -> None:
    """The files of the npm package maplibre-gl 6.14.0 as they are (frontend/maplibre/README.md gives their source)."""
    assert hashlib.sha256((MAPLIBRE_FOLDER / name).read_bytes()).hexdigest() == digest
    readme = (MAPLIBRE_FOLDER / "README.md").read_text(encoding="utf-8")
    assert f"version **{MAPLIBRE_VERSION}**" in readme and (name == "LICENSE.txt" or digest in readme)
