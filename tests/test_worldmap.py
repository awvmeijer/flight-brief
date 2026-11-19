"""The world map: the pictures (a PNG written by hand, Web Mercator rows, the shift to 180
west), the colours the estimate and the storm clouds get, the two addresses, and the page's
wording."""

import datetime as dt
import json
import re
import struct
import zlib
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from wsgiref.util import setup_testing_defaults

import numpy as np
import pytest

from brief_helpers import STORE, utc
from flight_brief import page, ride, vrs, web, worldmap
from flight_brief.forecast import layer
from flight_brief.forecast import shear_tiles as st
from flight_brief.forecast import wx_tiles as wx

UTC = dt.timezone.utc
RUN = dt.datetime(2026, 11, 14, 12, tzinfo=UTC)
STAMP = "20261114T12"
NOW = RUN + dt.timedelta(hours=11)
HOURS = (6, 9, 12)
VOID = {"meta", "link", "input", "br", "hr", "img"}
NEVER = re.compile(r"safe|severe|score", re.IGNORECASE)   # "unsafe" has "safe" in it
BARE = re.compile(r"\b(light|moderate)\b(?! \(estimate\))", re.IGNORECASE)
App = Callable[..., Any]


def shear_tile(hours: tuple[int, ...] = HOURS, run: dt.datetime = RUN) -> st.ShearTile:
    ti1 = np.zeros((len(hours), len(st.LAYER_FLS), st.NJ, st.NI), np.uint8)
    meta = {"format": st.FORMAT, "step": st.STEP, "run": f"{run:%Y-%m-%dT%H:00Z}"}
    return st.ShearTile(ti1, list(hours), list(st.LAYER_FLS), meta)


def wx_tile(hours: tuple[int, ...] = HOURS, run: dt.datetime = RUN) -> wx.WxTile:
    u, v, top = wx.empty(len(hours))
    top[:] = wx.TOP_NONE
    meta = {"format": wx.FORMAT, "run": f"{run:%Y-%m-%dT%H:00Z}",
            "wind_step_kt": wx.WIND_STEP_KT, "top_step_fl": wx.TOP_STEP_FL}
    return wx.WxTile(u, v, top, list(hours), list(st.LAYER_FLS), meta)


def keep(root: Path, tile: st.ShearTile | wx.WxTile) -> None:
    """Write a tile where the store keeps it: <root>/shear-tiles-YYYY-MM/<tile name>."""
    name = (st if isinstance(tile, st.ShearTile) else wx).tile_name(tile.run)
    path = root / f"shear-tiles-{tile.run:%Y-%m}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tile.to_bytes())


def make(table: vrs.RouteTable, root: Path | None, now: Callable[[], dt.datetime] = lambda: NOW,
         **kw: Any) -> App:
    kw.setdefault("mapbox", "pk.test")
    return web.create_app(table, snapshots=Path("unused-snapshots"), cache=Path("unused-cache"),
                          saved=None, now=now,
                          tiles=layer.TileSource(root) if root is not None else None, **kw)


def get(app: App, url: str, address: str = "192.0.2.1") -> tuple[int, dict[str, str], bytes]:
    path, _, query = url.partition("?")
    environ: dict[str, Any] = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query,
                               "REMOTE_ADDR": address}
    setup_testing_defaults(environ)
    seen: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        seen["status"], seen["headers"] = int(status.split()[0]), dict(headers)

    body = b"".join(app(environ, start))
    return seen["status"], seen["headers"], body


