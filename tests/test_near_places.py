"""Near places: what a hazard is close to, computed by Vigie (active faults, volcanoes in alert, the points the user watches).

The checks that matter run on **real answers recorded on 2026-10-20** (``tests/fixtures/places/README.md``): the M 7.7 earthquake of
Panama, the real volcano alerts of the USGS and GDACS. The distance to a fault is checked against a second, independent
computation (``tests/oracle_geodesy.py``: Vincenty on the WGS84 ellipsoid, to a dense sampling of the original, unsimplified GEM
traces), not against the code that is tested.
"""

from __future__ import annotations

import pytest
from shapely.geometry import LineString

from app.attribution import CREDITS
from app.collectors.disasters import GDACSCollector
from app.collectors.flood_air import USGSVolcanoCollector
from app.models.places import NearPlace, WatchedPoint
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.services.geodesy import deduplicate_hazards
from app.services.geoindex import line_distance_km
from app.services.places import FaultIndex, NearPlacesService, PlaceSettings
from tests import places_samples as real
from tests.oracle_geodesy import distance_to_line_km
from tools.build_faults import TOLERANCE_DEG, fault_label

KM_PER_DEGREE = 111.195
# The bundled traces are simplified within TOLERANCE_DEG (about 0.56 km) and rounded (about 0.1 km): the distance to them can differ
# from the distance to the original trace by that much, and no more. The radius is 10 km, so this is below one in fifteen of it.
SIMPLIFICATION_KM = TOLERANCE_DEG * KM_PER_DEGREE + 0.15


@pytest.fixture(scope="module")
def service() -> NearPlacesService:
    """The service with the real bundled fault file and the default settings."""
    return NearPlacesService(PlaceSettings())


def alert(
    alert_id: str = "t1",
    category: HazardCategory = HazardCategory.EARTHQUAKE,
    lat: float = 0.0,
    lon: float = 0.0,
    depth_km: float | None = 10.0,
    severity: SeverityLevel = SeverityLevel.YELLOW,
    sources: tuple[str, ...] = ("USGS",),
) -> HazardAlert:
    return HazardAlert(
        id=alert_id,
        category=category,
        severity=severity,
        title="A hazard",
        source="+".join(sources),
        sources=list(sources),
        timestamp_utc=1_792_000_000,
        lat=lat,
        lon=lon,
        depth_km=depth_km,
    )


def straight_index(*traces: tuple[str, list[list[float]]]) -> FaultIndex:
    return FaultIndex.from_features(
        [{"type": "Feature", "properties": {"n": name}, "geometry": {"type": "LineString", "coordinates": coordinates}} for name, coordinates in traces]
    )


def no_faults() -> NearPlacesService:
    """A service that knows no fault: the tests of the volcanoes and of the points look at those alone."""
    return NearPlacesService(PlaceSettings(), lambda: straight_index())


def north_of(volcano: HazardAlert, km: float) -> tuple[float, float]:
    """A position ``km`` north of a volcano's real position (a stated offset: no recorded earthquake happened there)."""
    return volcano.lat + km / KM_PER_DEGREE, volcano.lon


# ============================================================================
# 1. Active faults, against an independent computation
# ============================================================================


def raw_traces(label: str) -> list[list[list[float]]]:
    return [f["geometry"]["coordinates"] for f in real.gem_excerpt() if fault_label(f["properties"]) == label]


def test_the_panama_earthquake_is_about_4_km_from_the_azuero_fault_zone(service: NearPlacesService) -> None:
    """M 7.7 of 9 October 2026, depth 12.6 km: the real earthquake, the real GEM trace, and a second computation of the distance."""
    quake = real.usgs_earthquake(real.PANAMA_M77)
    assert (quake.value_primary, quake.depth_km) == (7.7, 12.6)

    places = service.near_places(quake, [], [])
    assert places is not None and [p.name for p in places] == ["Azuero Fault Zone"]
    place = places[0]
    assert (place.kind, place.sources) == ("fault", ["GEM"])

    oracle = min(distance_to_line_km(quake.lat, quake.lon, trace) for trace in raw_traces("Azuero Fault Zone"))
    assert oracle == pytest.approx(4.2, abs=0.3)  # the independent figure: a few kilometres, as the API says
    assert abs(place.distance_km - oracle) <= SIMPLIFICATION_KM  # the bundled, simplified trace


