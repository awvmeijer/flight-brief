"""Flight schedules from AeroDataBox: which flights leave an airport on a day, and when.

The route table has no times and no days, so without this the passenger types the departure
time. With a key, two questions are asked of AeroDataBox (https://aerodatabox.com/, through
the RapidAPI gateway):

- `departures`: every passenger departure from one airport on one local day, with the
  airport each one goes to. Their airport board endpoint, in two calls of twelve hours.
- `flight`: the departures of one flight number on one local day.

Each call costs 2 units of a small monthly allowance (both endpoints are "TIER 2"), so the
module is careful with them:

- a whole airport-day is kept in one file, whatever destination was asked for, and a flight
  number's day in another. A file younger than `TTL` answers without a call;
- a ledger file per calendar month counts the units of every call sent, answered or not, and
  no call is sent that would take the month over `monthly_units - margin`;
- one plain call per request, ten seconds at most, no retries.

Their terms (5.5) allow keeping their data for seven days: every write deletes the files
older than that. A call that fails never raises: the caller gets `Unavailable` with a reason
and falls back to what the route table knows.

Built against their published OpenAPI description. Two things in it shape the parsing: with
`withLeg=true` the airport that was asked about is left out of its own side of each flight
(a departure from Amsterdam has no `departure.airport`), and any field but the flight number
may be missing. The times are read as written, "2026-11-20 12:15+01:00" or with a `T`.
"""

import json
import os
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlencode

from pydantic import BaseModel

from .resolve import normalise
from .vrs import Airport

HOST = "aerodatabox.p.rapidapi.com"
BASE = f"https://{HOST}"
NAME = "AeroDataBox"
SITE = "https://aerodatabox.com/"
USER_AGENT = "flight-brief/0.2"
TIMEOUT_S = 10.0
TTL = timedelta(hours=6)
KEEP = timedelta(days=7)  # their terms, 5.5: no longer than this
UNITS = 2  # one call to either endpoint used here
BOARD = {"direction": "Departure", "withLeg": "true", "withCancelled": "false",
         "withCodeshared": "false", "withCargo": "false", "withPrivate": "false"}
# The local day in two calls: how long a span one call may cover depends on the plan.
HALVES = (("00:00", "11:59"), ("12:00", "23:59"))
GONE = ("Canceled", "CanceledUncertain")
LOCAL = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")

# (address, headers) -> (HTTP status, body). Raises OSError when there was no answer.
Get = Callable[[str, dict[str, str]], tuple[int, str]]
Reason = Literal["no_key", "quota", "failed", "not_found"]


class Scheduled(BaseModel):
    """One departure as the schedule has it."""
    number: str  # normalised, KL791
    airline: str  # the airline's name; empty when the source gave none
    callsign: str | None = None
    dep_local: str  # HH:MM at the origin
    dep_utc: datetime
    arr_local: str | None = None  # HH:MM at the destination
    arr_utc: datetime | None = None
    aircraft: str | None = None  # the source's own name for it, "Boeing 777-300ER"
    origin: str  # ICAO code, or the IATA code when the source gave no ICAO code
    destination: str
    day: str  # the local departure date at the origin, YYYY-MM-DD


class Unavailable(BaseModel):
    """Why there is no schedule: `no_key`, no key was given; `quota`, the month's units are
    used up, here or at the gateway; `failed`, the call did not give an answer that could be
    read; `not_found`, the source does not know the flight number on that day."""
    reason: Reason


