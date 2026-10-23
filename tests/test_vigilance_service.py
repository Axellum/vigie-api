"""The warnings for a point or a country (``VigilanceService``): the sources, the requests they cost, what is returned.

The providers are simulated behind ``httpx.MockTransport`` and serve the real feeds recorded on 2026-10-20; every
request they receive is counted, so the sobriety claims (one feed per country, one CAP document per warning, the
NWS answer shared with the hazards) are measured here, not assumed.
"""

from __future__ import annotations

import httpx
import pytest

from app.attribution import METEOALARM_DISCLAIMER, credit_text
from app.collectors.disasters import NOAANWSCollector
from app.collectors.meteoalarm import MeteoAlarmCapCollector, MeteoAlarmCollector
from app.models.schemas import SeverityLevel
from app.services.vigilance import (
    CAP_DOCUMENTS_PER_REQUEST,
    RECENT_COUNTRIES_MAX,
    RECENT_COUNTRIES_TTL_S,
    FollowedPoint,
    VigilanceService,
    is_active,
    keep_the_strongest,
    parse_languages,
    track_key,
)
from tests import vigilance_samples as samples
from tests.helpers import Provider, SimClock
from tests.vigilance_samples import warning
from tests.vigilance_upstream import Upstream, atom_entry, atom_feed, make_service


@pytest.fixture()
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture()
def service(upstream: Upstream, sim_clock: SimClock) -> VigilanceService:
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)  # the moment the feeds were recorded
    return make_service(upstream)


# ---------------------------------------------------------------- a point


async def test_a_point_in_a_warned_departement_gets_the_warning_with_the_words_of_the_issuer(
    service: VigilanceService, upstream: Upstream
) -> None:
    answer = await service.for_point(*samples.VALENCE)
    assert answer.countries == ["FR"] and (answer.query.lat, answer.query.lon) == samples.VALENCE
    assert answer.uncovered == [] and answer.note is None  # a country with a source is unchanged: no reason, no note
    assert [w.area.description for w in answer.vigilances] == ["Drôme"]
    drome = answer.vigilances[0]
    assert (drome.level, drome.phenomenon_original, drome.area.localization) == (SeverityLevel.YELLOW, "Flooding", "geocode")
    assert drome.issuer == "METEO-FRANCE" and {t.language for t in drome.texts} == {"fr-FR", "en-GB"}
    assert drome.attribution.endswith("| Issued by METEO-FRANCE")
    # The credit and the notice the terms of MeteoAlarm ask for come with the answer.
    assert answer.attribution == credit_text("meteoalarm") and answer.disclaimer == METEOALARM_DISCLAIMER
    # One feed and the one CAP document of this warning: nothing else is fetched.
    assert upstream.feed_requests == ["france"] and upstream.cap_requests == ["b77d3bc0-76a2-4886-8da8-bac8d99f57fc"]


async def test_the_coverage_says_what_was_read_and_what_could_not_be_matched(service: VigilanceService) -> None:
    [coverage] = (await service.for_point(*samples.VALENCE)).coverage
    assert (coverage.source, coverage.country, coverage.status) == ("meteoalarm", "FR", "ok")
    assert coverage.warnings_in_feed == 15 and coverage.warnings_not_matchable == 0  # 4 of the 19 are over at the recording time
    assert coverage.feed_updated_utc == samples.RECORDED_AT and coverage.fetched_age_s == 0
    assert coverage.note is None


async def test_a_point_without_a_warning_gets_an_empty_list_and_costs_no_cap_document(
    service: VigilanceService, upstream: Upstream
) -> None:
    answer = await service.for_point(*samples.PARIS)
    assert answer.vigilances == [] and answer.countries == ["FR"]
    assert answer.coverage[0].status == "ok" and answer.coverage[0].warnings_in_feed == 15
    assert upstream.cap_requests == []


