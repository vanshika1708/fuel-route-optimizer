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


def optimize_with_fuel(distance: float, candidates: list[FuelCandidate], initial_fuel: float):
    return FuelOptimizationService().optimize(
        distance,
        candidates,
        max_range_miles=500,
        mpg=10,
        tank_capacity_gallons=50,
        initial_fuel_gallons=initial_fuel,
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


def test_initial_fuel_is_free_and_destination_reachability_needs_no_stop():
    plan = optimize(400, [])

    assert plan.stops == []
    assert plan.total_fuel_purchased_gallons == 0
    assert plan.total_fuel_cost == Decimal("0.00")


def test_initial_fuel_can_reach_destination_at_exact_range():
    plan = optimize(500, [])

    assert plan.stops == []
    assert plan.ending_fuel_gallons == pytest.approx(0)


def test_exactly_reachable_station_is_allowed():
    plan = optimize(800, [candidate(1, 500, "3.00")])

    assert len(plan.stops) == 1
    assert plan.stops[0].candidate.station.pk == 1


def test_station_just_beyond_maximum_range_is_not_reachable():
    with pytest.raises(NoFeasibleFuelPlanError):
        optimize(800, [candidate(1, 500.01, "1.00")])


def test_initially_unreachable_cheapest_station_is_reached_via_bridge():
    plan = optimize(
        1_100,
        [candidate(1, 450, "4.00"), candidate(2, 951, "1.00"), candidate(3, 900, "5.00")],
    )

    assert [stop.candidate.station.pk for stop in plan.stops] == [1, 3, 2]
    assert plan.stops[0].gallons_purchased == pytest.approx(45)


def test_later_cheaper_station_requires_only_enough_to_reach_it():
    plan = optimize(
        1_000,
        [candidate(1, 450, "4.00"), candidate(2, 650, "2.00"), candidate(3, 900, "5.00")],
    )

    assert [stop.candidate.station.pk for stop in plan.stops] == [1, 2]
    assert plan.stops[0].gallons_purchased == pytest.approx(15)
    assert "next cheaper reachable station" in plan.stops[0].selection_reason


def test_no_cheaper_reachable_station_buys_to_useful_range():
    plan = optimize(1_200, [candidate(1, 450, "3.50"), candidate(2, 900, "4.00")])

    assert plan.stops[0].gallons_purchased == pytest.approx(45)
    assert "No cheaper station" in plan.stops[0].selection_reason


def test_partial_tank_purchase_never_exceeds_remaining_capacity():
    plan = optimize_with_fuel(600, [candidate(1, 100, "3.00")], initial_fuel=40)

    assert len(plan.stops) == 1
    assert plan.stops[0].gallons_purchased == pytest.approx(20)
    assert plan.stops[0].gallons_purchased <= 50 - 20


def test_all_planned_fuel_and_cost_totals_match_individual_stops():
    plan = optimize(
        1_200,
        [candidate(1, 450, "3.501"), candidate(2, 900, "4.002")],
    )

    assert plan.total_fuel_purchased_gallons == pytest.approx(
        sum(stop.gallons_purchased for stop in plan.stops)
    )
    expected_cost = sum(
        (
            Decimal(str(stop.gallons_purchased)) * Decimal(stop.candidate.station.retail_price)
            for stop in plan.stops
        ),
        start=Decimal("0.00"),
    )
    assert plan.total_fuel_cost == expected_cost
    assert all(stop.gallons_purchased >= 0 for stop in plan.stops)
    assert all(stop.gallons_purchased <= 50 for stop in plan.stops)


def test_competing_station_sequence_uses_cheaper_reachable_fuel():
    plan = optimize(
        1_200,
        [
            candidate(1, 450, "5.00"),
            candidate(2, 700, "2.00"),
            candidate(3, 900, "4.00"),
            candidate(4, 1_000, "1.00"),
        ],
    )

    assert [stop.candidate.station.pk for stop in plan.stops] == [1, 2, 4]
    assert plan.total_fuel_cost == Decimal("180.00")


def test_equal_price_candidates_have_deterministic_farthest_initial_choice():
    candidates = [candidate(2, 400, "3.00"), candidate(1, 450, "3.00")]

    first = optimize(800, candidates)
    second = optimize(800, list(reversed(candidates)))

    assert first.stops[0].candidate.station.pk == 1
    assert [stop.candidate.station.pk for stop in first.stops] == [
        stop.candidate.station.pk for stop in second.stops
    ]


def test_same_position_tie_is_resolved_by_station_id():
    plan = optimize(800, [candidate(2, 450, "3.00"), candidate(1, 450, "3.00")])

    assert plan.stops[0].candidate.station.pk == 1


def test_zero_fuel_at_station_never_becomes_negative():
    plan = optimize(750, [candidate(1, 500, "3.00")])

    assert plan.ending_fuel_gallons >= 0
    assert all(stop.gallons_purchased >= 0 for stop in plan.stops)