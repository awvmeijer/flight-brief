"""The brief as an HTML page, after the design's wireframe and in step with `render.py`.

The page says what the text renderer says, in the same order. It needs no JavaScript and
makes no request to any other host: the styles are one `<style>` block, the map is inline SVG.

Wording rules this module keeps (docs/DESIGN.md): no verdict and no overall score, and no
severity word of its own. Every piece of an official source's own text is put inside a
`<q class="quoted">` element and nothing else is, so a check can strip those and search the rest.
"""

import base64
import hashlib
import math
import re
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import urlencode

from .brief import NOT_OPS, Brief, Hour, age, day_hhmm, hhmm, levels
from .render import SOURCES, SYMBOL, _grouped, _local, _span
from .service import Problem

SITE = "Flight Brief"
STATE_WORDS = {"flagged": "flagged", "nothing_found": "nothing found", "too_early": "too early",
               "not_covered": "not covered", "stale": "source stale"}
AGREEMENT = {"agree": "the sources that cover it agree", "disagree": "the sources disagree",
             "single": "one source only"}
# The query parameters of a brief page, in the order a URL carries them.
PARAMS = ("dep", "arr", "aircraft", "from", "to", "fl", "gs", "airline")
DOT = "\u00b7"
GUESS = "The route is a great-circle guess, not the filed route"