def decode(data: bytes) -> tuple[np.ndarray, list[tuple[int, int, int]], list[int]]:
    """A paletted PNG read back with zlib alone: (indices, palette, alphas). Every chunk's
    length and checksum is checked on the way."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        crc, = struct.unpack(">I", data[pos + 8 + size:pos + 12 + size])
        assert crc == zlib.crc32(kind + body), kind
        chunks.append((kind, body))
        pos += 12 + size
    assert pos == len(data)
    assert [kind for kind, _ in chunks] == [b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"]
    parts = dict(chunks)
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", parts[b"IHDR"])
    assert (depth, colour, compression, filtering, interlace) == (8, 3, 0, 0, 0)
    lines = np.frombuffer(zlib.decompress(parts[b"IDAT"]), np.uint8).reshape(height, width + 1)
    assert not lines[:, 0].any()            # every line is unfiltered
    plte = parts[b"PLTE"]
    assert len(plte) % 3 == 0 and len(parts[b"tRNS"]) == len(plte) // 3 and parts[b"IEND"] == b""
    palette = [tuple(plte[i:i + 3]) for i in range(0, len(plte), 3)]
    return lines[:, 1:], palette, list(parts[b"tRNS"])


class Reader(HTMLParser):
    """Checks that every tag closes in order and collects the text a reader sees, the form
    controls with whether a label holds them, the scripts and the style sheets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.parts: list[str] = []
        self.inputs: list[dict[str, Any]] = []
        self.scripts: list[dict[str, Any]] = []
        self.sheets: list[str] = []
        self.attrs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        given = {name: value or "" for name, value in attrs}
        self.attrs += [(tag, name) for name in given]
        if tag == "input":
            self.inputs.append({**given, "labelled": "label" in self.open})
        if tag == "script":
            self.scripts.append({"attrs": given, "text": ""})
        if tag == "link" and given.get("rel") == "stylesheet":
            self.sheets.append(given["href"])
        if tag not in VOID:
            self.open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.open and self.open[-1] == tag, (tag, self.open[-3:])
        self.open.pop()

    def handle_data(self, data: str) -> None:
        if "script" in self.open:
            self.scripts[-1]["text"] += data
        else:
            self.parts.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def read(body: bytes) -> Reader:
    html = body.decode("utf-8")
    assert html.startswith("<!doctype html>")
    reader = Reader()
    reader.feed(html)
    reader.close()
    assert reader.open == [], reader.open
    # nothing is run or styled from inside the page
    assert "<style" not in html.lower()
    assert not [a for a in reader.attrs if a[1] == "style" or a[1].startswith("on")]
    for script in reader.scripts:
        if "src" in script["attrs"]:
            assert script["attrs"]["src"].startswith(("/static/", page.MAPBOX_GL + "/"))
            assert "defer" in script["attrs"] and not script["text"].strip()
        else:
            assert script["attrs"].get("type") == "application/json"
    return reader


def blob(reader: Reader) -> dict[str, Any]:
    data, = [s["text"] for s in reader.scripts if s["attrs"].get("id") == "fb-world-data"]
    return json.loads(data)


# --- the PNG -------------------------------------------------------------------------------

def test_a_picture_is_an_8_bit_paletted_png_that_reads_back_to_its_indices() -> None:
    rng = np.random.default_rng(7)
    indices = rng.integers(0, 3, size=(5, 9), dtype=np.uint8)
    palette = [(0, 0, 0, 0), (57, 255, 20, 112), (232, 150, 44, 184)]
    data = worldmap.png(indices, palette)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[8:16] == struct.pack(">I", 13) + b"IHDR"
    assert struct.unpack(">IIBBBBB", data[16:29]) == (9, 5, 8, 3, 0, 0, 0)   # 9 wide, 5 tall
    back, colours, alphas = decode(data)
    assert np.array_equal(back, indices)
    assert colours == [(0, 0, 0), (57, 255, 20), (232, 150, 44)] and alphas == [0, 112, 184]


def test_a_picture_with_an_index_the_palette_lacks_is_refused() -> None:
    palette = [(0, 0, 0, 0), (255, 255, 255, 140)]
    with pytest.raises(ValueError):
        worldmap.png(np.full((2, 2), 2, np.uint8), palette)
    with pytest.raises(ValueError):
        worldmap.png(np.zeros((2, 2), np.int32), palette)
    with pytest.raises(ValueError):
        worldmap.png(np.zeros(4, np.uint8), palette)


# --- where a grid point lands --------------------------------------------------------------

