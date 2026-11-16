"""The page, called as a WSGI app with no socket: the form, the redirect, the brief, the
pages for requests that give no brief, the limits, the headers and the saved briefs."""

import base64
import hashlib
import json
import re
import sys
import types
from collections.abc import Callable
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from wsgiref.util import setup_testing_defaults

import pytest

import flight_brief
from brief_helpers import AA273, SEVERITY, SQ231, STORE, UA837, UA1175, brief, utc
from flight_brief import page, vrs, web
from flight_brief.brief import DEPARTED, NOT_OPS, Brief, hhmm
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
    source's own words (`<q>`) put back inside double quotation marks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.parts: list[str] = []
        self.links: list[str] = []
        self.attrs: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))
            if tag == "a" and name == "href":
                self.links.append(value or "")
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
        if tag == "q":
            self.parts.append('"')

    def handle_data(self, data: str) -> None:
        if "style" not in self.open:
            self.parts.append(data)


def read(html: str) -> Reader:
    """Parse a page, failing unless it is well formed."""
    assert html.startswith("<!doctype html>")
    reader = Reader()
    reader.feed(html)
    reader.close()
    assert reader.open == [], f"left open: {reader.open}"
    return reader


def text_of(html: str) -> str:
    return " ".join("".join(read(html).parts).split())


def own_text(html: str) -> str:
    """The page source with the style block and every quoted span taken out: what is left,
    tags and attributes included, is this project's own wording."""
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)
    return re.sub(r'<q class="quoted[^"]*">.*?</q>', "", html, flags=re.DOTALL)


# --- the form and the redirect -------------------------------------------------------------

def test_the_form_asks_for_the_flight_and_shows_the_airports_only_when_needed(
        table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/")
    assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8"
    names = {value for tag, name, value in read(body).attrs if tag == "input" and name == "name"}
    assert names == {"code", "date", "dep", "arr", "aircraft"}
    assert '<form class="ask" action="/go" method="get">' in body
    assert NOT_OPS in body and "X-Robots-Tag" not in headers

    status, _, body = get(app, "/?airports=1")
    names = {value for tag, name, value in read(body).attrs if tag == "input" and name == "name"}
    assert status == 200 and {"from", "to"} <= names


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
    # the header lines of the text brief, piece by piece
    for line in lines[:4]:
        for piece in line.split(" . "):
            assert piece in seen, piece
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
    assert f"Sydney (YSSY), arrival {hhmm(b.flight.arrival_utc)}" in seen
    for own in [*b.flight.assumptions, *b.notes, *{p.licence for p in b.provenance}]:
        assert own in seen, own
    assert "great-circle guess" in seen and NOT_OPS in seen


def test_the_hour_strip_shows_each_state_as_a_symbol_and_a_colour(table: vrs.RouteTable) -> None:
    _, _, body = get(make(table), SQ231_URL)
    b = brief(table, "SQ231", "2026-11-04", "08:00", "2026-11-03T23:00")
    cells = re.findall(r'<li class="s-(\w+)" title="[^"]*"><span class="n">(\d+)</span>'
                       r'<span class="sym" aria-hidden="true">(.)</span>'
                       r'<span class="vh">([^<]+)</span></li>', body)
    assert [(state, int(n)) for state, n, _, _ in cells] == [(h.state, h.index) for h in b.hours]
    assert cells[0][2:] == ("#", "flagged")
    for state in {h.state for h in b.hours}:
        assert f".s-{state}{{" in page.CSS  # the colour
    for word in page.STATE_WORDS.values():  # the key names every state
        assert f"</span>{word}</li>" in body


def test_the_map_draws_each_hour_of_the_route_and_both_airports(table: vrs.RouteTable) -> None:
    _, _, body = get(make(table, "2026-11-03T01:00"), "/f/UA837/2026-11-02/1?dep=17:40")
    b = brief(table, "UA837", "2026-11-02", "17:40", "2026-11-03T01:00", root=STORE)
    svg = body[body.index("<svg"):body.index("</svg>")]
    width, height = map(int, re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).groups())  # type: ignore[union-attr]
    stretches = re.findall(r'<polyline class="seg m-(\w+)" points="([^"]+)"', svg)
    assert [s for s, _ in stretches] == [h.state for h in b.hours]
    # across the antimeridian every point stays inside the picture, in one unbroken line
    xs = [float(p.split(",")[0]) for _, points in stretches for p in points.split()]
    ys = [float(p.split(",")[1]) for _, points in stretches for p in points.split()]
    assert 0 < min(xs) and max(xs) < width and 0 < min(ys) and max(ys) < height
    assert all(abs(a - b) < width / 4 for a, b in zip(xs, xs[1:], strict=False))
    assert re.findall(r'<text class="hn[ b]*" [^>]*>(\d+)</text>', svg) == [
        str(h.index) for h in b.hours]
    assert ">SFO</text>" in svg and ">NRT</text>" in svg and 'class="grat"' in svg
    assert "great-circle guess, not the filed route" in body