CSS = """
:root{color-scheme:light dark;--bg:#fbfbf9;--fg:#1c2024;--dim:#5b6470;--line:#d9dde2;
--card:#fff;--link:#1a4fa0;--flag-bg:#fbe3bd;--flag-fg:#7a3d00;--flag-ln:#c2620a;
--none-bg:#dbe6f8;--none-fg:#1d4380;--none-ln:#3f6fc0;--early-fg:#5b6470;--stale-bg:#eadcf5;
--stale-fg:#5a2a82;--stale-ln:#8a4fb8;--warn-bg:#fff4d6}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--fg:#e6e8ea;--dim:#9aa3ad;
--line:#343a41;--card:#1b1f23;--link:#8ab4f8;--flag-bg:#5a3408;--flag-fg:#ffd9a3;
--flag-ln:#f0a44a;--none-bg:#1f3558;--none-fg:#c5d8f7;--none-ln:#7ea6e6;--early-fg:#9aa3ad;
--stale-bg:#3d2755;--stale-fg:#e3cdf5;--stale-ln:#b98be0;--warn-bg:#3a3214}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);overflow-wrap:break-word;
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main,header.site,footer.site{max-width:46rem;margin:0 auto;padding:0 1rem}
header.site{padding-top:1rem;display:flex;justify-content:space-between;gap:1rem;
align-items:baseline;flex-wrap:wrap}
header.site a{font-weight:600;text-decoration:none;color:var(--fg)}
header.site a.more{font-weight:400;color:var(--link);text-decoration:underline}
footer.site{color:var(--dim);font-size:.875rem;padding-top:1.5rem;padding-bottom:2.5rem}
a{color:var(--link)}
h1{font-size:1.5rem;line-height:1.25;margin:1.25rem 0 .25rem}
h2{font-size:1rem;margin:1.75rem 0 .5rem;padding-top:.75rem;border-top:1px solid var(--line)}
h3{font-size:1rem;margin:1rem 0 .25rem}
p{margin:.35rem 0}
ul{margin:.35rem 0;padding-left:1.25rem}
li{margin:.25rem 0}
.dim{color:var(--dim)}
.sub{color:var(--dim);margin:0 0 .75rem}
.facts{list-style:none;padding:0;margin:.5rem 0}
.summary{font-size:1.125rem;font-weight:600;margin:1rem 0 .5rem}
.message{font-size:1.125rem;font-weight:600;margin:1rem 0;padding:.75rem 1rem;
border:1px solid var(--line);border-radius:.5rem;background:var(--card)}
.banner{margin:1rem 0 0;padding:.75rem 1rem;border-radius:.5rem;background:var(--warn-bg);
border:1px solid var(--line)}
.notice{margin:.75rem 0;padding:.6rem .9rem;border-radius:.5rem;background:var(--warn-bg);
border:1px solid var(--line)}
q.quoted{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.875rem;
overflow-wrap:anywhere}
q.raw{display:block;margin:.2rem 0 .4rem;color:var(--dim)}
.strip{list-style:none;display:flex;flex-wrap:wrap;gap:.3rem;padding:0;margin:.5rem 0}
.strip li{margin:0;width:2.75rem;padding:.2rem 0 .3rem;text-align:center;border-radius:.4rem;
border:1.5px solid var(--line);background:var(--card)}
.strip .n{display:block;font-size:.75rem;color:var(--dim)}
.strip .sym{display:block;font:700 1.25rem/1.2 ui-monospace,SFMono-Regular,Menlo,Consolas,
monospace}
.strip .t{display:block;font-size:.625rem;color:var(--dim)}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:.25rem 1rem;padding:0;margin:.5rem 0;
font-size:.875rem;color:var(--dim)}
.legend li{margin:0}
.legend .sym{display:inline-block;min-width:1.5rem;text-align:center;border-radius:.3rem;
border:1.5px solid var(--line);margin-right:.35rem;
font:700 .875rem/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.s-flagged{background:var(--flag-bg)!important;color:var(--flag-fg);
border-color:var(--flag-ln)!important}
.s-nothing_found{background:var(--none-bg)!important;color:var(--none-fg);
border-color:var(--none-ln)!important}
.s-too_early{color:var(--early-fg);border-style:dashed!important}
.s-not_covered{color:var(--early-fg);border-style:dotted!important}
.s-stale{background:var(--stale-bg)!important;color:var(--stale-fg);
border-color:var(--stale-ln)!important}
.s-flagged .n,.s-flagged .t,.s-nothing_found .n,.s-nothing_found .t,.s-stale .n,.s-stale .t
{color:inherit}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
clip-path:inset(50%);white-space:nowrap}
figure.map{margin:1rem 0 .5rem}
figure.map svg{display:block;width:100%;max-width:36rem;height:auto;border:1px solid var(--line);
border-radius:.5rem;background:var(--card)}
figcaption{font-size:.875rem;color:var(--dim);margin-top:.35rem}
.grat{stroke:var(--line);stroke-width:1;fill:none}
.gl{fill:var(--dim);font-size:10px}
.seg{fill:none;stroke-width:4;stroke-linecap:round;stroke-linejoin:round;stroke:var(--dim)}
.seg.m-flagged{stroke:var(--flag-ln);stroke-width:7}
.seg.m-nothing_found{stroke:var(--none-ln)}
.seg.m-too_early{stroke:var(--early-fg);stroke-dasharray:7 7;stroke-linecap:butt}
.seg.m-not_covered{stroke:var(--early-fg);stroke-dasharray:1 7}
.seg.m-stale{stroke:var(--stale-ln);stroke-dasharray:12 5 2 5;stroke-linecap:butt}
.tick{fill:var(--card);stroke:var(--fg);stroke-width:1.2}
.hn{fill:var(--fg);font-size:13px;text-anchor:middle;paint-order:stroke;stroke:var(--card);
stroke-width:3px}
.hn.b{font-weight:700}
.apt{fill:var(--fg)}
.an{fill:var(--fg);font-size:14px;font-weight:700;paint-order:stroke;stroke:var(--card);
stroke-width:3px}
dl.src{margin:.5rem 0}
dl.src dt{font-weight:600;margin-top:.6rem}
dl.src dd{margin:.15rem 0 0}
dl.src ul{list-style:none;padding:0;margin:0}
dl.wx{margin:.25rem 0 .75rem}
dl.wx dt{font-size:.875rem;color:var(--dim);margin-top:.4rem}
dl.wx dd{margin:0}
details{margin:.5rem 0}
summary{cursor:pointer;color:var(--link)}
.scroll{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;font-size:.875rem}
th,td{text-align:left;padding:.25rem .75rem .25rem 0;vertical-align:top;
border-bottom:1px solid var(--line)}
form.ask{margin:1rem 0;padding:1rem;border:1px solid var(--line);border-radius:.5rem;
background:var(--card)}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:.75rem}
.fields label{display:block;font-size:.875rem;color:var(--dim);min-width:0}
.fields input{display:block;width:100%;min-width:0;margin-top:.2rem;padding:.5rem .6rem;
font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);
border-radius:.4rem}
fieldset{border:0;padding:0;margin:.9rem 0 0;min-width:0}
legend{padding:0;font-size:.875rem;font-weight:600;margin-bottom:.4rem}
button{margin-top:1rem;padding:.55rem 1.1rem;font:inherit;font-weight:600;color:var(--bg);
background:var(--fg);border:0;border-radius:.4rem;cursor:pointer}
ul.choices{list-style:none;padding:0}
ul.choices a{display:block;padding:.6rem .9rem;border:1px solid var(--line);border-radius:.5rem;
background:var(--card);text-decoration:none}
ul.choices a b{text-decoration:underline}
"""

