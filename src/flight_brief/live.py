"""The live fetcher: fills the snapshot store with what one brief needs, from AWC, now.

`fetch` matches `service.Fetch`. The engine still reads only the store, so a live brief is
the same code as a replay; this module decides what to ask for and what is already there.

What one brief asks for (docs/DESIGN.md, "Who fetches what"):
- `sigmet`: one worldwide call;
- `gairmet`: the five high-level turbulence snapshots, only when the route crosses the
  contiguous US;
- `pirep`: boxes along the part of the route the engine will look at for reports (the hours
  that start within `REPORT_REACH_H` of now, in a region with reports), 100 nm either side;
- `taf` and `metar`: one call each, for the two airports.

The cache is the store itself. An envelope younger than `TTL` that covers the request is
reused:
- `sigmet`: any envelope, the feed is worldwide. Slot `<HHMM>Z`.
- `gairmet`: one per forecast hour, tags `f00` to `f12`, as the engine reads them.
- `pirep`: the box is whole degrees and is the tag, `b<lat0>_<lon0>_<lat1>_<lon1>`. A cached
  box that contains the needed box serves it. A reply that hit the 400-record cap is not
  used: it is noted as a `skipped` envelope and the two halves are fetched, so the next brief
  goes to the halves without asking for the whole again.
- `taf`, `metar`: the engine reads the single newest envelope and looks for the airport in
  it, so the newest envelope must hold both airports of the brief being made. When it does
  not, the call asks for these two airports plus the ones in the newest envelope while that
  is still young, so flights briefed in turn keep serving each other. The tag is `r00`,
  `r01`, ... counting the envelopes of that minute, which makes a later write sort later.

The store's `status` here means: `ok`, the call answered (for `pirep`, `taf` and `metar` an
answer with no records is still an answer: no reports in the box, no TAF for the airport);
`empty`, an empty `sigmet` or `gairmet` reply, as the collector writes it. A call that fails
writes nothing, so the feed stays as it was and the engine reports it by its age.

AWC's rules: no key, at most 100 requests a minute, a custom User-Agent. One plain call per
request with a short timeout, no retries; a pause only if this process nears the limit.
"""

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import awc
from . import brief as engine
from .awc import Record
from .core import snapshots
from .core.models import PassengerFlight
from .core.route import TimedPoint, position_at, timeline
from .core.snapshots import Envelope
from .resolve import Leg

TTL = timedelta(minutes=30)
MAX_RECORDS = 400  # one reply holds at most this many pilot reports
REPORT_AGE = math.ceil(engine.REPORT_AGE_H)  # the `age` parameter, whole hours
PAD_DEG = engine.CORRIDOR_NM / 60  # the corridor's half width as degrees of latitude
# Consecutive hours share one box while it stays under this many square degrees. Over the
# contiguous US one box of about 1,500 square degrees held 316 reports in an hour, so boxes
# there are kept small; elsewhere reports are sparse.
BOX_DEG2_US = 300.0
BOX_DEG2 = 1500.0
MAX_IDS = 20  # airports carried in one taf or metar call
PER_MINUTE = 90  # this process's own ceiling, under AWC's 100
BUDGET_S = 30.0  # no new call starts later than this after `fetch` began
WORKERS = 4

Get = Callable[[str, dict[str, str]], list[Record]]
Box = tuple[int, int, int, int]  # lat0, lon0, lat1, lon1: AWC's order, whole degrees

log = logging.getLogger(__name__)
_sent: deque[float] = deque()
_lock = threading.Lock()


def polite_get(endpoint: str, params: dict[str, str]) -> list[Record]:
    """`awc.get_records`, waiting only when this process has sent `PER_MINUTE` calls within
    the last minute."""
    with _lock:
        now = time.monotonic()
        while _sent and now - _sent[0] >= 60:
            _sent.popleft()
        if len(_sent) >= PER_MINUTE:
            time.sleep(60 - (now - _sent[0]))
            _sent.popleft()
        _sent.append(time.monotonic())
    return awc.get_records(endpoint, params)


# --- boxes ---------------------------------------------------------------------------------

def report_segments(points: list[TimedPoint], as_of: datetime) -> list[list[TimedPoint]]:
    """The hours of the flight for which the engine reads pilot reports: those that start
    within the reports' reach and whose midpoint is in a region with reports."""
    takeoff, landing = points[0].t, points[-1].t
    out = []
    for i in range(math.ceil((landing - takeoff) / timedelta(hours=1))):
        start = takeoff + timedelta(hours=i)
        end = min(start + timedelta(hours=1), landing)
        mid = position_at(points, start + (end - start) / 2)
        if (start - as_of <= timedelta(hours=engine.REPORT_REACH_H)
                and engine.reports_cover(mid.lat, mid.lon)):
            out.append(engine._segment(points, start, end))
    return out


