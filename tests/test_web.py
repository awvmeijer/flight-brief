"""The page, called as a WSGI app with no socket: the form, the redirect, the brief, the
pages for requests that give no brief, the limits, the headers and the saved briefs."""

import json
import re
import sys
import types
from collections.abc import Callable
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from wsgiref.util import setup_testing_defaults

import pytest

import flight_brief
from brief_helpers import AA273, SEVERITY, SQ231, STORE, UA837, UA1175, brief, utc
from flight_brief import page, vrs, web
from flight_brief.brief import DEPARTED, NOT_OPS, Brief, SourceLine, day_hhmm, hhmm
from flight_brief.render import SOURCES, _grouped, render
from flight_brief.resolve import COULD_NOT_MAP
from flight_brief.service import BriefRequest, Problem, request_brief

App = Callable[..., Any]
VOID = {"meta", "link", "input", "br", "hr", "img"}
# Never these words in the page's own text; "score" stands for a single overall score.
VERDICT = re.compile(r"\b(safe|unsafe|safely|safety|score[sd]?|scoring|rating)\b", re.IGNORECASE)
SQ231_URL = "/f/SQ231/2026-11-04/1?dep=08:00"


def make(table: vrs.RouteTable, as_of: str = "2026-11-03T23:00", **kw: Any) -> App:
    kw.setdefault("snapshots", STORE)
    kw.setdefault("cache", Path("unused-cache"))
    kw.setdefault("saved", None)
    return web.create_app(table, now=lambda: utc(as_of), **kw)


def get(app: App, url: str, method: str = "GET", address: str = "192.0.2.1",
        **extra: str) -> tuple[int, dict[str, str], str]:
    path, _, query = url.partition("?")
    environ: dict[str, Any] = {"REQUEST_METHOD": method, "PATH_INFO": path,
                               "QUERY_STRING": query, "REMOTE_ADDR": address, **extra}
    setup_testing_defaults(environ)
    seen: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        seen["status"], seen["headers"] = int(status.split()[0]), dict(headers)

    body = b"".join(app(environ, start)).decode("utf-8")
    return seen["status"], seen["headers"], body


class Reader(HTMLParser):
    """Checks that every tag closes in order, and collects the text a reader sees, with a
    source's own words (`<q>`) put back inside double quotation marks. It also keeps every
    form field with the form it is in, and every script with what is written inside it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.parts: list[str] = []
        self.links: list[str] = []
        self.attrs: list[tuple[str, str, str]] = []
        self.inputs: list[dict[str, Any]] = []
        self.scripts: list[dict[str, Any]] = []
        self.form = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))
            if tag == "a" and name == "href":
                self.links.append(value or "")
        given = {name: value or "" for name, value in attrs}
        if tag == "form":
            self.form = given.get("action", "")
        if tag == "input":
            self.inputs.append({**given, "form": self.form, "labelled": "label" in self.open,
                                "folded": "details" in self.open})
        if tag == "script":
            self.scripts.append({"attrs": given, "text": ""})
        if tag == "q":
            self.parts.append('"')
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))

    def handle_endtag(self, tag: str) -> None:
        assert self.open and self.open[-1] == tag, f"</{tag}> closes {self.open[-3:]}"
        self.open.pop()
        if tag == "form":
            self.form = ""
        if tag == "q":
            self.parts.append('"')

    def handle_data(self, data: str) -> None:
        if "script" in self.open:
            self.scripts[-1]["text"] += data
        elif "style" not in self.open:
            self.parts.append(data)


def read(html: str) -> Reader:
    """Parse a page, failing unless it is well formed."""
    assert html.startswith("<!doctype html>")
    reader = Reader()
    reader.feed(html)
    reader.close()
    assert reader.open == [], f"left open: {reader.open}"
    return reader


# The loading screen, at the end of every page and hidden until `static/app.js` shows it.
VEIL = re.compile(r'<div id="fb-loading".*?</ul></div></div>', re.DOTALL)


def text_of(html: str) -> str:
    """The text a reader sees on the page as it arrives: everything but the hidden loading
    screen, which names every source on every page. Each page must carry that screen once."""
    assert len(VEIL.findall(html)) == 1
    return " ".join("".join(read(VEIL.sub("", html)).parts).split())


def own_text(html: str) -> str:
    """The whole page source, loading screen and drawings included, with every quoted span
    taken out: what is left, tags and attributes included, is this project's own wording."""
    return re.sub(r'<q class="quoted[^"]*">.*?</q>', "", html, flags=re.DOTALL)


def outside_the_answer(html: str) -> str:
    """The page without what `ride.py` wrote: the windows of kind "ride" (they hold no other
    window) and any element marked `data-ride`."""
    html = re.sub(r'<section class="st-win ride">.*?</section>', "", html, flags=re.DOTALL)
    return re.sub(r"<(\w+) data-ride>.*?</\1>", "", html, flags=re.DOTALL)


def sealed(html: str) -> Reader:
    """The page runs and styles nothing written into it. Every script is a file of this
    app's or Mapbox's, or is a block of JSON data; there is no style element, no style
    attribute and no event attribute; nothing but a script has a `src`."""
    reader = read(html)
    assert html.lower().count("<script") == len(reader.scripts)
    for script in reader.scripts:
        given = script["attrs"]
        if "src" in given:
            assert given["src"].startswith(("/static/", page.MAPBOX_GL + "/")), given
            assert "defer" in given and not script["text"].strip(), given
        else:
            assert given.get("type") == "application/json", given
    assert "<style" not in html.lower()
    assert not [a for a in reader.attrs if a[1] == "style" or a[1].startswith("on")]
    assert not [a for a in reader.attrs if a[1] == "src" and a[0] != "script"]
    return reader


def policy(headers: dict[str, str]) -> dict[str, list[str]]:
    """The Content-Security-Policy, checked: nothing by default, nothing inline, nothing
    evaluated, and no host but this one and Mapbox's."""
    csp = headers["Content-Security-Policy"]
    assert "unsafe" not in csp
    parts = {d.split()[0]: d.split()[1:] for d in csp.split(";") if d.strip()}
    assert parts["default-src"] == ["'none'"] and parts["base-uri"] == ["'none'"]
    assert parts["form-action"] == ["'self'"] and parts["frame-ancestors"] == ["'none'"]
    assert parts["script-src"] == ["'self'", "https://api.mapbox.com"] == parts["style-src"]
    assert page.MAPBOX_GL.startswith("https://api.mapbox.com/")
    for name, sources in parts.items():
        for source in sources:
            assert source in ("'none'", "'self'", "data:", "blob:") or re.fullmatch(
                r"https://(\*\.)?([a-z]+\.)*mapbox\.com", source), (name, source)
    assert "data:" not in parts["script-src"] and "blob:" not in parts["script-src"]
    return parts


def fields(html: str, action: str) -> dict[str, str]:
    """What one of the page's forms arrives filled in with: field name to value."""
    return {i["name"]: i["value"] for i in read(html).inputs
            if i["form"] == action and i.get("value")}


def choices(html: str) -> list[tuple[str, str]]:
    """A list of choices, each as (address, the link's text)."""
    block = re.search(r'<ul class="fb-choices">\n(.*?)\n</ul>', html, re.DOTALL)
    assert block is not None
    found = re.findall(r'<li><a href="([^"]*)"(?: data-brief)?>(.*?)</a></li>', block[1])
    assert len(found) == block[1].count("<li>")
    return [(unescape(url), unescape(re.sub(r"<[^>]+>", "", inner))) for url, inner in found]


FLIGHT_ROW = re.compile(
    r'<li><a class="fb-flight" href="[^"]*" data-brief="([^"]*)">'
    r'<span class="t">([^<]*)<i></i>([^<]*)(<sup>\+1</sup>)?</span>'
    r'<span class="d cap">([^<]*)</span>'
    r'<span class="w"><b>([^<]*)</b>([^<]*)</span>'
    r'<span class="a">([^<]*)</span><span class="go">Brief [^<]*</span></a></li>')


def flights(html: str) -> list[tuple[str, ...]]:
    """The rows of a list of scheduled flights, in order: departs, arrives ("--:--" when not
    known, "+1" after it for the next day), number, airline, aircraft, and the two airports
    when the row shows them."""
    block = re.search(r'<ol class="fb-flights">\n(.*?)\n</ol>', html, re.DOTALL)
    assert block is not None
    rows = FLIGHT_ROW.findall(block[1])
    assert len(rows) == block[1].count("<li>")
    for label, _, _, _, _, number, _, _ in rows:  # what the loading screen will say
        assert unescape(label).startswith(f"{number} \u00b7 ")
    return [tuple(unescape(x) for x in (dep, arr + ("+1" if nxt else ""), number, airline,
                                        aircraft, ends))
            for _, dep, arr, nxt, ends, number, airline, aircraft in rows]


def unknowns(html: str) -> list[str]:
    """The lines of "What this brief cannot know yet", as text; " / " where a line breaks."""
    block = re.search(r'<ul class="fb-unknown">\n(.*?)\n</ul>', html, re.DOTALL)
    assert block is not None
    return [unescape(re.sub(r"<[^>]+>", "", line.replace("<br>", " / ")))
            for line in block[1].split("\n")]


# --- the form and the redirect -------------------------------------------------------------