@pytest.mark.parametrize("event_id", [real.PANAMA_M77, real.PANAMA_M66, real.TEZPUR])
def test_the_distance_to_the_raw_trace_is_the_geodesic_one_to_a_fraction_of_a_percent(event_id: str) -> None:
    """The method (a flat map scaled to the latitude, then the great circle) against Vincenty on the ellipsoid, on the unsimplified traces."""
    quake = real.usgs_earthquake(event_id)
    compared = 0
    for feature in real.gem_excerpt():
        coordinates = [vertex[:2] for vertex in feature["geometry"]["coordinates"]]
        oracle = distance_to_line_km(quake.lat, quake.lon, coordinates)
        if oracle > 12.0:
            continue
        ours = line_distance_km(quake.lat, quake.lon, LineString(coordinates))
        assert ours == pytest.approx(oracle, rel=0.005, abs=0.06), (event_id, fault_label(feature["properties"]))
        compared += 1
    assert compared >= 1


@pytest.mark.parametrize(
    ("event_id", "radius"),
    [(real.PANAMA_M77, 10.0), (real.PANAMA_M77, 5.0), (real.PANAMA_M66, 15.0), (real.TEZPUR, 10.0)],
)
def test_the_faults_found_are_the_ones_an_independent_search_finds(event_id: str, radius: float) -> None:
    """Every fault the oracle puts within the radius is found (and nothing else): same names, distances within the simplification.

    The M 6.6 earthquake of Panama is 11.9 km from the Azuero Fault: not said at 10 km, said at 15.
    """
    service = NearPlacesService(PlaceSettings(fault_km=radius))
    quake = real.usgs_earthquake(event_id)
    truth: dict[str, float] = {}
    for feature in real.gem_excerpt():  # every trace within 30 km of the event: the excerpt is complete for this radius
        label = fault_label(feature["properties"])
        if label is None:
            continue
        distance = distance_to_line_km(quake.lat, quake.lon, [vertex[:2] for vertex in feature["geometry"]["coordinates"]])
        truth[label] = min(distance, truth.get(label, float("inf")))
    found = {p.name: p.distance_km for p in service.near_places(quake, [], []) or []}

    must_be_found = {name for name, d in truth.items() if d <= radius - SIMPLIFICATION_KM}
    may_be_found = {name for name, d in truth.items() if d <= radius + SIMPLIFICATION_KM}
    assert must_be_found <= set(found) <= may_be_found
    assert found, event_id
    for name, distance in found.items():
        assert abs(distance - truth[name]) <= SIMPLIFICATION_KM, name


def test_the_tezpur_earthquake_is_about_3_km_from_the_barapani_tyrsad_fault(service: NearPlacesService) -> None:
    quake = real.usgs_earthquake(real.TEZPUR)
    [place] = service.near_places(quake, [], []) or []
    assert place.name == "Barapani - Tyrsad Fault" and place.distance_km == pytest.approx(3.2, abs=0.6)


def test_a_deep_earthquake_is_not_compared_with_a_surface_trace(service: NearPlacesService) -> None:
    """Two earthquakes 162 and 206 km down have a mapped trace within 4 km of their epicentre: it says nothing about them."""
    for event_id, trace in ((real.COLOMBIA_DEEP, "Bucaramanga"), (real.NICARAGUA_DEEP, "Marabios Fault")):
        quake = real.usgs_earthquake(event_id)
        assert quake.depth_km is not None and quake.depth_km > 150.0
        index = service.faults()
        assert index is not None
        assert trace in dict(index.near(quake.lat, quake.lon, 10.0, 5))  # the trace is there...
        assert service.near_places(quake, [], []) == []  # ...and is not said

        # the depth limit is a setting
        deep_enough = NearPlacesService(PlaceSettings(fault_max_depth_km=250.0))
        assert [p.name for p in deep_enough.near_places(quake, [], []) or []] == [trace]


def test_an_earthquake_with_no_depth_is_compared_with_the_faults(service: NearPlacesService) -> None:
    """A depth the source did not give is unknown, not deep: the trace is said."""
    quake = real.usgs_earthquake(real.PANAMA_M77).model_copy(update={"depth_km": None})
    assert [p.name for p in service.near_places(quake, [], []) or []] == ["Azuero Fault Zone"]


