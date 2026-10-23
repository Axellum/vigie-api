"""The optional admin token (``VIGIE_ADMIN_TOKEN``): every route that changes something asks for it when it is set.

Without it nothing changes (the network is trusted, as before). With it, a request that is not a read needs ``Authorization: Bearer
<token>``. A test lists the routes of the application and fails if one with a write method answers without the token, so a route added
later cannot escape (``app/security.py``). Nothing reaches the network.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from starlette.routing import BaseRoute, Mount, WebSocketRoute

from app import security
from app.main import app
from app.security import READ_METHODS, WRITE_METHODS
from tests.conftest import World

# A value made for the tests, put together from words so that no secret scanner takes the line for a secret (it is not one).
TOKEN = "-".join(["not", "a", "real", "value", "for", "tests"])
WRONG = "the-wrong-one-for-tests"
RIGHT = {"Authorization": f"Bearer {TOKEN}"}
ROOT = Path(__file__).resolve().parents[1]
ZONE = {"id": "z_token", "name": "Token test", "lat": 50.6, "lon": 3.05, "utc_offset_s": 3600}
WATCHED_POINT = {"name": "Etna", "kind": "volcano", "lat": 37.751, "lon": 14.994, "radius_km": 30.0}

# What the application had when the token was added: if one goes, or one comes, this is the place that says so. A route that is not here is
# protected all the same (the test below does not read this list); it only has to be added for the documents to be right.
KNOWN_WRITE_ROUTES = {
    ("POST", "/v1/scheduler/trigger"),
    ("POST", "/v1/zones"),
    ("DELETE", "/v1/zones/{zone_id}"),
    ("POST", "/v1/watched-points"),
    ("DELETE", "/v1/watched-points/{point_id}"),
    ("POST", "/v1/simulate"),
}


@pytest.fixture()
def token_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_ADMIN_TOKEN", TOKEN)


def walk(routes: list[BaseRoute], prefix: str = "") -> list[tuple[BaseRoute, str]]:
    """Every route of the application with its full path: the routers that were included (FastAPI keeps each as one lazy route that
    holds the router and its prefix) and the sub-applications that were mounted are walked into, so a route cannot hide in one."""
    found: list[tuple[BaseRoute, str]] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            found.extend(walk(list(included.routes), prefix + (getattr(getattr(route, "include_context", None), "prefix", "") or "")))
        elif isinstance(route, Mount):
            found.extend(walk(list(route.routes), prefix + route.path))
        else:
            found.append((route, prefix + str(getattr(route, "path", ""))))
    return found


def routes_with_methods() -> list[tuple[str, str]]:
    """``(method, path)`` of every HTTP route of the application, a route with several methods once for each."""
    found: list[tuple[str, str]] = []
    for route, path in walk(list(app.routes)):
        methods = getattr(route, "methods", None)
        if methods:
            found.extend((method, path) for method in sorted(methods))
    return found


# Written here, not read from the code under test: the list of what counts as a read must not be able to loosen its own test.
READS = frozenset({"GET", "HEAD", "OPTIONS"})


def write_routes() -> list[tuple[str, str]]:
    return [(method, path) for method, path in routes_with_methods() if method not in READS]


def test_what_the_code_counts_as_a_read_is_what_the_test_counts() -> None:
    assert READ_METHODS == READS and WRITE_METHODS == {"POST", "PUT", "PATCH", "DELETE"}


def concrete(path: str) -> str:
    """A path with its parameters filled in with anything (the token is checked before the route is looked at)."""
    return re.sub(r"\{[^}]+\}", "x", path)


# ---------------------------------------------------------------- no token set: nothing changes


def test_without_a_token_the_write_routes_work_as_they_always_did(world: World) -> None:
    assert world.client.post("/v1/zones", json=ZONE).status_code == 200
    assert world.client.delete("/v1/zones/z_token").status_code == 200
    assert world.client.post("/v1/scheduler/trigger").status_code == 200
    assert world.client.post("/v1/simulate", json={"scenario": "clear"}).status_code == 403  # the switch, as before
    assert world.client.post("/v1/watched-points", json=WATCHED_POINT).status_code == 403
    assert "Authorization" not in world.client.headers


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_a_blank_token_is_no_token(world: World, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VIGIE_ADMIN_TOKEN", value)
    assert world.client.post("/v1/zones", json=ZONE).status_code == 200
    assert world.client.delete("/v1/zones/z_token").status_code == 200
    assert world.client.get("/v1/health").json()["admin_token_set"] is False


# ---------------------------------------------------------------- a token set: every write needs it


def test_the_routes_of_the_application_are_listed_and_the_known_write_routes_are_among_them() -> None:
    routes = routes_with_methods()
    assert len(routes) > 20 and ("GET", "/v1/health") in routes
    assert ("GET", "/dashboard") in routes  # a route of an included router: the walk goes into them
    assert set(write_routes()) >= KNOWN_WRITE_ROUTES, sorted(KNOWN_WRITE_ROUTES - set(write_routes()))
    assert {method for method, _ in write_routes()} <= WRITE_METHODS  # the application defines no other kind of write
    # What the OpenAPI document lists is a subset of the walk (a route can be left out of the document; it is not left out of the walk).
    document = app.openapi()
    documented = {(method.upper(), path) for path, item in document["paths"].items() for method in item if method.upper() in {"GET", *WRITE_METHODS}}
    assert documented <= set(routes), sorted(documented - set(routes))


@pytest.mark.parametrize(("method", "path"), sorted(set(write_routes())), ids=lambda value: str(value))
def test_every_write_route_of_the_application_refuses_a_request_without_the_token(
    world: World, token_on: None, method: str, path: str
) -> None:
    """The test that lets no route escape: it reads ``app.routes``, not a list of mine."""
    url = concrete(path)
    missing = world.client.request(method, url)
    assert missing.status_code == 401, f"{method} {path} answers {missing.status_code} without the token"
    assert missing.headers["www-authenticate"] == "Bearer"
    wrong = world.client.request(method, url, headers={"Authorization": f"Bearer {WRONG}"})
    assert wrong.status_code == 401, f"{method} {path} answers {wrong.status_code} with a wrong token"
    other_scheme = world.client.request(method, url, headers={"Authorization": f"Basic {TOKEN}"})
    assert other_scheme.status_code == 401, f"{method} {path}: only the Bearer scheme is accepted"
    # With the right token the request reaches the route (whatever the route then answers: not a 401).
    right = world.client.request(method, url, headers=RIGHT)
    assert right.status_code != 401, f"{method} {path}: the right token is refused"


def test_a_route_added_tomorrow_is_protected_without_anyone_remembering_to(world: World, token_on: None) -> None:
    router = APIRouter()

    @router.post("/v1/_added_later")
    async def added_later() -> dict[str, bool]:
        return {"changed": True}

    @router.put("/v1/_added_later/{item}")
    async def put_later(item: str) -> dict[str, str]:
        return {"item": item}

    @router.patch("/v1/_added_later/{item}")
    async def patch_later(item: str) -> dict[str, str]:
        return {"item": item}

    hidden = APIRouter()

    @hidden.delete("/_hidden", include_in_schema=False)  # left out of the OpenAPI document: only the walk of the routes finds it
    async def hidden_delete() -> dict[str, bool]:
        return {"changed": True}

    before = list(app.router.routes)
    app.include_router(router)
    app.include_router(hidden, prefix="/v1/extra")
    try:
        added = {("POST", "/v1/_added_later"), ("PUT", "/v1/_added_later/{item}"), ("PATCH", "/v1/_added_later/{item}"), ("DELETE", "/v1/extra/_hidden")}
        assert added <= set(write_routes())  # the walk finds them, the one of a router with a prefix and the one the document hides too
        for method, url in (("POST", "/v1/_added_later"), ("PUT", "/v1/_added_later/a"), ("PATCH", "/v1/_added_later/a"), ("DELETE", "/v1/extra/_hidden")):
            assert world.client.request(method, url).status_code == 401, method
            assert world.client.request(method, url, headers=RIGHT).status_code == 200, method
    finally:
        app.router.routes[:] = before
    assert world.client.post("/v1/_added_later", headers=RIGHT).status_code == 404  # gone again


def test_the_right_token_does_the_work(world: World, token_on: None) -> None:
    created = world.client.post("/v1/zones", json=ZONE, headers=RIGHT)
    assert created.status_code == 200 and created.json()["id"] == "z_token"
    assert world.client.delete("/v1/zones/z_token", headers=RIGHT).status_code == 200
    assert world.client.delete("/v1/zones/z_token", headers=RIGHT).status_code == 404  # the route's own answer
    assert world.client.post("/v1/scheduler/trigger", headers=RIGHT).status_code == 200


@pytest.mark.parametrize("header", [f"bearer {TOKEN}", f"BEARER {TOKEN}", f"Bearer   {TOKEN}  "])
def test_the_scheme_is_case_insensitive_and_spaces_around_the_token_do_not_matter(world: World, token_on: None, header: str) -> None:
    assert world.client.post("/v1/scheduler/trigger", headers={"Authorization": header}).status_code == 200


def test_a_token_with_spaces_around_it_in_the_setting_is_the_trimmed_one(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_ADMIN_TOKEN", f"  {TOKEN}\n")
    assert world.client.post("/v1/scheduler/trigger", headers=RIGHT).status_code == 200


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", TOKEN, f"Token {TOKEN}", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"Bearer {TOKEN.upper()}", f"Bearer {TOKEN} {TOKEN}"],
)
def test_anything_but_the_token_is_refused(world: World, token_on: None, header: str) -> None:
    response = world.client.post("/v1/scheduler/trigger", headers={"Authorization": header})
    assert response.status_code == 401


def test_the_refusal_comes_before_the_body_is_read(world: World, token_on: None) -> None:
    """A request without the token learns nothing about the route: not what a valid body is, not that the id exists."""
    assert world.client.post("/v1/zones", json={}).status_code == 401  # a valid token and an empty body would be a 422
    assert world.client.post("/v1/zones", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 401
    assert world.client.delete("/v1/zones/never-existed").status_code == 401  # and not the 404 of a zone that does not exist
    assert world.client.post("/v1/nowhere").status_code == 401  # nor which paths exist
    assert world.client.request("POST", "/v1/zones", headers=RIGHT, json={}).status_code == 422


def test_the_two_messages_say_what_to_send_and_never_the_token(world: World, token_on: None) -> None:
    missing = world.client.post("/v1/scheduler/trigger").json()["detail"]
    wrong = world.client.post("/v1/scheduler/trigger", headers={"Authorization": f"Bearer {WRONG}"}).json()["detail"]
    assert "Authorization: Bearer" in missing and "VIGIE_ADMIN_TOKEN" in missing
    assert "not the one of this instance" in wrong and "VIGIE_ADMIN_TOKEN" in wrong
    assert missing != wrong
    for text in (missing, wrong):
        assert TOKEN not in text and WRONG not in text


def test_an_unusual_method_needs_the_token_too(world: World, token_on: None) -> None:
    for method in ("TRACE", "PROPFIND", "PURGE"):
        assert world.client.request(method, "/v1/zones").status_code == 401, method


# ---------------------------------------------------------------- the reads never need it


def get_routes() -> list[str]:
    return sorted({path for method, path in routes_with_methods() if method == "GET"})


@pytest.mark.parametrize("path", get_routes())
def test_a_read_never_needs_the_token(world: World, token_on: None, path: str) -> None:
    response = world.client.get(concrete(path))
    assert response.status_code != 401, f"GET {path} asks for the token"


def test_head_and_options_never_need_it_and_a_browser_can_read_the_refusal(world: World, token_on: None) -> None:
    assert world.client.head("/v1/health").status_code != 401
    preflight = world.client.options(
        "/v1/zones", headers={"Origin": "https://example.org", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    )
    assert preflight.status_code == 200 and "authorization" in preflight.headers["access-control-allow-headers"].lower()
    refused = world.client.post("/v1/zones", json=ZONE, headers={"Origin": "https://example.org"})
    assert refused.status_code == 401 and refused.headers.get("access-control-allow-origin")  # the CORS middleware wraps the check


def test_the_websocket_is_not_a_write_route_and_stays_open(world: World, token_on: None) -> None:
    assert any(isinstance(route, WebSocketRoute) and path == "/v1/stream" for route, path in walk(list(app.routes)))
    with world.client.websocket_connect("/v1/stream") as socket:
        assert socket.receive_json()["event"] == "initial_snapshot"
        socket.send_json({"action": "follow", "lat": 0.0, "lon": -150.0})  # a point at sea: no source is asked
        assert socket.receive_json()["event"] == "vigilance_state"


# ---------------------------------------------------------------- it stacks with the switches that turn a route off


def test_the_token_is_checked_before_the_switch_that_turns_a_route_off(world: World, token_on: None) -> None:
    assert world.client.post("/v1/simulate", json={"scenario": "clear"}).status_code == 401  # no token: not even told that it is off
    off = world.client.post("/v1/simulate", json={"scenario": "clear"}, headers=RIGHT)
    assert off.status_code == 403 and "VIGIE_ENABLE_SIMULATION" in off.json()["detail"]  # the token does not turn the route on
    assert world.client.post("/v1/watched-points", json=WATCHED_POINT).status_code == 401
    off_points = world.client.post("/v1/watched-points", json=WATCHED_POINT, headers=RIGHT)
    assert off_points.status_code == 403 and "VIGIE_ENABLE_WATCHED_POINTS_WRITE" in off_points.json()["detail"]


def test_with_the_switch_on_and_the_token_a_write_goes_through(world: World, token_on: None, simulation_on: None, watched_points_write_on: None) -> None:
    assert world.client.post("/v1/simulate", json={"scenario": "clear"}).status_code == 401
    assert world.client.post("/v1/simulate", json={"scenario": "clear"}, headers=RIGHT).status_code == 200
    assert world.client.post("/v1/watched-points", json=WATCHED_POINT, headers=RIGHT).status_code == 200


# ---------------------------------------------------------------- the token is a secret


def test_the_token_never_appears_in_a_log_an_error_or_the_health(world: World, token_on: None, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    for headers in (None, {"Authorization": f"Bearer {WRONG}"}, RIGHT):
        for response in (
            world.client.post("/v1/zones", json=ZONE, headers=headers),
            world.client.delete("/v1/zones/z_token", headers=headers),
            world.client.post("/v1/zones", json={}, headers=headers),  # an error of the route itself
            world.client.get("/v1/health", headers=headers),
            world.client.get("/openapi.json", headers=headers),
        ):
            seen.append(response.text + str(dict(response.headers)))
    seen.append(caplog.text)
    for text in seen:
        assert TOKEN not in text and WRONG not in text


def test_health_says_that_a_token_is_set_and_nothing_more(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    assert world.client.get("/v1/health").json()["admin_token_set"] is False
    monkeypatch.setenv("VIGIE_ADMIN_TOKEN", TOKEN)
    health = world.client.get("/v1/health").json()
    assert health["admin_token_set"] is True
    assert TOKEN not in world.client.get("/v1/health").text


def test_the_comparison_is_the_constant_time_one(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, Any]] = []
    real = security.hmac.compare_digest

    def spy(left: Any, right: Any) -> bool:
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(security.hmac, "compare_digest", spy)
    assert security.token_matches(TOKEN, TOKEN) and not security.token_matches(WRONG, TOKEN) and not security.token_matches("", TOKEN)
    assert len(calls) == 3 and all(len(left) == len(right) == 32 for left, right in calls)  # digests of one length: the length of the token is not told


# ---------------------------------------------------------------- the documents and the clients


def test_the_openapi_document_says_which_operations_need_the_token(world: World) -> None:
    document = world.client.get("/openapi.json").json()
    assert document["components"]["securitySchemes"]["AdminToken"]["scheme"] == "bearer"
    asked = {(method.upper(), path) for path, item in document["paths"].items() for method, operation in item.items() if "security" in operation}
    assert asked == set(write_routes())  # every write operation, and no read
    for path, method in (("/v1/zones", "post"), ("/v1/simulate", "post")):
        operation = document["paths"][path][method]
        assert operation["security"] == [{}, {"AdminToken": []}] and "401" in operation["responses"]
    assert "security" not in document["paths"]["/v1/health"]["get"]


def test_the_dashboard_sends_the_token_with_its_two_writes(world: World) -> None:
    html = world.client.get("/dashboard").text
    assert "await writeRequest('/v1/simulate'" in html and "await writeRequest('/v1/scheduler/trigger'" in html
    assert "fetch('/v1/simulate'" not in html and "fetch('/v1/scheduler/trigger'" not in html  # no write goes around it
    assert "'Authorization': 'Bearer ' + token" in html and "sessionStorage" in html and "response.status === 401" in html
    assert "localStorage" not in html  # the token lives as long as the tab


def test_the_home_assistant_integration_only_reads() -> None:
    """The integration asks ``GET`` routes and follows positions on the WebSocket: it needs no token, whatever the instance sets."""
    sources = {path: path.read_text(encoding="utf-8") for path in (ROOT / "custom_components" / "vigie").glob("*.py")}
    assert sources, "custom_components/vigie not found"
    for path, text in sources.items():
        assert not re.search(r"\.(post|put|patch|delete|request)\(", text), f"{path.name} sends a write request"
        assert not re.search(r"method\s*=\s*['\"](POST|PUT|PATCH|DELETE)", text, re.IGNORECASE), path.name
    assert "self._session.get(url" in sources[ROOT / "custom_components" / "vigie" / "api.py"]
