"""The warnings service with the registry services: one source a country, the push of changes, a silent service, and Spain.

The feeds and documents are the recorded ones of 2026-10-20 (``tests/fixtures/swic/``); the changes over time (a warning that
appears, a feed that stops answering) are made by changing what the simulated server answers. Nothing reaches the network.
"""

from __future__ import annotations

import pytest

from app.collectors.swic_registry import REGISTRY_URL
from app.collectors.swic_terms import COUNTRY_OVERRIDES
from app.services.vigilance import FollowedPoint, VigilanceService
from tests import swic_samples as samples
from tests.helpers import SimClock
from tests.swic_samples import FixtureServer

IN_FORCE_AT = 1_792_680_000
EMPTY = b'<rss version="2.0"><channel><title>no alert</title></channel></rss>'


def server_with(*names: str) -> FixtureServer:
    server = FixtureServer(*names)
    server.answers[REGISTRY_URL] = samples.registry_json()
    return server


def spain_point() -> FollowedPoint:
    """A position inside the area of Spain's recorded yellow wind warning."""
    from app.collectors import swic_cap as cap

    alert = cap.parse_alert(samples.fixture("cap_es_aemet_alert.xml"))
    area = alert.infos[0].areas[0]
    from app.geo import shapes_to_shape

    spot = shapes_to_shape(area.rings, area.circles).representative_point()  # type: ignore[union-attr]
    return FollowedPoint(key="zone:es", lat=spot.y, lon=spot.x, label={}, audience="all")


def swap(server: FixtureServer, name: str, body: bytes | int | None) -> None:
    """Replace what a recorded index answers (no validator: the body changes under the same address)."""
    url = samples.index_url(name)
    server.validators[url] = {}
    server.answers.pop(url, None)
    if body is not None:
        server.answers[url] = body


# ---------------------------------------------------------------------------------------------------- one source a country


def test_a_country_with_a_collector_keeps_it_whatever_the_registry_says() -> None:
    """US NWS, DE DWD, CA ECCC, NZ MetService, FR MeteoAlarm + Vigicrues, the other MeteoAlarm countries: the registry never replaces them."""
    service = samples.service_serving_everything("all")
    registry_countries = set(service._swic_index()[0])  # noqa: SLF001
    for country in ("US", "DE", "CA", "NZ", "FR", "IT", "GB", "NO", "CH", "IS"):
        assert country not in registry_countries, country
    listed = {item.country: item.sources for item in service.countries_listing()}
    assert listed["US"] == ["noaa_nws"] and listed["DE"] == ["dwd"] and listed["CA"] == ["eccc"] and listed["NZ"] == ["metservice"]
    assert listed["FR"] == ["meteoalarm", "vigicrues"] and listed["IT"] == ["meteoalarm"]


def test_spain_is_the_one_exception_and_the_evidence_is_written_down() -> None:
    """AEMET gives polygons where MeteoAlarm gives codes whose shape is not bundled (docs/decisions/0006-wmo-registry-services.md)."""
    assert COUNTRY_OVERRIDES == {"ES": "es-aemet-es"}
    service = samples.service_serving_everything("verified")
    assert [source.source_id for source in service.swic_sources("ES")] == ["es-aemet-es"]
    assert service._plan("ES") == ["swic:es-aemet-es"]  # noqa: SLF001
    off = samples.service_serving_everything("off")
    assert off._plan("ES") == ["meteoalarm"]  # noqa: SLF001


async def test_when_aemet_does_not_answer_meteoalarm_stands_in_and_says_so(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(IN_FORCE_AT))
    server = server_with()
    swap(server, "index_es_aemet.xml", None)
    server.bodies[samples.index_url("index_es_aemet.xml")] = b""
    server.answers[samples.index_url("index_es_aemet.xml")] = 503
    service = samples.service_serving_everything("verified", server)
    response = await service.for_country("ES")
    assert [(c.source, c.source_id, c.status) for c in response.coverage][0] == ("swic", "es-aemet-es", "unavailable")
    assert any(c.source == "meteoalarm" for c in response.coverage)  # the fallback is listed, as a fallback
    assert any("Used because the national service gave no answer" in (c.note or "") for c in response.coverage)


# ---------------------------------------------------------------------------------------------------- the push of changes


