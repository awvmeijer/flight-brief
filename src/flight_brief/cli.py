"""`brief SQ322 2026-11-14 --dep 23:55 --as-of <time> --snapshots <dir>`

Prints the brief for one leg, read offline from a snapshot directory and a local route table.
`--json` prints the brief record instead of text.
"""

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .aircraft import lookup
from .brief import arrival_utc, make_brief, to_utc
from .render import render
from .resolve import Leg, resolve, typed_leg
from .vrs import RouteTable


def _fail(args: argparse.Namespace, status: str, message: str, **extra: Any) -> int:
    if args.json:
        print(json.dumps({"status": status, "message": message, **extra}, indent=2))
    else:
        print(message, file=sys.stderr)
    return 2


def _as_of(text: str | None) -> datetime:
    if text is None:
        return datetime.now(UTC).replace(second=0, microsecond=0)
    at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brief", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("code", help="flight number, for example SQ322")
    parser.add_argument("date", help="local departure date at the origin, YYYY-MM-DD")
    parser.add_argument("--dep", required=True,
                        help="local departure time HH:MM (or HH:MM+08:00 with a UTC offset)")
    parser.add_argument("--arr", help="local arrival time at the destination, HH:MM (or with a "
                                      "UTC offset); sets the groundspeed. Without it the "
                                      "arrival is an estimate")
    parser.add_argument("--snapshots", type=Path, required=True, help="snapshot store directory")
    parser.add_argument("--as-of", help="UTC time the brief is issued at; default now")
    parser.add_argument("--routes", type=Path,
                        default=Path(os.environ.get("FLIGHT_BRIEF_ROUTES", "data/vrs.sqlite")),
                        help="route table built by `python -m flight_brief.vrs`")
    parser.add_argument("--leg", type=int, help="which leg of a multi-leg number, from 1")
    parser.add_argument("--airline", help="ICAO airline code, when the flight code is shared")
    parser.add_argument("--from", dest="origin", help="origin airport, when the number does not map")
    parser.add_argument("--to", dest="destination", help="destination airport")
    parser.add_argument("--aircraft", help="aircraft type: an ICAO code such as AT72 or A320, "
                                           "or turboprop, jet or piston. Sets the default "
                                           "cruise level and groundspeed; --arr, --gs and "
                                           "--fl each take precedence over it")
    parser.add_argument("--fl", type=int, help="cruise flight level, instead of the default")
    parser.add_argument("--gs", type=float, help="groundspeed in kt, instead of the default")
    parser.add_argument("--json", action="store_true", help="print the brief record")
    args = parser.parse_args(argv)

    try:
        day = date.fromisoformat(args.date)
    except ValueError:
        return _fail(args, "invalid", f"'{args.date}' is not a date. Use YYYY-MM-DD")
    try:
        table = RouteTable(args.routes)
    except FileNotFoundError as exc:
        return _fail(args, "error", str(exc))

    found = resolve(args.code, table, args.airline)
    if found.status == "invalid":
        return _fail(args, "invalid", found.message)
    callsign = found.callsign
    leg: Leg
    if args.origin or args.destination:
        if not (args.origin and args.destination):
            return _fail(args, "invalid", "Give both airports: --from and --to")
        typed = typed_leg(table, args.origin, args.destination)
        if isinstance(typed, str):
            return _fail(args, "invalid", typed)
        leg, callsign = typed, None
    elif found.status != "mapped":
        return _fail(args, found.status, found.message,
                     candidates=[c.model_dump() for c in found.candidates])
    elif len(found.legs) > 1 and args.leg is None:
        legs = "; ".join(f"leg {x.index}: {x.origin.code} to {x.destination.code}"
                         for x in found.legs)
        return _fail(args, "choose_leg", f"{found.code} has {len(found.legs)} legs ({legs}). "
                                         "Say which with --leg")
    else:
        index = args.leg or 1
        if not 1 <= index <= len(found.legs):
            return _fail(args, "invalid", f"{found.code} has no leg {index}")
        leg = found.legs[index - 1]

    try:
        departure = to_utc(day, args.dep, leg.origin.tz)
        arrival = arrival_utc(departure, args.arr, leg.destination.tz) if args.arr else None
        assert found.code is not None
        aircraft = lookup(args.aircraft, table) if args.aircraft else None
        brief = make_brief(found.code, leg, day, departure, _as_of(args.as_of), args.snapshots,
                           callsign=callsign, cruise_fl=args.fl, groundspeed_kt=args.gs,
                           arrival=arrival, aircraft=aircraft)
    except ValueError as exc:
        return _fail(args, "invalid", str(exc))
    print(json.dumps(brief.record(), indent=2) if args.json else render(brief))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