async def test_the_strongest_warning_comes_first(service: VigilanceService) -> None:
    answer = await service.for_point(*samples.BASTIA)
    levels = [w.level for w in answer.vigilances]
    assert levels[0] == SeverityLevel.RED and levels == sorted(levels, key=lambda lv: -["GREEN", "YELLOW", "ORANGE", "RED"].index(lv.value))
    assert [(w.level.value, w.phenomenon_original) for w in answer.vigilances][0] == ("RED", "Rain-flood")


async def test_a_warning_that_is_over_is_not_returned(service: VigilanceService, sim_clock: SimClock) -> None:
    """The feed still lists the orange flooding of the Hérault, whose end was 22:00 UTC the day before."""
    now = await service.for_point(*samples.MONTPELLIER)
    assert [(w.level.value, w.phenomenon.value) for w in now.vigilances] == [("YELLOW", "flood")]
    earlier = make_service(Upstream())
    sim_clock.advance(-60_000)  # the evening before: both were in force
    before = await earlier.for_point(*samples.MONTPELLIER)
    assert [w.level.value for w in before.vigilances] == ["ORANGE", "YELLOW"]


async def test_a_point_in_a_swiss_polygon_is_matched_with_the_polygon(service: VigilanceService, upstream: Upstream) -> None:
    answer = await service.for_point(*samples.GLARUS)
    assert answer.countries == ["CH"] and [w.area.description for w in answer.vigilances] == ["Glarus Süd-Sernftal"]
    assert answer.vigilances[0].area.localization == "polygon"
    assert upstream.feed_requests == ["switzerland"]


async def test_a_point_in_a_country_whose_areas_have_no_shape_says_so(service: VigilanceService) -> None:
    answer = await service.for_point(*samples.CAGLIARI)
    assert answer.vigilances == []  # not "nothing to fear": the coverage tells why
    [coverage] = answer.coverage
    assert coverage.warnings_in_feed == 3 and coverage.warnings_not_matchable == 3
    assert "cannot be matched to a point" in (coverage.note or "") and "EMMA_ID" in (coverage.note or "")


async def test_a_point_nobody_covers_gets_a_note_and_no_request(service: VigilanceService, upstream: Upstream) -> None:
    answer = await service.for_point(35.6762, 139.6503)  # Tokyo: Japan has a border, and no source
    assert answer.vigilances == [] and answer.countries == [] and answer.coverage == [] and answer.disclaimer is None
    assert [(u.country, u.name, u.reason_code) for u in answer.uncovered] == [("JP", "Japan", "law_restricts")]
    assert (answer.note or "").startswith("Japan (JP): Japanese law reserves the giving of warnings")  # the country and why, not a bare "nothing"
    assert upstream.meteoalarm.count == 0 and upstream.nws_provider.count == 0


async def test_a_point_at_sea_is_in_no_country_and_says_so(service: VigilanceService, upstream: Upstream) -> None:
    answer = await service.for_point(0.0, -150.0)
    assert answer.countries == [] and answer.uncovered == [] and answer.coverage == []
    assert "No country was found for this point" in (answer.note or "") and "at sea" in (answer.note or "")
    assert upstream.meteoalarm.count == 0 and upstream.nws_provider.count == 0


async def test_two_countries_for_a_point_on_the_border(service: VigilanceService, upstream: Upstream) -> None:
    answer = await service.for_point(48.5734, 7.8152)  # Kehl, 2 km from France
    assert answer.countries == ["DE", "FR"]
    assert sorted(upstream.feed_requests) == ["france", "germany"]


async def test_the_languages_can_be_chosen(service: VigilanceService) -> None:
    only_french = await service.for_point(*samples.VALENCE, languages=("fr",))
    assert [t.language for t in only_french.vigilances[0].texts] == ["fr-FR"]


def test_the_language_parameter() -> None:
    assert parse_languages(None) is None and parse_languages("") is None and parse_languages(" , ") is None
    assert parse_languages("FR, en-GB ,") == ("fr", "en-gb")