# The page's Content-Security-Policy allows exactly this style block and nothing else.
CSS_HASH = "sha256-" + base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()


# --- small helpers -------------------------------------------------------------------------

def e(value: Any) -> str:
    return escape(str(value), quote=True)


def quoted(text: str, raw: bool = False) -> str:
    """An official source's own text. Browsers draw the quotation marks of a `<q>`."""
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


def layout(title: str, body: str, *, noindex: bool = False) -> str:
    robots = '<meta name="robots" content="noindex">\n' if noindex else ""
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'{robots}<meta name="referrer" content="no-referrer">\n'
        f'<title>{e(title)}</title>\n<link rel="icon" href="data:,">\n'
        f'<style>{CSS}</style>\n</head>\n<body>\n'
        f'<header class="site"><a href="/">{SITE}</a>'
        '<a class="more" href="/">Another flight</a></header>\n'
        f'<main>\n{body}\n</main>\n'
        f'<footer class="site"><p>{e(NOT_OPS)}. Weather for passengers, read from public '
        'sources; each page lists them.</p></footer>\n</body>\n</html>\n')


# --- the form and the pages that are not a brief -------------------------------------------

def form(values: dict[str, str] | None = None, *, airports: bool = False) -> str:
    v = values or {}

    def field(name: str, label: str, attrs: str) -> str:
        value = f' value="{e(v[name])}"' if v.get(name) else ""
        return f'<label>{label}<input name="{name}"{value} {attrs}></label>'

    need = airports or bool(v.get("from") or v.get("to"))
    out = ['<form class="ask" action="/go" method="get">', '<div class="fields">',
           field("code", "Flight number", 'type="text" required maxlength="10" '
                 'placeholder="SQ322" autocapitalize="characters" autocomplete="off" '
                 'spellcheck="false"'),
           field("date", "Departure date", 'type="date" required'),
           field("dep", "Departure time, local", 'type="time" required'),
           "</div>",
           "<fieldset><legend>Optional</legend>", '<div class="fields">',
           field("arr", "Arrival time, local at the destination", 'type="time"'),
           field("aircraft", "Aircraft type, such as A320, AT72 or turboprop",
                 'type="text" maxlength="12" autocomplete="off" spellcheck="false"'),
           "</div></fieldset>"]
    if need:
        code = ('type="text" maxlength="4" autocapitalize="characters" autocomplete="off" '
                'spellcheck="false"' + (" required" if airports else ""))
        out += ["<fieldset><legend>The two airports, as three-letter or four-letter codes"
                "</legend>", '<div class="fields">',
                field("from", "From", code + ' placeholder="SIN"'),
                field("to", "To", code + ' placeholder="LHR"'), "</div></fieldset>"]
    for name in ("fl", "gs", "airline"):
        if v.get(name):
            out.append(f'<input type="hidden" name="{name}" value="{e(v[name])}">')
    out.append("<button>Brief me</button>")
    if not need:
        out.append('<p class="dim">Flight number not matched to a route? '
                   '<a href="/?airports=1">Enter the two airports</a>.</p>')
    out.append("</form>")
    return "\n".join(out)


INTRO = ("<p>Type a flight number, the departure date and the departure time on your booking. "
         "The brief lists, for each hour of the flight, what each public weather source says, "
         "and what it cannot know yet.</p>")


