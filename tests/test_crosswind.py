"""Crosswind at take-off and landing: the geometry, the runways table, and the brief."""

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from brief_helpers import SEVERITY, SQ231, brief, own_strings, store, utc
from conftest import FIXTURES
from flight_brief import vrs
from flight_brief.airport_wx import crosswind, crosswind_text, taf_wind, wind_words
from flight_brief.brief import Brief
from flight_brief.render import render
from flight_brief.service import BriefRequest, request_brief
from flight_brief.vrs import Runway

EAST_WEST = [Runway(ident="09", heading=90, length_ft=9000),
             Runway(ident="27", heading=270, length_ft=9000)]
# Sydney's runway ends, typed by hand from OurAirports `runways.csv` (headings in degrees true).
YSSY = [Runway(ident="16R", heading=168, length_ft=12999),
        Runway(ident="34L", heading=348, length_ft=12999),
        Runway(ident="07", heading=74, length_ft=8300),
        Runway(ident="25", heading=254, length_ft=8300),
        Runway(ident="16L", heading=168, length_ft=7999),
        Runway(ident="34R", heading=348, length_ft=7999)]
# OurAirports' own columns. WSSS has headings; YSSY 07/25 has none and takes them from the
# runway numbers; the closed runway, the helipad and the airport not in the table are left out.
RUNWAYS_CSV = """"id","airport_ref","airport_ident","length_ft","width_ft","surface","lighted","closed","le_ident","le_latitude_deg","le_longitude_deg","le_elevation_ft","le_heading_degT","le_displaced_threshold_ft","he_ident","he_latitude_deg","he_longitude_deg","he_elevation_ft","he_heading_degT","he_displaced_threshold_ft"
1,10,"WSSS",13123,197,"PEM",1,0,"02L",,,,23,,"20R",,,,203,
2,10,"WSSS",9000,197,"ASP",1,1,"02X",,,,23,,"20X",,,,203,
3,11,"YSSY",12999,148,"ASP",1,0,"16R",,,,168,,"34L",,,,348,
4,11,"YSSY",8300,148,"ASP",1,0,"07",,,,,,"25",,,,,
5,11,"YSSY",100,100,"ASP",1,0,"H1",,,,,,,,,,,
6,12,"ZZZZ",5000,100,"ASP",1,0,"01",,,,10,,"19",,,,190,
"""


def near(value: float | None, expected: float) -> bool:
    return value is not None and abs(value - expected) < 0.01


# --- geometry ------------------------------------------------------------------------------

def test_a_wind_straight_across_is_all_crosswind() -> None:
    found = crosswind(180, 20, None, EAST_WEST)
    assert found is not None and found.runway in ("09", "27")
    assert near(found.crosswind_kt, 20) and near(found.headwind_kt, 0)
    assert found.gust_crosswind_kt is None and found.gust_headwind_kt is None


def test_a_wind_down_the_runway_is_all_headwind() -> None:
    found = crosswind(270, 15, None, EAST_WEST)
    assert found is not None and found.runway == "27"
    assert near(found.crosswind_kt, 0) and near(found.headwind_kt, 15)


def test_a_tailwind_on_one_end_picks_the_opposite_end() -> None:
    found = crosswind(60, 20, None, EAST_WEST)  # a tailwind on 27, so 09 is the one
    assert found is not None and found.runway == "09"
    assert near(found.crosswind_kt, 10) and near(found.headwind_kt, 17.32)
    alone = crosswind(60, 20, None, EAST_WEST[1:])  # only 27 is listed: the tailwind shows
    assert alone is not None and alone.runway == "27" and near(alone.headwind_kt, -17.32)
    assert near(alone.crosswind_kt, 10)


def test_gusts_are_split_on_the_same_runway() -> None:
    found = crosswind(300, 20, 30, EAST_WEST)
    assert found is not None and found.runway == "27"
    assert near(found.crosswind_kt, 10) and near(found.gust_crosswind_kt, 15)
    assert near(found.headwind_kt, 17.32) and near(found.gust_headwind_kt, 25.98)
    assert wind_words(300, 20, 30) == "300 at 20 kt, gusts 30"
    assert crosswind_text(found, "TAF") == (
        "Crosswind about 10 kt (gusts 15) on runway 27, the runway best aligned with the "
        "forecast wind. The runway in use is not known in advance.")


