"""The world map: this project's turbulence estimate and the model's storm clouds, as pictures
a Mapbox globe can drape.

    GET /map                                         the page
    GET /map/field/{kind}/{run}/{hour}/{level}.png   one picture: kind estimate or storm

A picture is one forecast hour of one tile, redrawn in Web Mercator so Mapbox can stretch it
between the four corners `CORNERS`: 85.0511 degrees north to south, 180 west to 180 east. It
is an 8-bit PNG with a palette and a transparency chunk, written with the standard library.

**Estimate.** The wind-shear index of `forecast/shear_tiles.py`, one layer, put into words by
`ride.estimate_level` for the medium weight class: nothing where it says smooth or there is
no data, green where it says light, amber where it says moderate. It never says more.

**Storm clouds.** The convective cloud top of `forecast/wx_tiles.py`: white where the model's
top is at FL300 or higher, nothing elsewhere.

A grid point is drawn as the pixel that starts at its longitude, so a picture sits an eighth
of a degree east of the grid (half that for the cloud tops). Both tiles already hold the
highest value within half a degree, so that shift is inside what a cell stands for.

Wording (see `ride.py`): a strength word is always followed by its source, "(estimate)".
"""
from __future__ import annotations

import json
import math
import struct
import threading
import zipfile
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from . import page, ride
from .brief import day_hhmm
from .forecast import shear_tiles, wx_tiles
from .forecast.layer import SHEAR_LAG, TileSource

ASSETS = "1"  # bump when static/worldmap.js or static/worldmap.css changes
MERCATOR_LAT = 85.0511  # where Web Mercator's square ends
CORNERS = [[-180.0, MERCATOR_LAT], [180.0, MERCATOR_LAT],
           [180.0, -MERCATOR_LAT], [-180.0, -MERCATOR_LAT]]
WEIGHT = "medium"
STORM_MIN_FL = 300
KINDS = ("estimate", "storm")
# Pictures are as wide as their grid and square, which is what Web Mercator's world is.
ESTIMATE_SIZE = shear_tiles.NI   # 1440: one pixel per 0.25 degree at the equator
STORM_SIZE = wx_tiles.TNI        # 720: one pixel per 0.5 degree
CLEAR = (0, 0, 0, 0)
GREEN = (57, 255, 20, 112)       # --st-accent, light (estimate)
AMBER = (232, 150, 44, 184)      # --st-warn, moderate (estimate)
WHITE = (255, 255, 255, 140)     # storm clouds
ESTIMATE_PALETTE = [CLEAR, GREEN, AMBER]
STORM_PALETTE = [CLEAR, WHITE]
KEEP = 160                       # pictures kept in memory: two runs at every hour and level
RECHECK = timedelta(minutes=5)   # how long "the newest tiles" is believed before looking again
DRAWS = 4                        # pictures an address may have drawn, per brief it may ask for
NOT_SET_UP = "The map is not set up on this copy of the page: it needs a Mapbox token."
NOT_YET = "The forecast file for this map is not available yet. Try again later."
TURNED_OFF = "This copy of the page runs without forecast files, so there is no map."


# --- pictures ------------------------------------------------------------------------------

def mercator_rows(height: int, n_lat_rows: int, lat_step: float) -> np.ndarray:
    """For a picture `height` pixels tall that covers Web Mercator from 85.0511 north to
    85.0511 south, the row of a latitude grid (row 0 at 90N, `lat_step` degrees a row) nearest
    the middle of each picture row."""
    top = math.asinh(math.tan(math.radians(MERCATOR_LAT)))
    y = top * (1.0 - 2.0 * (np.arange(height) + 0.5) / height)
    lat = np.degrees(np.arctan(np.sinh(y)))
    return np.clip(np.rint((90.0 - lat) / lat_step), 0, n_lat_rows - 1).astype(np.intp)


