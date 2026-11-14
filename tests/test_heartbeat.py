import datetime as dt

import pytest

from flight_brief.forecast import heartbeat
from flight_brief.forecast.store import LocalStore

UTC = dt.timezone.utc


def test_nothing_written_yet_reads_as_none(tmp_path):
    assert heartbeat.read(LocalStore(tmp_path), "gtg-tiles") is None


def test_write_then_read_and_the_second_write_replaces_the_first(tmp_path):
    store = LocalStore(tmp_path)
    run = dt.datetime(2026, 11, 14, 6, tzinfo=UTC)
    heartbeat.write(store, "gtg-tiles", started=dt.datetime(2026, 11, 14, 7, 47, tzinfo=UTC),
                    finished=dt.datetime(2026, 11, 14, 7, 51, 30, tzinfo=UTC),
                    newest_run=run, saved=[run], skipped=[])
    later = dt.datetime(2026, 11, 14, 12, tzinfo=UTC)
    heartbeat.write(store, "gtg-tiles", started=dt.datetime(2026, 11, 14, 13, 47, tzinfo=UTC),
                    finished=dt.datetime(2026, 11, 14, 13, 50, tzinfo=UTC),
                    newest_run=later, saved=[later], skipped=[run])
    r = heartbeat.read(store, "gtg-tiles")
    assert r == {"format": "heartbeat/1", "job": "gtg-tiles",
                 "started_at": "2026-11-14T13:47:00Z", "finished_at": "2026-11-14T13:50:00Z",
                 "newest_run": "2026-11-14T12:00:00Z",
                 "saved": ["2026-11-14T12:00:00Z"], "skipped": ["2026-11-14T06:00:00Z"]}
    assert store.names(heartbeat.GROUP) == {"gtg-tiles.json"}


def test_ages_are_measured_from_the_finish_and_from_the_newest_run(tmp_path):
    store = LocalStore(tmp_path)
    r = heartbeat.write(store, "gtg-tiles", started=dt.datetime(2026, 11, 14, 13, 47, tzinfo=UTC),
                        finished=dt.datetime(2026, 11, 14, 14, 0, tzinfo=UTC),
                        newest_run=dt.datetime(2026, 11, 14, 12, tzinfo=UTC), saved=[], skipped=[])
    job_age, run_age = heartbeat.ages_hours(r, dt.datetime(2026, 11, 14, 20, 30, tzinfo=UTC))
    assert job_age == pytest.approx(6.5) and run_age == pytest.approx(8.5)


def test_a_store_with_no_runs_at_all_has_no_newest_run(tmp_path):
    t = dt.datetime(2026, 11, 14, 14, tzinfo=UTC)
    r = heartbeat.write(LocalStore(tmp_path), "gtg-tiles", started=t, finished=t,
                        newest_run=None, saved=[], skipped=[])
    assert r["newest_run"] is None and heartbeat.ages_hours(r, t) == (0.0, None)


def test_tiles_are_not_overwritten_but_a_heartbeat_is(tmp_path):
    store = LocalStore(tmp_path)
    store.put("g", "a", b"1")
    with pytest.raises(FileExistsError):
        store.put("g", "a", b"2")
    store.put("g", "a", b"2", overwrite=True)
    assert store.get("g", "a") == b"2"
