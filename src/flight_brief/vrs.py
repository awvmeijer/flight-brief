"""The route table: VRS standing-data compiled into one small SQLite file.

Source: https://github.com/vradarserver/standing-data (CC0 1.0), schema-01: `airlines.csv`,
route files per airline (callsign to airports, no times) and airport files. The table adds
one column the source does not have, each airport's time zone, worked out once at build time
so that a brief needs only the standard library to turn a local departure time into UTC.
Airports VRS lacks come from a small supplement file, `data/airports_extra.csv` (OurAirports,
public domain), added one at a time when a real lookup fails.

Build it with `python -m flight_brief.vrs --out data/vrs.sqlite` (needs the `routes` extra
for timezonefinder, whose boundary data is from timezone-boundary-builder, ODbL).
"""

import argparse
import csv
import io
import re
import sqlite3
import tarfile
import urllib.request
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
MEMBER = re.compile(r"(?:^|/)(airlines|routes|airports)/schema-01/(?:.*/)?[^/]+\.csv$")
TzOf = Callable[[float, float], str | None]

SCHEMA = """
CREATE TABLE airlines (code TEXT, name TEXT, icao TEXT, iata TEXT);
CREATE TABLE routes (callsign TEXT PRIMARY KEY, airline TEXT, number TEXT, airports TEXT);
CREATE TABLE airports (code TEXT PRIMARY KEY, name TEXT, icao TEXT, iata TEXT, location TEXT,
                       country TEXT, lat REAL, lon REAL, tz TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX airlines_iata ON airlines (iata);
CREATE INDEX airlines_icao ON airlines (icao);
CREATE INDEX airports_iata ON airports (iata);
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


def build(files: Iterable[tuple[str, str]], out: Path, tz_of: TzOf,
          now: datetime | None = None) -> dict[str, int]:
    """Write the table to `out`, replacing any file there. Returns the row counts."""
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
            else:
                _add_airport(db, r, tz_of, "REPLACE")
    # The supplement goes in last and never replaces a row: VRS wins when it has the airport.
    before = db.total_changes
    for r in _rows(EXTRA_AIRPORTS.read_text(encoding="utf-8")):
        _add_airport(db, r, tz_of, "IGNORE")
    added = db.total_changes - before
    built = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    db.executemany("INSERT INTO meta VALUES (?,?)",
                   [("built_at", built), ("source", ARCHIVE), ("licence", LICENCE),
                    ("supplement", f"{SUPPLEMENT}: {added} airport{'s' if added != 1 else ''} "
                                   "that VRS does not have")])
    counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
              for t in ("airlines", "routes", "airports")}
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
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

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

    def airport(self, code: str) -> Airport | None:
        """An airport by ICAO code (four characters) or IATA code (three)."""
        column = "iata" if len(code) == 3 else "code"
        row = self.db.execute(
            "SELECT code, name, icao, iata, location, country, lat, lon, tz FROM airports "  # noqa: S608
            f"WHERE {column} = ? ORDER BY code", (code.upper(),)).fetchone()
        if row is None:
            return None
        keys = ("code", "name", "icao", "iata", "location", "country", "lat", "lon", "tz")
        return Airport(**dict(zip(keys, row, strict=True)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the route table from VRS standing-data.")
    parser.add_argument("--out", type=Path, default=Path("data/vrs.sqlite"))
    parser.add_argument("--from-folder", type=Path,
                        help="a local checkout of the VRS repository, instead of downloading it")
    args = parser.parse_args(argv)
    try:
        from timezonefinder import TimezoneFinder
    except ImportError:
        parser.error("needs timezonefinder: run with `uv run --extra routes python -m flight_brief.vrs`")
    finder = TimezoneFinder()

    def tz_of(lat: float, lon: float) -> str | None:
        return finder.timezone_at(lat=lat, lng=lon)

    if args.from_folder:
        counts = build(from_folder(args.from_folder), args.out, tz_of)
    else:
        request = urllib.request.Request(ARCHIVE, headers={"User-Agent": USER_AGENT})  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
            counts = build(from_archive(resp), args.out, tz_of)
    print(f"{args.out}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