def on_the_map(grid: np.ndarray, height: int, lat_step: float) -> np.ndarray:
    """A global grid (row 0 at 90N, column 0 at 0E) as a map picture: rows in Web Mercator,
    columns from 180 west to 180 east."""
    rows = mercator_rows(height, grid.shape[0], lat_step)
    return np.roll(grid[rows], grid.shape[1] // 2, axis=1)


def png(indices: np.ndarray, palette: list[tuple[int, int, int, int]]) -> bytes:
    """An 8-bit paletted PNG of a (height, width) array of palette indices. Each palette
    entry is (red, green, blue, alpha); the alphas go in the tRNS chunk."""
    if indices.ndim != 2 or indices.dtype != np.uint8 or 0 in indices.shape:
        raise ValueError("a picture is a two-dimensional uint8 array")
    if not 1 <= len(palette) <= 256 or int(indices.max()) >= len(palette):
        raise ValueError("an index has no palette entry")
    height, width = indices.shape
    lines = np.zeros((height, width + 1), np.uint8)  # each line starts with filter type 0
    lines[:, 1:] = indices

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", bytes(v for entry in palette for v in entry[:3]))
            + chunk(b"tRNS", bytes(entry[3] for entry in palette))
            + chunk(b"IDAT", zlib.compress(lines.tobytes(), 9))
            + chunk(b"IEND", b""))


def estimate_indices(tile: shear_tiles.ShearTile, hour_index: int, layer_index: int
                     ) -> np.ndarray:
    """0 smooth or no data, 1 light, 2 moderate: `ride.estimate_level` for every count a
    tile can hold, looked up for each grid point."""
    step = tile.meta["step"]
    words = np.array([0 if count == shear_tiles.MISSING
                      else ride.estimate_level(count * step, WEIGHT) for count in range(256)],
                     np.uint8)
    return words[tile.ti1[hour_index, layer_index]]


def estimate_png(tile: shear_tiles.ShearTile, hour_index: int, layer_index: int) -> bytes:
    """One forecast hour of one layer: clear where the estimate says smooth or there is no
    data, green where it says light, amber where it says moderate."""
    grid = estimate_indices(tile, hour_index, layer_index)
    return png(on_the_map(grid, ESTIMATE_SIZE, shear_tiles.GRID_DEG), ESTIMATE_PALETTE)


def storm_indices(wx_tile: wx_tiles.WxTile, hour_index: int, min_fl: int = STORM_MIN_FL
                  ) -> np.ndarray:
    """1 where the convective cloud top is at `min_fl` or higher, 0 elsewhere."""
    top = wx_tile.top[hour_index]
    known = (top != wx_tiles.TOP_NONE) & (top != wx_tiles.TOP_MISSING)
    high = top.astype(np.int32) * wx_tile.meta["top_step_fl"] >= min_fl
    return (known & high).astype(np.uint8)


def storm_png(wx_tile: wx_tiles.WxTile, hour_index: int, min_fl: int = STORM_MIN_FL) -> bytes:
    """One forecast hour: white where the model's storm clouds reach `min_fl` or higher."""
    grid = storm_indices(wx_tile, hour_index, min_fl)
    return png(on_the_map(grid, STORM_SIZE, wx_tiles.TOP_DEG), STORM_PALETTE)


# --- which pictures exist ------------------------------------------------------------------

@dataclass(frozen=True)
class Run:
    """What the page needs to know of one tile: its run, its forecast hours and its layers."""
    run: datetime
    hours: tuple[int, ...]
    levels: tuple[int, ...]

    @property
    def stamp(self) -> str:
        return f"{self.run:%Y%m%dT%H}"


def field_url(kind: str, stamp: str, hour: int, level: int) -> str:
    return f"/map/field/{kind}/{stamp}/{hour}/{level}.png"


