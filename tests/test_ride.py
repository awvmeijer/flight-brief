"""The plain answer: which word a stretch gets, who names it, and the sentences on top."""

import datetime as dt
import re
from pathlib import Path

import pytest

from brief_helpers import AA273, SQ231, UA1175, brief, utc
from flight_brief import page, ride, vrs
from flight_brief.aircraft import Aircraft, weight_class
from flight_brief.render import render
from test_forecast_brief import RUN, put_gtg

T0 = utc("2026-11-20T10:00")


def slots(levels: str, source: str = "estimate", storms: str = "", known_storms: bool = True
          ) -> list[ride.Slot]:
    """One stretch of ten minutes per character: 0 to 3 a level, ? not known. `storms` marks
    stretches the same way with w (warning), f (forecast) or a dot."""
    out = []
    for i, ch in enumerate(levels):
        mark = storms[i] if i < len(storms) else "."
        out.append(ride.Slot(
            start=T0 + dt.timedelta(minutes=10 * i), end=T0 + dt.timedelta(minutes=10 * i + 10),
            level=None if ch == "?" else int(ch), source=None if ch == "?" else source,
            storm={"w": "warning", "f": "forecast"}.get(mark), storm_known=known_storms,
            unknown="too_early" if ch == "?" else None))
    return out


# --- one stretch ------------------------------------------------------------------------------

def test_noaa_s_names_follow_its_thresholds_for_each_weight_class() -> None:
    assert [ride.noaa_level(v, "medium") for v in (0.149, 0.15, 0.199, 0.20, 0.439, 0.44)] == [0, 1, 1, 2, 2, 3]
    assert [ride.noaa_level(0.20, w) for w in ("light", "medium", "heavy")] == [2, 2, 1]
    assert ride.NOAA_EDR["heavy"] == (0.17, 0.24, 0.54) and ride.NOAA_EDR["light"] == (0.13, 0.16, 0.36)


def test_the_estimate_has_three_words_and_never_the_fourth() -> None:
    assert [ride.estimate_level(v, "medium") for v in (0, 10.0, 10.5, 19.5, 20.0, 127.0)] == [0, 0, 1, 1, 2, 2]
    assert [ride.estimate_level(14.0, w) for w in ("light", "medium", "heavy")] == [2, 1, 1]
    assert all(ride.estimate_level(1e6, w) == 2 for w in ride.ESTIMATE_CUTS)
    # a heavier aircraft needs more of the same air for the same word
    assert ride.ESTIMATE_CUTS["light"] < ride.ESTIMATE_CUTS["medium"] < ride.ESTIMATE_CUTS["heavy"]


def test_the_official_forecast_speaks_where_it_exists_and_the_estimate_only_elsewhere() -> None:
    level = lambda **kw: ride.slot_level(weight="medium", warning_kinds=kw.pop("kinds", []), **kw)  # noqa: E731
    assert level(edr=0.05, index=40.0) == (0, "forecast")     # the estimate does not outvote NOAA
    assert level(edr=0.21, index=1.0) == (2, "forecast")
    assert level(edr=None, index=40.0) == (2, "estimate")
    assert level(edr=None, index=None) == (None, None)        # nobody speaks: not known, not smooth


def test_a_warning_in_force_can_only_raise_the_word() -> None:
    level = lambda **kw: ride.slot_level(weight="medium", **kw)  # noqa: E731
    assert level(warning_kinds=["TURB"], edr=0.05, index=None) == (3, "warning")
    assert level(warning_kinds=["MTW"], edr=None, index=None) == (3, "warning")
    assert level(warning_kinds=["TURB-HI"], edr=None, index=1.0) == (2, "warning")
    assert level(warning_kinds=["TURB-HI"], edr=0.50, index=None) == (3, "forecast")
    assert level(warning_kinds=["TURB-HI"], edr=0.21, index=None) == (2, "warning")   # a tie
    # a thunderstorm or icing warning names no turbulence strength
    assert level(warning_kinds=["TS", "ICE"], edr=None, index=None) == (None, None)
    assert level(warning_kinds=["TS"], edr=None, index=11.0) == (1, "estimate")


