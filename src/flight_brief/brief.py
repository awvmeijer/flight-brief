"""The brief engine: one leg, one moment, the snapshot store in; the brief record out.

Deterministic geometry and templates, no model calls. Everything is read "as known at"
`as_of` from a snapshot directory, so a live brief and a replay are the same code at
different times.

Wording rules this module keeps (docs/DESIGN.md):
- no severity word (light, moderate, severe) in any text this code writes. An official
  source's own text is carried in fields named `quoted` and shown in quotation marks;
- "nothing found" only where a source speaks for that hour. Beyond a source's reach the hour
  says "too early to know" or "none issued this far ahead", never nothing;
- a feed with no recent snapshot says "last updated ... ago", never an empty list.

The forecast layer (the wind-shear index worldwide, GTG over the contiguous US) is read from
tiles when a `TileSource` is given; without one, `shear` and `gtg` stay null and unlisted.
"""

import math
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import Point, Polygon

from . import airport_wx, awc, vrs
from .aircraft import Aircraft
from .core import snapshots
from .core.encounters import encounters, expired_on_arrival, near_airports, unplaced
from .core.hazards import from_sigmet
from .core.models import Hazard, PassengerFlight, Waypoint
from .core.route import TimedPoint, distance_nm, position_at, timeline
from .core.snapshots import Envelope
from .forecast import layer
from .forecast.layer import TileSource
from .gairmet import HALF_WINDOW, from_gairmet, valid_time
from .resolve import Leg

# How far ahead each source can speak, in hours from the moment the brief is issued.
SIGMET_REACH_H = 4.0  # SIGMETs are valid for hours: median 4 h in the store
GAIRMET_REACH_H = 12.0
REPORT_REACH_H = 6.0
TAF_REACH_H = 30.0
TAF_CHECK_H = 24.0  # a TAF covering a time exists about a day before it
# The longest horizon of the sources switched on: the TAF without the forecast tiles, the
# wind-shear index with them.
TOO_FAR_H = TAF_REACH_H


def horizon_h(with_tiles: bool) -> float:
    return float(layer.SHEAR_REACH_H) if with_tiles else TOO_FAR_H


# The wind-shear index (Ellrod TI1 from open GFS winds, units 1e-7 s^-2, highest value within
# 0.5 degree). The flag and its rates are the recon's measurement at 24 to 27 h lead, FL290 to
# FL440 (docs/RECON.md). It is an index, not a turbulence forecast: no intensity is named.
SHEAR_FLAG = 8.0
SHEAR_RATES = "flags 72% of rough reports and 35% of smooth ones"
SHEAR_FL = (290, 440)
SHEAR_STALE = timedelta(hours=18)  # two runs missed
SHEAR_LICENCE = ("Wind-shear index computed by this project from NOAA GFS winds "
                 "(US Government work, public domain)")
# GTG (NOAA's turbulence forecast, contiguous US): eddy dissipation rate, m^2/3 s^-1. NOAA's
# intensity names depend on the aircraft, so none is used here. The flag is this project's
# own number and its hit rate has not been measured yet.
GTG_FLAG = 0.22
GTG_FL = (285, 435)  # the tiles hold FL300 to FL420
GTG_STALE = timedelta(hours=9)
GTG_LICENCE = "NOAA Graphical Turbulence Guidance (GTG), US Government work (public domain)"

# How old a snapshot may be before its feed counts as stale. These follow the snapshot
# store's cadence; the page's on-request caches will use 30 minutes.
MAX_AGE = {"sigmet": timedelta(hours=2), "gairmet": timedelta(hours=3),
           "pirep": timedelta(hours=4), "taf": timedelta(hours=2), "metar": timedelta(hours=7)}

NEAR_NM = 150.0
CORRIDOR_NM = 100.0
CORRIDOR_FT = 4000
REPORT_AGE_H = 3.0
# Departure and arrival times are gate times, typed or estimated. On every path the flight is
# airborne from TAXI_OUT after departure to TAXI_IN before arrival: a typed arrival time has
# both taken off before the groundspeed is worked out, and an estimated arrival has both added.
TAXI_OUT = timedelta(minutes=20)
TAXI_IN = timedelta(minutes=10)
GROUND = (f"{(TAXI_OUT + TAXI_IN) // timedelta(minutes=1)} minutes on the ground counted: "
          f"take-off {TAXI_OUT // timedelta(minutes=1)} minutes after the departure time, at "
          f"the gate {TAXI_IN // timedelta(minutes=1)} minutes after landing")
# A typed arrival time is refused when the groundspeed it gives is outside these limits. The
# slowest accepted speed rises with distance. Under 800 nm a turboprop on a short hop is real
# and the time on the ground dominates. From 800 to 1,200 nm turboprops still fly (an ATR 72
# at about 275 kt, Papeete to Gambier, 893 nm), so the floor sits under that. Beyond 1,200 nm
# only jets fly, and the limits sit under the slowest scheduled westbound flights (about
# 360 kt over great-circle distance).
FASTEST_KT = 700.0
# (upper distance in nm, slowest groundspeed in kt)
SLOWEST_KT = ((800, 200.0), (1200, 230.0), (2500, 300.0), (math.inf, 340.0))

# (upper distance in nm, cruise flight level, groundspeed in kt)
BANDS = ((300, 240, 380), (800, 340, 430), (2500, 360, 460), (math.inf, 380, 480))

KIND_WORDS = {"TS": "thunderstorm warning", "TURB": "turbulence warning",
              "ICE": "icing warning", "MTW": "mountain wave warning",
              "VA": "volcanic ash warning", "TC": "tropical cyclone warning",
              "DS": "dust storm warning", "SS": "sandstorm warning",
              "TURB-HI": "high-level turbulence area"}