def test_a_source_the_page_has_not_heard_of_gets_a_line_of_its_own(table: vrs.RouteTable) -> None:
    from flight_brief.brief import SourceLine

    b = brief(table, *SQ231)
    for h in b.hours:
        h.sources["shear"] = SourceLine(state="nothing_found", text="Nothing found: index 3")
        h.shear = {"value": 3.0, "threshold": 8.0}
    b.hours[1].sources["gtg"] = SourceLine(state="flagged", text='GTG says "MOD"')
    seen = text_of(page.render_page(b))
    assert f"Shear hours 1 to {len(b.hours)}: Nothing found: index 3" in seen
    assert 'Gtg hour 2: GTG says "MOD"' in seen
    assert "threshold" not in seen  # the dicts are shown only through the source line


def test_departed_and_too_far_out_show_the_header_and_the_message(table: vrs.RouteTable) -> None:
    status, headers, body = get(make(table, "2026-11-04T03:00"), SQ231_URL)
    seen = text_of(body)
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert "SQ231 · Singapore to Sydney" in seen and DEPARTED in seen and NOT_OPS in seen
    assert "<svg" not in body and "What each source says" not in seen

    _, _, body = get(make(table, "2026-10-28T00:00"), SQ231_URL)
    seen = text_of(body)
    assert "Too far out. Come back within 30 hours of departure, after Mon 02 Nov 18:00 UTC" in seen
    assert "Departs Wed 04 Nov 08:00 UTC+8 (00:00 UTC), as you entered" in seen


# --- requests that give no brief -----------------------------------------------------------

def test_a_number_with_no_route_shows_the_form_with_the_airports(table: vrs.RouteTable) -> None:
    app = make(table)
    status, headers, body = get(app, "/f/U28001/2026-11-04/1?dep=08:00")
    reader = read(body)
    assert status == 200 and COULD_NOT_MAP in text_of(body)
    inputs = {name: value for tag, name, value in reader.attrs if tag == "input"}
    filled = dict(re.findall(r'<input name="(\w+)" value="([^"]*)"', body))
    assert filled == {"code": "U28001", "date": "2026-11-04", "dep": "08:00"}
    assert 'name="from"' in body and 'name="to"' in body and inputs
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
    assert "Leg 2: WSSS to EGLL" in text_of(body)
    assert "leg 2 · WSSS to EGLL" in text_of(get(app, links[1])[2])
    # the same page from the service's own answer
    problem = request_brief(BriefRequest(code="QF1", day="2026-11-04", dep="08:00"), table,
                            STORE, utc("2026-11-03T23:00"))
    assert isinstance(problem, Problem) and problem.status == "choose_leg"
    again = page.problem_page(problem, {"code": "QF1", "date": "2026-11-04", "dep": "08:00"})
    assert [h for h in read(again).links if h.startswith("/f/")] == links


