"""The surf forecast stays sober: a spot costs at most 9 swell and 25 wind requests a day, and the overview of every spot 9 of each
kind, whatever the number of clients.

A simulated day (``sim_clock``): 200 clients ask the forecast of three spots every 10 minutes, with every number of days. The
providers are simulated; every request is counted and weighed by the Open-Meteo rule. The swell is kept 3 hours and the wind 1 hour
(``docs/data-sources.md``, "Surf"), and the number of days is not in the cache key.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.collectors.surf import SurfForecastCollector
from app.collectors.weights import open_meteo_weight
from app.services import surf
from tests.helpers import Provider, SimClock

FIXTURES = Path(__file__).parent / "fixtures" / "surf"
SWELL = json.loads((FIXTURES / "mfwam-hossegor.json").read_text(encoding="utf-8"))
WIND = json.loads((FIXTURES / "wind-hossegor.json").read_text(encoding="utf-8"))
DAY_S = 86_400
SPOTS = ("a", "b", "c")
SPOT_FILE: dict[str, Any] = {
    "built_utc": "2026-10-28T22:00:00Z",
    "licence": "ODbL-1.0",
    "spots": [
        {"id": spot, "name": spot, "lat": 43.66 + i / 10, "lon": -1.44, "facing_deg": 280, "osm": f"way/{i}", "evidence": []}
        for i, spot in enumerate(SPOTS)
    ],
}


@pytest.mark.asyncio
async def test_a_day_of_clients_costs_each_spot_at_most_34_requests(
    sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)
    provider = Provider(answer=lambda r: httpx.Response(200, json=SWELL if "marine" in r.url.host else WIND), latency_ticks=0)
    collector = SurfForecastCollector(http_client=provider.client())
    clients = 200
    for _ in range(DAY_S // 600):
        await asyncio.gather(
            *(surf.forecast(collector, SPOTS[c % len(SPOTS)], 1 + c % 7, sim_clock.time()) for c in range(clients))
        )
        sim_clock.advance(600)
    per_spot: dict[tuple[str, str], int] = {}
    for request in provider.requests:
        kind = "swell" if "marine" in request.url.host else "wind"
        key = (request.url.params["latitude"], kind)
        per_spot[key] = per_spot.get(key, 0) + 1
    for (_, kind), calls in per_spot.items():
        assert calls <= (DAY_S // SurfForecastCollector.SWELL_TTL_SECONDS + 1 if kind == "swell" else DAY_S // 3600 + 1)
    assert len(per_spot) == 2 * len(SPOTS)
    weight = sum(open_meteo_weight(dict(r.url.params)) for r in provider.requests)
    assert provider.count <= len(SPOTS) * 34 and weight == provider.count  # every request weighs one call
    # The 55 or so spots of the bundled file, all asked all day, would weigh at most 34 calls each: under a fifth of the
    # 10,000 a day of the free quota.
    assert 34 * 60 < 0.21 * 10_000


@pytest.mark.asyncio
async def test_the_overview_of_every_spot_costs_at_most_9_requests_of_each_kind_a_day(
    sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 clients ask the overview every 10 minutes, then a spot each: one request per kind names every spot, and a spot's
    own page reads the swell from it (its wind, an hour old at most, may be its own)."""
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)
    monkeypatch.setattr(surf, "_overview_memo", None)

    def answer(request: httpx.Request) -> httpx.Response:
        one = SWELL if "marine" in request.url.host else WIND
        count = len(request.url.params["latitude"].split(","))
        if count == 1:
            return httpx.Response(200, json=one)
        return httpx.Response(200, json=[one] + [{**one, "location_id": i} for i in range(1, count)])

    provider = Provider(answer=answer, latency_ticks=0)
    collector = SurfForecastCollector(http_client=provider.client())
    clients = 200
    for _ in range(DAY_S // 600):
        await asyncio.gather(*(surf.overview(collector, sim_clock.time()) for _ in range(clients)))
        await asyncio.gather(*(surf.forecast(collector, SPOTS[c % len(SPOTS)], 3, sim_clock.time()) for c in range(clients)))
        sim_clock.advance(600)
    every = [r for r in provider.requests if "," in r.url.params["latitude"]]
    alone = [r for r in provider.requests if "," not in r.url.params["latitude"]]
    for marine in (True, False):
        assert len([r for r in every if ("marine" in r.url.host) is marine]) <= DAY_S // (3 * 3600) + 1
    assert not [r for r in alone if "marine" in r.url.host]  # the swell of a spot always came from the overview
    assert len(alone) <= len(SPOTS) * (DAY_S // 3600 + 1)
    weight = sum(open_meteo_weight(dict(r.url.params)) for r in every)
    assert weight <= 2 * (DAY_S // (3 * 3600) + 1) * len(SPOTS)  # one call per spot and per request
    # The 51 spots of the bundled file: at most 18 requests weighing 51 calls, 918 a day, under a tenth of the free quota.
    assert 18 * 51 < 0.1 * 10_000
