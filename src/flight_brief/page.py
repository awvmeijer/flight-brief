"""The pages, drawn after docs/UI.md: Breakwise's night sheet, one green, read as a head-up
display. In step with `render.py`: a page says what the text renderer says.

The styles are files the app serves (`static/straat/straat.css`, `static/fb.css`); nothing is
styled inline, so the Content-Security-Policy can refuse inline styles and scripts. Two scripts
are optional conveniences (`static/app.js`: airport suggestions and the loading screen;
`static/map.js`: the Mapbox route map). Everything else works without JavaScript.

Wording rules this module keeps (docs/DESIGN.md): no verdict and no overall score, and no
severity word of its own. Every piece of an official source's own text is put inside a
`<q class="quoted">` element and nothing else is, so a check can strip those and search the rest.
"""

import json
import math
import re
from datetime import date
from html import escape
from typing import Any
from urllib.parse import urlencode

from .brief import NOT_OPS, SHEAR_RATES, SPEAKS, Brief, Hour, age, day_hhmm, hhmm, levels
from .render import SOURCES, _grouped, _local, _span
from .schedule import NAME as SCHEDULE_NAME
from .schedule import SITE as SCHEDULE_SITE
from .schedule import Scheduled
from .service import Problem
from .vrs import Airport, RouteFlight

SITE = "Flight Brief"
ASSETS = "2"  # bump when a file under static/ changes, so browsers fetch it again
MAPBOX_GL = "https://api.mapbox.com/mapbox-gl-js/v3.33.0"
STATE_WORDS = {"flagged": "flagged", "nothing_found": "nothing found", "too_early": "too early",
               "not_covered": "not covered", "stale": "source stale",
               "none_issued": "none issued", "no_reports": "no reports"}
SYMBOL = {"flagged": "◆", "nothing_found": "○", "too_early": "?",
          "not_covered": "–", "stale": "!"}
# A source line has two more states than an hour: both mean the source is silent, not clear.
LANE_SYMBOL = {**SYMBOL, "none_issued": "\u00b7", "no_reports": "\u00b7"}
UNKNOWN = ("too_early", "none_issued", "no_reports", "not_covered", "stale")
AGREEMENT = {"agree": "the sources that cover it agree", "disagree": "the sources disagree",
             "single": "one source only"}
# The query parameters of a brief page, in the order a URL carries them.
PARAMS = ("dep", "arr", "aircraft", "from", "to", "fl", "gs", "airline")
DOT = "·"
GUESS = "The route is a great-circle guess, not the filed route"
READING = ("Official warnings", "Pilot reports", "Forecasts", "Airport weather")


# --- small helpers -------------------------------------------------------------------------

def e(value: Any) -> str:
    return escape(str(value), quote=True)


def quoted(text: str, raw: bool = False) -> str:
    """An official source's own text."""
    return f'<q class="quoted{" raw" if raw else ""}">{e(text)}</q>'


def own(text: str) -> str:
    """This project's own line of text, escaped. The engine puts a source's words inside
    double quotation marks within some lines; those spans become `<q>` elements."""
    parts = re.split(r'"([^"]*)"', text)
    return "".join(quoted(part) if i % 2 else e(part) for i, part in enumerate(parts))


def flight_url(code: str, day: str, leg: int | str, values: dict[str, str]) -> str:
    """The shareable address of one flight and leg."""
    query = urlencode([(k, values[k]) for k in PARAMS if values.get(k)], safe=":")
    return f"/f/{code}/{day}/{leg}" + (f"?{query}" if query else "")


def go_url(values: dict[str, str]) -> str:
    """The address the form submits to; it redirects to the flight's own address."""
    keys = ("code", "date", *PARAMS)
    return "/go?" + urlencode([(k, values[k]) for k in keys if values.get(k)], safe=":")


def home_url(values: dict[str, str]) -> str:
    """The form with a flight already filled in."""
    keys = ("code", "date", *PARAMS)
    return "/?" + urlencode([(k, values[k]) for k in keys if values.get(k)], safe=":")


def route_search_url(values: dict[str, str]) -> str:
    """The address the route form submits to: two places as typed, and a date."""
    return "/r?" + urlencode([(k, values[k]) for k in ("from", "to", "date") if values.get(k)])


def route_url(origin: str, destination: str, day: str) -> str:
    """The address of one route on one day: two IATA airport codes and the date."""
    return f"/r/{origin}/{destination}/{day}"


def plain_model(model: str) -> str:
    """A schedule's name for an aircraft, cut down to what a brief's address carries:
    letters, digits, spaces and hyphens, forty characters at most."""
    text = " ".join(re.sub(r"[^A-Za-z0-9 \-]+", " ", model).split())[:40]
    return text.strip(" -")


def long_date(day: str) -> str:
    at = date.fromisoformat(day)
    return f"{at:%A} {at.day} {at:%B %Y}"


def cap(text: str) -> str:
    """A label in the machine's voice."""
    return f'<span class="cap">{e(text)}</span>'


def win(title: str, meta: str, body: str, *, foot: str = "", die: str = "",
        kind: str = "") -> str:
    """A window: bar (die, title, meta), body, foot. `die` is "", "off" or "warn"."""
    tail = f'<div class="st-win-foot">{foot}</div>' if foot else ""
    return (f'<section class="st-win{" " + kind if kind else ""}"><div class="st-win-bar">'
            f'<i class="die{" " + die if die else ""}"></i><b>{e(title)}</b>'
            f'<span class="meta">{e(meta)}</span></div>'
            f'<div class="st-win-body">{body}</div>{tail}</section>')


SCHEDULE_CREDIT = (f'<span>Schedule: <a href="{SCHEDULE_SITE}">{SCHEDULE_NAME}</a></span>'
                   "<span>Times are local at each airport</span>")


# --- the head-up display -------------------------------------------------------------------
# Line drawings, as SVG. A tape or a box shows a number only when the page has that number.

