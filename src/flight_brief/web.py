"""`python -m flight_brief.web --port 8000 --snapshots <dir> --as-of <time>`

The page: a small server-rendered site on the standard library (a WSGI app, served by
`wsgiref`), drawn after docs/UI.md.

    GET /                                     the two forms: route and date, or flight number
    GET /r?from=..&to=..&date=..              finds the two airports, redirects to the route
    GET /r/{FROM}/{TO}/{date}                 that day's flights on the route
    GET /go?code=..&date=..&dep=..            redirects to the flight's own address
    GET /f/{code}/{date}/{leg}?dep=HH:MM      the brief, one address per flight and leg
    GET /f/{code}/{date}/{leg}/saved/{id}     a saved brief, drawn again from its record
    GET /airports?q=..                        airport suggestions for the route form, as JSON
    GET /map                                  the world map: turbulence estimate, storm clouds
    GET /map/field/{kind}/{run}/{hour}/{level}.png   one picture for it, drawn from a tile
    GET /static/..                            the app's own style sheets, scripts and fonts

With `--snapshots` the briefs are read offline from that store as known at `--as-of`. Without
it the app is live: `flight_brief.live.fetch` fills the cache directory on request.

Two keys come from the environment: `AERODATABOX_KEY` turns on scheduled flights and
`MAPBOX_TOKEN` the route map. With a map the visitor's browser contacts Mapbox; otherwise
it contacts no other host. The page works without JavaScript except for the map.

Input is checked against strict patterns before anything else happens; nothing typed is
echoed unless it passed. Brief pages are rate-limited per address and are not indexed.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from . import page, worldmap
from .brief import Brief
from .resolve import normalise, resolve
from .forecast.layer import TileSource, default_root
from .schedule import Schedule, Scheduled
from .service import BriefRequest, Problem, request_brief
from .vrs import RouteTable

FLIGHT = re.compile(r"/f/([A-Za-z0-9]{3,8})/(\d{4}-\d{2}-\d{2})/([1-9])")
SAVED = re.compile(r"/f/([A-Z0-9]{3,8})/(\d{4}-\d{2}-\d{2})/([1-9])/saved/"
                   r"(\d{8}T\d{4}Z-[0-9a-f]{8})")
ROUTE = re.compile(r"/r/([A-Za-z0-9]{3})/([A-Za-z0-9]{3})/(\d{4}-\d{2}-\d{2})")
# One picture of the world map: kind, run as YYYYMMDDTHH, forecast hour, flight level (0 for
# storm clouds). Numbers are written one way only; the tile decides which of them exist.
FIELD = re.compile(r"/map/field/(estimate|storm)/(\d{8}T\d{2})/([1-9]\d{0,2})/(0|[1-9]\d{1,2})\.png")
# A place as a passenger types it: letters of any alphabet, digits, and . ' - and spaces.
PLACE = re.compile(r"[^\W_](?:[^\W_]|[ .'\-])*")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?:([+-])(\d{2}):?(\d{2}))?")
PATTERNS = {
    # Wide enough for a schedule's own name for the aircraft, "Boeing 777-300ER".
    "aircraft": re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-]{0,39}"),
    "from": re.compile(r"[A-Za-z0-9]{3,4}"),
    "to": re.compile(r"[A-Za-z0-9]{3,4}"),
    "airline": re.compile(r"[A-Za-z0-9]{2,3}"),
    "leg": re.compile(r"[1-9]"),
    "fl": re.compile(r"\d{2,3}"),
    "gs": re.compile(r"\d{2,4}(\.\d)?"),
}
BOUNDS = {"fl": (10, 600), "gs": (50, 1000)}  # flight level; groundspeed in kt
NAMES = {"aircraft": "aircraft type", "from": "departure airport", "to": "arrival airport",
         "airline": "airline code", "leg": "leg", "fl": "cruise level", "gs": "groundspeed"}
KNOWN = ("code", "date", "leg", *page.PARAMS)
MAX_QUERY = 400  # characters
MAX_VALUE = 16
MAX_LONG = 40  # the aircraft type, and a place typed in the route form
MISSING = {"code": "Enter a flight number", "date": "Enter the departure date",
           "dep": "Enter the departure time"}
NOT_A_DATE = "That is not a date. Use YYYY-MM-DD, for example 2026-11-14"
PLACES = {"from": "the place you are flying from", "to": "the place you are flying to"}

SECURITY = [
    ("Content-Security-Policy",
     # Own files only, plus Mapbox for the route map (its script, style sheet, tiles and the
     # worker it starts from a blob). No inline style or script is allowed.
     "default-src 'none'; style-src 'self' https://api.mapbox.com; "
     "script-src 'self' https://api.mapbox.com; font-src 'self'; "
     "img-src 'self' data: blob:; worker-src blob:; child-src blob:; "
     "connect-src 'self' https://api.mapbox.com https://*.tiles.mapbox.com "
     "https://events.mapbox.com; form-action 'self'; base-uri 'none'; "
     "frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "geolocation=(), camera=(), microphone=(), interest-cohort=()"),
]
NOINDEX = ("X-Robots-Tag", "noindex")
REASONS = {200: "OK", 302: "Found", 303: "See Other", 400: "Bad Request", 404: "Not Found",
           405: "Method Not Allowed", 429: "Too Many Requests", 502: "Bad Gateway",
           503: "Service Unavailable"}

Response = tuple[int, list[tuple[str, str]], str | bytes]
STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".woff2": "font/woff2"}


def static_files(root: Path = STATIC) -> dict[str, Path]:
    """The files the page may serve, by their address under /static/. Listed once, so a
    request can only ever name a file that was there when the app started."""
    return {"/static/" + path.relative_to(root).as_posix(): path
            for path in sorted(root.rglob("*")) if path.is_file() and path.suffix in TYPES}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0)


# --- input ---------------------------------------------------------------------------------

def _clock(text: str) -> str | None:
    """A typed time, HH:MM with an optional UTC offset, or None. A plus sign typed straight
    into an address arrives as a space, so a space before the offset is read as a plus."""
    text = re.sub(r"^(\d{1,2}:\d{2}) (\d{2}:?\d{2})$", r"\1+\2", text.strip())
    match = CLOCK.fullmatch(text)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        return None
    if match[3] and (int(match[4]) > 14 or int(match[5]) > 59):
        return None
    return text


def _date(text: str) -> str | None:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return None
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def validate_route(raw: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The route form's three values, by the same rule as `validate`: the ones that passed,
    and this module's own words for each one that did not."""
    ok: dict[str, str] = {}
    errors: list[str] = []
    for key in ("from", "to", "date"):
        value = " ".join(raw.get(key, "").split())
        if not value:
            errors.append(MISSING["date"] if key == "date" else f"Enter {PLACES[key]}")
        elif key == "date":
            if _date(value):
                ok[key] = value
            else:
                errors.append(NOT_A_DATE)
        elif len(value) > MAX_LONG:
            errors.append("One of the values is too long")
        elif not PLACE.fullmatch(value):
            errors.append(f"Type {PLACES[key]} as a city or an airport code, in letters")
        else:
            ok[key] = value
    return ok, errors