def home_page(values: dict[str, str] | None = None, *, message: str | None = None,
              airports: bool = False) -> str:
    note = f'<p class="notice" role="alert">{own(message)}</p>' if message else ""
    body = (f"<h1>Weather brief for one flight</h1>\n{INTRO}\n{note}\n"
            f"{form(values, airports=airports)}\n"
            "<p class=\"dim\">Times are the gate times you type. Delays, cancellations and "
            "whether the flight runs that day are not tracked.</p>")
    return layout(SITE, body, noindex=message is not None)


def message_page(title: str, text: str) -> str:
    return layout(f"{title} {DOT} {SITE}", f"<h1>{e(title)}</h1>\n<p>{e(text)}</p>\n"
                  '<p><a href="/">Brief a flight</a></p>', noindex=True)


def problem_page(problem: Problem, values: dict[str, str]) -> str:
    """A request that did not give a brief, as a page: a choice of legs or airlines as links,
    or the form again with the message beside it."""
    code, day = values.get("code", ""), values.get("date", "")
    if problem.status == "choose_leg" and problem.legs:
        items = "\n".join(
            f'<li><a href="{e(flight_url(code, day, int(x["index"]), values))}">'
            f'<b>Leg {int(x["index"])}</b>: {e(x["origin"])} to {e(x["destination"])}</a></li>'
            for x in problem.legs)
        body = (f"<h1>{e(code)} has {len(problem.legs)} legs</h1>\n"
                f"<p>Choose the one you are on.</p>\n<ul class=\"choices\">\n{items}\n</ul>")
        return layout(f"{code} {DOT} {SITE}", body, noindex=True)
    if problem.status in ("choose_airline", "ambiguous") and problem.candidates:
        items = "\n".join(
            f'<li><a href="{e(go_url({**values, "airline": str(c.get("airline", ""))}))}">'
            f'<b>{e(c.get("name", ""))}</b> ({e(c.get("airline", ""))}), callsign '
            f'{e(c.get("callsign", ""))}: {e(" to ".join(map(str, c.get("airports", []))))}'
            "</a></li>" for c in problem.candidates)
        body = (f"<h1>{e(code)} is used by more than one airline</h1>\n"
                f"<p>Choose yours.</p>\n<ul class=\"choices\">\n{items}\n</ul>")
        return layout(f"{code} {DOT} {SITE}", body, noindex=True)
    unmapped = problem.status in ("could_not_map", "unmapped")
    return home_page(values, message=problem.message, airports=unmapped)


# --- the brief -----------------------------------------------------------------------------

def _strip(hours: list[Hour]) -> str:
    cells = "\n".join(
        f'<li class="s-{h.state}" title="Hour {h.index}, {e(hhmm(h.start))} to '
        f'{e(hhmm(h.end))}: {STATE_WORDS[h.state]}"><span class="n">{h.index}</span>'
        f'<span class="sym" aria-hidden="true">{e(SYMBOL[h.state])}</span>'
        f'<span class="vh">{STATE_WORDS[h.state]}</span></li>' for h in hours)
    legend = "\n".join(
        f'<li><span class="sym s-{state}" aria-hidden="true">{e(symbol)}</span>'
        f"{STATE_WORDS[state]}</li>" for state, symbol in SYMBOL.items())
    return (f'<ol class="strip" aria-label="State of each flight hour">\n{cells}\n</ol>\n'
            f'<ul class="legend" aria-label="Key">\n{legend}\n</ul>')


def _source_keys(hours: list[Hour]) -> list[tuple[str, str]]:
    """Every source that has a line in any hour: the known ones first, in their order, then
    any other key the engine sends, under a label made from the key."""
    labels = dict(SOURCES)
    seen = [key for key, _ in SOURCES]
    for h in hours:
        seen += [key for key in h.sources if key not in seen]
    return [(key, labels.get(key) or key.replace("_", " ").capitalize()) for key in seen
            if any(key in h.sources for h in hours)]


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
    return ("<h2>The sources' own words</h2>\n<ul>\n"
            + "\n".join(f"<li>{item}</li>" for item in items) + "\n</ul>")


