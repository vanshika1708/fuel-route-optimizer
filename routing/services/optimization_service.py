import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from routing.services.fuel_service import FuelCandidate
from routing.services.provider_errors import NoFeasibleFuelPlanError

logger = logging.getLogger(__name__)
MONEY_QUANTUM = Decimal("0.01")
FUEL_EPSILON = 1e-9


@dataclass(frozen=True)
class PlannedFuelStop:
    candidate: FuelCandidate
    distance_from_previous_stop_miles: float
    gallons_purchased: float
    fuel_cost: Decimal


@dataclass(frozen=True)
class FuelPlan:
    stops: list[PlannedFuelStop]
    total_fuel_consumed_gallons: float
    total_fuel_purchased_gallons: float
    total_fuel_cost: Decimal
    ending_fuel_gallons: float


class FuelOptimizationService:
    """Choose a deterministic least-cost feasible sequence of route fuel stops."""

    def optimize(
        self,
        route_distance_miles: float,
        candidates: list[FuelCandidate],
        *,
        max_range_miles: float,
        mpg: float,
        tank_capacity_gallons: float,
        initial_fuel_gallons: float,
    ) -> FuelPlan:
        started = time.monotonic()
        if route_distance_miles < 0 or mpg <= 0 or tank_capacity_gallons <= 0:
            raise ValueError("Route distance and vehicle fuel parameters must be positive.")
        if max_range_miles > tank_capacity_gallons * mpg + FUEL_EPSILON:
            raise ValueError("Maximum range cannot exceed tank capacity multiplied by MPG.")
        if not 0 <= initial_fuel_gallons <= tank_capacity_gallons:
            raise ValueError("Initial fuel must fit within tank capacity.")

        ordered = sorted(
            (
                candidate
                for candidate in candidates
                if 0 <= candidate.distance_from_start_miles <= route_distance_miles
            ),
            key=lambda item: (item.distance_from_start_miles, item.station.pk),
        )
        remaining_fuel = initial_fuel_gallons
        current_position = 0.0
        current_station: FuelCandidate | None = None
        previous_stop_position = 0.0
        stops: list[PlannedFuelStop] = []

        while route_distance_miles - current_position > remaining_fuel * mpg + FUEL_EPSILON:
            if current_station is None:
                reachable = [
                    candidate
                    for candidate in ordered
                    if candidate.distance_from_start_miles > current_position + FUEL_EPSILON
                    and candidate.distance_from_start_miles - current_position
                    <= remaining_fuel * mpg + FUEL_EPSILON
                ]
                if not reachable:
                    raise NoFeasibleFuelPlanError
                next_station = min(
                    reachable,
                    key=lambda item: (
                        Decimal(item.station.retail_price),
                        -item.distance_from_start_miles,
                        item.station.pk,
                    ),
                )
                leg = next_station.distance_from_start_miles - current_position
                remaining_fuel -= leg / mpg
                current_position = next_station.distance_from_start_miles
                current_station = next_station
                continue

            station_price = Decimal(current_station.station.retail_price)
            distance_to_finish = route_distance_miles - current_position
            if distance_to_finish <= max_range_miles + FUEL_EPSILON:
                gallons_to_buy = max(0.0, distance_to_finish / mpg - remaining_fuel)
                next_cheaper = None
                finish_after_refuel = True
            else:
                cheaper_stations = [
                    candidate
                    for candidate in ordered
                    if candidate.distance_from_start_miles > current_position + FUEL_EPSILON
                    and candidate.distance_from_start_miles - current_position
                    <= max_range_miles + FUEL_EPSILON
                    and Decimal(candidate.station.retail_price) < station_price
                ]
                next_cheaper = (
                    min(cheaper_stations, key=lambda item: item.distance_from_start_miles)
                    if cheaper_stations
                    else None
                )
                finish_after_refuel = False

            if finish_after_refuel:
                pass
            elif next_cheaper is not None:
                distance_to_target = next_cheaper.distance_from_start_miles - current_position
                target_fuel = distance_to_target / mpg
                gallons_to_buy = max(0.0, target_fuel - remaining_fuel)
            else:
                gallons_to_buy = max(0.0, tank_capacity_gallons - remaining_fuel)

            if gallons_to_buy > FUEL_EPSILON:
                gallons_to_buy = min(gallons_to_buy, tank_capacity_gallons - remaining_fuel)
                cost = (Decimal(str(gallons_to_buy)) * station_price).quantize(MONEY_QUANTUM)
                stops.append(
                    PlannedFuelStop(
                        candidate=current_station,
                        distance_from_previous_stop_miles=current_position - previous_stop_position,
                        gallons_purchased=gallons_to_buy,
                        fuel_cost=cost,
                    )
                )
                previous_stop_position = current_position
                remaining_fuel += gallons_to_buy

            if finish_after_refuel:
                remaining_fuel -= distance_to_finish / mpg
                current_position = route_distance_miles
                break

            if next_cheaper is not None:
                next_station = next_cheaper
            else:
                reachable = [
                    candidate
                    for candidate in ordered
                    if candidate.distance_from_start_miles > current_position + FUEL_EPSILON
                    and candidate.distance_from_start_miles - current_position
                    <= remaining_fuel * mpg + FUEL_EPSILON
                ]
                if not reachable:
                    raise NoFeasibleFuelPlanError
                next_station = max(
                    reachable,
                    key=lambda item: (item.distance_from_start_miles, -item.station.pk),
                )

            leg = next_station.distance_from_start_miles - current_position
            if leg > remaining_fuel * mpg + FUEL_EPSILON:
                raise NoFeasibleFuelPlanError
            remaining_fuel = max(0.0, remaining_fuel - leg / mpg)
            current_position = next_station.distance_from_start_miles
            current_station = next_station

        total_consumed = route_distance_miles / mpg
        total_purchased = sum(stop.gallons_purchased for stop in stops)
        total_cost = sum((stop.fuel_cost for stop in stops), start=Decimal("0.00")).quantize(MONEY_QUANTUM)
        ending_fuel = max(0.0, remaining_fuel - max(0.0, route_distance_miles - current_position) / mpg)
        logger.info(
            "Fuel optimization completed in %.3fs with %d paid stops",
            time.monotonic() - started,
            len(stops),
        )
        return FuelPlan(stops, total_consumed, total_purchased, total_cost, ending_fuel)