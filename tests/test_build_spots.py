"""Offline unit tests for ``tools/build_spots.py``: beach boundary matching, merge rules, 360° coastal shelter,
GIP Littoral integration, and SHOM Natures de fond mapping."""

from __future__ import annotations

from tools import build_spots


def test_long_beach_geometry_distance_and_anchor_point() -> None:
    # A 3 km long north-south beach polygon whose center is 1.5 km away from a school near its south end.
    beach_poly = {
        "type": "way",
        "id": 1001,
        "center": {"lat": 44.015, "lon": -1.300},
        "bounds": {"minlat": 44.000, "minlon": -1.301, "maxlat": 44.030, "maxlon": -1.299},
        "geometry": [
            {"lat": 44.000, "lon": -1.301},
            {"lat": 44.030, "lon": -1.301},
            {"lat": 44.030, "lon": -1.299},
            {"lat": 44.000, "lon": -1.299},
            {"lat": 44.000, "lon": -1.301},
        ],
        "tags": {"natural": "beach", "name": "Grande Plage", "surface": "sand"},
    }
    school_at = (44.001, -1.298)  # 0.1 km from southern boundary, > 1.5 km from center
    dist = build_spots.element_distance_km(school_at, beach_poly)
    assert dist is not None and dist <= 0.25
    anchor = build_spots.anchor_point(beach_poly, school_at)
    assert anchor is not None and abs(anchor[0] - 44.001) <= 0.01


def test_merge_close_keeps_distinct_named_beaches_and_preserves_legacy_anchor() -> None:
    # Two differently named beaches 400 m apart stay distinct (>= MERGE_DISTINCT_KM=0.25).
    # Same-named duplicates within MERGE_KM=0.75 merge into one spot while preserving the legacy anchor.
    spots = [
        {"name": "Plage Nord", "at": (43.670, -1.440), "osm": "way/1", "facing_deg": 280, "beach_surface": None, "lifeguarded": None, "evidence": [{"osm": "way/1", "name": "Plage Nord", "kind": "spot"}]},
        {"name": "Plage Sud", "at": (43.6664, -1.440), "osm": "way/2", "facing_deg": 280, "beach_surface": "sand", "lifeguarded": None, "evidence": [{"osm": "way/2", "name": "Plage Sud", "kind": "spot"}]},
        {"name": "Plage Sud", "at": (43.6680, -1.440), "osm": "node/3", "facing_deg": 280, "beach_surface": None, "lifeguarded": True, "evidence": [{"source": "gip_littoral", "id": "GIP1", "name": "Plage Sud", "kind": "beach"}]},
    ]
    merged = build_spots.merge_close(spots, legacy_osm={"way/2"})
    names = sorted(s["name"] for s in merged)
    assert names == ["Plage Nord", "Plage Sud"]
    south = next(s for s in merged if s["name"] == "Plage Sud")
    assert south["osm"] == "way/2" and south["lifeguarded"] is True and len(south["evidence"]) == 2


def test_open_water_from_obstacles_splits_on_island_and_blocks_harbour_jetties() -> None:
    # Straight N-S coast running south->north along lon=-2.0 facing W (270°).
    # Small rock islet (< 0.15 km) due west is ignored; real island ~2 km wide blocks WNW; breakwater blocks SW.
    coast_main = [(48.0, -2.0), (48.20, -2.0), (48.35, -2.0), (48.50, -2.0), (48.7, -2.0)]
    tiny_rock = [
        (48.3500, -2.0200),
        (48.3503, -2.0200),
        (48.3503, -2.0203),
        (48.3500, -2.0203),
        (48.3500, -2.0200),
    ]
    real_island = [
        (48.358, -2.060),
        (48.372, -2.060),
        (48.372, -2.040),
        (48.358, -2.040),
        (48.358, -2.060),
    ]
    breakwater = [(48.348, -2.002), (48.345, -2.020)]
    sectors, width_deg = build_spots.open_water_from_obstacles(
        [coast_main, tiny_rock, real_island], [breakwater], (48.350, -2.000), facing_deg=270
    )
    assert width_deg > 0 and len(sectors) >= 2
    # East sector (landward) must never appear open.
    assert all(not (a <= 90 <= b if a <= b else 90 >= a or 90 <= b) for a, b in sectors)
    # Island to WNW blocks around ~285° while West (270°, past the tiny rock) stays open!
    assert any((a <= 270 <= b) if a <= b else (270 >= a or 270 <= b) for a, b in sectors)


def test_classify_seafloor_maps_shom_codes_and_ignores_unknowns() -> None:
    poly = [[[-1.46, 43.65], [-1.43, 43.65], [-1.43, 43.68], [-1.46, 43.68], [-1.46, 43.65]]]
    geom = {"type": "Polygon", "coordinates": poly}
    assert build_spots.classify_seafloor((43.665, -1.445), 270, [((-1.46, 43.65, -1.43, 43.68), geom, "NFS")]) == ("sand", ["NFS"])
    assert build_spots.classify_seafloor((43.665, -1.445), 270, [((-1.46, 43.65, -1.43, 43.68), geom, "NFRoche")]) == ("rock", ["NFRoche"])
    assert build_spots.classify_seafloor((43.665, -1.445), 270, [((-1.46, 43.65, -1.43, 43.68), geom, "UNKNOWN")]) == (None, ["UNKNOWN"])
    assert build_spots.classify_seafloor((44.500, -1.445), 270, [((-1.46, 43.65, -1.43, 43.68), geom, "NFS")]) == (None, None)


def test_gip_littoral_filters_non_oceanic_beaches_and_sets_lifeguarded() -> None:
    gip = [
        {
            "geometry": {"type": "Point", "coordinates": [-1.25, 44.65]},
            "properties": {
                "nature_baignade": "Océanique",
                "nom_plage": "Plage Océane",
                "id_plage_sit": "GIP100",
                "plage_surveillee": "Oui",
                "plage_caracteristiques": "Sable fin",
            },
        },
        {
            "geometry": {"type": "Point", "coordinates": [-1.15, 44.65]},
            "properties": {
                "nature_baignade": "Estuaire / Plan d'eau",
                "nom_plage": "Plage Lacustre",
                "id_plage_sit": "GIP200",
                "plage_surveillee": "Oui",
            },
        },
    ]
    found = build_spots.candidates([], [], gip_features=gip)
    assert len(found) == 1
    assert found[0]["name"] == "Plage Océane" and found[0]["lifeguarded"] is True and found[0]["beach_surface"] == "sand"
    assert found[0]["evidence"] == [{"source": "gip_littoral", "id": "GIP100", "name": "Plage Océane", "kind": "beach"}]
