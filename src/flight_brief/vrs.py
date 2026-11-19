"""The route table: VRS standing-data compiled into one small SQLite file.

Source: https://github.com/vradarserver/standing-data (CC0 1.0), schema-01: `airlines.csv`,
route files per airline (callsign to airports, no times), airport files and the `model-type`
files (ICAO aircraft type codes with their engine kind). The table adds
one column the source does not have, each airport's time zone, worked out once at build time
so that a brief needs only the standard library to turn a local departure time into UTC.
Airports VRS lacks come from a small supplement file, `data/airports_extra.csv` (OurAirports,
public domain), and routes it lacks from `data/routes_extra.csv` (single facts typed by hand).
Both grow one row at a time, when a real lookup fails. Runway directions, one row per runway
end, come from OurAirports `runways.csv` (public domain), for the airports the table has.

Build it with `python -m flight_brief.vrs --out data/vrs.sqlite` (needs the `routes` extra
for timezonefinder, whose boundary data is from timezone-boundary-builder, ODbL).
"""

import argparse
import csv
import io
import re
import sqlite3
import tarfile
import unicodedata
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

ARCHIVE = "https://github.com/vradarserver/standing-data/archive/refs/heads/main.tar.gz"
USER_AGENT = "flight-brief/0.1 (route table build)"
LICENCE = "VRS standing-data, CC0 1.0"
# Airports VRS does not have, in VRS's airport columns. See `data/README.md` for the source.
EXTRA_AIRPORTS = Path(__file__).parent / "data" / "airports_extra.csv"
SUPPLEMENT = "OurAirports, public domain (The Unlicense)"
# Routes VRS does not have, in VRS's route columns: single facts typed by hand.
EXTRA_ROUTES = Path(__file__).parent / "data" / "routes_extra.csv"
# Runway ends with their true headings: OurAirports `runways.csv`, read whole at build time.
RUNWAYS = "https://davidmegginson.github.io/ourairports-data/runways.csv"
RUNWAYS_LICENCE = "OurAirports runways, public domain (The Unlicense)"
RUNWAY_IDENT = re.compile(r"(\d{1,2})[LRC]?$")
MEMBER = re.compile(r"(?:^|/)(airlines|routes|airports|model-type)/schema-01/(?:.*/)?[^/]+\.csv$")
TzOf = Callable[[float, float], str | None]
AIRPORT_KEYS = ("code", "name", "icao", "iata", "location", "country", "lat", "lon", "tz")

SCHEMA = """
CREATE TABLE airlines (code TEXT, name TEXT, icao TEXT, iata TEXT);
CREATE TABLE routes (callsign TEXT PRIMARY KEY, airline TEXT, number TEXT, airports TEXT);
CREATE TABLE airports (code TEXT PRIMARY KEY, name TEXT, icao TEXT, iata TEXT, location TEXT,
                       country TEXT, lat REAL, lon REAL, tz TEXT);
CREATE TABLE model_types (icao TEXT PRIMARY KEY, engine TEXT, species TEXT);
CREATE TABLE runways (airport TEXT, ident TEXT, heading REAL, length_ft INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX airlines_iata ON airlines (iata);
CREATE INDEX airlines_icao ON airlines (icao);
CREATE INDEX airports_iata ON airports (iata);
CREATE INDEX runways_airport ON runways (airport);
"""


class Airline(BaseModel):
    code: str  # the code VRS callsigns start with: the ICAO designator when there is one
    name: str
    icao: str
    iata: str


class Airport(BaseModel):
    code: str
    name: str
    icao: str
    iata: str
    location: str
    country: str
    lat: float
    lon: float
    tz: str | None  # IANA zone name; None when the build could not place the airport


class Runway(BaseModel):
    """One end of a runway: the direction an aircraft points when it uses that end."""
    ident: str  # 09, 27L
    heading: float  # degrees true
    length_ft: int | None


class RouteFlight(BaseModel):
    """One flight number that flies a given pair of airports, as a passenger writes it."""
    number: str  # the airline's IATA code and the number, KL791; the ICAO code without one
    airline: str  # the airline's name
    callsign: str
    leg: int  # 1-based: which leg of the callsign's route the pair is


def fold(text: str) -> str:
    """Text as a search compares it: no accents, no case, punctuation read as a space."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.casefold()).split())


def _rows(text: str) -> Iterator[dict[str, str]]:
    return csv.DictReader(io.StringIO(text.lstrip("﻿")))


def from_folder(root: Path) -> Iterator[tuple[str, str]]:
    """(kind, csv text) for every schema-01 file under a checkout of the VRS repository."""
    for path in sorted(root.rglob("*.csv")):
        match = MEMBER.search(path.as_posix())
        if match:
            yield match.group(1), path.read_text(encoding="utf-8")


def from_archive(stream: io.BufferedIOBase) -> Iterator[tuple[str, str]]:
    """The same, read straight from the repository's tar.gz without unpacking it to disk."""
    with tarfile.open(fileobj=stream, mode="r|gz") as tar:
        for member in tar:
            match = MEMBER.search(member.name)
            if match and member.isfile():
                data = tar.extractfile(member)
                if data is not None:
                    yield match.group(1), data.read().decode("utf-8")


