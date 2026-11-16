"""One request for a brief, whoever asks: the command line and the page both call this.

It validates the input, resolves the flight, optionally lets a fetcher fill the snapshot
store for this leg, and runs the engine. Nothing upstream is called before the input is valid.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import brief as engine
from .aircraft import lookup
from .brief import Brief, arrival_utc, make_brief, make_flight, to_utc
from .core.models import PassengerFlight
from .resolve import Leg, resolve, typed_leg
from .vrs import RouteTable

# Fills the snapshot store at `root` with what a brief for this leg needs, as known at the
# given time. `flight` carries the route, the take-off time, the cruise level and the speed.
Fetch = Callable[[Path, Leg, PassengerFlight, datetime], None]


class BriefRequest(BaseModel):
    code: str
    day: str  # local departure date at the origin, YYYY-MM-DD
    dep: str  # local departure time, HH:MM or HH:MM+08:00
    arr: str | None = None
    leg: int | None = None
    airline: str | None = None
    origin: str | None = None
    destination: str | None = None
    aircraft: str | None = None
    fl: int | None = None
    gs: float | None = None


class Problem(BaseModel):
    """A request that did not produce a brief. `status` is one of invalid, error, unmapped,
    ambiguous or choose_leg (the resolver's own statuses pass through)."""
    status: str
    message: str
    candidates: list[dict[str, Any]] = []
    legs: list[dict[str, Any]] = []


def request_brief(req: BriefRequest, table: RouteTable, root: Path, as_of: datetime,
                  fetch: Fetch | None = None) -> Brief | Problem:
    try:
        day = date.fromisoformat(req.day)
    except ValueError:
        return Problem(status="invalid", message=f"'{req.day}' is not a date. Use YYYY-MM-DD")

    found = resolve(req.code, table, req.airline)
    if found.status == "invalid":
        return Problem(status="invalid", message=found.message)
    callsign = found.callsign
    leg: Leg
    if req.origin or req.destination:
        if not (req.origin and req.destination):
            return Problem(status="invalid", message="Give both airports: --from and --to")
        typed = typed_leg(table, req.origin, req.destination)
        if isinstance(typed, str):
            return Problem(status="invalid", message=typed)
        leg, callsign = typed, None
    elif found.status != "mapped":
        return Problem(status=found.status, message=found.message,
                       candidates=[c.model_dump() for c in found.candidates])
    elif len(found.legs) > 1 and req.leg is None:
        legs = "; ".join(f"leg {x.index}: {x.origin.code} to {x.destination.code}"
                         for x in found.legs)
        return Problem(status="choose_leg",
                       message=f"{found.code} has {len(found.legs)} legs ({legs}). "
                               "Say which with --leg",
                       legs=[{"index": x.index, "origin": x.origin.code,
                              "destination": x.destination.code} for x in found.legs])
    else:
        index = req.leg or 1
        if not 1 <= index <= len(found.legs):
            return Problem(status="invalid", message=f"{found.code} has no leg {index}")
        leg = found.legs[index - 1]

    try:
        departure = to_utc(day, req.dep, leg.origin.tz)
        arrival = arrival_utc(departure, req.arr, leg.destination.tz) if req.arr else None
        assert found.code is not None
        aircraft = lookup(req.aircraft, table) if req.aircraft else None
        if fetch is not None and timedelta(0) < departure - as_of <= timedelta(
                hours=engine.TOO_FAR_H):
            flight = make_flight(found.code, leg, departure, req.fl, req.gs, arrival, aircraft)
            fetch(root, leg, flight, as_of)
        return make_brief(found.code, leg, day, departure, as_of, root, callsign=callsign,
                          cruise_fl=req.fl, groundspeed_kt=req.gs, arrival=arrival,
                          aircraft=aircraft)
    except ValueError as exc:
        return Problem(status="invalid", message=str(exc))
