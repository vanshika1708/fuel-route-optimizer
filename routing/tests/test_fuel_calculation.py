from decimal import Decimal

import pytest

from routing.services.fuel_service import FuelCandidate
from routing.services.optimization_service import FuelOptimizationService


def fuel_plan(distance_miles, candidates):
    return FuelOptimizationService().optimize(
        distance_miles,
        candidates,
        max_range_miles=500,
        mpg=10,
        tank_capacity_gallons=50,
        initial_fuel_gallons=50,
    )


def test_consumed_fuel_is_distance_divided_by_mpg():
    station = type("Station", (), {"pk": 1, "retail_price": Decimal("3.50")})()
    plan = fuel_plan(790, [FuelCandidate(station, 450, 0)])

    assert plan.total_fuel_consumed_gallons == pytest.approx(79)


def test_first_stop_choice_respects_price_among_reachable_candidates():
    expensive_near = type("Station", (), {"pk": 1, "retail_price": Decimal("5.00")})()
    cheaper_far = type("Station", (), {"pk": 2, "retail_price": Decimal("2.00")})()
    bridge = type("Station", (), {"pk": 3, "retail_price": Decimal("4.00")})()
    plan = fuel_plan(
        1000,
        [
            FuelCandidate(expensive_near, 100, 0),
            FuelCandidate(cheaper_far, 450, 0),
            FuelCandidate(bridge, 900, 0),
        ],
    )

    assert plan.stops[0].candidate.station.pk == 2


def test_initial_fuel_is_consumed_but_never_charged():
    plan = fuel_plan(400, [])

    assert plan.total_fuel_consumed_gallons == 40
    assert plan.total_fuel_purchased_gallons == 0
    assert plan.total_fuel_cost == Decimal("0.00")
    assert plan.ending_fuel_gallons == 10


def test_purchased_fuel_and_cost_are_separate_from_total_consumption():
    station = type("Station", (), {"pk": 1, "retail_price": Decimal("3.50")})()
    plan = fuel_plan(750, [FuelCandidate(station, 450, 0)])

    assert plan.total_fuel_consumed_gallons == 75
    assert plan.total_fuel_purchased_gallons == pytest.approx(25)
    assert plan.total_fuel_cost == Decimal("87.50")