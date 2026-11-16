"""`brief SQ322 2026-11-14 --dep 23:55`

Prints the brief for one leg from a local route table. Without `--snapshots` the brief is
live: what it needs is fetched now from NOAA's Aviation Weather Center and kept for 30 minutes
in the `--cache` directory. With `--snapshots <dir>` it is read offline from that snapshot
store, as known at `--as-of`. `--json` prints the brief record instead of text.
"""

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import live
from .render import render
from .service import BriefRequest, Problem, request_brief
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


def default_cache() -> Path:
    return Path(os.environ.get("FLIGHT_BRIEF_CACHE")
                or Path.home() / ".cache" / "flight-brief" / "live")


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
    parser.add_argument("--snapshots", type=Path,
                        help="read offline from this snapshot store instead of fetching")
    parser.add_argument("--as-of", help="UTC time the brief is issued at, with --snapshots; "
                                        "default now")
    parser.add_argument("--cache", type=Path, default=default_cache(),
                        help="where a live brief keeps what it fetched (default "
                             "~/.cache/flight-brief/live, or $FLIGHT_BRIEF_CACHE)")
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

    if args.snapshots is None and args.as_of:
        return _fail(args, "invalid", "--as-of needs --snapshots: a live brief is always "
                                      "issued now")
    if os.environ.get("FLIGHT_BRIEF_DEBUG"):  # one line on stderr per request or cache hit
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    try:
        table = RouteTable(args.routes)
    except FileNotFoundError as exc:
        return _fail(args, "error", str(exc))
    req = BriefRequest(code=args.code, day=args.date, dep=args.dep, arr=args.arr, leg=args.leg,
                       airline=args.airline, origin=args.origin, destination=args.destination,
                       aircraft=args.aircraft, fl=args.fl, gs=args.gs)
    if args.snapshots is not None:
        brief = request_brief(req, table, args.snapshots, _as_of(args.as_of))
    else:
        try:
            args.cache.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _fail(args, "error", f"Cannot use the cache directory {args.cache}: {exc}")
        brief = request_brief(req, table, args.cache, _as_of(None), fetch=live.fetch)
    if isinstance(brief, Problem):
        extra = {"candidates": brief.candidates} if brief.status not in (
            "invalid", "error", "choose_leg") else {}
        return _fail(args, brief.status, brief.message, **extra)
    print(json.dumps(brief.record(), indent=2) if args.json else render(brief))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
