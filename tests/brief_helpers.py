"""Shared pieces for the brief tests: the real fixture store, and hand-built stores."""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from flight_brief import vrs
from flight_brief.brief import Brief, make_brief, to_utc
from flight_brief.core import snapshots
from flight_brief.core.snapshots import Envelope
from flight_brief.resolve import resolve, typed_leg

STORE = Path(__file__).parent / "fixtures" / "snapshots"
SEVERITY = re.compile(r"\b(light|moderate|severe|lgt|mod|sev|heavy|extreme)\b", re.IGNORECASE)

# (code, local date, local time, issued at UTC): real past flights inside the fixture store.
# The times are gate times 20 minutes before a round hour, so that take-off, where hour 1
# starts, falls on the round hour the replays were first worked out for.
SQ231 = ("SQ231", "2026-11-04", "07:40", "2026-11-03T23:00")  # thunderstorm warning, hour 1
UA837 = ("UA837", "2026-11-02", "17:40", "2026-11-03T01:00")  # turbulence warnings, antimeridian
AA273 = ("AA273", "2026-11-13", "21:40", "2026-11-14T03:00")  # US convective SIGMET
UA1175 = ("UA1175", "2026-11-07", "17:40", "2026-11-08T01:00")  # a quiet day


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def brief(table: vrs.RouteTable, code: str, day: str, clock: str, as_of: str | datetime,
          root: Path = STORE, leg: int = 1, airports: tuple[str, str] | None = None,
          **kw: Any) -> Brief:
    found = resolve(code, table)
    if airports:
        one = typed_leg(table, *airports)
        assert not isinstance(one, str)
    else:
        assert found.status == "mapped", found.message
        one = found.legs[leg - 1]
    at = utc(as_of) if isinstance(as_of, str) else as_of
    assert found.code
    return make_brief(found.code, one, date.fromisoformat(day),
                      to_utc(date.fromisoformat(day), clock, one.origin.tz), at, root,
                      callsign=found.callsign, **kw)


def unquoted(text: str) -> str:
    """The text with every double-quoted span removed: what this project wrote itself."""
    return re.sub(r'"[^"]*"', '""', text)


# Where a strength word may stand in a record: the plain answer, which only `ride.py` writes.
RIDE_KEYS = ("ride", "ride_word")


def own_strings(value: Any, key: str = "") -> list[str]:
    """Every string in a brief record that is this project's wording outside the plain
    answer: not a `quoted` field, and not what `ride.py` wrote."""
    if key in RIDE_KEYS:
        return []
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in own_strings(v, k)]
    if isinstance(value, list):
        return [s for v in value for s in own_strings(v, key)]
    return [unquoted(value)] if isinstance(value, str) and key != "quoted" else []


def without_ride(text: str) -> str:
    """A rendered brief without the block under "The ride" (it ends at the next empty line)."""
    out, inside = [], False
    for line in text.splitlines():
        if line == "The ride":
            inside = True
        elif inside and not line.strip():
            inside = False
        if not inside:
            out.append(line)
    return "\n".join(out)


def ring(lon0: float, lon1: float, lat0: float, lat1: float) -> list[dict[str, float]]:
    return [{"lon": lon, "lat": lat} for lon, lat in
            ((lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0))]


def sigmet(coords: list[dict[str, float]], start: datetime, end: datetime, *, top: int | None = 45000,
           base: int | None = None, hazard: str = "TS", series: str = "01",
           raw: str = "XXXX SIGMET 01 VALID EMBD TS FCST TOP FL450") -> dict[str, Any]:
    return {"icaoId": "XXXX", "firId": "XXXX", "seriesId": series, "hazard": hazard,
            "validTimeFrom": int(start.timestamp()), "validTimeTo": int(end.timestamp()),
            "base": base, "top": top, "geom": "AREA", "coords": coords, "rawSigmet": raw,
            "receiptTime": start.isoformat()}


def store(root: Path, at: datetime, **feeds: list[dict[str, Any]]) -> Path:
    """A hand-built snapshot store with one envelope per feed at `at`."""
    for source, payload in feeds.items():
        snapshots.write(root, Envelope(
            source=source, origin="backfill", as_of=at, request=f"https://example.test/{source}",
            status="ok" if payload else "empty", count=len(payload), payload=payload,
            retrieved_at=at + timedelta(minutes=1)))
    return root
