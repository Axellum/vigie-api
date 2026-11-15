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
    # The 171 spots of the bundled file collapse by Open-Meteo grid cell (78 marine cells, 144 wind cells):
    # even 9 marine + 25 wind calls per unique cell stays far below the 10,000/day free quota.
    assert 9 * 78 + 25 * 144 < 0.5 * 10_000


@pytest.mark.asyncio
async def test_the_overview_of_every_spot_costs_at_most_9_requests_of_each_kind_a_day(
    sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 clients ask the overview every 10 minutes, then a spot each: one request per unique grid cell names the
    representatives, and a spot's own page reads the swell from it (its wind, an hour old at most, may be its own)."""
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)
    monkeypatch.setattr(surf, "_overview_memo", None)

    def answer(request: httpx.Request) -> httpx.Response:
        one = SWELL if "marine" in request.url.host else WIND
        lats = request.url.params["latitude"].split(",")
        if len(lats) == 1:
            return httpx.Response(200, json={**one, "latitude": float(lats[0])})
        return httpx.Response(
            200,
            json=[
                {**one, **({} if i == 0 else {"location_id": i}), "latitude": float(lat)}
                for i, lat in enumerate(lats)
            ],
        )

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
    # 171 spots across 78 marine + 144 wind cells at 9 refreshes/day = 1,998 calls/day, well below 10,000.
    assert 9 * (78 + 144) < 0.25 * 10_000


@pytest.mark.asyncio
async def test_spots_sharing_a_model_grid_cell_deduplicate_batch_and_single_fetches(sim_clock: SimClock) -> None:
    """Neighboring beaches in the same Open-Meteo grid cell collapse to one coordinate on refresh, share single-spot
    caches via ``_from_peer_cell``, and re-probe peers individually if the provider cell changes later."""
    nearby = (("a", 43.45, -1.60), ("b", 43.46, -1.61), ("c", 43.47, -1.62))
    cell_lat, cell_lon = 43.458336, -1.625

    def answer(request: httpx.Request) -> httpx.Response:
        one = SWELL if "marine" in request.url.host else WIND
        lats = request.url.params["latitude"].split(",")
        if len(lats) == 1:
            return httpx.Response(200, json={**one, "latitude": cell_lat, "longitude": cell_lon})
        return httpx.Response(
            200,
            json=[
                {**one, **({} if i == 0 else {"location_id": i}), "latitude": cell_lat, "longitude": cell_lon}
                for i in range(len(lats))
            ],
        )

    provider = Provider(answer=answer, latency_ticks=0)
    collector = SurfForecastCollector(http_client=provider.client())

    # Cycle 1: cells unknown -> queries all 3 coordinates, learns they share (43.458336, -1.625).
    out1, _ = await collector.fetch_all("swell", nearby)
    assert len(out1) == 3 and len(provider.requests[-1].url.params["latitude"].split(",")) == 3

    # Cycle 2 after TTL -> queries only 1 representative coordinate (3x cheaper) and still returns all 3 spots.
    sim_clock.advance(SurfForecastCollector.SWELL_TTL_SECONDS + 1)
    out2, _ = await collector.fetch_all("swell", nearby)
    assert len(out2) == 3 and "," not in provider.requests[-1].url.params["latitude"]
    assert all(out2[sid]["model_lat"] == cell_lat for sid in ("a", "b", "c"))

    # Individual single-spot fetch for "a" populates peer cache; "b" reuses it with 0 extra HTTP requests.
    sim_clock.advance(SurfForecastCollector.SWELL_TTL_SECONDS + 1)
    before = provider.count
    a_single, _ = await collector.fetch_swell(*nearby[0])
    b_single, _ = await collector.fetch_swell(*nearby[1])
    assert provider.count == before + 1 and a_single is b_single

    # If the provider later shifts the representative's cell, peers on the old cell are invalidated so the next batch re-probes all 3.
    cell_lat = 43.541664
    sim_clock.advance(SurfForecastCollector.SWELL_TTL_SECONDS + 1)
    await collector.fetch_all("swell", nearby)
    assert "," not in provider.requests[-1].url.params["latitude"]  # detected shift during this refresh
    sim_clock.advance(SurfForecastCollector.SWELL_TTL_SECONDS + 1)
    await collector.fetch_all("swell", nearby)
    assert len(provider.requests[-1].url.params["latitude"].split(",")) == 3  # re-probed every spot