def _heading_tape(cx: float, y: float, track: int | None, half: float = 150,
                  px: float = 3.0) -> str:
    centre = 0 if track is None else track
    out = [f'<line class="dim" x1="{cx - half}" y1="{y}" x2="{cx + half}" y2="{y}"/>']
    for d in range(centre - 50, centre + 51):
        if d % 5:
            continue
        x = cx + (d - centre) * px
        if abs(x - cx) > half:
            continue
        big = d % 10 == 0
        out.append(f'<line class="{"" if big else "dim"}" x1="{x}" y1="{y}" x2="{x}" '
                   f'y2="{y + (9 if big else 5)}"/>')
        if big and track is not None and abs(x - cx) > 26:
            out.append(f'<text class="dimt" x="{x}" y="{y + 22}" text-anchor="middle">'
                       f"{(d % 360) // 10:02d}</text>")
    out.append(f'<path d="M{cx - 6} {y - 8}L{cx} {y - 1}L{cx + 6} {y - 8}"/>')
    label = "---" if track is None else f"{track:03d}"
    out.append(f'<rect class="box" x="{cx - 24}" y="{y + 12}" width="48" height="20"/>'
               f'<text class="big" x="{cx}" y="{y + 27}" text-anchor="middle">{label}</text>')
    return "".join(out)


def _tape(x: float, cy: float, value: int | None, label: str, step: int, side: int) -> str:
    """A vertical tape with a boxed read-out; the box is left (-1) or right (1) of the line."""
    out = [f'<line class="dim" x1="{x}" y1="{cy - 62}" x2="{x}" y2="{cy + 62}"/>']
    for k in range(-3, 4):
        yy = cy - k * 18
        out.append(f'<line class="dim" x1="{x}" y1="{yy}" x2="{x + side * 7}" y2="{yy}"/>')
        if value is not None and k and abs(k) != 3:
            anchor = "start" if side > 0 else "end"
            out.append(f'<text class="dimt" x="{x + side * 12}" y="{yy + 4}" '
                       f'text-anchor="{anchor}">{value + k * step}</text>')
    bx = x + (10 if side > 0 else -66)
    shown = "---" if value is None else str(value)
    out.append(f'<rect class="box" x="{bx}" y="{cy - 12}" width="56" height="24"/>'
               f'<text class="big" x="{bx + 28}" y="{cy + 5}" text-anchor="middle">{shown}</text>'
               f'<text class="dimt" x="{x}" y="{cy - 72}" text-anchor="middle">{label}</text>')
    return "".join(out)


def _fpv(cx: float, cy: float, r: float = 9) -> str:
    """The flight path vector: where the aircraft is going."""
    return (f'<g class="glow"><circle cx="{cx}" cy="{cy}" r="{r}"/>'
            f'<line x1="{cx - r}" y1="{cy}" x2="{cx - r - 22}" y2="{cy}"/>'
            f'<line x1="{cx + r}" y1="{cy}" x2="{cx + r + 22}" y2="{cy}"/>'
            f'<line x1="{cx}" y1="{cy - r}" x2="{cx}" y2="{cy - r - 11}"/></g>')


def _ladder(cx: float, cy: float) -> str:
    out = [f'<line x1="{cx - 210}" y1="{cy}" x2="{cx - 46}" y2="{cy}"/>'
           f'<line x1="{cx + 46}" y1="{cy}" x2="{cx + 210}" y2="{cy}"/>']
    for deg, off in ((5, -34), (10, -68), (-5, 34), (-10, 68)):
        y, half = cy + off, 44 if abs(deg) == 10 else 26
        dash = ' stroke-dasharray="6 5"' if deg < 0 else ""
        tick = 6 if deg > 0 else -6
        out.append(f'<path class="dim"{dash} d="M{cx - half} {y + tick}V{y}H{cx - 14} '
                   f'M{cx + 14} {y}H{cx + half}V{y + tick}"/>')
        if abs(deg) == 10:
            out.append(f'<text class="dimt" x="{cx + half + 8}" y="{y + 4}">{abs(deg)}</text>')
    return "".join(out)


def hud_band(track: int, groundspeed: float, level: int, caption: str) -> str:
    """One flight between its two airports: initial track, groundspeed, cruise level."""
    cx, cy = 300, 114
    return ('<svg class="hud" viewBox="0 0 600 216" role="img" '
            f'aria-label="Initial track {track:03d} degrees, groundspeed {groundspeed:.0f} knots, '
            f'flight level {level}">'
            + _heading_tape(cx, 14, track, half=140, px=2.8) + _ladder(cx, cy) + _fpv(cx, cy)
            + _tape(72, cy, round(groundspeed), "GS KT", 20, -1)
            + _tape(528, cy, level, "FL", 10, 1)
            + f'<text class="dimt" x="{cx}" y="210" text-anchor="middle">{e(caption.upper())}</text>'
            "</svg>")


def _hero_hud() -> str:
    """The display with no flight in it yet: scales, and dashes where the numbers will be."""
    cx, cy = 260, 150
    return ('<svg class="hud" viewBox="0 0 520 300" aria-hidden="true">'
            + _heading_tape(cx, 18, None, half=170) + _ladder(cx, cy) + _fpv(cx, cy, 11)
            + _tape(92, cy, None, "GS KT", 20, -1) + _tape(428, cy, None, "FL", 10, 1) + "</svg>")


def _drone(cx: float, cy: float) -> str:
    def rotor(x: float, y: float, rx: float) -> str:
        return (f'<ellipse class="rotor" cx="{x}" cy="{y}" rx="{rx}" ry="{rx / 5:.1f}"/>'
                f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y + 7}"/>')
    return ('<g class="drone glow">'
            f'<path d="M{cx - 20} {cy - 6}H{cx + 20}L{cx + 26} {cy + 8}H{cx - 26}Z"/>'
            f'<circle cx="{cx}" cy="{cy + 15}" r="5"/>'
            f'<line x1="{cx - 22}" y1="{cy - 2}" x2="{cx - 70}" y2="{cy - 22}"/>'
            f'<line x1="{cx + 22}" y1="{cy - 2}" x2="{cx + 70}" y2="{cy - 22}"/>'
            f'<line x1="{cx - 24}" y1="{cy + 4}" x2="{cx - 92}" y2="{cy + 10}"/>'
            f'<line x1="{cx + 24}" y1="{cy + 4}" x2="{cx + 92}" y2="{cy + 10}"/>'
            + rotor(cx - 70, cy - 29, 26) + rotor(cx + 70, cy - 29, 26)
            + rotor(cx - 92, cy + 3, 34) + rotor(cx + 92, cy + 3, 34)
            + f'<path class="dim" d="M{cx - 16} {cy + 8}l-8 16 M{cx + 16} {cy + 8}l8 16"/></g>')