def test_a_storm_cloud_counts_from_five_thousand_feet_under_the_cruise_level() -> None:
    assert ride.storm_from_top(330, 380) and ride.storm_from_top(450, 380)
    assert not ride.storm_from_top(325, 380) and not ride.storm_from_top(None, 380)


def test_weight_class_from_the_aircraft_type() -> None:
    def of(code: str, kind: str = "jet") -> str:
        return weight_class(Aircraft(code=code, kind=kind, groundspeed_kt=None, cruise_fl=None))
    assert [of(c) for c in ("B77W", "777", "A359", "A380", "B748", "A332", "A310")] == ["heavy"] * 7
    assert [of(c) for c in ("A320", "B738", "737 MAX", "B752", "E190", "A319", "A21N", "jet")] == ["medium"] * 8
    assert of("AT72", "turboprop") == "medium" and of("C208", "turboprop") == "light"
    assert of("piston", "piston") == "light" and weight_class(None) == "medium"


# --- the sentences ----------------------------------------------------------------------------

def test_a_smooth_flight_says_so_and_names_the_source() -> None:
    assert ride.answer(slots("0" * 12, known_storms=False), T0) == [
        "Smooth for the whole flight (estimate)."]
    told = ride.answer(slots("0" * 6 + "?" * 6, known_storms=False), T0)
    assert told == ["Smooth for the part that can be known (estimate).", "Not known yet: hour 2."]


def test_the_strongest_word_leads_with_how_long_and_when() -> None:
    told = ride.answer(slots("000000" "011220" "000000" "000000", "forecast", known_storms=False), T0)
    assert told == ["Mostly smooth. Moderate turbulence for about 20 minutes, in hour 2 (NOAA forecast).",
                    "Light for about 20 minutes (NOAA forecast)."]
    rough = ride.answer(slots("223333" "222222" "111111", "warning", known_storms=False), T0)
    assert rough[0] == ("Bumpy for most of the flight. Severe turbulence for about 40 minutes, "
                        "in hour 1 (official warning).")
    assert rough[1:] == ["Moderate for about 1 h 20 min (official warning).",
                         "Light for about 1 h (official warning)."]


def test_every_sentence_with_a_strength_word_names_its_source() -> None:
    mixed = slots("012", "estimate") + slots("123", "forecast") + slots("3", "warning")
    for sentence in ride.answer(mixed, T0):
        if re.search(r"\b(smooth|light|moderate|severe)\b", sentence, re.IGNORECASE):
            assert re.search(r"\((official warning|NOAA forecast|estimate)", sentence), sentence
    assert "(official warning and NOAA forecast)" in ride.answer(mixed, T0)[0]


def test_nothing_known_is_said_as_such_and_never_as_smooth() -> None:
    told = ride.answer(slots("??????", known_storms=False), T0)
    assert told == ["Too early to say how the ride will be."]
    assert not any("mooth" in s for s in told)


def test_storms_are_told_from_warnings_and_from_forecast_clouds() -> None:
    quiet = ride.answer(slots("0" * 12), T0)
    assert quiet[-1] == "No storm warning and no forecast storm clouds near cruise level."
    told = ride.answer(slots("0" * 18, storms="..w..." "......" "...ff."), T0)
    assert "Thunderstorm warning in force in hour 1." in told
    assert "Storm clouds forecast near cruise level in hour 3." in told
    assert not any(s.startswith("No storm") for s in told)
    silent = ride.answer(slots("0" * 6, known_storms=False), T0)
    assert not any("storm" in s.lower() for s in silent)     # no storm source speaks: no claim


