import logging
import time
from decimal import Decimal
from typing import Any

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.serializers import RouteRequestSerializer
from routing.services.fuel_service import FuelService
from routing.services.geocoding_service import GeocodingService
from routing.services.optimization_service import FuelOptimizationService
from routing.services.provider_errors import (
	GeocodingError,
	NoFeasibleFuelPlanError,
	NonUSLocationError,
	RoutingProviderError,
)
from routing.services.routing_service import (
	METERS_PER_MILE,
	SECONDS_PER_MINUTE,
	RoutingService,
)

logger = logging.getLogger(__name__)
@api_view(["GET"])
def health(request: Any) -> Response:
	return Response({"status": "ok"})


class RouteView(APIView):
	def post(self, request: Any) -> Response:
		started = time.monotonic()
		logger.info("Route request received")
		serializer = RouteRequestSerializer(data=request.data)
		if not serializer.is_valid():
			_field, messages = next(iter(serializer.errors.items()))
			return Response({"error": str(messages[0])}, status=status.HTTP_400_BAD_REQUEST)

		start_text = serializer.validated_data["start"]
		finish_text = serializer.validated_data["finish"]
		geocoder = GeocodingService()
		try:
			start = geocoder.geocode(start_text)
			finish = geocoder.geocode(finish_text)
		except NonUSLocationError:
			return Response(
				{"error": "Both locations must be within the USA."}, status=status.HTTP_400_BAD_REQUEST
			)
		except GeocodingError:
			return Response(
				{"error": "Unable to geocode the supplied location."}, status=status.HTTP_400_BAD_REQUEST
			)

		try:
			route = RoutingService().get_route(
				(start.latitude, start.longitude),
				(finish.latitude, finish.longitude),
			)
		except RoutingProviderError:
			return Response(
				{"error": "Routing provider unavailable."}, status=status.HTTP_502_BAD_GATEWAY
			)

		distance_miles = route.distance_meters / METERS_PER_MILE
		duration_minutes = route.duration_seconds / SECONDS_PER_MINUTE
		mpg = settings.VEHICLE_MPG
		tank_capacity = settings.MAX_RANGE_MILES / mpg
		candidates = FuelService().find_candidates(
			route.coordinates,
			settings.ROUTE_CORRIDOR_MILES,
			distance_miles,
		)
		try:
			plan = FuelOptimizationService().optimize(
				distance_miles,
				candidates,
				max_range_miles=settings.MAX_RANGE_MILES,
				mpg=mpg,
				tank_capacity_gallons=tank_capacity,
				initial_fuel_gallons=tank_capacity,
			)
		except NoFeasibleFuelPlanError:
			return Response(
				{
					"error": (
						"No feasible fuel-stop sequence exists within the "
						f"{settings.MAX_RANGE_MILES:g}-mile vehicle range."
					)
				},
				status=status.HTTP_422_UNPROCESSABLE_ENTITY,
			)

		fuel_stops = [self._serialize_stop(stop) for stop in plan.stops]
		logger.info(
			"Route optimized: candidates=%d selected_stops=%d response_seconds=%.3f",
			len(candidates),
			len(fuel_stops),
			time.monotonic() - started,
		)
		return Response(
			{
				"request": {"start": start_text, "finish": finish_text},
				"route": {
					"distance_miles": round(distance_miles, 2),
					"duration_minutes": round(duration_minutes),
					"geometry": route.geometry,
				},
				"vehicle": {
					"max_range_miles": settings.MAX_RANGE_MILES,
					"mpg": mpg,
					"tank_capacity_gallons": tank_capacity,
					"initial_fuel_gallons": tank_capacity,
				},
				"fuel_stops": fuel_stops,
				"fuel_summary": {
					"total_distance_miles": round(distance_miles, 2),
					"total_fuel_consumed_gallons": round(plan.total_fuel_consumed_gallons, 3),
					"initial_fuel_gallons": tank_capacity,
					"total_fuel_purchased_gallons": round(plan.total_fuel_purchased_gallons, 3),
					"ending_fuel_gallons": round(plan.ending_fuel_gallons, 3),
					"total_fuel_cost": float(plan.total_fuel_cost.quantize(Decimal("0.01"))),
				},
			}
		)

	@staticmethod
	def _serialize_stop(stop: Any) -> dict[str, Any]:
		station = stop.candidate.station
		price = Decimal(station.retail_price)
		return {
			"station_id": station.opis_truckstop_id,
			"station_record_id": station.pk,
			"opis_truckstop_id": station.opis_truckstop_id,
			"station_name": station.truckstop_name,
			"city": station.city,
			"state": station.state,
			"latitude": float(station.latitude),
			"longitude": float(station.longitude),
			"location_precision": station.location_precision,
			"price_per_gallon": float(price),
			"distance_from_start_miles": round(stop.candidate.distance_from_start_miles, 2),
			"distance_from_route_miles": round(stop.candidate.distance_from_route_miles, 2),
			"gallons_purchased": round(stop.gallons_purchased, 3),
			"fuel_cost": float(stop.fuel_cost.quantize(Decimal("0.01"))),
			"selection_reason": {
				"price": float(price),
				"distance_from_previous_stop_miles": round(stop.distance_from_previous_stop_miles, 2),
				"within_vehicle_range": True,
				"decision": stop.selection_reason,
			},
		}