def test_an_earthquake_far_from_every_trace_has_an_empty_list_not_a_missing_one(service: NearPlacesService) -> None:
    for event_id in (real.EASTER_ISLAND, real.FIJI_DATE_LINE):
        quake = real.usgs_earthquake(event_id)
        assert service.near_places(quake, [], []) == []


# ============================================================================
# 2. The index: wrapping, a fault said once, the order, the cap
# ============================================================================


def test_a_trace_across_the_date_line_is_near_a_position_on_either_side() -> None:
    index = straight_index(("Wrapped fault", [[-179.95, -1.0], [-179.95, 1.0]]), ("Plain fault", [[0.0, 10.0], [0.0, 11.0]]))
    east_side = index.near(0.0, 179.97, 10.0, 5)  # 0.08 degree of longitude west of the trace, across the line: 8.9 km
    assert [name for name, _ in east_side] == ["Wrapped fault"]
    assert east_side[0][1] == pytest.approx(0.08 * 111.19, abs=0.1)
    west_side = straight_index(("Wrapped fault", [[179.95, -1.0], [179.95, 1.0]])).near(0.0, -179.97, 10.0, 5)
    assert [name for name, _ in west_side] == ["Wrapped fault"]
    assert index.near(0.0, 179.5, 10.0, 5) == []  # 0.45 degree, 50 km: not near


def test_a_fault_with_several_traces_is_said_once_at_its_nearest() -> None:
    index = straight_index(
        ("Long fault", [[10.08, 0.0], [10.08, 1.0]]),  # 0.08 degree east of the position: 8.9 km
        ("Long fault", [[10.03, 0.0], [10.03, 1.0]]),  # 3.3 km: its nearest trace
        ("Other fault", [[10.05, 0.0], [10.05, 1.0]]),  # 5.6 km
        ("Far fault", [[10.12, 0.0], [10.12, 1.0]]),  # 13.3 km: out of the radius
    )
    result = index.near(0.5, 10.0, 10.0, 5)
    assert [name for name, _ in result] == ["Long fault", "Other fault"]
    assert result[0][1] == pytest.approx(3.34, abs=0.05) and result[1][1] == pytest.approx(5.56, abs=0.05)


def test_the_places_are_sorted_nearest_first_capped_and_rounded() -> None:
    traces = [(f"Fault {n}", [[10.0 + 0.01 * n, 0.0], [10.0 + 0.01 * n, 1.0]]) for n in range(1, 9)]  # 1.1 km, 2.2 km... 8.9 km
    service = NearPlacesService(PlaceSettings(max_places=5), lambda: straight_index(*traces))
    places = service.near_places(alert(lat=0.5, lon=10.0), [], []) or []
    assert [p.name for p in places] == [f"Fault {n}" for n in range(1, 6)]
    assert [p.distance_km for p in places] == sorted(p.distance_km for p in places)
    assert all(round(p.distance_km, 1) == p.distance_km for p in places)
    assert places[0].distance_km == pytest.approx(1.1, abs=0.06)


def test_an_empty_index_finds_nothing() -> None:
    assert straight_index().near(0.0, 0.0, 10.0, 5) == []
    assert len(straight_index()) == 0


# ============================================================================
# 3. Faults unknown, other hazards, simulated alerts
# ============================================================================


def test_when_the_fault_file_cannot_be_read_the_faults_of_an_earthquake_are_unknown_not_none_near() -> None:
    service = NearPlacesService(PlaceSettings(), lambda: None)
    quake = real.usgs_earthquake(real.PANAMA_M77)
    assert service.near_places(quake, [], []) is None  # not worked out: not "nothing is near"
    storm = alert("storm", HazardCategory.HURRICANE, 15.0, -60.0)
    assert service.near_places(storm, [], []) == []  # a storm needs no fault: it was worked out
    annotated = service.annotate([quake, storm], [])
    assert [h.near_places for h in annotated] == [None, []]
    assert annotated[0].derived == [] and annotated[1].derived == ["near_places"]


def test_a_storm_a_flood_and_a_tsunami_are_not_compared_with_faults(service: NearPlacesService) -> None:
    on_the_fault = real.usgs_earthquake(real.PANAMA_M77)
    for category in (HazardCategory.HURRICANE, HazardCategory.FLOOD, HazardCategory.TSUNAMI, HazardCategory.WILDFIRE):
        assert service.near_places(alert("x", category, on_the_fault.lat, on_the_fault.lon), [], []) == []


