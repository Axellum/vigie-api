"""The tool that rebuilds the geometry files (``tools/build_geometries.py``), played on tiny synthetic inputs.

The tool downloads Natural Earth and Wikidata; here the downloads are replaced by files the test writes, so the
tests check the *logic* (which countries, which part of France, which NUTS codes, determinism of the output) without
the network. The bundled files themselves are checked in ``test_vigilance_localization.py``.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.geoindex import DATA_DIR, CountryIndex, GeometryIndex
from tools import build_geometries as tool


def square(lon: float, lat: float, size: float = 1.0) -> dict[str, Any]:
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


def natural_earth_countries() -> dict[str, Any]:
    """One square per wanted country (spread over the globe), France with an overseas part, Norway with ISO_A2 = -99."""
    features = []
    for n, code in enumerate(tool.COUNTRIES):
        props: dict[str, Any] = {"ISO_A2_EH": code, "ISO_A2": code, "NAME": f"Country {code}"}
        geometry = square(-170.0 + 8 * (n % 40), -60.0 + 8 * (n // 40) * 3)
        if code == "FR":
            geometry = {"type": "MultiPolygon", "coordinates": [square(2.0, 46.0)["coordinates"], square(-61.5, 16.0)["coordinates"]]}
        if code == "NO":
            props["ISO_A2"] = "-99"  # Natural Earth's own oddity: ISO_A2_EH fixes it
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
    features.append({"type": "Feature", "properties": {"ISO_A2_EH": "JP", "NAME": "Japan"}, "geometry": square(139.0, 35.0)})
    return {"type": "FeatureCollection", "features": features}


def natural_earth_admin1() -> dict[str, Any]:
    def admin(iso: str, name: str, adm0: str, lon: float, lat: float) -> dict[str, Any]:
        return {"type": "Feature", "properties": {"iso_3166_2": iso, "name": name, "adm0_a3": adm0}, "geometry": square(lon, lat, 0.5)}

    return {
        "type": "FeatureCollection",
        "features": [
            admin("FR-26", "Drôme", "FRA", 4.5, 44.5),
            admin("FR-07", "Ardèche", "FRA", 4.0, 44.5),
            admin("FR-75", "Paris", "FRA", 2.2, 48.8),
            admin("BE-WLG", "Liège", "BEL", 5.0, 50.4),
            admin("DE-BY", "Bayern", "DEU", 11.0, 48.5),
        ],
    }


WIKIDATA_PAIRS = [
    ("FR-26", "FR713"),  # an old code
    ("FR-26", "FRK22"),  # the new one for the same area
    ("FR-07", "FR712"),
    ("FR-75C", "FR101"),  # Wikidata's code for the Paris collectivity
    ("BE-WLG", "BE33"),
    ("BE-WLG", "BE332"),  # NUTS3: not the level taken for Belgium
    ("DE-BY", "DE2"),  # a country with no level in the tool
    ("FR-26", "FR713"),  # twice
    ("FR-99", "FR999"),  # an area Natural Earth does not have
    ("FR-26", "FR71"),  # NUTS2: not the level taken for France
]


def test_coordinates_are_rounded_and_the_structure_is_kept() -> None:
    assert tool.round_coordinates([[[1.23456, 2.34567], [3.0, 4.0]]], 2) == [[[1.23, 2.35], [3.0, 4.0]]]
    assert tool.round_coordinates("not coordinates") == "not coordinates"


def test_a_small_island_is_dropped_by_the_simplification_but_the_mainland_is_kept() -> None:
    from shapely.geometry import MultiPolygon, Polygon, shape

    mainland = shape(square(0.0, 0.0, 1.0))
    islet = Polygon([(5, 5), (5.001, 5), (5.001, 5.001), (5, 5.001)])
    simplified = tool.simplify(MultiPolygon([mainland, islet]), 0.01)
    assert simplified.geom_type in ("Polygon", "MultiPolygon") and simplified.area == pytest.approx(1.0, abs=0.01)
    only_islets = MultiPolygon([islet, Polygon([(7, 7), (7.001, 7), (7.001, 7.001), (7, 7.001)])])
    assert not tool.simplify(only_islets, 0.01).is_empty  # a country that is only small islands is not erased


@pytest.mark.parametrize(
    ("properties", "code"),
    [
        ({"ISO_A2_EH": "FR", "ISO_A2": "-99"}, "FR"),
        ({"ISO_A2_EH": "-99", "ISO_A2": "NO"}, "NO"),
        ({"ISO_A2": "-99", "WB_A2": "XK"}, "XK"),
        ({"ISO_A2_EH": "fr"}, None),
        ({}, None),
    ],
)
def test_the_country_code_of_a_natural_earth_feature(properties: dict[str, Any], code: str | None) -> None:
    assert tool.country_code(properties) == code


def test_the_countries_are_the_wanted_ones_and_france_is_the_metropole(tmp_path: Path) -> None:
    source = tmp_path / "countries.geojson"
    source.write_text(json.dumps(natural_earth_countries()), encoding="utf-8")
    features = tool.build_countries(source)
    assert [f["properties"]["country"] for f in features] == tool.COUNTRIES and len(features) == 42
    france = next(f for f in features if f["properties"]["country"] == "FR")
    assert france["geometry"]["type"] == "Polygon"  # the overseas part (centroid at -61 degrees) is dropped
    assert france["properties"]["name"] == "Country FR"
    assert all(f["properties"]["country"] != "JP" for f in features)  # a country with no source is not kept


def test_a_missing_country_stops_the_build(tmp_path: Path) -> None:
    data = natural_earth_countries()
    data["features"] = [f for f in data["features"] if f["properties"]["ISO_A2_EH"] != "CH"]
    source = tmp_path / "countries.geojson"
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit, match="CH"):
        tool.build_countries(source)


def test_paris_collectivity_code_is_the_department_code() -> None:
    assert tool.normalize_iso("FR-75C") == "FR-75" and tool.normalize_iso("FR-26") == "FR-26" and tool.normalize_iso("BE-WLG") == "BE-WLG"


def test_the_nuts_shapes_take_the_right_level_and_every_code_once(tmp_path: Path) -> None:
    source = tmp_path / "admin1.geojson"
    source.write_text(json.dumps(natural_earth_admin1()), encoding="utf-8")
    features = tool.build_nuts(source, WIKIDATA_PAIRS)
    codes = {(f["properties"]["scheme"], f["properties"]["code"]): f["properties"]["name"] for f in features}
    assert codes == {
        ("NUTS3", "FR713"): "Drôme",
        ("NUTS3", "FRK22"): "Drôme",  # the old and the new code name the same shape
        ("NUTS3", "FR712"): "Ardèche",
        ("NUTS3", "FR101"): "Paris",
        ("NUTS2", "BE33"): "Liège",
    }
    assert len(features) == len(codes)


def test_the_output_is_deterministic_and_loadable(tmp_path: Path) -> None:
    source = tmp_path / "admin1.geojson"
    source.write_text(json.dumps(natural_earth_admin1()), encoding="utf-8")
    features = tool.build_nuts(source, WIKIDATA_PAIRS)
    first, second = tmp_path / "a.geojson.gz", tmp_path / "b.geojson.gz"
    tool.write_collection(first, features, "note")
    tool.write_collection(second, features, "note")
    assert first.read_bytes() == second.read_bytes()  # the gzip header carries no time: the same input, the same bytes
    index = GeometryIndex.from_features(json.loads(gzip.decompress(first.read_bytes()))["features"])
    assert index.has("NUTS3", "FR713") and index.has("NUTS2", "BE33")
    assert index.codes_at(44.75, 4.75) == {("NUTS3", "FR713"), ("NUTS3", "FRK22")}


def test_the_bundled_files_were_written_by_this_tool() -> None:
    """The gzip header holds the time of writing: the tool writes 0, so a file edited by hand or by another tool shows."""
    for path in DATA_DIR.glob("*.geojson.gz"):
        header = path.read_bytes()[:10]
        assert header[:2] == b"\x1f\x8b" and header[4:8] == b"\x00\x00\x00\x00", path.name
        collection = json.loads(gzip.decompress(path.read_bytes()))
        assert collection["type"] == "FeatureCollection" and collection["note"]


def test_the_emma_file_is_written_where_git_ignores_it() -> None:
    assert tool.LOCAL_DIR == DATA_DIR / "local"
    ignored = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "app/data/local/" in ignored


def test_run_builds_both_files_without_the_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole build with the downloads replaced: two files, the countries and the NUTS shapes, loadable by the API."""
    asked: list[str] = []

    def fetch(url: str, destination: Path, params: dict[str, str] | None = None) -> Path:
        asked.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url == tool.NE_COUNTRIES_URL:
            destination.write_text(json.dumps(natural_earth_countries()), encoding="utf-8")
        elif url == tool.NE_ADMIN1_URL:
            destination.write_text(json.dumps(natural_earth_admin1()), encoding="utf-8")
        elif url.startswith(tool.WIKIDATA_SPARQL_URL):
            rows = [{"iso": {"value": iso}, "nuts": {"value": nuts}} for iso, nuts in WIKIDATA_PAIRS]
            destination.write_text(json.dumps({"results": {"bindings": rows}}), encoding="utf-8")
        else:
            raise AssertionError(f"unexpected download {url}")
        return destination

    out = tmp_path / "out"
    assert tool.run(["--cache-dir", str(tmp_path / "cache"), "--out-dir", str(out)], fetch=fetch) == 0
    assert sorted(path.name for path in out.iterdir()) == ["countries.geojson.gz", "geocodes-nuts.geojson.gz"]
    assert len(asked) == 3 and all(url.startswith("https://") for url in asked)
    assert "Natural Earth" in json.loads(gzip.decompress((out / "countries.geojson.gz").read_bytes()))["note"]
    assert len(CountryIndex.from_file(out / "countries.geojson.gz").countries()) == 42
    assert GeometryIndex.from_disk(out).has("NUTS3", "FR713")