def test_mercator_rows_put_the_equator_in_the_middle_and_stretch_the_high_latitudes() -> None:
    for height, n, step in ((1440, st.NJ, st.GRID_DEG), (720, wx.TNJ, wx.TOP_DEG)):
        rows = worldmap.mercator_rows(height, n, step)
        equator = (n - 1) // 2
        assert rows.shape == (height,)
        assert rows[height // 2 - 1] == rows[height // 2] == equator
        assert (np.diff(rows) >= 0).all()                       # north to south, never back
        assert np.array_equal(rows + rows[::-1], np.full(height, n - 1))   # the same both ways
        # the picture ends at 85.0511 degrees: its first row is the grid's 85N, not its pole
        assert rows[0] == round(5 / step) and rows[-1] == n - 1 - round(5 / step)
        # five degrees of latitude take over four times as many picture rows at 75 to 80
        # degrees as at the equator
        per = round(1 / step)
        high = np.count_nonzero((rows >= 10 * per) & (rows < 15 * per))
        low = np.count_nonzero((rows >= equator - 5 * per) & (rows < equator))
        assert low > 0 and high > 4 * low


def test_the_picture_runs_from_180_west_to_180_east() -> None:
    tile = shear_tile()
    tile.ti1[0, 0, 360, 0] = 40        # the equator at 0E: index 20, moderate
    tile.ti1[0, 0, 360, 720] = 21      # the equator at 180E: index 10.5, light
    tile.ti1[0, 0, 360, 1080] = 21     # the equator at 90W
    picture, _, _ = decode(worldmap.estimate_png(tile, 0, 0))
    assert picture.shape == (1440, 1440)
    assert picture[720, 720] == 2      # 0E is the middle column
    assert picture[720, 0] == 1        # 180E is the left edge
    assert picture[720, 360] == 1      # 90W is a quarter of the way across
    assert np.count_nonzero(picture[720]) == 3
    grid = np.arange(8, dtype=np.uint8)[None, :].repeat(3, axis=0)
    assert worldmap.on_the_map(grid, 2, 85.0)[0].tolist() == [4, 5, 6, 7, 0, 1, 2, 3]


# --- the colours ---------------------------------------------------------------------------

def test_the_estimate_s_colours_follow_the_medium_cuts_exactly() -> None:
    assert ride.ESTIMATE_CUTS[worldmap.WEIGHT] == (10.5, 20.0)
    tile = shear_tile()
    counts = {20: 0, 21: 1, 39: 1, 40: 2, 254: 2, st.MISSING: 0}   # 10.0, 10.5, 19.5, 20.0, 127
    for col, count in enumerate(counts):
        tile.ti1[1, 2, 360, col * 8] = count
    picture, colours, alphas = decode(worldmap.estimate_png(tile, 1, 2))
    assert [int(picture[720, 720 + col * 8]) for col in range(len(counts))] == list(counts.values())
    for count, word in counts.items():   # the same word `ride` gives a brief
        if count != st.MISSING:
            assert ride.estimate_level(count * st.STEP, "medium") == word
    assert colours == [(0, 0, 0), (57, 255, 20), (232, 150, 44)]      # nothing, green, amber
    assert alphas[0] == 0 and all(0 < a < 255 for a in alphas[1:])    # clear, then translucent
    # another hour or layer of the same tile is not drawn into this one
    assert not decode(worldmap.estimate_png(tile, 0, 2))[0].any()
    assert not decode(worldmap.estimate_png(tile, 1, 1))[0].any()


def test_storm_clouds_are_white_from_fl300_up() -> None:
    tile = wx_tile()
    tops = {59: 0, 60: 1, 70: 1, wx.TOP_NONE: 0, wx.TOP_MISSING: 0}   # FL295, FL300, FL350
    for col, count in enumerate(tops):
        tile.top[2, 180, col * 4] = count
    picture, colours, alphas = decode(worldmap.storm_png(tile, 2))
    assert picture.shape == (720, 720)
    assert [int(picture[360, 360 + col * 4]) for col in range(len(tops))] == list(tops.values())
    assert colours == [(0, 0, 0), (255, 255, 255)] and alphas[0] == 0 and 0 < alphas[1] < 255
    higher, _, _ = decode(worldmap.storm_png(tile, 2, min_fl=350))
    assert [int(higher[360, 360 + col * 4]) for col in range(len(tops))] == [0, 0, 1, 0, 0]
    assert not decode(worldmap.storm_png(tile, 0))[0].any()


# --- the frames ----------------------------------------------------------------------------

def test_each_frame_names_its_time_and_takes_the_storm_picture_valid_at_that_time() -> None:
    estimate = worldmap.Run(RUN, HOURS, (320, 363, 415))
    same = worldmap.frames(estimate, worldmap.Run(RUN, HOURS, (0,)))
    assert [f["label"] for f in same] == ["Sat 14 Nov 18:00 UTC · +6 h", "Sat 14 Nov 21:00 UTC · +9 h",
                                          "Sun 15 Nov 00:00 UTC · +12 h"]
    assert same[1]["estimate"] == {str(fl): f"/map/field/estimate/{STAMP}/9/{fl}.png"
                                   for fl in (320, 363, 415)}
    assert [f["storm"] for f in same] == [f"/map/field/storm/{STAMP}/{h}/0.png" for h in HOURS]
    # a storm tile from the run before: the same valid time is six forecast hours further on
    older = worldmap.Run(RUN - dt.timedelta(hours=6), (6, 9, 12, 15), (0,))
    assert [f["storm"] for f in worldmap.frames(estimate, older)] == [
        "/map/field/storm/20261114T06/12/0.png", "/map/field/storm/20261114T06/15/0.png", None]
    assert [f["storm"] for f in worldmap.frames(estimate, None)] == [None, None, None]


# --- the page ------------------------------------------------------------------------------

def test_the_page_with_a_token_carries_the_map_its_controls_and_its_data(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    keep(tmp_path, shear_tile())
    keep(tmp_path, wx_tile())
    app = make(table, tmp_path)
    status, headers, body = get(app, "/map")
    assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Content-Security-Policy"] == get(app, "/")[1]["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "strict-origin"
    reader = read(body)
    html = body.decode()
    assert 'id="fb-world"' in html and "The map needs JavaScript and WebGL." in reader.text
    # Mapbox's script and style sheet, then this page's own; each of the app's files is served
    assert [s["attrs"].get("src") for s in reader.scripts] == [
        f"/static/app.js?v={page.ASSETS}", f"{page.MAPBOX_GL}/mapbox-gl.js",
        f"/static/map.js?v={page.ASSETS}", None, f"/static/worldmap.js?v={worldmap.ASSETS}"]
    assert reader.sheets[-2:] == [f"{page.MAPBOX_GL}/mapbox-gl.css",
                                  f"/static/worldmap.css?v={worldmap.ASSETS}"]
    for url, kind in (("/static/worldmap.js", "text/javascript; charset=utf-8"),
                      ("/static/worldmap.css", "text/css; charset=utf-8")):
        status, headers, served = get(app, url)
        assert status == 200 and headers["Content-Type"] == kind and served.strip()
    # the route map's script finds nothing of its own on this page
    assert 'id="fb-map"' not in html and 'id="fb-map-data"' not in html
    data = blob(reader)
    assert data["token"] == "pk.test" and data["storm"] is True
    assert data["corners"] == [[-180, 85.0511], [180, 85.0511], [180, -85.0511], [-180, -85.0511]]
    assert [f["hour"] for f in data["frames"]] == list(HOURS)
    assert data["frames"] == worldmap.frames(worldmap.Run(RUN, HOURS, tuple(st.LAYER_FLS)),
                                             worldmap.Run(RUN, HOURS, (0,)))
    # the controls are real form controls, each inside a label: the keyboard works them
    slider, = [i for i in reader.inputs if i["type"] == "range"]
    assert (slider["min"], slider["max"], slider["step"]) == ("0", "2", "1") and slider["labelled"]
    assert slider["value"] == "2"                   # the frame nearest now, 11 h after the run
    assert slider["aria-valuetext"] == "Sun 15 Nov 00:00 UTC · +12 h"
    assert '<output id="fb-world-when" for="fb-world-hour">Sun 15 Nov 00:00 UTC · +12 h</output>' in html
    levels = [i for i in reader.inputs if i["type"] == "radio"]
    assert [i["value"] for i in levels] == ["320", "363", "415"]
    assert {i["name"] for i in levels} == {"fb-world-level"} and all(i["labelled"] for i in levels)
    assert ["checked" in i for i in levels] == [False, True, False]
    assert all(f"<span>FL{fl}</span>" in html for fl in st.LAYER_FLS)
    box, = [i for i in reader.inputs if i["type"] == "checkbox"]
    assert box["id"] == "fb-world-storm" and "checked" in box and box["labelled"]
    # they are switched off until the script has a map to work on
    assert all("disabled" in i for i in reader.inputs)
    # the legend, the run and the plain sentences
    text = reader.text
    for line in ("green: light (estimate)", "amber: moderate (estimate)",
                 "white: storm clouds forecast to FL300 or higher", "no colour: smooth (estimate)",
                 "Estimate: GFS run of Sat 14 Nov 12:00 UTC",
                 "Storm clouds: GFS run of Sat 14 Nov 12:00 UTC",
                 "this project's own wind-shear index, worked out from the GFS weather model. "
                 "It is not an official turbulence forecast.",
                 "NOAA's official turbulence forecast, which covers the contiguous United "
                 "States, is not on this map.",
                 "Not for flight operations"):
        assert line in text, line


def test_without_a_storm_tile_the_map_still_shows_the_estimate(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    keep(tmp_path, shear_tile())
    reader = read(get(make(table, tmp_path), "/map")[2])
    data = blob(reader)
    assert data["storm"] is False and [f["storm"] for f in data["frames"]] == [None, None, None]
    box, = [i for i in reader.inputs if i["type"] == "checkbox"]
    assert "checked" not in box and "disabled" in box
    assert reader.text.count("Storm clouds: no forecast file yet") == 2   # the control, the foot


def test_without_a_token_the_page_says_the_map_is_not_set_up(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    keep(tmp_path, shear_tile())
    status, headers, body = get(make(table, tmp_path, mapbox=None), "/map")
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert headers["Referrer-Policy"] == "no-referrer"
    reader = read(body)
    html = body.decode()
    assert "The map is not set up on this copy of the page: it needs a Mapbox token." in reader.text
    assert '<b>World map</b><span class="meta">map not set up</span>' in html
    # it is the route map's own sentence, in the same place
    brief = web.create_app(table, snapshots=STORE, cache=Path("unused-cache"), saved=None,
                           now=lambda: utc("2026-11-03T01:00"))
    route = get(brief, "/f/UA837/2026-11-02/1?dep=17:40")[2].decode()
    said = re.compile(r'<div class="fb-map empty" role="img" aria-label="[^"]*">(<p>.*?</p>)</div>')
    assert said.findall(html) == said.findall(route) == [f"<p>{worldmap.NOT_SET_UP}</p>"]
    assert "fb-world-data" not in html and "mapbox-gl" not in html and "worldmap.js" not in html
    assert not reader.inputs
    assert "It is not an official turbulence forecast." in reader.text


def test_without_a_tile_the_page_says_the_forecast_file_is_not_there_and_shows_no_map(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    status, headers, body = get(make(table, tmp_path), "/map")          # an empty folder
    assert status == 200 and headers["Cache-Control"] == "no-store"
    reader = read(body)
    assert "The forecast file for this map is not available yet." in reader.text
    for missing in ('id="fb-world"', 'class="fb-map', "fb-world-data", "mapbox-gl", "worldmap.js"):
        assert missing not in body.decode(), missing
    assert not reader.inputs
    off = read(get(make(table, None), "/map")[2])                       # forecasts turned off
    assert "This copy of the page runs without forecast files, so there is no map." in off.text
    assert "not available yet" not in off.text
    # a storm tile alone gives no map either: the times come from the estimate's tile
    keep(tmp_path, wx_tile())
    alone = read(get(make(table, tmp_path), "/map")[2])
    assert "The forecast file for this map is not available yet." in alone.text


def test_the_page_s_own_words(table: vrs.RouteTable, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    keep(tmp_path, shear_tile())
    keep(tmp_path, wx_tile())
    pages = [get(make(table, tmp_path), "/map")[2], get(make(table, tmp_path, mapbox=None), "/map")[2],
             get(make(table, empty), "/map")[2], get(make(table, None), "/map")[2]]
    for body in pages:
        text = read(body).text
        assert not NEVER.search(text), NEVER.search(text)
        assert not NEVER.search(body.decode()), NEVER.search(body.decode())   # nor in the markup
        assert not BARE.search(text), BARE.search(text)
    # a strength word is one of `ride`'s, and the estimate never has the last of them
    full = read(pages[0]).text
    assert full.count("light (estimate)") >= 2 and full.count("moderate (estimate)") >= 2
    for name in ("worldmap.js", "worldmap.css"):
        own = (web.STATIC / name).read_text(encoding="utf-8")
        assert not NEVER.search(own) and not BARE.search(own), name


# --- the pictures' address -----------------------------------------------------------------

def test_a_picture_is_served_as_a_png_and_drawn_once(
        table: vrs.RouteTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shear, clouds = shear_tile(), wx_tile()
    shear.ti1[1, 1, 360, 0] = 40
    clouds.top[1, 180, 0] = 80
    keep(tmp_path, shear)
    keep(tmp_path, clouds)
    app = make(table, tmp_path)
    drawn: list[str] = []
    for name in ("estimate_png", "storm_png"):
        real = getattr(worldmap, name)
        monkeypatch.setattr(worldmap, name, lambda *a, _real=real, _name=name, **k: (
            drawn.append(_name), _real(*a, **k))[1])
    # no page was opened first: the app finds the newest run by itself
    status, headers, body = get(app, f"/map/field/estimate/{STAMP}/9/363.png")
    assert status == 200 and headers["Content-Type"] == "image/png"
    assert headers["Cache-Control"] == "public, max-age=3600"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert int(headers["Content-Length"]) == len(body) < 300_000
    picture, _, _ = decode(body)
    assert picture.shape == (1440, 1440) and picture[720, 720] == 2
    assert np.count_nonzero(picture) == 2      # the equator's grid row is two picture rows
    status, headers, body = get(app, f"/map/field/storm/{STAMP}/9/0.png")
    assert status == 200 and headers["Content-Type"] == "image/png"
    picture, _, _ = decode(body)
    assert picture.shape == (720, 720) and picture[360, 360] == 1 and np.count_nonzero(picture) == 2
    assert drawn == ["estimate_png", "storm_png"]
    # every address the page gives is one the app serves
    for frame in blob(read(get(app, "/map")[2]))["frames"]:
        for url in (*frame["estimate"].values(), frame["storm"]):
            assert get(app, url)[0] == 200, url
    assert len(drawn) == len(HOURS) * 4
    # asked for again, nothing is drawn again
    again = get(app, f"/map/field/estimate/{STAMP}/9/363.png")
    assert again[0] == 200 and decode(again[2])[0][720, 720] == 2
    assert get(app, f"/map/field/storm/{STAMP}/9/0.png")[0] == 200
    assert len(drawn) == len(HOURS) * 4


def test_an_unknown_kind_run_hour_or_level_is_not_found(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    keep(tmp_path, shear_tile())
    keep(tmp_path, wx_tile())
    keep(tmp_path, shear_tile(run=RUN - dt.timedelta(hours=6)))   # on disk, but not the newest
    app = make(table, tmp_path, limit=1000)
    assert get(app, f"/map/field/estimate/{STAMP}/9/363.png")[0] == 200
    for url in (
            f"/map/field/shear/{STAMP}/9/363.png", f"/map/field/gtg/{STAMP}/9/363.png",       # kind
            f"/map/field/ESTIMATE/{STAMP}/9/363.png", f"/map/field//{STAMP}/9/363.png",
            "/map/field/estimate/20261114T06/9/363.png",      # a run this app never offered
            "/map/field/estimate/20261114T18/9/363.png", "/map/field/estimate/20261114/9/363.png",
            "/map/field/estimate/2026-11-14T12/9/363.png", "/map/field/estimate/20261399T99/9/363.png",
            "/map/field/estimate/20261114t12/9/363.png", "/map/field/estimate/../9/363.png",
            f"/map/field/estimate/{STAMP}/3/363.png", f"/map/field/estimate/{STAMP}/10/363.png",  # hour
            f"/map/field/estimate/{STAMP}/09/363.png", f"/map/field/estimate/{STAMP}/0/363.png",
            f"/map/field/estimate/{STAMP}/-9/363.png", f"/map/field/estimate/{STAMP}/9.0/363.png",
            f"/map/field/estimate/{STAMP}/nine/363.png", f"/map/field/estimate/{STAMP}//363.png",
            f"/map/field/estimate/{STAMP}/9/360.png", f"/map/field/estimate/{STAMP}/9/0.png",     # level
            f"/map/field/estimate/{STAMP}/9/0363.png", f"/map/field/estimate/{STAMP}/9/36.png",
            f"/map/field/estimate/{STAMP}/9/3630.png", f"/map/field/estimate/{STAMP}/9/.png",
            f"/map/field/storm/{STAMP}/9/363.png", f"/map/field/storm/{STAMP}/9/1.png",
            f"/map/field/storm/{STAMP}/15/0.png",
            f"/map/field/estimate/{STAMP}/9/363", f"/map/field/estimate/{STAMP}/9/363.PNG",       # shape
            f"/map/field/estimate/{STAMP}/9/363.png/", f"/map/field/estimate/{STAMP}/9/363.jpg",
            f"/map/field/estimate/{STAMP}/9", "/map/field/", "/map/field", "/map/", "/maps", "/map.png"):
        status, headers, body = get(app, url)
        assert status == 404 and headers["Content-Type"] == "text/html; charset=utf-8", url
        assert b"Page not found" in body, url
    assert get(app, f"/map/field/storm/{STAMP}/9/0.png")[0] == 200
    # with forecasts turned off there are no pictures at all
    assert get(make(table, None), f"/map/field/estimate/{STAMP}/9/363.png")[0] == 404


def test_a_page_opened_before_a_new_run_keeps_its_pictures(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    keep(tmp_path, shear_tile())
    clock = [NOW]
    app = make(table, tmp_path, now=lambda: clock[0])
    assert blob(read(get(app, "/map")[2]))["frames"][0]["estimate"]["320"] == (
        f"/map/field/estimate/{STAMP}/6/320.png")
    newer = RUN + dt.timedelta(hours=6)
    keep(tmp_path, shear_tile(run=newer))
    # within five minutes the app does not look again
    clock[0] = NOW + dt.timedelta(minutes=4)
    assert f"/estimate/{STAMP}/" in get(app, "/map")[2].decode()
    clock[0] = NOW + dt.timedelta(minutes=6)
    assert "/map/field/estimate/20261114T18/6/320.png" in get(app, "/map")[2].decode()
    assert get(app, "/map/field/estimate/20261114T18/6/320.png")[0] == 200
    assert get(app, f"/map/field/estimate/{STAMP}/12/415.png")[0] == 200   # not drawn before


def test_the_page_and_the_drawing_of_pictures_are_limited_per_address(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    keep(tmp_path, shear_tile())
    app = make(table, tmp_path, limit=1)
    assert get(app, "/map")[0] == 200
    status, headers, _ = get(app, "/map")
    assert status == 429 and headers["Retry-After"] == "600"
    assert get(app, "/map", address="192.0.2.2")[0] == 200
    # drawing has its own count, a few pictures for each brief: one view asks for many
    urls = [f"/map/field/estimate/{STAMP}/{hour}/{fl}.png" for hour in HOURS for fl in (320, 363)]
    assert worldmap.DRAWS == 4 and len(urls) > worldmap.DRAWS
    assert [get(app, url)[0] for url in urls] == [200, 200, 200, 200, 429, 429]
    assert [get(app, url)[0] for url in urls[:4]] == [200] * 4     # drawn before: not counted
    assert get(app, urls[4], address="192.0.2.2")[0] == 200        # another address
    assert get(app, urls[4])[0] == 200                             # and now it is drawn for all
    # an address that is not a picture's is counted too: it cannot be used to make work
    assert get(app, f"/map/field/estimate/{STAMP}/99/363.png", address="192.0.2.3")[0] == 404
    assert [get(app, f"/map/field/estimate/{STAMP}/9{n}/363.png", address="192.0.2.3")[0]
            for n in range(5)] == [404, 404, 404, 429, 429]


def test_the_pictures_kept_in_memory_are_bounded(tmp_path: Path) -> None:
    keep(tmp_path, shear_tile())
    fields = worldmap.Fields(layer.TileSource(tmp_path), keep=2)
    assert fields.newest(NOW) == (worldmap.Run(RUN, HOURS, tuple(st.LAYER_FLS)), None)
    for hour in HOURS:
        assert fields.cached("estimate", STAMP, hour, 320) is None
        assert fields.draw("estimate", STAMP, hour, 320, NOW) is not None
    assert fields.cached("estimate", STAMP, 6, 320) is None            # the oldest was dropped
    assert fields.cached("estimate", STAMP, 9, 320) is not None
    assert fields.cached("estimate", STAMP, 12, 320) is not None
    assert fields.draw("storm", STAMP, 6, 0, NOW) is None              # there is no storm tile
    assert fields.draw("wind", STAMP, 6, 0, NOW) is None
