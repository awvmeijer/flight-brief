"""Heartbeats: each scheduled job records when it last finished and what it has.

The design's "source stale" state reads this record instead of guessing from
file dates. A job writes it only after a start that ended well, so a job that
keeps failing shows up as an old heartbeat. One small JSON file per job, in a
fixed place in the store, overwritten every time.
"""
from __future__ import annotations

import datetime as dt
import json

GROUP = "heartbeats"
FORMAT = "heartbeat/1"
_TIME = "%Y-%m-%dT%H:%M:%SZ"


def _stamp(t: dt.datetime | None) -> str | None:
    return None if t is None else t.astimezone(dt.timezone.utc).strftime(_TIME)


def _parse(s: str | None) -> dt.datetime | None:
    return None if s is None else dt.datetime.strptime(s, _TIME).replace(tzinfo=dt.timezone.utc)


def write(store, job: str, *, started: dt.datetime, finished: dt.datetime,
          newest_run: dt.datetime | None, saved: list[dt.datetime], skipped: list[dt.datetime]) -> dict:
    """Record a start of ``job`` that ended well.

    ``newest_run`` is the newest forecast run the store holds after this start,
    whether saved now or earlier: the page compares it with the clock.
    """
    record = {
        "format": FORMAT,
        "job": job,
        "started_at": _stamp(started),
        "finished_at": _stamp(finished),
        "newest_run": _stamp(newest_run),
        "saved": [_stamp(r) for r in saved],
        "skipped": [_stamp(r) for r in skipped],
    }
    store.put(GROUP, f"{job}.json", json.dumps(record, indent=1).encode(), overwrite=True)
    return record


def read(store, job: str) -> dict | None:
    """The last record of ``job``, or None if it has never finished a start."""
    try:
        record = json.loads(store.get(GROUP, f"{job}.json"))
    except FileNotFoundError:
        return None
    if record.get("format") != FORMAT:
        raise ValueError(f"unknown heartbeat format {record.get('format')!r}")
    return record


def ages_hours(record: dict, now: dt.datetime) -> tuple[float, float | None]:
    """(hours since the job last finished, hours since its newest forecast run was issued)."""
    job_age = (now - _parse(record["finished_at"])).total_seconds() / 3600
    newest = _parse(record["newest_run"])
    return job_age, None if newest is None else (now - newest).total_seconds() / 3600