def test_a_volcanic_event_is_compared_with_the_faults(service: NearPlacesService) -> None:
    quake = real.usgs_earthquake(real.PANAMA_M77)
    eruption = alert("v1", HazardCategory.VOLCANO, quake.lat, quake.lon, depth_km=None, sources=("GDACS",))
    assert [p.name for p in service.near_places(eruption, [], []) or []] == ["Azuero Fault Zone"]


def test_a_simulated_alert_is_not_compared_with_anything(service: NearPlacesService) -> None:
    quake = real.usgs_earthquake(real.PANAMA_M77).model_copy(update={"id": "sim_eq_panama"})
    point = WatchedPoint(id="p1", name="Here", kind="place", lat=quake.lat, lon=quake.lon, radius_km=50.0)
    assert service.near_places(quake, [], [point]) is None


# ============================================================================
# 4. Volcanoes: only those the hazard sources name
# ============================================================================


def test_the_volcanoes_are_named_by_the_sources_that_report_them() -> None:
    usgs, gdacs = real.usgs_volcanoes(), real.gdacs_volcanoes()
    assert sorted(h.place_name or "" for h in usgs) == ["Ahyi Seamount", "Great Sitkin", "Kilauea", "Shishaldin"]
    assert sorted(h.place_name or "" for h in gdacs) == ["Kanlaon", "Taal"]  # `eventname`: the title says "Eruption  Kanlaon"
    assert real.volcano(gdacs, "Kanlaon").title == "Eruption  Kanlaon (Philippines)"  # the title is untouched


def test_a_volcano_with_no_name_in_the_feed_is_not_named() -> None:
    [unnamed] = USGSVolcanoCollector.parse_volcanoes(
        [{"lat": 10.0, "long": 20.0, "colorCode": "YELLOW", "sentUtc": "2026-10-19 20:01:42", "vnum": "1"}]
    )
    assert unnamed.title.startswith("Unnamed volcano") and unnamed.place_name is None  # "Unnamed" is the API's placeholder, not a name


def test_the_volcano_name_survives_the_merge_of_two_reports() -> None:
    sitkin = real.volcano(real.usgs_volcanoes(), "Great Sitkin")
    gdacs_report = alert("gdacs_vo_9", HazardCategory.VOLCANO, sitkin.lat + 0.05, sitkin.lon, depth_km=None, sources=("GDACS",)).model_copy(
        update={"timestamp_utc": sitkin.timestamp_utc + 3600}
    )
    for pair in ([sitkin, gdacs_report], [gdacs_report, sitkin]):
        [merged] = deduplicate_hazards(pair)
        assert merged.place_name == "Great Sitkin" and sorted(merged.sources) == ["GDACS", "USGS_VOLCANO"]


def test_an_earthquake_near_a_volcano_in_alert_is_told() -> None:
    sitkin = real.volcano(real.usgs_volcanoes(), "Great Sitkin")
    lat, lon = north_of(sitkin, 12.0)
    quake = alert("eq1", lat=lat, lon=lon, depth_km=8.0)
    [place] = no_faults().near_places(quake, [sitkin], []) or []
    assert (place.name, place.kind, place.sources) == ("Great Sitkin", "volcano", ["USGS_VOLCANO"])
    assert place.distance_km == pytest.approx(12.0, abs=0.1)

    far_lat, far_lon = north_of(sitkin, 40.0)
    assert no_faults().near_places(alert("eq2", lat=far_lat, lon=far_lon), [sitkin], []) == []


def test_the_volcano_radius_is_a_setting() -> None:
    sitkin = real.volcano(real.usgs_volcanoes(), "Great Sitkin")
    lat, lon = north_of(sitkin, 30.0)
    quake = alert("eq3", lat=lat, lon=lon)
    wider = NearPlacesService(PlaceSettings(volcano_km=35.0), lambda: straight_index())
    assert no_faults().near_places(quake, [sitkin], []) == []
    assert [p.name for p in wider.near_places(quake, [sitkin], []) or []] == ["Great Sitkin"]


