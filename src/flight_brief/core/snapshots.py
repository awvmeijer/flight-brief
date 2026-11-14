"""The snapshot store: every fetch is saved as a gzip JSON envelope, keyed by source and time.

Layout: <root>/<source>/<YYYY-MM-DD>/<HHMM>Z[_<tag>].json.gz, where the time is `as_of`, the
moment the data describes. Backfill and the live collector write the same layout, so replay
reads one store. A file that exists is never rewritten, which makes every run resumable.

Copied from oceanic-flight-watch (`src/oceanic_watch/snapshots.py` at commit 962e41a, MIT,
Copyright (c) 2026 Anthony Meijer). Changed: `strip_secrets` uses the standard library
instead of httpx, and `latest` can accept an empty reply (for feeds where empty is an answer).
"""

import gzip
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

SECRET_PARAMS = ("apikey", "token", "key")
Origin = Literal["backfill", "live"]


class Envelope(BaseModel):
    source: str
    origin: Origin
    as_of: datetime
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request: str
    status: Literal["ok", "empty", "error", "skipped"]
    tag: str | None = None  # distinguishes several envelopes at one time, e.g. an airport
    error: str | None = None
    count: int = 0
    payload: Any = None


def strip_secrets(url: str) -> str:
    """The request URL with any credential query parameter removed, safe to store and log."""
    if "://" not in url:
        return url  # not a URL, e.g. the "watch snapshot" label on cycle records
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in SECRET_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def path_for(root: Path, source: str, as_of: datetime, tag: str | None = None) -> Path:
    as_of = as_of.astimezone(UTC)
    name = as_of.strftime("%H%MZ") + (f"_{tag}" if tag else "")
    return root / source / as_of.strftime("%Y-%m-%d") / f"{name}.json.gz"


def write(root: Path, env: Envelope) -> Path | None:
    """Save the envelope. Returns None, and writes nothing, when that slot is already filled."""
    path = path_for(root, env.source, env.as_of, env.tag)
    if path.exists():
        return None
    env.request = strip_secrets(env.request)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(env.model_dump_json().encode()))
    tmp.rename(path)  # never leave a half-written file that would be skipped forever
    return path


def read(path: Path) -> Envelope:
    env = Envelope.model_validate_json(gzip.decompress(path.read_bytes()))
    if env.tag is None and "_" in path.name:  # files from before the tag field existed
        env.tag = path.name.removesuffix(".json.gz").split("_", 1)[1]
    return env


def envelopes(root: Path, source: str) -> Iterator[Envelope]:
    """Every saved envelope for a source, in time order."""
    for path in sorted((root / source).glob("*/*.json.gz")):
        yield read(path)


def latest(
    root: Path, source: str, at: datetime, tag: str | None = None,
    max_age: timedelta = timedelta(hours=3), statuses: tuple[str, ...] = ("ok",),
) -> Envelope | None:
    """The newest successful envelope at or before `at`, no older than `max_age`: replay's
    "as known at T". None means the feed was silent, which callers must report, not hide."""
    days = {(at - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(max_age.days + 2)}
    paths = sorted(p for d in days for p in (root / source / d).glob("*.json.gz"))
    for path in reversed(paths):
        env = read(path)
        if (tag is None or env.tag == tag) and env.as_of <= at and env.status in statuses:
            return env if at - env.as_of <= max_age else None
    return None


def between(root: Path, source: str, start: datetime, end: datetime) -> list[Envelope]:
    """Successful envelopes with start < as_of <= end, oldest first."""
    days = {(start + timedelta(days=n)).strftime("%Y-%m-%d") for n in range((end - start).days + 2)}
    paths = sorted(p for d in days for p in (root / source / d).glob("*.json.gz"))
    return [e for e in map(read, paths) if start < e.as_of <= end and e.status == "ok"]