# ---------------------------------------------------------------- sobriety: what an answer costs


async def test_the_second_answer_costs_nothing_and_the_feed_is_asked_again_after_three_minutes(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    await service.for_point(*samples.VALENCE)
    await service.for_point(*samples.BASTIA)
    await service.for_point(*samples.PARIS)
    assert upstream.feed_requests == ["france"]  # one feed serves every point of the country
    sim_clock.advance(179)
    await service.for_point(*samples.VALENCE)
    assert upstream.feed_requests == ["france"]
    sim_clock.advance(2)  # 181 s: the time to live of 180 s is over
    answer = await service.for_point(*samples.VALENCE)
    assert upstream.feed_requests == ["france", "france"]
    assert answer.vigilances[0].issuer == "METEO-FRANCE"
    # A CAP document never changes: the words of Drôme and Haute-Corse were asked once, whatever the number of answers.
    assert len(upstream.cap_requests) == len(set(upstream.cap_requests))


async def test_a_cap_document_is_asked_once_for_all_the_warnings_it_holds(service: VigilanceService, upstream: Upstream) -> None:
    answer = await service.for_country("FR")
    assert len(answer.vigilances) == 15
    # 19 warnings come from 7 documents; the 4 of one of them are over, so 6 documents are asked.
    assert len(upstream.cap_requests) == len(set(upstream.cap_requests)) == 6


async def test_at_most_forty_new_documents_are_asked_per_answer(upstream: Upstream, sim_clock: SimClock) -> None:
    count = CAP_DOCUMENTS_PER_REQUEST + 10
    upstream.default_cap = samples.fixture("cap_france_b77d3bc0.xml")
    upstream.feeds["france"] = atom_feed(*(atom_entry(f"00000000-0000-0000-0000-{n:012d}", area=0) for n in range(count)))
    service = make_service(upstream)
    first = await service.for_point(*samples.VALENCE)
    assert len(first.vigilances) == count and len(upstream.cap_requests) == CAP_DOCUMENTS_PER_REQUEST
    assert "10 of the warnings returned have no text yet" in (first.coverage[0].note or "")
    second = await service.for_point(*samples.VALENCE)
    assert len(upstream.cap_requests) == count  # the 10 others, never the 40 again
    assert second.coverage[0].note is None or "no text yet" not in second.coverage[0].note


async def test_a_missing_cap_document_leaves_the_texts_empty_and_is_not_asked_again_at_once(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    answer = await service.for_country("IT")  # no document of the recorded Italian feed exists any more
    assert len(answer.vigilances) == 3 and all(w.texts == [] and w.issuer is None for w in answer.vigilances)
    assert "no text yet" in (answer.coverage[0].note or "")
    asked = len(upstream.cap_requests)
    await service.for_country("IT")
    assert len(upstream.cap_requests) == asked  # the 404 is remembered for 15 minutes
    sim_clock.advance(901)
    await service.for_country("IT")
    assert len(upstream.cap_requests) > asked


async def test_only_cap_documents_at_the_address_of_meteoalarm_are_fetched(upstream: Upstream) -> None:
    collector = MeteoAlarmCapCollector(http_client=upstream.meteoalarm.client())
    with pytest.raises(ValueError, match="not a MeteoAlarm CAP document address"):
        await collector.fetch_document("https://evil.example/api/v1/warnings/x")
    with pytest.raises(ValueError):
        await collector.fetch_document("https://feeds.meteoalarm.org.evil.example/api/v1/warnings/x")
    assert upstream.meteoalarm.count == 0


async def test_the_cap_documents_of_ended_warnings_are_forgotten(
    upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry("b77d3bc0-76a2-4886-8da8-bac8d99f57fc"))
    service = make_service(upstream)
    await service.for_point(*samples.VALENCE)
    assert list(service.meteoalarm_cap._cache) == [
        "https://feeds.meteoalarm.org/api/v1/warnings/feeds-france/b77d3bc0-76a2-4886-8da8-bac8d99f57fc"
    ]
    sim_clock.advance(181)
    upstream.feeds["france"] = atom_feed()
    await service.for_point(*samples.VALENCE)
    assert list(service.meteoalarm_cap._cache) == []


async def test_the_meteoalarm_feed_is_not_asked_conditionally_since_it_gives_no_validators(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    await service.for_point(*samples.PARIS)
    sim_clock.advance(181)
    await service.for_point(*samples.PARIS)
    headers = [r.headers for r in upstream.meteoalarm.requests]
    assert all("if-none-match" not in h and "if-modified-since" not in h for h in headers)
    # The server answers 406 to a narrow Accept header (checked on 2026-10-20): the wildcard must stay.
    assert all("*/*" in h["accept"] for h in headers)


async def test_validators_would_be_used_if_the_server_started_to_give_them(upstream: Upstream, sim_clock: SimClock) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=samples.fixture("atom_france.xml"), headers={"ETag": '"v1"'})

    collector = MeteoAlarmCollector(http_client=Provider(answer=answer, latency_ticks=0).client())
    first = await collector.fetch_country("FR")
    sim_clock.advance(181)
    second = await collector.fetch_country("FR")
    assert len(seen) == 2 and seen[1].headers["if-none-match"] == '"v1"'
    assert second.value is first.value  # a 304 keeps the parsed feed


# ---------------------------------------------------------------- a country


async def test_a_country_lists_every_warning_even_those_that_cannot_be_matched_to_a_point(service: VigilanceService) -> None:
    answer = await service.for_country("it")
    assert answer.countries == ["IT"] and answer.query.country == "IT"
    assert len(answer.vigilances) == 3
    assert {w.area.localization for w in answer.vigilances} == {"none"}
    assert answer.coverage[0].warnings_not_matchable == 3


async def test_a_country_without_warnings_is_an_empty_but_covered_answer(service: VigilanceService) -> None:
    answer = await service.for_country("DK")
    assert answer.vigilances == [] and answer.coverage[0].status == "ok" and answer.coverage[0].warnings_in_feed == 0


async def test_a_country_without_a_source_is_refused(service: VigilanceService, upstream: Upstream) -> None:
    with pytest.raises(KeyError):
        await service.for_country("JP")
    assert upstream.meteoalarm.count == 0


# ---------------------------------------------------------------- a provider that does not answer


async def test_a_provider_that_does_not_answer_is_reported_not_hidden(service: VigilanceService, upstream: Upstream) -> None:
    upstream.feeds["france"] = 503
    answer = await service.for_point(*samples.VALENCE)
    assert answer.vigilances == []
    [coverage] = answer.coverage
    assert coverage.status == "unavailable" and "gave no answer" in (coverage.note or "")


async def test_a_provider_that_stops_answering_serves_its_last_feed_marked_stale(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    await service.for_point(*samples.VALENCE)
    upstream.feeds["france"] = 503
    sim_clock.advance(400)
    answer = await service.for_point(*samples.VALENCE)
    assert [w.area.description for w in answer.vigilances] == ["Drôme"]
    assert answer.coverage[0].status == "stale" and answer.coverage[0].fetched_age_s == 400


async def test_a_feed_that_is_not_a_feed_is_an_unavailable_source(service: VigilanceService, upstream: Upstream) -> None:
    upstream.feeds["france"] = b"<html><body>maintenance</body></html>"
    answer = await service.for_point(*samples.VALENCE)
    assert answer.coverage[0].status == "unavailable"


# ---------------------------------------------------------------- the United States (the answer of the hazards)


def first_polygon_point() -> tuple[float, float]:
    from tests.vigilance_samples import nws_payload

    vigilances = NOAANWSCollector.parse_vigilances(nws_payload())
    shape = next(v.shape for v in vigilances if v.shape is not None)
    point = shape.representative_point()
    return point.y, point.x


async def test_a_point_in_an_nws_polygon_gets_the_alert(service: VigilanceService, upstream: Upstream) -> None:
    lat, lon = first_polygon_point()
    answer = await service.for_point(lat, lon)
    assert answer.countries == ["US"] and len(answer.vigilances) >= 1
    alert = answer.vigilances[0]
    assert alert.source == "noaa_nws" and alert.area.localization == "polygon" and alert.texts[0].headline
    assert answer.disclaimer is None  # the notice is MeteoAlarm's
    assert answer.attribution == credit_text("noaa_nws")
    assert "Severe and Extreme" in (answer.coverage[0].note or "")
    assert upstream.nws_provider.count == 1 and upstream.meteoalarm.count == 0


async def test_the_nws_answer_is_shared_with_the_hazards_in_both_orders(upstream: Upstream, sim_clock: SimClock) -> None:
    hazards_first = NOAANWSCollector(http_client=upstream.nws_provider.client())
    await hazards_first.fetch()
    vigilances = await hazards_first.fetch_vigilances()
    assert upstream.nws_provider.count == 1 and len(vigilances.value) == 7

    upstream.nws_provider.requests.clear()
    vigilances_first = NOAANWSCollector(http_client=upstream.nws_provider.client())
    await vigilances_first.fetch_vigilances()
    await vigilances_first.fetch()
    assert upstream.nws_provider.count == 1


async def test_the_nws_alerts_that_name_zones_are_listed_by_country_not_matched_to_a_point(service: VigilanceService) -> None:
    answer = await service.for_country("US")
    assert len(answer.vigilances) == 7
    assert sum(1 for w in answer.vigilances if w.area.localization == "none") == 5
    assert answer.coverage[0].warnings_not_matchable == 5
    assert "UGC" in (answer.coverage[0].note or "")


# ---------------------------------------------------------------- GeoJSON


async def test_the_geojson_has_the_shape_of_each_warning_and_where_it_comes_from(service: VigilanceService) -> None:
    drome = service.to_geojson(await service.for_point(*samples.VALENCE))
    assert drome.type == "FeatureCollection" and len(drome.features) == 1
    feature = drome.features[0]
    assert feature.id == "meteoalarm:b77d3bc0-76a2-4886-8da8-bac8d99f57fc:6-0-1"
    assert feature.geometry is not None and feature.geometry["type"] in {"Polygon", "MultiPolygon"}
    assert feature.properties["geometry_source"] == "geocode:NUTS3:FR713" and "id" not in feature.properties
    assert feature.properties["level"] == "YELLOW" and feature.properties["texts"][0]["language"] == "fr-FR"
    assert drome.metadata["attribution"] == credit_text("meteoalarm") and drome.metadata["disclaimer"] == METEOALARM_DISCLAIMER

    swiss = service.to_geojson(await service.for_point(*samples.GLARUS))
    assert swiss.features[0].properties["geometry_source"] == "polygon" and swiss.features[0].geometry is not None

    italy = service.to_geojson(await service.for_country("IT"))
    assert all(f.geometry is None and f.properties["geometry_source"] is None for f in italy.features)


# ---------------------------------------------------------------- which feeds the scheduler keeps fresh


def test_the_countries_that_are_needed(upstream: Upstream, sim_clock: SimClock) -> None:
    zones = [
        {"id": "home", "name": "Home", "lat": samples.PARIS[0], "lon": samples.PARIS[1]},
        {"id": "far", "name": "Far", "lat": samples.REYKJAVIK[0], "lon": samples.REYKJAVIK[1]},
        {"id": "kourou", "name": "Kourou", "lat": 5.16, "lon": -52.65},  # no source
        {"id": "sf", "name": "San Francisco", "lat": samples.SAN_FRANCISCO[0], "lon": samples.SAN_FRANCISCO[1]},
    ]
    service = make_service(upstream, zones)
    # The United States are not a MeteoAlarm feed: their alerts come with the hazards.
    assert service.needed_countries() == ["FR", "IS"]
    assert service.needed_countries([samples.GLARUS]) == ["CH", "FR", "IS"]
    service.remember("IT")
    assert service.needed_countries() == ["FR", "IS", "IT"]


def test_recent_countries_are_few_and_forgotten(upstream: Upstream, sim_clock: SimClock) -> None:
    service = make_service(upstream)
    for country in ["FR", "DE", "ES", "IT", "PT", "BE", "NL", "CH", "AT", "NO"]:
        service.remember(country)
    assert service.recent_countries() == ["ES", "IT", "PT", "BE", "NL", "CH", "AT", "NO"]
    assert len(service.recent_countries()) == RECENT_COUNTRIES_MAX
    sim_clock.advance(RECENT_COUNTRIES_TTL_S + 1)
    assert service.recent_countries() == []


async def test_the_scheduler_keeps_only_the_needed_feeds_fresh(upstream: Upstream, sim_clock: SimClock) -> None:
    zones = [{"id": "home", "name": "Home", "lat": samples.PARIS[0], "lon": samples.PARIS[1]}]
    service = make_service(upstream, zones)
    points = [FollowedPoint(key="point:1", lat=samples.GLARUS[0], lon=samples.GLARUS[1], label={}, audience="followers")]
    await service.refresh(points)
    assert sorted(upstream.feed_requests) == ["france", "switzerland"]
    await service.refresh(points)  # cached: no request
    assert sorted(upstream.feed_requests) == ["france", "switzerland"]
    sim_clock.advance(181)
    await service.refresh(points)
    assert sorted(upstream.feed_requests) == ["france", "france", "switzerland", "switzerland"]
    assert upstream.cap_requests == []  # the scheduler never fetches the words


# ---------------------------------------------------------------- small rules


def test_a_warning_is_in_force_until_the_later_of_its_end_and_its_expiry() -> None:
    now = 1_000
    assert is_active(warning(expires=1_001), now) and is_active(warning(expires=1_000), now)
    assert not is_active(warning(expires=999), now)
    assert is_active(warning(expires=None), now)  # no end given: not dropped
    no_end = warning(expires=900).model_copy(update={"ends_utc": 2_000})
    assert is_active(no_end, now)  # the NWS gives an end later than the expiry of the message
    assert not is_active(warning(expires=2_000, status=samples.VigilanceStatus.CANCEL), now)  # a cancelled warning is over


def test_the_same_warning_is_recognised_from_one_update_to_the_next() -> None:
    first = warning("FR713", identifier="a.1")
    update = warning("FR713", level=SeverityLevel.ORANGE, identifier="a.2")  # an update has a new identifier
    other_area = warning("FR712", identifier="a.1")
    other_kind = warning("FR713", phenomenon=samples.VigilancePhenomenon.WIND, identifier="a.1")
    assert track_key(first) == track_key(update)
    assert len({track_key(first), track_key(other_area), track_key(other_kind)}) == 3


def test_of_two_overlapping_warnings_the_stronger_is_kept() -> None:
    yellow = warning("FR713", identifier="a")
    orange = warning("FR713", level=SeverityLevel.ORANGE, identifier="b")
    unknown = warning("FR713", level=None, identifier="c")
    assert keep_the_strongest([yellow, orange, unknown]) == {track_key(yellow): orange}
    assert keep_the_strongest([unknown, yellow])[track_key(yellow)] is yellow  # an unknown level is below every known one


def test_an_unknown_level_is_below_every_known_one() -> None:
    from app.services.vigilance import level_rank

    assert level_rank(None) < level_rank(SeverityLevel.GREEN) < level_rank(SeverityLevel.YELLOW) < level_rank(SeverityLevel.RED)
    green = warning("FR713", level=SeverityLevel.GREEN, identifier="a")
    unknown = warning("FR713", level=None, identifier="b")
    assert keep_the_strongest([unknown, green])[track_key(green)] is green
    assert keep_the_strongest([green, unknown])[track_key(green)] is green
