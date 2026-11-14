from datetime import timedelta

import pytest

from core_helpers import T0, flight
from flight_brief.core.route import ahead, densify, position_at, timeline, unwrap


def test_unwrap_keeps_longitudes_continuous_across_the_antimeridian() -> None:
    assert unwrap([170, 179, -179, -170]) == [170, 179, 181, 190]
    assert unwrap([-170, 179]) == [-170, -181]


def test_singapore_to_los_angeles_is_about_7620_nm_and_continuous() -> None:
    xx231 = flight([(1.368, 103.982), (33.93817, -118.3866)])  # Changi to Los Angeles
    points = densify(xx231.route)
    assert points[-1][2] == pytest.approx(7622, abs=5)
    steps = [abs(b[1] - a[1]) for a, b in zip(points, points[1:], strict=False)]
    assert max(steps) < 2  # no 360 degree jump anywhere
    assert points[-1][1] == pytest.approx(-118.39 + 360, abs=0.01)


def test_timeline_runs_at_groundspeed() -> None:
    points = timeline(flight([(0.0, 0.0), (0.0, 8.0)], kt=480))  # 480 NM
    assert points[-1].t - T0 == pytest.approx(timedelta(hours=1), abs=timedelta(seconds=30))


def test_position_is_clamped_before_departure_and_after_arrival() -> None:
    points = timeline(flight([(0.0, 0.0), (0.0, 8.0)]))
    assert position_at(points, T0 - timedelta(hours=1)) == points[0]
    assert position_at(points, T0 + timedelta(hours=5)) == points[-1]
    mid = position_at(points, T0 + timedelta(minutes=30))
    assert mid.lon == pytest.approx(4.0, abs=0.05) and mid.nm == pytest.approx(240, abs=1)


def test_ahead_starts_now_and_ends_at_the_lookahead() -> None:
    points = timeline(flight([(0.0, 0.0), (0.0, 20.0)]))
    part = ahead(points, T0 + timedelta(hours=1), timedelta(hours=1))
    assert part[0].t == T0 + timedelta(hours=1) and part[-1].t == T0 + timedelta(hours=2)
    assert part[0].lon == pytest.approx(8.0, abs=0.05)
    assert part[-1].lon == pytest.approx(16.0, abs=0.1)