class Fields:
    """The pictures the app serves. It asks the tile source which runs are the newest, at
    most once every five minutes, draws a picture when it is first asked for and keeps the
    last `keep` in memory. A run it has not itself found is never looked up, so an address
    cannot make the app fetch an old tile."""

    def __init__(self, tiles: TileSource | None, keep: int = KEEP) -> None:
        self.tiles = tiles
        self.keep = keep
        self.lock = threading.Lock()
        self._pictures: OrderedDict[tuple[str, str, int, int], bytes] = OrderedDict()
        self._runs: OrderedDict[tuple[str, str], Run] = OrderedDict()
        self._looked: datetime | None = None
        self._newest: tuple[Run | None, Run | None] = (None, None)

    def _find(self, kind: str, at: datetime):
        assert self.tiles is not None
        try:
            return (self.tiles.shear if kind == "estimate" else self.tiles.wx)(at)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return None  # a file that cannot be read is a file that is not there

    def newest(self, as_of: datetime) -> tuple[Run | None, Run | None]:
        """The newest wind-shear tile and the newest storm-cloud tile known at `as_of`."""
        if self.tiles is None:
            return None, None
        with self.lock:
            if self._looked is not None and abs(as_of - self._looked) < RECHECK:
                return self._newest
        found: list[Run | None] = []
        for kind in KINDS:
            got = self._find(kind, as_of)
            found.append(None if got is None else Run(
                got[1], tuple(got[0].forecast_hours),
                tuple(got[0].layer_fls) if kind == "estimate" else (0,)))
        with self.lock:
            self._looked, self._newest = as_of, (found[0], found[1])
            for kind, run in zip(KINDS, found, strict=True):
                if run is not None:
                    self._runs[kind, run.stamp] = run
                    self._runs.move_to_end((kind, run.stamp))
            while len(self._runs) > 8:
                self._runs.popitem(last=False)
            return self._newest

    def cached(self, kind: str, stamp: str, hour: int, level: int) -> bytes | None:
        """A picture drawn before, or None."""
        with self.lock:
            data = self._pictures.get((kind, stamp, hour, level))
            if data is not None:
                self._pictures.move_to_end((kind, stamp, hour, level))
            return data

    def draw(self, kind: str, stamp: str, hour: int, level: int, as_of: datetime
             ) -> bytes | None:
        """The picture, drawn and kept; None unless the run is one this app found, the hour
        is one of its forecast hours and the level one of its layers (0 for storm clouds)."""
        if self.tiles is None or kind not in KINDS:
            return None
        with self.lock:
            run = self._runs.get((kind, stamp))
        if run is None:
            self.newest(as_of)
            with self.lock:
                run = self._runs.get((kind, stamp))
        if run is None or hour not in run.hours or level not in run.levels:
            return None
        # The run itself is the newest one known four hours after it; its file is on disk.
        got = self._find(kind, run.run + SHEAR_LAG)
        if got is None or got[1] != run.run:
            return None
        tile = got[0]
        if kind == "estimate":
            data = estimate_png(tile, tile.forecast_hours.index(hour),
                                tile.layer_fls.index(level))
        else:
            data = storm_png(tile, tile.forecast_hours.index(hour))
        with self.lock:
            self._pictures[kind, stamp, hour, level] = data
            while len(self._pictures) > self.keep:
                self._pictures.popitem(last=False)
        return data


# --- the page ------------------------------------------------------------------------------

LEGEND = (
    ("k1", "green", f"{ride.WORDS[1]} (estimate)"),
    ("k2", "amber", f"{ride.WORDS[2]} (estimate)"),
    ("k3", "white", f"storm clouds forecast to FL{STORM_MIN_FL} or higher"),
    ("k0", "no colour", f"{ride.WORDS[0]} (estimate)"),
)


def frames(estimate: Run, storm: Run | None) -> list[dict]:
    """One entry per forecast hour of the estimate: the hour, its valid time in words, the
    estimate's picture for each layer and the storm-cloud picture valid at the same time."""
    out = []
    for hour in estimate.hours:
        valid = estimate.run + timedelta(hours=hour)
        storm_url = None
        if storm is not None:
            lead = (valid - storm.run).total_seconds() / 3600
            if lead in storm.hours:
                storm_url = field_url("storm", storm.stamp, int(lead), 0)
        out.append({"hour": hour, "label": f"{day_hhmm(valid)} {page.DOT} +{hour} h",
                    "estimate": {str(fl): field_url("estimate", estimate.stamp, hour, fl)
                                 for fl in estimate.levels},
                    "storm": storm_url})
    return out


def _about() -> str:
    e = page.e
    lines = [
        "The green and amber areas are an estimate: this project's own wind-shear index, "
        "worked out from the GFS weather model. It is not an official turbulence forecast.",
        "NOAA's official turbulence forecast, which covers the contiguous United States, is "
        "not on this map.",
        "The estimate's words are set for a medium-size airliner such as an Airbus A320 or a "
        "Boeing 737, at the cruise level chosen above the map.",
        f"The white areas are storm clouds with tops at FL{STORM_MIN_FL} "
        f"({STORM_MIN_FL * 100:,} ft) or higher in the GFS weather model. The model says where "
        "it expects deep convection, not where a storm cell will be.",
        "Nothing is worked out beyond 85 degrees north and south.",
    ]
    words = "".join(f"<li><b>{e(ride.WORDS[q])} (estimate)</b> {e(ride.MEANING[ride.WORDS[q]])}</li>"
                    for q in (1, 2))
    return ('<div class="fb-world-about">' + "".join(f"<p>{e(line)}</p>" for line in lines)
            + f'<h2 class="cap">What the words mean</h2><ul class="fb-words">{words}</ul></div>')