def loading_veil() -> str:
    """The loading screen, hidden until `app.js` shows it: a drone holding its course through
    rough air while the sources are read. It names the sources; it does not pretend to know
    which one is done, because one request reads them all."""
    cx, cy = 380, 170
    streaks = "".join(
        f'<line class="streak {"warn" if i in (2, 7) else "dim"}" x1="{x}" y1="{y}" '
        f'x2="{x - 240}" y2="{y + 90}"/>'
        for i, (x, y) in enumerate([(700, 20), (760, 90), (640, 150), (760, 220), (560, 10),
                                    (300, 0), (420, 240), (200, 40), (760, 300), (500, 280)]))
    hud = ('<svg class="hud" viewBox="0 0 760 340" role="img" '
           'aria-label="A drone holding its course through rough air">'
           f'{streaks}<g class="horizon">{_ladder(cx, cy)}'
           f'<line class="faint" x1="-200" y1="{cy}" x2="960" y2="{cy}"/></g>'
           + _heading_tape(cx, 18, None, half=180) + _drone(cx, cy) + "</svg>")
    steps = "".join(f"<li>{e(name)}</li>" for name in READING)
    return ('<div id="fb-loading" class="fb-veil st-night st-grid" hidden role="status" '
            'aria-live="polite"><div class="fb-load">'
            f'<span class="cap" data-what></span>{hud}'
            "<p class=\"fb-load-title\">Reading the sky along your route</p>"
            f'<ul class="fb-steps" aria-label="Sources being read">{steps}</ul></div></div>')


# --- the frame -----------------------------------------------------------------------------

def layout(title: str, body: str, *, noindex: bool = False, mapbox: bool = False) -> str:
    robots = '<meta name="robots" content="noindex">\n' if noindex else ""
    extra_css = f'<link rel="stylesheet" href="{MAPBOX_GL}/mapbox-gl.css">\n' if mapbox else ""
    extra_js = (f'<script src="{MAPBOX_GL}/mapbox-gl.js" defer></script>\n'
                f'<script src="/static/map.js?v={ASSETS}" defer></script>\n' if mapbox else "")
    return (
        '<!doctype html>\n<html lang="en" data-theme="dark">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'{robots}'
        '<meta name="color-scheme" content="dark">\n'
        f'<title>{e(title)}</title>\n<link rel="icon" href="data:,">\n'
        f'<link rel="stylesheet" href="/static/straat/straat.css?v={ASSETS}">\n'
        f'<link rel="stylesheet" href="/static/fb.css?v={ASSETS}">\n{extra_css}'
        f'<script src="/static/app.js?v={ASSETS}" defer></script>\n{extra_js}'
        '</head>\n<body class="fb st-night st-grid">\n<div class="fb-wrap">\n'
        '<header class="fb-top"><a class="fb-word" href="/">flight brief<i></i></a>'
        '<nav class="fb-nav"><a href="/">Another flight</a></nav></header>\n'
        f'<main>\n{body}\n</main>\n'
        f'<footer class="fb-foot"><span><b>{e(NOT_OPS)}.</b> Weather for passengers, read from '
        'public sources; each page lists them.</span>'
        '<span class="cap">Open source</span></footer>\n</div>\n'
        f'{loading_veil()}\n</body>\n</html>\n')


# --- the forms and the pages that are not a brief ------------------------------------------

def _field(v: dict[str, str], name: str, label: str, attrs: str, sub: str = "") -> str:
    value = f' value="{e(v[name])}"' if v.get(name) else ""
    return (f'<label class="st-field"><span class="lab">{label}</span>'
            f'<input class="st-input" name="{name}"{value} {attrs}>'
            f'<span class="sub">{e(sub)}</span></label>')


def route_form(values: dict[str, str] | None = None) -> str:
    """The first way in: where from, where to, and the day."""
    v = values or {}
    place = ('type="text" required maxlength="40" autocomplete="off" spellcheck="false" '
             "data-airport")
    return "\n".join([
        '<form class="fb-search" action="/r" method="get">',
        _field(v, "from", "From", place + ' placeholder="Amsterdam"', "City or airport code"),
        '<button type="button" class="fb-swap" aria-label="Swap the two airports">⇄</button>',
        _field(v, "to", "To", place + ' placeholder="São Paulo"', "City or airport code"),
        _field(v, "date", "Date", 'type="date" required', "Local at departure"),
        '<button class="st-btn st-btn-solid"><span>Show flights</span>'
        '<span class="st-btn-arrow">↗</span></button>', "</form>"])


def form(values: dict[str, str] | None = None, *, airports: bool = False,
         need_time: bool = True) -> str:
    """The second way in: a flight number. `need_time` is False when a schedule can look
    the departure time up, so the field may be left empty."""
    v = values or {}
    need = airports or bool(v.get("from") or v.get("to"))
    out = ['<form class="fb-num" action="/go" method="get" data-brief>',
           '<div class="fb-fields">',
           _field(v, "code", "Flight number", 'type="text" required maxlength="10" '
                  'placeholder="KL791" autocapitalize="characters" autocomplete="off" '
                  'spellcheck="false"', ""),
           _field(v, "date", "Departure date", 'type="date" required', ""),
           _field(v, "dep", "Departure time, local",
                  'type="time" required' if need_time else 'type="time"',
                  "" if need_time else "Leave empty to have it looked up"),
           "</div>"]
    if need:
        code = ('type="text" maxlength="4" autocapitalize="characters" autocomplete="off" '
                'spellcheck="false"' + (" required" if airports else ""))
        out += ['<fieldset><legend class="cap">The two airports, as three-letter or '
                "four-letter codes</legend>", '<div class="fb-fields">',
                _field(v, "from", "From", code + ' placeholder="SIN"', ""),
                _field(v, "to", "To", code + ' placeholder="LHR"', ""), "</div></fieldset>"]
    opened = " open" if v.get("arr") or v.get("aircraft") else ""
    out += [f'<details class="st-details"{opened}><summary>Optional</summary>',
            '<div class="fb-fields">',
            _field(v, "arr", "Arrival time, local at the destination", 'type="time"', ""),
            _field(v, "aircraft", "Aircraft type",
                   'type="text" maxlength="40" autocomplete="off" spellcheck="false" '
                   'placeholder="777, A350, Dash 8"', ""),
            "</div></details>"]
    for name in ("fl", "gs", "airline"):
        if v.get(name):
            out.append(f'<input type="hidden" name="{name}" value="{e(v[name])}">')
    out.append('<p class="fb-actions"><button class="st-btn st-btn-line"><span>Brief me</span>'
               "</button></p>")
    if not need:
        out.append('<p class="fb-note">Flight number not matched to a route? '
                   '<a href="/?airports=1">Enter the two airports</a>.</p>')
    out.append("</form>")
    return "\n".join(out)


