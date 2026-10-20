"""``POST /v1/simulate`` is opt-in: it has no authentication and writes simulated alerts into the history.

The instance owner enables it with ``VIGIE_ENABLE_SIMULATION=1``; without it the route answers 403 and
changes nothing.
"""

from __future__ import annotations

import pytest

from app.models.schemas import SimulationScenario
from tests.conftest import World


def test_simulator_is_off_by_default(world: World) -> None:
    resp = world.client.post("/v1/simulate", json={"scenario": "mega_tsunami_japan"})
    assert resp.status_code == 403
    assert "VIGIE_ENABLE_SIMULATION" in resp.json()["detail"]


def test_a_refused_simulation_changes_nothing(world: World) -> None:
    world.client.post("/v1/simulate", json={"scenario": "cat5_hurricane_caribbean"})

    assert world.aggregator.simulation_active is False
    assert world.aggregator.active_scenario == SimulationScenario.CLEAR
    assert world.aggregator.simulated_hazards == []
    assert world.client.get("/v1/history").json()["count"] == 0  # nothing reached the hazard history
    assert world.client.get("/v1/snapshot").json()["simulation_active"] is False


def test_health_says_whether_the_simulator_is_enabled(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    assert world.client.get("/v1/health").json()["simulation_enabled"] is False
    monkeypatch.setenv("VIGIE_ENABLE_SIMULATION", "1")
    assert world.client.get("/v1/health").json()["simulation_enabled"] is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_simulator_runs_when_enabled(world: World, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VIGIE_ENABLE_SIMULATION", value)
    resp = world.client.post("/v1/simulate", json={"scenario": "volcano_eruption_iceland"})
    assert resp.status_code == 200
    assert resp.json()["simulation_active"] is True
    assert world.client.post("/v1/simulate", json={"scenario": "clear"}).json()["simulation_active"] is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2", "enabled"])
def test_simulator_stays_off_for_any_other_value(world: World, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VIGIE_ENABLE_SIMULATION", value)
    assert world.client.post("/v1/simulate", json={"scenario": "mega_tsunami_japan"}).status_code == 403


def test_dashboard_hides_the_simulator_buttons_until_the_instance_enables_it(world: World) -> None:
    html = world.client.get("/dashboard").text
    assert 'id="sim-controls" class="sim-controls" hidden' in html  # hidden until /v1/health says it is enabled
    assert "health.simulation_enabled" in html
    assert "VIGIE_ENABLE_SIMULATION" in html