def test_spans_durations_and_the_wind_sentence() -> None:
    assert ride.hour_span([3]) == "hour 3" and ride.hour_span([2, 3, 12]) == "hours 2 to 3 and 12"
    assert ride.hour_span([1, 3, 5, 6]) == "hours 1, 3 and 5 to 6"
    assert [ride.duration(m) for m in (20, 60, 100)] == ["20 minutes", "1 h", "1 h 40 min"]
    assert ride.wind_sentence([40.0, 52.0]) == "Tailwind about 45 kt on average."
    assert ride.wind_sentence([-28.0, -34.0]) == "Headwind about 30 kt on average."
    assert ride.wind_sentence([6.0, -3.0]) == "Little headwind or tailwind on average."
    assert ride.wind_sentence([]) is None


def test_the_module_never_writes_a_verdict() -> None:
    text = " ".join([*ride.MEANING.values(), ride.KEY, *ride.explain("heavy", False),
                     *ride.explain("medium", True)])
    assert not re.search(r"\b(safe|unsafe|safely|safety|score|rating|dangerous)\b", text, re.IGNORECASE)
    assert "never says severe" in text and "less certain" in ride.KEY


# --- in a brief -------------------------------------------------------------------------------

def test_a_brief_cuts_the_flight_into_ten_minutes_and_adds_them_up(table: vrs.RouteTable) -> None:
    b = brief(table, *UA1175)
    r = b.ride
    assert r is not None and r.weight_class == "medium" and r.weight_assumed
    airborne = round((b.flight.landing_utc - b.flight.takeoff_utc).total_seconds() / 60)
    assert sum(r.minutes.values()) == airborne
    assert r.slots[0].start == b.flight.takeoff_utc and r.slots[-1].end == b.flight.landing_utc
    assert all(a.end == c.start for a, c in zip(r.slots, r.slots[1:], strict=False))
    assert all(len([x for x in r.slots if x.hour == h.index]) == 6 for h in b.hours[:-1])
    # no forecast tile in this replay and no turbulence warning: nothing is called smooth
    assert all(x.level is None and x.unknown for x in r.slots)
    assert r.answer[0] == "Too early to say how the ride will be."
    assert all(h.ride_word is None for h in b.hours)


def test_a_thunderstorm_warning_on_the_route_is_told_as_a_storm_not_as_a_strength(
        table: vrs.RouteTable) -> None:
    b = brief(table, *SQ231)
    assert b.ride is not None and b.hours[0].storm == "warning"
    assert any(s.startswith("Thunderstorm warning in force in hour 1") for s in b.ride.answer)
    assert b.hours[0].ride_level is None


