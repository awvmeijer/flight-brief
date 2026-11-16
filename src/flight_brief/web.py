"""`python -m flight_brief.web --port 8000 --snapshots <dir> --as-of <time>`

The page: a small server-rendered site on the standard library (a WSGI app, served by
`wsgiref`). No JavaScript, no request to any other host from the browser.

    GET /                                     the form
    GET /go?code=..&date=..&dep=..            redirects to the flight's own address
    GET /f/{code}/{date}/{leg}?dep=HH:MM      the brief, one address per flight and leg
    GET /f/{code}/{date}/{leg}/saved/{id}     a saved brief, drawn again from its record

With `--snapshots` the briefs are read offline from that store as known at `--as-of`. Without
it the app is live: `flight_brief.live.fetch` fills the cache directory on request.

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

from . import page
from .brief import Brief
from .resolve import normalise, resolve
from .service import BriefRequest, Problem, request_brief
from .vrs import RouteTable

FLIGHT = re.compile(r"/f/([A-Za-z0-9]{3,8})/(\d{4}-\d{2}-\d{2})/([1-9])")
SAVED = re.compile(r"/f/([A-Z0-9]{3,8})/(\d{4}-\d{2}-\d{2})/([1-9])/saved/"
                   r"(\d{8}T\d{4}Z-[0-9a-f]{8})")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?:([+-])(\d{2}):?(\d{2}))?")
PATTERNS = {
    "aircraft": re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-]{0,11}"),
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

SECURITY = [
    ("Content-Security-Policy",
     f"default-src 'none'; style-src '{page.CSS_HASH}'; img-src data:; form-action 'self'; "
     "base-uri 'none'; frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "geolocation=(), camera=(), microphone=(), interest-cohort=()"),
]
NOINDEX = ("X-Robots-Tag", "noindex")
REASONS = {200: "OK", 302: "Found", 303: "See Other", 400: "Bad Request", 404: "Not Found",
           405: "Method Not Allowed", 429: "Too Many Requests", 502: "Bad Gateway",
           503: "Service Unavailable"}

Response = tuple[int, list[tuple[str, str]], str]


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


def validate(raw: dict[str, str], need: tuple[str, ...] = ("code", "date", "dep")
             ) -> tuple[dict[str, str], list[str]]:
    """The values that passed, cleaned, and a message for each one that did not. The
    messages are this module's own words: a refused value is never repeated back."""
    ok: dict[str, str] = {}
    errors: list[str] = []
    values = {k: v.strip() for k, v in raw.items() if k in KNOWN and v.strip()}
    for key, value in values.items():
        if len(value) > MAX_VALUE:
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
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
                ok[key] = value
            except ValueError:
                errors.append("That is not a date. Use YYYY-MM-DD, for example 2026-11-14")
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
    missing = {"code": "Enter a flight number", "date": "Enter the departure date",
               "dep": "Enter the departure time"}
    errors += [missing[key] for key in need if key not in values]
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
               behind_proxy: bool = False) -> Callable[..., Iterable[bytes]]:
    """The site as a WSGI app. With `snapshots` it reads that store offline; without, it is
    live and fills `cache` through `flight_brief.live.fetch`. `now` gives the time each brief
    is issued at. `saved`, when set, is where every brief that renders is kept with its inputs.
    `behind_proxy` takes the caller's address from the last X-Forwarded-For entry."""
    limiter = RateLimit(limit, window, clock)

    def html(status: int, body: str, *extra: tuple[str, str]) -> Response:
        return status, [("Content-Type", "text/html; charset=utf-8"), *extra], body

    def redirect(location: str, status: int = 303) -> Response:
        return status, [("Location", location), NOINDEX, ("Cache-Control", "no-store")], ""

    def not_found() -> Response:
        return html(404, page.message_page("Page not found", "There is nothing at this address."),
                    NOINDEX)

    def refused(values: dict[str, str], errors: list[str]) -> Response:
        return html(400, page.home_page(values, message=". ".join(errors),
                                        airports="Enter both airports" in errors),
                    NOINDEX, ("Cache-Control", "no-store"))

    def query(environ: dict[str, Any]) -> dict[str, str] | None:
        text = environ.get("QUERY_STRING", "")
        if len(text) > MAX_QUERY:
            return None
        try:
            fields = parse_qs(text, max_num_fields=20)
        except ValueError:
            return None
        return {k: v[-1] for k, v in fields.items()}

    def go(raw: dict[str, str]) -> Response:
        """The form's target. It only reads the route table, to see whether the number has
        several legs; the brief itself is made at the address it redirects to."""
        values, errors = validate(raw)
        if errors:
            return refused(values, errors)
        if "leg" not in values and "from" not in values:
            found = resolve(values["code"], table, values.get("airline"))
            if found.status == "mapped" and len(found.legs) > 1:
                legs = [{"index": x.index, "origin": x.origin.code,
                         "destination": x.destination.code} for x in found.legs]
                problem = Problem(status="choose_leg", message="", legs=legs)
                return html(200, page.problem_page(problem, values), NOINDEX,
                            ("Cache-Control", "no-store"))
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
            return html(429, page.message_page(
                "Too many briefs", "Too many briefs were asked for from this address. "
                                   f"Try again in {int(window // 60) or 1} minutes."),
                NOINDEX, ("Retry-After", str(int(window))), ("Cache-Control", "no-store"))
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
            result = request_brief(req, table, root, now(), fetch)
        except Exception:  # noqa: BLE001  a source that is down must not show a stack trace
            traceback.print_exc(file=errors_to)
            return html(502, page.message_page(
                "The sources could not be read",
                "Something went wrong while reading the weather sources. Try again in a few "
                "minutes."), NOINDEX, ("Cache-Control", "no-store"))
        if isinstance(result, Problem):
            status = 400 if result.status in ("invalid", "error") else 200
            return html(status, page.problem_page(result, values), NOINDEX,
                        ("Cache-Control", "no-store"))
        saved_url = None
        if saved is not None and result.status == "ok":
            ident = result.identity
            saved_url = (f"/f/{ident.code}/{ident.date.isoformat()}/{ident.leg}/saved/"
                         f"{save(saved, result, req)}")
        return html(200, page.render_page(result, saved_url=saved_url), NOINDEX,
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
        return html(200, page.render_page(brief, saved=True, current_url=current), NOINDEX,
                    ("Cache-Control", "max-age=3600"))

    def respond(environ: dict[str, Any]) -> Response:
        method = environ.get("REQUEST_METHOD", "GET")
        if method not in ("GET", "HEAD"):
            return 405, [("Allow", "GET, HEAD"), ("Content-Type", "text/plain; charset=utf-8")], \
                "Only GET and HEAD\n"
        path = environ.get("PATH_INFO", "") or "/"
        if len(path) > 80:
            return not_found()
        if path == "/":
            airports = environ.get("QUERY_STRING", "") == "airports=1"
            return html(200, page.home_page(airports=airports), ("Cache-Control", "max-age=300"))
        saved_match, flight_match = SAVED.fullmatch(path), FLIGHT.fullmatch(path)
        if path != "/go" and not saved_match and not flight_match:
            return not_found()
        if saved_match:
            return replay(saved_match)
        raw = query(environ)
        if raw is None:
            return refused({}, ["That address is too long or has too many parts"])
        if path == "/go":
            return go(raw)
        assert flight_match is not None
        address = environ.get("REMOTE_ADDR", "") or "unknown"
        if behind_proxy and environ.get("HTTP_X_FORWARDED_FOR"):
            address = environ["HTTP_X_FORWARDED_FOR"].split(",")[-1].strip()[:64]
        return flight(flight_match, raw, address, environ.get("wsgi.errors", sys.stderr))

    def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        status, headers, body = respond(environ)
        data = body.encode("utf-8")
        start_response(f"{status} {REASONS[status]}",
                       [*headers, *SECURITY, ("Content-Length", str(len(data)))])
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
    app = create_app(table, snapshots=args.snapshots, cache=args.cache, saved=args.saved,
                     now=(lambda: fixed) if fixed else utc_now, behind_proxy=args.behind_proxy)
    with make_server(args.host, args.port, app) as server:
        mode = f"snapshots {args.snapshots}" if args.snapshots else "live"
        print(f"Flight Brief on http://{args.host}:{server.server_port}/ ({mode})", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
