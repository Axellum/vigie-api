"""Solar eclipses: NASA's bundled catalogue, the local circumstances and the map worked out from its elements, and the routes.

The reference values are NASA's, copied from the path table of the total eclipse of 2027-08-02
(https://eclipse.gsfc.nasa.gov/SEpath/SEpath2001/SE2027Aug02Tpath.html) and its Besselian elements page; ``tools/check_eclipses.py``
compares all 101 rows of that table (docs/measurements/eclipse-2027-08-02.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point, shape

from app.attribution import CREDITS
from app.collectors.weather import OpenMeteoCollector
from app.services import eclipse_besselian as eb
from app.services import eclipse_map
from app.services.eclipses import here_of
from tests.helpers import Provider, make_service

ROOT = Path(__file__).resolve().parents[1]


def utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def dms(degrees: int, minutes: float, side: str) -> float:
    value = degrees + minutes / 60.0
    return -value if side in "SW" else value


# Rows of NASA's path table of 2027-08-02: (UT, northern limit, southern limit, central line, Sun's altitude, central duration).
NASA_ROWS = [
    ("08:46", (dms(36, 42.5, "N"), dms(7, 5.2, "W")), (dms(34, 36.5, "N"), dms(6, 24.4, "W")), (dms(35, 39.6, "N"), dms(6, 43.3, "W")), 37, 288.3),
    ("09:20", (dms(34, 42.8, "N"), dms(15, 24.3, "E")), (dms(32, 31.6, "N"), dms(14, 44.8, "E")), (dms(33, 37.1, "N"), dms(15, 4.8, "E")), 61, 356.8),
    ("10:00", (dms(27, 51.4, "N"), dms(31, 44.0, "E")), (dms(25, 55.3, "N"), dms(30, 18.2, "E")), (dms(26, 53.3, "N"), dms(31, 0.8, "E")), 81, 383.2),
    ("10:40", (dms(18, 20.7, "N"), dms(44, 16.2, "E")), (dms(16, 43.1, "N"), dms(42, 30.7, "E")), (dms(17, 32.0, "N"), dms(43, 23.0, "E")), 69, 360.8),
    ("11:40", (dms(3, 22.8, "S"), dms(72, 3.8, "E")), (dms(4, 27.4, "S"), dms(69, 42.2, "E")), (dms(3, 54.4, "S"), dms(70, 51.6, "E")), 23, 241.1),
]
NASA_GREATEST = (dms(25, 30.3, "N"), dms(33, 11.0, "E"))  # at 10:06:37.7 UT, central duration 06m22.6s


@pytest.fixture(scope="module")
def eclipse() -> eb.SolarEclipse:
    found = eb.eclipse_on("2027-08-02")
    assert found is not None
    return found


# ============================================================================
# The catalogue
# ============================================================================


def test_the_catalogue_holds_the_eclipses_of_2001_to_2100_in_order() -> None:
    eclipses = eb.catalogue()
    assert len(eclipses) == 224
    assert eclipses[0].date.startswith("2001") and eclipses[-1].date.startswith("2100")
    assert [e.date for e in eclipses] == sorted(e.date for e in eclipses)
    assert all(e.elements_source.startswith("https://eclipse.gsfc.nasa.gov/") for e in eclipses)


def test_the_eclipse_of_2027_is_as_nasa_states_it(eclipse: eb.SolarEclipse) -> None:
    assert (eclipse.kind, eclipse.type_code, eclipse.saros) == ("total", "T", 136)
    assert eclipse.delta_t_s == 71.7 and eclipse.gamma == 0.1421 and eclipse.magnitude == 1.079
    assert datetime.fromtimestamp(eclipse.greatest_utc, timezone.utc).isoformat() == "2027-08-02T10:06:37.700000+00:00"
    assert (eclipse.greatest_lat, eclipse.greatest_lon) == pytest.approx(NASA_GREATEST, abs=1e-4)
    assert (eclipse.path_width_km, eclipse.central_duration_s) == (257.7, 382.6)
    assert eclipse.elements_source.endswith("SE2027Aug02Tbeselm.html")


def test_a_partial_eclipse_has_no_path_and_no_central_duration() -> None:
    partial = eb.eclipse_on("2029-01-14")
    assert partial is not None and partial.kind == "partial"
    assert partial.path_width_km is None and partial.central_duration_s is None
    assert eclipse_map.path_geometry(partial) is None


def test_the_eclipse_credit_is_nasa_s_own_wording() -> None:
    assert CREDITS["nasa_eclipses"].text == "Eclipse Predictions by Fred Espenak, NASA's GSFC"
    assert CREDITS["nasa_eclipses"].wording == "provider"


# ============================================================================
# Against NASA's path table
# ============================================================================


@pytest.mark.parametrize("row", NASA_ROWS, ids=[r[0] for r in NASA_ROWS])
def test_the_central_line_point_has_its_maximum_at_the_row_s_time(eclipse: eb.SolarEclipse, row: Any) -> None:
    time, _, _, central, altitude, duration = row
    local = eb.local_circumstances(eclipse, *central)
    maximum = next(c for c in local.contacts if c.name == "maximum")

    assert local.kind == "total"
    assert maximum.time_utc == pytest.approx(utc(f"2027-08-02T{time}:00"), abs=1.0)
    assert local.central_duration_s == pytest.approx(duration, abs=1.0)
    assert maximum.sun_altitude_deg == pytest.approx(altitude, abs=1.0)  # NASA rounds it to the degree


@pytest.mark.parametrize("row", NASA_ROWS, ids=[r[0] for r in NASA_ROWS])
def test_the_limits_are_on_the_edge_of_the_umbra(eclipse: eb.SolarEclipse, row: Any) -> None:
    _, north, south, _, _, _ = row
    for lat, lon in (north, south):
        places = eb.Places.at(lat, lon)
        s = eb.shadow(eclipse, eb.time_of_maximum(eclipse, places), places)
        assert abs(float((s.distance - np.abs(s.l2)).reshape(-1)[0])) * eb.EARTH_RADIUS_KM < 1.0  # km


def test_the_place_of_greatest_eclipse(eclipse: eb.SolarEclipse) -> None:
    local = eb.local_circumstances(eclipse, *NASA_GREATEST)
    names = [c.name for c in local.contacts]
    maximum = local.contacts[names.index("maximum")]

    assert names == ["partial_begin", "central_begin", "maximum", "central_end", "partial_end"]
    assert maximum.time_utc == pytest.approx(utc("2027-08-02T10:06:37.7"), abs=1.0)
    assert local.central_duration_s == pytest.approx(6 * 60 + 22.6, abs=1.0)
    assert maximum.sun_altitude_deg == pytest.approx(81.7, abs=0.2)
    assert local.obscuration == 1.0 and local.magnitude > 1.0


# ============================================================================
# Places off the path, and the geometry
# ============================================================================


def test_a_place_outside_the_shadow_sees_nothing(eclipse: eb.SolarEclipse) -> None:
    local = eb.local_circumstances(eclipse, -33.87, 151.21)  # Sydney
    assert (local.kind, local.contacts, local.obscuration) == ("none", (), 0.0)


def test_paris_sees_a_partial_eclipse_about_half_hidden(eclipse: eb.SolarEclipse) -> None:
    local = eb.local_circumstances(eclipse, 48.8566, 2.3522)
    assert local.kind == "partial" and [c.name for c in local.contacts] == ["partial_begin", "maximum", "partial_end"]
    assert 0.5 < local.obscuration < 0.53  # astronomy-engine: 0.512 (tools/check_eclipses.py)
    assert local.central_duration_s is None


def test_a_contact_after_the_elements_end_is_left_out_and_the_sunset_said() -> None:
    annular = eb.eclipse_on("2028-01-26")
    assert annular is not None
    local = eb.local_circumstances(annular, 40.4168, -3.7038)  # Madrid: the Sun sets eclipsed
    assert [c.name for c in local.contacts] == ["partial_begin", "maximum"]
    assert local.sunset_utc is not None and local.sunrise_utc is None
    assert local.contacts[-1].time_utc < local.sunset_utc


@pytest.mark.parametrize(
    ("distance", "moon", "expected"),
    [
        (0.0, 1.05, 1.0),  # total
        (0.0, 0.95, 0.95**2),  # annular, centred
        (2.1, 1.0, 0.0),  # apart
        (1.0, 1.0, (2 * np.pi / 3 - np.sqrt(3) / 2) / np.pi),  # two equal discs, each centre on the other's edge
    ],
)
def test_the_share_hidden_is_the_overlap_of_the_two_discs(distance: float, moon: float, expected: float) -> None:
    # The penumbra and umbra radii that make a Moon of radius ``moon`` (the Sun's being 1) at ``distance`` Sun radii.
    l1, l2 = (1 + moon) / 2, (1 - moon) / 2
    zero = np.zeros(1)
    s = eb.Shadow(np.array([distance / 2]), zero, np.array([l1]), np.array([l2]), zero, zero, zero, zero)
    assert float(eb.obscuration(s)[0]) == pytest.approx(expected, abs=1e-9)


def test_the_disc_frames_follow_the_moon_across_the_sun(eclipse: eb.SolarEclipse) -> None:
    here = here_of(eclipse, 36.5271, -6.2886, with_frames=True)  # Cadiz, in the path
    assert here.kind == "total" and here.visible and here.frames is not None
    first, last = here.frames[0], here.frames[-1]
    # At the first and the last contact the discs touch (the Moon's apparent radius grows as it rises in the sky).
    assert np.hypot(first.moon_east, first.moon_north) == pytest.approx(1 + first.moon_radius, abs=1e-3)
    assert np.hypot(last.moon_east, last.moon_north) == pytest.approx(1 + last.moon_radius, abs=1e-3)
    assert first.moon_east < 0 < last.moon_east  # the Moon comes from the west
    assert max(f.obscuration for f in here.frames) == 1.0


def test_the_map_s_path_follows_nasa_s_limits_and_central_line(eclipse: eb.SolarEclipse) -> None:
    path = shape(eclipse_map.path_geometry(eclipse))
    line = shape(eclipse_map.central_line_geometry(eclipse))
    for _, north, south, central, _, _ in NASA_ROWS:
        assert path.contains(Point(central[1], central[0]))
        assert line.distance(Point(central[1], central[0])) < 0.01  # degrees
        for lat, lon in (north, south):
            assert path.exterior.distance(Point(lon, lat)) < 0.01


def test_the_bands_are_nested_around_the_path(eclipse: eb.SolarEclipse) -> None:
    bands = eclipse_map.obscuration_bands(eclipse)
    assert [b[:2] for b in bands] == [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, None)]
    darkest = shape(bands[-1][2])
    assert darkest.contains(Point(-6.2886, 36.5271))  # Cadiz
    assert shape(bands[2][2]).contains(Point(2.3522, 48.8566))  # Paris: 0.51 hidden


# ============================================================================
# The routes
# ============================================================================


def clouds_provider(start: float) -> Provider:
    hours = [start + 3600 * i for i in range(48)]
    return Provider(
        {
            "utc_offset_seconds": 0,
            "daily": {"time": [datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d")]},
            "hourly": {
                "time": [datetime.fromtimestamp(h, timezone.utc).strftime("%Y-%m-%dT%H:%M") for h in hours],
                "cloud_cover": [10.0] * 48,
            },
        }
    )


@pytest.fixture
def eclipse_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.main as main
    from app import clock

    weather = clouds_provider(utc("2027-08-01T12:00:00"))
    service = make_service(tmp_path / "eclipses.db", open_meteo=OpenMeteoCollector(http_client=weather.client()))
    monkeypatch.setattr(main, "aggregator", service)
    now = {"t": utc("2026-10-28T21:00:00")}
    monkeypatch.setattr(clock, "_wall", lambda: now["t"])
    return TestClient(main.app), weather, now


def test_the_list_gives_ten_years_of_nasa_s_eclipses_and_what_a_place_sees(eclipse_client: Any) -> None:
    client, weather, _ = eclipse_client
    body = client.get("/v1/sky/eclipses", params={"lat": 36.5271, "lon": -6.2886}).json()

    assert (body["year_from"], body["year_to"]) == (2026, 2035)
    ids = [e["id"] for e in body["eclipses"]]
    assert "2027-08-02" in ids and all("2026" <= i[:4] <= "2035" for i in ids)
    total = body["eclipses"][ids.index("2027-08-02")]
    assert total["here"]["kind"] == "total" and total["here"]["frames"] is None
    assert body["derived"] == ["eclipses.here"]
    assert body["attribution"] == "Eclipse Predictions by Fred Espenak, NASA's GSFC"
    assert weather.count == 0


def test_one_eclipse_far_ahead_has_no_clouds_and_asks_no_weather(eclipse_client: Any) -> None:
    client, weather, _ = eclipse_client
    body = client.get("/v1/sky/eclipses/2027-08-02", params={"lat": 36.5271, "lon": -6.2886}).json()

    assert body["id"] == "2027-08-02" and body["here"]["kind"] == "total"
    assert len(body["here"]["frames"]) > 60
    assert body["here"]["clouds"] is None and "48 hours" in body["here"]["clouds_note"]
    assert weather.count == 0


def test_within_48_hours_the_clouds_of_the_maximum_s_hour_are_given(eclipse_client: Any) -> None:
    client, weather, now = eclipse_client
    now["t"] = utc("2027-08-01T12:00:00")
    body = client.get("/v1/sky/eclipses/2027-08-02", params={"lat": 36.5271, "lon": -6.2886}).json()

    clouds = body["here"]["clouds"]
    assert clouds["cloud_cover_pct"] == 10.0 and clouds["time_utc"] == int(utc("2027-08-02T08:00:00"))
    assert "Open-Meteo" in body["attribution"] and "Fred Espenak" in body["attribution"]
    assert weather.count == 1


def test_the_map_route_and_the_errors(eclipse_client: Any) -> None:
    client, _, _ = eclipse_client
    body = client.get("/v1/sky/eclipses/2027-08-02.geojson").json()

    assert body["type"] == "FeatureCollection" and body["id"] == "2027-08-02"
    assert [f["properties"]["kind"] for f in body["features"]][-2:] == ["path", "central_line"]
    assert body["derived"] == ["features"]
    assert client.get("/v1/sky/eclipses/2027-08-03").status_code == 404
    assert client.get("/v1/sky/eclipses/2027-08-03.geojson").status_code == 404
    assert client.get("/v1/sky/eclipses/27-8-2").status_code == 422
    assert client.get("/v1/sky/eclipses", params={"lat": 1.0}).status_code == 422
    assert client.get("/v1/sky/eclipses", params={"year_from": 2030, "year_to": 2029}).status_code == 422


def test_no_request_leaves_without_a_place(eclipse_client: Any) -> None:
    client, weather, _ = eclipse_client
    client.get("/v1/sky/eclipses")
    client.get("/v1/sky/eclipses/2027-08-02")
    assert weather.count == 0  # and the guard of conftest.py refuses any other request