DEPARTED = "This flight has departed. Briefs are issued before departure"
NOT_OPS = "Not for flight operations"
TRACKER = ("Delays, cancellations and whether the flight runs that day are not tracked. "
           "Check your airline or flight tracker")

# A rough outline of the contiguous US, (lon, lat). G-AIRMETs exist only inside it.
CONUS = Polygon([
    (-124.7, 48.4), (-95.2, 49.0), (-89.5, 48.0), (-84.5, 46.5), (-82.5, 45.3), (-82.4, 41.7),
    (-79.0, 43.3), (-75.0, 45.0), (-71.5, 45.0), (-69.2, 47.4), (-67.0, 45.0), (-70.0, 41.5),
    (-74.0, 40.4), (-75.5, 35.2), (-81.0, 31.0), (-80.0, 25.0), (-81.8, 24.5), (-84.0, 30.0),
    (-89.5, 29.0), (-94.0, 29.4), (-97.4, 25.9), (-101.4, 29.8), (-104.5, 29.6),
    (-106.5, 31.8), (-111.0, 31.3), (-114.8, 32.5), (-117.1, 32.5), (-120.6, 34.5),
    (-124.3, 40.3)])

SourceState = Literal["flagged", "nothing_found", "too_early", "none_issued", "no_reports",
                      "not_covered", "stale"]
HourState = Literal["flagged", "nothing_found", "too_early", "not_covered", "stale"]
SPEAKS = ("flagged", "nothing_found")


