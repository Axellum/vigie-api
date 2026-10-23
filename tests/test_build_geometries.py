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


def multi(*squares: dict[str, Any]) -> dict[str, Any]:
    return {"type": "MultiPolygon", "coordinates": [square["coordinates"] for square in squares]}


def natural_earth_countries() -> dict[str, Any]:
    """What the tool reads from Natural Earth, in miniature: ordinary countries, France drawn with its overseas departments,
    Norway with ISO_A2 = -99, Australia in two features, three places with no code that lie in a country, and two that do not."""
    features = []

    def add(properties: dict[str, Any], geometry: dict[str, Any]) -> None:
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})

    for n, code in enumerate(("DE", "CH", "JP", "SO", "CY", "MC")):
        add({"ISO_A2_EH": code, "ISO_A2": code, "NAME": f"Short {code}", "NAME_EN": f"Country {code}", "ADM0_A3": code}, square(-170.0 + 8 * n, 50.0))
    add({"ISO_A2_EH": "NO", "ISO_A2": "-99", "NAME": "Norway", "ADM0_A3": "NOR"}, square(10.0, 60.0))  # Natural Earth's own oddity
    add(  # the metropole, then French Guiana, Reunion, Guadeloupe (two islands), Martinique and Mayotte
        {"ISO_A2_EH": "FR", "ISO_A2": "-99", "NAME": "France", "NAME_EN": "France", "ADM0_A3": "FRA"},
        multi(square(2.0, 46.0), square(-53.0, 4.0), square(55.4, -21.1, 0.4), square(-61.7, 16.1, 0.3), square(-61.4, 16.2, 0.3), square(-61.1, 14.6, 0.3), square(45.0, -12.9, 0.3)),
    )
    add({"ISO_A2_EH": "FR", "ISO_A2": "-99", "NAME": "Clipperton I.", "ADM0_A3": "CLP"}, square(-109.0, 10.0, 0.1))  # a feature of its own, far from the metropole
    add({"ISO_A2_EH": "AU", "ISO_A2": "AU", "NAME": "Australia", "ADM0_A3": "AUS"}, square(113.0, -40.0, 30.0))
    add({"ISO_A2_EH": "AU", "ISO_A2": "-99", "NAME": "Coral Sea Is.", "ADM0_A3": "CSI"}, square(150.0, -20.0, 0.5))
    for name, adm0 in (("Somaliland", "SOL"), ("N. Cyprus", "CYN"), ("Cyprus U.N. Buffer Zone", "CNM")):
        add({"ISO_A2_EH": "-99", "ISO_A2": "-99", "WB_A2": "-99", "NAME": name, "ADM0_A3": adm0}, square(-100.0, -20.0 - len(name), 0.5))
    add({"ISO_A2_EH": "-99", "ISO_A2": "-99", "NAME": "Akrotiri", "ADM0_A3": "WSB"}, square(32.9, 34.6, 0.1))
    add({"ISO_A2_EH": "-99", "ISO_A2": "-99", "NAME": "Siachen Glacier", "ADM0_A3": "KAS"}, square(77.0, 35.0, 0.1))
    add({"ISO_A2_EH": "JP", "NAME": "no geometry"}, None)  # type: ignore[arg-type]
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


def built(tmp_path: Path, data: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """The properties and geometry of each border the tool writes for a Natural Earth file, by country code."""
    source = tmp_path / "countries.geojson"
    source.write_text(json.dumps(data or natural_earth_countries()), encoding="utf-8")
    features = tool.build_countries(source)
    codes = [f["properties"]["country"] for f in features]
    assert codes == sorted(codes) and len(set(codes)) == len(codes)  # one border a country, sorted: the same input, the same file
    return {f["properties"]["country"]: f for f in features}


def test_every_country_of_natural_earth_has_a_border_not_only_the_ones_with_a_source(tmp_path: Path) -> None:
    borders = built(tmp_path)
    assert {"DE", "CH", "JP", "NO", "AU", "MC", "SO", "CY", "FR"} <= set(borders)  # Japan and Monaco: no source, and a border all the same
    assert borders["JP"]["properties"]["name"] == "Country JP"  # the spelled-out English name, not the abbreviated one
    assert borders["NO"]["properties"]["name"] == "Norway"  # no NAME_EN: the short name


def test_france_is_the_metropole_and_each_overseas_department_has_its_own_code(tmp_path: Path) -> None:
    borders = built(tmp_path)
    assert borders["FR"]["geometry"]["type"] == "Polygon"  # the overseas parts are cut off, and Clipperton (a feature of its own) is dropped
    assert {code: borders[code]["properties"]["name"] for code in ("GF", "GP", "MQ", "RE", "YT")} == {
        "GF": "French Guiana",
        "GP": "Guadeloupe",
        "MQ": "Martinique",
        "RE": "Réunion",
        "YT": "Mayotte",
    }
    assert borders["GP"]["geometry"]["type"] == "MultiPolygon"  # both islands of the department are in
    assert borders["GF"]["geometry"]["coordinates"][0][0] == [-53.0, 4.0]


def test_an_overseas_department_missing_from_the_file_stops_the_build(tmp_path: Path) -> None:
    data = natural_earth_countries()
    france = next(f for f in data["features"] if f["properties"].get("ADM0_A3") == "FRA")
    france["geometry"]["coordinates"] = [part for part in france["geometry"]["coordinates"] if part[0][0] != [55.4, -21.1]]  # no Reunion
    with pytest.raises(SystemExit, match="Réunion"):
        built(tmp_path, data)


def test_a_country_drawn_in_several_features_is_one_border_named_after_the_largest(tmp_path: Path) -> None:
    australia = built(tmp_path)["AU"]
    assert australia["properties"]["name"] == "Australia"  # not "Coral Sea Is.", which comes after it in the file
    assert australia["geometry"]["type"] == "MultiPolygon" and len(australia["geometry"]["coordinates"]) == 2


def test_places_with_no_code_are_joined_to_their_country_or_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    borders = built(tmp_path)
    assert borders["SO"]["geometry"]["type"] == "MultiPolygon"  # Somaliland, joined to Somalia
    assert borders["CY"]["geometry"]["type"] == "MultiPolygon" and len(borders["CY"]["geometry"]["coordinates"]) == 3  # N. Cyprus and the buffer zone
    assert set(tool.MERGED_INTO.values()) == {"SO", "CY"}
    reported = capsys.readouterr().err
    assert "Akrotiri" in reported and "Siachen Glacier" in reported  # not written, and said
    assert "Somaliland" not in reported and "N. Cyprus" not in reported


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
    index = CountryIndex.from_file(out / "countries.geojson.gz")
    assert {"JP", "FR", "RE", "AU"} <= set(index.countries()) and index.name("RE") == "Réunion"
    assert index.countries_for_point(50.5, -153.5) == ["JP"]  # inside the square of Japan
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