def _near(brief: Brief) -> str:
    if not brief.near_airports:
        warn = brief.hours[0].sources.get("warnings") if brief.hours else None
        text = ("Official warnings are stale, so this is not known"
                if warn and warn.state == "stale"
                else "No warning in force within 150 nm of either airport, "
                     "surface to cruise level")
        return f"<h2>Near the airports</h2>\n<p>{text}</p>"
    items = []
    for n in brief.near_airports:
        where = "below cruise level" if n.below_cruise else "up to cruise level"
        items.append(f"<li>{e(n.airport)} {e(n.phase)}: {e(n.what)} {where}, "
                     f"{e(levels(n.base_ft, n.top_ft))}, valid to {e(hhmm(n.valid_to))}: "
                     f"{quoted(n.quoted)}</li>")
    return "<h2>Near the airports</h2>\n<ul>\n" + "\n".join(items) + "\n</ul>"


def _airports(brief: Brief) -> str:
    out = ["<h2>The two airports</h2>"]
    f = brief.flight
    for end, label in (("origin", "departure"), ("destination", "arrival")):
        a = brief.airports[end]
        place = f.origin if end == "origin" else f.destination
        out.append(f"<h3>{e(place.location)} ({e(a.code)}), {label} {e(hhmm(a.at))}</h3>")
        rows = []
        for name, line in (("TAF for then", a.taf), ("METAR now", a.metar)):
            raw = quoted(line.quoted, raw=True) if line.quoted else ""
            rows.append(f"<dt>{name}</dt>\n<dd>{own(line.text)}{raw}</dd>")
        out.append('<dl class="wx">\n' + "\n".join(rows) + "\n</dl>")
    return "\n".join(out)


def _provenance(brief: Brief) -> str:
    used = sorted({p.licence for p in brief.provenance})
    out = ["<h2>Sources and licences</h2>",
           "<ul>" + "".join(f"<li>{e(text)}</li>" for text in used) + "</ul>"]
    rows = []
    for p in brief.provenance:
        rows.append(
            f"<tr><td>{e(p.source)}</td>"
            f"<td>{e(day_hhmm(p.issued_at)) if p.issued_at else 'not known'}</td>"
            f"<td>{e(day_hhmm(p.fetched_at)) if p.fetched_at else 'not known'}</td>"
            f"<td>{e(p.url_without_secrets or '')}</td></tr>")
    if rows:
        out.append("<details><summary>Each file read for this brief</summary>\n"
                   '<div class="scroll"><table>\n<tr><th>Source</th><th>As of</th>'
                   "<th>Fetched</th><th>Address</th></tr>\n" + "\n".join(rows)
                   + "\n</table></div></details>")
    return "\n".join(out)


def brief_body(brief: Brief) -> str:
    """Everything on the page that comes from the brief itself: a live brief and the same
    brief read back from a saved record give the same text."""
    f = brief.flight
    out = [f"<h1>{e(f.code)} {DOT} {e(f.origin.location)} to {e(f.destination.location)}</h1>",
           f'<p class="sub">leg {brief.identity.leg} {DOT} {e(f.origin.code)} to '
           f"{e(f.destination.code)}"
           + (f" {DOT} callsign {e(f.callsign)}" if f.callsign else "") + "</p>"]
    departs = (f"Departs {e(_local(f.departure_utc, f.origin.tz))} ({e(hhmm(f.departure_utc))}), "
               f"as you entered {DOT} assumed FL{f.cruise_fl}, {f.groundspeed_kt:.0f} kt"
               + (" from your times" if f.arrival_source == "user input"
                  else f" for {e(f.aircraft)}" if f.aircraft else "")
               + f" {DOT} route: great-circle guess")
    if brief.status != "ok":
        notes = "".join(f"<li>{own(note)}</li>" for note in brief.notes)
        out += [f'<ul class="facts"><li>{departs}</li></ul>',
                f'<p class="message">{own(brief.message or "")}</p>',
                f'<ul class="facts dim">{notes}</ul>']
        return "\n".join(out)

    lead = f.departure_utc - brief.identity.issued_at
    arrives = ("Arrives {} ({}), as you entered" if f.arrival_source == "user input"
               else "Arrives about {} ({})")
    out.append(
        '<ul class="facts">'
        f"<li>{departs}</li>"
        f"<li>{e(arrives.format(_local(f.arrival_utc, f.destination.tz), hhmm(f.arrival_utc)))}"
        f" {DOT} {f.distance_nm:,.0f} nm {DOT} issued {e(age(lead))} before departure</li>"
        f"<li>In the air about {e(hhmm(f.takeoff_utc))} to {e(hhmm(f.landing_utc))} {DOT} "
        "hour 1 starts at take-off</li></ul>")
    out.append(f'<p class="summary">{own(brief.summary)}</p>')
    out.append(_strip(brief.hours))
    out.append(route_map(brief))
    out.append("<h2>What each source says</h2>")
    out.append(_sources(brief))
    out.append(_own_words(brief))
    out.append(_near(brief))
    out.append(_airports(brief))
    if brief.warnings_without_area:
        out.append(f"<p>{len(brief.warnings_without_area)} warning(s) worldwide came without an "
                   "area and could not be placed on the route (listed in the record)</p>")
    out.append("<h2>Assumptions</h2>\n<ul>"
               + "".join(f"<li>{own(a)}</li>" for a in f.assumptions) + "</ul>")
    notes = [*brief.notes, *[p.note for p in brief.provenance if p.stale and p.note]]
    out.append("<h2>Notes</h2>\n<ul>" + "".join(f"<li>{own(n)}</li>" for n in notes) + "</ul>")
    out.append(_provenance(brief))
    return "\n".join(part for part in out if part)


