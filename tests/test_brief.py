import re
"""The Phase 1 items of the design's Verification section that need no forecast layer."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

from brief_helpers import (
    AA273,
    SEVERITY,
    SQ231,
    STORE,
    UA837,
    UA1175,
    brief,
    own_strings,
    ring,
    sigmet,
    store,
    unquoted,
    utc,
    without_ride,
)
from flight_brief import vrs
from flight_brief.brief import DEPARTED, NOT_OPS, TOO_FAR_H, defaults, make_flight, to_utc
from flight_brief.core import snapshots
from flight_brief.core.encounters import encounters
from flight_brief.core.hazards import from_sigmet
from flight_brief.core.route import position_at, timeline
from flight_brief.render import render
from flight_brief.resolve import resolve

# --- Replay ------------------------------------------------------------------------------

def test_replay_sq231_crosses_a_thunderstorm_warning_at_the_times_the_core_gives(
        table: vrs.RouteTable) -> None:
    b = brief(table, *SQ231)
    first = b.hours[0]
    assert first.state == "flagged" and first.sources["warnings"].state == "flagged"
    (hit,) = first.warnings
    assert hit.kind == "TS" and "WIIF SIGMET 14" in hit.quoted

    # The same crossing straight from the core, on the same snapshot.
    leg = resolve("SQ231", table).legs[0]
    flight = make_flight("SQ231", leg, b.flight.departure_utc)
    env = snapshots.latest(STORE, "sigmet", utc(SQ231[3]), max_age=timedelta(hours=2))
    assert env is not None
    hazards = [from_sigmet(r) for r in env.payload]
    (core,) = encounters(flight, hazards, utc(SQ231[3]), lookahead_h=12)
    assert (hit.entry, hit.exit, hit.hazard_id) == (core.entry, core.exit, core.hazard_id)

    # And independently of `crossing`: inside the polygon between those times, outside before,
    # and the warning is valid throughout.
    area = Polygon(next(h for h in hazards if h.hazard_id == hit.hazard_id).rings[0])
    points = timeline(flight)

    def inside(t):  # type: ignore[no-untyped-def]
        p = position_at(points, t)
        return area.contains(Point(p.lon, p.lat))

    assert inside(hit.entry + timedelta(minutes=2)) and inside(hit.exit - timedelta(minutes=2))
    assert not inside(hit.entry - timedelta(minutes=5))
    assert hit.entry >= b.flight.takeoff_utc and hit.exit <= hit.valid_to
    assert timedelta(minutes=15) < hit.entry - b.flight.takeoff_utc < timedelta(minutes=30)


def test_replay_ua837_crosses_two_turbulence_warnings_and_the_antimeridian(
        table: vrs.RouteTable) -> None:
    b = brief(table, *UA837)
    kinds = [w.kind for w in b.hours[0].warnings]
    assert kinds == ["TURB", "TURB"] and b.hours[0].state == "flagged"
    lons = [lon for _, lon in b.route.points]
    assert max(abs(a - c) for a, c in zip(lons, lons[1:], strict=False)) < 30  # no 360 jump
    assert min(lons) < -180 < max(lons)  # continuous through the date line
    assert [h.index for h in b.hours] == list(range(1, len(b.hours) + 1))
    # Only the first hour is over the contiguous US, so only it has a G-AIRMET line.
    assert ["gairmet" in h.sources for h in b.hours] == [True] + [False] * (len(b.hours) - 1)


def test_quiet_day_says_nothing_found_where_sources_speak_and_never_elsewhere(
        table: vrs.RouteTable) -> None:
    b = brief(table, *UA1175)
    assert all(h.state != "flagged" for h in b.hours)
    assert not any(h.warnings or h.gairmets or h.expired_warnings for h in b.hours)
    assert b.near_airports == []
    assert [h.state for h in b.hours] == ["nothing_found"] * 4 + ["too_early"]
    assert b.hours[0].sources["warnings"].text.startswith("Nothing found")
    assert b.hours[0].sources["gairmet"].state == "nothing_found"
    assert b.hours[4].sources["warnings"].text == "None issued this far ahead"
    assert b.summary.startswith("0 flagged stretches.")
    assert "Nothing found" in render(b)


def test_us_route_with_a_convective_sigmet_a_gairmet_and_pilot_reports(
        table: vrs.RouteTable) -> None:
    b = brief(table, *AA273)
    (hit,) = b.hours[0].warnings
    assert hit.kind == "TS" and "CONVECTIVE SIGMET 3C" in hit.quoted
    assert hit.base_ft is None and hit.top_ft == 40000  # no base, a top in feet
    assert all("gairmet" in h.sources for h in b.hours)
    assert b.hours[0].gairmets and b.hours[0].gairmets[0].quoted == "TURB-HI MOD"
    assert b.hours[1].sources["reports"].state == "flagged" and b.hours[1].reports
    assert all(r.distance_nm <= 100 and abs((r.flight_level or 360) - 360) <= 40
               for h in b.hours for r in h.reports)
    assert b.hours[1].agreement == "disagree"  # warnings say nothing, two other sources flag
    assert b.airports["origin"].taf.state == "not_covered"  # KDFW is not in the store
    assert b.airports["destination"].taf.state == "ok"


# --- Knowability ---------------------------------------------------------------------------

def test_five_days_out_is_too_far(table: vrs.RouteTable) -> None:
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-10-30T00:00")
    assert b.status == "too_far_out" and b.hours == []
    assert b.message and b.message.startswith(f"Too far out. Come back within {TOO_FAR_H:.0f} hours")


def test_a_day_out_every_hour_is_too_early_and_only_the_origin_has_a_taf(
        table: vrs.RouteTable) -> None:
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T00:00")
    assert b.status == "ok"
    assert {h.state for h in b.hours} == {"too_early"}
    assert {h.sources["warnings"].state for h in b.hours} == {"none_issued"}
    assert all(h.agreement is None for h in b.hours)
    assert b.airports["origin"].taf.state == "ok"
    late = b.airports["destination"].taf
    assert late.state == "too_early" and late.text.startswith("Too early to know. Check again after")


def test_six_hours_out_warnings_still_do_not_reach_but_both_tafs_do(table: vrs.RouteTable) -> None:
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T18:00")
    assert {h.sources["warnings"].state for h in b.hours} == {"none_issued"}
    assert b.airports["origin"].taf.state == b.airports["destination"].taf.state == "ok"


def test_one_hour_out_warnings_speak_for_the_first_hours_only(table: vrs.RouteTable) -> None:
    b = brief(table, *SQ231)
    states = [h.sources["warnings"].state for h in b.hours]
    assert states == ["flagged", "nothing_found", "nothing_found", "nothing_found"] + ["none_issued"] * 4
    assert {h.sources["reports"].state for h in b.hours} == {"not_covered"}
    assert b.hours[0].agreement == "single"


def test_pilot_reports_wait_until_an_hour_is_within_reach(table: vrs.RouteTable) -> None:
    b = brief(table, *UA837)
    states = [h.sources["reports"].state for h in b.hours]
    assert states[:6] == ["no_reports"] * 6 and set(states[6:]) == {"too_early"}


def test_a_departed_flight_gets_no_brief(table: vrs.RouteTable) -> None:
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-04T00:30")
    assert (b.status, b.message, b.hours) == ("departed", DEPARTED, [])
    assert DEPARTED in render(b)


def test_an_empty_store_says_stale_and_still_renders(table: vrs.RouteTable, tmp_path: Path) -> None:
    b = brief(table, "UA837", "2026-11-02", "18:00", "2026-11-03T01:00", root=tmp_path)
    assert {h.sources["warnings"].state for h in b.hours} == {"stale"}
    assert b.hours[0].state == "stale" and "gairmet" not in b.hours[0].sources
    assert b.airports["origin"].taf.state == b.airports["origin"].metar.state == "stale"
    stale = [p for p in b.provenance if p.stale]
    assert {p.source for p in stale} >= {"sigmet", "gairmet", "pirep", "taf", "metar"}
    text = render(b)
    assert "NOT an all clear" in text and "Nothing found" not in text


def test_a_feed_older_than_its_limit_says_how_long_ago(table: vrs.RouteTable, tmp_path: Path) -> None:
    at = utc("2026-11-03T01:00")
    store(tmp_path, at - timedelta(hours=5), sigmet=[])
    b = brief(table, "UA837", "2026-11-02", "18:00", at, root=tmp_path)
    assert b.hours[0].sources["warnings"].text == "Official warnings last updated 5 h ago"


# --- Climb and descent, and warnings that end before arrival -------------------------------

def test_low_topped_warning_near_the_origin_is_skipped_at_cruise_and_listed_near_the_airport(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    at = utc("2026-11-03T23:00")
    low = sigmet(ring(103.5, 105.0, -0.5, 2.5), at - timedelta(hours=1), at + timedelta(hours=3),
                 top=20000, raw="XXXX SIGMET 01 VALID EMBD TS OBS TOP FL200")
    b = brief(table, "SQ231", "2026-11-04", "08:00", at, root=store(tmp_path, at, sigmet=[low]))
    assert all(not h.warnings for h in b.hours) and b.hours[0].state == "nothing_found"
    (near,) = b.near_airports
    assert (near.end, near.phase, near.airport, near.below_cruise) == ("origin", "climb", "WSSS", True)
    assert "WSSS climb: thunderstorm warning below cruise level" in render(b)


def test_a_warning_that_ends_before_arrival_reads_none_issued_this_far_ahead(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    at = utc("2026-11-03T23:00")
    # Over the Timor Sea, about five hours into the flight; valid for two more hours only.
    far = sigmet(ring(124.0, 128.0, -18.0, -12.0), at - timedelta(hours=1), at + timedelta(hours=2))
    b = brief(table, "SQ231", "2026-11-04", "08:00", at, root=store(tmp_path, at, sigmet=[far]))
    (hour,) = [h for h in b.hours if h.expired_warnings]
    assert hour.warnings == [] and hour.state == "too_early"
    line = hour.sources["warnings"]
    assert line.state == "none_issued" and line.text.startswith("None issued this far ahead")
    assert "ending before you arrive" in line.text


# --- Edge inputs ---------------------------------------------------------------------------

def test_a_red_eye_keeps_the_typed_local_date_and_runs_past_midnight(table: vrs.RouteTable) -> None:
    b = brief(table, "SQ322", "2026-11-03", "23:55", "2026-11-03T14:55")
    assert str(b.identity.date) == "2026-11-03"
    assert b.flight.departure_local == "2026-11-03T23:55+08:00"
    assert b.flight.departure_utc == utc("2026-11-03T15:55")
    assert b.flight.arrival_utc.date().isoformat() == "2026-11-04"
    assert (b.flight.cruise_fl, b.flight.groundspeed_kt) == (380, 480)
    assert all(a.end == c.start for a, c in zip(b.hours, b.hours[1:], strict=False))
    assert b.hours[0].start == b.flight.takeoff_utc == utc("2026-11-03T16:15")
    assert b.hours[-1].end == b.flight.landing_utc and len(b.hours) == 13
    assert b.flight.arrival_utc - b.flight.landing_utc == timedelta(minutes=10)
    assert "Fri" not in render(b) and "Tue 03 Nov 23:55 UTC+8" in render(b)


def test_leading_zeros_give_the_same_brief(table: vrs.RouteTable) -> None:
    assert brief(table, "SQ0231", *SQ231[1:]).record() == brief(table, *SQ231).record()


def test_each_leg_of_a_multi_leg_number_is_its_own_brief(table: vrs.RouteTable) -> None:
    one = brief(table, "QF1", "2026-11-03", "16:00", "2026-11-03T04:00", leg=1)
    two = brief(table, "QF1", "2026-11-03", "23:30", "2026-11-03T14:55", leg=2)
    assert (one.identity.leg, one.flight.origin.code, one.flight.destination.code) == (1, "YSSY", "WSSS")
    assert (two.identity.leg, two.flight.origin.code, two.flight.destination.code) == (2, "WSSS", "EGLL")


def test_typed_airports_brief_a_number_that_does_not_map(table: vrs.RouteTable) -> None:
    assert resolve("U28001", table).status == "could_not_map"
    b = brief(table, "U28001", "2026-11-04", "08:00", SQ231[3], airports=("SIN", "SYD"))
    assert b.status == "ok" and b.flight.callsign is None and b.flight.code == "U28001"


def test_defaults_follow_the_distance_band_and_are_printed_as_assumptions(
        table: vrs.RouteTable) -> None:
    assert [defaults(d) for d in (120, 299, 300, 799, 800, 2499, 2500, 6000)] == [
        (240, 380), (240, 380), (340, 430), (340, 430), (360, 460), (360, 460), (380, 480), (380, 480)]
    b = brief(table, *SQ231)
    assert any("FL380 assumed from the distance" in a for a in b.flight.assumptions)
    assert any("great-circle guess" in a for a in b.flight.assumptions)
    typed = brief(table, *SQ231, cruise_fl=350, groundspeed_kt=500)
    assert typed.flight.cruise_fl == 350 and "Cruise level FL350 as you entered" in typed.flight.assumptions


def test_departure_time_parsing() -> None:
    from datetime import date
    day = date(2026, 11, 14)
    assert to_utc(day, "23:55", "Asia/Singapore") == utc("2026-11-14T15:55")
    assert to_utc(day, "23:55+08:00", None) == utc("2026-11-14T15:55")
    assert to_utc(day, "06:10-05:00", "Asia/Singapore") == utc("2026-11-14T11:10")
    for bad in ("25:00", "noon", "7pm"):
        with pytest.raises(ValueError, match="not a time"):
            to_utc(day, bad, "Asia/Singapore")
    with pytest.raises(ValueError, match="UTC offset"):
        to_utc(day, "23:55", None)


# --- The record ----------------------------------------------------------------------------

def test_the_record_has_the_designs_shape_and_names_its_sources(table: vrs.RouteTable) -> None:
    rec = brief(table, *AA273).record()
    json.dumps(rec)  # plain JSON all the way down
    assert set(rec["identity"]) == {"code", "date", "leg", "issued_at"}
    assert {"code", "callsign", "origin", "destination", "departure_utc", "departure_local",
            "cruise_fl", "groundspeed_kt", "assumptions"} <= set(rec["flight"])
    assert rec["flight"]["departure_source"] == "user input"
    assert rec["route"]["kind"] == "great_circle" and rec["route"]["n_tracks"] == 0
    hour = rec["hours"][0]
    assert {"index", "from", "to", "state", "agreement", "shear", "gtg", "warnings", "gairmets",
            "reports"} <= set(hour)
    assert hour["shear"] is None and hour["gtg"] is None  # Phase 2
    assert set(rec["airports"]) == {"origin", "destination"}
    for item in rec["provenance"]:
        assert {"source", "fetched_at", "issued_at", "url_without_secrets", "licence"} <= set(item)
    urls = [p["url_without_secrets"] for p in rec["provenance"] if p["url_without_secrets"]]
    assert any("/api/data/sigmet?" in u for u in urls) and not any("isigmet" in u for u in urls)
    assert NOT_OPS in rec["notes"] and any("airline or flight tracker" in n for n in rec["notes"])


def test_the_same_inputs_give_the_same_record(table: vrs.RouteTable) -> None:
    assert brief(table, *UA837).record() == brief(table, *UA837).record()


# --- Wording -------------------------------------------------------------------------------

def every_brief(table: vrs.RouteTable, tmp_path: Path):  # type: ignore[no-untyped-def]
    at = utc("2026-11-03T23:00")
    rough = sigmet(ring(103.5, 105.0, -0.5, 2.5), at - timedelta(hours=1), at + timedelta(hours=3),
                   hazard="TURB", top=20000, raw="XXXX SIGMET 02 VALID SEV TURB FCST SFC/FL200")
    yield brief(table, *SQ231)
    yield brief(table, *UA837)
    yield brief(table, *AA273)
    yield brief(table, *UA1175)
    yield brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T00:00")
    yield brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T18:00")
    yield brief(table, "SQ231", "2026-11-04", "08:00", "2026-10-30T00:00")
    yield brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-04T00:30")
    yield brief(table, "UA837", "2026-11-02", "18:00", "2026-11-03T01:00", root=tmp_path / "none")
    yield brief(table, "SQ231", "2026-11-04", "08:00", at,
                root=store(tmp_path / "rough", at, sigmet=[rough]))


def test_strength_words_stand_only_in_quoted_official_text_and_in_the_plain_answer(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    quoted_some = answered_some = False
    for b in every_brief(table, tmp_path):
        text = render(b)
        rest = unquoted(without_ride(text))
        assert not SEVERITY.search(rest), SEVERITY.search(rest)
        for own in own_strings(b.record()):
            assert not SEVERITY.search(own), own
        quoted_some = quoted_some or bool(SEVERITY.search(text))
        if b.ride is not None:
            for sentence in b.ride.answer:
                if re.search(r"\b(smooth|light|moderate|severe)\b", sentence, re.IGNORECASE):
                    answered_some = True     # a strength word always names its source
                    assert re.search(r"\((official warning|NOAA forecast|estimate)"
                                     r"( and (NOAA forecast|estimate))*\)", sentence), sentence
    assert quoted_some  # the fixtures do carry such words, so the check has something to catch
    assert answered_some


def test_every_page_says_not_for_flight_operations_and_never_safe_or_a_score(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    for b in every_brief(table, tmp_path):
        text = render(b)
        assert NOT_OPS in text
        assert not any(word in unquoted(text).lower().split() for word in ("safe", "unsafe", "score"))
