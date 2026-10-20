"""``GET /v1/vigilances/countries`` and the warnings of the services of the WMO registry through the application.

The application runs with the recorded feeds of 2026-10-20 (``tests/fixtures/swic/``) in place of the services. Nothing reaches
the network. The settings are the ones of ``VIGIE_WMO_SOURCES``: ``verified`` (the default), ``all`` and ``off``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.collectors.swic import SwicCollector
from app.collectors.swic_registry import REGISTRY_URL, SwicRegistryCollector
from app.collectors.swic_terms import EXCLUDED, TERMS
from app.services.geoindex import default_countries
from tests import swic_samples as samples
from tests.conftest import World
from tests.helpers import SimClock
from tests.swic_samples import FixtureServer

IN_FORCE_AT = 1_792_680_000  # 2026-10-22T14:40Z: Spain's yellow wind warning of the recording is in force


def use(world: World, monkeypatch: pytest.MonkeyPatch, mode: str, server: FixtureServer | None = None) -> FixtureServer:
    """Put the registry services of the application on a ``FixtureServer`` and set ``VIGIE_WMO_SOURCES``."""
    served = server or FixtureServer(*samples.MANIFEST["indexes"])
    served.answers[REGISTRY_URL] = samples.registry_json()
    client = served.client()
    service = world.aggregator.vigilances
    monkeypatch.setattr(service, "swic", SwicCollector(http_client=client))
    monkeypatch.setattr(service, "swic_registry", SwicRegistryCollector(http_client=client))
    monkeypatch.setattr(service, "swic_mode", mode)
    for name, value in (("_registry", None), ("_registry_checked_at", None), ("_registry_failed", False), ("_swic_cache", None)):
        monkeypatch.setattr(service, name, value)
    return served


def listing(world: World) -> dict[str, dict[str, Any]]:
    response = world.client.get("/v1/vigilances/countries")
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["country"] for item in items] == sorted(item["country"] for item in items)
    assert all(set(item) == {"country", "sources", "licence_verified"} for item in items)
    return {item["country"]: item for item in items}


@pytest.fixture()
def served(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> World:
    sim_clock.advance(samples.at(IN_FORCE_AT))
    return world


def test_the_default_instance_lists_the_collectors_countries_and_the_verified_services(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    use(served, monkeypatch, "verified")
    countries = listing(served)
    # The countries that already had a source, with the sources the API asks for them.
    assert countries["FR"] == {"country": "FR", "sources": ["meteoalarm", "vigicrues"], "licence_verified": True}
    assert countries["DE"] == {"country": "DE", "sources": ["dwd"], "licence_verified": True}
    assert countries["US"] == {"country": "US", "sources": ["noaa_nws", "tsunami"], "licence_verified": True}
    assert countries["CA"] == {"country": "CA", "sources": ["eccc", "tsunami"], "licence_verified": True}
    assert countries["NZ"] == {"country": "NZ", "sources": ["metservice"], "licence_verified": False}  # MetService's terms are unverified
    assert countries["IS"]["sources"] == ["meteoalarm"] and countries["IS"]["licence_verified"] is True
    # Spain: AEMET's own feed (polygons), MeteoAlarm only stands in when AEMET gives no answer.
    assert countries["ES"] == {"country": "ES", "sources": ["swic:es-aemet-es"], "licence_verified": True}
    # The verified services of the registry, and only them.
    registry = {code: item for code, item in countries.items() if any(name.startswith("swic:") for name in item["sources"])}
    verified = {t.country: t.source_id for t in TERMS if t.status == "verified"}
    assert {code: item["sources"] for code, item in registry.items()} == {code: [f"swic:{sid}"] for code, sid in verified.items()}
    assert all(item["licence_verified"] is True for item in registry.values())
    assert "IN" not in countries and "RU" not in countries and "BR" not in countries  # unverified: not served by default
    assert "JP" not in countries


def test_all_serves_every_service_that_is_not_excluded_and_says_which_are_unverified(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    use(served, monkeypatch, "all")
    countries = listing(served)
    assert {"MX", "AR", "DZ", "JM", "PH", "BB", "ES"} <= set(countries)
    assert countries["MX"]["licence_verified"] is True
    assert countries["IN"]["licence_verified"] is False and all(name.startswith("swic:") for name in countries["IN"]["sources"])
    assert countries["RU"]["licence_verified"] is False
    assert "HK" not in countries and "CN" not in countries  # the excluded services, whatever the setting
    assert "hk-hko-xx" in EXCLUDED and "cn-cma-xx" in EXCLUDED
    assert len(countries) > 90
    # A country that has a collector keeps it: Germany, the United States and France have no registry service in front of them.
    assert countries["DE"]["sources"] == ["dwd"] and countries["US"]["sources"] == ["noaa_nws", "tsunami"]
    assert countries["FR"]["sources"] == ["meteoalarm", "vigicrues"]
    # Every country listed can be asked by a position: it has a border.
    assert set(countries) <= set(default_countries().countries())


def test_off_serves_the_collectors_countries_only(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    use(served, monkeypatch, "off")
    monkeypatch.setattr(served.aggregator.vigilances, "swic", None)
    countries = listing(served)
    assert "MX" not in countries and "AR" not in countries
    assert countries["ES"] == {"country": "ES", "sources": ["meteoalarm"], "licence_verified": True}  # MeteoAlarm's feed, as before
    assert len(countries) == 42


def test_new_zealand_is_not_listed_when_metservice_is_turned_off(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    use(served, monkeypatch, "verified")
    assert "NZ" in listing(served)
    monkeypatch.setattr(served.aggregator.vigilances, "metservice", None)
    assert "NZ" not in listing(served)


def test_a_country_nobody_serves_is_a_404_that_points_to_the_list(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    use(served, monkeypatch, "verified")
    response = served.client.get("/v1/vigilances", params={"country": "IN"})  # unverified: not served by default
    assert response.status_code == 404 and "/v1/vigilances/countries" in response.json()["detail"]
    use(served, monkeypatch, "all")
    assert served.client.get("/v1/vigilances", params={"country": "IN"}).status_code == 200


def test_spain_is_answered_by_aemet_with_a_coverage_line_of_its_own(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    server = use(served, monkeypatch, "verified")
    body = served.client.get("/v1/vigilances", params={"country": "ES"}).json()
    assert body["countries"] == ["ES"]
    [line] = body["coverage"]
    assert (line["source"], line["source_id"], line["country"], line["status"]) == ("swic", "es-aemet-es", "ES", "ok")
    assert line["warnings_in_feed"] == 1 and line["warnings_not_matchable"] == 0
    [warning] = body["vigilances"]
    assert warning["source"] == "swic" and warning["identifier"].startswith("2.49.0.0.724.0.ES.20261020092533")
    assert warning["id"].startswith("swic:es-aemet-es:") and warning["id"].endswith(":0.0")
    assert (warning["level"], warning["level_original"], warning["phenomenon"]) == ("YELLOW", "amarillo", "wind")
    assert warning["derived"] == ["status", "level", "phenomenon"]
    assert [text["language"] for text in warning["texts"]] == ["es-ES", "en-GB"]
    assert warning["area"]["geocodes"] == [{"scheme": "AEMET-Meteoalerta zona", "value": "712701"}] and warning["area"]["has_polygon"] is True
    # The credit names the issuer, the service and the registry; the texts of the issuer are untouched.
    assert "AEMET" in warning["attribution"] and "WMO Severe Weather Information Centre" in warning["attribution"]
    assert "Issued by AEMET" in warning["attribution"]
    assert "AEMET" in body["attribution"] and "WMO" in body["attribution"]
    assert server.count(samples.index_url("index_es_aemet.xml")) == 1


def test_the_geojson_keeps_the_shape_the_site_reads(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same properties as any other source, the geocodes and the identifier the site builds its ids from, the coverage in the metadata."""
    use(served, monkeypatch, "verified")
    body = served.client.get("/v1/vigilances.geojson", params={"country": "ES"}).json()
    assert body["type"] == "FeatureCollection" and len(body["features"]) == 1
    feature = body["features"][0]
    expected = {
        "source", "country", "attribution", "identifier", "sent_utc", "effective_utc", "onset_utc", "expires_utc", "ends_utc", "message_type",
        "status", "level", "level_original", "phenomenon", "phenomenon_original", "event", "issuer", "texts", "area", "distance_km", "link",
        "web", "derived", "geometry_source",
    }
    assert set(feature["properties"]) == expected
    assert feature["properties"]["geometry_source"] == "polygon" and feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert feature["properties"]["area"]["geocodes"][0]["value"] == "712701"
    assert body["metadata"]["coverage"][0]["source_id"] == "es-aemet-es"
    assert "AEMET" in body["metadata"]["attribution"] and set(body["metadata"]) >= {"query", "countries", "coverage", "attribution", "disclaimer", "note", "server_utc"}