def test_only_earthquakes_are_told_about_volcanoes_and_a_volcano_is_not_its_own_neighbour() -> None:
    gdacs = real.gdacs_volcanoes()
    kanlaon, taal = real.volcano(gdacs, "Kanlaon"), real.volcano(gdacs, "Taal")
    service = no_faults()
    assert service.near_places(kanlaon, [kanlaon, taal], []) == []  # the volcano itself is not near itself; Taal is 4 degrees away
    storm = alert("tc", HazardCategory.HURRICANE, kanlaon.lat + 0.05, kanlaon.lon)
    assert service.near_places(storm, [kanlaon], []) == []  # a storm is not told about volcanoes


def test_the_gdacs_volcano_is_named_too_and_credited() -> None:
    kanlaon = real.volcano(real.gdacs_volcanoes(), "Kanlaon")
    lat, lon = north_of(kanlaon, 9.0)
    [annotated] = no_faults().annotate([alert("eq4", lat=lat, lon=lon), kanlaon], [])[:1]
    [place] = annotated.near_places or []
    assert (place.name, place.kind, place.sources) == ("Kanlaon", "volcano", ["GDACS"])
    assert CREDITS["gdacs"].text in (annotated.attribution or "")  # the volcano's name comes from GDACS: its credit goes with it


# ============================================================================
# 5. The points the user watches
# ============================================================================


def point(name: str, kind: str, lat: float, lon: float, radius_km: float, point_id: str = "p1") -> WatchedPoint:
    return WatchedPoint(id=point_id, name=name, kind=kind, lat=lat, lon=lon, radius_km=radius_km)  # type: ignore[arg-type]


def test_a_watched_point_is_named_within_its_own_radius_for_every_kind_of_hazard() -> None:
    service = no_faults()
    quake = real.usgs_earthquake(real.EASTER_ISLAND)
    lat, lon = quake.lat + 30.0 / KM_PER_DEGREE, quake.lon
    inside, outside = point("Inside", "place", lat, lon, 50.0, "p1"), point("Outside", "place", lat, lon, 20.0, "p2")
    assert [(p.name, p.kind, p.sources) for p in service.near_places(quake, [], [inside, outside]) or []] == [("Inside", "place", [])]
    storm = alert("tc2", HazardCategory.HURRICANE, quake.lat, quake.lon)
    assert [p.name for p in service.near_places(storm, [], [inside, outside]) or []] == ["Inside"]


def test_the_kind_the_user_gave_is_kept_and_a_name_is_said_once() -> None:
    service = no_faults()
    sitkin = real.volcano(real.usgs_volcanoes(), "Great Sitkin")
    lat, lon = north_of(sitkin, 10.0)
    quake = alert("eq5", lat=lat, lon=lon)
    mine = point("great sitkin", "volcano", sitkin.lat, sitkin.lon, 40.0)  # the user watches the same volcano, in lower case
    mixed = point("My camp", "custom", sitkin.lat, sitkin.lon, 40.0, "p2")
    places = service.near_places(quake, [sitkin], [mine, mixed]) or []
    # The volcano is said once (the report's spelling is kept, the user's copy is the same name); at the same distance the order is kind, then name.
    assert [(p.name, p.kind, p.sources) for p in places] == [("My camp", "custom", []), ("Great Sitkin", "volcano", ["USGS_VOLCANO"])]


def test_without_a_watched_point_nothing_is_added() -> None:
    assert no_faults().near_places(real.usgs_earthquake(real.EASTER_ISLAND), [], []) == []


# ============================================================================
# 6. What the result is: derived, credited, never a severity
# ============================================================================


def test_the_result_is_named_in_derived_and_the_fault_is_credited(service: NearPlacesService) -> None:
    [annotated] = service.annotate([real.usgs_earthquake(real.PANAMA_M77)], [])
    assert annotated.near_places is not None and annotated.derived == ["near_places"]
    attribution = annotated.attribution or ""
    assert CREDITS["gem_faults"].text in attribution and CREDITS["usgs"].text in attribution
    dumped = annotated.model_dump(mode="json")
    assert dumped["near_places"] == [
        {"name": "Azuero Fault Zone", "kind": "fault", "distance_km": annotated.near_places[0].distance_km, "sources": ["GEM"]}
    ]
    assert dumped["derived"] == ["near_places"]


def test_an_empty_answer_is_still_derived_but_carries_no_fault_credit(service: NearPlacesService) -> None:
    [annotated] = service.annotate([real.usgs_earthquake(real.EASTER_ISLAND)], [])
    assert annotated.near_places == [] and annotated.derived == ["near_places"]
    assert CREDITS["gem_faults"].text not in (annotated.attribution or "")


