"""Byte-range fetch of selected GRIB2 messages, using the wgrib2-style ``.idx`` file.

NOAA publishes an index next to each GRIB2 file, one line per message::

    36:39535069:d=2026111410:EDPARM:10363 m above mean sea level:6 hour fcst:

Reading it first lets a job download a few messages instead of a 150 MB file.
"""
from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

# Generic on purpose: no name, address or repository.
USER_AGENT = "flight-brief/0.1 (scheduled job)"

NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com"
GFS_AWS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"


@dataclass(frozen=True)
class IdxEntry:
    number: int
    start: int
    end: int | None  # inclusive; None means "to the end of the file"
    var: str
    level: str
    forecast: str


def parse_idx(text: str) -> list[IdxEntry]:
    rows = [line.split(":") for line in text.splitlines() if line.strip()]
    starts = [int(r[1]) for r in rows]
    out = []
    for i, r in enumerate(rows):
        end = starts[i + 1] - 1 if i + 1 < len(rows) else None
        out.append(IdxEntry(int(r[0]), starts[i], end, r[3], r[4], r[5] if len(r) > 5 else ""))
    return out


def select(entries: Iterable[IdxEntry], wants: Iterable[tuple[str, str]]) -> list[IdxEntry]:
    """Entries whose (variable, level) text is in ``wants``, in file order.

    Raises if a wanted pair is absent, so a renamed field fails loudly.
    """
    wants = set(wants)
    hit = [e for e in entries if (e.var, e.level) in wants]
    missing = wants - {(e.var, e.level) for e in hit}
    if missing:
        raise LookupError(f"not in index: {sorted(missing)}")
    return hit


def coalesce(entries: Iterable[IdxEntry]) -> list[tuple[int, int | None]]:
    """Merge messages that sit next to each other into one byte range each."""
    ranges: list[tuple[int, int | None]] = []
    for e in sorted(entries, key=lambda e: e.start):
        if ranges and ranges[-1][1] is not None and ranges[-1][1] + 1 == e.start:
            ranges[-1] = (ranges[-1][0], e.end)
        else:
            ranges.append((e.start, e.end))
    return ranges


def http_get(url: str, byte_range: tuple[int, int | None] | None = None, *,
             timeout: float = 120, attempts: int = 3) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if byte_range:
        start, end = byte_range
        headers["Range"] = f"bytes={start}-{'' if end is None else end}"
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # A missing file will not appear by retrying; rate limits and server errors may clear.
            if e.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            # HTTPException covers a garbled reply; NOMADS has answered "HTTP/1.1 0 Init".
            if attempt == attempts - 1:
                raise
        time.sleep(5 + 10 * attempt)
    raise AssertionError("unreachable")


@dataclass
class Fetched:
    data: bytes
    messages: int
    requests: int
    seconds: float


def fetch_messages(base_url: str, wants: Iterable[tuple[str, str]], *,
                   pause: float = 0.6, get: Callable[..., bytes] = http_get) -> Fetched:
    """Download the wanted messages of ``base_url`` (the GRIB2 file, without ``.idx``).

    ``pause`` seconds are slept after every request, which keeps a job far
    below NOMADS's limit of 120 requests a minute.
    """
    t0 = time.monotonic()
    entries = select(parse_idx(get(base_url + ".idx").decode()), wants)
    time.sleep(pause)
    ranges = coalesce(entries)
    chunks = []
    for rng in ranges:
        chunks.append(get(base_url, rng))
        time.sleep(pause)
    return Fetched(b"".join(chunks), len(entries), 1 + len(ranges), time.monotonic() - t0)