def test_the_form_asks_for_the_flight_and_shows_the_airports_only_when_needed(
        table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/")
    assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8"
    inputs = read(body).inputs
    # the route and the day first, the flight number second
    assert [(i["form"], i["name"]) for i in inputs] == [
        ("/r", "from"), ("/r", "to"), ("/r", "date"),
        ("/go", "code"), ("/go", "date"), ("/go", "dep"), ("/go", "arr"), ("/go", "aircraft")]
    assert body.index('<form class="fb-search" action="/r" method="get">') < body.index(
        '<form class="fb-num" action="/go" method="get" data-brief>')
    # every field sits in its label; the optional ones are folded away; nothing is filled in
    assert all(i["labelled"] for i in inputs)
    assert body.count('<label class="st-field"><span class="lab">') == len(inputs)
    assert {i["name"] for i in inputs if i["folded"]} == {"arr", "aircraft"}
    assert {i["name"] for i in inputs if "required" in i} == {"from", "to", "date", "code", "dep"}
    assert not [i for i in inputs if "value" in i]
    assert NOT_OPS in body and "X-Robots-Tag" not in headers

    status, _, body = get(app, "/?airports=1")
    asked = [i for i in read(body).inputs if i["form"] == "/go"]
    assert status == 200 and [i["name"] for i in asked] == [
        "code", "date", "dep", "from", "to", "arr", "aircraft"]
    assert all("required" in i for i in asked if i["name"] in ("from", "to"))


def test_the_form_redirects_to_one_address_per_flight_and_leg(table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/go?code=sq+0231&date=2026-11-04&dep=08%3A00&arr=&aircraft=")
    assert (status, headers["Location"], body) == (303, SQ231_URL, "")
    # every typed option is carried; a UTC offset keeps its plus sign
    _, headers, _ = get(app, "/go?code=vt956&date=2026-11-17&dep=06:30%2B10:00&arr=11:15"
                             "&aircraft=AT72&from=ppt&to=gmr")
    assert headers["Location"] == ("/f/VT956/2026-11-17/1?dep=06:30%2B10:00&arr=11:15"
                                   "&aircraft=AT72&from=PPT&to=GMR")
    # a lower-case or zero-padded address goes to the one address
    status, headers, _ = get(app, "/f/sq0231/2026-11-04/1?dep=08:00")
    assert (status, headers["Location"]) == (302, SQ231_URL)
    assert get(app, "/go?code=SQ231&date=2026-11-04")[0] == 400


# --- a brief --------------------------------------------------------------------------------

def test_an_ok_brief_page_says_what_the_text_brief_says(table: vrs.RouteTable) -> None:
    status, headers, body = get(make(table), SQ231_URL)
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    b = request_brief(BriefRequest(code="SQ231", day="2026-11-04", dep="08:00", leg=1), table,
                      STORE, utc("2026-11-03T23:00"))
    assert isinstance(b, Brief) and b.status == "ok"
    seen = text_of(body)
    lines = render(b).splitlines()
    # the header lines of the text brief, piece by piece. Three are said the display's way:
    # the level and the speed on the head-up drawing, whose label says them in words
    f = b.flight
    drawn = (f"groundspeed {f.groundspeed_kt:.0f} knots, flight level {f.cruise_fl}")
    worded = {f"assumed FL{f.cruise_fl}, {f.groundspeed_kt:.0f} kt": drawn,
              "route: great-circle guess": page.GUESS,
              "issued 60 min before departure": "Issued Tue 03 Nov 23:00 UTC · 60 min before "
                                                "departure"}
    pieces = [piece for line in lines[:4] for piece in line.split(" . ")]
    assert set(worded) <= set(pieces)
    for piece in pieces:
        assert worded.get(piece, piece) in seen or piece in worded and re.search(
            rf'<svg class="hud"[^>]*aria-label="[^"]*{worded[piece]}"', body), piece
    for value in (f"{f.groundspeed_kt:.0f}", str(f.cruise_fl)):  # and as the boxed read-outs
        assert re.search(rf'<text class="big"[^>]*>{value}</text>', body), value
    assert "SQ231 · Singapore to Sydney" in seen and "callsign SIA231" in seen
    assert "1 flagged stretch (hour 1)" in seen and b.summary in seen
    for key, name in SOURCES:
        grouped = _grouped(b.hours, key)
        assert all(line in seen for line in grouped)
        assert (name in seen) == bool(grouped)
    assert "hour 1: the sources that cover it agree" in seen or "hour 1: one source only" in seen
    quoted = [w.quoted for h in b.hours for w in h.warnings]
    assert quoted and all(f'"{q}"' in seen for q in quoted)
    for a in b.airports.values():
        assert a.taf.text in seen and a.metar.text in seen
        assert f'"{a.taf.quoted}"' in seen and f'"{a.metar.quoted}"' in seen
    assert (f'<b>Arrival</b><span class="meta">{hhmm(b.flight.arrival_utc)}</span>' in body
            and "<h2>Sydney <code>YSSY</code></h2>" in body)
    assert (f'<b>Departure</b><span class="meta">{hhmm(b.flight.departure_utc)}</span>' in body
            and "<h2>Singapore <code>WSSS</code></h2>" in body)
    for own in [*b.flight.assumptions, *b.notes, *{p.licence for p in b.provenance}]:
        assert own in seen, own
    assert "great-circle guess" in seen and NOT_OPS in seen


def test_the_hour_strip_shows_each_state_as_a_symbol_and_a_colour(table: vrs.RouteTable) -> None:
    _, _, body = get(make(table), SQ231_URL)
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T23:00")
    strip = re.search(r'<ol class="fb-cells strip" aria-label="[^"]+">(.*?)</ol>', body)
    assert strip is not None
    cells = re.findall(r'<li class="fb-cell big s-(\w+)" title="([^"]*)"><span class="n">(\d+)'
                       r'</span><span aria-hidden="true">(.)</span>'
                       r'<span class="vh">([^<]+)</span></li>', strip[1])
    assert len(cells) == strip[1].count("<li")
    assert [(state, int(n)) for state, _, n, _, _ in cells] == [(h.state, h.index)
                                                                 for h in b.hours]
    assert cells[0] == ("flagged", "Hour 1: flagged", "01", "\u25c6", "hour 1 flagged")
    for state, title, n, symbol, words in cells:  # never the colour alone: a mark and words
        assert symbol == page.SYMBOL[state]
        assert words == f"hour {int(n)} {page.STATE_WORDS[state]}"
        assert title == f"Hour {int(n)}: {page.STATE_WORDS[state]}"
    assert len(set(page.SYMBOL.values())) == len(page.SYMBOL) == 5
    assert page.SYMBOL == {"flagged": "\u25c6", "nothing_found": "\u25cb", "too_early": "?",
                           "not_covered": "\u2013", "stale": "!"}
    css = (web.STATIC / "fb.css").read_text(encoding="utf-8")
    for state in page.LANE_SYMBOL:
        assert re.search(rf"\.s-{state}\b[^{{}}]*\{{", css), state  # the colour, or the line
    # the key names every state an hour can have, with its mark
    key = re.findall(r'<li class="cap"><span class="fb-cell s-(\w+)" aria-hidden="true">(.)'
                     r"</span>([^<]+)</li>", body)
    assert key == [(state, symbol, page.STATE_WORDS[state])
                   for state, symbol in page.SYMBOL.items()]
    assert {h.state for h in b.hours} <= set(page.SYMBOL)
    seen = text_of(body)
    assert f"{hhmm(b.flight.takeoff_utc)} take-off" in seen
    assert f"{hhmm(b.flight.landing_utc)} landing" in seen


def test_each_source_has_a_lane_with_a_cell_for_every_hour(table: vrs.RouteTable) -> None:
    b = brief(table, *SQ231)
    for h in b.hours:
        h.sources["shear"] = SourceLine(state="nothing_found", text="Nothing found: index 3")
        h.shear = {"value": 3.0, "threshold": 8.0}
    b.hours[1].sources["gtg"] = SourceLine(state="flagged", text='GTG says "MOD"')
    b.hours[1].gtg = {"edr": 0.25}
    html = page.render_page(b)
    lanes = re.findall(r'<div class="fb-lane"><div class="name"><b>([^<]*)</b>'
                       r'<span class="cap">([^<]*)</span></div>'
                       r'<ol class="fb-cells">(.*?)</ol></div>', html)
    keys = [(key, name) for key, name in SOURCES if any(key in h.sources for h in b.hours)]
    assert [name for name, _, _ in lanes] == [name for _, name in keys]
    assert {"Official warnings", "Pilot reports", "Wind-shear index", "GTG forecast"} == {
        name for name, _, _ in lanes}
    states = set()
    for (key, _), (name, speaks, cells) in zip(keys, lanes, strict=True):
        got = re.findall(r'<li class="fb-cell s-none"></li>|<li class="fb-cell s-(\w+)" '
                         r'title="[^"]*"><span aria-hidden="true">(.)</span>'
                         r'<span class="vh">([^<]*)</span>(?:<span class="v">([^<]*)</span>)?</li>',
                         cells)
        assert len(got) == len(b.hours) == cells.count("<li"), name
        for h, (state, symbol, words, value) in zip(b.hours, got, strict=True):
            line = h.sources.get(key)
            assert state == (line.state if line else ""), (name, h.index)  # no line, no mark
            if line:
                states.add(state)
                assert symbol == page.LANE_SYMBOL[state]
                assert words == f"hour {h.index} {page.STATE_WORDS[state]}"
            assert value == {"shear": "3", "gtg": ".25" if line else ""}.get(key, "")
        said = sum(1 for h in b.hours if key in h.sources
                   and h.sources[key].state in ("flagged", "nothing_found"))
        assert speaks == f"speaks for {said} of {len(b.hours)} hours"
        if key in ("warnings", "pireps"):  # the lane counts what the summary counts
            assert f"{name} speak for {said} of {len(b.hours)} hours" in b.summary
    # a source that is silent is not drawn as one that looked and found nothing
    assert {"none_issued", "not_covered", "flagged", "nothing_found"} <= states
    assert page.LANE_SYMBOL["none_issued"] == page.LANE_SYMBOL["no_reports"] == "\u00b7"
    assert "\u00b7" not in page.SYMBOL.values()
    assert "threshold" not in html  # the dicts are shown only as the cell's one figure


def test_the_map_draws_each_hour_of_the_route_and_both_airports(table: vrs.RouteTable) -> None:
    b = brief(table, "UA837", "2026-11-02", "17:40", "2026-11-03T01:00", root=STORE)
    data = page.map_data(b)
    hours = data["hours"]
    # one stretch for each hour, numbered; the line follows the plain answer: amber where
    # it says moderate or more, dashed where it is not known
    def drawn(h):
        return ("too_early" if h.ride_level is None else "flagged" if h.ride_level >= 2
                else "nothing_found")
    assert [(h["label"], h["state"]) for h in hours] == [(f"{h.index:02d}", drawn(h))
                                                         for h in b.hours]
    assert len({h["state"] for h in hours}) > 1 and set(data) == {"hours", "airports"}
    # points are [longitude, latitude], the order Mapbox reads
    first, last = b.route.points[0], b.route.points[-1]
    assert data["airports"] == [{"code": "SFO", "at": [round(first[1], 4), round(first[0], 4)]},
                                {"code": "NRT", "at": [round(last[1], 4), round(last[0], 4)]}]
    assert data["airports"][0]["at"] == [-122.375, 37.619]
    line = [point for h in hours for point in h["line"]]
    assert all(len(point) == 2 and -90 <= point[1] <= 90 for point in line)
    # one unbroken line from one airport to the other: each stretch starts where the last
    # ended, and across the antimeridian the longitude runs on past 180 instead of jumping
    assert line[0] == data["airports"][0]["at"] and line[-1] == data["airports"][1]["at"]
    assert all(a["line"][-1] == b_["line"][0] for a, b_ in zip(hours, hours[1:], strict=False))
    assert all(len(h["line"]) >= 2 for h in hours)
    assert min(lon for lon, _ in line) < -180
    assert all(abs(a[0] - b_[0]) < 3 and abs(a[1] - b_[1]) < 3
               for a, b_ in zip(line, line[1:], strict=False))
    for h in hours:  # the hour's number sits on its own stretch
        lons = [lon for lon, _ in h["line"]]
        assert min(lons) <= h["mid"][0] <= max(lons)

    # without a token the window says the map is not set up, and nothing of Mapbox's is named
    url = "/f/UA837/2026-11-02/1?dep=17:40"
    _, _, body = get(make(table, "2026-11-03T01:00"), url)
    seen = text_of(body)
    assert "The map is not set up on this copy of the page: it needs a Mapbox token" in seen
    assert '<b>Route</b><span class="meta">map not set up</span>' in body
    assert 'id="fb-map"' not in body and "fb-map-data" not in body and "mapbox.com" not in body
    assert "/static/map.js" not in body
    assert 'role="img" aria-label="Route from KSFO to RJAA"' in body
    assert "great-circle guess, not the filed route" in seen

    # with one the page carries the same data for `static/map.js`, as JSON and nothing else
    _, headers, body = get(make(table, "2026-11-03T01:00", mapbox="pk.test"), url)
    reader = sealed(body)
    block, = [s for s in reader.scripts if "src" not in s["attrs"]]
    assert block["attrs"] == {"type": "application/json", "id": "fb-map-data"}
    assert json.loads(block["text"]) == {**data, "token": "pk.test"}
    assert body.count('id="fb-map"') == 1 and "map not set up" not in body
    assert [s["attrs"]["src"] for s in reader.scripts if "src" in s["attrs"]] == [
        f"/static/app.js?v={page.ASSETS}", f"{page.MAPBOX_GL}/mapbox-gl.js",
        f"/static/map.js?v={page.ASSETS}"]
    assert f'<link rel="stylesheet" href="{page.MAPBOX_GL}/mapbox-gl.css">' in body
    # the token is in the data block and nowhere else: no attribute, no address, no header
    assert body.count("pk.test") == block["text"].count("pk.test") == 1
    assert "pk.test" not in "".join(headers.values())
    seen = text_of(body)
    assert "The map needs JavaScript and WebGL" in seen and "pk.test" not in seen
    assert "great-circle guess, not the filed route" in seen
    # a saved brief drawn again has the map too
    assert 'id="fb-map-data"' in page.render_page(b, saved=True, mapbox="pk.test")


def test_nothing_in_the_map_data_can_end_its_block(table: vrs.RouteTable) -> None:
    b = brief(table, *UA837)
    hostile = '</script><script>alert(1)</script><!--<img src=x onerror=alert(1)>'
    b.flight.origin.iata = b.flight.destination.code = hostile
    b.flight.destination.iata = None
    html = page.render_page(b, mapbox="pk.<b>")
    reader = sealed(html)
    block, = [s for s in reader.scripts if "src" not in s["attrs"]]
    raw = html[html.index('id="fb-map-data">') + len('id="fb-map-data">'):]
    raw = raw[:raw.index("</script>")]
    assert raw == block["text"] and "<" not in raw and "\\u003c" in raw
    data = json.loads(raw)
    assert [a["code"] for a in data["airports"]] == [hostile, hostile]
    assert data["token"] == "pk.<b>"
    assert "alert(1)</script>" not in html and "<img" not in html and "pk.<b>" not in html


def test_a_source_the_page_has_not_heard_of_gets_a_line_of_its_own(table: vrs.RouteTable) -> None:
    from flight_brief.brief import SourceLine

    b = brief(table, *SQ231)
    for h in b.hours:
        h.sources["shear"] = SourceLine(state="nothing_found", text="Nothing found: index 3")
        h.shear = {"value": 3.0, "threshold": 8.0}
    b.hours[1].sources["gtg"] = SourceLine(state="flagged", text='GTG says "MOD"')
    seen = text_of(page.render_page(b))
    assert f"Wind-shear index hours 1 to {len(b.hours)}: Nothing found: index 3" in seen
    assert 'GTG forecast hour 2: GTG says "MOD"' in seen
    assert "threshold" not in seen  # the dicts are shown only through the source line


def test_departed_and_too_far_out_show_the_header_and_the_message(table: vrs.RouteTable) -> None:
    status, headers, body = get(make(table, "2026-11-04T03:00"), SQ231_URL)
    seen = text_of(body)
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert "SQ231 · Singapore to Sydney" in seen and DEPARTED in seen and NOT_OPS in seen
    assert f'<p class="fb-sum message">{DEPARTED}</p>' in body
    # no hour strip, no lanes, no map and no list of unknowns: there is no brief to draw
    assert "fb-cells" not in body and "fb-lane" not in body and "fb-map" not in body
    assert "What each source says" not in seen and "fb-unknown" not in body
    assert "<b>Route</b>" not in body and '<p class="fb-sum summary">' not in body
    # the head-up drawing still shows this flight's own figures
    assert 'aria-label="Initial track 133 degrees, groundspeed 480 knots, flight level 380"' \
        in body
    # and a token changes nothing on a page with no route to draw
    _, _, mapped = get(make(table, "2026-11-04T03:00", mapbox="pk.test"), SQ231_URL)
    assert mapped == body and "pk.test" not in mapped and "mapbox" not in mapped

    _, _, body = get(make(table, "2026-10-28T00:00"), SQ231_URL)
    seen = text_of(body)
    assert "Too far out. Come back within 30 hours of departure, after Mon 02 Nov 18:00 UTC" in seen
    assert "Departs Wed 04 Nov 08:00 UTC+8 (00:00 UTC), as you entered" in seen


# --- requests that give no brief -----------------------------------------------------------

def test_a_number_with_no_route_shows_the_form_with_the_airports(table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/f/U28001/2026-11-04/1?dep=08:00")
    assert status == 200 and COULD_NOT_MAP in text_of(body)
    assert f'<p class="fb-notice" role="alert">{page.e(COULD_NOT_MAP)}</p>' in body
    assert fields(body, "/go") == {"code": "U28001", "date": "2026-11-04", "dep": "08:00"}
    assert fields(body, "/r") == {}
    asked = {i["name"]: i for i in read(body).inputs if i["form"] == "/go"}
    assert "required" in asked["from"] and "required" in asked["to"]
    # the form that needs an answer comes first on this page
    assert body.index('action="/go"') < body.index('action="/r"')
    _, headers, _ = get(app, "/go?code=U28001&date=2026-11-04&dep=08:00&from=SIN&to=YSSY")
    status, _, body = get(app, headers["Location"])
    assert status == 200 and "WSSS to YSSY" in text_of(body)


def test_a_shared_code_lists_the_airlines_as_links(table: vrs.RouteTable) -> None:
    app = make(table)
    status, _, body = get(app, "/f/BA10A/2026-11-04/1?dep=08:00")
    links = [h for h in read(body).links if "airline=" in h]
    assert status == 200 and "used by more than one airline" in text_of(body)
    assert len(links) == 2 and all(h.startswith("/go?code=BA10A&date=2026-11-04&dep=08:00")
                                   for h in links)
    assert [url for url, _ in choices(body)] == links
    assert all("callsign" in words for _, words in choices(body))
    chosen = next(h for h in links if h.endswith("airline=SHT"))
    status, headers, _ = get(app, chosen)
    assert status == 303 and headers["Location"].endswith("airline=SHT")
    assert "callsign SHT10A" in text_of(get(app, headers["Location"])[2])


def test_a_number_with_several_legs_lists_them_as_links(table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/go?code=qf1&date=2026-11-04&dep=08:00")
    assert status == 200 and "Location" not in headers and "QF1 has 2 legs" in text_of(body)
    links = [h for h in read(body).links if h.startswith("/f/")]
    assert links == ["/f/QF1/2026-11-04/1?dep=08:00", "/f/QF1/2026-11-04/2?dep=08:00"]
    assert choices(body) == [(links[0], "Leg 1 YSSY to WSSS"), (links[1], "Leg 2 WSSS to EGLL")]
    assert body.count(" data-brief>Leg ") == 0 and body.count(" data-brief><b>Leg ") == 2
    assert "leg 2 · WSSS to EGLL" in text_of(get(app, links[1])[2])
    # the same page from the service's own answer
    problem = request_brief(BriefRequest(code="QF1", day="2026-11-04", dep="08:00"), table,
                            STORE, utc("2026-11-03T23:00"))
    assert isinstance(problem, Problem) and problem.status == "choose_leg"
    again = page.problem_page(problem, {"code": "QF1", "date": "2026-11-04", "dep": "08:00"})
    assert [h for h in read(again).links if h.startswith("/f/")] == links
    assert choices(again) == choices(body)


@pytest.mark.parametrize(("url", "says"), [
    ("/f/QF1/2026-11-04/3?dep=08:00", "QF1 has no leg 3"),
    ("/f/SQ231/2026-11-04/1?dep=08:00&arr=09:00", "That arrival time does not fit a flight"),
    ("/f/SQ231/2026-11-04/1?dep=08:00&from=SIN&to=ZZZ", "Unknown airport code"),
])
def test_an_invalid_request_shows_the_message_beside_the_form(table: vrs.RouteTable, url: str,
                                                              says: str) -> None:
    status, headers, body = get(make(table), url)
    assert status == 400 and says in text_of(body) and 'action="/go"' in body
    assert 'role="alert"' in body and headers["X-Robots-Tag"] == "noindex"


def test_unknown_paths_are_404_and_only_get_and_head_are_taken(table: vrs.RouteTable) -> None:
    app = make(table)
    for url in ("/nope", "/f", "/f/SQ231", "/f/SQ231/2026-11-04", "/f/SQ231/2026-11-04/0?dep=08:00",
                "/f/SQ231/2026-11-04/1/", "/f/SQ231/2026-11-04/1/saved/x", "/static/site.css",
                "/f/%3Cscript%3E/2026-11-04/1", "/" + "a" * 200):
        status, headers, body = get(app, url)
        assert status == 404 and "Page not found" in text_of(body), url
    for method in ("POST", "PUT", "DELETE", "OPTIONS"):
        status, headers, _ = get(app, SQ231_URL, method=method)
        assert (status, headers["Allow"]) == (405, "GET, HEAD")
    status, headers, body = get(app, SQ231_URL, method="HEAD")
    assert status == 200 and body == "" and int(headers["Content-Length"]) > 1000


# --- limits ---------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "/f/SQ231/2026-11-04/1",                              # no departure time
    "/f/SQ231/2026-11-04/1?dep=8am",
    "/f/SQ231/2026-11-04/1?dep=24:00",
    "/f/SQ231/2026-11-04/1?dep=08:00%2B99:00",
    "/f/SQ231/2026-13-40/1?dep=08:00",                    # not a date
    "/f/HELLO/2026-11-04/1?dep=08:00",                    # not a flight number
    "/f/SQ231/2026-11-04/1?dep=08:00&arr=late",
    "/f/SQ231/2026-11-04/1?dep=08:00&fl=9999",
    "/f/SQ231/2026-11-04/1?dep=08:00&fl=5",
    "/f/SQ231/2026-11-04/1?dep=08:00&gs=fast",
    "/f/SQ231/2026-11-04/1?dep=08:00&gs=5000",
    "/f/SQ231/2026-11-04/1?dep=08:00&from=SIN",           # one airport only
    "/f/SQ231/2026-11-04/1?dep=08:00&from=SIN&to=SYDNEY",
    "/f/SQ231/2026-11-04/1?dep=08:00&airline=%3Cb%3E",
    "/f/SQ231/2026-11-04/1?dep=08:00&aircraft=" + "A" * 41,
    "/f/SQ231/2026-11-04/1?dep=08:00&aircraft=Boeing+777%2F300",
    "/f/SQ231/2026-11-04/1?dep=08:00&aircraft=%3Cscript%3Ealert(1)%3C/script%3E",
    "/f/SQ231/2026-11-04/1?dep=08:00&x=" + "y" * 500,     # too long
    "/f/SQ231/2026-11-04/1?dep=08:00" + "&a=1" * 30,      # too many parts
    "/go?code=SQ231&date=tomorrow&dep=08:00",
    "/go?code=%22%3E%3Cscript%3E&date=2026-11-04&dep=08:00",
])
def test_input_is_refused_before_anything_is_asked_for(
        table: vrs.RouteTable, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(web, "request_brief", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(web, "resolve", lambda *a, **k: calls.append(a))
    status, _, body = get(make(table), url)
    assert status == 400 and calls == []
    # nothing refused is said back, not even escaped, and the page stays sealed
    assert "<script>" not in body and "&lt;script" not in body and "alert(1)" not in body
    assert 'action="/go"' in body and 'role="alert"' in body
    sealed(body)


def test_a_valid_request_is_passed_on_once_with_what_was_typed(
        table: vrs.RouteTable, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    def spy(*args: Any) -> Problem:
        calls.append(args)
        return Problem(status="invalid", message="stop here")

    monkeypatch.setattr(web, "request_brief", spy)
    get(make(table), "/f/VT956/2026-11-17/2?dep=06:30+10:00&arr=11:15&aircraft=at-72&from=ppt"
                     "&to=gmr&fl=240&gs=275.5&airline=vta&other=ignored")
    (req, _, root, as_of, fetch, _tiles), = calls
    assert req == BriefRequest(code="VT956", day="2026-11-17", dep="06:30+10:00", arr="11:15",
                               leg=2, airline="VTA", origin="PPT", destination="GMR",
                               aircraft="at-72", fl=240, gs=275.5)
    assert (root, as_of, fetch) == (STORE, utc("2026-11-03T23:00"), None)


def test_brief_pages_are_limited_per_address(table: vrs.RouteTable) -> None:
    clock = [1000.0]
    app = make(table, limit=3, window=600.0, clock=lambda: clock[0])
    assert get(app, "/f/SQ231/2026-11-04/1?dep=bad")[0] == 400  # a refusal is not counted
    assert [get(app, SQ231_URL)[0] for _ in range(3)] == [200, 200, 200]
    status, headers, body = get(app, SQ231_URL)
    assert status == 429 and headers["Retry-After"] == "600" and "Too many briefs" in text_of(body)
    assert NOT_OPS in body and headers["X-Robots-Tag"] == "noindex"
    assert get(app, SQ231_URL, address="192.0.2.2")[0] == 200  # another address
    assert get(app, "/")[0] == 200  # the form is never limited
    clock[0] += 599
    assert get(app, SQ231_URL)[0] == 429
    clock[0] += 2
    assert get(app, SQ231_URL)[0] == 200


def test_behind_a_proxy_the_limit_follows_the_forwarded_address(table: vrs.RouteTable) -> None:
    app = make(table, limit=1, behind_proxy=True)
    assert get(app, SQ231_URL, HTTP_X_FORWARDED_FOR="203.0.113.5, 198.51.100.7")[0] == 200
    assert get(app, SQ231_URL, HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.7")[0] == 429
    assert get(app, SQ231_URL, HTTP_X_FORWARDED_FOR="203.0.113.5, 198.51.100.8")[0] == 200


# --- headers and escaping -------------------------------------------------------------------

def test_headers_keep_the_page_closed_and_unindexed(table: vrs.RouteTable) -> None:
    own = [f"/static/straat/straat.css?v={page.ASSETS}", f"/static/fb.css?v={page.ASSETS}"]
    for app, token in ((make(table), False), (make(table, mapbox="pk.test"), True)):
        for url in ("/", SQ231_URL, "/nope", "/f/U28001/2026-11-04/1?dep=08:00"):
            _, headers, body = get(app, url)
            policy(headers)
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert headers["X-Frame-Options"] == "DENY"
            # with a map the origin may be sent (a token limited to certain sites is checked
            # against it), and never more than the origin; without one, nothing is sent
            assert headers["Referrer-Policy"] == ("strict-origin" if token else "no-referrer")
            assert 'name="referrer"' not in body
            assert int(headers["Content-Length"]) == len(body.encode())
            # no script and no style written into the page; its styles and scripts are this
            # app's own files, and Mapbox's only on a brief with a route when a token is set
            reader = sealed(body)
            mapped = token and url == SQ231_URL
            sheets = [value for tag, name, value in reader.attrs
                      if tag == "link" and name == "href" and value != "data:,"]
            assert sheets == own + ([f"{page.MAPBOX_GL}/mapbox-gl.css"] if mapped else [])
            assert [s["attrs"].get("src") for s in reader.scripts] == [
                f"/static/app.js?v={page.ASSETS}"] + (
                [f"{page.MAPBOX_GL}/mapbox-gl.js", f"/static/map.js?v={page.ASSETS}", None]
                if mapped else [])
            named = [value for _, name, value in reader.attrs if name in ("href", "action", "src")]
            for address in named:
                if address.startswith("/static/"):  # each file it names is one the app serves
                    assert get(app, address)[0] == 200, address
                else:
                    assert (address == "data:," or re.match(r"/(?!/)", address)
                            or mapped and address.startswith(page.MAPBOX_GL + "/")), address
            indexed = url == "/"
            assert ("X-Robots-Tag" in headers) != indexed
            assert ('<meta name="robots" content="noindex">' in body) != indexed


def test_the_loading_screen_is_on_every_page_and_hidden_until_a_brief_is_asked_for(
        table: vrs.RouteTable) -> None:
    app = make(table)
    for url in ("/", SQ231_URL, "/nope", ROUTE_URL, "/go?code=QF1&date=2026-11-04&dep=08:00"):
        _, _, body = get(app, url)
        veil, = VEIL.findall(body)
        assert veil.startswith('<div id="fb-loading" class="fb-veil st-night st-grid" hidden ')
        assert body.endswith(veil + "\n</body>\n</html>\n") and body.count('id="fb-loading"') == 1
        # it names the sources and claims nothing about which of them is done
        assert re.findall(r"<li>([^<]*)</li>", veil) == list(page.READING)
        assert "Reading the sky along your route" in veil and "Reading the sky" not in text_of(body)
        assert f'<script src="/static/app.js?v={page.ASSETS}" defer></script>' in body
    # what leads to a brief is marked for the script; the route form, which does not, is not
    _, _, home = get(app, "/")
    assert '<form class="fb-num" action="/go" method="get" data-brief>' in home
    assert '<form class="fb-search" action="/r" method="get">' in home
    _, _, legs = get(app, "/go?code=QF1&date=2026-11-04&dep=08:00")
    assert legs.count('" data-brief>') == 2
    _, _, numbers = get(app, ROUTE_URL)  # these links open the form, not a brief
    assert "data-brief" not in VEIL.sub("", numbers).replace("[data-brief]", "")


def test_the_apps_own_files_are_served_and_nothing_else_under_static(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    app = make(table)
    for url, kind in (("/static/fb.css", "text/css; charset=utf-8"),
                      ("/static/straat/straat.css", "text/css; charset=utf-8"),
                      ("/static/app.js", "text/javascript; charset=utf-8"),
                      ("/static/map.js", "text/javascript; charset=utf-8")):
        status, headers, body = get(app, url)
        assert status == 200 and headers["Content-Type"] == kind and body.strip(), url
        assert "<html" not in body and int(headers["Content-Length"]) == len(body.encode())
        # kept for a day at least: the page's address for it changes when the file does
        age = re.fullmatch(r"max-age=(\d+)", headers["Cache-Control"])
        assert age is not None and int(age[1]) >= 86400, url
        assert headers["X-Content-Type-Options"] == "nosniff", url
        assert get(app, f"{url}?v={page.ASSETS}")[:2] == (status, headers)
        status, headers, body = get(app, url, method="HEAD")
        assert status == 200 and body == "" and int(headers["Content-Length"]) > 100
        assert get(app, url, method="POST")[0] == 405
    for url in ("/static/../web.py", "/static/x.py", "/static/../../pyproject.toml",
                "/static/straat/../../web.py", "/static/..", "/static/", "/static",
                "/static/fb.css/", "/static//fb.css", "/static/./fb.css", "/static/fb.css%00",
                "/static/straat", "/static/straat/", "/static/straat/STAMP",
                "/static/nothing.css", "/static/nothing.js", "/staticfb.css", "/web.py",
                "/static/%2e%2e/web.py", "/static/..%2fweb.py", "/static/..\\web.py"):
        status, headers, body = get(app, url)
        assert status == 404 and "Page not found" in text_of(body), url
        assert headers["Content-Type"] == "text/html; charset=utf-8", url
        assert "import " not in body and "def " not in body, url
    # the list is made once, from the folder: style sheets, scripts and fonts, by their place
    for name in ("a.css", "b.py", "c.txt", "d.html", "sub/e.js", "sub/f.woff2", "sub/.env"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("x")
    assert web.static_files(tmp_path) == {
        "/static/a.css": tmp_path / "a.css", "/static/sub/e.js": tmp_path / "sub/e.js",
        "/static/sub/f.woff2": tmp_path / "sub/f.woff2"}
    served = web.static_files()
    assert {"/static/fb.css", "/static/app.js", "/static/map.js",
            "/static/straat/straat.css"} <= set(served)
    assert all(re.fullmatch(r"/static/[a-z0-9/_.\-]+\.(css|js|woff2)", url)
               and ".." not in url and path.is_relative_to(web.STATIC)
               for url, path in served.items())


def test_airport_suggestions_are_json_and_say_back_nothing_typed(table: vrs.RouteTable) -> None:
    app = make(table, limit=1)
    status, headers, body = get(app, "/airports?q=sin")
    assert status == 200 and headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["X-Robots-Tag"] == "noindex" and headers["X-Content-Type-Options"] == "nosniff"
    assert int(headers["Content-Length"]) == len(body.encode())
    assert json.loads(body) == [{"iata": "SIN", "city": "Singapore",
                                 "name": "Singapore Changi International Airport"}]
    for typed in ("Sin", "+sin+", "singapore", "changi"):
        assert json.loads(get(app, f"/airports?q={typed}")[2]) == json.loads(body), typed
    found = json.loads(get(app, "/airports?q=london")[2])
    assert [a["iata"] for a in found] == ["LHR", "LGW"]
    assert all(set(a) == {"iata", "city", "name"} and a["city"] == "London" for a in found)
    # too short, too long, not a place, or no such place: an empty list, never an error,
    # and never a word of what was typed
    for typed in ("", "s", "+s+", "y" * 41, "sin" * 14, "%3Cscript%3Ealert(1)%3C/script%3E",
                  "%22%3E%3Cb%3E", "sin%3Cb%3E", "a%26b", "_x", "si%00n", "sin%0Asyd",
                  "atlantis", "sin&x=" + "y" * 400, "sin" + "&a=1" * 30):
        status, headers, body = get(app, f"/airports?q={typed}")
        assert (status, body) == (200, "[]"), typed
        assert headers["Content-Type"] == "application/json; charset=utf-8", typed
    assert get(app, "/airports")[2] == "[]" and get(app, "/airports?x=sin")[2] == "[]"
    assert get(app, "/airports/")[0] == 404 and get(app, "/airports/sin")[0] == 404
    assert get(app, "/airports?q=sin", method="POST")[0] == 405


def test_every_dynamic_string_is_escaped(table: vrs.RouteTable,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    bad = '<img src=x onerror=alert(1)>"&'
    b = brief(table, *AA273)
    b.flight.origin.location = b.flight.callsign = bad
    b.flight.assumptions.append(bad)
    b.summary = bad
    b.hours[0].warnings[0].quoted = bad
    b.hours[1].reports[0].quoted = bad
    b.airports["destination"].taf.text = b.airports["destination"].taf.quoted = bad
    b.notes.append(bad)
    b.provenance[0].url_without_secrets = b.provenance[0].licence = bad
    for h in b.hours:
        h.sources["x<y"] = h.sources["warnings"].model_copy(update={"text": bad})
    b.flight.origin.iata = b.flight.destination.code = bad
    html = page.render_page(b, saved=True, current_url='/f/"><b>', saved_url=bad,
                            mapbox="pk.test")
    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert '/f/"' not in html and 'href="/f/&quot;&gt;&lt;b&gt;"' in html
    assert "onerror" not in [name for _, name, _ in sealed(html).attrs]
    for hostile_page in (page.render_page(b, saved_url=bad), page.render_page(b)):
        assert "<img" not in hostile_page and "&lt;img" in hostile_page
        sealed(hostile_page)

    hostile = Problem(status="choose_airline", message=bad, candidates=[
        {"airline": bad, "name": bad, "callsign": bad, "airports": [bad, bad]}])
    monkeypatch.setattr(web, "request_brief", lambda *a, **k: hostile)
    _, _, body = get(make(table), SQ231_URL)
    assert "<img" not in body and "&lt;img" in body
    sealed(body)
    for status in ("invalid", "could_not_map", "choose_leg"):
        hostile = Problem(status=status, message=bad,
                          legs=[{"index": 1, "origin": bad, "destination": bad}])
        _, _, body = get(make(table), SQ231_URL)
        assert "<img" not in body and "&lt;img" in body
        sealed(body)


# --- saved briefs ---------------------------------------------------------------------------

def test_an_ok_brief_is_saved_with_its_inputs_and_replays_the_same(
        table: vrs.RouteTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make(table, saved=tmp_path)
    _, _, body = get(app, SQ231_URL + "&aircraft=A320")
    link = next(h for h in read(body).links if "/saved/" in h)
    name = link.rsplit("/", 1)[1]
    assert re.fullmatch(r"/f/SQ231/2026-11-04/1/saved/20261103T2300Z-[0-9a-f]{8}", link)
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json"))
    assert files == [f"SQ231/2026-11-04/leg1-{name}.json"]
    kept = json.loads((tmp_path / files[0]).read_text())
    assert kept["request"] == {"code": "SQ231", "day": "2026-11-04", "dep": "08:00", "leg": 1,
                               "aircraft": "A320"}
    assert kept["brief"]["identity"] == {"code": "SQ231", "date": "2026-11-04", "leg": 1,
                                         "issued_at": "2026-11-03T23:00:00Z"}
    live = page.brief_from_record(kept["brief"])
    assert live.record() == kept["brief"]

    # the replay asks for nothing, even at a later time and with the snapshots gone
    def never(*args: Any, **kw: Any) -> None:
        raise AssertionError("a saved brief must not be worked out again")

    monkeypatch.setattr(web, "request_brief", never)
    later = make(table, "2026-12-01T00:00", saved=tmp_path, snapshots=tmp_path / "none", limit=0)
    status, headers, again = get(later, link)
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert "Saved brief, issued Tue 03 Nov 23:00 UTC." in text_of(again)
    assert f'href="{SQ231_URL}&amp;aircraft=A320"' in again  # back to the current brief
    assert page.brief_body(live) in body and page.brief_body(live) in again
    assert "Saved brief" not in body
    monkeypatch.undo()

    # the same brief again writes nothing new; other inputs get their own file
    get(app, SQ231_URL + "&aircraft=A320")
    assert len(list(tmp_path.rglob("*.json"))) == 1
    get(app, "/f/SQ231/2026-11-04/1?dep=08:10")
    assert len(list(tmp_path.rglob("*.json"))) == 2 and not list(tmp_path.rglob("*.part"))

    assert get(app, link[:-1] + ("0" if link[-1] != "0" else "1"))[0] == 404
    assert get(make(table), link)[0] == 404  # nothing is kept, so nothing to show
    (tmp_path / files[0]).write_text("{not json")
    assert get(app, link)[0] == 404


def test_only_ok_briefs_are_saved(table: vrs.RouteTable, tmp_path: Path) -> None:
    get(make(table, "2026-11-04T03:00", saved=tmp_path), SQ231_URL)        # departed
    get(make(table, "2026-10-28T00:00", saved=tmp_path), SQ231_URL)        # too far out
    get(make(table, saved=tmp_path), "/f/U28001/2026-11-04/1?dep=08:00")   # no route
    assert list(tmp_path.rglob("*")) == []


# --- live data ------------------------------------------------------------------------------

def test_without_snapshots_and_without_the_live_module_the_page_says_so(
        table: vrs.RouteTable, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(flight_brief, "live", raising=False)
    monkeypatch.setitem(sys.modules, "flight_brief.live", None)  # the import fails
    monkeypatch.setattr(web, "request_brief", lambda *a, **k: pytest.fail("must not be called"))
    status, _, body = get(make(table, snapshots=None), SQ231_URL)
    assert status == 503 and "Live data is not installed" in text_of(body) and NOT_OPS in body


def test_without_snapshots_the_live_fetch_fills_the_cache(
        table: vrs.RouteTable, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fetch(root: Path, leg: Any, flight: Any, as_of: datetime) -> None:
        raise ConnectionError("the source is down")

    fake = types.SimpleNamespace(fetch=fetch)
    monkeypatch.setattr(flight_brief, "live", fake, raising=False)
    monkeypatch.setitem(sys.modules, "flight_brief.live", fake)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(web, "request_brief",
                        lambda *a: calls.append(a) or Problem(status="invalid", message="stop"))
    app = make(table, snapshots=None, cache=tmp_path)
    get(app, SQ231_URL)
    assert calls[0][2:] == (tmp_path, utc("2026-11-03T23:00"), fetch, None)
    # a source that fails gives a page, not a stack trace
    monkeypatch.setattr(web, "request_brief", request_brief)
    status, _, body = get(app, SQ231_URL)
    assert status == 502 and "Try again in a few minutes" in text_of(body)
    assert "ConnectionError" not in body and "Traceback" not in body


# --- wording --------------------------------------------------------------------------------

def test_no_verdict_and_no_severity_word_outside_quoted_text(table: vrs.RouteTable) -> None:
    pages = {name: page.render_page(brief(table, *flight)) for name, flight in
             (("SQ231", SQ231), ("UA837", UA837), ("AA273", AA273), ("UA1175", UA1175))}
    pages["UA837 the day before"] = page.render_page(
        brief(table, "UA837", "2026-11-02", "18:00", "2026-11-02T22:00"))
    pages["stale"] = page.render_page(brief(table, "SQ231", "2026-11-04", "07:40",
                                            "2026-11-03T23:00", root=Path("no-such-store")))
    pages["typed airports"] = page.render_page(brief(
        table, "VT956", "2026-11-17", "06:30", "2026-11-17T10:00", airports=("PPT", "GMR")))
    pages["saved"] = page.render_page(brief(table, *AA273), saved=True, current_url="/f/x")
    pages["with the map"] = page.render_page(brief(table, *AA273), mapbox="pk.test")
    app = make(table)
    for url in ("/", "/?airports=1", "/nope", SQ231_URL, "/f/AA273/2026-11-13/1?dep=21:40",
                "/f/U28001/2026-11-04/1?dep=08:00", "/f/BA10A/2026-11-04/1?dep=08:00",
                "/go?code=QF1&date=2026-11-04&dep=08:00", "/f/SQ231/2026-11-04/1?dep=late",
                "/f/SQ231/2026-11-04/1?dep=08:00&aircraft=ZZZZ"):
        pages[url] = get(app, url)[2]
    pages["departed"] = get(make(table, "2026-11-04T03:00"), SQ231_URL)[2]
    pages["too far out"] = get(make(table, "2026-10-28T00:00"), SQ231_URL)[2]
    pages["limit"] = get(make(table, limit=0), SQ231_URL)[2]
    for name, html in pages.items():
        sealed(html)
        # the whole page: the loading screen, the drawings and their labels, every attribute
        rest = own_text(html)
        assert 'id="fb-loading"' in rest and "Reading the sky along your route" in rest, name
        assert '<svg class="hud"' in rest and "aria-label=" in rest, name
        # a strength word stands only in quoted text and in the plain answer
        outside = outside_the_answer(rest)
        assert not SEVERITY.search(outside), (name, SEVERITY.search(outside))
        assert not VERDICT.search(rest), (name, VERDICT.search(rest))
        assert NOT_OPS in rest, name
    assert 'aria-label="Initial track ' in own_text(pages["AA273"])
    assert "GS KT" in own_text(pages["AA273"]) and "GS KT" in own_text(pages["/"])
    assert 'id="fb-map-data"' in own_text(pages["with the map"])
    # the check bites: the sources' own words do carry such words, inside the quoted spans
    assert SEVERITY.search(pages["AA273"]) and "TURB-HI MOD" in pages["AA273"]
    assert '<li class="fb-cell big s-stale"' in pages["stale"]
    assert "Official warnings: no data in this snapshot set" in text_of(pages["stale"])
    # the words the page's own scripts and style sheet can put on the screen keep the rules too
    for name in ("app.js", "map.js", "fb.css"):
        text = (web.STATIC / name).read_text(encoding="utf-8")
        text = re.sub(r"\bprefers-color-scheme\b[^)]*|\bcolor-scheme:[^;}]*", "", text)
        assert not SEVERITY.search(text), (name, SEVERITY.search(text))
        assert not VERDICT.search(text), (name, VERDICT.search(text))


def test_what_the_brief_cannot_know_is_listed_line_by_line(table: vrs.RouteTable) -> None:
    fixed = ["The route: the route is a great-circle guess, not the filed route. The filed "
             "route can lie well to either side.",
             "Delays and cancellations are not tracked. Check your airline."]
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T23:00")
    html = page.render_page(b)
    lines = unknowns(html)
    # a source that is silent for part of the flight, and one that never covers it
    assert lines == [
        "Official warnings, hours 4 to 8: None issued this far ahead",
        "Pilot reports, hours 1 to 8: Not covered here (too few pilot reports mention "
        "turbulence in this region)", *fixed]
    assert ('<i class="die off"></i><b>What this brief cannot know yet</b>'
            f'<span class="meta">{len(lines)}</span>') in html
    # what a source did say is not in this list: it is in the source's own lane
    assert b.hours[0].sources["warnings"].state == "flagged"
    assert not [x for x in lines if "hour 1" in x or "Nothing found" in x or "hours 2" in x]

    # too early carries when to look again; no reports, a stale source, and both airports
    b = brief(table, "UA837", "2026-11-02", "18:00", "2026-11-02T22:00")
    lines = unknowns(page.render_page(b))
    assert lines[-2:] == fixed
    for key, name in SOURCES:
        for state in page.UNKNOWN:
            hit = [h for h in b.hours if key in h.sources and h.sources[key].state == state]
            mine = [x for x in lines if x.startswith(f"{name}, ") and hit
                    and x.split(": ", 1)[1].startswith(hit[0].sources[key].text)]
            assert len(mine) == bool(hit), (name, state)
            if hit:
                span = (f"hours {hit[0].index} to {hit[-1].index}" if len(hit) > 1
                        else f"hour {hit[0].index}")
                assert mine[0].startswith(f"{name}, {span}: "), mine
            again = [h.sources[key].check_after for h in hit if h.sources[key].check_after]
            if again:  # said once: in the engine's own line, or else as the tag
                assert mine[0].lower().count("check again") == 1, mine
                assert day_hhmm(min(again)) in mine[0], mine
            elif hit:
                assert "Check again from" not in mine[0]
    states = {line.state for h in b.hours for line in h.sources.values()}
    assert {"too_early", "no_reports", "stale"} <= states
    assert any(x == "Pilot reports, hours 3 to 10: Too early to know. Check again after "
                    "Mon 02 Nov 22:20 UTC" for x in lines)
    # within one source the lines run in the order of the flight
    mine = [int(x.split("hour")[1].split()[1 if "hours" in x else 0].rstrip(":"))
            for x in lines if x.startswith("Pilot reports, hour")]
    assert mine == sorted(mine)
    f = b.flight
    assert [x for x in lines if ", forecast: " in x or ", observation: " in x] == [
        f"{f.origin.location}, forecast: {b.airports['origin'].taf.text}",
        f"{f.origin.location}, observation: {b.airports['origin'].metar.text}",
        f"{f.destination.location}, forecast: {b.airports['destination'].taf.text}",
        f"{f.destination.location}, observation: {b.airports['destination'].metar.text}"]
    assert len(lines) == 3 + 4 + 2

    # hours that do not run on are counted, not given as a span they do not fill
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T23:00")
    b.hours[5].sources["warnings"] = SourceLine(state="nothing_found", text="Nothing found")
    assert unknowns(page.render_page(b))[0] == (
        "Official warnings, 4 of 8 hours: None issued this far ahead")
    # an airport line that is known is not listed; one that is not, is
    quiet = brief(table, *AA273)
    known = [end for end, a in quiet.airports.items()
             if a.taf.state == "ok" and a.metar.state == "ok"]
    lines = unknowns(page.render_page(quiet))
    assert known == ["destination"]
    assert not [x for x in lines if x.startswith(quiet.flight.destination.location)]
    assert f"{quiet.flight.origin.location}, forecast: Not covered here: no TAF for " \
           "this airport" in lines


# --- finding the flight: a route and a day, and the schedule --------------------------------

ROUTE_URL = "/r/SIN/SYD/2026-11-04"
SCHEDULED_URL = ("/f/SQ231/2026-11-04/1?dep=08:00&arr=18:50&aircraft=Airbus+A350-900"
                 "&from=SIN&to=SYD")
ATTRIBUTION = '<a href="https://aerodatabox.com/">AeroDataBox</a>'


def departure(number: str, dep: str, arr: str | None, to: str, *, start: str | None = None,
              model: str | None = "Airbus A350-900", airline: str = "Singapore Airlines",
              hours: tuple[int, int] = (8, 11)) -> dict[str, Any]:
    """One flight as the schedule source sends it. `dep` and `arr` are local, HH:MM, on
    2026-11-04, an arrival earlier in the day than the departure being on the 5th; `hours`
    are the two airports' UTC offsets."""
    def both(hhmm: str, offset: int, day: int = 4) -> dict[str, str]:
        local = datetime.fromisoformat(f"2026-11-{day:02d}T{hhmm}")
        return {"utc": f"{local - timedelta(hours=offset):%Y-%m-%d %H:%M}Z",
                "local": f"{local:%Y-%m-%d %H:%M}{offset:+03d}:00"}

    item: dict[str, Any] = {
        "departure": {"scheduledTime": both(dep, hours[0])},
        "arrival": {"airport": {"icao": to, "name": to}}, "number": number,
        "status": "Expected", "codeshareStatus": "IsOperator", "isCargo": False,
        "airline": {"name": airline}}
    if start:
        item["departure"]["airport"] = {"icao": start, "name": start}
    if arr:
        item["arrival"]["scheduledTime"] = both(arr, hours[1], 5 if arr < dep else 4)
    if model:
        item["aircraft"] = {"model": model}
    return item


class Gateway:
    def __init__(self, *replies: Any) -> None:
        self.replies, self.asked = list(replies), []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        self.asked.append(url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, tuple) else (200, json.dumps(reply))


def scheduled(tmp_path: Path, *replies: Any, **kw: Any) -> tuple[Any, Gateway]:
    from flight_brief.schedule import Schedule

    gateway = Gateway(*replies)
    return Schedule("k3y", tmp_path / "schedule", gateway,
                    lambda: utc("2026-11-03T23:00"), **kw), gateway


BOARD = ({"departures": [
    departure("SQ 231", "08:00", "18:50", "YSSY"),
    departure("SQ 322", "09:00", "15:45", "EGLL", hours=(8, 0)),
    departure("QF 2", "07:10", None, "YSSY", model="Airbus A380-800 (winglets)",
              airline="Qantas")]},
    {"departures": [departure("SQ 221", "20:40", "07:30", "YSSY", model=None)]})


@pytest.mark.parametrize(("url", "says"), [
    ("/r", "Enter the place you are flying from. Enter the place you are flying to. "
           "Enter the departure date"),
    ("/r?from=Singapore&to=&date=2026-11-04", "Enter the place you are flying to"),
    ("/r?from=Singapore&to=Sydney", "Enter the departure date"),
    ("/r?from=Singapore&to=Sydney&date=4+November", "That is not a date"),
    ("/r?from=Singapore&to=Sydney&date=2026-13-40", "That is not a date"),
    ("/r?from=%3Cscript%3Ealert(1)%3C/script%3E&to=Sydney&date=2026-11-04",
     "Type the place you are flying from as a city or an airport code"),
    ("/r?from=Singapore&to=a%26b%3Dc&date=2026-11-04", "Type the place you are flying to"),
    ("/r?from=Singapore&to=_x&date=2026-11-04", "Type the place you are flying to"),
    ("/r?from=Singapore&to=" + "y" * 41 + "&date=2026-11-04", "One of the values is too long"),
    ("/r?from=Singapore&to=Sydney&date=2026-11-04&x=" + "y" * 400, "That address is too long"),
])
def test_the_route_form_refuses_what_it_cannot_read_and_echoes_only_what_passed(
        table: vrs.RouteTable, monkeypatch: pytest.MonkeyPatch, url: str, says: str) -> None:
    asked: list[Any] = []
    monkeypatch.setattr(vrs.RouteTable, "find_airports", lambda *a, **k: asked.append(a))
    monkeypatch.setattr(vrs.RouteTable, "best_airport", lambda *a, **k: asked.append(a))
    status, headers, body = get(make(table), url)
    assert status == 400 and says in text_of(body) and asked == []
    assert headers["X-Robots-Tag"] == "noindex" and headers["Cache-Control"] == "no-store"
    assert "<script>" not in body and "&lt;script" not in body and "alert(1)" not in body
    assert "a&b" not in body and "a&amp;b" not in body and "_x" not in body
    assert 'action="/r"' in body and 'role="alert"' in body
    reader = sealed(body)
    values = {value for tag, name, value in reader.attrs if tag == "input" and name == "value"}
    assert values <= {"Singapore", "Sydney", "2026-11-04"}
    assert fields(body, "/go") == {} and set(fields(body, "/r").values()) == values
    if "from=Singapore" in url and "address" not in says:
        assert fields(body, "/r")["from"] == "Singapore"


def test_the_route_form_finds_one_airport_each_side_and_redirects(table: vrs.RouteTable) -> None:
    app = make(table)
    for typed in ("from=sin&to=SYD", "from=Singapore&to=sydney", "from=+changi+&to=YSSY",
                  "from=WSSS&to=Kingsford+Smith"):
        status, headers, body = get(app, f"/r?{typed}&date=2026-11-04")
        assert (status, headers["Location"], body) == (303, ROUTE_URL, ""), typed
        assert headers["X-Robots-Tag"] == "noindex"
    # one address per route: lower case goes to the upper-case one
    status, headers, _ = get(app, "/r/sin/syd/2026-11-04")
    assert (status, headers["Location"]) == (302, ROUTE_URL)


def test_several_airports_for_a_place_are_offered_as_links_that_keep_the_rest(
        table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/r?from=singapore&to=London&date=2026-11-04")
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    text = text_of(body)
    assert "Which airport are you flying to?" in text
    assert "London Heathrow Airport (LHR), London, GB" in text
    links = [h for h in read(body).links if h.startswith("/r?")]
    assert links == ["/r?from=singapore&to=LHR&date=2026-11-04",
                     "/r?from=singapore&to=LGW&date=2026-11-04"]
    status, headers, _ = get(app, links[1])
    assert (status, headers["Location"]) == (303, "/r/SIN/LGW/2026-11-04")
    # the departure side is asked first when both are open
    _, _, body = get(app, "/r?from=london&to=san&date=2026-11-04")
    assert "Which airport are you flying from?" in text_of(body)
    assert [h for h in read(body).links if h.startswith("/r?")][0] == (
        "/r?from=LHR&to=san&date=2026-11-04")


def test_a_place_with_no_airport_and_the_same_airport_twice_give_the_form_again(
        table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/r?from=Atlantis&to=O%27Hare&date=2026-11-04")
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert "No airport was found for the place you are flying from" in text_of(body)
    assert fields(body, "/r") == {"from": "Atlantis", "to": "O'Hare", "date": "2026-11-04"}
    assert fields(body, "/go") == {} and "O&#x27;Hare" in body
    _, _, body = get(app, "/r?from=SIN&to=changi&date=2026-11-04")
    assert "The two airports are the same" in text_of(body)
    for url in ("/r/SIN/SIN/2026-11-04", "/r/SIN/ZZZ/2026-11-04", "/r/SIN/SYD/2026-13-40"):
        status, headers, body = get(app, url)
        assert status in (400, 404) and 'role="alert"' in body, url
        assert headers["X-Robots-Tag"] == "noindex"
    for url in ("/r/SIN/SYD", "/r/SIN/SYD/2026-11-04/", "/r/SINGAPORE/SYD/2026-11-04",
                "/r/%3Cb%3E/SYD/2026-11-04", "/r/"):
        assert get(app, url)[0] == 404, url


def test_the_route_page_lists_the_days_flights_from_the_schedule(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    schedule, gateway = scheduled(tmp_path, *BOARD)
    app = make(table, schedule=schedule)
    status, headers, body = get(app, ROUTE_URL)
    assert status == 200 and len(gateway.asked) == 2
    assert headers["X-Robots-Tag"] == "noindex" and headers["Cache-Control"] == "no-store"
    assert '<meta name="robots" content="noindex">' in body
    assert headers["Content-Security-Policy"].startswith("default-src 'none'")
    text = text_of(body)
    assert "Singapore (SIN) to Sydney (SYD)" in text and "Wednesday 4 November 2026" in text
    # departs, arrives (not known for one; the next day for another), number, airline, aircraft
    assert flights(body) == [
        ("07:10", "--:--", "QF2", "Qantas", "Airbus A380-800 (winglets)", ""),
        ("08:00", "18:50", "SQ231", "Singapore Airlines", "Airbus A350-900", ""),
        ("20:40", "07:30+1", "SQ221", "Singapore Airlines", "", "")]
    assert '<b>Flights</b><span class="meta">3 that day</span>' in body
    assert "SQ322" not in text  # it goes to London
    assert "Times are local at each airport" in text
    assert ATTRIBUTION in body and "Schedule: AeroDataBox" in text
    # each flight is a link to its brief, with the times, the aircraft and the two airports,
    # so the brief does not depend on the route table having the number
    links = [h for h in read(body).links if h.startswith("/f/")]
    assert links == [
        "/f/QF2/2026-11-04/1?dep=07:10&aircraft=Airbus+A380-800+winglets&from=SIN&to=SYD",
        SCHEDULED_URL, "/f/SQ221/2026-11-04/1?dep=20:40&arr=07:30&from=SIN&to=SYD"]
    assert not [h for h in read(body).links if h.startswith("/?code=")]  # no fallback list
    assert body.count('<a class="fb-flight" ') == body.count(' data-brief="') == len(links)
    status, _, brief_page = get(app, SCHEDULED_URL)
    assert status == 200 and "SQ231 · Singapore to Sydney" in text_of(brief_page)
    assert "Arrives Wed 04 Nov 18:50" in text_of(brief_page)
    assert "not recognised" in text_of(brief_page)  # the slice has no A350; the brief is made
    for link in links:  # SQ221 is not in the route table at all
        assert get(app, link)[0] == 200, link
    # the same route again, and another destination from the same airport: nothing is asked
    assert get(app, ROUTE_URL)[2] == body
    _, _, london = get(app, "/r/SIN/LHR/2026-11-04")
    assert flights(london) == [
        ("09:00", "15:45", "SQ322", "Singapore Airlines", "Airbus A350-900", "")]
    assert len(gateway.asked) == 2 and schedule.units_spent() == 4


def test_without_a_schedule_the_route_page_lists_the_flight_numbers_it_knows(
        table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, ROUTE_URL)
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    text = text_of(body)
    assert "Flight numbers on this route" in text and "QF2 Qantas" in text
    assert "Departure times are not known here, so choose your flight number and type the " \
           "departure time on your booking" in text
    assert "AeroDataBox" not in body
    links = [h for h in read(body).links if h.startswith("/?")]
    assert links == ["/?code=QF2&date=2026-11-04&from=SIN&to=SYD",
                     "/?code=SQ231&date=2026-11-04&from=SIN&to=SYD"]
    # the link is the form with the number, the date and the two airports filled in
    status, headers, form_page = get(app, links[1])
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert fields(form_page, "/go") == {"code": "SQ231", "date": "2026-11-04", "from": "SIN",
                                        "to": "SYD"}
    assert fields(form_page, "/r") == {}
    assert choices(body) == [(links[0], "QF2 Qantas"), (links[1], "SQ231 Singapore Airlines")]
    # adding the time gives the brief for the typed airports
    status, headers, _ = get(app, "/go?code=QF2&date=2026-11-04&from=SIN&to=SYD&dep=08:00")
    assert (status, headers["Location"]) == (
        303, "/f/QF2/2026-11-04/1?dep=08:00&from=SIN&to=SYD")
    # what a link carries is checked like anything typed
    status, _, odd = get(app, "/?code=%3Cscript%3E&date=tomorrow&from=SIN&to=SYD&dep=25:00")
    assert status == 200 and "tomorrow" not in odd and "25:00" not in odd
    assert "<script>" not in odd and "&lt;script" not in odd
    assert fields(odd, "/go") == {"from": "SIN", "to": "SYD"} and fields(odd, "/r") == {}
    sealed(odd)


@pytest.mark.parametrize("replies", [
    ((429, ""),), ((500, ""),), (TimeoutError(),), ((200, "not json"),),
    ((204, ""), (204, "")),                          # answered: no flights at all that day
    ({"departures": [departure("SQ 322", "09:00", "15:45", "EGLL", hours=(8, 0))]},
     {"departures": []}),                            # answered: none to Sydney
])
def test_a_schedule_with_no_answer_or_no_flights_falls_back_to_the_numbers(
        table: vrs.RouteTable, tmp_path: Path, replies: tuple[Any, ...]) -> None:
    schedule, gateway = scheduled(tmp_path, *replies)
    status, _, body = get(make(table, schedule=schedule), ROUTE_URL)
    text = text_of(body)
    assert status == 200 and "Flight numbers on this route" in text
    assert "/?code=SQ231&date=2026-11-04&from=SIN&to=SYD" in read(body).links
    answered = len(replies) == 2
    assert ("No flights were found on this route that day" in text) == answered
    assert "AeroDataBox" not in body  # none of its data is on the page


def test_a_month_with_no_units_left_falls_back_without_asking(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    schedule, gateway = scheduled(tmp_path, monthly_units=20, margin=20)
    app = make(table, schedule=schedule)
    status, _, body = get(app, ROUTE_URL)
    assert status == 200 and "Flight numbers on this route" in text_of(body)
    status, _, body = get(app, "/go?code=SQ231&date=2026-11-04")
    assert status == 400 and "Enter the departure time" in text_of(body)
    assert gateway.asked == []


def test_a_route_with_nothing_known_gives_the_airports_form(table: vrs.RouteTable,
                                                            tmp_path: Path) -> None:
    status, headers, body = get(make(table), "/r/SIN/PPT/2026-11-04")
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert "No flight numbers are known here for this route. Enter your flight number and " \
           "the departure time on your booking" in text_of(body)
    assert fields(body, "/go") == {"date": "2026-11-04", "from": "SIN", "to": "PPT"}
    assert fields(body, "/r") == {}
    schedule, _ = scheduled(tmp_path, (204, ""), (204, ""))
    _, _, body = get(make(table, schedule=schedule), "/r/SIN/PPT/2026-11-04")
    assert "No flights were found on this route that day. Enter your flight number" in \
        text_of(body)


def test_a_number_typed_without_a_time_has_its_time_looked_up(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    schedule, gateway = scheduled(
        tmp_path, [departure("SQ 231", "08:00", "18:50", "YSSY", start="WSSS")])
    app = make(table, schedule=schedule)
    # the time field may be left empty when there is a schedule to ask, and only then
    for one, needed in ((app, False), (make(table), True)):
        _, _, home = get(one, "/")
        dep, = [i for i in read(home).inputs if i["form"] == "/go" and i["name"] == "dep"]
        assert dep["type"] == "time" and ("required" in dep) == needed
        assert ("Leave empty to have it looked up" in text_of(home)) != needed
        assert ('<span class="meta">time and aircraft looked up</span>' in home) != needed
    status, headers, body = get(app, "/go?code=sq231&date=2026-11-04")
    # the route table has SQ231 from Singapore to Sydney, so the airports are not carried
    assert (status, headers["Location"], body) == (
        303, "/f/SQ231/2026-11-04/1?dep=08:00&arr=18:50&aircraft=Airbus+A350-900", "")
    assert gateway.asked == ["https://aerodatabox.p.rapidapi.com/flights/number/SQ231/"
                             "2026-11-04?dateLocalRole=Departure"]
    assert get(app, headers["Location"])[0] == 200
    # what the passenger typed is kept over what the schedule says; nothing is asked twice
    _, headers, _ = get(app, "/go?code=SQ231&date=2026-11-04&arr=19:05&aircraft=A320")
    assert headers["Location"] == "/f/SQ231/2026-11-04/1?dep=08:00&arr=19:05&aircraft=A320"
    assert len(gateway.asked) == 1
    # a typed time needs no schedule
    _, headers, _ = get(app, "/go?code=SQ231&date=2026-11-04&dep=08:10")
    assert headers["Location"] == "/f/SQ231/2026-11-04/1?dep=08:10" and len(gateway.asked) == 1


def test_a_looked_up_number_the_route_table_lacks_carries_its_airports(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    schedule, gateway = scheduled(
        tmp_path,
        [departure("U2 8001", "08:00", "09:20", "EGKK", start="EGAC", hours=(0, 0),
                   model="Airbus A320", airline="easyJet")],
        # QF1 is two legs in the route table; the second is known there, by its airports
        [departure("QF 1", "06:10", "13:25", "EGLL", start="WSSS", hours=(8, 0), model=None,
                   airline="Qantas"),
         departure("QF 1", "00:05", "05:10", "WSSS", start="YSSY", hours=(11, 8), model=None,
                   airline="Qantas")],
        # SQ322 is Singapore to London in the route table; the schedule has it to Sydney
        [departure("SQ 322", "09:00", "19:50", "YSSY", start="WSSS", model=None)],
        [departure("SQ 231", "08:00", None, "ZZZZ", start="WSSS", model=None)])
    app = make(table, schedule=schedule)
    status, headers, _ = get(app, "/go?code=U28001&date=2026-11-04")
    assert (status, headers["Location"]) == (
        303, "/f/U28001/2026-11-04/1?dep=08:00&arr=09:20&aircraft=Airbus+A320"
             "&from=EGAC&to=EGKK")
    assert get(app, headers["Location"])[0] == 200
    # several departures on the day: a choice, each with its own leg of the number
    status, headers, body = get(app, "/go?code=QF1&date=2026-11-04")
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    text = text_of(body)
    assert "QF1 leaves 2 times" in text and "Wednesday 4 November 2026" in text
    assert ('<header class="fb-route"><span class="cap">Wednesday 4 November 2026</span>'
            "<h1>QF1 leaves 2 times</h1>") in body
    assert flights(body) == [("00:05", "05:10", "QF1", "Qantas", "", "YSSY \u00b7 WSSS"),
                             ("06:10", "13:25", "QF1", "Qantas", "", "WSSS \u00b7 EGLL")]
    assert ATTRIBUTION in body
    assert [h for h in read(body).links if h.startswith("/f/")] == [
        "/f/QF1/2026-11-04/1?dep=00:05&arr=05:10", "/f/QF1/2026-11-04/2?dep=06:10&arr=13:25"]
    # the schedule's airports win over the route table's when they differ
    _, headers, _ = get(app, "/go?code=SQ322&date=2026-11-04")
    assert headers["Location"] == "/f/SQ322/2026-11-04/1?dep=09:00&arr=19:50&from=WSSS&to=YSSY"
    # an airport this table does not have is not put in the address
    _, headers, _ = get(app, "/go?code=SQ231&date=2026-11-04")
    assert headers["Location"] == "/f/SQ231/2026-11-04/1?dep=08:00"


@pytest.mark.parametrize("reply", [(204, ""), (404, ""), (429, ""), (500, ""), [],
                                   OSError("down"), (200, "{")])
def test_a_time_that_cannot_be_looked_up_is_asked_for_as_before(
        table: vrs.RouteTable, tmp_path: Path, reply: Any) -> None:
    schedule, _ = scheduled(tmp_path, reply)
    status, headers, body = get(make(table, schedule=schedule),
                                "/go?code=SQ231&date=2026-11-04")
    assert status == 400 and "Enter the departure time" in text_of(body)
    assert 'name="code" value="SQ231"' in body and headers["X-Robots-Tag"] == "noindex"


def test_a_schedule_is_not_asked_when_the_input_is_refused(table: vrs.RouteTable,
                                                           tmp_path: Path) -> None:
    schedule, gateway = scheduled(tmp_path)
    app = make(table, schedule=schedule)
    for url in ("/go?code=HELLO&date=2026-11-04", "/go?code=SQ231&date=tomorrow",
                "/go?code=SQ231", "/go?date=2026-11-04", "/go?code=SQ231&date=2026-11-04&fl=5",
                "/go?code=SQ231&date=2026-11-04&from=SIN", "/r/SIN/SIN/2026-11-04",
                "/r/SIN/SYD/2026-02-30", "/r/SIN/QQQ/2026-11-04"):
        assert get(app, url)[0] in (400, 404), url
    assert gateway.asked == [] and schedule.units_spent() == 0


def test_route_pages_and_time_look_ups_are_rate_limited_per_address(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    schedule, gateway = scheduled(
        tmp_path, *BOARD, [departure("SQ 231", "08:00", "18:50", "YSSY", start="WSSS")])
    app = make(table, schedule=schedule, limit=2)
    assert get(app, ROUTE_URL)[0] == 200
    assert get(app, "/go?code=SQ231&date=2026-11-04")[0] == 303
    for url in (ROUTE_URL, "/go?code=SQ231&date=2026-11-04", SCHEDULED_URL):
        status, headers, body = get(app, url)
        assert status == 429 and headers["Retry-After"] == "600", url
        assert headers["X-Robots-Tag"] == "noindex" and "Try again in 10 minutes" in text_of(body)
    assert len(gateway.asked) == 3
    # what costs nothing is not counted: the forms, finding the airports, a typed time
    for url in ("/", "/r?from=sin&to=syd&date=2026-11-04",
                "/go?code=SQ231&date=2026-11-04&dep=08:00", "/r/sin/syd/2026-11-04"):
        assert get(app, url)[0] in (200, 302, 303), url
    assert get(app, ROUTE_URL, address="192.0.2.2")[0] == 200  # another address
    assert get(make(table, limit=1), ROUTE_URL)[0] == 200      # with no schedule too
    limited = make(table, limit=1)
    assert [get(limited, ROUTE_URL)[0] for _ in range(2)] == [200, 429]


def test_an_aircraft_type_that_is_not_recognised_is_noted_on_the_brief(
        table: vrs.RouteTable) -> None:
    app = make(table)
    for typed, shown in (("ZZZZ", "ZZZZ"), ("Boeing+777-300ER", "Boeing 777-300ER")):
        status, _, body = get(app, f"{SQ231_URL}&aircraft={typed}")
        assert status == 200
        assert (f"Aircraft type '{shown}' was not recognised, so the distance decides the "
                "cruise level and the speed") in text_of(body)
    plain = text_of(get(app, SQ231_URL)[2])
    assert "not recognised" not in plain
    # a name the table can place is used, not noted
    assert "A320, a jet: no figure of its own" in text_of(
        get(app, SQ231_URL + "&aircraft=Airbus+A320neo")[2])


def test_the_new_pages_keep_the_wording_rules_and_every_header(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    schedule, _ = scheduled(
        tmp_path, *BOARD,
        [departure("QF 1", "06:10", "13:25", "EGLL", start="WSSS", hours=(8, 0)),
         departure("QF 1", "00:05", "05:10", "WSSS", start="YSSY", hours=(11, 8))])
    with_schedule, without = make(table, schedule=schedule), make(table)
    pages = {}
    for app, name in ((with_schedule, "schedule"), (without, "no schedule")):
        for url in ("/", ROUTE_URL, "/r?from=singapore&to=london&date=2026-11-04",
                    "/r?from=nowhere&to=london&date=2026-11-04", "/r?from=a", "/r/SIN/PPT/2026-11-04",
                    "/go?code=QF1&date=2026-11-04", "/?code=SQ231&date=2026-11-04&from=SIN&to=SYD"):
            status, headers, body = get(app, url)
            pages[f"{name} {url}"] = body
            assert headers["X-Content-Type-Options"] == "nosniff", url
            assert headers["Referrer-Policy"] == "no-referrer", url
            policy(headers)
            if url != "/":
                assert headers["X-Robots-Tag"] == "noindex", url
                assert headers["Cache-Control"] == "no-store", url
                assert '<meta name="robots" content="noindex">' in body, url
    assert "X-Robots-Tag" not in get(without, "/")[1]
    for name, html in pages.items():
        read(html)
        rest = own_text(html)
        # a strength word stands only in quoted text and in the plain answer
        outside = outside_the_answer(rest)
        assert not SEVERITY.search(outside), (name, SEVERITY.search(outside))
        assert not VERDICT.search(rest), (name, VERDICT.search(rest))
        assert NOT_OPS in rest, name
        sealed(html)
        assert 'id="fb-loading"' in rest and '<svg class="hud"' in rest, name
        assert "http://" not in html and "mapbox" not in html, name
        outside = set(re.findall(r'(?:href|src|action)="(https?:[^"]*)"', html))
        assert outside <= {"https://aerodatabox.com/"}, name