PROMISES = (
    ("Sources, not a verdict", "Official warnings, pilot reports and forecasts, each in its "
                               "own lane. Nothing adds them up into one number."),
    ("What it cannot know", "Too early, not covered, out of date: said as plainly as what "
                            "was found."),
    ("Every source named", "Each page lists what was read, when, and how old it was."),
)


def home_page(values: dict[str, str] | None = None, *, message: str | None = None,
              airports: bool = False, route: dict[str, str] | None = None,
              need_time: bool = True) -> str:
    """The two forms. `values` fills the flight-number form and `route` the route form."""
    note = f'<p class="fb-notice" role="alert">{own(message)}</p>' if message else ""
    promises = "".join(f"<div>{cap(f'{i:02d}')}<h3>{e(head)}</h3><p>{e(text)}</p></div>"
                       for i, (head, text) in enumerate(PROMISES, 1))
    number_first = bool(message and (values or airports) and not route)
    by_route = win("Find your flight", "route and date", route_form(route))
    by_number = win("Or a flight number",
                    "type the time" if need_time else "time and aircraft looked up",
                    form(values, airports=airports, need_time=need_time), die="off")
    forms = (by_number + by_route) if number_first else (by_route + by_number)
    body = (
        '<section class="fb-hero"><div>' + cap("Weather for one flight, hour by hour")
        + "<h1>What the sky says about <em>your flight</em>.</h1>"
        "<p>Every public source for your route, side by side. Where they agree, where they "
        f"differ, and what nobody can know yet.</p></div><div>{_hero_hud()}</div></section>\n"
        f"{note}\n{forms}\n"
        f'<div class="fb-three">{promises}</div>\n'
        '<p class="fb-note">Times are the gate times on the schedule or the ones you type. '
        "Delays, cancellations and whether the flight runs that day are not tracked.</p>")
    return layout(SITE, body, noindex=bool(message or values or route))


def message_page(title: str, text: str) -> str:
    body = win(title, "", f"<h1 class=\"fb-h\">{e(title)}</h1>\n<p>{e(text)}</p>\n"
               '<p class="fb-actions"><a class="st-btn st-btn-line st-btn-sm" href="/">'
               "<span>Brief a flight</span></a></p>", die="off")
    return layout(f"{title} {DOT} {SITE}", body, noindex=True)


def _choices(items: list[tuple[str, str, bool]]) -> str:
    """Links to choose from: (address, inner HTML, whether it leads to a brief)."""
    return ('<ul class="fb-choices">\n' + "\n".join(
        f'<li><a href="{e(url)}"{" data-brief" if brief else ""}>{inner}</a></li>'
        for url, inner, brief in items) + "\n</ul>")


def problem_page(problem: Problem, values: dict[str, str], *, need_time: bool = True) -> str:
    """A request that did not give a brief, as a page: a choice of legs or airlines as links,
    or the form again with the message beside it."""
    code, day = values.get("code", ""), values.get("date", "")
    if problem.status == "choose_leg" and problem.legs:
        items = [(flight_url(code, day, int(x["index"]), values),
                  f'<b>Leg {int(x["index"])}</b> {e(x["origin"])} to {e(x["destination"])}',
                  True) for x in problem.legs]
        body = (f'<header class="fb-route"><h1>{e(code)} has {len(problem.legs)} legs</h1>'
                "<p>Choose the one you are on.</p></header>\n"
                + win("Legs", f"{len(problem.legs)}", _choices(items)))
        return layout(f"{code} {DOT} {SITE}", body, noindex=True)
    if problem.status in ("choose_airline", "ambiguous") and problem.candidates:
        items = [(go_url({**values, "airline": str(c.get("airline", ""))}),
                  f'<b>{e(c.get("name", ""))}</b> ({e(c.get("airline", ""))}), callsign '
                  f'{e(c.get("callsign", ""))}: '
                  f'{e(" to ".join(map(str, c.get("airports", []))))}', False)
                 for c in problem.candidates]
        body = (f'<header class="fb-route"><h1>{e(code)} is used by more than one airline</h1>'
                "<p>Choose yours.</p></header>\n"
                + win("Airlines", f"{len(problem.candidates)}", _choices(items)))
        return layout(f"{code} {DOT} {SITE}", body, noindex=True)
    unmapped = problem.status in ("could_not_map", "unmapped")
    return home_page(values, message=problem.message, airports=unmapped, need_time=need_time)


# --- finding the flight --------------------------------------------------------------------

def _airport_line(place: Airport) -> str:
    return (f"<b>{e(place.name)}</b> ({e(place.iata)}), {e(place.location)}, "
            f"{e(place.country)}")


def airport_choice_page(side: str, matches: list[Airport], values: dict[str, str]) -> str:
    """Several airports fit what was typed for one end of the route (`side` is from or to):
    each as a link that asks again with that airport's code and the other fields kept."""
    items = [(route_search_url({**values, side: place.iata}), _airport_line(place), False)
             for place in matches]
    which = "from" if side == "from" else "to"
    body = (f'<header class="fb-route"><h1>Which airport are you flying {which}?</h1>'
            "<p>More than one airport fits what you typed. Choose yours.</p></header>\n"
            + win("Airports", f"{len(matches)}", _choices(items)))
    return layout(f"Which airport {DOT} {SITE}", body, noindex=True)


def _flight_row(f: Scheduled, url: str, *, ends: bool = False) -> str:
    arr = e(f.arr_local) if f.arr_local else "--:--"
    next_day = ("<sup>+1</sup>" if f.arr_utc and f.dep_utc
                and f.arr_local and f.arr_local < f.dep_local else "")
    where = f"{f.origin} {DOT} {f.destination}" if ends else ""
    label = f"{f.number} {DOT} {f.origin} to {f.destination}"
    return (f'<li><a class="fb-flight" href="{e(url)}" data-brief="{e(label)}">'
            f'<span class="t">{e(f.dep_local)}<i></i>{arr}{next_day}</span>'
            f'<span class="d cap">{e(where)}</span>'
            f'<span class="w"><b>{e(f.number)}</b>{e(f.airline)}</span>'
            f'<span class="a">{e(f.aircraft or "")}</span>'
            '<span class="go">Brief ↗</span></a></li>')


