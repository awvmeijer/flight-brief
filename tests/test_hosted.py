"""The hosted copy: kept files on a blob store, the route table fetched at start."""

import datetime as dt
import gzip
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flight_brief import hosted, vrs, web
from flight_brief.blobstore import BlobPath
from flight_brief.schedule import KEEP, Schedule

UTC = dt.timezone.utc


class NotThere(FileNotFoundError):
    pass


class FakeBlob:
    """What `vercel.blob.sync` gives, kept in a dict. Counts the calls."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, dt.datetime]] = {}
        self.calls: list[tuple[str, str]] = []
        self.now = dt.datetime(2026, 11, 19, 12, tzinfo=UTC)
        self.broken = False

    def _note(self, what: str, name: str) -> None:
        self.calls.append((what, name))
        if self.broken:
            raise RuntimeError("service down")

    def put_blob(self, pathname, body, *, access, allow_overwrite=False, content_type=None):
        self._note("put", pathname)
        assert access == "private" and allow_overwrite
        self.files[pathname] = (bytes(body), self.now)

    def get_blob(self, pathname, *, access):
        self._note("get", pathname)
        if pathname not in self.files:
            raise NotThere(pathname)
        return SimpleNamespace(body=self.files[pathname][0])

    def head_blob(self, pathname):
        self._note("head", pathname)
        if pathname not in self.files:
            raise NotThere(pathname)
        return SimpleNamespace(size=len(self.files[pathname][0]))

    def delete_blob(self, pathname):
        self._note("delete", pathname)
        self.files.pop(pathname, None)

    def query_blobs(self, *, prefix=None):
        self._note("list", prefix or "")
        return iter([SimpleNamespace(pathname=k, uploaded_at=v[1])
                     for k, v in sorted(self.files.items()) if k.startswith(prefix or "")])


def test_a_blob_path_does_what_the_app_asks_of_a_path() -> None:
    blob = FakeBlob()
    root = BlobPath("flight-brief", blob) / "saved"
    path = root / "SQ231" / "2026-11-04" / "leg1-x.json"
    assert path.pathname == "flight-brief/saved/SQ231/2026-11-04/leg1-x.json" and path.name == "leg1-x.json"
    assert not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)           # nothing to do, nothing asked
    part = path.with_suffix(".123.part")
    part.write_text('{"a": 1}', encoding="utf-8")
    assert not blob.files                                    # a part is not a file in the store
    part.replace(path)
    assert path.exists() and json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert [c for c in blob.calls if c[0] == "put"] == [("put", path.pathname)]
    with pytest.raises(FileNotFoundError):
        (root / "nothing.json").read_text()
    path.unlink(missing_ok=True)
    assert not path.exists()


def test_listing_gives_the_files_of_one_folder_with_when_they_were_written() -> None:
    blob = FakeBlob()
    root = BlobPath("fb/schedule", blob)
    for name in ("board-EHAM-2026-11-20.json", "units-2026-11.json", "deeper/x.json", "note.txt"):
        (root / name).write_text("{}")
    found = root.glob("*.json")
    assert [p.name for p in found] == ["board-EHAM-2026-11-20.json", "units-2026-11.json"]
    assert all(p.modified == blob.now for p in found)


def test_a_failing_store_is_a_failing_disk() -> None:
    blob = FakeBlob()
    path = BlobPath("fb", blob) / "x.json"
    blob.broken = True
    for act in (path.exists, path.read_text, lambda: path.write_text("{}"), path.unlink,
                lambda: path.parent.glob("*.json")):
        with pytest.raises(OSError, match="service down"):
            act()


def test_the_schedule_counts_and_keeps_its_answers_in_the_store() -> None:
    blob = FakeBlob()
    sent: list[str] = []

    def get(url, headers):
        sent.append(url)
        return 204, ""

    now = [dt.datetime(2026, 11, 19, 12, tzinfo=UTC)]
    s = Schedule("key", BlobPath("fb", blob) / "schedule", get=get, now=lambda: now[0])
    assert s.units_spent() == 0
    assert s.flight("KL791", dt.date(2026, 11, 20)) is not None and len(sent) == 1
    assert s.units_spent() == 2 and "fb/schedule/units-2026-11.json" in blob.files
    assert "fb/schedule/number-KL791-2026-11-20.json" in blob.files
    s.flight("KL791", dt.date(2026, 11, 20))
    assert len(sent) == 1 and s.units_spent() == 2            # answered from the store
    # another instance of the app sees the same count and the same kept answer
    other = Schedule("key", BlobPath("fb", blob) / "schedule", get=get, now=lambda: now[0])
    assert other.units_spent() == 2
    other.flight("KL791", dt.date(2026, 11, 20))
    assert len(sent) == 1

    # tidying reads no file that the listing shows to be young, and deletes the old ones
    blob.calls.clear()
    blob.now = now[0] = now[0] + KEEP + dt.timedelta(hours=1)
    s.flight("KL792", dt.date(2026, 11, 28))
    assert "fb/schedule/number-KL791-2026-11-20.json" not in blob.files
    assert "fb/schedule/number-KL792-2026-11-28.json" in blob.files
    assert ("get", "fb/schedule/number-KL792-2026-11-28.json") not in blob.calls[-4:]


def test_a_call_that_cannot_be_counted_is_not_sent() -> None:
    blob = FakeBlob()
    sent: list[str] = []
    s = Schedule("key", BlobPath("fb", blob) / "schedule",
                 get=lambda url, headers: (sent.append(url), (204, ""))[1],
                 now=lambda: dt.datetime(2026, 11, 19, 12, tzinfo=UTC))
    blob.broken = True
    answer = s.flight("KL791", dt.date(2026, 11, 20))
    assert not sent and getattr(answer, "reason", None) == "failed"


def test_the_route_table_is_fetched_once_and_unpacked(tmp_path: Path) -> None:
    asked: list[str] = []

    def opener(request, timeout):
        asked.append(request.full_url)
        assert "flight-brief" in request.get_header("User-agent")
        return io.BytesIO(gzip.compress(b"SQLite format 3\x00 and the rest"))

    path = hosted.fetch_routes(tmp_path / "t" / "vrs.sqlite", "https://example.invalid/vrs.sqlite.gz", opener)
    assert path.read_bytes().startswith(b"SQLite format 3") and asked == ["https://example.invalid/vrs.sqlite.gz"]
    assert hosted.fetch_routes(path, "https://example.invalid/vrs.sqlite.gz", opener) == path and len(asked) == 1
    assert list(path.parent.iterdir()) == [path]              # no part file left behind
    assert hosted.ROUTES_URL.endswith("/releases/download/route-table/vrs.sqlite.gz")


def get(app, url: str):
    out: dict = {}
    path, _, query = url.partition("?")
    body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query,
                         "HTTP_X_FORWARDED_FOR": "203.0.113.9"},
                        lambda status, headers: out.update(status=status, headers=dict(headers))))
    return out["status"], out["headers"], body.decode()


def test_schedules_stay_off_without_the_store_and_saving_is_off_too(table: vrs.RouteTable, tmp_path: Path, capsys) -> None:
    env = {"FLIGHT_BRIEF_TMP": str(tmp_path), "AERODATABOX_KEY": "k", "MAPBOX_TOKEN": "pk.x"}
    app = hosted.build(env, routes=table.path)
    said = capsys.readouterr().err
    assert "kept files off" in said and "schedules off" in said and "map on" in said
    status, _, body = get(app, "/")
    assert status.startswith("200") and 'name="dep"' in body   # the time is asked: no schedule


def test_with_the_store_schedules_are_on_and_nothing_is_written_to_the_function_s_folder(
        table: vrs.RouteTable, tmp_path: Path, capsys) -> None:
    blob = FakeBlob()
    env = {"FLIGHT_BRIEF_TMP": str(tmp_path), "AERODATABOX_KEY": "k"}
    app = hosted.build(env, routes=table.path, blob=blob)
    said = capsys.readouterr().err
    assert "kept files in the blob store" in said and "schedules on" in said and "map off" in said
    status, headers, body = get(app, "/")
    assert status.startswith("200") and "Content-Security-Policy" in headers
    assert get(app, "/static/fb.css")[0].startswith("200")
    assert get(app, "/airports?q=amst")[0].startswith("200")


def test_the_entry_point_gives_the_same_app(table: vrs.RouteTable, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hosted, "_app", None)
    monkeypatch.setattr(hosted, "build", lambda: web.create_app(
        table, snapshots=None, cache=tmp_path / "c", saved=None, behind_proxy=True))
    sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
    try:
        import index
    finally:
        sys.path.pop(0)
    assert index.app is hosted.app
    assert get(index.app, "/nope")[0].startswith("404")
    assert get(index.app, "/")[0].startswith("200") and hosted._app is not None
    settings = json.loads((Path(__file__).parent.parent / "vercel.json").read_text())
    assert settings["rewrites"] == [{"source": "/(.*)", "destination": "/api/index?__path=/$1"}]
    assert "api/index.py" in settings["functions"]


def test_a_brief_is_saved_to_the_store_and_opens_again_from_it(table: vrs.RouteTable, tmp_path: Path) -> None:
    from brief_helpers import SQ231, brief
    from flight_brief.service import BriefRequest
    blob = FakeBlob()
    root = BlobPath("fb", blob) / "saved"
    b = brief(table, *SQ231)
    req = BriefRequest(code="SQ231", day="2026-11-04", leg=1, dep="07:40")
    name = web.save(root, b, req)
    assert web.save(root, b, req) == name                      # written once
    assert [c for c in blob.calls if c[0] == "put"] == [("put", f"fb/saved/SQ231/2026-11-04/leg1-{name}.json")]
    kept = json.loads(web.saved_path(root, "SQ231", "2026-11-04", 1, name).read_text())
    assert kept["request"]["dep"] == "07:40" and kept["brief"]["identity"]["code"] == "SQ231"


def test_the_visitor_s_address_is_put_back_after_vercel_s_rewrite() -> None:
    env = {"PATH_INFO": "/api/index", "QUERY_STRING": "dep=10%3A45&aircraft=Boeing+777&__path=/f/KL791/2026-11-20/1"}
    back = hosted.asked(env)
    assert back["PATH_INFO"] == "/f/KL791/2026-11-20/1"
    assert back["QUERY_STRING"] == "dep=10%3A45&aircraft=Boeing+777"
    assert hosted.asked({"PATH_INFO": "/api/index", "QUERY_STRING": "__path=/"})["PATH_INFO"] == "/"
    assert hosted.asked({"PATH_INFO": "/api/index", "QUERY_STRING": "__path="})["PATH_INFO"] == "/"
    plain = {"PATH_INFO": "/map", "QUERY_STRING": "q=1"}
    assert hosted.asked(plain) is plain                      # a laptop's request is left alone


def test_a_rewritten_request_reaches_its_page(table: vrs.RouteTable, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hosted, "_app", web.create_app(
        table, snapshots=None, cache=tmp_path / "c", saved=None, behind_proxy=True))
    assert get(hosted.app, "/api/index?__path=/")[0].startswith("200")
    status, _, body = get(hosted.app, "/api/index?q=amst&__path=/airports")
    assert status.startswith("200") and "Amsterdam" in body
    assert get(hosted.app, "/api/index?__path=/nope")[0].startswith("404")
