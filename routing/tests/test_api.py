from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from routing.services.fuel_service import FuelCandidate
from routing.services.geocoding_service import GeoLocation
from routing.services.provider_errors import NonUSLocationError, RoutingProviderError
from routing.services.routing_service import RouteResult

pytestmark = pytest.mark.django_db


def test_health_endpoint_returns_ok():
    response = APIClient().get("/api/v1/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"finish": "Chicago, IL"}, "Start location is required."),
        ({"start": "New York, NY"}, "Finish location is required."),
        ({"start": " ", "finish": "Chicago, IL"}, "Start location is required."),
        ({"start": "New York, NY", "finish": " "}, "Finish location is required."),
        ({"start": 123, "finish": "Chicago, IL"}, "A valid string is required."),
    ],
)
def test_required_and_blank_locations_return_field_errors(payload, expected):
    response = APIClient().post("/api/v1/route/", payload, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": expected}


def test_malformed_json_returns_clean_error():
    response = APIClient().generic(
        "POST",
        "/api/v1/route/",
        data="{broken",
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json() == {"error": "Malformed request."}


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_route_request_returns_calculated_response():
    route = RouteResult(
        400 * 1609.344,
        6_000,
        {"type": "LineString", "coordinates": [[-74.0, 40.7], [-73.0, 41.0]]},
    )
    location = GeoLocation(40.7, -74.0, "us", "New York, NY, USA")
    with (
        patch("routing.views.GeocodingService.geocode", return_value=location),
        patch("routing.views.RoutingService.get_route", return_value=route) as route_call,
        patch("routing.views.FuelService.find_candidates", return_value=[]),
    ):
        response = APIClient().post(
            "/api/v1/route/",
            {"start": "New York, NY", "finish": "Boston, MA"},
            format="json",
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"]["distance_miles"] == 400
    assert payload["route"]["duration_minutes"] == 100
    assert payload["route"]["geometry"]["type"] == "LineString"
    assert payload["fuel_stops"] == []
    assert payload["fuel_summary"]["total_fuel_consumed_gallons"] == 40
    assert payload["fuel_summary"]["total_fuel_purchased_gallons"] == 0
    assert payload["fuel_summary"]["total_fuel_cost"] == 0
    route_call.assert_called_once()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_new_york_to_chicago_route_response_remains_compatible():
    route = RouteResult(
        790.57 * 1609.344,
        891 * 60,
        {"type": "LineString", "coordinates": [[-74.006, 40.713], [-87.624, 41.876]]},
    )
    station = SimpleNamespace(
        pk=1,
        opis_truckstop_id=101,
        truckstop_name="Test Fuel",
        city="Hubbard",
        state="OH",
        latitude=41.0,
        longitude=-80.5,
        location_precision="city",
        retail_price=Decimal("3.25999999"),
    )
    location = GeoLocation(40.7, -74.0, "us", "US")
    with (
        patch("routing.views.GeocodingService.geocode", return_value=location),
        patch("routing.views.RoutingService.get_route", return_value=route),
        patch(
            "routing.views.FuelService.find_candidates",
            return_value=[FuelCandidate(station, 450, 2.0)],
        ),
    ):
        response = APIClient().post(
            "/api/v1/route/",
            {"start": "New York, NY", "finish": "Chicago, IL"},
            format="json",
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"]["distance_miles"] == pytest.approx(790.57)
    assert payload["route"]["duration_minutes"] == 891
    assert payload["route"]["geometry"]["type"] == "LineString"
    assert len(payload["fuel_stops"]) == 1
    stop = payload["fuel_stops"][0]
    assert stop["location_precision"] == "city"
    assert stop["gallons_purchased"] == pytest.approx(29.057, abs=0.001)
    assert stop["selection_reason"]["decision"]
    assert payload["fuel_summary"]["total_fuel_cost"] == pytest.approx(94.73, abs=0.01)


def test_non_us_locations_return_400():
    with patch("routing.views.GeocodingService.geocode", side_effect=NonUSLocationError):
        response = APIClient().post(
            "/api/v1/route/",
            {"start": "Toronto, Canada", "finish": "Chicago, IL"},
            format="json",
        )

    assert response.status_code == 400
    assert response.json() == {"error": "Both locations must be within the USA."}


def test_routing_provider_failure_returns_502():
    with (
        patch("routing.views.GeocodingService.geocode", return_value=GeoLocation(40, -74, "us", "US")),
        patch("routing.views.RoutingService.get_route", side_effect=RoutingProviderError),
    ):
        response = APIClient().post(
            "/api/v1/route/",
            {"start": "New York, NY", "finish": "Boston, MA"},
            format="json",
        )

    assert response.status_code == 502
    assert response.json() == {"error": "Routing provider unavailable."}


def test_no_feasible_fuel_plan_returns_422():
    route = RouteResult(
        900 * 1609.344,
        10_000,
        {"type": "LineString", "coordinates": [[-74.0, 40.7], [-87.6, 41.9]]},
    )
    with (
        patch("routing.views.GeocodingService.geocode", return_value=GeoLocation(40, -74, "us", "US")),
        patch("routing.views.RoutingService.get_route", return_value=route),
        patch("routing.views.FuelService.find_candidates", return_value=[]),
    ):
        response = APIClient().post(
            "/api/v1/route/",
            {"start": "New York, NY", "finish": "Chicago, IL"},
            format="json",
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": "No feasible fuel-stop sequence exists within the 500-mile vehicle range."
    }