def _around(seg: list[TimedPoint]) -> tuple[float, float, float, float]:
    """The segment's bounding box widened by the corridor, longitudes still unwrapped."""
    lats = [p.lat for p in seg]
    lons = [p.lon for p in seg]
    lat0, lat1 = max(-90.0, min(lats) - PAD_DEG), min(90.0, max(lats) + PAD_DEG)
    pad_lon = PAD_DEG / max(0.2, math.cos(math.radians(max(abs(lat0), abs(lat1)))))
    return lat0, min(lons) - pad_lon, lat1, max(lons) + pad_lon


def _wrap(lat0: float, lon0: float, lat1: float, lon1: float) -> list[Box]:
    """Whole-degree boxes with longitudes in -180..180; a box across 180 becomes two."""
    a, b, c, d = math.floor(lat0), math.floor(lon0), math.ceil(lat1), math.ceil(lon1)
    if d - b >= 360:
        return [(a, -180, c, 180)]
    shift = 360 * math.floor((b + 180) / 360)
    b, d = b - shift, d - shift
    return [(a, b, c, d)] if d <= 180 else [(a, b, c, 180), (a, -180, c, d - 360)]


def _contains(outer: Box, inner: Box) -> bool:
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def report_boxes(points: list[TimedPoint], as_of: datetime) -> list[Box]:
    """The boxes to ask pilot reports for: consecutive hours joined while the box stays
    small, split at the antimeridian, none inside another."""
    groups: list[tuple[tuple[float, float, float, float], bool]] = []
    for seg in report_segments(points, as_of):
        box = _around(seg)
        us = any(engine.in_conus(p.lat, p.lon) for p in seg)
        if groups:
            last, was_us = groups[-1]
            joined = (min(last[0], box[0]), min(last[1], box[1]),
                      max(last[2], box[2]), max(last[3], box[3]))
            limit = BOX_DEG2_US if us or was_us else BOX_DEG2
            if (joined[2] - joined[0]) * (joined[3] - joined[1]) <= limit:
                groups[-1] = (joined, us or was_us)
                continue
        groups.append((box, us))
    boxes = [b for box, _ in groups for b in _wrap(*box)]
    out: list[Box] = []
    for box in sorted(set(boxes), key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        if not any(_contains(kept, box) for kept in out):
            out.append(box)
    return sorted(out)


def halves(box: Box) -> list[Box]:
    """The box cut in two across its longer side, at a whole degree; [] when it cannot be."""
    lat0, lon0, lat1, lon1 = box
    if lon1 - lon0 >= lat1 - lat0 and lon1 - lon0 >= 2:
        mid = (lon0 + lon1) // 2
        return [(lat0, lon0, lat1, mid), (lat0, mid, lat1, lon1)]
    if lat1 - lat0 >= 2:
        mid = (lat0 + lat1) // 2
        return [(lat0, lon0, mid, lon1), (mid, lon0, lat1, lon1)]
    return []


def _bbox(box: Box) -> str:
    return ",".join(str(v) for v in box)


def _param(env: Envelope, name: str) -> str | None:
    return (parse_qs(urlsplit(env.request).query).get(name) or [None])[0]


def _box_of(env: Envelope) -> Box | None:
    try:
        lat0, lon0, lat1, lon1 = (int(v) for v in (_param(env, "bbox") or "").split(","))
    except ValueError:
        return None
    return lat0, lon0, lat1, lon1


# --- one fetch -----------------------------------------------------------------------------

class _Run:
    def __init__(self, root: Path, as_of: datetime, get: Get) -> None:
        self.root, self.as_of, self.get = root, as_of.astimezone(UTC), get
        self.deadline = time.monotonic() + BUDGET_S

    def fresh(self, env: Envelope | None) -> bool:
        return env is not None and timedelta(0) <= self.as_of - env.as_of < TTL

    def young(self, source: str) -> list[Envelope]:
        """Every envelope of a source younger than `TTL`, whatever its status. The file name
        carries the time, so older files are not opened."""
        out = []
        for day in {self.as_of.date(), (self.as_of - TTL).date()}:
            for path in sorted((self.root / source / day.isoformat()).glob("*.json.gz")):
                try:
                    at = datetime.strptime(f"{day.isoformat()} {path.name[:4]}",
                                           "%Y-%m-%d %H%M").replace(tzinfo=UTC)
                except ValueError:
                    continue
                if timedelta(0) <= self.as_of - at < TTL:
                    out.append(snapshots.read(path))
        return out

    def call(self, endpoint: str, params: dict[str, str]) -> list[Record] | None:
        """The records, or None when the call failed or was not started."""
        line = awc.describe(endpoint, params)
        if time.monotonic() > self.deadline:
            log.warning("not asked, out of time: %s", line)
            return None
        try:
            records = self.get(endpoint, params)
        except Exception as exc:  # noqa: BLE001  a failed source must not stop the brief
            log.warning("failed: %s (%s: %s)", line, type(exc).__name__, exc)
            return None
        log.info("GET %s -> %d records", line, len(records))
        return records

    def save(self, source: str, params: dict[str, str], records: list[Record] | None,
             tag: str | None, status: str, error: str | None = None, count: int = 0) -> None:
        snapshots.write(self.root, Envelope(
            source=source, origin="live", as_of=self.as_of, tag=tag,
            request=awc.describe(source, params), status=status,  # type: ignore[arg-type]
            error=error, count=len(records) if records is not None else count,
            payload=records))

    # Each of these is one job: it looks in the store, asks if it must, and saves.

    def sigmets(self) -> None:
        if self.fresh(snapshots.latest(self.root, "sigmet", self.as_of, max_age=TTL)):
            log.info("cached: sigmet")
            return
        records = self.call("sigmet", {})
        if records is not None:
            self.save("sigmet", {}, records, None, "ok" if records else "empty")

    def gairmet(self, hour: int) -> None:
        tag = f"f{hour:02d}"
        if self.fresh(snapshots.latest(self.root, "gairmet", self.as_of, tag=tag, max_age=TTL,
                                       statuses=("ok", "empty"))):
            log.info("cached: gairmet %s", tag)
            return
        params = {"hazard": "turb-hi", "fore": str(hour)}
        records = self.call("gairmet", params)
        if records is not None:
            self.save("gairmet", params, records, tag, "ok" if records else "empty")

    def airports(self, source: str, ids: list[str]) -> None:
        newest = snapshots.latest(self.root, source, self.as_of,
                                  max_age=engine.MAX_AGE[source])
        have = (set((_param(newest, "ids") or "").split(",")) - {""}
                if newest is not None and self.fresh(newest) else set())
        if set(ids) <= have:
            log.info("cached: %s %s", source, ",".join(ids))
            return
        others = sorted(have - set(ids))[:MAX_IDS - len(ids)]
        params = {"ids": ",".join(sorted({*ids, *others}))}
        records = self.call(source, params)
        if records is not None:
            slot = snapshots.path_for(self.root, source, self.as_of)
            taken = len(list(slot.parent.glob(slot.name.removesuffix(".json.gz") + "*")))
            self.save(source, params, records, f"r{taken:02d}", "ok")

    def reports(self, box: Box, known: list[tuple[str, Box]]) -> None:
        if any(status == "ok" and _contains(have, box) for status, have in known):
            log.info("cached: pirep %s", _bbox(box))
            return
        if ("skipped", box) not in known:
            params = {"bbox": _bbox(box), "age": str(REPORT_AGE)}
            records = self.call("pirep", params)
            if records is None:
                return
            tag = "b" + "_".join(str(v) for v in box)
            if len(records) < MAX_RECORDS or not halves(box):
                if len(records) >= MAX_RECORDS:
                    log.warning("pirep %s still hit the %d cap", _bbox(box), MAX_RECORDS)
                self.save("pirep", params, records, tag, "ok")
                known.append(("ok", box))
                return
            self.save("pirep", params, None, tag, "skipped", count=len(records),
                      error=f"hit the {MAX_RECORDS}-record cap; fetched as two halves")
            known.append(("skipped", box))
        for half in halves(box):
            self.reports(half, known)


def fetch(root: Path, leg: Leg, flight: PassengerFlight, as_of: datetime,
          get: Get | None = None) -> None:
    """Fill the store at `root` with what a brief for this leg needs, as AWC has it now.
    `as_of` must be the current time: the calls carry no date. Never raises for a call that
    fails; that feed is left as it was."""
    run = _Run(root, as_of, get or polite_get)
    points = timeline(flight)
    jobs: list[Callable[[], None]] = [
        run.sigmets,
        lambda: run.airports("taf", [leg.origin.code, leg.destination.code]),
        lambda: run.airports("metar", [leg.origin.code, leg.destination.code]),
    ]
    if any(engine.in_conus(p.lat, p.lon) for p in points):
        jobs += [lambda hour=hour: run.gairmet(hour) for hour in awc.GAIRMET_HOURS]
    boxes = report_boxes(points, run.as_of)
    if boxes:
        known = [(e.status, box) for e in run.young("pirep")
                 if e.status in ("ok", "skipped") and (box := _box_of(e)) is not None]
        jobs += [lambda box=box: run.reports(box, known) for box in boxes]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for done in [pool.submit(job) for job in jobs]:
            done.result()