def _controls(shown: list[dict], start: int, estimate: Run, level: int, storm: bool) -> str:
    e = page.e
    picks = "".join(
        f'<label class="fb-world-pick"><input type="radio" name="fb-world-level" '
        f'value="{fl}"{" checked" if fl == level else ""} disabled><span>FL{fl}</span></label>'
        for fl in estimate.levels)
    storm_text = "Storm clouds" if storm else "Storm clouds: no forecast file yet"
    return (
        '<div class="fb-world-controls">'
        '<div class="fb-world-time"><label><span class="cap">Forecast time</span>'
        f'<input type="range" id="fb-world-hour" min="0" max="{len(shown) - 1}" step="1" '
        f'value="{start}" aria-valuetext="{e(shown[start]["label"])}" disabled></label>'
        f'<output id="fb-world-when" for="fb-world-hour">{e(shown[start]["label"])}</output>'
        "</div>"
        '<fieldset class="fb-world-levels"><legend class="cap">Cruise level</legend>'
        f"{picks}</fieldset>"
        '<div class="fb-world-layers"><span class="cap">Layer</span>'
        '<label class="fb-world-pick box"><input type="checkbox" id="fb-world-storm"'
        f'{" checked" if storm else ""} disabled><span>{e(storm_text)}</span></label></div>'
        "</div>")


def map_page(estimate: Run | None, storm: Run | None, mapbox: str | None, now: datetime, *,
             forecasts: bool = True) -> str:
    """The world map page. `estimate` and `storm` are the newest tiles (`Fields.newest`),
    `mapbox` the public Mapbox token, `now` the time the page opens at. Without an estimate
    tile the page says the forecast file is not there and shows no map; without a token it
    says the map is not set up, in the route map's own words."""
    e = page.e
    title = f"World map {page.DOT} {page.SITE}"
    head = ('<section class="fb-route">' + page.cap("World map")
            + "<h1>Turbulence estimate and storm clouds</h1>"
            "<p>Where this project's estimate expects bumps at cruise level, and where the "
            "weather model has storm clouds, hour by hour.</p></section>\n")
    sheet = f'<link rel="stylesheet" href="/static/worldmap.css?v={ASSETS}">\n'
    about = page.win("What this map shows", "an estimate, not an official forecast", _about())
    label = "World map of the turbulence estimate and forecast storm clouds"
    if estimate is None:
        body = page.win("World map", "no forecast file",
                        f"<p>{e(NOT_YET if forecasts else TURNED_OFF)}</p>", die="off")
        return page.layout(title, f"{sheet}{head}{body}\n{about}", noindex=True)
    foot = f"<span>Estimate: GFS run of {e(day_hhmm(estimate.run))}</span>"
    foot += (f"<span>Storm clouds: GFS run of {e(day_hhmm(storm.run))}</span>" if storm
             else "<span>Storm clouds: no forecast file yet</span>")
    legend = ('<ul class="fb-world-legend" aria-label="What the colours mean">'
              + "".join(f'<li><i class="{key}"></i>{e(colour)}: {e(text)}</li>'
                        for key, colour, text in LEGEND) + "</ul>")
    if not mapbox:
        body = page.win("World map", "map not set up",
                        f'<div class="fb-map empty" role="img" aria-label="{label}">'
                        f"<p>{e(NOT_SET_UP)}</p></div>", foot=foot, die="off", kind="flush")
        return page.layout(title, f"{sheet}{head}{body}\n{about}", noindex=True)
    shown = frames(estimate, storm)
    start = min(range(len(shown)), key=lambda i: abs(
        estimate.run + timedelta(hours=shown[i]["hour"]) - now))
    level = estimate.levels[len(estimate.levels) // 2]
    data = {"token": mapbox, "corners": CORNERS, "frames": shown, "storm": storm is not None}
    blob = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    body = page.win(
        "World map", f"{len(shown)} forecast hours",
        _controls(shown, start, estimate, level, storm is not None)
        + f'<div id="fb-world" class="fb-map fb-world" role="region" aria-label="{label}">'
        "<p>The map needs JavaScript and WebGL.</p></div>"
        '<p id="fb-world-note" class="fb-world-note" role="status"></p>'
        + legend
        + f'<script type="application/json" id="fb-world-data">{blob}</script>'
        f'<script src="/static/worldmap.js?v={ASSETS}" defer></script>',
        foot=foot, kind="flush")
    return page.layout(title, f"{sheet}{head}{body}\n{about}", mapbox=True)