def test_the_warnings_of_a_service_that_only_names_codes_are_counted_not_placed(
    served: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Philippines' tropical cyclone warnings name SAME codes and carry no polygon."""
    sim_clock.now = samples.at(samples.RECORDED_AT)  # the tropical cyclone warnings of the recording are in force
    use(served, monkeypatch, "verified")
    body = served.client.get("/v1/vigilances", params={"country": "PH"}).json()
    [line] = body["coverage"]
    assert line["source_id"] == "ph-pagasa-en" and line["warnings_in_feed"] >= 1
    assert line["warnings_not_matchable"] == line["warnings_in_feed"]
    assert all(warning["area"]["has_polygon"] is False for warning in body["vigilances"])
    # Asked by a position inside the Philippines, such a warning is not placed: the answer says so instead of saying "nothing".
    point = served.client.get("/v1/vigilances", params={"lat": 14.6, "lon": 121.0}).json()
    assert point["countries"] == ["PH"] and point["vigilances"] == []
    assert point["coverage"][0]["warnings_not_matchable"] == line["warnings_not_matchable"]


def test_a_service_that_does_not_answer_is_unavailable_never_nothing_in_force(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    server = FixtureServer(*samples.MANIFEST["indexes"])
    server.answers[samples.index_url("index_mx_smn.xml")] = 503
    use(served, monkeypatch, "verified", server)
    body = served.client.get("/v1/vigilances", params={"country": "MX"}).json()
    [line] = body["coverage"]
    assert (line["source"], line["source_id"], line["status"]) == ("swic", "mx-smn-es", "unavailable")
    assert body["vigilances"] == [] and "no answer" in (line["note"] or "").lower()


def test_a_second_service_of_a_country_is_its_own_coverage_line(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """India has two services in the registry; with ``all`` each is a line (the IMD's feed lives in the WMO bucket and answers nothing)."""
    use(served, monkeypatch, "all")
    body = served.client.get("/v1/vigilances", params={"country": "IN"}).json()
    lines = {line["source_id"]: line for line in body["coverage"]}
    assert sorted(lines) == ["in-imd-en", "in-ndma-xx"]
    assert all(line["source"] == "swic" for line in lines.values())
    assert lines["in-ndma-xx"]["status"] == "ok" and "VIGIE_WMO_SOURCES=all" in lines["in-ndma-xx"]["note"]  # said in the answer
    assert lines["in-imd-en"]["status"] == "unavailable"  # nothing recorded for it: a silent service is never "nothing in force"
