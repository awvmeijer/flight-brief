"""The page as a hosted function (Vercel): `app` is the WSGI app.

What differs from `brief-web` on a laptop:

- **Route table.** It is not in git. A hosted copy downloads the one this repository
  publishes (release `route-table`, built by the `route-table` workflow) into its temporary
  folder when it starts.
- **Kept files.** There is no disk that lasts. Fetched weather and forecast tiles go to the
  temporary folder and are fetched again by the next instance. Saved briefs, and the
  schedule's kept answers and unit counter, go to a private Vercel Blob store
  (`blobstore.py`) when `BLOB_READ_WRITE_TOKEN` is set.
- **Schedules need the store.** The unit counter is what stops look-ups before the month's
  free units are gone, and it only counts across instances in the store. So with a key but
  without the store, schedules stay off.
- The caller's address is read from X-Forwarded-For.

Settings, all from the environment: `AERODATABOX_KEY`, `MAPBOX_TOKEN`,
`BLOB_READ_WRITE_TOKEN`, and optionally `FLIGHT_BRIEF_ROUTES_URL` (another place to get the
route table from) and `FLIGHT_BRIEF_TMP` (the temporary folder, default /tmp/flight-brief).
"""
from __future__ import annotations

import gzip
import os
import shutil
import sys
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from .blobstore import BlobPath
from .forecast.layer import TileSource
from .schedule import Schedule
from .vrs import RouteTable
from .web import create_app

ROUTES_URL = "https://github.com/awvmeijer/flight-brief/releases/download/route-table/vrs.sqlite.gz"
USER_AGENT = "flight-brief/0.3 (hosted page)"
KEPT = "flight-brief"   # the folder of this app in the store


def fetch_routes(path: Path, url: str, opener: Callable[..., Any] = urllib.request.urlopen) -> Path:
    """The route table at `path`, downloaded and unpacked from `url` when it is not there."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(f".{os.getpid()}.part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with opener(request, timeout=60) as reply, gzip.open(reply) as packed, part.open("wb") as out:
        shutil.copyfileobj(packed, out, 1 << 20)
    part.replace(path)
    return path


def build(environ: dict[str, str] | None = None, *, routes: Path | None = None,
          blob: Any = None) -> Callable[..., Iterable[bytes]]:
    env = os.environ if environ is None else environ
    tmp = Path(env.get("FLIGHT_BRIEF_TMP") or "/tmp/flight-brief")  # noqa: S108
    table = RouteTable(routes or fetch_routes(tmp / "vrs.sqlite",
                                              env.get("FLIGHT_BRIEF_ROUTES_URL") or ROUTES_URL))
    stored = bool(env.get("BLOB_READ_WRITE_TOKEN", "").strip()) or blob is not None
    kept = BlobPath(KEPT, blob) if stored else None
    key = env.get("AERODATABOX_KEY", "").strip()
    schedule = Schedule(key, kept / "schedule") if key and kept is not None else None  # type: ignore[arg-type]
    mapbox = env.get("MAPBOX_TOKEN", "").strip() or None
    print(f"Flight Brief hosted: route table {table.path}; "
          f"kept files {'in the blob store' if stored else 'off (no BLOB_READ_WRITE_TOKEN)'}; "
          f"schedules {'on' if schedule else 'off'}; map {'on' if mapbox else 'off'}",
          file=sys.stderr, flush=True)
    return create_app(table, snapshots=None, cache=tmp / "cache",
                      saved=kept / "saved" if kept is not None else None,  # type: ignore[arg-type]
                      tiles=TileSource(tmp / "tiles", download=True), schedule=schedule,
                      mapbox=mapbox, behind_proxy=True)


_app: Callable[..., Iterable[bytes]] | None = None


PATH_KEY = "__path"   # vercel.json's rewrite puts the address that was asked for here


def asked(environ: dict[str, Any]) -> dict[str, Any]:
    """The request as the visitor made it. Vercel rewrites every address to this function and
    hands over the function's own address; the rewrite carries the visitor's path in the query
    string, and this puts it back."""
    pairs = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    path = next((v for k, v in pairs if k == PATH_KEY), None)
    if path is None:
        return environ
    return {**environ, "PATH_INFO": "/" + path.lstrip("/"),
            "QUERY_STRING": urlencode([(k, v) for k, v in pairs if k != PATH_KEY])}


def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
    """The WSGI app, built on the first request and kept for the instance's life."""
    global _app  # noqa: PLW0603
    if _app is None:
        _app = build()
    return _app(asked(environ), start_response)
