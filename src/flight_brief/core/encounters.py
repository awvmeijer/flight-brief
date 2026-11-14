"""Which hazards the remaining route crosses, in space, altitude and time.

This is the deterministic core of the project: every encounter is computed here, not by the
model. The model may add concerns later, but it cannot remove one this function returns.

Conventions, chosen to fail safe:
- a hazard with no base starts at the surface; one with no top has no upper limit;
- touching a hazard's edge or corner counts as an encounter, with zero distance inside;
- hazard areas are static over their validity (SIGMET movement is not extrapolated yet).

Copied from oceanic-flight-watch (`src/oceanic_watch/geo/encounters.py` at commit 962e41a, MIT,
Copyright (c) 2026 Anthony Meijer). Changed: the flight type is `PassengerFlight`, and the
passenger tests at the end of the file are new (`expired_on_arrival`, `near_airports`).
"""

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from .models import Encounter, Hazard, PassengerFlight
from .route import TimedPoint, ahead, timeline, unwrap


def _coords(geom: BaseGeometry) -> list[tuple[float, ...]]:
    """Every vertex of a line-with-area intersection: points, lines or a collection of both."""
    if hasattr(geom, "geoms"):
        return [c for part in geom.geoms for c in _coords(part)]
    return list(geom.coords)


def _time_along(
    line: LineString, path: list[TimedPoint], cum: list[float], c: tuple[float, ...]
) -> datetime:
    """The time the flight passes the point of `line` nearest to c."""
    d = line.project(Point(c))
    i = min(max(bisect_right(cum, d), 1), len(cum) - 1)
    a, b = path[i - 1], path[i]
    seg = cum[i] - cum[i - 1]
    f = 0.0 if seg == 0 else (d - cum[i - 1]) / seg
    return a.t + (b.t - a.t) * f


