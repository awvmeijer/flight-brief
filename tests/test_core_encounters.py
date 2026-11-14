from datetime import timedelta

import pytest

from core_helpers import T0, box, flight, hazard
from flight_brief.core.encounters import (
    encounters,
    expired_on_arrival,
    near_airports,
    unplaced,
)

# 20N from 170E to 170W: crosses the antimeridian about 1 h 15 min after departure at 480 kt.
TRANSPACIFIC = flight([(20.0, 170.0), (20.0, -170.0)])


@pytest.mark.parametrize("ring", [
    box(178, 182, 15, 25),  # aviationweather.gov style: unwrapped past 180
    box(178, -178, 15, 25),  # wrapped, straddling the line
    box(-182, -178, 15, 25),  # unwrapped the other way
])
def test_hazard_straddling_the_antimeridian_is_found_in_every_notation(ring: list) -> None:
    (enc,) = encounters(TRANSPACIFIC, [hazard([ring])], now=T0)
    # 4 degrees of longitude at about 20N is close to 225 NM.
    assert 215 < enc.distance_nm < 235
    assert enc.entry < enc.exit
    assert 55 < enc.minutes_to_entry < 75


def test_hazard_entirely_beyond_the_antimeridian_is_found() -> None:
    (enc,) = encounters(TRANSPACIFIC, [hazard([box(-176, -174, 15, 25)])], now=T0)
    assert 105 < enc.distance_nm < 120


def test_hazard_above_the_flight_level_is_not_an_encounter() -> None:
    above = hazard([box(178, 182, 15, 25)], base=41000, top=50000)
    assert encounters(TRANSPACIFIC, [above], now=T0) == []


def test_hazard_below_the_flight_level_is_not_an_encounter() -> None:
    below = hazard([box(178, 182, 15, 25)], base=None, top=30000)
    assert encounters(TRANSPACIFIC, [below], now=T0) == []


def test_unknown_top_is_treated_as_unlimited() -> None:
    assert len(encounters(TRANSPACIFIC, [hazard([box(178, 182, 15, 25)], top=None)], now=T0)) == 1


def test_hazard_that_expires_before_the_flight_arrives_is_not_an_encounter() -> None:
    expired = hazard([box(178, 182, 15, 25)], hours=(-4, 0.5))
    assert encounters(TRANSPACIFIC, [expired], now=T0) == []


def test_hazard_that_starts_mid_crossing_clips_the_entry() -> None:
    later = hazard([box(178, 182, 15, 25)], hours=(1.25, 6))
    (enc,) = encounters(TRANSPACIFIC, [later], now=T0)
    assert enc.entry == T0 + timedelta(hours=1.25)
    assert enc.distance_nm < 200


def test_exact_corner_touch_counts_with_zero_distance() -> None:
    # The route passes exactly through a fix at (0N, 5E), which is the diamond's bottom corner.
    route = flight([(0.0, 0.0), (0.0, 5.0), (0.0, 10.0)])
    diamond = [(5.0, 0.0), (6.0, 1.0), (5.0, 2.0), (4.0, 1.0)]
    (enc,) = encounters(route, [hazard([diamond])], now=T0)
    assert enc.entry == enc.exit
    assert enc.distance_nm == 0


def test_lookahead_limits_what_is_reported() -> None:
    far = hazard([box(-162, -160, 15, 25)])  # about 3 h 45 min to 4 h out at 480 kt
    route = flight([(20.0, 170.0), (20.0, -150.0)])
    assert encounters(route, [far], now=T0, lookahead_h=3) == []
    assert len(encounters(route, [far], now=T0, lookahead_h=6)) == 1


def test_only_the_route_ahead_counts() -> None:
    behind = hazard([box(171, 173, 15, 25)])
    assert len(encounters(TRANSPACIFIC, [behind], now=T0)) == 1
    assert encounters(TRANSPACIFIC, [behind], now=T0 + timedelta(hours=1)) == []


def test_landed_flight_has_no_encounters() -> None:
    assert encounters(TRANSPACIFIC, [hazard([box(178, 182, 15, 25)])],
                      now=T0 + timedelta(hours=10)) == []


def test_any_ring_of_a_multi_area_sigmet_counts_and_bowties_are_repaired() -> None:
    miss = box(160, 161, 40, 41)
    bowtie = [(178.0, 15.0), (182.0, 25.0), (182.0, 15.0), (178.0, 25.0)]  # self-crossing
    (enc,) = encounters(TRANSPACIFIC, [hazard([miss, bowtie])], now=T0)
    assert enc.distance_nm > 0


def test_encounters_are_sorted_by_entry_and_carry_the_levels() -> None:
    first = hazard([box(172, 174, 15, 25)], hid="A")
    second = hazard([box(178, 182, 15, 25)], hid="B")
    found = encounters(TRANSPACIFIC, [second, first], now=T0)
    assert [e.hazard_id for e in found] == ["A", "B"]
    assert found[0].flight_level == 370 and found[0].hazard_top_ft == 45000


def test_hazard_without_geometry_is_unplaced_not_clear() -> None:
    blank = hazard([], hid="NOGEOM")
    assert encounters(TRANSPACIFIC, [blank], now=T0) == []
    assert [h.hazard_id for h in unplaced([blank, hazard([box(1, 2, 1, 2)])])] == ["NOGEOM"]


# --- Passenger tests, new in this repo ---

def test_a_warning_that_ends_before_arrival_is_reported_not_dropped() -> None:
    expired = hazard([box(178, 182, 15, 25)], hours=(-4, 0.5))
    (late,) = expired_on_arrival(TRANSPACIFIC, [expired], now=T0)
    assert late.valid_to == T0 + timedelta(hours=0.5) and late.reach > late.valid_to
    assert expired_on_arrival(TRANSPACIFIC, [expired], now=T0 + timedelta(hours=1)) == []


def test_a_warning_still_valid_on_arrival_is_an_encounter_not_expired() -> None:
    assert expired_on_arrival(TRANSPACIFIC, [hazard([box(178, 182, 15, 25)])], now=T0) == []


def test_low_topped_warning_near_the_origin_is_skipped_at_cruise_and_found_on_the_climb() -> None:
    low = hazard([box(170.5, 171.5, 15, 25)], base=None, top=20000)  # 30 to 85 nm out
    assert encounters(TRANSPACIFIC, [low], now=T0) == []
    (near,) = near_airports(TRANSPACIFIC, [low])
    assert near.end == "origin" and near.below_cruise and near.entry < near.exit


def test_warning_near_the_destination_counts_on_the_descent_and_beyond_150_nm_does_not() -> None:
    by_dest = hazard([box(-171.5, -170.5, 15, 25)], top=20000)
    mid = hazard([box(178, 182, 15, 25)], top=20000)
    found = near_airports(TRANSPACIFIC, [by_dest, mid])
    assert [n.end for n in found] == ["destination"]


def test_warning_based_above_cruise_or_expired_is_not_met_near_the_airport() -> None:
    above = hazard([box(170.5, 171.5, 15, 25)], base=41000, top=50000)
    gone = hazard([box(170.5, 171.5, 15, 25)], hours=(-4, -1))
    assert near_airports(TRANSPACIFIC, [above, gone]) == []


def test_a_cruise_level_warning_near_the_origin_is_listed_and_not_marked_below_cruise() -> None:
    (near,) = near_airports(TRANSPACIFIC, [hazard([box(170.5, 171.5, 15, 25)])])
    assert not near.below_cruise