def test_noaa_s_forecast_names_the_us_hours_and_the_aircraft_size_changes_the_word(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    tiles = put_gtg(tmp_path, RUN, counts=42)        # 0.21 everywhere on the grid
    medium = brief(table, *AA273, tiles=tiles)
    us = [h for h in medium.hours if "gtg" in h.sources and h.sources["gtg"].state in ("flagged", "nothing_found")]
    assert us and all(h.ride_source in ("forecast", "warning") and (h.ride_level or 0) >= 2 for h in us)
    assert medium.ride is not None and "NOAA forecast" in medium.ride.answer[0]
    heavy = brief(table, *AA273, tiles=tiles,
                  aircraft=Aircraft(code="B77W", kind="jet", groundspeed_kt=None, cruise_fl=None))
    assert heavy.ride is not None and heavy.ride.weight_class == "heavy" and not heavy.ride.weight_assumed
    # the same forecast value is one word lower for the heavier aircraft; where the flight
    # also crosses a G-AIRMET turbulence area (this one does), the warning's word stands
    named = [x for x in heavy.ride.slots if x.source == "forecast"]
    assert named and all(x.level == 1 for x in named)
    assert all(x.level == 2 for x in heavy.ride.slots if x.source == "warning")


def test_the_text_brief_prints_the_answer_block(table: vrs.RouteTable, tmp_path: Path) -> None:
    text = render(brief(table, *AA273, tiles=put_gtg(tmp_path, RUN, counts=42)))
    block = text[text.index("The ride"):]
    block = block[:block.index("\n\n")]
    assert "moderate" in block.lower() and "NOAA forecast)" in block
    assert ". smooth  - light  = moderate  # severe  ? not known yet" in block
    assert "Words for a medium aircraft (no aircraft type given)" in block


# --- on the page ------------------------------------------------------------------------------

def test_the_page_opens_with_the_answer_and_folds_the_evidence(table: vrs.RouteTable, tmp_path: Path) -> None:
    b = brief(table, *AA273, tiles=put_gtg(tmp_path, RUN, counts=42))
    html = page.render_page(b)
    assert b.ride is not None
    ride_at, fold_at = html.index('<section class="st-win ride">'), html.index('<details class="fb-evidence">')
    assert ride_at < html.index("What this brief cannot know yet") < fold_at
    window = html[ride_at:html.index("</section>", ride_at)]
    assert page.e(b.ride.answer[0]) in window
    assert window.count('<li class="fb-rh"') == len(b.hours)
    assert window.count("<i class=\"r") + window.count('<i class="rq') >= len(b.ride.slots)
    assert all(page.e(text) in window for text in b.ride.meanings.values())
    assert 'class="r2 off"' in window                 # an official word is drawn solid
    fold = html[fold_at:]
    assert "Show the evidence" in fold and "How the words are chosen" in fold
    assert "What each source says" in fold and "Sources and licences" in fold
    assert "What each source says" not in html[:fold_at]
    assert "<details" in fold and "open" not in fold[:fold.index(">")]   # closed until asked


def test_an_estimate_is_drawn_as_an_estimate(table: vrs.RouteTable) -> None:
    b = brief(table, *UA1175)
    assert b.ride is not None
    for x in b.ride.slots[:6]:
        x.level, x.word, x.source, x.unknown = 1, "light", "estimate", None
    b.hours[0].ride_level, b.hours[0].ride_word, b.hours[0].ride_source = 1, "light", "estimate"
    html = page.render_page(b)
    assert html.count('<i class="r1 est"></i>') == 6 and 'class="r1 off"></i><i' not in html
    assert 'title="Hour 1: light (estimate)"' in html
    assert '<i class="rq"></i>' in html               # the hours nobody can know yet


def test_a_record_saved_before_the_answer_existed_still_opens(table: vrs.RouteTable) -> None:
    record = brief(table, *SQ231).record()
    record.pop("ride")
    for h in record["hours"]:
        for key in ("ride_level", "ride_word", "ride_source", "storm", "wind_kt"):
            h.pop(key, None)
    old = page.brief_from_record(record)
    assert old.ride is None
    html = page.render_page(old)
    assert '<section class="st-win ride">' not in html and "fb-evidence" not in html
    assert "What each source says" in html and "Each source, hour by hour" in html


@pytest.mark.parametrize("flight", [SQ231, AA273, UA1175])
def test_a_saved_brief_reads_back_with_its_answer(table: vrs.RouteTable, flight: tuple) -> None:
    b = brief(table, *flight)
    again = page.brief_from_record(b.record())
    assert again.ride == b.ride and page.brief_body(again) == page.brief_body(b)


def test_the_cut_points_in_the_code_are_the_ones_the_calibration_found() -> None:
    import json
    found = json.loads((Path(__file__).parent.parent / "docs/recon/estimate_match.json").read_text())
    for weight, block in found["classes"].items():
        cuts = block["index_at_the_same_share"]
        assert ride.ESTIMATE_CUTS[weight] == (cuts["light"], cuts["moderate"])
        assert tuple(block["edr"].values()) == ride.NOAA_EDR[weight]
    medium = found["classes"]["medium"]
    assert round(medium["same_level"], 2) == 0.77 and round(medium["within_one_level"], 2) == 0.95
    assert "3 stretches in 4" in ride.ESTIMATE_CHECK and "19 in 20" in ride.ESTIMATE_CHECK
