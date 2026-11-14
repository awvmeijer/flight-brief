import pytest

from flight_brief.forecast import rangeget as rg

IDX = """\
1:0:d=2026111410:MXEDPRM:entire atmosphere:6 hour fcst:
2:100:d=2026111410:EDPARM:10058 m above mean sea level:6 hour fcst:
3:250:d=2026111410:EDPARM:10363 m above mean sea level:6 hour fcst:
4:400:d=2026111410:CATEDR:10058 m above mean sea level:6 hour fcst:
5:900:d=2026111410:var discipline=0 master_table=2 parmcat=19 parm=50:10058 m above mean sea level:6 hour fcst:
"""
L1, L2 = "10058 m above mean sea level", "10363 m above mean sea level"


def test_parse_idx_gives_inclusive_ends_and_open_last():
    e = rg.parse_idx(IDX)
    assert [(x.start, x.end) for x in e] == [(0, 99), (100, 249), (250, 399), (400, 899), (900, None)]
    assert e[1].var == "EDPARM" and e[1].level == L1 and e[1].forecast == "6 hour fcst"
    # A wgrib2 name with '=' and spaces, for a parameter it has no name for.
    assert e[4].var == "var discipline=0 master_table=2 parmcat=19 parm=50"


def test_select_keeps_file_order_and_fails_on_a_missing_field():
    e = rg.parse_idx(IDX)
    assert [x.number for x in rg.select(e, [("CATEDR", L1), ("EDPARM", L1)])] == [2, 4]
    with pytest.raises(LookupError, match="CATEDR"):
        rg.select(e, [("CATEDR", L2)])


def test_coalesce_merges_only_adjacent_messages():
    e = rg.parse_idx(IDX)
    assert rg.coalesce([e[1], e[2]]) == [(100, 399)]
    assert rg.coalesce([e[1], e[3]]) == [(100, 249), (400, 899)]
    assert rg.coalesce([e[3], e[4]]) == [(400, None)]


def test_fetch_messages_requests_the_index_then_one_range_per_run_of_messages():
    body = bytes(range(256)) * 4
    calls = []

    def fake_get(url, byte_range=None):
        calls.append((url, byte_range))
        if url.endswith(".idx"):
            return IDX.encode()
        start, end = byte_range
        return body[start:(end + 1) if end is not None else None]

    got = rg.fetch_messages("http://x/f.grib2", [("EDPARM", L1), ("EDPARM", L2), ("CATEDR", L1)],
                            pause=0, get=fake_get)
    assert calls == [("http://x/f.grib2.idx", None), ("http://x/f.grib2", (100, 899))]
    assert got.data == body[100:900] and got.messages == 3 and got.requests == 2


def test_http_get_retries_a_garbled_reply(monkeypatch):
    import http.client
    import io

    replies = [http.client.BadStatusLine("HTTP/1.1   0 Init"), io.BytesIO(b"ok")]

    def fake_urlopen(req, timeout):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(rg.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(rg.time, "sleep", lambda s: None)
    assert rg.http_get("http://x/f") == b"ok" and replies == []