def render_page(brief: Brief, *, saved: bool = False, saved_url: str | None = None,
                current_url: str | None = None) -> str:
    """The brief page. `saved` marks a page drawn from a saved record: it says so and says
    when the brief was issued. `saved_url` is where this brief's saved copy lives."""
    issued = day_hhmm(brief.identity.issued_at)
    top = ""
    if saved:
        link = (f' <a href="{e(current_url)}">Open the current brief for this flight</a>.'
                if current_url else "")
        top = (f'<p class="banner" role="note"><strong>Saved brief, issued {e(issued)}.</strong> '
               f"It shows what the sources said then and does not change.{link}</p>\n")
    tail = f'<p class="dim">Issued {e(issued)}.'
    if saved_url and not saved:
        tail += f' <a href="{e(saved_url)}">Saved copy of this brief</a>, which does not change.'
    tail += "</p>"
    f = brief.flight
    title = f"{f.code} {f.origin.code} to {f.destination.code}, {brief.identity.date} {DOT} {SITE}"
    return layout(title, f"{top}{brief_body(brief)}\n{tail}", noindex=True)


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


def _step(span: float) -> float:
    return next((s for s in (1, 2, 5, 10, 15, 30, 45) if span / s <= 5), 90)


def _lat_label(lat: float) -> str:
    return "0°" if lat == 0 else f"{abs(lat):g}°{'N' if lat > 0 else 'S'}"


def _lon_label(lon: float) -> str:
    lon = (lon + 180) % 360 - 180
    return "0°" if lon == 0 else "180°" if lon == -180 else f"{abs(lon):g}°{'E' if lon > 0 else 'W'}"


