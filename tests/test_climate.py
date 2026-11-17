"""``GET /v1/climate/normals``: the normals and records of the Météo-France climate sheets, and what Vigie adds (ADR 0027)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.attribution import CREDITS
from app.main import app
from app.services.climate import default_index, load_index, query_climate_normals

client = TestClient(app)


def _station(station_id: str, lat: float, lon: float, *, until: str, tmax: float = 10.0, record: float = 20.0) -> dict[str, Any]:
    tmax_values = [tmax] * 13
    record_values = [record] * 13
    return {
        "id": station_id,
        "name": f"STATION {station_id}",
        "lat": lat,
        "lon": lon,
        "alt": 100,
        "edited": "2026-11-06",
        "period": ["1991", "2020"],
        "blocks": {
            "tmax_mean": {"p": None, "v": tmax_values},
            "tmin_mean": {"p": ["2002", "2020"], "v": [2.0] * 13},
            "tmean": {"p": None, "v": [6.0] * 13},
            "rain_mean": {"p": None, "v": [80.0] * 12 + [960.0]},
            "tmax_record": {"p": ["1950-01-01", until], "v": record_values, "d": ["2015-11-07"] * 12 + ["2015"]},
            "tmin_record": {"p": ["1950-01-01", until], "v": [-10.0] * 13, "d": ["1998-11-23"] * 12 + ["1998"]},
            "rain_day_record": {"p": ["1950-01-01", until], "v": [40.0] * 13, "d": ["1965-11-20"] * 12 + ["1965"]},
        },
    }


def _index(*stations: dict[str, Any]):
    return load_index({"dataset_updated": "2026-11-06", "stations": list(stations)})


def test_paris_gets_a_station_in_paris_with_the_values_of_its_sheet() -> None:
    body = client.get("/v1/climate/normals", params={"lat": 48.8566, "lon": 2.3522, "month": 11}).json()
    assert body["status"] == "available"
    assert body["station"]["distance_km"] < 5 and body["station"]["active"] is True
    assert body["station"]["name"].endswith("(75)")
    assert body["normals"]["tmax_mean_c"]["value"] is not None
    assert body["normals"]["tmax_mean_c"]["period"] == "1991-2020"
    assert body["records"]["tmax_c"]["date"].startswith(tuple(f"{y}-11-" for y in range(1800, 2027)))
    assert body["records_all_year"]["tmax_c"]["date"].isdigit()
    assert body["comparison"] is None
    assert body["attribution"] == CREDITS["meteofrance_climate"].text
    assert "station" in body["derived"]


def test_a_given_value_is_set_against_the_normal_and_the_record() -> None:
    body = client.get(
        "/v1/climate/normals", params={"lat": 48.8566, "lon": 2.3522, "month": 11, "tmax": 50, "tmin": -40, "rain_mm": 1}
    ).json()
    normal = body["normals"]["tmax_mean_c"]["value"]
    assert body["comparison"]["tmax_c"]["difference"] == round(50 - normal, 1)
    assert body["comparison"]["tmax_c"]["beats_record"] is True
    assert body["comparison"]["tmin_c"]["beats_record"] is True
    rain = body["comparison"]["rain_mm"]
    assert rain["normal"] is None and rain["difference"] is None and rain["beats_record"] is False
    assert "comparison" in body["derived"]


def test_far_from_any_sheet_nothing_is_estimated() -> None:
    body = client.get("/v1/climate/normals", params={"lat": 40.4168, "lon": -3.7038}).json()
    assert body["status"] == "outside_coverage"
    assert body["station"] is None and body["normals"] is None and body["records"] is None
    assert body["nearest_distance_km"] > 50


def test_the_overseas_sheets_are_served() -> None:
    body = client.get("/v1/climate/normals", params={"lat": -21.115, "lon": 55.536, "month": 1}).json()
    assert body["status"] == "available"


def test_the_month_is_validated() -> None:
    assert client.get("/v1/climate/normals", params={"lat": 48.8, "lon": 2.3, "month": 13}).status_code == 422


def test_a_station_that_still_measures_wins_over_a_closer_one_that_stopped() -> None:
    idx = _index(
        _station("00000001", 45.0, 5.0, until="2010-12-31"),
        _station("00000002", 45.1, 5.0, until="2026-11-02"),
    )
    body = query_climate_normals(lat=45.0, lon=5.0, month=11, index=idx)
    assert body.station is not None and body.station.id == "00000002" and body.station.active


def test_a_stopped_station_alone_answers_but_never_says_a_record_is_beaten() -> None:
    idx = _index(_station("00000001", 45.0, 5.0, until="2010-12-31"))
    body = query_climate_normals(lat=45.0, lon=5.0, month=11, tmax=30.0, index=idx)
    assert body.station is not None and body.station.active is False
    assert body.comparison is not None and body.comparison.tmax_c is not None
    assert body.comparison.tmax_c.difference == 20.0
    assert body.comparison.tmax_c.beats_record is None
    assert "no longer measures" in body.note


def test_an_unknown_normal_gives_no_difference() -> None:
    station = _station("00000001", 45.0, 5.0, until="2026-11-02")
    station["blocks"]["tmax_mean"]["v"][10] = None
    body = query_climate_normals(lat=45.0, lon=5.0, month=11, tmax=12.0, index=_index(station))
    assert body.comparison is not None and body.comparison.tmax_c is not None
    assert body.comparison.tmax_c.normal is None and body.comparison.tmax_c.difference is None


def test_a_block_period_wins_over_the_period_of_the_sheet() -> None:
    body = query_climate_normals(lat=45.0, lon=5.0, month=1, index=_index(_station("00000001", 45.0, 5.0, until="2026-11-02")))
    assert body.normals is not None
    assert body.normals.tmax_mean_c.period == "1991-2020"
    assert body.normals.tmin_mean_c.period == "2002-2020"
    assert body.normals.sunshine_h.value is None


def test_the_distance_is_measured_across_the_antimeridian() -> None:
    idx = _index(_station("00000001", -17.5, 179.9, until="2026-11-02"))
    body = query_climate_normals(lat=-17.5, lon=-179.9, month=1, index=idx)
    assert body.status == "available" and body.station is not None and body.station.distance_km < 30


def test_the_bundle_holds_the_sheets_of_france_and_overseas() -> None:
    idx = default_index()
    assert len(idx.stations) > 1500
    assert idx.dataset_updated is not None
    assert any(s.lat < 0 for s in idx.stations) and any(s.lat > 48 for s in idx.stations)
