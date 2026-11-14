import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flight_brief.core import snapshots
from flight_brief.core.snapshots import Envelope

AT = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def env(**kw: object) -> Envelope:
    base: dict[str, object] = dict(source="taf", origin="backfill", as_of=AT,
                                   request="https://x.test/taf?ids=PGUM", status="ok")
    return Envelope.model_validate(base | kw)


def test_strip_secrets_removes_keys_and_keeps_the_rest() -> None:
    url = "https://api.tomorrow.io/v4/weather/forecast?location=1,2&apikey=SECRET&units=metric"
    stripped = snapshots.strip_secrets(url)
    assert "SECRET" not in stripped and "apikey" not in stripped
    assert "location=1%2C2" in stripped or "location=1,2" in stripped
    assert "units=metric" in stripped


def test_layout_is_source_day_time_and_tag(tmp_path: Path) -> None:
    path = snapshots.path_for(tmp_path, "pirep", AT, "wpac")
    assert path == tmp_path / "pirep" / "2026-09-20" / "0600Z_wpac.json.gz"


def test_write_never_overwrites_and_never_stores_a_key(tmp_path: Path) -> None:
    first = snapshots.write(tmp_path, env(request="https://x.test/f?apikey=SECRET", count=1))
    assert first is not None
    assert snapshots.write(tmp_path, env(count=99)) is None
    saved = snapshots.read(first)
    assert saved.count == 1
    assert b"SECRET" not in gzip.decompress(first.read_bytes())


def test_envelopes_come_back_in_time_order(tmp_path: Path) -> None:
    for hour in (9, 7, 8):
        snapshots.write(tmp_path, env(as_of=AT.replace(hour=hour)))
    assert [e.as_of.hour for e in snapshots.envelopes(tmp_path, "taf")] == [7, 8, 9]


def test_read_recovers_the_tag_from_the_file_name(tmp_path: Path) -> None:
    path = snapshots.write(tmp_path, env(source="pirep", tag="wpac"))
    assert path is not None
    legacy = snapshots.read(path).model_copy(update={"tag": None})
    path.write_bytes(gzip.compress(legacy.model_dump_json().encode()))
    assert snapshots.read(path).tag == "wpac"


def test_latest_is_as_known_at_and_silence_is_none(tmp_path: Path) -> None:
    for hour, status in ((5, "ok"), (6, "error"), (8, "ok")):
        snapshots.write(tmp_path, env(as_of=AT.replace(hour=hour), status=status))
    found = snapshots.latest(tmp_path, "taf", AT.replace(hour=7))
    assert found is not None and found.as_of.hour == 5  # skips the error, ignores the future
    assert snapshots.latest(tmp_path, "taf", AT.replace(hour=10)) is not None
    assert snapshots.latest(tmp_path, "taf", AT.replace(hour=12)) is None  # 4 h old: stale
    assert snapshots.latest(tmp_path, "taf", AT.replace(hour=12), max_age=timedelta(hours=5))
    assert snapshots.latest(tmp_path, "metar", AT) is None
