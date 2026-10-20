"""The ECCC collector: the recorded list of 2026-10-20 (MSC GeoMet), the normalization tables, the points, the pages.

The recorded answer is real (``tests/fixtures/eccc/weather_alerts_items.json``: 36 alerts in force in Canada, all yellow),
and so is the one ended alert recorded later the same day (``ended_item.json``: ``status_en`` ``ended``). The items built
here are **synthetic** copies of a recorded one with a word changed, to reach what that day does not hold: an orange or
red alert, a statement, an alert whose ``alert_type`` says it ended.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any, Optional

import httpx
import pytest
from shapely.geometry import Point

from app.attribution import credit_text
from app.collectors import eccc
from app.collectors.base import Fetched
from app.collectors.eccc import ECCCCollector, derive_level, derive_phenomenon, parse_feature, parse_items
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon, VigilanceStatus
from tests import vigilance_samples as samples
from tests.helpers import Provider, SimClock


def recorded_payload() -> dict[str, Any]:
    return json.loads(samples.national_fixture("eccc", "weather_alerts_items.json"))


def altered(**properties: Any) -> dict[str, Any]:
    """A copy of the first recorded item (a frost advisory in Ontario) with some properties changed."""
    feature = copy.deepcopy(recorded_payload()["features"][0])
    feature["properties"].update(properties)
    return feature


# ---------------------------------------------------------------- the recorded list


def test_the_recorded_list_is_36_alerts_by_forecast_area() -> None:
    alerts = samples.eccc_alerts()
    assert len(alerts.vigilances) == 36 and alerts.skipped == 0
    assert Counter(v.event for v in alerts.vigilances) == {
        "frost advisory": 23,
        "wind warning": 8,
        "snowfall warning": 3,
        "blizzard warning": 1,
        "storm surge warning": 1,
    }
    assert len({v.id for v in alerts.vigilances}) == 36
    assert {v.area.geocodes[0].value for v in alerts.vigilances} == {"ON", "QC", "NT", "BC", "YT", "BC/YT"}


def test_a_recorded_alert_is_normalized_with_its_original_words_beside() -> None:
    rondeau = next(v for v in samples.eccc_alerts().vigilances if v.area.description == "Chatham-Kent - Rondeau Park")
    assert rondeau.id == "eccc:174219162609692864202610190503_fea1-1158"
    assert (rondeau.level, rondeau.level_original) == (SeverityLevel.YELLOW, "yellow")
    assert (rondeau.phenomenon, rondeau.phenomenon_original) == (VigilancePhenomenon.COLD, "frost advisory")
    assert rondeau.status == VigilanceStatus.UPDATE and rondeau.message_type is None  # GeoMet has no msgType: not invented
    assert (rondeau.country, rondeau.source) == ("CA", "eccc")
    assert [(g.scheme, g.value) for g in rondeau.area.geocodes] == [("province", "ON"), ("feature_id", "fea1-1158")]
    assert rondeau.area.has_polygon is True and rondeau.shape is not None
    # 2026-10-20 08:19:35Z published, valid from 08:10Z, expires 16:00:35Z, the event ends 15:00Z.
    assert (rondeau.sent_utc, rondeau.onset_utc, rondeau.expires_utc, rondeau.ends_utc) == (
        1_792_484_375,
        1_792_483_800,
        1_792_512_035,
        1_792_508_400,
    )
    assert rondeau.issuer is None and rondeau.link.endswith("/items/174219162609692864202610190503_fea1-1158")  # type: ignore[union-attr]


def test_the_texts_are_the_issuers_in_english_and_french_untouched() -> None:
    rondeau = next(v for v in samples.eccc_alerts().vigilances if v.area.description == "Chatham-Kent - Rondeau Park")
    english, french = rondeau.texts
    assert (english.language, french.language) == ("en", "fr")
    assert (english.event, french.event) == ("frost advisory", "avis de gel")
    assert english.description is not None and english.description.startswith("Patchy frost continues early this morning.\n\n")
    assert french.description is not None and french.description.startswith("Le gel épars persiste tôt ce matin.")
    assert french.description.endswith("#ONMeteo.")
    assert {"name": "colour", "value": "jaune"} in [p.model_dump() for p in french.parameters]
    assert {"name": "confidence", "value": "High"} in [p.model_dump() for p in english.parameters]


def test_the_credit_is_the_one_of_the_end_use_licence() -> None:
    [first, *_] = samples.eccc_alerts().vigilances
    assert first.attribution == credit_text("eccc_alerts")
    assert first.attribution.startswith("Data Source: Environment and Climate Change Canada")  # the wording the licence gives
    assert "Data Server End-use Licence of Environment and Climate Change Canada" in first.attribution  # and its fallback statement


@pytest.mark.parametrize(
    ("place", "point", "events"),
    [
        ("Whitehorse", samples.WHITEHORSE, {"wind warning"}),
        ("Inuvik", (68.3607, -133.7230), {"snowfall warning"}),
        ("Rondeau Park", samples.RONDEAU, {"frost advisory"}),
        ("Tuktoyaktuk", (69.445, -133.03), {"storm surge warning"}),
        ("Toronto", samples.TORONTO, set()),
        ("Montréal", (45.5, -73.57), set()),
    ],
)
def test_the_polygons_of_the_recording_tell_where_a_point_is(place: str, point: tuple[float, float], events: set[str]) -> None:
    lat, lon = point
    inside = {v.event for v in samples.eccc_alerts().vigilances if v.shape is not None and v.shape.covers(Point(lon, lat))}
    assert inside == events, place


# ---------------------------------------------------------------- the tables


@pytest.mark.parametrize(
    ("colour", "level"),
    [
        ("yellow", SeverityLevel.YELLOW),
        ("orange", SeverityLevel.ORANGE),
        ("Red", SeverityLevel.RED),
        (" red ", SeverityLevel.RED),
        ("grey", None),  # a statement has none of the three
        ("", None),
        (None, None),
    ],
)
def test_the_colour_table(colour: Optional[str], level: Optional[SeverityLevel]) -> None:
    assert derive_level(colour) == level


@pytest.mark.parametrize(
    ("name", "phenomenon"),
    [
        ("wind warning", VigilancePhenomenon.WIND),
        ("tornado warning", VigilancePhenomenon.WIND),
        ("hurricane warning", VigilancePhenomenon.WIND),
        ("storm surge warning", VigilancePhenomenon.COASTAL),
        ("flash flood warning", VigilancePhenomenon.FLOOD),
        ("severe thunderstorm warning", VigilancePhenomenon.THUNDERSTORM),
        ("blizzard warning", VigilancePhenomenon.SNOW_ICE),
        ("snowfall warning", VigilancePhenomenon.SNOW_ICE),
        ("freezing rain warning", VigilancePhenomenon.SNOW_ICE),  # not rain: the first rule that fits wins
        ("winter storm warning", VigilancePhenomenon.SNOW_ICE),
        ("frost advisory", VigilancePhenomenon.COLD),
        ("extreme cold warning", VigilancePhenomenon.COLD),
        ("wind chill warning", VigilancePhenomenon.COLD),
        ("heat warning", VigilancePhenomenon.HEAT),
        ("fog advisory", VigilancePhenomenon.FOG),
        ("rainfall warning", VigilancePhenomenon.RAIN),
        ("air quality statement", VigilancePhenomenon.OTHER),  # no rule covers it: "other", not a guess
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_the_alert_name_table(name: Optional[str], phenomenon: Optional[VigilancePhenomenon]) -> None:
    assert derive_phenomenon(name) == phenomenon


@pytest.mark.parametrize(
    ("word", "status"),
    [("issued", VigilanceStatus.NEW), ("continued", VigilanceStatus.UPDATE), ("upgraded", None), (None, None)],
)
def test_the_status_table_knows_only_the_words_that_were_seen(word: Optional[str], status: Optional[VigilanceStatus]) -> None:
    assert parse_feature(altered(status_en=word)).status == status  # type: ignore[union-attr]


def test_an_orange_alert_is_orange_and_keeps_its_word() -> None:
    warning = parse_feature(altered(risk_colour_en="orange", risk_colour_fr="orange", alert_name_en="freezing rain warning"))
    assert warning is not None
    assert (warning.level, warning.level_original) == (SeverityLevel.ORANGE, "orange")
    assert warning.phenomenon == VigilancePhenomenon.SNOW_ICE


def test_a_statement_has_no_level_and_is_not_made_one() -> None:
    warning = parse_feature(altered(risk_colour_en="grey", alert_name_en="special weather statement"))
    assert warning is not None and warning.level is None and warning.level_original == "grey"
    assert warning.phenomenon == VigilancePhenomenon.OTHER


def recorded_ended_item() -> dict[str, Any]:
    return json.loads(samples.national_fixture("eccc", "ended_item.json"))


def test_the_recorded_ended_alert_is_not_in_force() -> None:
    """REAL: the frost advisory of Rondeau Park, listed by GeoMet with ``status_en`` ``ended`` while the official page said "no alert"."""
    item = recorded_ended_item()
    assert (item["properties"]["status_en"], item["properties"]["feature_name_en"]) == ("ended", "Chatham-Kent - Rondeau Park")
    assert parse_feature(item) is None
    assert eccc.is_ended(item["properties"]) and not eccc.is_ended(altered()["properties"])
    result = parse_items({"features": [item, *recorded_payload()["features"]]})
    assert len(result.vigilances) == 36 and result.skipped == 1


def test_an_alert_whose_type_says_it_ended_is_not_in_force_either() -> None:
    # SYNTHETIC: the word is ECCC's alert_type "ended" in the schema's spirit; it was not seen in alert_type.
    assert parse_feature(altered(alert_type="ended")) is None
    assert parse_feature(altered(status_en="Ended")) is None  # case does not matter


def test_an_item_without_identifier_is_not_in_force() -> None:
    feature = altered()
    del feature["id"]
    assert parse_feature(feature) is None
    result = parse_items({"features": [altered(alert_type="ended"), feature, altered()]})
    assert len(result.vigilances) == 1 and result.skipped == 2


@pytest.mark.asyncio
async def test_the_collector_does_not_serve_the_ended_alert_listed_with_the_others(sim_clock: SimClock) -> None:
    payload = recorded_payload()
    payload["features"] = [recorded_ended_item(), *payload["features"][1:]]  # Rondeau Park, now ended, instead of the one in force
    provider = Provider(answer=lambda request: httpx.Response(200, json=payload), latency_ticks=0)
    alerts = (await ECCCCollector(http_client=provider.client()).fetch_alerts()).value
    assert len(alerts.vigilances) == 35 and alerts.skipped == 1
    assert "Chatham-Kent - Rondeau Park" not in {v.area.description for v in alerts.vigilances}


def test_an_item_without_geometry_is_an_alert_with_no_shape() -> None:
    feature = altered()
    feature["geometry"] = None
    warning = parse_feature(feature)
    assert warning is not None and warning.shape is None and warning.area.has_polygon is False


def test_an_empty_list_is_a_valid_answer_and_a_list_of_nothing_readable_is_not() -> None:
    assert parse_items({"features": []}).vigilances == ()
    with pytest.raises(ValueError, match="no 'features' list"):
        parse_items({"error": "maintenance"})
    with pytest.raises(ValueError, match="none could be read"):
        parse_items({"features": [{"id": "x", "properties": "not an object"}]})


# ---------------------------------------------------------------- the requests


def geomet(payload: dict[str, Any]) -> Provider:
    """GeoMet as recorded: pages of ``limit`` items from ``offset``, no validator, no cache header."""

    def answer(request: httpx.Request) -> httpx.Response:
        offset, limit = int(request.url.params.get("offset", 0)), int(request.url.params["limit"])
        page = payload["features"][offset : offset + limit]
        return httpx.Response(200, json={**payload, "features": page, "numberReturned": len(page)})

    return Provider(answer=answer, latency_ticks=0)


@pytest.mark.asyncio
async def test_one_request_reads_all_of_canada_and_is_kept_five_minutes(sim_clock: SimClock) -> None:
    provider = geomet(recorded_payload())
    collector = ECCCCollector(http_client=provider.client())

    alerts = (await collector.fetch_alerts()).value
    assert provider.count == 1 and len(alerts.vigilances) == 36
    request = provider.requests[0]
    assert str(request.url).startswith("https://api.weather.gc.ca/collections/weather-alerts/items?")
    assert request.url.params["f"] == "json" and request.url.params["limit"] == "1000"
    assert "If-None-Match" not in request.headers and "If-Modified-Since" not in request.headers  # it sends no validator

    sim_clock.advance(299)
    await collector.fetch_alerts()
    assert provider.count == 1
    sim_clock.advance(2)
    await collector.fetch_alerts()
    assert provider.count == 2


@pytest.mark.asyncio
async def test_a_stormy_day_is_read_page_by_page(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eccc, "PAGE_SIZE", 10)
    provider = geomet(recorded_payload())
    collector = ECCCCollector(http_client=provider.client())
    alerts = (await collector.fetch_alerts()).value
    assert len(alerts.vigilances) == 36 and len({v.id for v in alerts.vigilances}) == 36
    assert [r.url.params["offset"] for r in provider.requests] == ["0", "10", "20", "30"]  # 4 pages: the last has 6 items


@pytest.mark.asyncio
async def test_the_pages_stop_at_the_limit(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eccc, "PAGE_SIZE", 2)
    monkeypatch.setattr(eccc, "MAX_PAGES", 3)
    provider = geomet(recorded_payload())
    collector = ECCCCollector(http_client=provider.client())
    assert len((await collector.fetch_alerts()).value.vigilances) == 6 and provider.count == 3


@pytest.mark.asyncio
async def test_a_failing_server_falls_back_on_the_last_list_and_says_so(sim_clock: SimClock) -> None:
    ok = geomet(recorded_payload())
    state = {"down": False}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500) if state["down"] else ok.answer(request)  # type: ignore[misc]

    collector = ECCCCollector(http_client=Provider(answer=answer, latency_ticks=0).client())
    await collector.fetch_alerts()
    state["down"] = True
    sim_clock.advance(301)
    fetched: Fetched[Any] = await collector.fetch_alerts()
    assert fetched.stale is True and len(fetched.value.vigilances) == 36


def test_the_collector_is_a_warnings_provider_with_the_chosen_cadence() -> None:
    collector = ECCCCollector()
    assert collector.provider_id == "eccc_alerts" and collector.domain == "warnings" and collector.ttl_seconds == 300