def scheduled_url(f: Scheduled, origin: str, destination: str) -> str:
    """The brief for one scheduled flight. The airports go in the address, so the brief does
    not depend on the route table knowing the number."""
    values = {"dep": f.dep_local, "arr": f.arr_local or "",
              "aircraft": plain_model(f.aircraft or ""), "from": origin, "to": destination}
    return flight_url(f.number, f.day, 1, values)


def numbers_list(numbers: list[RouteFlight], origin: Airport, destination: Airport,
                 day: str) -> str:
    """Flight numbers the route table has for the pair, with no times: each opens the form
    with the number, the date and the two airports filled in."""
    items = [(home_url({"code": n.number, "date": day, "from": origin.iata,
                        "to": destination.iata}),
              f"<b>{e(n.number)}</b> {e(n.airline)}", False) for n in numbers]
    return win("Flight numbers on this route", f"{len(numbers)}",
               "<p>Departure times are not known here, so choose your flight number and type "
               "the departure time on your booking. These numbers have flown this route; "
               "whether one runs on your day is not known here.</p>\n" + _choices(items),
               foot="<span>Route table: VRS standing-data, CC0</span>", die="off")


def route_page(origin: Airport, destination: Airport, day: str,
               flights: list[Scheduled] | None, numbers: list[RouteFlight]) -> str:
    """One route on one day. `flights` is what the schedule has: None when there is no
    schedule to ask, an empty list when it answered that it has none. `numbers` is the
    fallback from the route table, shown when there are no scheduled flights to list."""
    title = (f"{origin.location} ({origin.iata}) to {destination.location} "
             f"({destination.iata})")
    out = ['<a class="fb-crumb" href="/">‹ Change route or date</a>',
           f'<header class="fb-route">{cap(long_date(day))}'
           f"<h1>{e(origin.location)}<i>→</i>{e(destination.location)}</h1>"
           f"<p>{e(origin.iata)} {e(origin.name)} to {e(destination.iata)} "
           f"{e(destination.name)}</p></header>"]
    if flights:
        rows = "\n".join(_flight_row(f, scheduled_url(f, origin.iata, destination.iata))
                         for f in flights)
        out.append(win("Flights", f"{len(flights)} that day",
                       f'<ol class="fb-flights">\n{rows}\n</ol>', foot=SCHEDULE_CREDIT,
                       kind="flush"))
        out.append('<p class="fb-note">Choose your flight for its weather brief. Delays, '
                   "cancellations and aircraft swaps are not tracked here.</p>")
    elif flights is not None:
        out.append('<p class="fb-notice">No flights were found on this route that day.</p>')
    if not flights and numbers:
        out.append(numbers_list(numbers, origin, destination, day))
    return layout(f"{title}, {day} {DOT} {SITE}", "\n".join(out), noindex=True)


def departure_choice_page(code: str, day: str, choices: list[tuple[Scheduled, str]]) -> str:
    """A flight number the schedule has more than once on the day, a number with several
    legs for one: each departure as a link to its brief."""
    rows = "\n".join(_flight_row(f, url, ends=True) for f, url in choices)
    body = (f'<header class="fb-route">{cap(long_date(day))}'
            f"<h1>{e(code)} leaves {len(choices)} times</h1>"
            "<p>Choose the one you are on.</p></header>\n"
            + win("Departures", f"{len(choices)}", f'<ol class="fb-flights">\n{rows}\n</ol>',
                  foot=SCHEDULE_CREDIT, kind="flush"))
    return layout(f"{code} {DOT} {SITE}", body, noindex=True)


# --- the brief -----------------------------------------------------------------------------

def _cell(index: int, state: str | None, *, big: bool = False, value: str = "") -> str:
    if state is None:
        return '<li class="fb-cell s-none"></li>'
    number = f'<span class="n">{index:02d}</span>' if big else ""
    shown = f'<span class="v">{e(value)}</span>' if value else ""
    return (f'<li class="fb-cell{" big" if big else ""} s-{state}" '
            f'title="Hour {index}: {STATE_WORDS[state]}">{number}'
            f'<span aria-hidden="true">{LANE_SYMBOL[state]}</span>'
            f'<span class="vh">hour {index} {STATE_WORDS[state]}</span>{shown}</li>')


def _strip(brief: Brief) -> str:
    f = brief.flight
    cells = "".join(_cell(h.index, h.state, big=True) for h in brief.hours)
    legend = "".join(
        f'<li class="cap"><span class="fb-cell s-{state}" aria-hidden="true">{symbol}</span>'
        f"{STATE_WORDS[state]}</li>" for state, symbol in SYMBOL.items())
    return (f'<ol class="fb-cells strip" aria-label="State of each flight hour">{cells}</ol>\n'
            f'<div class="fb-axis cap"><span>{e(hhmm(f.takeoff_utc))} take-off</span>'
            f"<span>{e(hhmm(f.landing_utc))} landing</span></div>\n"
            f'<ul class="fb-legend" aria-label="Key">{legend}</ul>')


def _source_keys(hours: list[Hour]) -> list[tuple[str, str]]:
    """Every source that has a line in any hour: the known ones first, in their order, then
    any other key the engine sends, under a label made from the key."""
    labels = dict(SOURCES)
    seen = [key for key, _ in SOURCES]
    for h in hours:
        seen += [key for key in h.sources if key not in seen]
    return [(key, labels.get(key) or key.replace("_", " ").capitalize()) for key in seen
            if any(key in h.sources for h in hours)]


def _lane_value(h: Hour, key: str) -> str:
    if key == "shear" and h.shear and h.shear.get("value") is not None:
        return f"{float(h.shear['value']):.0f}"
    if key == "gtg" and h.gtg and h.gtg.get("edr") is not None:
        return f"{float(h.gtg['edr']):.2f}"[1:]
    return ""