def validate(raw: dict[str, str], need: tuple[str, ...] = ("code", "date", "dep")
             ) -> tuple[dict[str, str], list[str]]:
    """The values that passed, cleaned, and a message for each one that did not. The
    messages are this module's own words: a refused value is never repeated back."""
    ok: dict[str, str] = {}
    errors: list[str] = []
    values = {k: v.strip() for k, v in raw.items() if k in KNOWN and v.strip()}
    for key, value in values.items():
        if len(value) > (MAX_LONG if key == "aircraft" else MAX_VALUE):
            errors.append("One of the values is too long")
            continue
        if key == "code":
            code = normalise(value)
            if code is None:
                errors.append("That does not look like a flight number. Use the airline code "
                              "and number, for example SQ322")
            else:
                ok[key] = str(code)
        elif key == "date":
            if _date(value):
                ok[key] = value
            else:
                errors.append(NOT_A_DATE)
        elif key in ("dep", "arr"):
            clock = _clock(value)
            if clock is None:
                which = "departure" if key == "dep" else "arrival"
                errors.append(f"That {which} time is not a time. Use HH:MM, for example 23:55")
            else:
                ok[key] = clock
        elif not PATTERNS[key].fullmatch(value):
            errors.append(f"That {NAMES[key]} is not in a form this page reads")
        elif key in BOUNDS and not BOUNDS[key][0] <= float(value) <= BOUNDS[key][1]:
            errors.append(f"That {NAMES[key]} is outside {BOUNDS[key][0]} to {BOUNDS[key][1]}")
        else:
            ok[key] = value.upper() if key in ("from", "to", "airline") else value
    if ("from" in values) != ("to" in values):
        errors.append("Enter both airports")
    errors += [MISSING[key] for key in need if key not in values]
    return ok, errors


