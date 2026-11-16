"""The live fetcher: what it asks AWC for, what it reuses, and what a failure leaves behind.
No test here touches the network except the one marked `network`."""

import functools
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from brief_helpers import SEVERITY, own_strings, sigmet, store
from flight_brief import awc, cli, live, vrs
from flight_brief.brief import Brief, make_flight
from flight_brief.core import snapshots
from flight_brief.core.models import PassengerFlight
from flight_brief.core.route import timeline
from flight_brief.render import render
from flight_brief.resolve import Leg, typed_leg
from flight_brief.service import BriefRequest, request_brief

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)


class Fake:
    """Stands in for `awc.get_records`: records every call, answers with small made-up records."""

    def __init__(self, fail: tuple[str, ...] = (), reports: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail, self.reports = fail, reports
        self.lock = threading.Lock()

    def __call__(self, endpoint: str, params: dict[str, str]) -> list[dict[str, Any]]:
        with self.lock:
            self.calls.append((endpoint, dict(params)))
        if endpoint in self.fail:
            raise TimeoutError("timed out")
        if endpoint == "sigmet":
            return [sigmet([], NOW, NOW + timedelta(hours=4))]
        if endpoint == "taf":
            return [{"icaoId": i, "validTimeTo": (NOW + timedelta(hours=24)).timestamp(),
                     "rawTAF": f"TAF {i} 1618/1718 VRB03KT", "issueTime": NOW.isoformat(),
                     "fcsts": [{"timeFrom": 0, "timeTo": 2e10, "fcstChange": None, "wspd": 3}]}
                    for i in params["ids"].split(",") if i != "NTGJ"]
        if endpoint == "metar":
            return [{"icaoId": i, "obsTime": int(NOW.timestamp()) - 600, "rawOb": f"{i} 161750Z",
                     "wspd": 5, "temp": 12} for i in params["ids"].split(",") if i != "NTGJ"]
        if endpoint == "pirep" and self.reports:
            return list(self.reports(params))
        return []

    def of(self, endpoint: str) -> list[dict[str, str]]:
        return [p for e, p in self.calls if e == endpoint]


def flight(table: vrs.RouteTable, a: str, b: str, departs: datetime) -> tuple[Leg, PassengerFlight]:
    leg = typed_leg(table, a, b)
    assert not isinstance(leg, str)
    return leg, make_flight("XX1", leg, departs)


def boxes(fake: Fake) -> list[live.Box]:
    return sorted(tuple(int(v) for v in p["bbox"].split(",")) for p in fake.of("pirep"))  # type: ignore[misc]


# --- what is asked ---------------------------------------------------------------------------

def test_a_us_flight_asks_each_source_with_the_right_parameters(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    leg, fl = flight(table, "KSFO", "KJFK", NOW + timedelta(hours=1))
    fake = Fake()
    live.fetch(tmp_path, leg, fl, NOW, get=fake)

    assert fake.of("sigmet") == [{}]  # one worldwide call
    assert sorted(fake.of("gairmet"), key=lambda p: int(p["fore"])) == [
        {"hazard": "turb-hi", "fore": str(h)} for h in (0, 3, 6, 9, 12)]
    assert fake.of("taf") == [{"ids": "KJFK,KSFO"}] and fake.of("metar") == [{"ids": "KJFK,KSFO"}]
    reports = fake.of("pirep")
    assert 1 <= len(reports) <= 4 and all(p["age"] == "3" and set(p) == {"bbox", "age"}
                                          for p in reports)
    assert not any("date" in p for _, p in fake.calls)  # live: as AWC has it now
    assert len(fake.calls) == 8 + len(reports)

    # Every point of the route, 100 nm either side, is inside one of the boxes.
    for p in timeline(fl):
        assert any(a <= p.lat - 1.67 and p.lat + 1.67 <= c and b <= p.lon - 1.67 and p.lon + 1.67 <= d
                   for a, b, c, d in boxes(fake)), (p.lat, p.lon)

    saved = {s: list(snapshots.envelopes(tmp_path, s)) for s in ("sigmet", "gairmet", "pirep", "taf")}
    assert [e.tag for e in saved["gairmet"]] == ["f00", "f03", "f06", "f09", "f12"]
    assert all(e.origin == "live" and e.as_of == NOW for es in saved.values() for e in es)
    assert saved["sigmet"][0].request == "https://aviationweather.gov/api/data/sigmet"
    assert saved["pirep"][0].tag == "b" + "_".join(map(str, boxes(fake)[0]))
    # An answer with no reports is still an answer, so the engine can say "no reports".
    assert {e.status for e in saved["pirep"]} == {"ok"} and saved["pirep"][0].count == 0
    assert {e.status for e in saved["gairmet"]} == {"empty"}


def test_outside_the_us_and_the_report_regions_only_three_sources_are_asked(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    leg, fl = flight(table, "WSSS", "YSSY", NOW + timedelta(hours=1))
    fake = Fake()
    live.fetch(tmp_path, leg, fl, NOW, get=fake)
    assert sorted(e for e, _ in fake.calls) == ["metar", "sigmet", "taf"]
    assert fake.of("taf") == [{"ids": "WSSS,YSSY"}]


def test_reports_are_asked_only_for_the_hours_within_their_reach(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    # Ten hours out no hour is within the reports' six: G-AIRMETs still, no pilot reports.
    leg, fl = flight(table, "KSFO", "KJFK", NOW + timedelta(hours=10))
    fake = Fake()
    live.fetch(tmp_path, leg, fl, NOW, get=fake)
    assert not fake.of("pirep") and len(fake.of("gairmet")) == 5

    # Four hours out only the first two hours of the flight are: the boxes stop mid-country.
    leg, fl = flight(table, "KSFO", "KJFK", NOW + timedelta(hours=4))
    assert len(live.report_segments(timeline(fl), NOW)) == 2
    east = max(d for _, _, _, d in live.report_boxes(timeline(fl), NOW))
    assert -110 < east < -95


def test_a_route_that_leaves_the_report_regions_is_boxed_only_inside_them(
        table: vrs.RouteTable) -> None:
    # Los Angeles to Sydney: the North Pacific region ends at 20N, and Hawaii is passed wide.
    leg, fl = flight(table, "KLAX", "YSSY", NOW + timedelta(minutes=30))
    found = live.report_boxes(timeline(fl), NOW)
    assert found and all(lat1 > 15 for _, _, lat1, _ in found)
    assert min(lat0 for lat0, _, _, _ in found) >= 5


# --- boxes -----------------------------------------------------------------------------------

def test_a_route_across_the_antimeridian_is_boxed_on_both_sides(table: vrs.RouteTable) -> None:
    leg, fl = flight(table, "RJAA", "KSFO", NOW + timedelta(minutes=30))
    found = live.report_boxes(timeline(fl), NOW)
    assert all(-180 <= b < d <= 180 and a < c for a, b, c, d in found)
    assert any(d == 180 for _, _, _, d in found) and any(b == -180 for _, b, _, _ in found)
    assert len(found) <= 5  # hours share boxes over the ocean


def test_wrap_and_halves() -> None:
    assert live._wrap(30.2, 170.5, 40.9, 189.1) == [(30, 170, 41, 180), (30, -180, 41, -170)]
    assert live._wrap(30, 181, 41, 190) == [(30, -179, 41, -170)]
    assert live._wrap(30, -200, 41, -190) == [(30, 160, 41, 170)]
    assert live._wrap(-5, 0, 5, 400) == [(-5, -180, 5, 180)]
    assert live.halves((30, -110, 40, -90)) == [(30, -110, 40, -100), (30, -100, 40, -90)]
    assert live.halves((30, -91, 40, -90)) == [(30, -91, 35, -90), (35, -91, 40, -90)]
    assert live.halves((30, -91, 31, -90)) == []


def test_a_reply_at_the_cap_is_set_aside_and_the_box_fetched_in_halves(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    leg, fl = flight(table, "KSFO", "KJFK", NOW + timedelta(hours=1))
    whole = live.report_boxes(timeline(fl), NOW)[0]

    def reports(params: dict[str, str]) -> list[dict[str, Any]]:
        full = params["bbox"] == live._bbox(whole)
        return [{"rawOb": f"UA {i}", "obsTime": int(NOW.timestamp()) - 60, "lat": 0, "lon": 0}
                for i in range(400 if full else 7)]

    fake = Fake(reports=reports)
    live.fetch(tmp_path, leg, fl, NOW, get=fake)
    left, right = live.halves(whole)
    assert {whole, left, right} <= set(boxes(fake))
    saved = {e.tag: e for e in snapshots.envelopes(tmp_path, "pirep")}
    capped = saved["b" + "_".join(map(str, whole))]
    assert (capped.status, capped.count, capped.payload) == ("skipped", 400, None)
    assert saved["b" + "_".join(map(str, left))].status == "ok"
    assert all(e.count < 400 for e in saved.values() if e.status == "ok")

    # Ten minutes later the same flight goes straight to what is cached: no call at all.
    again = Fake(reports=reports)
    live.fetch(tmp_path, leg, fl, NOW + timedelta(minutes=10), get=again)
    assert again.calls == []


# --- the cache -------------------------------------------------------------------------------

def test_a_second_brief_within_half_an_hour_fetches_nothing_and_a_later_one_everything(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    leg, fl = flight(table, "KSFO", "KJFK", NOW + timedelta(hours=1))
    first, second, third = Fake(), Fake(), Fake()
    live.fetch(tmp_path, leg, fl, NOW, get=first)
    live.fetch(tmp_path, leg, fl, NOW + timedelta(minutes=29), get=second)
    assert second.calls == []
    live.fetch(tmp_path, leg, fl, NOW + timedelta(minutes=30), get=third)
    assert sorted(e for e, _ in third.calls) == sorted(e for e, _ in first.calls)


def test_a_box_inside_a_cached_box_is_not_fetched(table: vrs.RouteTable, tmp_path: Path) -> None:
    leg, fl = flight(table, "KSFO", "KJFK", NOW + timedelta(hours=1))
    live.fetch(tmp_path, leg, fl, NOW, get=Fake())
    # The same route four hours out needs only its first two hours: inside the first box.
    leg, later = flight(table, "KSFO", "KJFK", NOW + timedelta(hours=4, minutes=5))
    fake = Fake()
    live.fetch(tmp_path, leg, later, NOW + timedelta(minutes=5), get=fake)
    assert fake.calls == []


def brief_for(table: vrs.RouteTable, root: Path, a: str, b: str, departs: datetime,
              at: datetime, get: Fake) -> Brief:
    req = BriefRequest(code="XX1", day=departs.date().isoformat(), dep=f"{departs:%H:%M}+00:00",
                       origin=a, destination=b)
    out = request_brief(req, table, root, at, fetch=functools.partial(live.fetch, get=get))
    assert isinstance(out, Brief), out
    return out


def test_two_flights_in_the_same_half_hour_each_get_their_own_airports(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    departs = NOW + timedelta(hours=2)
    fake = Fake()
    us = brief_for(table, tmp_path, "KSFO", "KJFK", departs, NOW, fake)
    assert us.airports["origin"].taf.state == "ok" and us.airports["destination"].metar.state == "ok"

    # A second flight in the same minute: its airports are asked together with the first's,
    # and its envelope sorts after the first, which is the one the engine reads.
    asia = brief_for(table, tmp_path, "WSSS", "YSSY", departs, NOW, fake)
    assert fake.of("taf") == [{"ids": "KJFK,KSFO"}, {"ids": "KJFK,KSFO,WSSS,YSSY"}]
    assert [e.tag for e in snapshots.envelopes(tmp_path, "taf")] == ["r00", "r01"]
    assert len(fake.of("sigmet")) == 1  # the worldwide feed serves both
    for b in (asia, brief_for(table, tmp_path, "KSFO", "KJFK", departs, NOW, fake),
              brief_for(table, tmp_path, "KSFO", "KJFK", departs, NOW + timedelta(minutes=9), fake),
              brief_for(table, tmp_path, "WSSS", "YSSY", departs, NOW + timedelta(minutes=9), fake)):
        for end in ("origin", "destination"):
            assert b.airports[end].taf.state == "ok", (b.flight.origin.code, end)
            assert b.airports[end].metar.state == "ok"
            assert b.airports[end].code in (b.airports[end].taf.quoted or "")
    assert len(fake.of("taf")) == 2 and len(fake.of("metar")) == 2  # nothing asked again

    # Without the shared envelope: a third flight after the first two have aged out asks for
    # its own airports only, and the first flight then asks again rather than read "no TAF".
    late = NOW + timedelta(minutes=40)
    brief_for(table, tmp_path, "RJAA", "KSFO", departs, late, fake)
    assert fake.of("taf")[-1] == {"ids": "KSFO,RJAA"}
    b = brief_for(table, tmp_path, "KSFO", "KJFK", departs, late + timedelta(minutes=1), fake)
    assert fake.of("taf")[-1] == {"ids": "KJFK,KSFO,RJAA"}
    assert b.airports["destination"].taf.state == "ok"


def test_an_airport_with_no_taf_reads_not_covered_not_stale(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    b = brief_for(table, tmp_path, "NTAA", "NTGJ", NOW + timedelta(hours=2), NOW, Fake())
    assert b.airports["destination"].taf.state == "not_covered"
    assert b.airports["origin"].taf.state == "ok"


# --- failure ---------------------------------------------------------------------------------

def test_a_source_that_times_out_is_reported_by_its_age_and_the_brief_still_renders(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    old = NOW - timedelta(hours=3)
    store(tmp_path, old, sigmet=[sigmet([], old, old + timedelta(hours=4))])
    fake = Fake(fail=("sigmet", "pirep", "taf"))
    b = brief_for(table, tmp_path, "KSFO", "KJFK", NOW + timedelta(hours=1), NOW, fake)
    text = render(b)
    assert b.hours[0].sources["warnings"].state == "stale"
    assert "Official warnings last updated 3 h ago" in text
    assert "Nothing found: no warning in force" not in text
    assert b.hours[0].sources["reports"].state == "stale"
    assert b.airports["origin"].taf.state == "stale" and b.airports["origin"].metar.state == "ok"
    assert b.hours[0].sources["gairmet"].state in ("nothing_found", "too_early")
    assert not list(snapshots.envelopes(tmp_path, "pirep"))  # a failed call writes nothing
    assert not any(SEVERITY.search(s) for s in own_strings(b.record()))

    # The next brief asks again for what failed, and only for that.
    fake = Fake()
    brief_for(table, tmp_path, "KSFO", "KJFK", NOW + timedelta(hours=1),
              NOW + timedelta(minutes=1), fake)
    assert {e for e, _ in fake.calls} == {"sigmet", "pirep", "taf"}


def test_everything_failing_still_gives_a_brief(table: vrs.RouteTable, tmp_path: Path) -> None:
    fake = Fake(fail=("sigmet", "gairmet", "pirep", "taf", "metar"))
    b = brief_for(table, tmp_path, "KSFO", "KJFK", NOW + timedelta(hours=1), NOW, fake)
    assert {h.state for h in b.hours} == {"stale"}
    assert "Official warnings: no data in this snapshot set" in render(b)


def test_nothing_is_fetched_for_a_flight_that_cannot_be_briefed(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    fake = Fake()
    for departs in (NOW - timedelta(hours=1), NOW + timedelta(hours=31)):
        b = brief_for(table, tmp_path, "KSFO", "KJFK", departs, NOW, fake)
        assert b.status != "ok"
    assert fake.calls == []


def test_the_pause_comes_only_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(live.time, "sleep", slept.append)
    monkeypatch.setattr(awc, "get_records", lambda endpoint, params: [])
    monkeypatch.setattr(live, "_sent", type(live._sent)())
    for _ in range(live.PER_MINUTE):
        live.polite_get("sigmet", {})
    assert slept == []
    live.polite_get("sigmet", {})
    assert len(slept) == 1 and 55 < slept[0] <= 60


# --- the command line ------------------------------------------------------------------------

def soon() -> list[str]:
    t = datetime.now(UTC) + timedelta(hours=2)
    return [t.date().isoformat(), "--dep", f"{t:%H:%M}+00:00"]


def test_cli_without_snapshots_is_live_and_keeps_its_cache_where_told(
        table: vrs.RouteTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[tuple[Path, str, datetime]] = []

    def fake(root: Path, leg: Leg, fl: PassengerFlight, as_of: datetime) -> None:
        seen.append((root, f"{leg.origin.code}-{leg.destination.code}", as_of))
        store(root, as_of, sigmet=[sigmet([], as_of, as_of + timedelta(hours=4))])

    monkeypatch.setattr(live, "fetch", fake)
    cache = tmp_path / "made" / "on" / "demand"
    code = cli.main(["SQ231", *soon(), "--routes", str(table.path), "--cache", str(cache)])
    out = capsys.readouterr().out
    assert code == 0 and out.startswith("SQ231 . Singapore to Sydney")
    assert "Nothing found: no warning in force here" in out
    (root, route, as_of), = seen
    assert root == cache and route == "WSSS-YSSY"
    assert abs(datetime.now(UTC) - as_of) < timedelta(minutes=2)

    # The default cache follows the environment variable.
    monkeypatch.setenv("FLIGHT_BRIEF_CACHE", str(tmp_path / "env"))
    assert cli.main(["SQ231", *soon(), "--routes", str(table.path)]) == 0
    assert seen[-1][0] == tmp_path / "env"
    monkeypatch.delenv("FLIGHT_BRIEF_CACHE")
    assert cli.default_cache() == Path.home() / ".cache" / "flight-brief" / "live"


def test_cli_live_does_not_fetch_for_a_request_that_stops_early(
        table: vrs.RouteTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(live, "fetch", lambda *a: pytest.fail("fetched"))
    base = ["--routes", str(table.path), "--cache", str(tmp_path)]
    assert cli.main(["hello", *soon(), *base]) == 2
    assert cli.main(["SQ231", "2026-11-04", "--dep", "08:00", *base]) == 0  # long departed
    assert "This flight has departed" in capsys.readouterr().out


def test_cli_refuses_as_of_without_snapshots(
        table: vrs.RouteTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(live, "fetch", lambda *a: pytest.fail("fetched"))
    code = cli.main(["SQ231", "2026-11-04", "--dep", "08:00", "--as-of", "2026-11-03T23:00",
                     "--routes", str(table.path), "--cache", str(tmp_path)])
    assert code == 2 and "--as-of needs --snapshots" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


# --- the real thing --------------------------------------------------------------------------

@pytest.mark.network
def test_a_real_fetch_fills_the_store(table: vrs.RouteTable, tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    leg, fl = flight(table, "KSFO", "KJFK", now + timedelta(hours=1))
    live.fetch(tmp_path, leg, fl, now)
    assert snapshots.latest(tmp_path, "sigmet", now) is not None
    assert snapshots.latest(tmp_path, "taf", now) is not None
    assert len(list(snapshots.envelopes(tmp_path, "gairmet"))) == 5
