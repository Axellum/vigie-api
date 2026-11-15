"""Offline API and service tests for GET /v1/history/catnat."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.attribution import CREDITS
from app.main import app

client = TestClient(app)


def test_catnat_by_insee_commune_paris() -> None:
    resp = client.get("/v1/history/catnat", params={"commune": "75056"})
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "public, max-age=86400"
    data = resp.json()

    assert data["status"] == "available"
    assert data["country"] == "FR"
    assert data["commune"]["insee_code"] == "75056"
    assert data["commune"]["name"] == "Paris"
    assert data["commune"]["department"] == "75"
    assert data["commune"]["centroid_distance_km"] is None
    assert data["total_decrees"] == 22
    assert data["filtered_decrees"] == 22
    assert data["returned"] == 22
    assert len(data["decrees"]) == 22
    assert sum(item["count"] for item in data["by_type"]) == 22
    assert sum(item["count"] for item in data["by_year"]) == 22
    assert data["attribution"] == CREDITS["gaspar_catnat"].text
    assert "commune_match" not in data["derived"]
    assert data["derived"] == ["category", "summary"]

    # Newest start_date first
    starts = [d["start_date"] for d in data["decrees"] if d["start_date"]]
    assert starts == sorted(starts, reverse=True)


def test_catnat_by_coordinates_paris_biarritz_and_overseas_dept() -> None:
    paris = client.get("/v1/history/catnat", params={"lat": 48.8566, "lon": 2.3522}).json()
    assert paris["status"] == "available"
    assert paris["commune"]["insee_code"] == "75056"
    assert paris["commune"]["centroid_distance_km"] is not None
    assert paris["commune"]["centroid_distance_km"] < 5.0
    assert "commune_match" in paris["derived"]
    assert "centroid_distance_km" in paris["derived"]

    biarritz = client.get("/v1/history/catnat", params={"commune": "64122"}).json()
    assert biarritz["status"] == "available"
    assert biarritz["total_decrees"] == 17

    abymes = client.get("/v1/history/catnat", params={"commune": "97101"}).json()
    assert abymes["status"] == "available"
    assert abymes["country"] == "GP"
    assert abymes["commune"]["department"] == "971"
    assert abymes["total_decrees"] == 14


def test_catnat_corsica_lowercase_code() -> None:
    resp = client.get("/v1/history/catnat", params={"commune": "2a004"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "available"
    assert data["commune"]["insee_code"] == "2A004"
    assert data["commune"]["department"] == "2A"
    assert data["total_decrees"] > 0


def test_catnat_confirmed_zero_decree_commune() -> None:
    resp = client.get("/v1/history/catnat", params={"commune": "01010"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "confirmed_none"
    assert data["commune"]["insee_code"] == "01010"
    assert data["total_decrees"] == 0
    assert data["filtered_decrees"] == 0
    assert data["by_type"] == []
    assert data["by_year"] == []
    assert data["decrees"] == []
    assert data["returned"] == 0


def test_catnat_outside_coverage_never_fabricates_zero_counts() -> None:
    london = client.get("/v1/history/catnat", params={"lat": 51.5074, "lon": -0.1278})
    assert london.status_code == 200
    london_data = london.json()
    assert london_data["status"] == "outside_coverage"
    assert london_data["country"] == "GB"
    assert london_data["total_decrees"] is None
    assert london_data["filtered_decrees"] is None
    assert london_data["by_type"] == []
    assert london_data["by_year"] == []
    assert london_data["decrees"] == []
    assert london_data["derived"] == []

    # Uncovered French overseas collectivity (975 = Saint-Pierre-et-Miquelon)
    spm_code = client.get("/v1/history/catnat", params={"commune": "97501"}).json()
    assert spm_code["status"] == "outside_coverage"
    assert spm_code["total_decrees"] is None

    spm_coords = client.get("/v1/history/catnat", params={"lat": 46.77, "lon": -56.17}).json()
    assert spm_coords["status"] == "outside_coverage"
    assert spm_coords["total_decrees"] is None


def test_catnat_filters_and_pagination() -> None:
    filtered = client.get(
        "/v1/history/catnat",
        params={"commune": "75056", "hazard_type": "floods", "since_year": 2000, "until_year": 2020},
    ).json()
    assert filtered["status"] == "available"
    assert filtered["total_decrees"] == 22
    assert 0 < filtered["filtered_decrees"] < 22
    for decree in filtered["decrees"]:
        assert decree["category"] == "floods"
        year = int(decree["start_date"][:4])
        assert 2000 <= year <= 2020

    summary_only = client.get("/v1/history/catnat", params={"commune": "75056", "limit": 0}).json()
    assert summary_only["filtered_decrees"] == 22
    assert summary_only["returned"] == 0
    assert summary_only["decrees"] == []
    assert len(summary_only["by_type"]) > 0

    page1 = client.get("/v1/history/catnat", params={"commune": "75056", "limit": 5, "offset": 0}).json()
    page2 = client.get("/v1/history/catnat", params={"commune": "75056", "limit": 5, "offset": 5}).json()
    assert page1["returned"] == 5 and page2["returned"] == 5
    assert page1["decrees"] != page2["decrees"]


def test_catnat_validation_errors() -> None:
    assert client.get("/v1/history/catnat").status_code == 422
    assert client.get("/v1/history/catnat", params={"lat": 48.85}).status_code == 422
    assert client.get("/v1/history/catnat", params={"lat": 48.85, "lon": 2.35, "commune": "75056"}).status_code == 422
    assert client.get("/v1/history/catnat", params={"commune": "75056", "since_year": 2020, "until_year": 2010}).status_code == 422
    assert client.get("/v1/history/catnat", params={"commune": "BAD"}).status_code == 422
    assert client.get("/v1/history/catnat", params={"commune": "99999"}).status_code == 404
