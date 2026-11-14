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