def crossing(path: list[TimedPoint], hazard: Hazard) -> tuple[datetime, datetime] | None:
    """When the path is first and last inside any of the hazard's rings, or None.

    Each ring is unwrapped like the route, then tried at -360, 0 and +360 degrees, so a ring
    written either side of the antimeridian lines up with the route's continuous longitudes.
    """
    line = LineString([(p.lon, p.lat) for p in path])
    cum = [0.0]
    for (x1, y1), (x2, y2) in zip(line.coords, line.coords[1:], strict=False):
        cum.append(cum[-1] + ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    times: list[datetime] = []
    for ring in hazard.rings:
        lats = [lat for _, lat in ring]
        lons = unwrap([lon for lon, _ in ring])
        for shift in (-360, 0, 360):
            area = make_valid(Polygon([(x + shift, y) for x, y in zip(lons, lats, strict=True)]))
            inside = line.intersection(area)
            if not inside.is_empty:
                times += [_time_along(line, path, cum, c) for c in _coords(inside)]
    return (min(times), max(times)) if times else None


def encounters(
    flight: PassengerFlight, hazards: list[Hazard], now: datetime, lookahead_h: float = 4.0
) -> list[Encounter]:
    """Every hazard the flight meets within `lookahead_h` hours, earliest first."""
    path = ahead(timeline(flight), now, timedelta(hours=lookahead_h))
    if path[0].t == path[-1].t:
        return []  # landed, nothing ahead
    altitude = flight.cruise_fl * 100
    found = []
    for hz in hazards:
        if not hz.covers_level(altitude) or not hz.rings:
            continue
        span = crossing(path, hz)
        if span is None:
            continue
        entry, exit_ = max(span[0], hz.valid_from), min(span[1], hz.valid_to)
        if entry > exit_:
            continue  # the flight is there, but not while the hazard is valid
        found.append(Encounter(
            flight_id=flight.flight_id, hazard_id=hz.hazard_id, kind=hz.kind,
            entry=entry, exit=exit_,
            distance_nm=round((exit_ - entry).total_seconds() / 3600 * flight.groundspeed_kt, 1),
            minutes_to_entry=round((entry - now).total_seconds() / 60, 1),
            flight_level=flight.cruise_fl, hazard_base_ft=hz.base_ft, hazard_top_ft=hz.top_ft,
        ))
    return sorted(found, key=lambda e: e.entry)


def unplaced(hazards: list[Hazard]) -> list[Hazard]:
    """Hazards whose source gave no usable area: shown to the dispatcher as text, never dropped."""
    return [hz for hz in hazards if not hz.rings]


# --- Passenger tests, new in this repo -----------------------------------------------------
#
# `encounters` answers a dispatcher's question: what does this flight meet, at its cruise
# level, while the warning is valid. A passenger brief needs two more answers, and neither may
# be silence:
# - a warning that lies on the route now but ends before the aircraft gets there. The brief
#   says so, because "nothing found" would claim more than the sources know;
# - a warning below cruise level near either airport, which the aircraft climbs or descends
#   through.


class Expired(BaseModel):
    """A warning in force on the route at cruise level that ends before the aircraft arrives."""

    hazard_id: str
    kind: str
    reach: datetime  # when the aircraft would enter the area
    leave: datetime
    valid_to: datetime
    hazard_base_ft: int | None
    hazard_top_ft: int | None


class NearAirport(BaseModel):
    """A warning met within `radius_nm` of an airport at any level from the surface to cruise."""

    end: Literal["origin", "destination"]
    hazard_id: str
    kind: str
    entry: datetime
    exit: datetime
    below_cruise: bool  # the warning tops out under the cruise level
    hazard_base_ft: int | None
    hazard_top_ft: int | None
    valid_to: datetime


def expired_on_arrival(flight: PassengerFlight, hazards: list[Hazard],
                       now: datetime) -> list[Expired]:
    """Warnings valid at `now` that the route crosses at cruise level only after they end."""
    path = timeline(flight)
    altitude = flight.cruise_fl * 100
    found = []
    for hz in hazards:
        if not hz.covers_level(altitude) or not hz.rings or hz.valid_to < now:
            continue
        span = crossing(path, hz)
        if span is not None and span[0] > hz.valid_to:
            found.append(Expired(hazard_id=hz.hazard_id, kind=hz.kind, reach=span[0],
                                 leave=span[1], valid_to=hz.valid_to,
                                 hazard_base_ft=hz.base_ft, hazard_top_ft=hz.top_ft))
    return sorted(found, key=lambda e: e.reach)


def _cut(path: list[TimedPoint], nm0: float, nm1: float) -> list[TimedPoint]:
    """The part of the path between two distances from the origin, with interpolated ends."""
    def at(nm: float) -> TimedPoint:
        for a, b in zip(path, path[1:], strict=False):
            if a.nm <= nm <= b.nm and b.nm > a.nm:
                f = (nm - a.nm) / (b.nm - a.nm)
                return TimedPoint(a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon),
                                  a.t + (b.t - a.t) * f, nm)
        return path[0] if nm <= path[0].nm else path[-1]
    return [at(nm0), *[p for p in path if nm0 < p.nm < nm1], at(nm1)]


def near_airports(flight: PassengerFlight, hazards: list[Hazard],
                  radius_nm: float = 150) -> list[NearAirport]:
    """Warnings the aircraft meets while climbing out or descending, tested surface to cruise.

    Within `radius_nm` of either end the aircraft may be at any level up to cruise, so a
    warning counts when any part of it lies at or below the cruise level, and only while it is
    valid. A route shorter than twice the radius is split at its midpoint.
    """
    path = timeline(flight)
    total = path[-1].nm
    reach = min(radius_nm, total / 2)
    if reach <= 0:
        return []
    ends: list[tuple[Literal["origin", "destination"], list[TimedPoint]]] = [
        ("origin", _cut(path, 0.0, reach)), ("destination", _cut(path, total - reach, total))]
    altitude = flight.cruise_fl * 100
    found = []
    for end, part in ends:
        for hz in hazards:
            if (hz.base_ft or 0) > altitude or not hz.rings:
                continue
            span = crossing(part, hz)
            if span is None:
                continue
            entry, exit_ = max(span[0], hz.valid_from), min(span[1], hz.valid_to)
            if entry > exit_:
                continue
            found.append(NearAirport(
                end=end, hazard_id=hz.hazard_id, kind=hz.kind, entry=entry, exit=exit_,
                below_cruise=hz.top_ft is not None and hz.top_ft < altitude,
                hazard_base_ft=hz.base_ft, hazard_top_ft=hz.top_ft, valid_to=hz.valid_to))
    return sorted(found, key=lambda e: e.entry)
