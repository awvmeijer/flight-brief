import datetime as dt

import numpy as np
import pytest

from flight_brief.forecast import grib, layer, sources
from flight_brief.forecast import shear_tiles as st
from flight_brief.forecast import wx_tiles as wx
from flight_brief.forecast.rangeget import Fetched
from flight_brief.jobs import shear_tiles as job

from test_shear_tiles import RUN, FakeFetch, _fake_fields

UTC = dt.timezone.utc


def _tile(hours=(6, 9), run=RUN):
    u, v, top = wx.empty(len(hours))
    meta = {"format": wx.FORMAT, "run": f"{run:%Y-%m-%dT%H:00Z}", "wind_step_kt": wx.WIND_STEP_KT,
            "top_step_fl": wx.TOP_STEP_FL}
    return wx.WxTile(u, v, top, list(hours), list(st.LAYER_FLS), meta)


def test_pressure_becomes_the_flight_level_an_altimeter_on_1013_would_show():
    fl = wx.pressure_to_fl(np.array([101325.0, 30000.0, 25000.0, 20000.0, 15000.0, 10000.0]))
    assert np.allclose(fl, [0, 300.6, 340.0, 386.6, 446.5, 530.8], atol=0.6)
    assert (np.diff(fl) > 0).all()      # no jump where the two formulas meet at 226 hPa


def test_wind_is_kept_at_whole_degrees_in_two_knot_steps():
    ms = np.zeros((st.NJ, st.NI))
    ms[160, 40] = 51.0          # 50N 10E: 99.1 kt
    ms[161, 40] = 99.0          # a quarter degree south: not kept
    ms[164, 44] = -200.0        # past the top count
    ms[168, 48] = np.nan
    q = wx.quantise_wind(ms)
    assert q.shape == (181, 360) and q.dtype == np.int8
    assert q[40, 10] * wx.WIND_STEP_KT == 100.0 and q[41, 11] == -127 and q[42, 12] == wx.WIND_MISSING
    assert np.count_nonzero(q) == 3


def test_cloud_tops_spread_half_a_degree_and_the_highest_one_wins():
    pa = np.full((st.NJ, st.NI), np.nan)
    pa[200, 400] = 30000.0      # 40N 100E, FL301
    pa[202, 0] = 20000.0        # 39.5N on the first column, FL387: its reach wraps west
    q = wx.quantise_top(pa).astype(int) * wx.TOP_STEP_FL
    assert q.shape == (361, 720) and wx.quantise_top(pa).dtype == np.uint8
    assert q[100, 200] == 300 and q[99, 201] == 300
    assert q[100, 202] == 0                                 # a full degree east: no storm cloud
    assert q[101, 0] == 385 and q[101, 719] == 385
    pa[200, 402] = 15000.0      # a higher top within reach of the first
    assert int(wx.quantise_top(pa)[100, 200]) * wx.TOP_STEP_FL == 445
    low = np.full((st.NJ, st.NI), np.nan)
    low[400, 800] = 101000.0    # a top near the ground is still a top
    assert wx.quantise_top(low)[200, 400] == 1


def test_round_trip_names_and_an_unknown_format():
    tile = _tile()
    tile.u[1, 2, 40, 10], tile.v[1, 2, 40, 10] = 30, -7
    tile.top[0, 100, 200] = 77
    back = wx.WxTile.from_bytes(tile.to_bytes())
    assert (back.u == tile.u).all() and (back.v == tile.v).all() and (back.top == tile.top).all()
    assert back.forecast_hours == [6, 9] and back.layer_fls == [320, 363, 415] and back.run == RUN
    assert wx.tile_name(RUN) == "gfs_global_wx_20261114T12Z.npz" and wx.run_of(wx.tile_name(RUN)) == RUN
    with pytest.raises(ValueError):
        wx.run_of(st.tile_name(RUN))
    tile.meta["format"] = "wx-tile/0"
    with pytest.raises(ValueError, match="unknown tile format"):
        wx.WxTile.from_bytes(tile.to_bytes())


def test_wind_is_read_at_the_nearest_degree_layer_and_hour():
    tile = _tile()
    tile.u[1, 2, 40, 10], tile.v[1, 2, 40, 10] = 30, -7       # 50N 10E, FL415, +9 h
    tile.u[1, 2, 40, 350], tile.v[1, 2, 40, 350] = -5, 0      # 50N 10W
    at = RUN + dt.timedelta(hours=9)
    assert tile.wind(50.3, 10.4, 400, at) == (60.0, -14.0, 415, 9)
    assert tile.wind(49.6, -9.8, 430, at + dt.timedelta(hours=1.5)) == (-10.0, 0.0, 415, 9)
    assert tile.wind(50.3, 10.4, 330, at) is None             # the other layer holds no data
    assert tile.wind(50.3, 10.4, 400, at + dt.timedelta(hours=2)) is None
    with pytest.raises(ValueError):
        tile.wind(50, 10, 400, at.replace(tzinfo=None))


def test_a_cloud_top_none_and_no_data_are_three_different_answers():
    tile = _tile()
    tile.top[0] = wx.TOP_NONE
    tile.top[0, 100, 200] = 77                                # 40N 100E, +6 h
    at = RUN + dt.timedelta(hours=6)
    assert tile.storm_top(40.1, 100.2, at) == (385, 6)
    assert tile.storm_top(41.0, 100.0, at) == (None, 6)       # the model has no storm cloud here
    assert tile.storm_top(40.1, 100.2, RUN + dt.timedelta(hours=9)) is None   # that hour has no field
    assert tile.storm_top(40.1, 100.2, RUN + dt.timedelta(hours=3)) is None   # no hour near


