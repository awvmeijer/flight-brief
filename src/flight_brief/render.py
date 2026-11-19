"""The brief as plain text, after the design's wireframe.

Every piece of an official source's own text is printed inside double quotation marks, and
nothing else is: the wording check relies on that to tell quoted text from this code's words.
The plain answer (`ride.py`) is the block under "The ride", up to the next empty line; it is
the only place outside quotation marks where a strength word stands.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from .brief import TOO_EARLY, Brief, Hour, SourceLine, age, day_hhmm, hhmm, levels

SYMBOL = {"flagged": "#", "nothing_found": ".", "too_early": "?", "not_covered": "-",
          "stale": "!"}
LEGEND = "# flagged  . nothing found  ? too early  - not covered  ! source stale"
SOURCES = (("shear", "Wind-shear index"), ("gtg", "GTG forecast"),
           ("warnings", "Official warnings"), ("gairmet", "G-AIRMETs"),
           ("reports", "Pilot reports"))


def _span(a: int, b: int) -> str:
    return f"hour {a}" if a == b else f"hours {a} to {b}"


def _grouped(hours: list[Hour], key: str) -> list[str]:
    """One line per run of hours for which a source says the same thing. "Too early" hours
    differ only in when each comes into reach, so a run of them is one line giving the range."""
    runs: list[tuple[int, int, SourceLine, SourceLine]] = []
    for h in hours:
        if key not in h.sources:
            continue
        line = h.sources[key]
        if runs and runs[-1][1] == h.index - 1 and (
                runs[-1][2].text == line.text or runs[-1][2].state == line.state == "too_early"):
            runs[-1] = (runs[-1][0], h.index, runs[-1][2], line)
        else:
            runs.append((h.index, h.index, line, line))
    out = []
    for a, b, first, last in runs:
        text = first.text
        if first.check_after and last.check_after and first.check_after != last.check_after:
            text = (f"{TOO_EARLY} from {day_hhmm(first.check_after)} (hour {a}) to "
                    f"{day_hhmm(last.check_after)} (hour {b})")
        out.append(f"{_span(a, b)}: {text}")
    return out


def _local(t: datetime, tz: str | None) -> str:
    if tz is None:
        return t.strftime("%a %d %b %H:%M UTC")
    local = t.astimezone(ZoneInfo(tz))
    offset = local.utcoffset()
    assert offset is not None
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hh, mm = divmod(abs(minutes), 60)
    return local.strftime("%a %d %b %H:%M") + f" UTC{sign}{hh}" + (f":{mm:02d}" if mm else "")


RIDE_SYMBOL = (".", "-", "=", "#")
RIDE_LEGEND = ". smooth  - light  = moderate  # severe  ? not known yet  (ten minutes each)"
RIDE_SOURCE = {"warning": "official warning", "forecast": "NOAA forecast", "estimate": "estimate"}


def ride_block(brief: Brief) -> list[str]:
    """The plain answer: the sentences, then ten minutes per character, grouped by hour."""
    r = brief.ride
    if r is None:
        return []
    out = ["The ride", *[f"  {line}" for line in r.answer]]
    if r.wind_text:
        out.append(f"  {r.wind_text}")
    groups: dict[int, str] = {}
    for x in r.slots:
        groups[x.hour] = groups.get(x.hour, "") + ("?" if x.level is None else RIDE_SYMBOL[x.level])
    out.append("  " + " ".join(groups[h] for h in sorted(groups)))
    out.append("  " + RIDE_LEGEND)
    used = [s for s in RIDE_SOURCE if any(x.source == s for x in r.slots)]
    if used:
        out.append("  From: " + ", ".join(RIDE_SOURCE[s] for s in used) + ". Words for a "
                   f"{r.weight_class} aircraft"
                   + (" (no aircraft type given)" if r.weight_assumed else ""))
    return [*out, ""]


def render(brief: Brief) -> str:
    f = brief.flight
    out = [f"{f.code} . {f.origin.location} to {f.destination.location} . "
           f"leg {brief.identity.leg} . {f.origin.code} to {f.destination.code}"
           + (f" . callsign {f.callsign}" if f.callsign else "")]
    out.append(f"Departs {_local(f.departure_utc, f.origin.tz)} ({hhmm(f.departure_utc)}), "
               f"as you entered . assumed FL{f.cruise_fl}, {f.groundspeed_kt:.0f} kt"
               + (" from your times" if f.arrival_source == "user input"
                  else f" for {f.aircraft}" if f.aircraft else "") + " . "
               "route: great-circle guess")
    if brief.status != "ok":
        return "\n".join([*out, "", brief.message or "", "", *brief.notes])
    lead = f.departure_utc - brief.identity.issued_at
    arrives = ("Arrives {} ({}), as you entered" if f.arrival_source == "user input"
               else "Arrives about {} ({})")
    out.append(arrives.format(_local(f.arrival_utc, f.destination.tz), hhmm(f.arrival_utc))
               + f" . {f.distance_nm:,.0f} nm . "
               f"issued {age(lead)} before departure")
    out.append(f"In the air about {hhmm(f.takeoff_utc)} to {hhmm(f.landing_utc)} . "
               "hour 1 starts at take-off")
    out += ["", *ride_block(brief), brief.summary, ""]
    out.append("hour  " + " ".join(f"{h.index:>2}" for h in brief.hours))
    out.append("      " + " ".join(f"{SYMBOL[h.state]:>2}" for h in brief.hours) + "     " + LEGEND)
    out += ["", "What each source says"]
    for key, name in SOURCES:
        for i, line in enumerate(_grouped(brief.hours, key)):
            out.append(f"  {name if i == 0 else '':<19}{line}")
    agree = [f"{_span(h.index, h.index)}: "
             + {"agree": "the sources that cover it agree", "disagree": "the sources disagree",
                "single": "one source only"}[h.agreement]
             for h in brief.hours if h.agreement and h.state == "flagged"]
    if agree:
        out.append(f"  {'Agreement':<19}" + " . ".join(agree))

    quoted: list[str] = []
    seen: set[str] = set()
    for h in brief.hours:
        for w in [*h.warnings, *h.gairmets]:
            if w.hazard_id not in seen:
                seen.add(w.hazard_id)
                quoted.append(f"  hour {h.index}, {w.what}, {levels(w.base_ft, w.top_ft)}, "
                              f'valid to {hhmm(w.valid_to)}: "{w.quoted}"')
        for r in h.reports:
            level = f"FL{r.flight_level:03d}" if r.flight_level is not None else "level not given"
            quoted.append(f"  hour {h.index}, pilot report at {hhmm(r.observed_at)}, {level}, "
                          f'{r.distance_nm:.0f} nm from the route: "{r.quoted}"')
    if quoted:
        out += ["", "The sources' own words", *quoted]

    out += ["", "Near the airports"]
    if not brief.near_airports:
        warn = brief.hours[0].sources.get("warnings") if brief.hours else None
        out.append("  " + ("Official warnings are stale, so this is not known"
                           if warn and warn.state == "stale"
                           else "No warning in force within 150 nm of either airport, "
                                "surface to cruise level"))
    for n in brief.near_airports:
        where = "below cruise level" if n.below_cruise else "up to cruise level"
        out.append(f"  {n.airport} {n.phase}: {n.what} {where}, {levels(n.base_ft, n.top_ft)}, "
                   f'valid to {hhmm(n.valid_to)}: "{n.quoted}"')

    out.append("")
    for end, label in (("origin", "departure"), ("destination", "arrival")):
        a = brief.airports[end]
        place = f.origin if end == "origin" else f.destination
        out.append(f"{place.location} ({a.code}), {label} {hhmm(a.at)}")
        out.append(f"  TAF for then   {a.taf.text}")
        if a.taf.quoted:
            out.append(f'                 "{a.taf.quoted}"')
        out.append(f"  METAR now      {a.metar.text}")
        if a.metar.quoted:
            out.append(f'                 "{a.metar.quoted}"')
        if a.crosswind:
            out.append(f"  Crosswind      {a.crosswind.text}")

    if brief.warnings_without_area:
        out += ["", f"{len(brief.warnings_without_area)} warning(s) worldwide came without an "
                    "area and could not be placed on the route (listed in the record)"]
    out += ["", "Assumptions", *[f"  {a}" for a in f.assumptions]]
    out += ["", *brief.notes]
    stale = [p.note for p in brief.provenance if p.stale and p.note]
    out += stale
    used = sorted({p.licence for p in brief.provenance})
    out.append("Sources: " + " . ".join(used))
    return "\n".join(out)
