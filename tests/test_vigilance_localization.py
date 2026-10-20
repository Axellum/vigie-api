"""Where a point is: its country (offline) and whether it lies inside the area of a warning (shapely).

The shapes are the ones bundled in ``app/data/`` (``tools/build_geometries.py`` builds them, ``SOURCES.md`` gives
their licences). The warnings are the recorded feeds of 2026-10-20. Nothing reaches the network.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.collectors.meteoalarm import COUNTRY_FEEDS
from app.services.geoindex import (
    DATA_DIR,
    CountryIndex,
    GeometryIndex,
    PointLocator,
    default_countries,
    default_geometries,
    geometry_of,
    localization_of,
)
from tests import vigilance_samples as samples
from tests.vigilance_samples import warning

# ---------------------------------------------------------------- the country of a point, without any request


@pytest.mark.parametrize(
    ("place", "point", "countries"),
    [
        ("Paris", samples.PARIS, ["FR"]),
        ("Valence", samples.VALENCE, ["FR"]),
        ("Ajaccio, Corsica", (41.9192, 8.7386), ["FR"]),
        ("Zurich", (47.3769, 8.5417), ["CH"]),
        ("Reykjavik", samples.REYKJAVIK, ["IS"]),
        ("Cagliari, Sardinia", samples.CAGLIARI, ["IT"]),
        ("Luxembourg", (49.6116, 6.1319), ["LU"]),
        ("San Francisco", samples.SAN_FRANCISCO, ["US"]),
        ("Kehl, on the German side of the Rhine, 2 km from France", (48.5734, 7.8152), ["DE", "FR"]),
        ("off Calais, 10 km at sea", (50.98, 1.7), ["FR"]),
        ("Tokyo: no source", (35.6762, 139.6503), []),
        ("middle of the North Sea", (56.0, 3.0), []),
        ("middle of the Atlantic", (40.0, -30.0), []),
        ("Pointe-a-Pitre: the overseas departments are not in the metropolitan feed", (16.2415, -61.5331), []),
    ],
)
def test_the_country_of_a_point(place: str, point: tuple[float, float], countries: list[str]) -> None:
    assert default_countries().countries_for_point(*point) == countries, place


def test_every_country_a_source_answers_for_has_a_border() -> None:
    """A position finds its country by the borders: a country with a source and no border could never be asked by a point."""
    from app.collectors.swic_registry import bundled_registry
    from app.collectors.swic_terms import is_eligible

    borders = set(default_countries().countries())
    collectors = {*COUNTRY_FEEDS, "US", "CA", "NZ", "DE"}
    registry = {source.country for source in bundled_registry() if source.country and is_eligible(source, "all")}
    assert collectors <= borders
    assert registry <= borders, sorted(registry - borders)
    assert borders == collectors | registry  # and no border without a source: it would be bytes for nothing


def test_every_country_the_service_serves_has_a_border() -> None:
    """With every service of the registry on, the countries the instance lists are all countries a position can fall in."""
    from tests.swic_samples import service_serving_everything

    service = service_serving_everything()
    served = {item.country for item in service.countries_listing()}
    assert served and served <= set(default_countries().countries()), sorted(served - set(default_countries().countries()))


# ---------------------------------------------------------------- the shapes of the codes


def test_the_bundled_shapes_cover_every_code_of_the_recorded_french_feed() -> None:
    geometries = default_geometries()
    codes = {(g.scheme, g.value) for w in samples.feed("FR").vigilances for g in w.area.geocodes if g.scheme == "NUTS3"}
    assert len(codes) == 12
    assert all(geometries.has(*code) for code in codes)


def test_the_shapes_keep_the_names_of_the_regions() -> None:
    """The name next to a code is the check that the code was joined to the right shape (the feed's own names are the reference)."""
    with gzip.open(DATA_DIR / "geocodes-nuts.geojson.gz", "rt", encoding="utf-8") as handle:
        features = json.load(handle)["features"]
    names = {f["properties"]["code"]: f["properties"]["name"] for f in features}
    for warning_ in samples.feed("FR").vigilances:
        for code in warning_.area.geocodes:
            if code.scheme == "NUTS3":
                assert names[code.value] == warning_.area.description, code.value


def test_the_shapes_are_small_enough_to_live_in_the_repository() -> None:
    sizes = {path.name: path.stat().st_size for path in DATA_DIR.glob("*.geojson.gz")}
    assert set(sizes) == {"countries.geojson.gz", "geocodes-nuts.geojson.gz"}
    assert sum(sizes.values()) < 1024 * 1024, sizes  # 135 countries since the services of the WMO registry (it was 42)


def test_a_point_belongs_to_the_one_region_it_lies_in() -> None:
    codes = default_geometries().codes_at(*samples.VALENCE)
    # Valence is on the Rhône, 5 km from the Ardèche: only the Drôme (the exact answer), not both through the margin.
    assert codes == {("NUTS3", "FR713")}


def test_a_point_on_the_coast_belongs_to_the_nearest_region_within_the_margin() -> None:
    inside = default_geometries().codes_at(43.38, 3.70)  # a few hundred metres off Sète, in the simplification gap
    assert ("NUTS3", "FR813") in inside


def test_a_point_far_from_any_region_belongs_to_none() -> None:
    assert default_geometries().codes_at(40.0, -30.0) == set()
    assert default_geometries().codes_at(35.0, 139.0) == set()


def test_belgium_is_matched_by_province() -> None:
    assert ("NUTS2", "BE25") in default_geometries().codes_at(51.2093, 3.2247)  # Bruges, West Flanders


# ---------------------------------------------------------------- a point and the area of a warning


def _covering(point: tuple[float, float], country: str) -> list[tuple[str, str, str]]:
    locator = PointLocator(*point, default_geometries())
    return [
        (w.area.description or "?", w.level.value if w.level else "?", locator.covering(w) or "?")
        for w in samples.feed(country).vigilances
        if locator.covering(w)
    ]


def test_a_point_in_a_french_departement_gets_its_warning() -> None:
    assert _covering(samples.VALENCE, "FR") == [("Drôme", "YELLOW", "geocode")]


def test_a_point_outside_every_warned_area_gets_nothing() -> None:
    assert _covering(samples.PARIS, "FR") == []


def test_a_point_in_a_swiss_polygon_is_matched_with_the_polygon_of_the_source() -> None:
    assert _covering(samples.GLARUS, "CH") == [("Glarus Süd-Sernftal", "YELLOW", "polygon")]
    assert _covering((46.9, 9.0), "CH") == []  # a few kilometres west of the polygon


def test_the_edge_of_a_polygon_counts_as_inside() -> None:
    ring = next(w for w in samples.feed("CH").vigilances if w.area.description == "Glarus Süd-Sernftal")
    assert ring.shape is not None
    x, y = ring.shape.exterior.coords[0]
    assert PointLocator(y, x, default_geometries()).covering(ring) == "polygon"


def test_warnings_named_by_a_code_without_a_shape_cannot_be_matched_to_a_point() -> None:
    """Italy, Germany and Austria name their areas with EMMA_ID codes only; their shapes are not bundled (no licence)."""
    assert _covering(samples.CAGLIARI, "IT") == []
    geometries = default_geometries()
    assert {localization_of(w, geometries) for w in samples.feed("IT").vigilances} == {"none"}
    assert {localization_of(w, geometries) for w in samples.feed("DE").vigilances} == {"none"}


def test_the_localization_of_each_kind_of_area() -> None:
    geometries = default_geometries()
    assert {localization_of(w, geometries) for w in samples.feed("FR").vigilances} == {"geocode"}
    assert {localization_of(w, geometries) for w in samples.feed("CH").vigilances} == {"polygon"}
    iceland = {localization_of(w, geometries) for w in samples.feed("IS").vigilances}
    assert iceland == {"polygon", "none"}  # half polygons, half EMMA_ID codes
    assert {localization_of(w, geometries) for w in samples.feed("BE").vigilances} == {"geocode"}  # through NUTS2


def test_the_geometry_to_draw_says_where_it_comes_from() -> None:
    geometries = default_geometries()
    drome = samples.feed("FR").vigilances[0]
    shape, origin = geometry_of(drome, geometries)
    assert shape is not None and origin == "geocode:NUTS3:FR713"
    swiss = samples.feed("CH").vigilances[0]
    shape, origin = geometry_of(swiss, geometries)
    assert shape is swiss.shape and origin == "polygon"
    assert geometry_of(samples.feed("IT").vigilances[0], geometries) == (None, None)


# ---------------------------------------------------------------- the operator's own EMMA_ID shapes (not bundled)


def _square_file(path: Path, code: str, lon: float, lat: float) -> Path:
    ring = [[lon - 0.5, lat - 0.5], [lon + 0.5, lat - 0.5], [lon + 0.5, lat + 0.5], [lon - 0.5, lat + 0.5], [lon - 0.5, lat - 0.5]]
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"scheme": "EMMA_ID", "code": code}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
        ],
    }
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        json.dump(collection, handle)
    return path