def route_map(brief: Brief, width: int = 480) -> str:
    """The route as inline SVG: each hour's stretch drawn in its state's colour and line
    style, hour numbers along it, the two airports, and a grid of latitude and longitude.
    A plain equal-spacing projection, stretched east to west for the route's latitude."""
    pts = brief.route.points
    if len(pts) < 2:
        return ""
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    k = max(0.25, math.cos(math.radians((min(lats) + max(lats)) / 2)))
    x0, x1 = min(lons) * k, max(lons) * k
    y0, y1 = -max(lats), -min(lats)
    pad = max(x1 - x0, y1 - y0) * 0.1 + 0.5
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    # Keep the picture between a wide strip and a near square, growing the short side.
    if (y1 - y0) < 0.42 * (x1 - x0):
        grow = (0.42 * (x1 - x0) - (y1 - y0)) / 2
        y0, y1 = y0 - grow, y1 + grow
    elif (y1 - y0) > 0.9 * (x1 - x0):
        grow = ((y1 - y0) / 0.9 - (x1 - x0)) / 2
        x0, x1 = x0 - grow, x1 + grow
    scale = width / (x1 - x0)
    height = round((y1 - y0) * scale)

    def xy(lat: float, lon: float) -> tuple[float, float]:
        return (lon * k - x0) * scale, (-lat - y0) * scale

    def path(points: list[tuple[float, float]]) -> str:
        return " ".join("{:.1f},{:.1f}".format(*xy(*p)) for p in points)

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="map-title">',
           f'<title id="map-title">Route from {e(brief.flight.origin.code)} to '
           f"{e(brief.flight.destination.code)}, drawn as a great-circle guess</title>"]

    # Grid of latitude and longitude, labelled at the left and bottom edges.
    lat_lo, lat_hi, lon_lo, lon_hi = -y1, -y0, x0 / k, x1 / k
    step = _step(max(lat_hi - lat_lo, lon_hi - lon_lo))
    lat = math.ceil(lat_lo / step) * step
    while lat < lat_hi:
        if -90 <= lat <= 90:
            y = xy(lat, 0)[1]
            out.append(f'<line class="grat" x1="0" y1="{y:.1f}" x2="{width}" y2="{y:.1f}"/>')
            if 14 < y < height - 16:
                out.append(f'<text class="gl" x="4" y="{y - 3:.1f}">{_lat_label(lat)}</text>')
        lat += step
    lon = math.ceil(lon_lo / step) * step
    while lon < lon_hi:
        x = xy(0, lon)[0]
        out.append(f'<line class="grat" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}"/>')
        if 30 < x < width - 34:
            out.append(f'<text class="gl" x="{x + 3:.1f}" y="{height - 4}">'
                       f"{_lon_label(lon)}</text>")
        lon += step

    # Distance flown at each route point. One groundspeed for the whole flight, so a share
    # of the time in the air is the same share of the distance.
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

    def stretch(a: float, b: float) -> list[tuple[float, float]]:
        inner = [p for p, d in zip(pts, flown, strict=True) if a * total < d < b * total]
        return [at(a), *inner, at(b)]

    f = brief.flight
    airborne = (f.landing_utc - f.takeoff_utc).total_seconds() or 1.0
    labels: list[str] = []
    if not brief.hours:
        out.append(f'<polyline class="seg" points="{path(pts)}"/>')
    for h in brief.hours:
        a = (h.start - f.takeoff_utc).total_seconds() / airborne
        b = (h.end - f.takeoff_utc).total_seconds() / airborne
        out.append(f'<polyline class="seg m-{h.state}" points="{path(stretch(a, b))}">'
                   f"<title>Hour {h.index}: {STATE_WORDS[h.state]}</title></polyline>")
        if h.index > 1:
            tx, ty = xy(*at(a))
            labels.append(f'<circle class="tick" cx="{tx:.1f}" cy="{ty:.1f}" r="2.5"/>')
        # The hour's number beside the middle of its stretch, on the upper side of the line.
        (ax, ay), (bx, by) = xy(*at(a)), xy(*at(b))
        mx, my = xy(*at((a + b) / 2))
        dx, dy = bx - ax, by - ay
        size = math.hypot(dx, dy) or 1.0
        nx, ny = dy / size, -dx / size
        if ny > 0:
            nx, ny = -nx, -ny
        lx = min(width - 9.0, max(9.0, mx + nx * 14))
        ly = min(height - 6.0, max(14.0, my + ny * 14 + 4.5))
        labels.append(f'<text class="hn{" b" if h.state == "flagged" else ""}" '
                      f'x="{lx:.1f}" y="{ly:.1f}">{h.index}</text>')
    out += labels

    for place, point in ((f.origin, pts[0]), (f.destination, pts[-1])):
        x, y = xy(*point)
        anchor, dx = ("start", 8) if x < width / 2 else ("end", -8)
        ty = y + 22 if y < height - 30 else y - 12
        out.append(f'<circle class="apt" cx="{x:.1f}" cy="{y:.1f}" r="4.5"/>')
        out.append(f'<text class="an" x="{x + dx:.1f}" y="{ty:.1f}" text-anchor="{anchor}">'
                   f"{e(place.iata or place.code)}</text>")
    out.append("</svg>")
    caption = (f"<figcaption>{GUESS}. Each hour's stretch has the colour of its state in the "
               "strip above, and its own line style: flagged is the thick line, too early is "
               "dashed, not covered is dotted. No coastlines are drawn; the grid is latitude "
               "and longitude.</figcaption>")
    return '<figure class="map">\n' + "\n".join(out) + f"\n{caption}\n</figure>"