@pytest.mark.parametrize("wdir", ["VRB", 0])
def test_a_variable_wind_has_no_runway_and_counts_whole(wdir: Any) -> None:
    found = crosswind(wdir, 6, 14, EAST_WEST)
    assert found is not None and found.variable and found.runway is None
    assert found.crosswind_kt == 6 and found.gust_crosswind_kt == 14
    assert found.headwind_kt is None and found.gust_headwind_kt is None
    assert wind_words(wdir, 6, 14) == "variable at 6 kt, gusts 14"
    assert "variable" in crosswind_text(found, "METAR")


def test_a_calm_wind_is_zero() -> None:
    found = crosswind(0, 0, None, EAST_WEST)
    assert found is not None and not found.variable and found.runway is None
    assert found.crosswind_kt == 0 and found.headwind_kt == 0
    assert wind_words(0, 0, None) == "calm"


def test_runways_of_5000_ft_are_preferred_when_there_are_any() -> None:
    short = Runway(ident="18", heading=180, length_ft=3000)
    found = crosswind(180, 20, None, [*EAST_WEST, short])  # 18 fits the wind but is short
    assert found is not None and found.runway != "18" and near(found.crosswind_kt, 20)
    only_short = [short, Runway(ident="09", heading=90, length_ft=4999),
                  Runway(ident="36", heading=360, length_ft=None)]
    found = crosswind(180, 20, None, only_short)  # no long runway: all of them count
    assert found is not None and found.runway == "18" and near(found.headwind_kt, 20)


def test_parallel_runways_go_to_the_longer_one() -> None:
    found = crosswind(170, 10, None, YSSY)
    assert found is not None and found.runway == "16R"


def test_no_speed_no_direction_or_no_runway_gives_nothing() -> None:
    assert crosswind(180, None, None, EAST_WEST) is None
    assert crosswind(None, 10, None, EAST_WEST) is None
    assert crosswind(180, 10, None, []) is None


def test_the_taf_wind_follows_the_changes() -> None:
    taf = {"fcsts": [
        {"timeFrom": 0, "timeTo": 100, "fcstChange": None, "wdir": 220, "wspd": 12, "wgst": 22},
        {"timeFrom": 20, "timeTo": 40, "fcstChange": "TEMPO", "wdir": 300, "wspd": 30, "wgst": 45},
        {"timeFrom": 50, "timeTo": 100, "fcstChange": "BECMG", "wdir": "VRB", "wspd": 3,
         "wgst": None},
        {"timeFrom": 70, "timeTo": 100, "fcstChange": "FM", "wdir": 60, "wspd": 12, "wgst": None}]}
    assert taf_wind(taf, 30) == (220, 12, 22)  # what prevails, not what comes at times
    assert taf_wind(taf, 60) == ("VRB", 3, None)  # a change of wind brings its direction
    assert taf_wind(taf, 80) == (60, 12, None)
    assert taf_wind(taf, -5) is None  # before the TAF begins it states no wind


# --- the runways table ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def with_runways(tmp_path_factory: pytest.TempPathFactory) -> vrs.RouteTable:
    out = tmp_path_factory.mktemp("vrs-runways") / "vrs.sqlite"
    counts = vrs.build(vrs.from_folder(FIXTURES / "vrs"), out, lambda lat, lon: None,
                       runways=RUNWAYS_CSV)
    assert counts["runways"] == 6
    return vrs.RouteTable(out)


def test_the_runways_table_has_one_row_per_open_runway_end(with_runways: vrs.RouteTable) -> None:
    assert with_runways.runways("WSSS") == [Runway(ident="02L", heading=23, length_ft=13123),
                                            Runway(ident="20R", heading=203, length_ft=13123)]
    # Longest first; 07 and 25 had no heading, so ten times the runway number stands in.
    assert [(r.ident, r.heading, r.length_ft) for r in with_runways.runways("yssy")] == [
        ("16R", 168, 12999), ("34L", 348, 12999), ("07", 70, 8300), ("25", 250, 8300)]
    assert with_runways.runways("ZZZZ") == [] and with_runways.runways("EGLL") == []
    assert with_runways.meta("runways") == f"{vrs.RUNWAYS_LICENCE}: 6 runway ends"
    assert with_runways.meta("runways_source") == vrs.RUNWAYS


def test_a_table_built_without_the_file_has_no_runways(table: vrs.RouteTable) -> None:
    assert table.runways("WSSS") == []
    assert table.meta("runways") == f"{vrs.RUNWAYS_LICENCE}: 0 runway ends"