def _lanes(brief: Brief) -> str:
    hours = brief.hours
    head = ('<div class="fb-lane head"><div class="name"></div><ol class="fb-cells fb-hrs" '
            'aria-hidden="true">' + "".join(f"<li>{h.index:02d}</li>" for h in hours)
            + "</ol></div>")
    out = [head]
    for key, name in _source_keys(hours):
        speaks = sum(1 for h in hours if key in h.sources and h.sources[key].state in SPEAKS)
        cells = "".join(
            _cell(h.index, h.sources[key].state if key in h.sources else None,
                  value=_lane_value(h, key)) for h in hours)
        out.append(f'<div class="fb-lane"><div class="name"><b>{e(name)}</b>'
                   f'{cap(f"speaks for {speaks} of {len(hours)} hours")}</div>'
                   f'<ol class="fb-cells">{cells}</ol></div>')
    if any(line.state in ("none_issued", "no_reports") for h in hours
           for line in h.sources.values()):
        out.append('<p class="fb-small"><span aria-hidden="true">\u00b7</span> in a lane: the '
                   "source has nothing for that hour. That is silence, and it is not the same "
                   "as nothing found.</p>")
    return "\n".join(out)


def _sources(brief: Brief) -> str:
    out = []
    for key, name in _source_keys(brief.hours):
        lines = "".join(f"<li>{own(line)}</li>" for line in _grouped(brief.hours, key))
        out.append(f"<dt>{e(name)}</dt>\n<dd><ul>{lines}</ul></dd>")
    agree = [f"{_span(h.index, h.index)}: {AGREEMENT[h.agreement]}"
             for h in brief.hours if h.agreement and h.state == "flagged"]
    if agree:
        lines = "".join(f"<li>{e(line)}</li>" for line in agree)
        out.append(f"<dt>Agreement</dt>\n<dd><ul>{lines}</ul></dd>")
    return '<dl class="src">\n' + "\n".join(out) + "\n</dl>"


def _meter(share: int, kind: str) -> str:
    return (f'<svg class="fb-meter" viewBox="0 0 100 4" preserveAspectRatio="none" '
            f'aria-hidden="true"><rect class="bg" width="100" height="4"/>'
            f'<rect class="{kind}" width="{share}" height="4"/></svg>')


def _rates(brief: Brief) -> str:
    """How often the index's flag falls on rough and on smooth reports, as the engine states
    it. Shown only when the index has a lane."""
    if not any("shear" in h.sources for h in brief.hours):
        return ""
    found = re.findall(r"(\d+)%", SHEAR_RATES)
    if len(found) != 2:
        return ""
    rough, smooth = int(found[0]), int(found[1])
    return ("<aside class=\"fb-rates\">" + cap("How much to read into the index")
            + "<p>Measured against pilot reports, the index's flag marks:</p>"
            f'<div class="fb-rate"><span>{rough}% of rough-air reports</span>'
            f'{_meter(rough, "w")}</div>'
            f'<div class="fb-rate"><span>{smooth}% of smooth-air reports</span>'
            f'{_meter(smooth, "g")}</div>'
            '<p class="fb-small">A flag is raised over most rough air, and also over about '
            "a third of smooth air.</p></aside>")


def _own_words(brief: Brief) -> str:
    items: list[str] = []
    seen: set[str] = set()
    for h in brief.hours:
        for w in [*h.warnings, *h.gairmets]:
            if w.hazard_id not in seen:
                seen.add(w.hazard_id)
                items.append(f"hour {h.index}, {e(w.what)}, {e(levels(w.base_ft, w.top_ft))}, "
                             f"valid to {e(hhmm(w.valid_to))}: {quoted(w.quoted)}")
        for r in h.reports:
            level = f"FL{r.flight_level:03d}" if r.flight_level is not None else "level not given"
            items.append(f"hour {h.index}, pilot report at {e(hhmm(r.observed_at))}, {level}, "
                         f"{r.distance_nm:.0f} nm from the route: {quoted(r.quoted)}")
    if not items:
        return ""
    return win("The sources' own words", f"{len(items)}",
               '<ul class="fb-words">\n' + "\n".join(f"<li>{item}</li>" for item in items)
               + "\n</ul>")


def _unknown(brief: Brief) -> str:
    """What the brief cannot know, gathered from every source line and airport line that is
    too early, not covered or stale, plus the two things no brief knows."""
    items: list[str] = []
    for key, name in _source_keys(brief.hours):
        found: list[tuple[int, str]] = []
        for state in UNKNOWN:
            hit = [h for h in brief.hours if key in h.sources and h.sources[key].state == state]
            if not hit:
                continue
            first = hit[0].sources[key]
            span = (_span(hit[0].index, hit[-1].index)
                    if len(hit) == hit[-1].index - hit[0].index + 1
                    else f"{len(hit)} of {len(brief.hours)} hours")
            again = min((h.sources[key].check_after for h in hit
                         if h.sources[key].check_after), default=None)
            # The engine's own line may already say when to look again.
            tag = (f'<br><span class="fb-again">Check again from {e(day_hhmm(again))}</span>'
                   if again and "check again" not in first.text.lower() else "")
            found.append((hit[0].index,
                          f"<li><b>{e(name)}</b>, {span}: {own(first.text)}{tag}</li>"))
        items += [line for _, line in sorted(found)]
    for end in ("origin", "destination"):
        a = brief.airports.get(end)
        if a is None:
            continue
        place = brief.flight.origin if end == "origin" else brief.flight.destination
        for label, line in (("forecast", a.taf), ("observation", a.metar)):
            if line.state != "ok":
                items.append(f"<li><b>{e(place.location)}, {label}</b>: {own(line.text)}</li>")
    items.append(f"<li><b>The route</b>: {e(GUESS[0].lower() + GUESS[1:])}. The filed route can "
                 "lie well to either side.</li>")
    items.append("<li><b>Delays and cancellations</b> are not tracked. Check your airline.</li>")
    return win("What this brief cannot know yet", f"{len(items)}",
               '<ul class="fb-unknown">\n' + "\n".join(items) + "\n</ul>", die="off")


def _near(brief: Brief) -> str:
    if not brief.near_airports:
        warn = brief.hours[0].sources.get("warnings") if brief.hours else None
        text = ("Official warnings are stale, so this is not known"
                if warn and warn.state == "stale"
                else "No warning in force within 150 nm of either airport, "
                     "surface to cruise level")
        return win("Near the airports", "climb and descent", f"<p>{text}</p>", die="off")
    items = []
    for n in brief.near_airports:
        where = "below cruise level" if n.below_cruise else "up to cruise level"
        items.append(f"<li>{e(n.airport)} {e(n.phase)}: {e(n.what)} {where}, "
                     f"{e(levels(n.base_ft, n.top_ft))}, valid to {e(hhmm(n.valid_to))}: "
                     f"{quoted(n.quoted)}</li>")
    return win("Near the airports", f"{len(items)} in force",
               '<ul class="fb-words">\n' + "\n".join(items) + "\n</ul>", die="warn")