def test_a_hazard_that_was_never_annotated_has_nothing_derived() -> None:
    quake = real.usgs_earthquake(real.PANAMA_M77)
    assert quake.near_places is None and quake.derived == []
    assert CREDITS["gem_faults"].text not in (quake.attribution or "")


def test_the_severity_and_everything_else_never_change(service: NearPlacesService) -> None:
    sitkin = real.volcano(real.usgs_volcanoes(), "Great Sitkin")
    hazards = [
        real.usgs_earthquake(real.PANAMA_M77),
        real.usgs_earthquake(real.EASTER_ISLAND),
        real.usgs_earthquake(real.TEZPUR),
        sitkin,
        alert("storm", HazardCategory.HURRICANE, 15.0, -60.0, severity=SeverityLevel.RED),
    ]
    mine = [point("Camp", "place", h.lat, h.lon, 100.0, f"p{n}") for n, h in enumerate(hazards)]
    before = [h.model_dump(exclude={"near_places", "derived", "attribution"}) for h in hazards]
    annotated = service.annotate(hazards, mine)
    assert [h.model_dump(exclude={"near_places", "derived", "attribution"}) for h in annotated] == before
    assert [h.severity for h in annotated] == [h.severity for h in hazards]
    assert all(h.near_places is None for h in hazards)  # the collectors' own (cached) objects are not touched
    assert annotated[3].place_name == "Great Sitkin"  # the private fields go with the copy


def test_the_result_survives_the_history(service: NearPlacesService) -> None:
    """The history stores a hazard as JSON: the places come back, and an old row without them still reads."""
    [annotated] = service.annotate([real.usgs_earthquake(real.PANAMA_M77)], [])
    assert HazardAlert.model_validate_json(annotated.model_dump_json()).near_places == annotated.near_places
    old_row = real.usgs_earthquake(real.PANAMA_M77).model_dump_json(exclude={"near_places", "derived", "attribution"})
    assert HazardAlert.model_validate_json(old_row).near_places is None


def test_a_place_cannot_be_nearer_than_zero() -> None:
    with pytest.raises(ValueError):
        NearPlace(name="x", kind="fault", distance_km=-1.0)


def test_the_gdacs_parser_still_reads_a_volcano_without_a_name() -> None:
    payload = real.read_json("gdacs_volcano_2026-10-20.json")
    for feature in payload["features"]:
        feature["properties"]["iscurrent"] = "true"
        feature["properties"]["eventname"] = ""
    assert [h.place_name for h in GDACSCollector.parse_geojson(payload)] == [None, None]


# ============================================================================
# 7. The settings
# ============================================================================


def test_the_defaults() -> None:
    settings = PlaceSettings()
    assert (settings.fault_km, settings.fault_max_depth_km, settings.volcano_km, settings.max_places) == (10.0, 70.0, 20.0, 5)
    assert (settings.watched_points_max, settings.quiet_max_gap_s) == (50, 900)


def test_the_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_NEAR_FAULT_KM", "25")
    monkeypatch.setenv("VIGIE_NEAR_FAULT_MAX_DEPTH_KM", "40.5")
    monkeypatch.setenv("VIGIE_NEAR_VOLCANO_KM", "7")
    monkeypatch.setenv("VIGIE_NEAR_MAX_PLACES", "3")
    monkeypatch.setenv("VIGIE_WATCHED_POINTS_MAX", "12")
    monkeypatch.setenv("VIGIE_QUIET_MAX_GAP_S", "1800")
    settings = PlaceSettings.from_env()
    assert (settings.fault_km, settings.fault_max_depth_km, settings.volcano_km) == (25.0, 40.5, 7.0)
    assert (settings.max_places, settings.watched_points_max, settings.quiet_max_gap_s) == (3, 12, 1800)


@pytest.mark.parametrize("value", ["", "ten", "nan", "inf", "-5", "0", "100000"])
def test_a_setting_that_makes_no_sense_means_the_default(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    for name in ("VIGIE_NEAR_FAULT_KM", "VIGIE_NEAR_VOLCANO_KM", "VIGIE_NEAR_MAX_PLACES", "VIGIE_WATCHED_POINTS_MAX", "VIGIE_QUIET_MAX_GAP_S"):
        monkeypatch.setenv(name, value)
    assert PlaceSettings.from_env() == PlaceSettings()