def test_run_with_emma_writes_only_the_local_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    emma = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"type": "EMMA_ID", "code": "IT019", "name": "Sardegna", "country": "IT"}, "geometry": square(8.0, 39.0)},
            {"type": "Feature", "properties": {"type": "NUTS", "code": "ITG2", "name": "x", "country": "IT"}, "geometry": square(8.0, 39.0)},
            {"type": "Feature", "properties": {"type": "EMMA_ID", "code": "IT020", "name": "no shape", "country": "IT"}, "geometry": None},
        ],
    }

    def fetch(url: str, destination: Path, params: dict[str, str] | None = None) -> Path:
        assert url == tool.EMMA_URL
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(emma), encoding="utf-8")
        return destination

    out = tmp_path / "local"
    assert tool.run(["--emma", "--cache-dir", str(tmp_path / "cache"), "--out-dir", str(out)], fetch=fetch) == 0
    assert [path.name for path in out.iterdir()] == ["emma_id.geojson.gz"]
    index = GeometryIndex.from_features(json.loads(gzip.decompress((out / "emma_id.geojson.gz").read_bytes()))["features"])
    assert index.schemes() == {"EMMA_ID": 1} and index.has("EMMA_ID", "IT019")


def test_a_download_is_kept_and_not_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    calls: list[str] = []

    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def get(self, url: str, params: Any = None) -> httpx.Response:
            calls.append(url)
            return httpx.Response(200, content=b"data", request=httpx.Request("GET", url))

    monkeypatch.setattr(tool.httpx, "Client", Client)
    target = tmp_path / "sub" / "file.json"
    assert tool.download("https://example.test/a", target).read_bytes() == b"data"
    assert tool.download("https://example.test/a", target).read_bytes() == b"data"
    assert calls == ["https://example.test/a"]
