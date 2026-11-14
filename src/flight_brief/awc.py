"""NOAA Aviation Weather Center Data API: the record handling, pointed at `sigmet`.

Adapted from oceanic-flight-watch (`src/oceanic_watch/sources/awc.py` at commit 962e41a, MIT,
Copyright (c) 2026 Anthony Meijer): `stamp`, `describe` and the record cleaning are the same.
That repo's `Polite` client, which paces and sleeps, is left out on purpose: the request path
makes one plain short-timeout call per source, and the brief engine itself reads snapshots.

Their rules (https://aviationweather.gov/data/api/): no key, at most 100 requests a minute, a
custom User-Agent, about 30 days of history through the `date` parameter. `sigmet` returns
international and US domestic SIGMETs in one call; `isigmet` and `airsigmet` are deprecated.

`python -m flight_brief.awc gairmet --at 2026-11-14T03:00 --out <snapshots>` saves the five
G-AIRMET high-level turbulence snapshots known at a time, in the snapshot store's layout.
"""

import argparse
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .core import snapshots
from .core.snapshots import Envelope

BASE = "https://aviationweather.gov/api/data"
USER_AGENT = "flight-brief/0.1"
LICENCE = "NOAA Aviation Weather Center, US Government work (public domain)"
GAIRMET_HOURS = (0, 3, 6, 9, 12)

Record = dict[str, Any]


def stamp(at: datetime) -> str:
    """The `date` parameter format the API accepts, in UTC."""
    return at.astimezone(UTC).strftime("%Y%m%d_%H%M")


def describe(endpoint: str, params: dict[str, str]) -> str:
    """A readable request line for logs and snapshot envelopes. This API takes no key."""
    return f"{BASE}/{endpoint}?{urlencode(params)}"


def clean(data: Any, endpoint: str) -> list[Record]:
    """The records of a JSON reply; the API sometimes includes empty {} records."""
    if not isinstance(data, list):
        raise ValueError(f"{endpoint} returned {type(data).__name__}, expected a list")
    return [r for r in data if r]


def get_records(endpoint: str, params: dict[str, str], timeout: float = 10.0) -> list[Record]:
    """One plain call with a short timeout. An empty body means no data."""
    url = describe(endpoint, params | {"format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
        body = resp.read()
    return clean(json.loads(body), endpoint) if body.strip() else []


def save_gairmets(root: Path, at: datetime) -> list[Path]:
    """Save the high-level turbulence G-AIRMET snapshots known at `at`, one envelope each."""
    saved = []
    for hour in GAIRMET_HOURS:
        params = {"hazard": "turb-hi", "fore": str(hour), "date": stamp(at)}
        records = get_records("gairmet", params)
        env = Envelope(source="gairmet", origin="backfill", as_of=at, tag=f"f{hour:02d}",
                       request=describe("gairmet", params),
                       status="ok" if records else "empty", count=len(records), payload=records)
        path = snapshots.write(root, env)
        if path:
            saved.append(path)
    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Save AWC records as snapshots.")
    parser.add_argument("feed", choices=["gairmet"])
    parser.add_argument("--at", required=True, help="UTC time, e.g. 2026-11-14T03:00")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    at = datetime.fromisoformat(args.at).replace(tzinfo=UTC)
    for path in save_gairmets(args.out, at):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