def _request(values: dict[str, str]) -> BriefRequest:
    return BriefRequest(
        code=values["code"], day=values["date"], dep=values["dep"], arr=values.get("arr"),
        leg=int(values["leg"]) if values.get("leg") else None, airline=values.get("airline"),
        origin=values.get("from"), destination=values.get("to"),
        aircraft=values.get("aircraft"), fl=int(values["fl"]) if values.get("fl") else None,
        gs=float(values["gs"]) if values.get("gs") else None)


# --- saved briefs --------------------------------------------------------------------------

def saved_id(brief: Brief, req: BriefRequest) -> str:
    """The name of a saved brief within its flight and leg: when it was issued, and a short
    hash of what was typed, so two briefs issued in the same minute from different inputs
    (another departure time, typed airports) do not share a file."""
    typed = json.dumps(req.model_dump(exclude_none=True), sort_keys=True)
    return (brief.identity.issued_at.astimezone(UTC).strftime("%Y%m%dT%H%MZ") + "-"
            + hashlib.sha256(typed.encode()).hexdigest()[:8])


def saved_path(root: Path, code: str, day: str, leg: int | str, name: str) -> Path:
    return root / code / day / f"leg{leg}-{name}.json"


def save(root: Path, brief: Brief, req: BriefRequest) -> str:
    """Write the brief record with its inputs, once. Returns the saved brief's name."""
    name = saved_id(brief, req)
    ident = brief.identity
    path = saved_path(root, ident.code, ident.date.isoformat(), ident.leg, name)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(f".{os.getpid()}.part")
        part.write_text(json.dumps({"request": req.model_dump(exclude_none=True),
                                    "brief": brief.record()}, indent=1), encoding="utf-8")
        part.replace(path)
    return name


# --- the app -------------------------------------------------------------------------------

