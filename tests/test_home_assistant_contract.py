"""The Home Assistant integration (``custom_components/vigie``) reads ``GET /v1/vigilances`` and ``GET /v1/earthquakes``: keep
what it reads true.

The integration is tested in its own environment (``tests_ha/``, Python and Home Assistant of their own, see
``docs/home-assistant.md``). Here, in the API's own CI, the answers its tests use are checked against the API's
models, so they cannot drift from the real shape: the recorded real answer, the builders, and the fake instance.
"""

from __future__ import annotations

import ast
import json
import threading
import typing
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app.models.earthquakes import EarthquakesResponse
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilanceCoverage, VigilancePhenomenon, VigilancesResponse
from tests_ha import payloads
from tools import fake_instance

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests_ha" / "fixtures").glob("vigilances_*.json"))
QUAKE_FIXTURES = sorted((ROOT / "tests_ha" / "fixtures").glob("earthquakes_*.json"))


def test_every_recorded_answer_is_checked() -> None:
    assert FIXTURES and QUAKE_FIXTURES
    assert sorted((ROOT / "tests_ha" / "fixtures").glob("*.json")) == sorted(FIXTURES + QUAKE_FIXTURES)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_the_recorded_answers_are_answers_of_the_api(path: Path) -> None:
    response = VigilancesResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert response.vigilances and response.coverage


@pytest.mark.parametrize("path", QUAKE_FIXTURES, ids=lambda p: p.name)
def test_the_recorded_earthquake_answers_are_answers_of_the_api(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    response = EarthquakesResponse.model_validate(raw)
    assert response.earthquakes and response.sources and response.attribution
    assert raw == json.loads(response.model_dump_json())  # every field the API sends, and none it does not


def test_the_earthquake_builders_of_the_integration_tests_make_answers_of_the_api() -> None:
    quakes = [payloads.earthquake(), payloads.earthquake("emsc_x", sources=("EMSC",), depth_km=None), payloads.earthquake("usgs_y", sources=("USGS", "EMSC"), tsunami=True)]
    for answer in (payloads.earthquakes_response(quakes), payloads.earthquakes_response([], answered=(False, False))):
        response = EarthquakesResponse.model_validate(answer)
        assert answer == json.loads(response.model_dump_json())


def test_the_builders_of_the_integration_tests_make_answers_of_the_api() -> None:
    every_phenomenon = [
        payloads.vigilance(f"w{i}", phenomenon=phenomenon.value, original=phenomenon.value.title(), level=level.value)
        for i, (phenomenon, level) in enumerate((p, SeverityLevel.ORANGE) for p in VigilancePhenomenon)
    ]
    river = payloads.vigilance("vigicrues:GA32", source="vigicrues", phenomenon="flood", original="3", distance_km=3.9, codes=(("VIGICRUES_SECTION", "GA32"),))
    without_level = payloads.vigilance("none", level=None, phenomenon=None, original=None)
    for answer in (
        payloads.response(every_phenomenon + [river, without_level], [payloads.coverage(), payloads.coverage("vigicrues", status="stale")]),
        payloads.response([], sources=[], note="No official warning source covers this point.", attribution=None),
        payloads.response([], [payloads.coverage(status="unavailable", not_matchable=None)]),
    ):
        VigilancesResponse.model_validate(answer)


def integration_constant(name: str) -> object:
    """A literal tuple assigned at the top level of the integration's ``models.py`` (read without importing Home Assistant)."""
    tree = ast.parse((ROOT / "custom_components" / "vigie" / "models.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def test_the_levels_and_statuses_the_integration_reads_are_the_ones_of_the_api() -> None:
    assert integration_constant("LEVEL_ORDER") == tuple(level.value.lower() for level in SeverityLevel)
    assert integration_constant("COVERAGE_STATUSES") == typing.get_args(VigilanceCoverage.model_fields["status"].annotation)


def test_the_fake_instance_speaks_the_language_of_the_api() -> None:
    assert fake_instance.PHENOMENA == tuple(phenomenon.value for phenomenon in VigilancePhenomenon)
    assert fake_instance.LEVELS == ("YELLOW", "ORANGE", "RED")
    scenario = fake_instance.Scenario()
    scenario.set("wind", "YELLOW", now=1_800_000_000)
    scenario.set("flood", "RED", now=1_800_000_000)
    scenario.set("wind", "ORANGE", now=1_800_000_100)  # raised: a new identifier, as MeteoAlarm re-issues it
    answer = fake_instance.vigilances(scenario, 45.0, 5.0, 5.0, now=1_800_000_100)
    parsed = VigilancesResponse.model_validate(answer)
    assert [(v.level, v.phenomenon) for v in parsed.vigilances] == [(SeverityLevel.RED, VigilancePhenomenon.FLOOD), (SeverityLevel.ORANGE, VigilancePhenomenon.WIND)]
    assert [v.id for v in parsed.vigilances] == ["fake:2", "fake:3"]
    assert "SIMULATION" in (parsed.attribution or "") and all("SIMULATION" in (v.event or "") for v in parsed.vigilances)
    assert scenario.remove("wind") and not scenario.remove("wind")
    scenario.clear()
    assert VigilancesResponse.model_validate(fake_instance.vigilances(scenario, 45.0, 5.0, 5.0)).vigilances == []
    health = fake_instance.health()
    assert health["status"] == "ok" and isinstance(health["version"], str) and health["scheduler"] == {"running": False}


def test_the_fake_instance_refuses_what_is_not_a_phenomenon_or_a_level() -> None:
    scenario = fake_instance.Scenario()
    with pytest.raises(ValueError):
        scenario.set("earthquake", "RED")
    with pytest.raises(ValueError):
        scenario.set("wind", "PURPLE")


def test_the_fake_instance_serves_over_http_on_the_loopback_only() -> None:
    scenario = fake_instance.Scenario()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake_instance.make_handler(scenario))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:

        def call(path: str, method: str = "GET") -> tuple[int, dict]:
            request = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
            try:
                with urllib.request.urlopen(request, timeout=5) as reply:
                    return reply.status, json.loads(reply.read())
            except urllib.error.HTTPError as err:
                return err.code, json.loads(err.read())

        assert call("/v1/health")[1]["status"] == "ok"
        assert call("/v1/vigilances")[0] == 422  # a position is required, as on the real route
        assert call("/fake/set?phenomenon=wind&level=orange", "POST")[0] == 200
        status, body = call("/v1/vigilances?lat=45&lon=5&river_radius_km=7")
        parsed = VigilancesResponse.model_validate(body)
        assert status == 200 and parsed.query.river_radius_km == 7.0 and [v.level for v in parsed.vigilances] == [SeverityLevel.ORANGE]
        assert call("/fake/set?phenomenon=earthquake&level=RED", "POST")[0] == 422
        assert call("/fake/remove?phenomenon=wind", "POST")[1] == {"removed": True}
        assert call("/fake/clear", "POST")[0] == 200 and call("/fake")[1] == {"warnings": []}
        assert call("/nothing")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
