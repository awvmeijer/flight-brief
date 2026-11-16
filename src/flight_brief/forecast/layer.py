"""The forecast tiles as the brief reads them: newest run known at a time, from a local folder.

The scheduled jobs publish tiles as release assets of this repository. A `TileSource` keeps
a local folder in the store's layout, `<group>/<name>`, and can fill it on request from the
public release URL, which needs no key. With `download=False` it reads the folder only, which
is what a replay uses.

"Known at" is the run time plus the time NOAA takes to post the run. A tile cannot have been
read earlier than that, so a replay never uses a run from its own future.
"""
from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .tiles import Tile, tile_name

RELEASES = "https://github.com/awvmeijer/flight-brief/releases/download"
USER_AGENT = "flight-brief/0.1"
RUN_STEP_H = 6
GTG_LAG = dt.timedelta(minutes=90)   # GTG files post 53 to 88 minutes after run time
GTG_REACH_H = 18
GTG_LOOK_BACK = 3                    # runs; an older one reaches no further than now
SHEAR_LAG = dt.timedelta(hours=4)    # GFS f054 posts about 3.7 to 4 h after run time
SHEAR_REACH_H = 54
SHEAR_FIRST_H = 6
SHEAR_LOOK_BACK = 5                  # runs, about 30 h

Get = Callable[[str], bytes | None]


def default_root() -> Path:
    import os
    return Path(os.environ.get("FLIGHT_BRIEF_TILES")
                or Path.home() / ".cache" / "flight-brief" / "tiles")


def http_get(url: str, timeout: float = 60.0) -> bytes | None:
    """The file, or None when it does not exist. Other failures raise."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def runs_before(at: dt.datetime, lag: dt.timedelta, count: int) -> list[dt.datetime]:
    """The 00/06/12/18Z runs posted by `at`, newest first."""
    t = (at - lag).astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
    t -= dt.timedelta(hours=t.hour % RUN_STEP_H)
    return [t - dt.timedelta(hours=RUN_STEP_H * n) for n in range(count)]


class TileSource:
    def __init__(self, root: Path, download: bool = False, base_url: str = RELEASES,
                 get: Get | None = None):
        self.root = Path(root)
        self.download = download
        self.base_url = base_url
        self._get = get or http_get
        self._decoded: dict[str, object] = {}
        self.errors: list[str] = []  # what could not be fetched, for the brief's notes

    def url(self, group: str, name: str) -> str:
        return f"{self.base_url}/{group}/{name}"

    def _bytes(self, group: str, name: str) -> bytes | None:
        path = self.root / group / name
        if path.exists():
            return path.read_bytes()
        if not self.download:
            return None
        try:
            data = self._get(self.url(group, name))
        except (OSError, ValueError) as exc:
            self.errors.append(f"{name}: {exc}")
            return None
        if data is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.rename(path)
        return data

    def _newest(self, runs: list[dt.datetime], group_of: Callable[[dt.datetime], str],
                name_of: Callable[[dt.datetime], str], decode: Callable[[bytes], object]
                ) -> tuple[object, dt.datetime, str] | None:
        for run in runs:
            group, name = group_of(run), name_of(run)
            if name not in self._decoded:
                data = self._bytes(group, name)
                if data is None:
                    continue
                if len(self._decoded) >= 4:  # tiles are large; keep only the latest few
                    self._decoded.pop(next(iter(self._decoded)))
                self._decoded[name] = decode(data)
            return self._decoded[name], run, self.url(group, name)
        return None

    def gtg(self, as_of: dt.datetime) -> tuple[Tile, dt.datetime, str] | None:
        """The newest GTG tile known at `as_of`: (tile, run, url)."""
        found = self._newest(runs_before(as_of, GTG_LAG, GTG_LOOK_BACK),
                             lambda run: f"gtg-tiles-{run:%Y-%m}", tile_name, Tile.from_bytes)
        return found  # type: ignore[return-value]

    def shear(self, as_of: dt.datetime):
        """The newest wind-shear index tile known at `as_of`: (tile, run, url)."""
        from . import shear_tiles
        return self._newest(runs_before(as_of, SHEAR_LAG, SHEAR_LOOK_BACK),
                            lambda run: f"shear-tiles-{run:%Y-%m}", shear_tiles.tile_name,
                            shear_tiles.ShearTile.from_bytes)