def test_an_operator_can_add_his_own_emma_id_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sardinia = _square_file(tmp_path / "mine.geojson.gz", "IT019", samples.CAGLIARI[1], samples.CAGLIARI[0])
    monkeypatch.setenv("VIGIE_EMMA_GEOMETRIES", str(sardinia))
    geometries = GeometryIndex.from_disk()
    assert geometries.has("EMMA_ID", "IT019") and geometries.has("NUTS3", "FR713")  # his shapes come on top of the bundled ones
    sardinian = next(w for w in samples.feed("IT").vigilances if w.area.description == "Sardegna")
    assert localization_of(sardinian, geometries) == "geocode"
    assert PointLocator(*samples.CAGLIARI, geometries).covering(sardinian) == "geocode"
    assert PointLocator(*samples.PARIS, geometries).covering(sardinian) is None


def test_the_local_file_is_found_next_to_the_bundled_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIGIE_EMMA_GEOMETRIES", raising=False)
    (tmp_path / "local").mkdir()
    _square_file(tmp_path / "local" / "emma_id.geojson.gz", "IT019", 9.1, 39.2)
    _square_file(tmp_path / "geocodes-test.geojson.gz", "TEST1", 0.0, 0.0)
    geometries = GeometryIndex.from_disk(tmp_path)
    assert geometries.has("EMMA_ID", "IT019")
    assert geometries.schemes() == {"EMMA_ID": 2}


def test_without_any_file_there_is_no_shape_and_no_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIGIE_EMMA_GEOMETRIES", raising=False)
    geometries = GeometryIndex.from_disk(tmp_path)
    assert geometries.schemes() == {} and geometries.codes_at(48.0, 2.0) == set()
    assert localization_of(warning("FR713"), geometries) == "none"


def test_a_file_that_is_not_a_feature_collection_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "geocodes-bad.geojson.gz"
    with gzip.open(broken, "wt", encoding="utf-8") as handle:
        json.dump({"type": "Feature"}, handle)
    with pytest.raises(ValueError, match="not a FeatureCollection"):
        GeometryIndex.from_disk(tmp_path)
    with pytest.raises(ValueError, match="not a FeatureCollection"):
        CountryIndex.from_file(broken)


def test_a_synthetic_warning_is_matched_through_its_code() -> None:
    geometries = default_geometries()
    assert PointLocator(*samples.VALENCE, geometries).covering(warning("FR713")) == "geocode"
    assert PointLocator(*samples.VALENCE, geometries).covering(warning("FR712")) is None
    assert PointLocator(*samples.VALENCE, geometries).covering(warning("FR713", scheme="OTHER")) is None
