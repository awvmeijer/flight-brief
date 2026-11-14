import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from brief_helpers import SEVERITY, STORE
from flight_brief import airport_wx, cli, vrs
from flight_brief.awc import clean, describe, stamp
from flight_brief.brief import in_conus, reports_cover
from flight_brief.gairmet import from_gairmet


def run(table: vrs.RouteTable, *args: str) -> list[str]:
    return [*args, "--snapshots", str(STORE), "--routes", str(table.path)]


def test_cli_prints_a_brief_offline(table: vrs.RouteTable, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(run(table, "SQ231", "2026-11-04", "--dep", "08:00", "--as-of", "2026-11-03T23:00"))
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("SQ231 . Singapore to Sydney . leg 1 . WSSS to YSSY . callsign SIA231")
    assert "1 flagged stretch (hour 1)" in out and "Not for flight operations" in out


def test_cli_json_is_the_brief_record(table: vrs.RouteTable, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(run(table, "sq0231", "2026-11-04", "--dep", "08:00", "--as-of", "2026-11-03T23:00Z",
                 "--json"))
    rec = json.loads(capsys.readouterr().out)
    assert rec["status"] == "ok" and rec["identity"] == {
        "code": "SQ231", "date": "2026-11-04", "leg": 1, "issued_at": "2026-11-03T23:00:00Z"}
    assert rec["hours"][0]["state"] == "flagged"


@pytest.mark.parametrize(("args", "status", "says"), [
    (["U28001", "2026-11-04", "--dep", "08:00"], "could_not_map", "Couldn't match that flight number"),
    (["BA10A", "2026-11-04", "--dep", "08:00"], "choose_airline", "Say which airline"),
    (["QF1", "2026-11-04", "--dep", "08:00"], "choose_leg", "leg 2: WSSS to EGLL"),
    (["hello", "2026-11-04", "--dep", "08:00"], "invalid", "does not look like a flight number"),
    (["SQ231", "4 Nov", "--dep", "08:00"], "invalid", "is not a date"),
    (["SQ231", "2026-11-04", "--dep", "8 am"], "invalid", "is not a time"),
    (["SQ231", "2026-11-04", "--dep", "08:00", "--from", "SIN"], "invalid", "Give both airports"),
    (["QF1", "2026-11-04", "--dep", "08:00", "--leg", "3"], "invalid", "has no leg 3"),
])
def test_cli_states_that_stop_before_a_brief(table: vrs.RouteTable, args: list[str], status: str,
                                             says: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(run(table, *args, "--as-of", "2026-11-03T23:00", "--json")) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == status and says in out["message"]


def test_cli_takes_typed_airports_a_leg_and_an_airline(
        table: vrs.RouteTable, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["2026-11-04", "--dep", "08:00", "--as-of", "2026-11-03T23:00"]
    assert cli.main(run(table, "U28001", *base, "--from", "SIN", "--to", "YSSY")) == 0
    assert "WSSS to YSSY" in capsys.readouterr().out
    assert cli.main(run(table, "QF1", *base, "--leg", "2")) == 0
    assert "leg 2 . WSSS to EGLL" in capsys.readouterr().out
    assert cli.main(run(table, "BA10A", *base, "--airline", "SHT")) == 0
    assert "callsign SHT10A" in capsys.readouterr().out


def test_cli_without_a_route_table_says_how_to_build_one(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["SQ231", "2026-11-04", "--dep", "08:00", "--snapshots", str(STORE),
                     "--routes", str(tmp_path / "none.sqlite")])
    assert code == 2 and "flight_brief.vrs" in capsys.readouterr().err


# --- G-AIRMET records ----------------------------------------------------------------------

GAIRMET = {"tag": "5W", "forecastHour": 0, "validTime": "2026-11-10T15:00:00.000Z",
           "hazard": "TURB-HI", "geometryType": "AREA", "severity": "MOD", "due_to": "MTN WAVE",
           "top": "420", "base": "180", "issueTime": 1794321900, "expireTime": 1794344400,
           "coords": [{"lat": "49.01", "lon": "-109.75"}, {"lat": "46.84", "lon": "-108.37"},
                      {"lat": "45.00", "lon": "-112.00"}, {"lat": "49.01", "lon": "-109.75"}]}


def test_gairmet_strings_become_numbers_and_a_snapshot_stands_for_three_hours() -> None:
    hz = from_gairmet(GAIRMET)
    assert (hz.base_ft, hz.top_ft, hz.kind, hz.source) == (18000, 42000, "TURB-HI", "gairmet")
    assert hz.rings[0][0] == (-109.75, 49.01)
    at = datetime(2026, 11, 10, 15, tzinfo=UTC)
    assert (hz.valid_from, hz.valid_to) == (at - timedelta(minutes=90), at + timedelta(minutes=90))
    assert hz.raw == "TURB-HI MOD DUE TO MTN WAVE"
    assert hz.covers_level(36000) and not hz.covers_level(43000)


def test_gairmet_without_a_usable_area_or_level_is_kept_without_inventing_one() -> None:
    line = from_gairmet(GAIRMET | {"geometryType": "LINE"})
    assert line.rings == []
    odd = from_gairmet(GAIRMET | {"base": "SFC", "top": None})
    assert odd.base_ft is None and odd.top_ft is None


# --- Airport weather wording ---------------------------------------------------------------

@pytest.mark.parametrize(("wx", "words"), [
    ("-RA", ["rain"]), ("+TSRA", ["thunderstorms", "rain"]), ("-SHRA BR", ["showers", "rain", "mist"]),
    ("VCTS", ["thunderstorms"]), ("FZDZ", ["freezing", "drizzle"]), ("NSW", []), (None, []),
])
def test_weather_words_never_state_an_intensity(wx: str | None, words: list[str]) -> None:
    assert airport_wx.weather_words(wx) == words
    assert not SEVERITY.search(" ".join(words))


def test_taf_at_follows_fm_becmg_and_tempo_groups() -> None:
    taf = {"fcsts": [
        {"timeFrom": 0, "timeTo": 100, "fcstChange": None, "wspd": 3, "wxString": None,
         "clouds": [{"cover": "FEW", "base": 1500}], "visib": "6+"},
        {"timeFrom": 40, "timeTo": 60, "fcstChange": "BECMG", "wspd": 10, "wgst": 22,
         "wxString": None, "clouds": []},
        {"timeFrom": 40, "timeTo": 70, "fcstChange": "TEMPO", "wxString": "+TSRA", "visib": 2,
         "clouds": [{"cover": "BKN", "base": 1500}]},
        {"timeFrom": 80, "timeTo": 100, "fcstChange": "FM", "wspd": 0, "wxString": "-RA",
         "clouds": [{"cover": "OVC", "base": 800}], "visib": "6+"},
    ]}
    assert airport_wx.taf_at(taf, 10) == "a few clouds, wind 3 kt"
    assert airport_wx.taf_at(taf, 50) == (
        "a few clouds, wind 10 kt, gusts 22 kt; at times thunderstorms, rain, mostly cloudy, "
        "visibility 2 miles")
    assert airport_wx.taf_at(taf, 75) == "a few clouds, wind 10 kt, gusts 22 kt"
    assert airport_wx.taf_at(taf, 90) == "rain, overcast, calm"


# --- AWC helpers and regions ---------------------------------------------------------------

def test_awc_helpers_point_at_sigmet_and_drop_empty_records() -> None:
    at = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
    assert stamp(at) == "20261114_0300"
    assert describe("sigmet", {"date": stamp(at)}) == (
        "https://aviationweather.gov/api/data/sigmet?date=20261114_0300")
    assert clean([{"a": 1}, {}], "sigmet") == [{"a": 1}]
    with pytest.raises(ValueError, match="expected a list"):
        clean({"status": "error"}, "sigmet")


def test_regions() -> None:
    assert in_conus(39.86, -104.67) and in_conus(37.62, -122.38 + 360)  # Denver; SFO unwrapped
    assert not in_conus(51.47, -0.46) and not in_conus(21.32, -157.92) and not in_conus(19.4, -99.1)
    assert reports_cover(21.32, -157.92) and reports_cover(45.0, 170.0) and reports_cover(35.7, 140.4)
    assert not reports_cover(1.35, 103.99) and not reports_cover(51.47, -0.46)


# --- check-again times and a typed arrival time ----------------------------------------------

def test_too_early_hours_each_carry_their_own_check_time_and_a_group_shows_the_range(
        table: vrs.RouteTable) -> None:
    from brief_helpers import brief
    from flight_brief.brief import REPORT_REACH_H, day_hhmm
    from flight_brief.render import render

    b = brief(table, "UA837", "2026-11-02", "18:00", "2026-11-02T22:00")
    early = [h for h in b.hours if h.sources["reports"].state == "too_early"]
    assert len(early) >= 2
    for h in early:
        assert h.sources["reports"].check_after == h.start - timedelta(hours=REPORT_REACH_H)
    first, last = early[0], early[-1]
    assert [h.index for h in early] == list(range(first.index, last.index + 1))
    text = render(b)
    assert (f"hours {first.index} to {last.index}: Too early to know. Check again from "
            f"{day_hhmm(first.sources['reports'].check_after)} (hour {first.index}) to "
            f"{day_hhmm(last.sources['reports'].check_after)} (hour {last.index})") in text
    assert f"to {last.index}: Too early to know. Check again after" not in text


def test_a_typed_arrival_time_sets_the_groundspeed_less_taxi(table: vrs.RouteTable) -> None:
    from brief_helpers import brief, utc
    from flight_brief.render import render

    plain = brief(table, "UA837", "2026-11-02", "18:00", "2026-11-03T01:00")
    arrival = plain.flight.departure_utc + timedelta(hours=11, minutes=30)
    b = brief(table, "UA837", "2026-11-02", "18:00", "2026-11-03T01:00", arrival=arrival)
    assert b.flight.arrival_utc == arrival and b.flight.arrival_source == "user input"
    assert plain.flight.arrival_source == "estimate"
    assert b.flight.groundspeed_kt == pytest.approx(b.flight.distance_nm / 11.0, rel=1e-3)
    assert len(b.hours) == 12 and b.hours[-1].end == arrival
    assert b.airports["destination"].at == arrival
    assert any("30 minutes" in a and "arrival" in a for a in b.flight.assumptions)
    assert "as you entered" in render(b).splitlines()[2]
    assert "Arrives about" in render(plain) and "Arrives about" not in render(b)
    with pytest.raises(ValueError, match="arrival time"):
        brief(table, "UA837", "2026-11-02", "18:00", "2026-11-03T01:00",
              arrival=utc("2026-11-03T04:00"))


def test_arrival_clock_is_the_first_such_local_time_after_departure() -> None:
    from brief_helpers import utc
    from flight_brief.brief import arrival_utc

    # San Francisco 10:40 on 14 Nov to Singapore 20:15 on 15 Nov, across the date line
    assert arrival_utc(utc("2026-11-14T18:40"), "20:15", "Asia/Singapore") == utc("2026-11-15T12:15")
    assert arrival_utc(utc("2026-11-14T18:40"), "20:15+08:00", None) == utc("2026-11-15T12:15")
    # Honolulu: arrives on the same local date it left
    assert arrival_utc(utc("2026-11-08T02:00"), "21:30", "Pacific/Honolulu") == utc("2026-11-08T07:30")


def test_cli_takes_an_arrival_time(table: vrs.RouteTable, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["SQ231", "2026-11-04", "--dep", "08:00", "--as-of", "2026-11-03T23:00"]
    assert cli.main(run(table, *base, "--arr", "19:10", "--json")) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["flight"]["arrival_utc"] == "2026-11-04T08:10:00Z"
    assert 400 < rec["flight"]["groundspeed_kt"] < 480
    assert cli.main(run(table, *base, "--arr", "19:10", "--gs", "450", "--json")) == 2
    assert "not both" in json.loads(capsys.readouterr().out)["message"]
    assert cli.main(run(table, *base, "--arr", "09:00", "--json")) == 2
    assert "arrival time" in json.loads(capsys.readouterr().out)["message"]


@pytest.mark.parametrize(("pair", "block", "ok"), [
    (("KSFO", "WSSS"), timedelta(hours=17, minutes=35), True),   # 7,340 nm as scheduled: 430 kt
    (("KSFO", "WSSS"), timedelta(hours=23, minutes=20), False),  # a typo: 321 kt
    (("KSFO", "WSSS"), timedelta(hours=21, minutes=30), True),   # slow, still a jet: 350 kt
    (("KSFO", "WSSS"), timedelta(hours=10, minutes=20), False),  # too fast
    (("KDFW", "KLAX"), timedelta(hours=4, minutes=20), True),    # 1,073 nm, turboprop range: 280 kt
    (("KDFW", "KLAX"), timedelta(hours=5, minutes=20), False),   # 222 kt
    (("KSFO", "PHNL"), timedelta(hours=7, minutes=20), True),    # 2,084 nm, jets only: 305 kt
    (("KSFO", "PHNL"), timedelta(hours=7, minutes=40), False),   # 291 kt
    (("KSFO", "KLAX"), timedelta(hours=1, minutes=55), True),    # 294 nm on a turboprop: 207 kt
    (("KSFO", "KLAX"), timedelta(hours=2, minutes=10), False),   # 176 kt
])
def test_slowest_accepted_groundspeed_depends_on_distance(
        table: vrs.RouteTable, pair: tuple[str, str], block: timedelta, ok: bool) -> None:
    from brief_helpers import utc
    from flight_brief.brief import make_flight
    from flight_brief.resolve import typed_leg

    leg = typed_leg(table, *pair)
    assert not isinstance(leg, str)
    departure = utc("2026-11-14T18:40")
    if ok:
        make_flight("XX1", leg, departure, arrival=departure + block)
    else:
        with pytest.raises(ValueError, match="arrival time does not fit a flight of"):
            make_flight("XX1", leg, departure, arrival=departure + block)


def test_refusal_says_whether_the_flight_is_too_long_or_too_short(table: vrs.RouteTable) -> None:
    from brief_helpers import utc
    from flight_brief.brief import make_flight
    from flight_brief.resolve import typed_leg

    leg = typed_leg(table, "KSFO", "WSSS")
    assert not isinstance(leg, str)
    departure = utc("2026-11-14T18:40")
    with pytest.raises(ValueError, match="23 h 20 min gate to gate is too long"):
        make_flight("UA29", leg, departure, arrival=utc("2026-11-15T18:00"))
    with pytest.raises(ValueError, match="gate to gate is too short"):
        make_flight("UA29", leg, departure, arrival=utc("2026-11-15T05:00"))


@pytest.mark.parametrize(("a", "b", "block", "ok"), [
    # Air Tahiti VT956, Papeete to Gambier on an ATR 72: 893 nm in 3 h 45 min is 275 kt
    ((-17.5537, -149.6073), (-23.0799, -134.8903), timedelta(hours=3, minutes=45), True),
    ((-17.5537, -149.6073), (-23.0799, -134.8903), timedelta(hours=4, minutes=40), False),  # 214 kt
    # Los Angeles to Seattle, 829 nm: 2 h 55 min as scheduled, 4 h 50 min is a typo (191 kt)
    ((33.9425, -118.408), (47.449, -122.309), timedelta(hours=2, minutes=55), True),
    ((33.9425, -118.408), (47.449, -122.309), timedelta(hours=4, minutes=50), False),
])
def test_a_long_turboprop_flight_is_accepted(a: tuple[float, float], b: tuple[float, float],
                                             block: timedelta, ok: bool) -> None:
    from brief_helpers import utc
    from flight_brief.brief import make_flight
    from flight_brief.resolve import Leg

    def place(code: str, at: tuple[float, float]) -> vrs.Airport:
        return vrs.Airport(code=code, name=code, icao=code, iata="", location=code, country="",
                           lat=at[0], lon=at[1], tz=None)

    leg = Leg(index=1, origin=place("AAAA", a), destination=place("BBBB", b))
    departure = utc("2026-11-17T16:30")
    if ok:
        assert make_flight("VT956", leg, departure, arrival=departure + block).groundspeed_kt > 230
    else:
        with pytest.raises(ValueError, match="gate to gate is too long"):
            make_flight("VT956", leg, departure, arrival=departure + block)


def test_cli_briefs_a_turboprop_flight_to_an_airport_from_the_supplement(
        table: vrs.RouteTable, capsys: pytest.CaptureFixture[str]) -> None:
    # Air Tahiti VT956, Papeete to Gambier: 06:30 UTC-10 to 11:15 UTC-9 is 3 h 45 min
    assert cli.main(run(table, "VT956", "2026-11-17", "--dep", "06:30", "--arr", "11:15",
                        "--from", "PPT", "--to", "GMR", "--as-of", "2026-11-17T10:00")) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("VT956 . Papeete to Gambier Islands . leg 1 . NTAA to NTGJ")
    assert "275 kt from your times" in out[1]
    assert out[2].startswith("Arrives Tue 17 Nov 11:15 UTC-9 (20:15 UTC), as you entered . 893 nm")


def test_cli_resolves_a_flight_number_from_the_route_supplement(
        table: vrs.RouteTable, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(run(table, "VT956", "2026-11-17", "--dep", "06:30", "--arr", "11:15",
                        "--as-of", "2026-11-17T10:00")) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == ("VT956 . Papeete to Gambier Islands . leg 1 . NTAA to NTGJ . "
                      "callsign VTA956")
    assert out[2].startswith("Arrives Tue 17 Nov 11:15 UTC-9 (20:15 UTC), as you entered")
