from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from routing.services.geocoding_service import GeocodingService
from routing.services.provider_errors import GeocodingError, NonUSLocationError, RoutingProviderError
from routing.services.route_geometry_service import RouteGeometryService
from routing.services.routing_service import RoutingService


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def response_with_json(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@override_settings(NOMINATIM_MIN_INTERVAL_SECONDS=0)
def test_geocoder_success_and_cache():
    http_response = response_with_json(
        [{"lat": "40.7", "lon": "-74.0", "address": {"country_code": "us"}, "display_name": "NYC"}]
    )
    with patch("routing.services.geocoding_service.requests.get", return_value=http_response) as get:
        first = GeocodingService().geocode("New York, NY")
        second = GeocodingService().geocode("  NEW YORK,   NY ")

    assert first == second
    assert first.latitude == 40.7
    assert first.country_code == "us"
    get.assert_called_once()
    assert get.call_args.kwargs["headers"]["User-Agent"]
    assert get.call_args.kwargs["timeout"] > 0


@pytest.mark.parametrize(
    ("query", "latitude", "longitude"),
    [
        ("New York, NY", 40.7127281, -74.0060152),
        ("Chicago, IL", 41.8755616, -87.6244212),
    ],
)
@override_settings(NOMINATIM_MIN_INTERVAL_SECONDS=0)
def test_geocoder_parses_nominatim_jsonv2_coordinate_strings(query, latitude, longitude):
    payload = [
        {
            "lat": str(latitude),
            "lon": str(longitude),
            "display_name": query + ", United States",
            "address": {"country_code": "us"},
        }
    ]
    with patch(
        "routing.services.geocoding_service.requests.get",
        return_value=response_with_json(payload),
    ):
        location = GeocodingService().geocode(query)

    assert location.latitude == latitude
    assert location.longitude == longitude
    assert location.country_code == "us"


@override_settings(NOMINATIM_MIN_INTERVAL_SECONDS=0)
def test_geocoder_missing_result_is_clean_error():
    with patch("routing.services.geocoding_service.requests.get", return_value=response_with_json([])):
        with pytest.raises(GeocodingError):
            GeocodingService().geocode("Not a real city")


@override_settings(NOMINATIM_MIN_INTERVAL_SECONDS=0)
def test_geocoder_rejects_non_us_response():
    payload = [{"lat": "43.6", "lon": "-79.3", "address": {"country_code": "ca"}}]
    with patch("routing.services.geocoding_service.requests.get", return_value=response_with_json(payload)):
        with pytest.raises(NonUSLocationError):
            GeocodingService().geocode("Toronto, Canada")


def test_routing_service_requests_geojson_and_caches_route():
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 1609.344,
                "duration": 600,
                "geometry": {"type": "LineString", "coordinates": [[-74.0, 40.7], [-73.0, 41.0]]},
            }
        ],
    }
    with patch(
        "routing.services.routing_service.requests.get",
        return_value=response_with_json(payload),
    ) as get:
        first = RoutingService().get_route((40.7, -74.0), (41.0, -73.0))
        second = RoutingService().get_route((40.7, -74.0), (41.0, -73.0))

    assert first == second
    assert first.distance_meters == 1609.344
    assert first.coordinates[0] == [-74.0, 40.7]
    get.assert_called_once()
    assert get.call_args.kwargs["params"]["geometries"] == "geojson"
    assert get.call_args.kwargs["timeout"] > 0


def test_routing_provider_error_is_wrapped():
    with patch(
        "routing.services.routing_service.requests.get",
        side_effect=__import__("requests").Timeout("timeout"),
    ):
        with pytest.raises(RoutingProviderError, match="Routing provider unavailable"):
            RoutingService().get_route((40.7, -74.0), (41.0, -73.0))


def test_route_projection_scales_geometry_progress_to_provider_distance():
    geometry = RouteGeometryService([[-74.0, 40.0], [-73.0, 40.0]], route_distance_miles=100)

    midpoint = geometry.project(-73.5, 40.0)
    near_route = geometry.project(-73.5, 40.1)

    assert geometry.total_distance_miles == pytest.approx(100)
    assert midpoint.distance_from_start_miles == pytest.approx(50, abs=0.01)
    assert midpoint.distance_from_route_miles == pytest.approx(0, abs=0.01)
    assert near_route.distance_from_route_miles > midpoint.distance_from_route_miles