def _airports(brief: Brief) -> str:
    out = []
    f = brief.flight
    for end, label in (("origin", "Departure"), ("destination", "Arrival")):
        a = brief.airports[end]
        place = f.origin if end == "origin" else f.destination
        rows = []
        for name, line in (("TAF for then", a.taf), ("METAR now", a.metar)):
            raw = quoted(line.quoted, raw=True) if line.quoted else ""
            tone = "" if line.state == "ok" else ' class="unknown"'
            rows.append(f'<dt class="cap">{name}</dt>\n<dd{tone}>{own(line.text)}{raw}</dd>')
        known = a.taf.state == "ok" or a.metar.state == "ok"
        out.append(win(label, hhmm(a.at),
                       f'<div class="fb-apt"><h2>{e(place.location)} <code>{e(a.code)}</code></h2>'
                       '<dl class="wx">\n' + "\n".join(rows) + "\n</dl></div>",
                       foot="<span>NOAA Aviation Weather Center</span>",
                       die="" if known else "off"))
    return '<div class="fb-cols">\n' + "\n".join(out) + "\n</div>"


def _provenance(brief: Brief) -> str:
    used = sorted({p.licence for p in brief.provenance})
    out = ['<ul class="fb-assume">' + "".join(f"<li>{e(text)}</li>" for text in used) + "</ul>"]
    rows = []
    for p in brief.provenance:
        rows.append(
            f"<tr><td>{e(p.source)}</td>"
            f"<td>{e(day_hhmm(p.issued_at)) if p.issued_at else 'not known'}</td>"
            f"<td>{e(day_hhmm(p.fetched_at)) if p.fetched_at else 'not known'}</td>"
            f"<td>{e(p.url_without_secrets or '')}</td></tr>")
    if rows:
        out.append('<details class="st-details"><summary>Each file read for this brief</summary>\n'
                   '<div class="st-table-scroll"><table class="st-table">\n<thead><tr>'
                   "<th>Source</th><th>As of</th><th>Fetched</th><th>Address</th></tr></thead>\n"
                   "<tbody>" + "\n".join(rows) + "\n</tbody></table></div></details>")
    return win("Sources and licences", f"{len(brief.provenance)} files read", "\n".join(out))