class RateLimit:
    """At most `limit` hits per address in any `window` seconds. Kept in memory: it resets
    when the process restarts and is not shared between processes."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float]) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, address: str) -> bool:
        now = self.clock()
        with self.lock:
            if len(self.hits) > 10_000:  # forget addresses with nothing recent
                self.hits = {a: q for a, q in self.hits.items() if q and now - q[-1] < self.window}
            queue = self.hits.setdefault(address, deque())
            while queue and now - queue[0] >= self.window:
                queue.popleft()
            if len(queue) >= self.limit:
                return False
            queue.append(now)
            return True


def create_app(table: RouteTable, *, snapshots: Path | None, cache: Path, saved: Path | None,
               now: Callable[[], datetime] = utc_now, limit: int = 30, window: float = 600.0,
               clock: Callable[[], float] = time.monotonic,
               behind_proxy: bool = False, tiles: TileSource | None = None,
               schedule: Schedule | None = None, mapbox: str | None = None
               ) -> Callable[..., Iterable[bytes]]:
    """The site as a WSGI app. With `snapshots` it reads that store offline; without, it is
    live and fills `cache` through `flight_brief.live.fetch`. `now` gives the time each brief
    is issued at. `saved`, when set, is where every brief that renders is kept with its inputs.
    `behind_proxy` takes the caller's address from the last X-Forwarded-For entry.
    `schedule`, when set, gives the flights of a route and the time of a flight number.
    `mapbox` is a public Mapbox token for the route map; without one the map window says so."""
    limiter = RateLimit(limit, window, clock)
    files = static_files()
    # A Mapbox token limited to certain sites is checked against the page's origin, so with a
    # map the browser may send the origin, and only the origin: never the flight's address.
    referrer = ("Referrer-Policy", "strict-origin" if mapbox else "no-referrer")
    quiet = (NOINDEX, ("Cache-Control", "no-store"))
    # The world map's pictures. One view of the map asks for many, so drawing them has its own
    # count per address, `worldmap.DRAWS` for each brief the address may ask for.
    fields = worldmap.Fields(tiles)
    drawing = RateLimit(limit * worldmap.DRAWS, window, clock)

    def home(values: dict[str, str] | None = None, **kw: Any) -> str:
        return page.home_page(values, need_time=schedule is None, **kw)

    def html(status: int, body: str, *extra: tuple[str, str]) -> Response:
        return status, [("Content-Type", "text/html; charset=utf-8"), *extra], body

    def redirect(location: str, status: int = 303) -> Response:
        return status, [("Location", location), NOINDEX, ("Cache-Control", "no-store")], ""

    def not_found() -> Response:
        return html(404, page.message_page("Page not found", "There is nothing at this address."),
                    NOINDEX)

    def refused(values: dict[str, str], errors: list[str]) -> Response:
        return html(400, home(values, message=". ".join(errors),
                              airports="Enter both airports" in errors), *quiet)

    def limited() -> Response:
        return html(429, page.message_page(
            "Too many briefs", "Too many briefs were asked for from this address. "
                               f"Try again in {int(window // 60) or 1} minutes."),
            NOINDEX, ("Retry-After", str(int(window))), ("Cache-Control", "no-store"))

    def query(environ: dict[str, Any]) -> dict[str, str] | None:
        text = environ.get("QUERY_STRING", "")
        if len(text) > MAX_QUERY:
            return None
        try:
            fields = parse_qs(text, max_num_fields=20)
        except ValueError:
            return None
        return {k: v[-1] for k, v in fields.items()}

    def find(raw: dict[str, str]) -> Response:
        """The route form's target: two places as typed, each matched to one airport. It
        reads only the route table; the flights are found at the address it redirects to."""
        values, errors = validate_route(raw)
        if errors:
            return html(400, home(route=values, message=". ".join(errors)), *quiet)
        ends = []
        for side in ("from", "to"):
            place = table.best_airport(values[side])
            if place is None:
                matches = table.find_airports(values[side])
                if not matches:
                    return html(200, home(route=values, message=(
                        f"No airport was found for {PLACES[side]}. Try the city's name in "
                        "English or the three-letter airport code")), *quiet)
                return html(200, page.airport_choice_page(side, matches, values), *quiet)
            ends.append(place)
        if ends[0].code == ends[1].code:
            return html(200, home(route=values, message="The two airports are the same"),
                        *quiet)
        return redirect(page.route_url(ends[0].iata, ends[1].iata, values["date"]))

    def route(match: re.Match[str], address: str, errors_to: Any) -> Response:
        """The flights of one route on one day: from the schedule with their times, or else
        the flight numbers the route table has for the pair."""
        day = _date(match[3])
        if day is None:
            return html(400, home(message=NOT_A_DATE), *quiet)
        origin, destination = (table.airport(match[i].upper()) for i in (1, 2))
        if origin is None or destination is None or not (origin.iata and destination.iata):
            return html(404, home(route={"date": day}, message=(
                "One of those airport codes is not known here. Type the city or the "
                "airport code again")), *quiet)
        typed = {"from": origin.iata, "to": destination.iata, "date": day}
        if origin.code == destination.code:
            return html(400, home(route=typed, message="The two airports are the same"),
                        *quiet)
        here = page.route_url(origin.iata, destination.iata, day)
        if match[0] != here:
            return redirect(here, 302)
        if not limiter.allow(address):
            return limited()
        flights: list[Scheduled] | None = None
        if schedule is not None:
            try:
                found = schedule.departures(origin, destination, date.fromisoformat(day))
            except Exception:  # noqa: BLE001  the fallback list is still a page
                traceback.print_exc(file=errors_to)
                found = None
            flights = found if isinstance(found, list) else None
        numbers = [] if flights else table.flights_between(origin.code, destination.code)
        if not flights and not numbers:
            none = ("No flights were found on this route that day" if flights is not None
                    else "No flight numbers are known here for this route")
            return html(200, home({**typed, "from": origin.iata, "to": destination.iata},
                                  airports=True, message=(
                f"{none}. Enter your flight number and the departure time on your booking")),
                        *quiet)
        return html(200, page.route_page(origin, destination, day, flights, numbers), *quiet)

    def timed(values: dict[str, str], f: Scheduled) -> str:
        """The address of the brief for one scheduled departure of a typed flight number.
        What the passenger typed stays; the schedule fills in the times and the aircraft.
        The airports go in only when the route table does not have this leg of the number."""
        out = {**values, "dep": f.dep_local}
        if f.arr_local:
            out.setdefault("arr", f.arr_local)
        if f.aircraft and page.plain_model(f.aircraft):
            out.setdefault("aircraft", page.plain_model(f.aircraft))
        leg = values.get("leg")
        if "from" not in values and leg is None:
            known = resolve(values["code"], table, values.get("airline"))
            index = next((x.index for x in known.legs
                          if f.origin in (x.origin.code, x.origin.iata)
                          and f.destination in (x.destination.code, x.destination.iata)), None)
            if index is not None:
                leg = str(index)
            elif table.airport(f.origin) and table.airport(f.destination):
                out["from"], out["to"] = f.origin, f.destination
        return page.flight_url(values["code"], values["date"], leg or "1", out)

    def look_up(values: dict[str, str], address: str, errors_to: Any) -> Response:
        """A flight number and a date with no departure time: ask the schedule for it."""
        assert schedule is not None
        if not limiter.allow(address):
            return limited()
        try:
            found = schedule.flight(values["code"], date.fromisoformat(values["date"]))
        except Exception:  # noqa: BLE001  the passenger can still type the time
            traceback.print_exc(file=errors_to)
            found = None
        if not isinstance(found, list) or not found:
            return refused(values, [MISSING["dep"]])
        if len(found) == 1:
            return redirect(timed(values, found[0]))
        return html(200, page.departure_choice_page(
            values["code"], values["date"], [(f, timed(values, f)) for f in found]), *quiet)

    def go(raw: dict[str, str], address: str, errors_to: Any) -> Response:
        """The form's target. It only reads the route table, to see whether the number has
        several legs; the brief itself is made at the address it redirects to. With a
        schedule, a number typed without a departure time has its time looked up first."""
        values, errors = validate(raw)
        if errors == [MISSING["dep"]] and schedule is not None:
            return look_up(values, address, errors_to)
        if errors:
            return refused(values, errors)
        if "leg" not in values and "from" not in values:
            found = resolve(values["code"], table, values.get("airline"))
            if found.status == "mapped" and len(found.legs) > 1:
                legs = [{"index": x.index, "origin": x.origin.code,
                         "destination": x.destination.code} for x in found.legs]
                problem = Problem(status="choose_leg", message="", legs=legs)
                return html(200, page.problem_page(problem, values), *quiet)
        return redirect(page.flight_url(values["code"], values["date"],
                                        values.get("leg", "1"), values))

    def flight(match: re.Match[str], raw: dict[str, str], address: str,
               errors_to: Any) -> Response:
        values, errors = validate({**raw, "code": match[1], "date": match[2], "leg": match[3]})
        if errors:
            return refused(values, errors)
        here = page.flight_url(values["code"], values["date"], values["leg"], values)
        if values["code"] != match[1]:
            return redirect(here, 302)  # one address per flight: SQ0231 and sq231 go to SQ231
        if not limiter.allow(address):
            return limited()
        root, fetch = snapshots, None
        if root is None:
            try:
                from . import live
            except ImportError:
                return html(503, page.message_page(
                    "Live data is not installed",
                    "This copy of the page can only read saved data. Start it with a "
                    "snapshot directory."), NOINDEX, ("Cache-Control", "no-store"))
            root, fetch = cache, live.fetch
        req = _request(values)
        try:
            if tiles is not None:
                tiles.errors.clear()
            result = request_brief(req, table, root, now(), fetch, tiles)
        except Exception:  # noqa: BLE001  a source that is down must not show a stack trace
            traceback.print_exc(file=errors_to)
            return html(502, page.message_page(
                "The sources could not be read",
                "Something went wrong while reading the weather sources. Try again in a few "
                "minutes."), NOINDEX, ("Cache-Control", "no-store"))
        if isinstance(result, Problem):
            status = 400 if result.status in ("invalid", "error") else 200
            return html(status, page.problem_page(result, values, need_time=schedule is None),
                        *quiet)
        saved_url = None
        if saved is not None and result.status == "ok":
            ident = result.identity
            saved_url = (f"/f/{ident.code}/{ident.date.isoformat()}/{ident.leg}/saved/"
                         f"{save(saved, result, req)}")
        return html(200, page.render_page(result, saved_url=saved_url, mapbox=mapbox), NOINDEX,
                    ("Cache-Control", "no-store"))

    def replay(match: re.Match[str]) -> Response:
        """A saved brief drawn again from its record. Nothing is fetched or worked out."""
        if saved is None:
            return not_found()
        path = saved_path(saved, match[1], match[2], match[3], match[4])
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
            brief = page.brief_from_record(kept["brief"])
            typed = BriefRequest.model_validate(kept["request"])
        except (OSError, ValueError, KeyError):
            return not_found()
        values = {"dep": typed.dep, "arr": typed.arr, "aircraft": typed.aircraft,
                  "from": typed.origin, "to": typed.destination, "airline": typed.airline,
                  "fl": str(typed.fl) if typed.fl else None,
                  "gs": f"{typed.gs:g}" if typed.gs else None}
        current = page.flight_url(match[1], match[2], match[3],
                                  {k: v for k, v in values.items() if v})
        return html(200, page.render_page(brief, saved=True, current_url=current, mapbox=mapbox), NOINDEX,
                    ("Cache-Control", "max-age=3600"))

    def airports(raw: dict[str, str]) -> Response:
        """Suggestions for the route form, as JSON: airports that fit what is typed so far."""
        text = raw.get("q", "").strip()
        found = (table.find_airports(text, limit=6)
                 if 2 <= len(text) <= MAX_LONG and PLACE.fullmatch(text) else [])
        body = json.dumps([{"iata": a.iata, "city": a.location, "name": a.name} for a in found])
        return 200, [("Content-Type", "application/json; charset=utf-8"), NOINDEX,
                     ("Cache-Control", "max-age=3600")], body

    def caller(environ: dict[str, Any]) -> str:
        """The address a request is counted against."""
        if behind_proxy and environ.get("HTTP_X_FORWARDED_FOR"):
            return environ["HTTP_X_FORWARDED_FOR"].split(",")[-1].strip()[:64]
        return environ.get("REMOTE_ADDR", "") or "unknown"

    def world(address: str) -> Response:
        """The world map, from the newest tiles known now. Counted like a brief."""
        if not limiter.allow(address):
            return limited()
        estimate, storm = fields.newest(now())
        body = worldmap.map_page(estimate, storm, mapbox, now(), forecasts=tiles is not None)
        if estimate is None or not mapbox:
            return html(200, body, *quiet)
        return html(200, body, ("Cache-Control", "max-age=300"))

    def field(match: re.Match[str], address: str) -> Response:
        """One picture of the world map. A picture drawn before is served as it is; drawing
        one is counted against the address."""
        key = (match[1], match[2], int(match[3]), int(match[4]))
        data = fields.cached(*key)
        if data is None:
            if not drawing.allow(address):
                return limited()
            data = fields.draw(*key, now())
        if data is None:
            return not_found()
        return 200, [("Content-Type", "image/png"),
                     ("Cache-Control", "public, max-age=3600")], data

    def respond(environ: dict[str, Any]) -> Response:
        method = environ.get("REQUEST_METHOD", "GET")
        if method not in ("GET", "HEAD"):
            return 405, [("Allow", "GET, HEAD"), ("Content-Type", "text/plain; charset=utf-8")], \
                "Only GET and HEAD\n"
        path = environ.get("PATH_INFO", "") or "/"
        if len(path) > 80:
            return not_found()
        if path == "/":
            text = environ.get("QUERY_STRING", "")
            if text in ("", "airports=1"):
                return html(200, home(airports=bool(text)), ("Cache-Control", "max-age=300"))
            # A link from a route page: the flight-number form with what is known filled in.
            values, _ = validate(query(environ) or {}, need=())
            return html(200, home(values, airports="from" in values and "to" in values), *quiet)
        if path in files:
            return 200, [("Content-Type", TYPES[files[path].suffix]),
                         ("Cache-Control", "max-age=86400")], files[path].read_bytes()
        if path == "/airports":
            return airports(query(environ) or {})
        if path == "/map":
            return world(caller(environ))
        field_match = FIELD.fullmatch(path)
        if field_match:
            return field(field_match, caller(environ))
        saved_match, flight_match = SAVED.fullmatch(path), FLIGHT.fullmatch(path)
        route_match = ROUTE.fullmatch(path)
        if path not in ("/go", "/r") and not (saved_match or flight_match or route_match):
            return not_found()
        if saved_match:
            return replay(saved_match)
        address = caller(environ)
        errors_to = environ.get("wsgi.errors", sys.stderr)
        if route_match:
            return route(route_match, address, errors_to)
        raw = query(environ)
        if raw is None:
            return refused({}, ["That address is too long or has too many parts"])
        if path == "/r":
            return find(raw)
        if path == "/go":
            return go(raw, address, errors_to)
        assert flight_match is not None
        return flight(flight_match, raw, address, errors_to)

    def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        status, headers, body = respond(environ)
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        start_response(f"{status} {REASONS[status]}",
                       [*headers, *SECURITY, referrer, ("Content-Length", str(len(data)))])
        return [] if environ.get("REQUEST_METHOD") == "HEAD" else [data]

    return app


def _as_of(text: str) -> datetime:
    at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brief-web", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--snapshots", type=Path,
                        help="read briefs offline from this snapshot store; without it the "
                             "page is live")
    parser.add_argument("--as-of", help="UTC time every brief is issued at; default now")
    parser.add_argument("--routes", type=Path,
                        default=Path(os.environ.get("FLIGHT_BRIEF_ROUTES", "data/vrs.sqlite")),
                        help="route table built by `python -m flight_brief.vrs`")
    parser.add_argument("--cache", type=Path, default=Path("data/cache"),
                        help="where live data is kept between requests")
    parser.add_argument("--tiles", type=Path,
                        help="folder of forecast tiles. Live, the newest are downloaded there "
                             "(default ~/.cache/flight-brief/tiles); with --snapshots the "
                             "folder is read as it is, and without --tiles the forecast "
                             "lines are left out")
    parser.add_argument("--no-forecast", action="store_true",
                        help="leave the wind-shear index and GTG out")
    parser.add_argument("--saved", type=Path,
                        help="keep every brief that renders, with its inputs, in this directory")
    parser.add_argument("--behind-proxy", action="store_true",
                        help="rate-limit by the last X-Forwarded-For address")
    args = parser.parse_args(argv)

    try:
        table = RouteTable(args.routes)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    fixed = _as_of(args.as_of) if args.as_of else None
    if args.no_forecast or (args.snapshots and not args.tiles):
        tiles = None
    else:
        tiles = TileSource(args.tiles or default_root(), download=args.snapshots is None)
    # The key comes from the environment only. Without one the page works as before: the
    # route table's flight numbers, and the passenger types the departure time.
    key = os.environ.get("AERODATABOX_KEY", "").strip()
    schedule = Schedule(key, args.cache / "schedule") if key else None
    app = create_app(table, snapshots=args.snapshots, cache=args.cache, saved=args.saved,
                     tiles=tiles, schedule=schedule,
                     mapbox=os.environ.get("MAPBOX_TOKEN", "").strip() or None,
                     now=(lambda: fixed) if fixed else utc_now, behind_proxy=args.behind_proxy)
    with make_server(args.host, args.port, app) as server:
        mode = f"snapshots {args.snapshots}" if args.snapshots else "live"
        print(f"Flight Brief on http://{args.host}:{server.server_port}/ ({mode})", flush=True)
        if schedule is not None:
            print(f"Schedules: on, from AeroDataBox ({schedule.units_spent()} of "
                  f"{schedule.monthly_units} units counted this month)", flush=True)
        else:
            print("Schedules: off (no AERODATABOX_KEY in the environment)", flush=True)
        print("Route map: " + ("on, Mapbox" if os.environ.get("MAPBOX_TOKEN", "").strip()
                               else "off (no MAPBOX_TOKEN in the environment)"), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
