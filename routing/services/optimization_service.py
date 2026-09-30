import logging
import time
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal

from routing.services.fuel_service import FuelCandidate
from routing.services.provider_errors import NoFeasibleFuelPlanError

logger = logging.getLogger(__name__)
FUEL_EPSILON = 1e-9


@dataclass(frozen=True)
class PlannedFuelStop:
    candidate: FuelCandidate
    distance_from_previous_stop_miles: float
    gallons_purchased: float
    fuel_cost: Decimal
    selection_reason: str


@dataclass(frozen=True)
class FuelPlan:
    stops: list[PlannedFuelStop]
    total_fuel_consumed_gallons: float
    total_fuel_purchased_gallons: float
    total_fuel_cost: Decimal
    ending_fuel_gallons: float


class FuelOptimizationService:
    """Minimize additional fuel cost with the standard gas-station greedy rule.

    On a route ordered by progress, buying enough to reach the nearest cheaper
    station is optimal whenever one is reachable on a full tank. If none is
    reachable, filling maximizes the distance before another purchase. The
    opening tank is sunk-cost fuel, so the first purchase is made at the
    cheapest candidate reachable on that initial fuel.
    """

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
        if route_distance_miles < 0 or max_range_miles <= 0 or mpg <= 0 or tank_capacity_gallons <= 0:
            raise ValueError("Route distance and vehicle fuel parameters must be positive.")
        if max_range_miles > tank_capacity_gallons * mpg + FUEL_EPSILON:
            raise ValueError("Maximum range cannot exceed tank capacity multiplied by MPG.")
        if not 0 <= initial_fuel_gallons <= tank_capacity_gallons:
            raise ValueError("Initial fuel must fit within tank capacity.")

        ordered_candidates = sorted(
            (
                candidate
                for candidate in candidates
                if 0 <= candidate.distance_from_start_miles <= route_distance_miles
            ),
            key=lambda item: (item.distance_from_start_miles, item.station.pk),
        )
        ordered: list[FuelCandidate] = []
        for candidate in ordered_candidates:
            same_position = (
                ordered
                and candidate.distance_from_start_miles - ordered[-1].distance_from_start_miles
                <= FUEL_EPSILON
            )
            if same_position:
                existing = ordered[-1]
                if (Decimal(candidate.station.retail_price), candidate.station.pk) < (
                    Decimal(existing.station.retail_price),
                    existing.station.pk,
                ):
                    ordered[-1] = candidate
            else:
                ordered.append(candidate)

        positions = [candidate.distance_from_start_miles for candidate in ordered]
        prices = [Decimal(candidate.station.retail_price) for candidate in ordered]
        next_cheaper = [-1] * len(ordered)
        cheaper_stack: list[int] = []
        for index in range(len(ordered) - 1, -1, -1):
            while cheaper_stack and prices[cheaper_stack[-1]] >= prices[index]:
                cheaper_stack.pop()
            if cheaper_stack:
                next_cheaper[index] = cheaper_stack[-1]
            cheaper_stack.append(index)

        remaining_fuel = initial_fuel_gallons
        current_position = 0.0
        previous_stop_position = 0.0
        stops: list[PlannedFuelStop] = []
        total_cost = Decimal("0.00")

        if route_distance_miles > min(initial_fuel_gallons * mpg, max_range_miles) + FUEL_EPSILON:
            initial_reach_index = bisect_right(
                positions,
                min(initial_fuel_gallons * mpg, max_range_miles) + FUEL_EPSILON,
            )
            if initial_reach_index == 0:
                raise NoFeasibleFuelPlanError
            current_index = min(
                range(initial_reach_index),
                key=lambda index: (prices[index], -positions[index], ordered[index].station.pk),
            )
            current_position = positions[current_index]
            remaining_fuel = max(0.0, initial_fuel_gallons - current_position / mpg)
        else:
            current_index = -1

        while True:
            distance_to_finish = route_distance_miles - current_position
            if distance_to_finish <= min(remaining_fuel * mpg, max_range_miles) + FUEL_EPSILON:
                remaining_fuel = max(0.0, remaining_fuel - distance_to_finish / mpg)
                current_position = route_distance_miles
                break

            if current_index < 0:
                raise NoFeasibleFuelPlanError

            station = ordered[current_index]
            cheaper_index = next_cheaper[current_index]
            furthest_reachable_index = bisect_right(
                positions,
                current_position + max_range_miles + FUEL_EPSILON,
            ) - 1
            if 0 <= cheaper_index <= furthest_reachable_index:
                target_index = cheaper_index
                target_position = positions[target_index]
                gallons_to_buy = max(0.0, (target_position - current_position) / mpg - remaining_fuel)
                reason = "Purchased only enough fuel to reach the next cheaper reachable station."
                finish_after_purchase = False
            elif distance_to_finish <= max_range_miles + FUEL_EPSILON:
                gallons_to_buy = max(0.0, distance_to_finish / mpg - remaining_fuel)
                reason = "Destination is within one tank; purchased only the fuel needed to finish."
                finish_after_purchase = True
                target_index = -1
            else:
                gallons_to_buy = max(0.0, tank_capacity_gallons - remaining_fuel)
                reason = (
                    "No cheaper station is reachable within one tank; "
                    "purchased fuel to maximize useful range."
                )
                finish_after_purchase = False
                target_index = furthest_reachable_index
                if target_index <= current_index:
                    raise NoFeasibleFuelPlanError

            gallons_to_buy = min(gallons_to_buy, tank_capacity_gallons - remaining_fuel)
            if gallons_to_buy > FUEL_EPSILON:
                cost = Decimal(str(gallons_to_buy)) * prices[current_index]
                total_cost += cost
                stops.append(
                    PlannedFuelStop(
                        candidate=station,
                        distance_from_previous_stop_miles=current_position - previous_stop_position,
                        gallons_purchased=gallons_to_buy,
                        fuel_cost=cost,
                        on_reason=selectireason,
                    )
                )
                previous_stop_position = current_position
                remaining_fuel += gallons_to_buy

            if finish_after_purchase:
                remaining_fuel = max(0.0, remaining_fuel - distance_to_finish / mpg)
                current_position = route_distance_miles
                break

            if target_index < 0:
                target_index = bisect_right(
                    positions,
                    current_position + remaining_fuel * mpg + FUEL_EPSILON,
                ) - 1
                if target_index <= current_index:
                    raise NoFeasibleFuelPlanError

            leg = positions[target_index] - current_position
            if leg > remaining_fuel * mpg + FUEL_EPSILON:
                raise NoFeasibleFuelPlanError
            remaining_fuel = max(0.0, remaining_fuel - leg / mpg)
            current_position = positions[target_index]
            current_index = target_index

        total_consumed = route_distance_miles / mpg
        total_purchased = sum(stop.gallons_purchased for stop in stops)
        ending_fuel = remaining_fuel
        logger.info(
            "Fuel optimization completed in %.3fs with %d paid stops",
            time.monotonic() - started,
            len(stops),
        )
        return FuelPlan(stops, total_consumed, total_purchased, total_cost, ending_fuel)