def runway_ends(text: str) -> Iterator[tuple[str, str, float, int | None]]:
    """(airport, ident, heading in degrees true, length in feet) for each end of every open
    runway in an OurAirports `runways.csv`. An end without a heading takes ten times its
    number (09L is 90) when its name is a runway number, and is left out otherwise."""
    for r in _rows(text):
        if (r.get("closed") or "").strip() == "1" or not (r.get("airport_ident") or "").strip():
            continue
        length = (r.get("length_ft") or "").strip()
        for end in ("le", "he"):
            ident = (r.get(f"{end}_ident") or "").strip().upper()
            stated = (r.get(f"{end}_heading_degT") or "").strip()
            number = RUNWAY_IDENT.fullmatch(ident)
            try:
                heading = float(stated)
            except ValueError:
                if not number or not 1 <= int(number[1]) <= 36:
                    continue
                heading = int(number[1]) * 10.0
            if ident:
                yield (r["airport_ident"].strip().upper(), ident, heading % 360.0,
                       int(float(length)) if length else None)


def build(files: Iterable[tuple[str, str]], out: Path, tz_of: TzOf,
          now: datetime | None = None, runways: str | None = None) -> dict[str, int]:
    """Write the table to `out`, replacing any file there. Returns the row counts. `runways`
    is the text of an OurAirports `runways.csv`; without it the runways table stays empty."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    for kind, text in files:
        for r in _rows(text):
            if kind == "airlines":
                db.execute("INSERT INTO airlines VALUES (?,?,?,?)",
                           (r["Code"], r["Name"], r["ICAO"], r["IATA"]))
            elif kind == "routes":
                db.execute("INSERT OR REPLACE INTO routes VALUES (?,?,?,?)",
                           (r["Callsign"], r["AirlineCode"], r["Number"], r["AirportCodes"]))
            elif kind == "model-type":
                # Several rows share one type code (one per manufacturer and model name) and
                # agree on engine kind and species, so the first is kept.
                db.execute("INSERT OR IGNORE INTO model_types VALUES (?,?,?)",
                           (r["ICAO"], r["EngineTypeCode"], r["SpeciesCode"]))
            else:
                _add_airport(db, r, tz_of, "REPLACE")
    # The supplement goes in last and never replaces a row: VRS wins when it has the airport.
    before = db.total_changes
    for r in _rows(EXTRA_AIRPORTS.read_text(encoding="utf-8")):
        _add_airport(db, r, tz_of, "IGNORE")
    added = db.total_changes - before
    before = db.total_changes
    for r in _rows(EXTRA_ROUTES.read_text(encoding="utf-8")):
        db.execute("INSERT OR IGNORE INTO routes VALUES (?,?,?,?)",
                   (r["Callsign"], r["AirlineCode"], r["Number"], r["AirportCodes"]))
    routes = db.total_changes - before
    # Runways only for the airports in the table: the rest of the file is never asked about.
    known = {code for (code,) in db.execute("SELECT code FROM airports")}
    ends = [e for e in runway_ends(runways or "") if e[0] in known]
    db.executemany("INSERT INTO runways VALUES (?,?,?,?)", ends)
    built = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    db.executemany("INSERT INTO meta VALUES (?,?)",
                   [("built_at", built), ("source", ARCHIVE), ("licence", LICENCE),
                    ("supplement", f"{SUPPLEMENT}: {added} airport{'s' if added != 1 else ''} "
                                   "that VRS does not have"),
                    ("supplement_routes", f"typed by hand from airline schedules: {routes} "
                                          f"route{'s' if routes != 1 else ''} that VRS does "
                                          "not have"),
                    ("runways", f"{RUNWAYS_LICENCE}: {len(ends)} runway "
                                f"end{'s' if len(ends) != 1 else ''}"),
                    ("runways_source", RUNWAYS if runways is not None else "")])
    counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
              for t in ("airlines", "routes", "airports", "runways")}
    db.commit()
    db.close()
    tmp.replace(out)
    return counts


def _add_airport(db: sqlite3.Connection, r: dict[str, str], tz_of: TzOf, clash: str) -> None:
    """Insert one airport row unless VRS lists it under another key: the supplement must not
    shadow a VRS airport that has the same ICAO or IATA code."""
    if clash == "IGNORE" and db.execute(
            "SELECT 1 FROM airports WHERE code = ? OR icao = ? OR (iata = ? AND iata <> '')",
            (r["Code"], r["ICAO"], r["IATA"])).fetchone():
        return
    lat, lon = float(r["Latitude"]), float(r["Longitude"])
    db.execute(f"INSERT OR {clash} INTO airports VALUES (?,?,?,?,?,?,?,?,?)",  # noqa: S608
               (r["Code"], r["Name"], r["ICAO"], r["IATA"], r["Location"],
                r["CountryISO2"], lat, lon, tz_of(lat, lon)))


class RouteTable:
    """Read-only lookups on a built table."""

    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(
                f"no route table at {path}; build one with: python -m flight_brief.vrs --out {path}")
        self.path = path
        # Read-only, and read from whichever thread serves a request: a hosted copy does not
        # promise the thread that opened it.
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self._airports: list[tuple[Airport, str, str, int]] | None = None

    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def airlines(self, code: str) -> list[Airline]:
        """Every airline a passenger's code can mean: two characters are an IATA code, which
        several airlines may share; three letters are an ICAO designator."""
        column = "iata" if len(code) == 2 else "icao"
        rows = self.db.execute(
            f"SELECT code, name, icao, iata FROM airlines WHERE {column} = ? ORDER BY code",  # noqa: S608
            (code,)).fetchall()
        return [Airline(code=c, name=n, icao=i, iata=a) for c, n, i, a in rows]

    def route(self, callsign: str) -> list[str] | None:
        """The airports a callsign flies between, in order: two, or more for a multi-leg number."""
        row = self.db.execute("SELECT airports FROM routes WHERE callsign = ?",
                              (callsign,)).fetchone()
        return row[0].split("-") if row else None

    def model_type(self, icao: str) -> tuple[str, str] | None:
        """(engine code, species code) of an ICAO aircraft type designator, from VRS
        `model-type`: engine J jet, T turboprop, P piston; species L is a landplane."""
        try:
            row = self.db.execute("SELECT engine, species FROM model_types WHERE icao = ?",
                                  (icao,)).fetchone()
        except sqlite3.OperationalError as exc:
            raise ValueError(f"The route table at {self.path} has no aircraft types. Rebuild "
                             f"it with: python -m flight_brief.vrs --out {self.path}") from exc
        return (row[0], row[1]) if row else None

    def model_family(self, like: str) -> list[tuple[str, str, str]]:
        """(designator, engine code, species code) of every aircraft type whose designator
        fits the pattern, where `_` stands for any one character: `B77_` is the 777s."""
        try:
            return self.db.execute("SELECT icao, engine, species FROM model_types "
                                   "WHERE icao LIKE ? ORDER BY icao", (like,)).fetchall()
        except sqlite3.OperationalError:
            return []

    def runways(self, icao: str) -> list[Runway]:
        """Every runway end of an airport, by its ICAO code, longest first. Empty when the
        airport has none listed, and when the table was built before it carried runways."""
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                               "AND name = 'runways'").fetchone():
            return []
        rows = self.db.execute(
            "SELECT ident, heading, length_ft FROM runways WHERE airport = ? "
            "ORDER BY length_ft DESC, ident", (icao.upper(),)).fetchall()
        return [Runway(ident=i, heading=h, length_ft=n) for i, h, n in rows]

    def airport(self, code: str) -> Airport | None:
        """An airport by ICAO code (four characters) or IATA code (three)."""
        column = "iata" if len(code) == 3 else "code"
        row = self.db.execute(
            "SELECT code, name, icao, iata, location, country, lat, lon, tz FROM airports "  # noqa: S608
            f"WHERE {column} = ? ORDER BY code", (code.upper(),)).fetchone()
        if row is None:
            return None
        return Airport(**dict(zip(AIRPORT_KEYS, row, strict=True)))

    def flights_between(self, origin_icao: str, destination_icao: str) -> list[RouteFlight]:
        """Every flight number whose route has the two airports as consecutive stops, by
        airline name and then number. The table has no times and no days: a number listed
        here flew this pair at some time, not necessarily on the day asked about. This reads
        the whole route column once, about 65 ms on the full table."""
        pair = [origin_icao.upper(), destination_icao.upper()]
        rows = self.db.execute(
            "SELECT callsign, airline, number, airports FROM routes WHERE instr(airports, ?) > 0",
            ("-".join(pair),)).fetchall()
        found = []
        for callsign, code, number, airports in rows:
            stops = airports.split("-")
            leg = next((i + 1 for i in range(len(stops) - 1) if stops[i:i + 2] == pair), None)
            if leg is None:
                continue
            line = self.db.execute("SELECT name, icao, iata FROM airlines WHERE code = ? "
                                   "ORDER BY name", (code,)).fetchone()
            name, icao, iata = line or (code, code, "")
            found.append(RouteFlight(number=(iata or icao or code) + number, airline=name,
                                     callsign=callsign, leg=leg))
        digits = re.compile(r"\d+")

        def order(f: RouteFlight) -> tuple[str, int, str]:
            n = digits.search(f.number[2:])
            return f.airline.casefold(), int(n[0]) if n else 0, f.number

        return sorted(found, key=order)

    def _listed(self) -> list[tuple[Airport, str, str, int]]:
        """Every airport with an IATA code, with its city and name folded for a search and
        the number of routes that use it. Read once, on the first search: about 0.4 s on the
        full table, most of it counting the routes."""
        if self._airports is None:
            used: Counter[str] = Counter()
            for (airports,) in self.db.execute("SELECT airports FROM routes"):
                used.update(airports.split("-"))
            rows = self.db.execute(
                "SELECT code, name, icao, iata, location, country, lat, lon, tz FROM airports "
                "WHERE iata <> '' ORDER BY code").fetchall()
            places = [Airport(**dict(zip(AIRPORT_KEYS, row, strict=True))) for row in rows]
            self._airports = [(p, fold(p.location), fold(p.name), used[p.code]) for p in places]
        return self._airports

    def _ranked(self, text: str) -> list[tuple[int, Airport]]:
        """(rank, airport), best first. Rank 0 is the airport's own code, 1 the city by its
        whole name, 2 the city by its first letters, 3 the airport's name containing the
        text. Within a rank the airport on more routes comes first."""
        typed = fold(text)
        if not typed:
            return []
        code = typed.upper()
        ranked = []
        for place, city, name, used in self._listed():
            if code in (place.iata, place.icao or place.code):
                rank = 0
            elif city == typed:
                rank = 1
            elif len(typed) >= 3 and city.startswith(typed):
                rank = 2
            elif len(typed) >= 3 and typed in name:
                rank = 3
            else:
                continue
            ranked.append((rank, -used, place.name, place))
        ranked.sort(key=lambda r: r[:3])
        if ranked and ranked[0][0] == 0:  # a code was typed: a name that happens to hold
            ranked = [r for r in ranked if r[0] < 3]  # those letters is not what was meant
        return [(rank, place) for rank, _, _, place in ranked]

    def find_airports(self, text: str, limit: int = 8) -> list[Airport]:
        """The airports a passenger can mean by what they typed, best first: a three-letter
        IATA or four-letter ICAO code that exists, then the city by its whole name, the city
        by its first letters, and the airport's name containing the text. Accents and case
        do not count. Only airports with an IATA code are listed: the ones a ticket names."""
        return [place for _, place in self._ranked(text)[:limit]]

    def best_airport(self, text: str) -> Airport | None:
        """The one airport the text means, when there is no real choice: its own code, or
        the only airport in the best rank that matched (the only one in a city of that name,
        say). None when nothing matched or several airports are as good as each other."""
        ranked = self._ranked(text)
        if not ranked:
            return None
        top = [place for rank, place in ranked if rank == ranked[0][0]]
        return top[0] if ranked[0][0] == 0 or len(top) == 1 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the route table from VRS standing-data.")
    parser.add_argument("--out", type=Path, default=Path("data/vrs.sqlite"))
    parser.add_argument("--from-folder", type=Path,
                        help="a local checkout of the VRS repository, instead of downloading it")
    parser.add_argument("--runways", type=Path,
                        help="a local copy of OurAirports runways.csv, instead of downloading it")
    args = parser.parse_args(argv)
    try:
        from timezonefinder import TimezoneFinder
    except ImportError:
        parser.error("needs timezonefinder: run with `uv run --extra routes python -m flight_brief.vrs`")
    finder = TimezoneFinder()

    def tz_of(lat: float, lon: float) -> str | None:
        return finder.timezone_at(lat=lat, lng=lon)

    if args.runways:
        runways = args.runways.read_text(encoding="utf-8")
    else:
        asked = urllib.request.Request(RUNWAYS, headers={"User-Agent": USER_AGENT})  # noqa: S310
        with urllib.request.urlopen(asked, timeout=60) as resp:  # noqa: S310
            runways = resp.read().decode("utf-8")
    if args.from_folder:
        counts = build(from_folder(args.from_folder), args.out, tz_of, runways=runways)
    else:
        request = urllib.request.Request(ARCHIVE, headers={"User-Agent": USER_AGENT})  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
            counts = build(from_archive(resp), args.out, tz_of, runways=runways)
    print(f"{args.out}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