def http_get(url: str, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def utc_now() -> datetime:
    return datetime.now(UTC)


# --- reading a reply -----------------------------------------------------------------------

def _at(text: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(text).strip().replace(" ", "T", 1))
    except ValueError:
        return None
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


def _code(airport: Any) -> str | None:
    if not isinstance(airport, dict):
        return None
    code = airport.get("icao") or airport.get("iata")
    return str(code).upper() if code else None


def _block(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def parse(item: Any, origin: str | None = None) -> Scheduled | None:
    """One flight of a reply, or None when it is not a passenger departure this page can
    brief: no flight number that reads as one, no scheduled departure time, no destination,
    a cargo flight, a cancelled one, or a second number for a flight another airline
    operates. `origin` is the airport that was asked about, which a board leaves out."""
    if not isinstance(item, dict):
        return None
    code = normalise(str(item.get("number") or ""))
    if code is None or item.get("isCargo") is True or item.get("status") in GONE \
            or item.get("codeshareStatus") == "IsCodeshared":
        return None
    # Without `withLeg` a board has one `movement` per flight: the time at the airport asked
    # about, and the airport at the other end.
    moved = _block(item, "movement")
    dep = _block(item, "departure") or moved
    arr = _block(item, "arrival") or ({"airport": moved.get("airport")} if moved else {})
    times = _block(dep, "scheduledTime")
    dep_utc, local = _at(times.get("utc")), LOCAL.search(str(times.get("local") or ""))
    start = (_code(dep.get("airport")) if "departure" in item else None) or origin
    end = _code(arr.get("airport"))
    if dep_utc is None or local is None or not start or not end or start == end:
        return None
    landing = _block(arr, "scheduledTime")
    arr_utc, arr_local = _at(landing.get("utc")), LOCAL.search(str(landing.get("local") or ""))
    if arr_utc is None or arr_local is None or arr_utc <= dep_utc:
        arr_utc = arr_local = None
    call, model = item.get("callSign"), _block(item, "aircraft").get("model")
    return Scheduled(
        number=str(code), airline=str(_block(item, "airline").get("name") or "").strip(),
        callsign=str(call).strip().upper() if call else None,
        dep_local=local[2], dep_utc=dep_utc, arr_local=arr_local[2] if arr_local else None,
        arr_utc=arr_utc, aircraft=" ".join(str(model).split())[:80] if model else None,
        origin=start.upper(), destination=end, day=local[1])


def _tidy(found: list[Scheduled]) -> list[Scheduled]:
    """By departure time, each flight once."""
    seen: dict[tuple[str, datetime, str], Scheduled] = {}
    for f in found:
        seen.setdefault((f.number, f.dep_utc, f.destination), f)
    return sorted(seen.values(), key=lambda f: (f.dep_utc, f.number))


# --- the schedule --------------------------------------------------------------------------

class Schedule:
    """Schedules read through `get`, kept in the folder `root`. `key` is the RapidAPI key;
    without one every question is answered `Unavailable("no_key")` and nothing is sent."""

    def __init__(self, key: str | None, root: Path, get: Get | None = None,
                 now: Callable[[], datetime] = utc_now, monthly_units: int = 400,
                 margin: int = 20) -> None:
        self.key, self.root, self.get, self.now = key, root, get or http_get, now
        self.monthly_units, self.margin = monthly_units, margin
        self.lock = threading.Lock()

    # the ledger

    def _ledger(self) -> Path:
        return self.root / f"units-{self.now():%Y-%m}.json"

    def units_spent(self) -> int:
        """Units counted in this calendar month (UTC)."""
        try:
            return int(json.loads(self._ledger().read_text(encoding="utf-8"))["spent"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    def units_left(self) -> int:
        """Units that may still be spent this month before the margin is reached."""
        return max(0, self.monthly_units - self.margin - self.units_spent())

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(f".{os.getpid()}.part")
        part.write_text(json.dumps(record, indent=1), encoding="utf-8")
        part.replace(path)

    def _call(self, path: str, params: dict[str, str]) -> list[Any] | dict[str, Any] | Reason:
        """One call, counted before it is sent. The reply's JSON, an empty list when the
        source answers that it has nothing, or the reason there is no answer."""
        self._write(self._ledger(), {"spent": self.units_spent() + UNITS})
        headers = {"X-RapidAPI-Key": self.key or "", "X-RapidAPI-Host": HOST,
                   "Accept": "application/json", "User-Agent": USER_AGENT}
        try:
            status, body = self.get(f"{BASE}{path}?{urlencode(params)}", headers)
        except (OSError, ValueError):
            return "failed"
        if status == 204:
            return []
        if status == 429:
            return "quota"
        if status == 404:
            return "not_found"
        if status != 200:
            return "failed"
        try:
            data = json.loads(body)
        except ValueError:
            return "failed"
        return data if isinstance(data, list | dict) else "failed"

    # the files

    def _kept(self, path: Path) -> list[Scheduled] | None:
        """What a file holds, when it is young enough to answer."""
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            age = self.now() - datetime.fromisoformat(record["fetched_at"])
            if not timedelta(0) <= age < TTL:
                return None
            return [Scheduled.model_validate(f) for f in record["flights"]]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _keep(self, path: Path, found: list[Scheduled]) -> None:
        self._write(path, {"fetched_at": self.now().isoformat(timespec="seconds"),
                           "source": NAME,
                           "flights": [f.model_dump(mode="json", exclude_none=True)
                                       for f in found]})
        self._forget()

    def _forget(self) -> None:
        """Delete every kept schedule older than `KEEP`. A file that cannot be read goes too."""
        for path in self.root.glob("*.json"):
            if path.name.startswith("units-"):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                old = self.now() - datetime.fromisoformat(record["fetched_at"]) >= KEEP
            except (OSError, ValueError, KeyError, TypeError):
                old = True
            if old:
                path.unlink(missing_ok=True)

    # the two questions

    def departures(self, origin: Airport, destination: Airport, day: date
                   ) -> list[Scheduled] | Unavailable:
        """The passenger flights scheduled from `origin` to `destination` on that local day
        at the origin, by departure time. An empty list means the source answered and has
        none. Costs 4 units the first time an airport and day are asked for, then nothing
        for `TTL`, whatever the destination."""
        if not self.key:
            return Unavailable(reason="no_key")
        start = (origin.icao or origin.code).upper()
        path = self.root / f"board-{start}-{day.isoformat()}.json"
        with self.lock:
            found = self._kept(path)
            if found is None:
                if self.units_left() < UNITS * len(HALVES):
                    return Unavailable(reason="quota")
                found = []
                for first, last in HALVES:
                    reply = self._call(f"/flights/airports/icao/{start}/{day}T{first}/"
                                       f"{day}T{last}", BOARD)
                    if isinstance(reply, str):
                        return Unavailable(reason="failed" if reply == "not_found" else reply)
                    rows = reply.get("departures") if isinstance(reply, dict) else reply
                    found += [f for item in rows or [] if (f := parse(item, start))]
                found = _tidy([f for f in found if f.day == day.isoformat()])
                self._keep(path, found)
        ends = {destination.icao.upper(), destination.code.upper(), destination.iata.upper()} - {""}
        return [f for f in found if f.destination in ends]

    def flight(self, code: str, day: date) -> list[Scheduled] | Unavailable:
        """The departures of one flight number on that local departure day, by time: one for
        most numbers, more for a number with several legs. Costs 2 units, then nothing for
        `TTL`."""
        if not self.key:
            return Unavailable(reason="no_key")
        number = normalise(code)
        if number is None:
            return Unavailable(reason="not_found")
        path = self.root / f"number-{number}-{day.isoformat()}.json"
        with self.lock:
            found = self._kept(path)
            if found is None:
                if self.units_left() < UNITS:
                    return Unavailable(reason="quota")
                reply = self._call(f"/flights/number/{quote(str(number))}/{day}",
                                   {"dateLocalRole": "Departure"})
                if reply == "not_found":
                    reply = []
                if isinstance(reply, str):
                    return Unavailable(reason=reply)
                rows = reply if isinstance(reply, list) else []
                found = _tidy([f for item in rows if (f := parse(item))
                               and f.day == day.isoformat()])
                self._keep(path, found)
        return found or Unavailable(reason="not_found")
