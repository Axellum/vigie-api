"""Tests of the provider parsers.

Rule under test: a variable the provider does not supply becomes ``None`` (or the item is
skipped / the parser raises); no parser invents a default value.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector
from app.models.schemas import HazardCategory, SeverityLevel
from tests import samples
from tests.helpers import Provider

# ============================================================================
# Seismic
# ============================================================================


def test_usgs_parser() -> None:
    data = {
        "features": [
            {
                "id": "us1234",
                "properties": {"mag": 6.8, "place": "Sendai, Japan", "time": 1792334500000, "tsunami": 1, "alert": "red"},
                "geometry": {"coordinates": [142.85, 38.10, 25.0]},
            }
        ]
    }
    parsed = USGSCollector.parse_geojson(data)
    assert len(parsed) == 1
    assert parsed[0].severity == SeverityLevel.RED
    assert parsed[0].tsunami_warning is True
    assert parsed[0].depth_km == 25.0
    assert parsed[0].timestamp_utc == 1792334500


def test_usgs_parser_never_invents_depth_or_time() -> None:
    data = {
        "features": [
            {  # no depth in the geometry: depth stays unknown
                "id": "a",
                "properties": {"mag": 5.1, "place": "Somewhere", "time": 1792334500000},
                "geometry": {"coordinates": [10.0, 20.0]},
            },
            {  # no event time: skipped, not dated "now"
                "id": "b",
                "properties": {"mag": 5.1, "place": "Elsewhere"},
                "geometry": {"coordinates": [10.0, 20.0, 5.0]},
            },
        ]
    }
    parsed = USGSCollector.parse_geojson(data)
    assert [h.id for h in parsed] == ["usgs_a"]
    assert parsed[0].depth_km is None


def test_emsc_parser() -> None:
    data = {
        "features": [
            {
                "id": "emsc99",
                "properties": {"mag": 5.3, "flynn_region": "CRETE, GREECE", "time": "2026-10-18T12:00:00Z", "magtype": "ML"},
                "geometry": {"coordinates": [25.1, 35.2, -15.0]},
            },
            {
                "id": "emsc100",
                "properties": {"mag": 5.0, "flynn_region": "NO TIME"},
                "geometry": {"coordinates": [25.1, 35.2]},
            },
        ]
    }
    parsed = EMSCCollector.parse_geojson(data)
    assert len(parsed) == 1  # the event without a time is skipped
    assert parsed[0].depth_km == 15.0
    assert parsed[0].severity == SeverityLevel.YELLOW
    assert parsed[0].value_unit == "ML"


# ============================================================================
# Disasters
# ============================================================================


def test_gdacs_parser() -> None:
    data = {
        "features": [
            {
                "properties": {
                    "eventtype": "TC",
                    "eventid": "9988",
                    "alertlevel": "Red",
                    "name": "Cyclone Milton",
                    "country": "United States",
                    "severitydata": {"severity": 250.0, "severityunit": "km/h"},
                    "fromdate": "2026-10-18T10:00:00Z",
                },
                "geometry": {"coordinates": [-82.5, 27.5]},
            },
            {  # no severity value reported: the value stays unknown
                "properties": {
                    "eventtype": "FL",
                    "eventid": "1",
                    "alertlevel": "Orange",
                    "name": "Flood",
                    "fromdate": "2026-10-18T10:00:00Z",
                },
                "geometry": {"coordinates": [10.0, 40.0]},
            },
            {  # no alert level: cannot be classified, skipped
                "properties": {"eventtype": "EQ", "eventid": "2", "fromdate": "2026-10-18T10:00:00Z"},
                "geometry": {"coordinates": [10.0, 40.0]},
            },
        ]
    }
    parsed = GDACSCollector.parse_geojson(data)
    assert len(parsed) == 2
    assert parsed[0].category == HazardCategory.HURRICANE
    assert parsed[0].value_primary == 250.0
    assert parsed[1].category == HazardCategory.FLOOD
    assert parsed[1].value_primary is None


def _gdacs_feature(event_id: str, iscurrent: str) -> dict[str, object]:
    return {
        "properties": {
            "eventtype": "EQ",
            "eventid": event_id,
            "alertlevel": "Orange",
            "name": f"Earthquake {event_id}",
            "fromdate": "2026-10-18T10:00:00",
            "iscurrent": iscurrent,
            "severitydata": {"severity": 6.4, "severityunit": "M"},
        },
        "geometry": {"type": "Point", "coordinates": [120.0, 15.0]},
    }


def test_gdacs_parser_keeps_current_events_only() -> None:
    data = {"features": [_gdacs_feature("1", "true"), _gdacs_feature("2", "false")]}
    assert [h.id for h in GDACSCollector.parse_geojson(data)] == ["gdacs_eq_1"]


def test_gdacs_search_query() -> None:
    now = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc).timestamp()
    assert GDACSCollector.search_params(now) == {
        "eventlist": "EQ;TC;FL;VO;WF",
        "alertlevel": "Orange;Red",
        "fromDate": "2026-09-20",
        "pageSize": "100",
        "pageNumber": "1",
    }


async def test_gdacs_collector_uses_the_search_endpoint() -> None:
    provider = Provider({"type": "FeatureCollection", "features": [_gdacs_feature("7", "true")]})
    alerts = await GDACSCollector(http_client=provider.client()).fetch()
    assert provider.count == 1
    request = provider.requests[0]
    assert request.url.path == "/gdacsapi/api/events/geteventlist/SEARCH"
    assert request.url.params["eventlist"] == "EQ;TC;FL;VO;WF"
    assert [h.id for h in alerts] == ["gdacs_eq_7"]


async def test_gdacs_no_matching_event_is_an_empty_answer_not_a_failure() -> None:
    provider = Provider(answer=lambda request: httpx.Response(204))
    collector = GDACSCollector(http_client=provider.client())
    assert await collector.fetch() == []
    assert collector.consecutive_failures == 0 and collector.last_error is None


def test_noaa_parser_has_no_made_up_value() -> None:
    data = {
        "features": [
            {
                "id": "urn:oid:2.49.0.1.840.0.1234",
                "properties": {
                    "event": "Tornado Warning",
                    "severity": "Extreme",
                    "areaDesc": "Oklahoma County, OK; Canadian County, OK",
                    "sent": "2026-10-18T13:00:00+00:00",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-97.6, 35.4], [-97.4, 35.4], [-97.4, 35.6], [-97.6, 35.6], [-97.6, 35.4]]],
                },
            },
            {  # no send time: skipped
                "id": "x",
                "properties": {"event": "Tornado Warning", "severity": "Extreme"},
                "geometry": {"type": "Point", "coordinates": [-97.0, 35.0]},
            },
        ]
    }
    parsed = NOAANWSCollector.parse_alerts(data)
    assert len(parsed) == 1
    assert parsed[0].category == HazardCategory.TORNADO
    assert parsed[0].severity == SeverityLevel.RED
    assert parsed[0].value_primary is None  # NWS alerts carry no wind speed


def test_usgs_volcano_parser_real_feed_shape() -> None:
    """The real USGS feed uses vName / lat / long / colorCode / sentUtc."""
    payload = [
        {
            "vName": "Great Sitkin",
            "vnum": "311120",
            "lat": 52.0765,
            "long": -176.1109,
            "alertLevel": "WATCH",
            "colorCode": "ORANGE",
            "sentUtc": "2026-10-19 20:01:42",
        },
        {"vName": "No time", "vnum": "1", "lat": 1.0, "long": 2.0, "colorCode": "YELLOW"},
        {"vName": "No level", "vnum": "2", "lat": 1.0, "long": 2.0, "sentUtc": "2026-10-19 20:01:42"},
    ]
    alerts = USGSVolcanoCollector.parse_volcanoes(payload)
    assert len(alerts) == 1
    sitkin = alerts[0]
    assert sitkin.severity == SeverityLevel.ORANGE
    assert sitkin.lat == 52.0765 and sitkin.lon == -176.1109
    assert sitkin.timestamp_utc == int(datetime(2026, 10, 19, 20, 1, 42, tzinfo=timezone.utc).timestamp())
    assert sitkin.value_primary is None  # the VEI is not derived from the colour code
    assert sitkin.value_unit is None
    assert "Great Sitkin" in sitkin.title


def test_usgs_volcano_parser_uses_vei_only_when_given() -> None:
    payload = [
        {
            "vnum": "311240",
            "v_name": "Kilauea",
            "latitude": 19.421,
            "longitude": -155.287,
            "color_code": "RED",
            "vei": 4,
            "sentUtc": "2026-10-19 20:01:42",
        }
    ]
    alerts = USGSVolcanoCollector.parse_volcanoes(payload)
    assert alerts[0].severity == SeverityLevel.RED
    assert alerts[0].value_primary == 4.0
    assert alerts[0].value_unit == "VEI"


# ============================================================================
# Space weather, radar, marine
# ============================================================================


def test_swpc_parser_current_object_layout() -> None:
    """The live file is a list of objects with a capital-K ``Kp`` key."""
    payload = [
        {"time_tag": "2026-10-13T00:00:00", "Kp": 3.33, "a_running": 18, "station_count": 8},
        {"time_tag": "2026-10-20T03:00:00", "Kp": 6.33, "a_running": 80, "station_count": 8},
    ]
    status = NOAASWPCCollector.parse_kp_json(payload)
    assert status.solar_kp == 6.3
    assert status.solar_storm_level == "G2"
    assert status.gps_degraded is True
    assert status.hf_radio_blackout_risk is True


def test_swpc_parser_header_row_layout() -> None:
    payload = [
        ["time_tag", "Kp", "Kp_fraction", "a_running", "station_count"],
        ["2026-10-18 12:00:00.000", "1.33", "1.33", "5", "8"],
    ]
    status = NOAASWPCCollector.parse_kp_json(payload)
    assert status.solar_kp == 1.3
    assert status.solar_storm_level == "G0"
    assert status.gps_degraded is False


@pytest.mark.parametrize("payload", [[], {}, "x", [{"time_tag": "t"}], [["time_tag", "Kp"], ["t", "n/a"]]])
def test_swpc_parser_raises_instead_of_defaulting(payload: object) -> None:
    with pytest.raises(ValueError):
        NOAASWPCCollector.parse_kp_json(payload)


def test_rainviewer_parser_uses_the_latest_observed_frame() -> None:
    """Since 2026-01-01 the free API has no nowcast; a future frame would be a forecast, so it is ignored."""
    data = {
        "host": "https://tilecache.rainviewer.com",
        "radar": {
            "past": [
                {"time": 1792329400, "path": "/v2/radar/older"},
                {"time": 1792330000, "path": "/v2/radar/1792330000"},
            ],
            "nowcast": [{"time": 1792330600, "path": "/v2/radar/nowcast_abc"}],
        },
    }
    parsed = RainViewerCollector.parse_maps(data)
    assert parsed["radar_timestamp_utc"] == 1792330000
    assert parsed["radar_tile_url_template"] == (
        "https://tilecache.rainviewer.com/v2/radar/1792330000/256/{z}/{x}/{y}/2/1_1.png"
    )
    assert parsed["past_frames_count"] == 2


def test_rainviewer_parser_raises_without_a_past_frame() -> None:
    with pytest.raises(ValueError):
        RainViewerCollector.parse_maps({"host": "https://tilecache.rainviewer.com", "radar": {}})
    with pytest.raises(ValueError):  # a nowcast frame alone is not a radar image
        RainViewerCollector.parse_maps(
            {"host": "https://tilecache.rainviewer.com", "radar": {"nowcast": [{"time": 1, "path": "/v2/radar/x"}]}}
        )


def test_marine_parser() -> None:
    parsed = OpenMeteoMarineCollector.parse_response(samples.marine_payload())
    assert parsed.sea_state_label == "HIGH"
    assert parsed.navigation_warning is True
    assert parsed.wave_height_m == 4.2


def test_marine_parser_keeps_missing_values_unknown() -> None:
    parsed = OpenMeteoMarineCollector.parse_response({"current": {"wave_height": 0.4}})
    assert parsed.wave_height_m == 0.4
    assert parsed.sea_state_label == "SMOOTH"
    assert parsed.wave_period_s is None
    assert parsed.ocean_current_velocity_kmh is None
    assert parsed.navigation_warning is False


def test_marine_parser_raises_without_any_value() -> None:
    with pytest.raises(ValueError):
        OpenMeteoMarineCollector.parse_response({"current": {"wave_height": None}})


# ============================================================================
# Flood and air quality
# ============================================================================


def test_flood_parser_levels() -> None:
    red = OpenMeteoFloodCollector.parse_response(
        {
            "daily": {
                "river_discharge": [180.0, 310.0, 420.0],
                "river_discharge_mean": [65.0, 65.0, 65.0],
                "river_discharge_max": [210.0, 350.0, 420.0],
            }
        }
    )
    assert red.river_discharge_m3s == 180.0
    assert red.river_discharge_max_m3s == 420.0
    assert red.anomaly_ratio is not None and red.anomaly_ratio >= 5.0
    assert red.flood_alert_level == SeverityLevel.RED

    green = OpenMeteoFloodCollector.parse_response(
        {
            "daily": {
                "river_discharge": [42.0, 44.0],
                "river_discharge_mean": [40.0, 40.0],
                "river_discharge_max": [45.0, 46.0],
            }
        }
    )
    assert green.flood_alert_level == SeverityLevel.GREEN


def test_flood_parser_without_mean_has_no_level() -> None:
    status = OpenMeteoFloodCollector.parse_response({"daily": {"river_discharge": [12.0, 14.0]}})
    assert status.river_discharge_m3s == 12.0
    assert status.river_discharge_mean_m3s is None
    assert status.anomaly_ratio is None
    assert status.flood_alert_level is None


def test_flood_parser_raises_without_discharge() -> None:
    with pytest.raises(ValueError):
        OpenMeteoFloodCollector.parse_response({"daily": {"river_discharge": [None]}})


def test_air_quality_parser() -> None:
    payload = {
        "current": {
            "european_aqi": 115,
            "us_aqi": 142,
            "pm2_5": 48.5,
            "pm10": 165.0,
            "uv_index": 6.4,
            "dust": 185.0,
        }
    }
    status = OpenMeteoAirQualityCollector.parse_response(payload)
    assert status.european_aqi == 115
    assert status.us_aqi == 142
    assert status.pm2_5_ugm3 == 48.5
    assert status.pm10_ugm3 == 165.0
    assert status.uv_index == 6.4
    assert status.dust_ugm3 == 185.0
    assert status.ash_or_dust_warning is True


def test_air_quality_parser_keeps_missing_values_unknown() -> None:
    status = OpenMeteoAirQualityCollector.parse_response({"current": {"european_aqi": 0, "pm10": 9.0}})
    assert status.european_aqi == 0  # a real zero is kept
    assert status.us_aqi is None
    assert status.pm2_5_ugm3 is None
    assert status.uv_index is None
    assert status.ash_or_dust_warning is False


def test_air_quality_parser_raises_without_any_value() -> None:
    with pytest.raises(ValueError):
        OpenMeteoAirQualityCollector.parse_response({"current": {"time": "2026-10-20T07:00"}})


# ============================================================================
# Weather
# ============================================================================


def test_open_meteo_parser_full_response() -> None:
    parsed = OpenMeteoCollector.parse_response(samples.open_meteo_payload())
    current = parsed["current"]
    assert parsed["utc_offset_s"] == 7200
    assert current.temp_c == 18.2
    assert current.pressure_hpa == 1016.4
    assert current.uv_index == 1.7
    assert current.aqi_eu is None  # filled later from the air quality collector
    assert parsed["rain_next_60m"] == [0.0, 0.2, 0.5, 0.1]

    days = parsed["daily_10d"]
    assert [d.date for d in days] == ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    assert [d.rain_probability_pct for d in days] == [80, 10, 5, 35, 90]
    assert [d.uv_max for d in days] == [3.5, 4.0, 4.2, 3.0, 2.5]
    assert days[0].code == 61 and days[0].tmin == 12.0 and days[0].tmax == 19.0


def test_open_meteo_parser_keeps_missing_values_unknown() -> None:
    parsed = OpenMeteoCollector.parse_response(
        {
            "current": {"temperature_2m": 9.0},
            "daily": {"time": ["2026-10-20"], "temperature_2m_max": [11.0]},
        }
    )
    current = parsed["current"]
    assert current.temp_c == 9.0
    assert current.humidity_pct is None
    assert current.pressure_hpa is None
    assert current.wind_kmh is None
    assert current.gust_kmh is None
    assert current.weather_code is None
    assert current.uv_index is None
    assert parsed["utc_offset_s"] is None
    assert parsed["rain_next_60m"] is None  # no nowcast: unknown, not four zeros

    day = parsed["daily_10d"][0]
    assert day.tmax == 11.0
    assert day.tmin is None and day.code is None and day.rain_mm is None
    assert day.rain_probability_pct is None and day.wind_max is None and day.uv_max is None


def test_open_meteo_parser_incomplete_nowcast_is_unknown() -> None:
    payload = samples.open_meteo_payload()
    payload["minutely_15"] = {"precipitation": [0.0, None, 0.2, 0.0]}
    assert OpenMeteoCollector.parse_response(payload)["rain_next_60m"] is None


def test_open_meteo_parser_raises_without_temperature() -> None:
    with pytest.raises(ValueError):
        OpenMeteoCollector.parse_response({"current": {"relative_humidity_2m": 70}})
    with pytest.raises(ValueError):
        OpenMeteoCollector.parse_response({})


def test_metno_parser_reports_what_the_compact_product_lacks_as_unknown() -> None:
    parsed = samples.parsed_metno()
    current = parsed["current"]
    assert current.temp_c == 14.8
    assert current.humidity_pct == 81
    assert current.pressure_hpa == 1009.5
    assert current.wind_kmh == 21.6
    assert current.rain_1h_mm == 1.6
    assert current.weather_code == 61
    assert current.feels_like_c is None
    assert current.gust_kmh is None
    assert current.uv_index is None
    assert parsed["rain_next_60m"] is None
    assert parsed["utc_offset_s"] is None


def test_metno_parser_daily_values_come_from_each_day() -> None:
    days = samples.parsed_metno()["daily_10d"]
    assert [d.date for d in days] == ["2026-10-20", "2026-10-21"]
    first, second = days
    assert (first.tmin, first.tmax) == (10.5, 14.8)
    assert first.wind_max == 32.4
    assert first.rain_mm == 1.6
    assert first.code == 63  # most severe symbol of the day (rain), not the current symbol
    assert second.code == 0 and second.tmax == 9.0
    assert first.rain_probability_pct is None and first.uv_max is None


def test_metno_parser_groups_hours_into_local_days() -> None:
    days = MetNorwayCollector.parse_response(samples.metno_payload(), days=5, utc_offset_s=9 * 3600)["daily_10d"]
    # 14:00Z is 23:00 on Oct 20 in UTC+9; the other three steps fall on Oct 21.
    assert [d.date for d in days] == ["2026-10-20", "2026-10-21"]
    assert days[0].tmin == days[0].tmax == 14.8
    assert days[1].tmin == 9.0 and days[1].tmax == 12.0


def test_metno_parser_unknown_symbol_is_unknown_code() -> None:
    payload = samples.metno_payload()
    first = payload["properties"]["timeseries"][0]["data"]
    first["next_1_hours"]["summary"]["symbol_code"] = "somethingnew"
    assert MetNorwayCollector.parse_response(payload)["current"].weather_code is None


def test_metno_parser_raises_without_temperature_or_series() -> None:
    with pytest.raises(ValueError):
        MetNorwayCollector.parse_response({"properties": {"timeseries": []}})
    with pytest.raises(ValueError):
        MetNorwayCollector.parse_response(
            {"properties": {"timeseries": [{"time": "2026-10-20T14:00:00Z", "data": {"instant": {"details": {}}}}]}}
        )