@pytest.mark.parametrize(("url", "says"), [
    ("/f/SQ231/2026-11-04/1?dep=08:00&aircraft=ZZZZ", "is not an aircraft type this brief knows"),
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
    "/f/SQ231/2026-11-04/1?dep=08:00&aircraft=" + "A" * 40,
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
    assert "<script" not in body and "alert(1)" not in body and 'action="/go"' in body
    read(body)


def test_a_valid_request_is_passed_on_once_with_what_was_typed(
        table: vrs.RouteTable, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    def spy(*args: Any) -> Problem:
        calls.append(args)
        return Problem(status="invalid", message="stop here")

    monkeypatch.setattr(web, "request_brief", spy)
    get(make(table), "/f/VT956/2026-11-17/2?dep=06:30+10:00&arr=11:15&aircraft=at-72&from=ppt"
                     "&to=gmr&fl=240&gs=275.5&airline=vta&other=ignored")
    (req, _, root, as_of, fetch), = calls
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
    app = make(table)
    for url in ("/", SQ231_URL, "/nope", "/f/U28001/2026-11-04/1?dep=08:00"):
        _, headers, body = get(app, url)
        csp = headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp and "unsafe" not in csp and "script-src" not in csp
        assert "form-action 'self'" in csp and "frame-ancestors 'none'" in csp
        style = re.search(r"<style>(.*?)</style>", body, re.DOTALL)
        assert style is not None
        digest = base64.b64encode(hashlib.sha256(style[1].encode()).digest()).decode()
        assert f"style-src 'sha256-{digest}'" in csp  # the one style block, by its hash
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY" and headers["Referrer-Policy"] == "no-referrer"
        assert int(headers["Content-Length"]) == len(body.encode())
        # no script, no inline style, nothing fetched from anywhere but this page
        attrs = read(body).attrs
        assert "<script" not in body and not [a for a in attrs if a[1] in ("style", "src")]
        assert not [a for a in attrs if a[1].startswith("on")]
        hrefs = [value for _, name, value in attrs if name in ("href", "action")]
        assert all(h.startswith("/") or h == "data:," for h in hrefs)
        indexed = url == "/"
        assert ("X-Robots-Tag" in headers) != indexed
        assert ('<meta name="robots" content="noindex">' in body) != indexed


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
    html = page.render_page(b, saved=True, current_url='/f/"><b>', saved_url=bad)
    assert "<img" not in html and "<b>" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    read(html)

    hostile = Problem(status="choose_airline", message=bad, candidates=[
        {"airline": bad, "name": bad, "callsign": bad, "airports": [bad, bad]}])
    monkeypatch.setattr(web, "request_brief", lambda *a, **k: hostile)
    _, _, body = get(make(table), SQ231_URL)
    assert "<img" not in body and "&lt;img" in body
    read(body)
    for status in ("invalid", "could_not_map", "choose_leg"):
        hostile = Problem(status=status, message=bad,
                          legs=[{"index": 1, "origin": bad, "destination": bad}])
        _, _, body = get(make(table), SQ231_URL)
        assert "<img" not in body and "&lt;img" in body
        read(body)


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
    assert calls[0][2:] == (tmp_path, utc("2026-11-03T23:00"), fetch)
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
        read(html)
        rest = own_text(html)
        assert not SEVERITY.search(rest), (name, SEVERITY.search(rest))
        assert not VERDICT.search(rest), (name, VERDICT.search(rest))
        assert NOT_OPS in rest, name
    # the check bites: the sources' own words do carry such words, inside the quoted spans
    assert SEVERITY.search(pages["AA273"]) and "TURB-HI MOD" in pages["AA273"]
    assert '<li class="s-stale"' in pages["stale"]
    assert "Official warnings: no data in this snapshot set" in text_of(pages["stale"])
    assert not SEVERITY.search(re.sub(r"\bprefers-color-scheme\b|color-scheme:light dark", "",
                                      page.CSS))