class Out(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class SourceLine(Out):
    state: SourceState
    text: str
    check_after: datetime | None = None  # for "too early": when this hour comes into reach


class WarningHit(Out):
    hazard_id: str
    kind: str
    what: str
    entry: datetime
    exit: datetime
    base_ft: int | None
    top_ft: int | None
    valid_to: datetime
    issued_at: datetime | None
    quoted: str  # the warning's own text


class ExpiredHit(Out):
    hazard_id: str
    kind: str
    what: str
    reach: datetime
    valid_to: datetime
    quoted: str


class ReportHit(Out):
    observed_at: datetime
    lat: float
    lon: float
    flight_level: int | None
    distance_nm: float
    flags: bool  # the pilot stated more than the lowest intensities
    quoted: str  # the report's own text


class Hour(Out):
    index: int
    start: datetime = Field(serialization_alias="from")
    end: datetime = Field(serialization_alias="to")
    state: HourState
    agreement: Literal["agree", "disagree", "single"] | None
    shear: dict[str, Any] | None = None  # {model, run, layer, value, threshold, flagged}
    gtg: dict[str, Any] | None = None  # {run, edr, level, threshold, flagged, category}
    warnings: list[WarningHit] = []
    expired_warnings: list[ExpiredHit] = []
    gairmets: list[WarningHit] = []
    reports: list[ReportHit] = []
    sources: dict[str, SourceLine] = {}


class NearAirportHit(Out):
    end: Literal["origin", "destination"]
    airport: str
    phase: Literal["climb", "descent"]
    hazard_id: str
    kind: str
    what: str
    below_cruise: bool
    base_ft: int | None
    top_ft: int | None
    valid_to: datetime
    quoted: str


class WxLine(Out):
    state: Literal["ok", "too_early", "not_covered", "stale"]
    text: str
    issued_at: datetime | None = None
    quoted: str | None = None  # the raw TAF or METAR


class AirportWx(Out):
    code: str
    name: str
    at: datetime  # departure for the origin, estimated arrival for the destination
    taf: WxLine
    metar: WxLine


class Provenance(Out):
    source: str
    fetched_at: datetime | None
    issued_at: datetime | None
    url_without_secrets: str | None
    licence: str
    stale: bool = False
    note: str | None = None


class Place(Out):
    code: str
    iata: str
    name: str
    location: str
    tz: str | None


class FlightBlock(Out):
    code: str
    callsign: str | None
    origin: Place
    destination: Place
    departure_utc: datetime
    departure_local: str  # as the user entered it, with the origin's UTC offset
    departure_source: str = "user input"
    arrival_utc: datetime  # as the user entered it, or from distance and assumed groundspeed
    takeoff_utc: datetime  # departure plus the assumed time to take-off; hour 1 starts here
    landing_utc: datetime  # arrival less the assumed time from landing to the gate
    arrival_source: Literal["estimate", "user input"] = "estimate"
    aircraft: str | None = None  # the type the user typed, when it set a default
    distance_nm: float
    cruise_fl: int
    groundspeed_kt: float
    assumptions: list[str]


class Route(Out):
    kind: Literal["great_circle", "track_median"] = "great_circle"
    n_tracks: int = 0
    points: list[tuple[float, float]]  # (lat, lon), longitudes continuous across 180


class Identity(Out):
    code: str
    date: date  # the scheduled local departure date at the leg's origin
    leg: int
    issued_at: datetime


class Brief(Out):
    status: Literal["ok", "departed", "too_far_out"]
    message: str | None = None
    identity: Identity
    flight: FlightBlock
    route: Route
    summary: str = ""
    hours: list[Hour] = []
    near_airports: list[NearAirportHit] = []
    airports: dict[str, AirportWx] = {}
    warnings_without_area: list[dict[str, str]] = []
    notes: list[str] = []
    provenance: list[Provenance] = []

    def record(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


# --- the flight ----------------------------------------------------------------------------

def to_utc(day: date, clock: str, tz: str | None) -> datetime:
    """A typed local departure time as UTC. `clock` is HH:MM, or HH:MM+HH:MM with an offset."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?:([+-])(\d{2}):?(\d{2}))?", clock.strip())
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"'{clock}' is not a time. Use HH:MM, for example 23:55")
    local = datetime.combine(day, time(int(match[1]), int(match[2])))
    if match[3]:
        offset = timedelta(hours=int(match[4]), minutes=int(match[5]))
        return (local - (offset if match[3] == "+" else -offset)).replace(tzinfo=UTC)
    if tz is None:
        raise ValueError("No time zone is known for the departure airport. "
                         "Add the UTC offset to the time, for example 23:55+08:00")
    return local.replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


def arrival_utc(departure: datetime, clock: str, tz: str | None) -> datetime:
    """A typed local arrival time as UTC: the first time that clock reads so at the destination
    after departure. No flight is a day long, so the date never has to be typed."""
    start = departure.astimezone(UTC).date() - timedelta(days=1)
    try:
        times = [to_utc(start + timedelta(days=n), clock, tz) for n in range(4)]
    except ValueError as exc:
        raise ValueError(str(exc).replace("departure airport", "arrival airport")) from exc
    return min(t for t in times if t > departure)


def defaults(distance: float) -> tuple[int, float]:
    """Cruise level and groundspeed for a distance band."""
    for limit, fl, kt in BANDS:
        if distance < limit:
            return fl, float(kt)
    raise AssertionError("unreachable")


def make_flight(code: str, leg: Leg, departure: datetime, cruise_fl: int | None = None,
                groundspeed_kt: float | None = None, arrival: datetime | None = None,
                aircraft: Aircraft | None = None) -> PassengerFlight:
    """`departure` and `arrival` are gate times. However the groundspeed is found, the
    flight is airborne from `TAXI_OUT` after departure, at one groundspeed, and reaches the
    gate `TAXI_IN` after landing. The returned flight's `departure` is the take-off time.

    Where each number comes from, first match wins. Groundspeed: the arrival time, then a
    typed groundspeed, then the aircraft type, then the distance band. Cruise level: a typed
    level, then the aircraft type, then the distance band. A jet type has no figure of its
    own, so it leaves the distance bands in place."""
    a, b = leg.origin, leg.destination
    distance = distance_nm(a.lat, a.lon, b.lat, b.lon)
    fl, kt = defaults(distance)
    own = "this project's round number, not from a source"
    level = f"Cruise level FL{fl} assumed from the distance ({distance:,.0f} nm)"
    band_speed = f"Groundspeed {kt:.0f} kt assumed for the whole flight; winds are not counted"
    replaced = ""
    if aircraft is not None and aircraft.cruise_fl is not None:
        fl = aircraft.cruise_fl
        level = f"Cruise level FL{fl} assumed for {aircraft.label} ({own})"
    if aircraft is not None and aircraft.groundspeed_kt is not None:
        kt = aircraft.groundspeed_kt
        replaced = f"; this replaces the {kt:.0f} kt assumed for {aircraft.code}"
        band_speed = (f"Groundspeed {kt:.0f} kt assumed for {aircraft.label}, for the whole "
                      f"flight ({own}); winds are not counted")
    elif aircraft is not None:
        band_speed += f". {aircraft.label}: no figure of its own, so the distance decides"
        level += f". {aircraft.label}: no figure of its own" if not cruise_fl else ""
    if cruise_fl:
        level = f"Cruise level FL{cruise_fl} as you entered" + (
            f"; this replaces FL{aircraft.cruise_fl} assumed for {aircraft.code}"
            if aircraft is not None and aircraft.cruise_fl is not None else "")
    taxi = TAXI_OUT + TAXI_IN
    if arrival is not None:
        if groundspeed_kt:
            raise ValueError("Give an arrival time or a groundspeed, not both")
        airborne = (arrival - departure - taxi) / timedelta(hours=1)
        groundspeed_kt = distance / airborne if airborne > 0 else 0.0
        slowest = next(kt for limit, kt in SLOWEST_KT if distance < limit)
        if not slowest <= groundspeed_kt <= FASTEST_KT:
            h, m = divmod(int((arrival - departure) / timedelta(minutes=1)), 60)
            raise ValueError(
                f"That arrival time does not fit a flight of {distance:,.0f} nm: "
                f"{h} h {m:02d} min gate to gate is too "
                f"{'short' if groundspeed_kt > FASTEST_KT or airborne <= 0 else 'long'}. "
                "Check the arrival time, which is local time at the destination")
        speed = (f"Groundspeed {groundspeed_kt:.0f} kt for the whole flight, worked out from "
                 f"the departure and arrival times you entered less {taxi // timedelta(minutes=1)}"
                 " minutes on the ground" + replaced)
        times = "Departure and arrival times as you entered"
    else:
        speed = (f"Groundspeed {groundspeed_kt:.0f} kt as you entered" + replaced
                 if groundspeed_kt else band_speed)
        times = ("Departure time as you entered; arrival is an estimate from distance and "
                 "speed plus the time on the ground")
    notes = [
        level,
        speed,
        GROUND,
        "Route is a great-circle guess, not the filed route",
        times,
    ]
    return PassengerFlight(
        flight_id=code, origin=a.code, destination=b.code, departure=departure + TAXI_OUT,
        cruise_fl=cruise_fl or fl, groundspeed_kt=groundspeed_kt or kt,
        route=[Waypoint(name=a.code, lat=a.lat, lon=a.lon),
               Waypoint(name=b.code, lat=b.lat, lon=b.lon)],
        assumptions=notes)


# --- small helpers -------------------------------------------------------------------------

def hhmm(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%H:%M UTC")


def day_hhmm(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%a %d %b %H:%M UTC")


def age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h"


def levels(base: int | None, top: int | None) -> str:
    low = "surface" if not base else f"FL{base // 100:03d}"
    return f"{low} to FL{top // 100:03d}" if top is not None else f"{low}, top not stated"


def what(kind: str) -> str:
    return KIND_WORDS.get(kind, f"warning ({kind})")


def quote(text: str) -> str:
    """Official text on one line, safe to put inside quotation marks."""
    return " ".join(text.split()).replace('"', "'")


TOO_EARLY = "Too early to know. Check again"


def too_early(after: datetime) -> str:
    return f"{TOO_EARLY} after {day_hhmm(after)}"


def _too_early(after: datetime) -> SourceLine:
    return SourceLine(state="too_early", text=too_early(after), check_after=after)


def in_conus(lat: float, lon: float) -> bool:
    return bool(CONUS.contains(Point((lon + 180) % 360 - 180, lat)))


def reports_cover(lat: float, lon: float) -> bool:
    """Regions where enough pilot reports mention turbulence to say anything (docs/RECON.md):
    the contiguous US and the North Pacific corridor (Alaska, Hawaii, Japan, the ocean between)."""
    lon = (lon + 180) % 360 - 180
    if in_conus(lat, lon):
        return True
    if 15 <= lat <= 25 and -165 <= lon <= -150:
        return True  # Hawaii
    return lat >= 20 and (lon >= 125 or lon <= -120)


def _segment(points: list[TimedPoint], start: datetime, end: datetime) -> list[TimedPoint]:
    return [position_at(points, start), *[p for p in points if start < p.t < end],
            position_at(points, end)]


def _hit(hz: Hazard, entry: datetime, exit_: datetime) -> WarningHit:
    return WarningHit(hazard_id=hz.hazard_id, kind=hz.kind, what=what(hz.kind), entry=entry,
                      exit=exit_, base_ft=hz.base_ft, top_ft=hz.top_ft, valid_to=hz.valid_to,
                      issued_at=hz.issued_at, quoted=quote(hz.raw)[:600])


def _provenance(env: Envelope | None, source: str, as_of: datetime) -> Provenance:
    """The source-stale pattern from oceanic-flight-watch's `wx.py`: a feed with nothing recent
    is named as stale, so its silence cannot be read as an all clear."""
    if env is None:
        return Provenance(source=source, fetched_at=None, issued_at=None,
                          url_without_secrets=None, licence=awc.LICENCE, stale=True,
                          note=f"no recent {source} data at {day_hhmm(as_of)}: "
                               "nothing listed is NOT an all clear")
    return Provenance(source=source, fetched_at=env.retrieved_at, issued_at=env.as_of,
                      url_without_secrets=snapshots.strip_secrets(env.request),
                      licence=awc.LICENCE)


LABELS = {"sigmet": "Official warnings", "gairmet": "G-AIRMETs", "pirep": "Pilot reports",
          "taf": "TAF", "metar": "METAR"}


# --- the forecast layer --------------------------------------------------------------------

class _Shear:
    """The wind-shear index for the hours of one brief."""

    def __init__(self, tiles: TileSource, as_of: datetime, cruise_fl: int):
        self.cruise_fl = cruise_fl
        self.found = tiles.shear(as_of) if SHEAR_FL[0] <= cruise_fl <= SHEAR_FL[1] else None
        self.age = as_of - self.found[1] if self.found else None
        self.newest = layer.runs_before(as_of, layer.SHEAR_LAG, 1)[0]  # the run posted by now

    def provenance(self, as_of: datetime) -> Provenance | None:
        if not SHEAR_FL[0] <= self.cruise_fl <= SHEAR_FL[1]:
            return None
        if self.found is None:
            return Provenance(source="wind-shear index", fetched_at=None, issued_at=None,
                              url_without_secrets=None, licence=SHEAR_LICENCE, stale=True,
                              note=f"no wind-shear index tile at {day_hhmm(as_of)}: "
                                   "nothing listed is NOT an all clear")
        _, run, url = self.found
        assert self.age is not None
        late = self.age > SHEAR_STALE
        return Provenance(source="wind-shear index", fetched_at=None, issued_at=run,
                          url_without_secrets=url, licence=SHEAR_LICENCE, stale=late,
                          note=f"Wind-shear index last updated {age(self.age)} ago" if late
                          else None)

    def hour(self, seg: list[TimedPoint], end: datetime
             ) -> tuple[SourceLine, dict[str, Any] | None]:
        if not SHEAR_FL[0] <= self.cruise_fl <= SHEAR_FL[1]:
            return SourceLine(state="not_covered", text=(
                f"Not covered here: the index is measured for FL{SHEAR_FL[0]} to "
                f"FL{SHEAR_FL[1]} only")), None
        if self.found is None:
            return SourceLine(state="stale", text="Wind-shear index: no tile available"), None
        tile, run, _ = self.found
        if end > run + timedelta(hours=layer.SHEAR_REACH_H):
            if end <= self.newest + timedelta(hours=layer.SHEAR_REACH_H):
                # a run that reaches this hour is posted, but its tile is not here
                assert self.age is not None
                return SourceLine(state="stale", text=(
                    f"Wind-shear index last updated {age(self.age)} ago")), None
            return _too_early(end - timedelta(hours=layer.SHEAR_REACH_H) + layer.SHEAR_LAG), None
        got = [x for x in (tile.sample(p.lat, p.lon, self.cruise_fl, p.t) for p in seg) if x]
        if not got:
            return SourceLine(state="not_covered",
                              text="Not covered here: no index value on this stretch"), None
        value, layer_fl, _ = max(got)
        flagged = value >= SHEAR_FLAG
        record = {"model": "GFS 0.25 degree", "run": run.isoformat(), "layer": layer_fl,
                  "value": round(value, 1), "threshold": SHEAR_FLAG, "flagged": flagged}
        if flagged:
            return SourceLine(state="flagged", text=(
                f"Elevated wind shear: index {value:.0f}, flag at {SHEAR_FLAG:.0f} "
                f"({SHEAR_RATES})")), record
        return SourceLine(state="nothing_found", text=(
            f"Nothing found: index below the flag at {SHEAR_FLAG:.0f}")), record


class _Gtg:
    """NOAA's GTG turbulence forecast for the hours of one brief that lie over the US."""

    def __init__(self, tiles: TileSource, as_of: datetime, cruise_fl: int):
        self.cruise_fl = cruise_fl
        self.level_ok = GTG_FL[0] <= cruise_fl <= GTG_FL[1]
        self.found = tiles.gtg(as_of) if self.level_ok else None
        self.age = as_of - self.found[1] if self.found else None
        self.newest = layer.runs_before(as_of, layer.GTG_LAG, 1)[0]  # the run posted by now

    def provenance(self, as_of: datetime) -> Provenance | None:
        if not self.level_ok:
            return None
        if self.found is None:
            return Provenance(source="gtg", fetched_at=None, issued_at=None,
                              url_without_secrets=None, licence=GTG_LICENCE, stale=True,
                              note="GTG line left out: no GTG tile at " + day_hhmm(as_of))
        _, run, url = self.found
        assert self.age is not None
        late = self.age > GTG_STALE
        return Provenance(source="gtg", fetched_at=None, issued_at=run, url_without_secrets=url,
                          licence=GTG_LICENCE, stale=late,
                          note=f"GTG last updated {age(self.age)} ago" if late else None)

    def hour(self, seg: list[TimedPoint], end: datetime
             ) -> tuple[SourceLine | None, dict[str, Any] | None]:
        if not self.level_ok:
            return SourceLine(state="not_covered", text=(
                "Not covered at this cruise level: the saved GTG levels are FL300 to FL420")), None
        if self.found is None:
            return None, None  # the line is left out, with a note
        tile, run, _ = self.found
        if end > run + timedelta(hours=layer.GTG_REACH_H):
            if end <= self.newest + timedelta(hours=layer.GTG_REACH_H):
                assert self.age is not None
                return SourceLine(state="stale",
                                  text=f"GTG last updated {age(self.age)} ago"), None
            return _too_early(end - timedelta(hours=layer.GTG_REACH_H) + layer.GTG_LAG), None
        level = min(tile.flight_levels, key=lambda fl: abs(fl - self.cruise_fl))
        values = []
        for p in seg:
            hour = round((p.t - run) / timedelta(hours=1))
            if hour in tile.forecast_hours:
                v = tile.sample(p.lat, (p.lon + 180) % 360 - 180, level, hour)
                if not math.isnan(v):
                    values.append(v)
        if not values:
            return SourceLine(state="not_covered",
                              text="Not covered here: outside the GTG grid"), None
        edr = max(values)
        flagged = edr >= GTG_FLAG
        record = {"run": run.isoformat(), "edr": round(edr, 3), "level": level,
                  "threshold": GTG_FLAG, "flagged": flagged, "category": None}
        if flagged:
            return SourceLine(state="flagged", text=(
                f"GTG eddy dissipation rate {edr:.2f} at FL{level}, flag at {GTG_FLAG:.2f} "
                "(this project's flag; its hit rate is not measured yet)")), record
        return SourceLine(state="nothing_found", text=(
            f"Nothing found: GTG below the flag at {GTG_FLAG:.2f}")), record


def _last_update(root: Path, source: str, as_of: datetime) -> str:
    """The stale wording: when the feed was last seen, however long ago."""
    env = snapshots.latest(root, source, as_of, max_age=timedelta(days=30),
                           statuses=("ok", "empty"))
    if env is None:
        return f"{LABELS[source]}: no data in this snapshot set"
    return f"{LABELS[source]} last updated {age(as_of - env.as_of)} ago"


# --- sources -------------------------------------------------------------------------------

def read_sigmets(root: Path, as_of: datetime) -> tuple[Envelope | None, list[Hazard]]:
    env = snapshots.latest(root, "sigmet", as_of, max_age=MAX_AGE["sigmet"])
    return env, [from_sigmet(r) for r in (env.payload if env else [])]


def read_gairmets(root: Path, as_of: datetime
                  ) -> tuple[list[Envelope], list[Hazard], list[tuple[datetime, datetime]]]:
    """The G-AIRMET snapshots known at `as_of`: envelopes, areas, and the time windows the
    snapshots stand for (a snapshot with no areas still speaks for its window)."""
    found: list[tuple[int, Envelope]] = []
    for hour in awc.GAIRMET_HOURS:
        env = snapshots.latest(root, "gairmet", as_of, tag=f"f{hour:02d}",
                               max_age=MAX_AGE["gairmet"], statuses=("ok", "empty"))
        if env is not None:
            found.append((hour, env))
    # `fore` counts from the package's first snapshot, not from the request time. Packages
    # start at 03, 09, 15 and 21 UTC and are issued 15 minutes before; a snapshot that came
    # back empty takes its time from a sibling, or from that schedule.
    first = next((valid_time(env.payload[0]) - timedelta(hours=hour)
                  for hour, env in found if env.payload), None)
    if first is None:
        shifted = as_of + timedelta(minutes=15) - timedelta(hours=3)
        first = (shifted.replace(minute=0, second=0, microsecond=0)
                 - timedelta(hours=shifted.hour % 6) + timedelta(hours=3))
    hazards: list[Hazard] = []
    windows = []
    for hour, env in found:
        at = first + timedelta(hours=hour)
        windows.append((at - HALF_WINDOW, at + HALF_WINDOW))
        hazards += [from_gairmet(r, i) for i, r in enumerate(env.payload or [])
                    if r.get("hazard") == "TURB-HI"]
    return [env for _, env in found], hazards, windows


def read_reports(root: Path, as_of: datetime) -> tuple[list[Envelope], list[dict[str, Any]]]:
    """Pilot reports that mention turbulence, observed in the last few hours, each once."""
    start = as_of - timedelta(hours=REPORT_AGE_H)
    envs = snapshots.between(root, "pirep", as_of - MAX_AGE["pirep"], as_of)
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for env in envs:
        for r in env.payload or []:
            if (r.get("tbInt1") and r.get("lat") is not None and r.get("lon") is not None
                    and start.timestamp() < r["obsTime"] <= as_of.timestamp()):
                seen[(r["obsTime"], r["lat"], r["lon"], r["rawOb"])] = r
    return envs, list(seen.values())


def _reports_near(reports: list[dict[str, Any]], seg: list[TimedPoint],
                  cruise_ft: int) -> list[ReportHit]:
    lats = [p.lat for p in seg]
    found = []
    for r in reports:
        level = r.get("fltLvl")
        if isinstance(level, int | float) and abs(level * 100 - cruise_ft) > CORRIDOR_FT:
            continue
        if not min(lats) - 2 <= r["lat"] <= max(lats) + 2:
            continue
        near = min(distance_nm(p.lat, p.lon, r["lat"], r["lon"]) for p in seg)
        if near <= CORRIDOR_NM:
            found.append(ReportHit(
                observed_at=datetime.fromtimestamp(r["obsTime"], UTC), lat=r["lat"],
                lon=r["lon"], flight_level=int(level) if isinstance(level, int | float) else None,
                distance_nm=round(near, 0),
                flags=bool(re.search(r"MOD|SEV|EXTRM", str(r["tbInt1"]))),
                quoted=quote(str(r["rawOb"]))))
    return sorted(found, key=lambda h: h.observed_at, reverse=True)


def _airport(root: Path, place: vrs.Airport, at: datetime, as_of: datetime,
             used: dict[str, Envelope | None]) -> AirportWx:
    taf_env = used.setdefault("taf", snapshots.latest(root, "taf", as_of, max_age=MAX_AGE["taf"]))
    metar_env = used.setdefault(
        "metar", snapshots.latest(root, "metar", as_of, max_age=MAX_AGE["metar"]))
    if taf_env is None:
        taf = WxLine(state="stale", text=_last_update(root, "taf", as_of))
    else:
        record = next((r for r in taf_env.payload if r.get("icaoId") == place.code), None)
        if record is None:
            taf = WxLine(state="not_covered", text="Not covered here: no TAF for this airport")
        elif at.timestamp() >= record["validTimeTo"]:
            taf = WxLine(state="too_early", text=too_early(at - timedelta(hours=TAF_CHECK_H)),
                         issued_at=record.get("issueTime"), quoted=quote(record["rawTAF"]))
        else:
            taf = WxLine(state="ok", text=airport_wx.taf_at(record, at.timestamp()),
                         issued_at=record.get("issueTime"), quoted=quote(record["rawTAF"]))
    if metar_env is None:
        metar = WxLine(state="stale", text=_last_update(root, "metar", as_of))
    else:
        obs = [r for r in metar_env.payload if r.get("icaoId") == place.code
               and r["obsTime"] <= as_of.timestamp()]
        if not obs:
            metar = WxLine(state="not_covered",
                           text="Not covered here: no METAR for this airport")
        else:
            last = max(obs, key=lambda r: r["obsTime"])
            seen = datetime.fromtimestamp(last["obsTime"], UTC)
            metar = WxLine(state="ok", text=f"{airport_wx.metar_now(last)} (observed {hhmm(seen)})",
                           issued_at=seen, quoted=quote(last["rawOb"]))
    return AirportWx(code=place.code, name=place.name, at=at, taf=taf, metar=metar)


# --- the brief -----------------------------------------------------------------------------

def _place(a: vrs.Airport) -> Place:
    return Place(code=a.code, iata=a.iata, name=a.name, location=a.location, tz=a.tz)


def _stretches(hours: list[Hour]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for h in hours:
        if h.state != "flagged":
            continue
        if out and out[-1][1] == h.index - 1:
            out[-1] = (out[-1][0], h.index)
        else:
            out.append((h.index, h.index))
    return out


def _summary(hours: list[Hour]) -> str:
    runs = _stretches(hours)
    spans = ", ".join(f"hour {a}" if a == b else f"hours {a} to {b}" for a, b in runs)
    head = (f"{len(runs)} flagged stretch{'es' if len(runs) != 1 else ''}"
            + (f" ({spans})" if runs else ""))
    names = {"shear": "Wind-shear index", "gtg": "GTG", "warnings": "Official warnings",
             "gairmet": "G-AIRMETs", "reports": "Pilot reports"}
    parts = []
    for key, name in names.items():
        listed = [h for h in hours if key in h.sources]
        if listed:
            n = sum(h.sources[key].state in SPEAKS for h in listed)
            verb = "speaks" if key in ("shear", "gtg") else "speak"
            parts.append(f"{name} {verb} for {n} of {len(hours)} hours")
    return f"{head}. {'. '.join(parts)}."


def make_brief(code: str, leg: Leg, day: date, departure: datetime, as_of: datetime,
               root: Path, callsign: str | None = None, cruise_fl: int | None = None,
               groundspeed_kt: float | None = None, arrival: datetime | None = None,
               aircraft: Aircraft | None = None, tiles: TileSource | None = None) -> Brief:
    """The brief for one leg as known at `as_of`, read from the snapshot store at `root`.
    `departure` and the optional `arrival` are the gate times the user typed. With `tiles`,
    the wind-shear index and GTG are read too, and the brief reaches as far as the index."""
    flight = make_flight(code, leg, departure, cruise_fl, groundspeed_kt, arrival, aircraft)
    points = timeline(flight)
    typed = arrival is not None
    # Gate to gate is `departure` to `arrival`; the hours of the brief run take-off to landing.
    takeoff, landing = flight.departure, points[-1].t
    arrival = arrival or landing + TAXI_IN
    tz = leg.origin.tz
    local = departure.astimezone(ZoneInfo(tz)) if tz else departure
    block = FlightBlock(
        code=code, callsign=callsign, origin=_place(leg.origin),
        destination=_place(leg.destination), departure_utc=departure,
        departure_local=local.isoformat(timespec="minutes"), arrival_utc=arrival,
        takeoff_utc=takeoff, landing_utc=landing,
        arrival_source="user input" if typed else "estimate",
        aircraft=aircraft.code if aircraft is not None and not typed and not groundspeed_kt
        and aircraft.groundspeed_kt is not None else None,
        distance_nm=round(points[-1].nm, 0), cruise_fl=flight.cruise_fl,
        groundspeed_kt=flight.groundspeed_kt, assumptions=flight.assumptions)
    step = max(1, len(points) // 60)
    route = Route(points=[(round(p.lat, 3), round(p.lon, 3))
                          for p in [*points[:-1:step], points[-1]]])
    base = {"identity": Identity(code=code, date=day, leg=leg.index, issued_at=as_of),
            "flight": block, "route": route, "notes": [TRACKER, NOT_OPS]}
    if departure <= as_of:
        return Brief(status="departed", message=DEPARTED, **base)
    limit = horizon_h(tiles is not None)
    if departure - as_of > timedelta(hours=limit):
        return Brief(status="too_far_out", **base,
                     message=f"Too far out. Come back within {limit:.0f} hours of departure, "
                             f"after {day_hhmm(departure - timedelta(hours=limit))}")

    lookahead = (arrival - as_of).total_seconds() / 3600 + 1
    cruise_ft = flight.cruise_fl * 100
    provenance: list[Provenance] = []
    notes: list[str] = []

    sig_env, sigmets = read_sigmets(root, as_of)
    provenance.append(_provenance(sig_env, "sigmet", as_of))
    by_id = {h.hazard_id: h for h in sigmets}
    sig_hits = encounters(flight, sigmets, as_of, lookahead)
    sig_late = expired_on_arrival(flight, sigmets, as_of)

    over_us = [any(in_conus(p.lat, p.lon) for p in _segment(
        points, takeoff + timedelta(hours=i), min(takeoff + timedelta(hours=i + 1), landing)))
        for i in range(math.ceil((landing - takeoff) / timedelta(hours=1)))]
    gair_envs: list[Envelope] = []
    gairmets: list[Hazard] = []
    windows: list[tuple[datetime, datetime]] = []
    if any(over_us):
        gair_envs, gairmets, windows = read_gairmets(root, as_of)
        provenance += ([_provenance(e, "gairmet", as_of) for e in gair_envs]
                       or [_provenance(None, "gairmet", as_of)])
        if not gair_envs:
            notes.append("G-AIRMET line left out. " + _last_update(root, "gairmet", as_of))
    gair_by_id = {h.hazard_id: h for h in gairmets}
    gair_hits = encounters(flight, gairmets, as_of, lookahead)

    rep_envs, reports = read_reports(root, as_of)
    provenance += [_provenance(e, "pirep", as_of) for e in rep_envs]

    shear = _Shear(tiles, as_of, flight.cruise_fl) if tiles is not None else None
    gtg = _Gtg(tiles, as_of, flight.cruise_fl) if tiles is not None and any(over_us) else None
    provenance += [p for p in (shear.provenance(as_of) if shear else None,
                               gtg.provenance(as_of) if gtg else None) if p is not None]
    if tiles is not None and tiles.errors:
        notes.append("A forecast tile could not be fetched: " + "; ".join(tiles.errors[:2]))

    hours: list[Hour] = []
    for i, us in enumerate(over_us):
        start = takeoff + timedelta(hours=i)
        end = min(start + timedelta(hours=1), landing)
        seg = _segment(points, start, end)
        lead = (start - as_of).total_seconds() / 3600
        sources: dict[str, SourceLine] = {}
        shear_rec: dict[str, Any] | None = None
        gtg_rec: dict[str, Any] | None = None
        if shear is not None:
            sources["shear"], shear_rec = shear.hour(seg, end)
        if gtg is not None and us:
            gtg_line, gtg_rec = gtg.hour(seg, end)
            if gtg_line is not None:
                sources["gtg"] = gtg_line

        hits = [_hit(by_id[e.hazard_id], e.entry, e.exit) for e in sig_hits
                if e.entry < end and e.exit >= start]
        late = [ExpiredHit(hazard_id=x.hazard_id, kind=x.kind, what=what(x.kind), reach=x.reach,
                           valid_to=x.valid_to, quoted=quote(by_id[x.hazard_id].raw)[:600])
                for x in sig_late if start <= x.reach < end]
        if sig_env is None:
            sources["warnings"] = SourceLine(state="stale",
                                             text=_last_update(root, "sigmet", as_of))
        elif hits:
            sources["warnings"] = SourceLine(state="flagged", text="; ".join(
                f"{h.what} crossed, {levels(h.base_ft, h.top_ft)}, valid to {hhmm(h.valid_to)}"
                for h in hits))
        elif lead <= SIGMET_REACH_H and not late:
            sources["warnings"] = SourceLine(state="nothing_found",
                                             text="Nothing found: no warning in force here")
        else:
            text = "None issued this far ahead"
            if late:
                text += "; in force there now, ending before you arrive: " + ", ".join(
                    f"{x.what} valid to {hhmm(x.valid_to)}" for x in late)
            sources["warnings"] = SourceLine(state="none_issued", text=text)

        g_hits: list[WarningHit] = []
        if us and gair_envs:
            g_hits = [_hit(gair_by_id[e.hazard_id], e.entry, e.exit) for e in gair_hits
                      if e.entry < end and e.exit >= start]
            covered = any(w0 < end and w1 > start for w0, w1 in windows)
            if g_hits:
                sources["gairmet"] = SourceLine(state="flagged", text="; ".join(
                    f"high-level turbulence area crossed, {levels(h.base_ft, h.top_ft)}, "
                    f'the G-AIRMET says "{h.quoted}"' for h in g_hits))
            elif covered:
                sources["gairmet"] = SourceLine(
                    state="nothing_found",
                    text="Nothing found: no high-level turbulence area at cruise level here")
            else:
                sources["gairmet"] = _too_early(start - timedelta(hours=GAIRMET_REACH_H))

        mid = position_at(points, start + (end - start) / 2)
        near: list[ReportHit] = []
        if not reports_cover(mid.lat, mid.lon):
            sources["reports"] = SourceLine(
                state="not_covered",
                text="Not covered here (too few pilot reports mention turbulence in this region)")
        elif lead > REPORT_REACH_H:
            sources["reports"] = _too_early(start - timedelta(hours=REPORT_REACH_H))
        elif not rep_envs:
            if not any(p.source == "pirep" for p in provenance):
                provenance.append(_provenance(None, "pirep", as_of))
            sources["reports"] = SourceLine(state="stale",
                                            text=_last_update(root, "pirep", as_of))
        else:
            near = _reports_near(reports, seg, cruise_ft)
            n_flag = sum(r.flags for r in near)
            if n_flag:
                sources["reports"] = SourceLine(state="flagged", text=(
                    f"{n_flag} of {len(near)} report{'s' if len(near) != 1 else ''} within "
                    f"{CORRIDOR_NM:.0f} nm in the last {REPORT_AGE_H:.0f} h flagged; "
                    "the reports are quoted below"))
            elif near:
                sources["reports"] = SourceLine(state="nothing_found", text=(
                    f"Nothing flagged in {len(near)} report{'s' if len(near) != 1 else ''} "
                    f"within {CORRIDOR_NM:.0f} nm in the last {REPORT_AGE_H:.0f} h"))
            else:
                sources["reports"] = SourceLine(
                    state="no_reports",
                    text="No reports near this stretch; no report is not a smooth-air report")

        states = [s.state for s in sources.values()]
        speaking = [s for s in states if s in SPEAKS]
        state: HourState = (
            "flagged" if "flagged" in states else
            "nothing_found" if "nothing_found" in states else
            "too_early" if any(s in ("too_early", "none_issued", "no_reports") for s in states)
            else "stale" if "stale" in states else "not_covered")
        agreement: Literal["agree", "disagree", "single"] | None = (
            None if not speaking else "single" if len(speaking) == 1
            else "agree" if len(set(speaking)) == 1 else "disagree")
        hours.append(Hour(index=i + 1, start=start, end=end, state=state, agreement=agreement,
                          shear=shear_rec, gtg=gtg_rec, warnings=hits, expired_warnings=late, gairmets=g_hits, reports=near,
                          sources=sources))

    names = {"origin": (leg.origin.code, "climb"), "destination": (leg.destination.code, "descent")}
    near_hits = [NearAirportHit(
        end=n.end, airport=names[n.end][0], phase=names[n.end][1],  # type: ignore[arg-type]
        hazard_id=n.hazard_id, kind=n.kind, what=what(n.kind), below_cruise=n.below_cruise,
        base_ft=n.hazard_base_ft, top_ft=n.hazard_top_ft, valid_to=n.valid_to,
        quoted=quote(by_id[n.hazard_id].raw)[:600])
        for n in near_airports(flight, sigmets, NEAR_NM)]

    used: dict[str, Envelope | None] = {}
    airports = {"origin": _airport(root, leg.origin, departure, as_of, used),
                "destination": _airport(root, leg.destination, arrival, as_of, used)}
    provenance += [_provenance(used[s], s, as_of) for s in ("taf", "metar")]
    provenance.append(Provenance(source="route table", fetched_at=None, issued_at=None,
                                 url_without_secrets=vrs.ARCHIVE, licence=vrs.LICENCE))
    if sig_env is None:
        notes.append(_last_update(root, "sigmet", as_of))

    return Brief(
        status="ok", **{**base, "notes": [*notes, TRACKER, NOT_OPS]},
        summary=_summary(hours), hours=hours, near_airports=near_hits, airports=airports,
        warnings_without_area=[{"hazard_id": h.hazard_id, "quoted": quote(h.raw)[:600]}
                               for h in unplaced(sigmets) if h.valid_to >= as_of],
        provenance=provenance)
