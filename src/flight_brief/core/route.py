"""A flight's route as a timed polyline: densified great circles, continuous longitudes.

Longitudes are unwrapped so they never jump by 360 degrees: a transpacific route runs from 104
to about 242 instead of flipping from 180 to -180. Geometry against hazards is then done in that
one continuous frame, which is what makes the antimeridian a non-event.

Copied from oceanic-flight-watch (`src/oceanic_watch/geo/route.py` at commit 962e41a, MIT,
Copyright (c) 2026 Anthony Meijer). Changed: the flight type is `PassengerFlight`.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta

from pyproj import Geod

from .models import PassengerFlight, Waypoint

GEOD = Geod(ellps="WGS84")
METRES_PER_NM = 1852.0


@dataclass(frozen=True)
class TimedPoint:
    lat: float
    lon: float  # unwrapped, may lie outside -180..180
    t: datetime
    nm: float  # distance flown from the origin


def unwrap(lons: list[float]) -> list[float]:
    """Shift each longitude by a multiple of 360 so it is within 180 of the one before."""
    out: list[float] = []
    for lon in lons:
        if out:
            lon += 360 * round((out[-1] - lon) / 360)
        out.append(lon)
    return out


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return float(GEOD.inv(lon1, lat1, lon2, lat2)[2]) / METRES_PER_NM


def densify(route: list[Waypoint], step_nm: float = 20) -> list[tuple[float, float, float]]:
    """(lat, lon, cumulative nm) along great circles between waypoints, about step_nm apart."""
    pts = [(route[0].lat, route[0].lon, 0.0)]
    for a, b in zip(route, route[1:], strict=False):
        leg = distance_nm(a.lat, a.lon, b.lat, b.lon)
        inner = GEOD.npts(a.lon, a.lat, b.lon, b.lat, max(0, int(leg // step_nm)))
        for lon, lat in [*inner, (b.lon, b.lat)]:
            prev = pts[-1]
            pts.append((lat, lon, prev[2] + distance_nm(prev[0], prev[1], lat, lon)))
    lons = unwrap([p[1] for p in pts])
    return [(lat, lon, nm) for (lat, _, nm), lon in zip(pts, lons, strict=True)]


def timeline(flight: PassengerFlight, step_nm: float = 20) -> list[TimedPoint]:
    """The whole route with the time the flight reaches each point at constant groundspeed."""
    hours = 1 / flight.groundspeed_kt
    return [
        TimedPoint(lat, lon, flight.departure + timedelta(hours=nm * hours), nm)
        for lat, lon, nm in densify(flight.route, step_nm)
    ]


def _between(a: TimedPoint, b: TimedPoint, at: datetime) -> TimedPoint:
    span = (b.t - a.t).total_seconds()
    f = 0.0 if span == 0 else (at - a.t).total_seconds() / span
    return TimedPoint(a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon), at,
                      a.nm + f * (b.nm - a.nm))


def position_at(points: list[TimedPoint], at: datetime) -> TimedPoint:
    """Where the flight is at `at`, clamped to the origin before departure and the destination
    after arrival."""
    times = [p.t for p in points]
    i = bisect_right(times, at)
    if i == 0:
        return points[0]
    if i == len(points):
        return points[-1]
    return _between(points[i - 1], points[i], at)


def ahead(points: list[TimedPoint], now: datetime, lookahead: timedelta) -> list[TimedPoint]:
    """The part of the route the flight will fly between now and now + lookahead."""
    end = now + lookahead
    inner = [p for p in points if now < p.t < end]
    return [position_at(points, now), *inner, position_at(points, end)]