async def test_a_new_warning_of_a_registry_service_is_pushed_and_a_silent_service_ends_nothing(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(IN_FORCE_AT))
    server = server_with("index_es_aemet.xml")
    swap(server, "index_es_aemet.xml", EMPTY)
    service = samples.service_serving_everything("verified", server)
    point = spain_point()

    assert await service.poll_changes([point]) == []  # the first look sets the baseline: nothing in force
    sim_clock.advance(301)
    swap(server, "index_es_aemet.xml", samples.fixture("index_es_aemet.xml"))
    changes = await service.poll_changes([point])
    assert [(c.change, c.vigilance.source, c.vigilance.level.value) for _, c in changes] == [("appeared", "swic", "YELLOW")]
    assert changes[0][1].vigilance.attribution.startswith("Source: AEMET")

    # The service goes silent: its warning has not ended, and no change is pushed for it.
    sim_clock.advance(301)
    swap(server, "index_es_aemet.xml", 503)
    assert await service.poll_changes([point]) == []
    assert any(v.identifier.startswith("2.49.0.0.724.0.ES.20261020092533") for v in service._tracked[point.key].values())  # noqa: SLF001
    # It answers again with the same warning: nothing new.
    sim_clock.advance(301)
    swap(server, "index_es_aemet.xml", samples.fixture("index_es_aemet.xml"))
    assert await service.poll_changes([point]) == []


async def test_each_service_has_its_own_silence(sim_clock: SimClock) -> None:
    """A service that failed after a good read is served stale (answered); one that never answered is silent, and only it."""
    sim_clock.advance(samples.at(samples.RECORDED_AT))
    server = server_with("index_mx_smn.xml", "index_ar_smn.xml", "index_dz_onm.xml")
    server.answers[samples.index_url("index_dz_onm.xml")] = 503  # Algeria's service never answers
    service = samples.service_serving_everything("verified", server)
    mexico = FollowedPoint(key="zone:mx", lat=19.43, lon=-99.13, label={}, audience="all")
    argentina = FollowedPoint(key="zone:ar", lat=-34.6, lon=-58.4, label={}, audience="all")
    algeria = FollowedPoint(key="zone:dz", lat=36.75, lon=3.06, label={}, audience="all")
    await service.poll_changes([mexico, argentina, algeria])
    server.answers[samples.index_url("index_mx_smn.xml")] = 503  # Mexico answered once, then stops
    sim_clock.advance(301)
    _, answered, silent, _ = await service._current(argentina.lat, argentina.lon)  # noqa: SLF001
    assert answered == {"swic:ar-smn-es"} and silent == set()
    _, answered, silent, _ = await service._current(mexico.lat, mexico.lon)  # noqa: SLF001
    assert answered == {"swic:mx-smn-es"} and silent == set()  # its last answer still stands, marked stale in the coverage
    body = await service.for_country("MX")
    assert [(c.source_id, c.status) for c in body.coverage] == [("mx-smn-es", "stale")]
    _, answered, silent, _ = await service._current(algeria.lat, algeria.lon)  # noqa: SLF001
    assert silent == {"swic:dz-onm-fr"} and answered == set()  # nothing ever came from it: no word about "nothing in force"
    assert isinstance(service, VigilanceService)


async def test_the_registry_is_read_once_and_the_snapshot_serves_until_then(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(IN_FORCE_AT))
    server = server_with()
    service = samples.service_serving_everything("verified", server)
    assert "MX" in service.supported_countries()  # known from the snapshot before any request
    assert server.count(REGISTRY_URL) == 0
    await service.ensure_registry()
    await service.ensure_registry()
    assert server.count(REGISTRY_URL) == 1
    # The registry does not answer: the snapshot stays, and it is asked again in an hour, not in a minute.
    down = server_with()
    down.answers[REGISTRY_URL] = 503
    other = samples.service_serving_everything("verified", down)
    await other.ensure_registry()
    assert "MX" in other.supported_countries() and down.count(REGISTRY_URL) == 1
    sim_clock.advance(600)
    await other.ensure_registry()
    assert down.count(REGISTRY_URL) == 1
    sim_clock.advance(3600)
    await other.ensure_registry()
    assert down.count(REGISTRY_URL) == 2


@pytest.mark.parametrize("country", ["MX", "AR", "DZ", "JM", "PH", "BB", "ES"])
def test_every_verified_country_is_served_by_default(country: str) -> None:
    service = samples.service_serving_everything("verified")
    assert country in service.supported_countries()
    assert [item for item in service.countries_listing() if item.country == country][0].licence_verified is True