def test_the_job_builds_both_tiles_from_one_pass():
    def fetch(run, hour):
        fields = _fake_fields(run, hour)
        top = np.full((st.NJ, st.NI), np.nan)
        top[200, 400] = 25000.0
        fields[job.CTOP] = top
        return fields, 1000, 14, 0.5

    shear, tile, stats = job.build_tiles(RUN, hours=(6, 9), fetch=fetch)
    alone, _ = job.build_tile(RUN, hours=(6, 9), fetch=fetch)
    assert (shear.ti1 == alone.ti1).all() and stats["tops"] == 2
    f = _fake_fields(RUN, 9)
    row, col = 50 * 4, 12 * 4                                 # 40N 12E
    want_u = np.rint(0.5 * (f[("UGRD", 250)][row, col] + f[("UGRD", 200)][row, col]) * wx.KT_PER_MS / 2) * 2
    want_v = np.rint(0.5 * (f[("VGRD", 250)][row, col] + f[("VGRD", 200)][row, col]) * wx.KT_PER_MS / 2) * 2
    back = wx.WxTile.from_bytes(tile.to_bytes())
    assert back.wind(40.2, 11.8, 363, RUN + dt.timedelta(hours=9)) == (want_u, want_v, 363, 9)
    assert want_u > 60                                        # the fake jet blows
    assert back.storm_top(40.0, 100.0, RUN + dt.timedelta(hours=6)) == (340, 6)
    m = back.meta
    assert m["format"] == "wx-tile/1" and m["run"] == "2026-11-14T12:00Z" and m["hours_with_tops"] == 2
    assert m["grids"]["wind"] == {"nj": 181, "ni": 360, "step_deg": 1.0}
    assert m["grids"]["top"] == {"nj": 361, "ni": 720, "step_deg": 0.5}
    assert len(tile.to_bytes()) < 1_000_000


def test_a_run_without_the_cloud_top_still_gives_winds_and_says_no_data_for_tops():
    _, tile, stats = job.build_tiles(RUN, hours=(6,), fetch=FakeFetch())
    at = RUN + dt.timedelta(hours=6)
    assert stats["tops"] == 0 and tile.storm_top(40.0, 100.0, at) is None
    assert tile.wind(40.0, 12.0, 363, at) is not None


class _Msg:
    grid_type, ny, nx, data_date, data_time, forecast_time = "regular_ll", st.NJ, st.NI, 20261114, 1200, 6

    def __init__(self, param, type_of_level, level):
        self.param, self.type_of_level, self.level = param, type_of_level, level
        self.values = np.zeros((2, 2))


def _messages(with_top):
    out = [_Msg(sources.GFS_PARAM[v], "isobaricInhPa", lv)
           for v in sources.GFS_VARS for lv in sources.GFS_LEVELS_HPA]
    return out + ([_Msg(sources.GFS_CTOP_PARAM, "convectiveCloudTop", 0)] if with_top else [])


def test_fetch_hour_asks_for_the_cloud_top_and_goes_on_without_it_when_the_run_has_none(monkeypatch, capsys):
    asked = []

    def fetch_messages(url, wants, pause):
        asked.append(list(wants))
        if sources.GFS_CTOP_WANT in asked[-1] and not has_top:
            raise LookupError("not in index: [('PRES', 'convective cloud top level')]")
        return Fetched(b"x", len(asked[-1]), 2, 0.1)

    grid = (np.array([[90.0], [-90.0]]), np.array([[0.0, 0.25]]))
    monkeypatch.setattr(job, "fetch_messages", fetch_messages)
    monkeypatch.setattr(grib, "grid_latlon", lambda m: grid)
    monkeypatch.setattr(grib, "decode", lambda data: _messages(sources.GFS_CTOP_WANT in asked[-1]))

    has_top = True
    fields, *_ = job.fetch_hour(RUN, 6)
    assert job.CTOP in fields and len(fields) == 13 and len(asked) == 1

    has_top, asked[:] = False, []
    fields, *_ = job.fetch_hour(RUN, 6)
    assert job.CTOP not in fields and len(fields) == 12 and len(asked) == 2
    assert "no convective cloud top" in capsys.readouterr().out


def test_fetch_hour_refuses_a_message_it_did_not_ask_for(monkeypatch):
    monkeypatch.setattr(job, "fetch_messages", lambda url, wants, pause: Fetched(b"x", 1, 2, 0.1))
    monkeypatch.setattr(grib, "grid_latlon", lambda m: (np.array([[90.0], [-90.0]]), np.array([[0.0, 0.25]])))
    monkeypatch.setattr(grib, "decode", lambda data: [_Msg((0, 1, 1), "isobaricInhPa", 300)])
    with pytest.raises(ValueError, match="unexpected message"):
        job.fetch_hour(RUN, 6)


def test_the_brief_s_tile_source_finds_the_newest_wind_tile_in_its_folder(tmp_path):
    tile = _tile()
    path = tmp_path / "shear-tiles-2026-11" / wx.tile_name(RUN)
    path.parent.mkdir(parents=True)
    path.write_bytes(tile.to_bytes())
    source = layer.TileSource(tmp_path)
    found = source.wx(RUN + dt.timedelta(hours=11))            # the next run is not posted yet
    assert found is not None and found[1] == RUN and found[2].endswith(wx.tile_name(RUN))
    assert source.wx(RUN + dt.timedelta(hours=3)) is None       # this run was not posted yet