def _track(brief: Brief) -> int:
    pts = brief.route.points
    if len(pts) < 2:
        return 0
    la1, lo1, la2, lo2 = map(math.radians, (*pts[0], *pts[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return round((math.degrees(math.atan2(y, x)) + 360) % 360) % 360


def _head(brief: Brief) -> str:
    f = brief.flight
    lead = f.departure_utc - brief.identity.issued_at
    arrives = "" if f.arrival_source == "user input" else "about "
    minutes = int((f.arrival_utc - f.departure_utc).total_seconds() // 60)
    basis = ("from your times" if f.arrival_source == "user input"
             else f"assumed for {f.aircraft}" if f.aircraft else "assumed")
    caption = (f"{minutes // 60} h {minutes % 60:02d} {DOT} {f.distance_nm:,.0f} nm {DOT} "
               f"speed and level {basis}")
    sub = [f"leg {brief.identity.leg}", f"{f.origin.code} to {f.destination.code}"]
    if f.callsign:
        sub.append(f"callsign {f.callsign}")
    issued = f"Issued {day_hhmm(brief.identity.issued_at)}"
    if lead.total_seconds() > 0:
        issued += f" {DOT} {age(lead)} before departure"
    return (
        '<a class="fb-crumb" href="/">‹ Another flight</a>\n'
        f'<h1 class="vh">{e(f.code)} {DOT} {e(f.origin.location)} to '
        f"{e(f.destination.location)}</h1>\n"
        f'<div class="fb-id"><span class="fb-code">{e(f.code)}</span>'
        f'{cap(f" {DOT} ".join(sub))}<span class="cap right">{e(issued)}</span></div>\n'
        '<header class="fb-od">'
        f'<div class="fb-end"><span class="iata">{e(f.origin.iata or f.origin.code)}</span>'
        f'<span class="city">{e(f.origin.location)}</span>'
        f'<span class="time">Departs {e(_local(f.departure_utc, f.origin.tz))} '
        f"({e(hhmm(f.departure_utc))}), as you entered</span></div>"
        f'<div class="fb-end r"><span class="iata">'
        f"{e(f.destination.iata or f.destination.code)}</span>"
        f'<span class="city">{e(f.destination.location)}</span>'
        f'<span class="time">Arrives {arrives}{e(_local(f.arrival_utc, f.destination.tz))} '
        f"({e(hhmm(f.arrival_utc))})</span></div>"
        f'<div class="fb-hudband">{hud_band(_track(brief), f.groundspeed_kt, f.cruise_fl, caption)}'
        "</div></header>")


def brief_body(brief: Brief, mapbox: str | None = None) -> str:
    """Everything on the page that comes from the brief itself: a live brief and the same
    brief read back from a saved record give the same text."""
    f = brief.flight
    out = [_head(brief)]
    if brief.status != "ok":
        notes = "".join(f"<li>{own(note)}</li>" for note in brief.notes)
        out.append(win("No brief yet", brief.status.replace("_", " "),
                       f'<p class="fb-sum message">{own(brief.message or "")}</p>'
                       f'<ul class="fb-assume">{notes}</ul>', die="off"))
        return "\n".join(out)

    flagged = sum(1 for h in brief.hours if h.state == "flagged")
    # The engine's summary is several sentences: the first is the headline, the rest follow.
    first, _, rest = brief.summary.partition(". ")
    more = f'<p class="fb-more">{own(rest)}</p>\n' if rest else ""
    headline = (f'<p class="fb-sum summary">{own(first + ("." if rest else ""))}</p>\n{more}'
                f'{_strip(brief)}\n'
                f'<p class="fb-small">In the air about {e(hhmm(f.takeoff_utc))} to '
                f"{e(hhmm(f.landing_utc))} {DOT} hour 1 starts at take-off</p>")
    out.append('<div class="fb-cols">\n'
               + win("The flight, hour by hour", f"{len(brief.hours)} hours {DOT} {flagged} flagged",
                     headline, die="warn" if flagged else "")
               + "\n" + route_map(brief, mapbox) + "\n</div>")
    keys = _source_keys(brief.hours)
    out.append(win("What each source says", f"{len(keys)} sources",
                   f'<div class="fb-lanes"><div>{_lanes(brief)}</div>{_rates(brief)}</div>\n'
                   f"{_sources(brief)}"))
    out.append(_own_words(brief))
    out.append(_unknown(brief))
    out.append(_near(brief))
    out.append(_airports(brief))
    if brief.warnings_without_area:
        out.append(f'<p class="fb-note">{len(brief.warnings_without_area)} warning(s) worldwide '
                   "came without an area and could not be placed on the route (listed in the "
                   "record)</p>")
    notes = [*brief.notes, *[p.note for p in brief.provenance if p.stale and p.note]]
    body = ("<h2 class=\"cap\">Assumptions</h2>\n<ul class=\"fb-assume\">"
            + "".join(f"<li>{own(a)}</li>" for a in f.assumptions) + "</ul>")
    if notes:
        body += ("\n<h2 class=\"cap\">Notes</h2>\n<ul class=\"fb-assume\">"
                 + "".join(f"<li>{own(n)}</li>" for n in notes) + "</ul>")
    out.append(win("Assumptions and notes", f"{len(f.assumptions)} assumed", body))
    out.append(_provenance(brief))
    return "\n".join(part for part in out if part)


def render_page(brief: Brief, *, saved: bool = False, saved_url: str | None = None,
                current_url: str | None = None, mapbox: str | None = None) -> str:
    """The brief page. `saved` marks a page drawn from a saved record: it says so and says
    when the brief was issued. `saved_url` is where this brief's saved copy lives. `mapbox` is
    the public Mapbox token; without one the map window says the map is not set up."""
    issued = day_hhmm(brief.identity.issued_at)
    top = ""
    if saved:
        link = (f' <a href="{e(current_url)}" data-brief>Open the current brief for this '
                "flight</a>." if current_url else "")
        top = (f'<p class="fb-notice banner" role="note"><strong>Saved brief, issued {e(issued)}.'
               f"</strong> It shows what the sources said then and does not change.{link}</p>\n")
    tail = f'<p class="fb-note">Issued {e(issued)}.'
    if saved_url and not saved:
        tail += f' <a href="{e(saved_url)}">Saved copy of this brief</a>, which does not change.'
    tail += "</p>"
    f = brief.flight
    title = f"{f.code} {f.origin.code} to {f.destination.code}, {brief.identity.date} {DOT} {SITE}"
    with_map = bool(mapbox) and brief.status == "ok" and len(brief.route.points) > 1
    return layout(title, f"{top}{brief_body(brief, mapbox)}\n{tail}", noindex=True,
                  mapbox=with_map)


def brief_from_record(record: dict[str, Any]) -> Brief:
    """A brief read back from `Brief.record()`, which writes each hour's times as from and to."""
    hours = [{("start" if k == "from" else "end" if k == "to" else k): v for k, v in h.items()}
             for h in record.get("hours", [])]
    return Brief.model_validate({**record, "hours": hours})


# --- the map -------------------------------------------------------------------------------

def _nm(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points, in nautical miles."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 3440.065 * math.asin(min(1.0, math.sqrt(h)))


def map_data(brief: Brief) -> dict[str, Any]:
    """What `static/map.js` draws: each hour's stretch of the route as [lon, lat] points with
    its state, the middle of each stretch for its number, and the two airports. Longitudes
    stay continuous across 180, as the engine gives them."""
    pts = brief.route.points
    flown = [0.0]
    for a, b in zip(pts, pts[1:], strict=False):
        flown.append(flown[-1] + _nm(a, b))
    total = flown[-1] or 1.0

    def at(share: float) -> tuple[float, float]:
        target = min(1.0, max(0.0, share)) * total
        for i in range(1, len(pts)):
            if flown[i] >= target:
                span = flown[i] - flown[i - 1]
                t = (target - flown[i - 1]) / span if span else 0.0
                return (pts[i - 1][0] + t * (pts[i][0] - pts[i - 1][0]),
                        pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1]))
        return pts[-1]

    def lonlat(p: tuple[float, float]) -> list[float]:
        return [round(p[1], 4), round(p[0], 4)]

    f = brief.flight
    airborne = (f.landing_utc - f.takeoff_utc).total_seconds() or 1.0
    hours = []
    for h in brief.hours:
        a = (h.start - f.takeoff_utc).total_seconds() / airborne
        b = (h.end - f.takeoff_utc).total_seconds() / airborne
        inner = [p for p, d in zip(pts, flown, strict=True) if a * total < d < b * total]
        hours.append({"label": f"{h.index:02d}", "state": h.state,
                      "line": [lonlat(p) for p in (at(a), *inner, at(b))],
                      "mid": lonlat(at((a + b) / 2))})
    return {"hours": hours,
            "airports": [{"code": f.origin.iata or f.origin.code, "at": lonlat(pts[0])},
                         {"code": f.destination.iata or f.destination.code,
                          "at": lonlat(pts[-1])}]}


def route_map(brief: Brief, mapbox: str | None = None) -> str:
    """The route window. With a Mapbox token it carries the data `static/map.js` draws on a
    globe; without one it says the map is not set up. Either way the hour strip beside it and
    the lanes below say everything the map shows."""
    if len(brief.route.points) < 2:
        return ""
    foot = f"<span>{GUESS}</span><span>Numbers are flight hours; flagged stretches are amber</span>"
    f = brief.flight
    label = f"Route from {e(f.origin.code)} to {e(f.destination.code)}"
    if not mapbox:
        return win("Route", "map not set up",
                   f'<div class="fb-map empty" role="img" aria-label="{label}">'
                   "<p>The map is not set up on this copy of the page: it needs a Mapbox "
                   "token.</p></div>", foot=foot, die="off", kind="flush")
    data = {**map_data(brief), "token": mapbox}
    blob = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    return win("Route", "great-circle guess",
               f'<div id="fb-map" class="fb-map" role="img" aria-label="{label}, each hour\'s '
               'stretch drawn in its state">'
               "<p>The map needs JavaScript and WebGL. The hour strip and the lanes say the "
               "same in words.</p></div>"
               f'<script type="application/json" id="fb-map-data">{blob}</script>',
               foot=foot, kind="flush")
