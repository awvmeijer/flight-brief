"""Hand-built flights and hazards for the geometry tests.

Adapted from oceanic-flight-watch (`tests/geo_helpers.py` at commit 962e41a, MIT).
"""

from datetime import UTC, datetime, timedelta

from flight_brief.core.models import Hazard, PassengerFlight, Waypoint

T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)


def flight(points: list[tuple[float, float]], fl: int = 370, kt: float = 480) -> PassengerFlight:
    """A flight departing at T0 along (lat, lon) waypoints."""
    return PassengerFlight(
        flight_id="XX999", origin="AAAA", destination="BBBB", departure=T0,
        cruise_fl=fl, groundspeed_kt=kt,
        route=[Waypoint(name=f"P{i}", lat=lat, lon=lon) for i, (lat, lon) in enumerate(points)],
    )


def box(lon0: float, lon1: float, lat0: float, lat1: float) -> list[tuple[float, float]]:
    return [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1)]  # left open on purpose


def hazard(rings: list[list[tuple[float, float]]], hours: tuple[float, float] = (-1, 24),
           base: int | None = None, top: int | None = 45000, hid: str = "H1",
           kind: str = "TS", raw: str = "") -> Hazard:
    start, end = (T0 + timedelta(hours=h) for h in hours)
    return Hazard(hazard_id=hid, kind=kind, source="test", valid_from=start, valid_to=end,
                  base_ft=base, top_ft=top, rings=rings, raw=raw)
