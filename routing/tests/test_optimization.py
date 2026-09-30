from decimal import Decimal
from types import SimpleNamespace

import pytest

from routing.services.fuel_service import FuelCandidate
from routing.services.optimization_service import FuelOptimizationService
from routing.services.provider_errors import NoFeasibleFuelPlanError


def candidate(station_id: int, position: float, price: str) -> FuelCandidate:
    station = SimpleNamespace(pk=station_id, retail_price=Decimal(price))
    return FuelCandidate(station, position, 0.0)


def optimize(distance: float, candidates: list[FuelCandidate]):
    return FuelOptimizationService().optimize(
        distance,
        candidates,
        max_range_miles=500,
        mpg=10,
        tank_capacity_gallons=50,
        initial_fuel_gallons=50,
    )


def test_short_route_uses_initial_fuel_without_a_paid_stop():
    plan = optimize(400, [])

    assert plan.stops == []
    assert plan.total_fuel_consumed_gallons == 40
    assert plan.total_fuel_purchased_gallons == 0
    assert plan.total_fuel_cost == Decimal("0.00")
    assert plan.ending_fuel_gallons == 10


def test_one_stop_buys_only_fuel_needed_to_reach_destination():
    plan = optimize(750, [candidate(1, 450, "3.50")])

    assert len(plan.stops) == 1
    assert plan.stops[0].gallons_purchased == pytest.approx(25)
    assert plan.stops[0].fuel_cost == Decimal("87.50")
    assert plan.total_fuel_consumed_gallons == 75
    assert plan.total_fuel_purchased_gallons == pytest.approx(25)


def test_long_route_uses_multiple_feasible_stops():
    plan = optimize(1200, [candidate(1, 450, "3.50"), candidate(2, 900, "4.00")])

    assert [stop.candidate.station.pk for stop in plan.stops] == [1, 2]
    assert [stop.gallons_purchased for stop in plan.stops] == pytest.approx([45, 25])
    assert plan.total_fuel_purchased_gallons == pytest.approx(70)
    assert plan.total_fuel_consumed_gallons == 120
    assert plan.ending_fuel_gallons == pytest.approx(0)


def test_cheapest_station_is_used_only_after_a_reachable_bridge():
    plan = optimize(
        1000,
        [candidate(1, 450, "4.00"), candidate(2, 650, "2.00"), candidate(3, 900, "5.00")],
    )

    assert [stop.candidate.station.pk for stop in plan.stops] == [1, 2]
    assert plan.stops[0].gallons_purchased == pytest.approx(15)
    assert plan.stops[1].gallons_purchased == pytest.approx(35)


def test_no_station_sequence_within_range_raises():
    with pytest.raises(NoFeasibleFuelPlanError):
        optimize(1200, [candidate(1, 400, "3.00"), candidate(2, 950, "2.00")])


def test_vehicle_cannot_reach_a_station_outside_initial_range():
    with pytest.raises(NoFeasibleFuelPlanError):
        optimize(800, [candidate(1, 550, "2.00")])