def test_an_older_table_without_runways_still_answers(
        with_runways: vrs.RouteTable, tmp_path: Path) -> None:
    old = tmp_path / "old.sqlite"
    old.write_bytes(with_runways.path.read_bytes())
    db = sqlite3.connect(old)
    db.execute("DROP TABLE runways")
    db.commit()
    db.close()
    older = vrs.RouteTable(old)
    assert older.runways("WSSS") == []
    assert older.airport("WSSS") is not None


# --- the brief -----------------------------------------------------------------------------

def test_the_brief_gives_the_crosswind_from_the_taf_for_the_landing(table: vrs.RouteTable) -> None:
    b = brief(table, *SQ231, runways={"YSSY": YSSY})
    # The TAF for the landing: BECMG 0406/0407 06012KT. Runway 07 points 074 true, 14 degrees
    # off the wind: 12 kt x sin 14 is 2.9 across, 12 kt x cos 14 is 11.6 along.
    cross = b.airports["destination"].crosswind
    assert cross is not None and cross.basis == "TAF" and cross.wind == "060 at 12 kt"
    assert (cross.runway, cross.crosswind_kt, cross.headwind_kt) == ("07", 3, 12)
    assert cross.gust_crosswind_kt is None
    assert cross.text == ("Crosswind about 3 kt on runway 07, the runway best aligned with the "
                          "forecast wind. The runway in use is not known in advance.")
    assert b.airports["origin"].crosswind is None  # no runways were given for Singapore
    assert f"  Crosswind      {cross.text}" in render(b).splitlines()
    record = b.model_dump(mode="json")["airports"]
    assert record["destination"]["crosswind"]["runway"] == "07"
    assert not any(SEVERITY.search(s) for s in own_strings(record))
    assert not any(w in cross.text.lower() for w in ("strong", "safe"))


def test_without_runways_the_brief_has_no_crosswind(table: vrs.RouteTable) -> None:
    b = brief(table, *SQ231)
    assert b.airports["origin"].crosswind is None
    assert b.airports["destination"].crosswind is None
    assert "Crosswind" not in render(b)


def test_the_service_reads_the_runways_from_the_table(with_runways: vrs.RouteTable) -> None:
    code, day, clock, as_of = SQ231
    # This table was built without time zones, so the time carries Singapore's offset.
    b = request_brief(BriefRequest(code=code, day=day, dep=f"{clock}+08:00", leg=1),
                      with_runways, FIXTURES / "snapshots", utc(as_of))
    assert isinstance(b, Brief), b
    cross = b.airports["destination"].crosswind
    # The tiny table has 07 at 070 (from its number): 10 degrees off a wind from 060.
    assert cross is not None
    assert (cross.runway, cross.crosswind_kt, cross.headwind_kt) == ("07", 2, 12)


def test_the_metar_stands_in_only_close_to_the_moment(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    at = utc("2026-11-03T23:00")
    wsss = [Runway(ident="02L", heading=23, length_ft=13123),
            Runway(ident="20R", heading=203, length_ft=13123)]

    def metar(seen: Any) -> dict[str, Any]:
        return {"icaoId": "WSSS", "obsTime": int(seen.timestamp()), "wdir": 173, "wspd": 10,
                "wgst": 20, "rawOb": "METAR WSSS 17310G20KT"}

    fresh = store(tmp_path / "fresh", at, metar=[metar(at - timedelta(minutes=30))])
    soon = brief(table, "SQ231", "2026-11-04", "08:00", at, root=fresh, runways={"WSSS": wsss})
    cross = soon.airports["origin"].crosswind  # leaves an hour after `at`: the METAR stands
    assert cross is not None and cross.basis == "METAR" and cross.wind == "173 at 10 kt, gusts 20"
    # Runway 20R points 203 true, 30 degrees off the wind: half of it blows across.
    assert (cross.runway, cross.crosswind_kt, cross.headwind_kt, cross.gust_crosswind_kt) == (
        "20R", 5, 9, 10)
    assert cross.text == ("Crosswind about 5 kt (gusts 10) on runway 20R, the runway best "
                          "aligned with the wind now. The runway in use is not known in advance.")
    later = brief(table, "SQ231", "2026-11-04", "12:00", at, root=fresh, runways={"WSSS": wsss})
    assert later.airports["origin"].crosswind is None  # five hours off: the METAR says nothing
    aged = store(tmp_path / "aged", at, metar=[metar(at - timedelta(hours=5))])
    old = brief(table, "SQ231", "2026-11-04", "08:00", at, root=aged, runways={"WSSS": wsss})
    assert old.airports["origin"].crosswind is None  # the observation itself is too old
