import datetime as dt

import numpy as np
import pytest

from flight_brief.forecast import ellrod, heartbeat
from flight_brief.forecast import shear_tiles as st
from flight_brief.forecast.store import LocalStore
from flight_brief.jobs import shear_tiles as job

UTC = dt.timezone.utc
RUN = dt.datetime(2026, 11, 14, 12, tzinfo=UTC)


def _tile(hours=(6, 9, 12), run=RUN):
    ti1 = np.full((len(hours), 3, st.NJ, st.NI), st.MISSING, np.uint8)
    meta = {"format": st.FORMAT, "step": st.STEP, "run": f"{run:%Y-%m-%dT%H:00Z}"}
    return st.ShearTile(ti1, list(hours), list(st.LAYER_FLS), meta)


def _recon_nb(field, lat, lon):
    """The recon's ``_nb`` value, copied from docs/recon/fallback_skill.py line for line."""
    nj, ni = field.shape
    iy = int(round((90 - lat) / 0.25)); ix = int(round((lon % 360) / 0.25)) % ni  # noqa: E702
    ys = slice(max(0, iy - 2), min(nj, iy + 3)); xs = [(ix + q) % ni for q in range(-2, 3)]  # noqa: E702
    return float(np.nanmax(field[ys][:, xs]))


# --- encoding ---------------------------------------------------------------------------------

def test_quantise_rounds_down_so_the_flag_thresholds_are_exact():
    v = np.array([0.0, 0.49, 7.99, 8.0, 8.49, 11.999, 12.0, 126.9, np.nan])
    q = st.quantise(v)
    back = st.dequantise(q)
    assert q.dtype == np.uint8 and q[-1] == st.MISSING and np.isnan(back[-1])
    assert back[:-1].tolist() == [0.0, 0.0, 7.5, 8.0, 8.0, 11.5, 12.0, 126.5]
    for threshold in (8, 12):
        assert ((back[:-1] >= threshold) == (v[:-1] >= threshold)).all()
    assert np.all((v[:-1] - back[:-1] >= 0) & (v[:-1] - back[:-1] < st.STEP))


def test_values_past_the_top_count_clip_and_are_not_marked_missing():
    q = st.quantise(np.array([127.0, 127.4, 300.0, 1e6, -0.1]))
    assert q.tolist() == [254, 254, 254, 254, 0]
    assert st.dequantise(q)[:4].tolist() == [127.0] * 4


def test_round_trip_through_a_store_keeps_everything(tmp_path):
    t = _tile()
    t.ti1[1, 2, 100, 200] = 40
    store = LocalStore(tmp_path)
    store.put("g", st.tile_name(RUN), t.to_bytes())
    back = st.ShearTile.from_bytes(store.get("g", st.tile_name(RUN)))
    assert back.forecast_hours == [6, 9, 12] and back.layer_fls == [320, 363, 415]
    assert back.meta == t.meta and back.run == RUN
    assert back.ti1.dtype == np.uint8 and np.array_equal(back.ti1, t.ti1)


def test_unknown_format_is_refused():
    t = _tile()
    t.meta["format"] = "something-else/9"
    with pytest.raises(ValueError, match="unknown tile format"):
        st.ShearTile.from_bytes(t.to_bytes())


def test_tile_names_and_back():
    assert st.tile_name(RUN) == "gfs_global_ti1_0p25_20261114T12Z.npz"
    assert st.run_of(st.tile_name(RUN)) == RUN
    for bad in ("gtg_conus_edr_12km_20261114T12Z.npz", "shear-tiles.json", "gfs_global_ti1_0p25_2026Z.npz"):
        with pytest.raises(ValueError):
            st.run_of(bad)


def test_layers_and_hours_are_the_design_s():
    assert st.FORECAST_HOURS == tuple(range(6, 55, 3)) and len(st.FORECAST_HOURS) == 17
    assert st.LAYER_FLS == tuple(fl for _, fl in ellrod.LAYERS) == (320, 363, 415)


# --- neighbourhood ----------------------------------------------------------------------------

def test_neighbourhood_max_equals_brute_force_including_the_longitude_wrap():
    rng = np.random.default_rng(7)
    v = rng.gamma(2.0, 3.0, size=(13, 16))
    v[0] = np.nan            # a row with no data, like the polar rows
    v[3:10, 3:11] = np.nan   # a gap wide enough that its middle has no data within reach
    got = st.neighbourhood_max(v)
    assert np.isnan(got[6, 6]) and not np.isnan(got[0]).any()   # row 0 borrows from rows 1 and 2
    nj, ni = v.shape
    for j in range(nj):
        for i in range(ni):
            window = [v[jj, (i + q) % ni] for jj in range(max(0, j - 2), min(nj, j + 3)) for q in range(-2, 3)]
            want = np.nan if np.all(np.isnan(window)) else np.nanmax(window)
            assert (np.isnan(got[j, i]) and np.isnan(want)) or got[j, i] == want, (j, i)


def test_a_neighbourhood_with_no_data_stays_empty_and_one_value_reaches_two_points_each_way():
    v = np.full((11, 12), np.nan)
    v[5, 0] = 9.0            # first column: its neighbourhood crosses the wrap
    got = st.neighbourhood_max(v)
    reached = np.argwhere(got == 9.0)
    assert sorted(set(reached[:, 0])) == [3, 4, 5, 6, 7]
    assert sorted(set(reached[:, 1])) == [0, 1, 2, 10, 11]
    assert len(reached) == 25 and np.isnan(got).sum() == v.size - 25


def test_tile_cells_equal_the_recon_s_neighbourhood_value_on_the_real_grid():
    rng = np.random.default_rng(11)
    field = rng.gamma(2.0, 3.0, size=(st.NJ, st.NI))
    field[np.abs(np.linspace(90, -90, st.NJ)) > 85] = np.nan
    nb = st.neighbourhood_max(field)
    points = [(51.47, -0.46), (50.0, -30.0), (-17.76, 179.9), (0.1, 359.95), (84.9, 10.0),
              (-84.9, -179.99), (35.55, 139.78), (12.345, 0.0), (40.0, 0.374), (40.0, 0.376)]
    for lat, lon in points:
        row, col = st.grid_index(lat, lon)
        assert nb[row, col] == _recon_nb(field, lat, lon), (lat, lon)


def _tile_with_one_value(lat, lon, value=20.0):
    field = np.zeros((st.NJ, st.NI))
    field[np.abs(np.linspace(90, -90, st.NJ)) > 85] = np.nan
    field[st.grid_index(lat, lon)] = value
    t = _tile(hours=(6,))
    for layer in range(3):
        t.ti1[0, layer] = st.quantise(st.neighbourhood_max(field))
    return t, field


def test_within_half_a_degree_means_two_grid_points_either_side_as_in_the_recon():
    # A value of 20 on the grid point 50N 10E, everything else 0. Every result is also checked
    # against the recon's own indexing.
    t, field = _tile_with_one_value(50.0, 10.0)
    at = RUN + dt.timedelta(hours=6)

    def value(lat, lon):
        got = t.sample(lat, lon, 360, at)
        assert got[0] == _recon_nb(field, lat, lon)
        return got[0]

    # From a position ON a grid point the reach is exact: two points (0.5 degree) either side
    # along each axis, both ends included; the third point (0.75 degree) is outside.
    assert value(50.0, 10.0) == 20.0
    assert value(50.25, 9.75) == 20.0
    assert value(50.5, 10.0) == value(49.5, 10.0) == value(50.0, 10.5) == value(50.0, 9.5) == 20.0
    assert value(50.5, 10.5) == value(49.5, 9.5) == 20.0     # corners of the square
    assert value(50.75, 10.0) == value(49.25, 10.0) == value(50.0, 10.75) == value(50.0, 9.25) == 0.0
    assert value(50.5, 10.75) == 0.0                         # inside on one axis is not enough


def test_a_value_0_4_degree_away_is_found_and_0_6_only_until_the_position_snaps_a_row_further():
    # A position between grid points goes to its nearest grid point first, as the recon's did,
    # so the reach from the position itself is 0.5 degree give or take half a grid step:
    # anything up to 0.375 degree away along an axis is always found, anything more than 0.625
    # never, and in between it depends on which grid point the position snaps to.
    t, field = _tile_with_one_value(50.0, 10.0)
    at = RUN + dt.timedelta(hours=6)

    def found(lat, lon):
        got = t.sample(lat, lon, 360, at)[0]
        assert got == _recon_nb(field, lat, lon)
        return got == 20.0

    assert found(50.4, 10.0) and found(49.6, 10.0) and found(50.0, 10.4) and found(50.0, 9.6)
    assert found(50.4, 9.6)
    # 0.6 degree away: the position snaps to the grid point 0.5 degree from the value, so the
    # recon's method still finds it ...
    assert found(50.6, 10.0) and found(50.0, 10.6)
    # ... and stops finding it once the position snaps to the point 0.75 degree from the value.
    assert not found(50.63, 10.0) and not found(49.37, 10.0)
    assert not found(50.0, 10.63) and not found(50.0, 9.37)
    assert not found(50.7, 10.0) and not found(50.4, 10.7)


def test_the_neighbourhood_wraps_at_the_first_and_last_column_when_sampling():
    t, _ = _tile_with_one_value(10.0, 359.75)      # the last column
    at = RUN + dt.timedelta(hours=6)
    assert t.sample(10.0, 0.25, 360, at)[0] == 20.0     # two columns east, across the wrap
    assert t.sample(10.0, -0.25, 360, at)[0] == 20.0    # the same column, written as a west longitude
    assert t.sample(10.0, 0.5, 360, at)[0] == 0.0


# --- sample -----------------------------------------------------------------------------------

def test_sample_takes_the_nearest_grid_point_and_any_longitude_convention():
    t = _tile()
    row, col = st.grid_index(40.0, -100.0)
    assert (row, col) == (200, 1040)
    t.ti1[0, 1, row, col] = 24
    at = RUN + dt.timedelta(hours=6)
    for lon in (-100.0, 260.0, -100.1, 260.12):
        assert t.sample(40.05, lon, 360, at) == (12.0, 363, 6)
    assert t.sample(40.0, -100.2, 360, at) is None          # the next column holds no data
    t.ti1[0, 1, 300, 0] = 16
    for lon in (0.0, 360.0, 359.9, -0.1, 0.12):
        assert t.sample(15.0, lon, 360, at) == (8.0, 363, 6)
    t.ti1[0, 1, 300, 720] = 18
    assert t.sample(15.0, 180.0, 360, at) == t.sample(15.0, -180.0, 360, at) == (9.0, 363, 6)


def test_sample_picks_the_layer_nearest_the_flight_level():
    t = _tile()
    for layer, count in enumerate((10, 20, 30)):
        t.ti1[0, layer, 200, 400] = count
    at = RUN + dt.timedelta(hours=6)
    lat, lon = 40.0, 100.0
    assert t.sample(lat, lon, 290, at) == (5.0, 320, 6)
    assert t.sample(lat, lon, 341, at) == (5.0, 320, 6)
    assert t.sample(lat, lon, 342, at) == (10.0, 363, 6)
    assert t.sample(lat, lon, 389, at) == (10.0, 363, 6)     # 389 is an exact tie: the lower layer
    assert t.sample(lat, lon, 390, at) == (15.0, 415, 6)
    assert t.sample(lat, lon, 440, at) == (15.0, 415, 6)


def test_sample_picks_the_nearest_forecast_hour_within_an_hour_and_a_half():
    t = _tile(hours=(6, 9, 12))
    for h, count in enumerate((10, 20, 30)):
        t.ti1[h, :, 200, 400] = count

    def hour(lead_h):
        got = t.sample(40.0, 100.0, 360, RUN + dt.timedelta(hours=lead_h))
        return None if got is None else got[2]

    assert [hour(x) for x in (4.5, 6, 7.4, 7.6, 9, 10.4, 12, 13.5)] == [6, 6, 6, 9, 9, 9, 12, 12]
    assert hour(7.5) == 6                       # equally near two hours: the earlier
    assert hour(4.49) is None and hour(13.51) is None and hour(0) is None and hour(60) is None
    assert t.sample(40.0, 100.0, 360, RUN + dt.timedelta(hours=9)) == (10.0, 363, 9)
    # A rehearsal tile with a gap between its hours has no value inside the gap.
    gap = _tile(hours=(6, 30))
    gap.ti1[:, :, 200, 400] = 10
    assert gap.sample(40.0, 100.0, 360, RUN + dt.timedelta(hours=18)) is None
    # Another time zone is the same instant.
    east = dt.timezone(dt.timedelta(hours=8))
    assert t.sample(40.0, 100.0, 360, (RUN + dt.timedelta(hours=12)).astimezone(east))[2] == 12
    with pytest.raises(ValueError, match="time zone"):
        t.sample(40.0, 100.0, 360, dt.datetime(2026, 11, 14, 18))


def test_sample_is_none_poleward_of_85_and_where_there_is_no_data():
    t = _tile()
    t.ti1[:] = 10
    at = RUN + dt.timedelta(hours=6)
    assert t.sample(85.0, 0.0, 360, at) == (5.0, 363, 6) and t.sample(-85.0, 0.0, 360, at) is not None
    assert t.sample(85.01, 0.0, 360, at) is None and t.sample(-86.0, 50.0, 360, at) is None
    assert t.sample(90.0, 0.0, 360, at) is None and t.sample(float("nan"), 0.0, 360, at) is None
    t.ti1[0, 1, 200, 400] = st.MISSING
    assert t.sample(40.0, 100.0, 360, at) is None


# --- the job ----------------------------------------------------------------------------------

def _fake_fields(run, hour):
    """Twelve small-number fields with a jet whose position depends on the run and hour."""
    lat = np.linspace(90, -90, st.NJ)[:, None]
    lon = np.arange(st.NI)[None, :] * 0.25
    fields = {}
    for k, level in enumerate((300, 250, 200, 150)):
        jet = (40 + 25 * k) * np.exp(-((lat - 40 - hour / 6) / 3) ** 2) * (1 + 0.3 * np.sin(np.deg2rad(3 * lon + run.hour)))
        fields[("UGRD", level)] = jet
        fields[("VGRD", level)] = 5 * np.cos(np.deg2rad(lon * 4)) * np.cos(np.deg2rad(lat)) * (1 + k)
        fields[("HGT", level)] = np.full((st.NJ, st.NI), 9000.0 + 1500.0 * k)
    return fields


class FakeFetch:
    def __init__(self, missing=()):
        self.calls, self.missing = [], set(missing)

    def __call__(self, run, hour):
        self.calls.append((run, hour))
        if run in self.missing:
            import urllib.error
            raise urllib.error.HTTPError("https://example.invalid/f", 404, "Not Found", None, None)
        return _fake_fields(run, hour), 1000, 13, 0.5


NOW = dt.datetime(2026, 11, 14, 16, 37, tzinfo=UTC)


def test_candidate_runs_are_the_synoptic_hours_of_two_days_oldest_first():
    runs = job.candidate_runs(NOW)
    assert runs[0] == dt.datetime(2026, 11, 12, 18, tzinfo=UTC) and runs[-1] == RUN
    assert len(runs) == 8 and all(r.hour in (0, 6, 12, 18) for r in runs) and runs == sorted(runs)
    assert job.group_of(runs[0]) == "shear-tiles-2026-11" and job.JOB == "shear-tiles"
    assert job.group_of(dt.datetime(2026, 12, 1, 0, tzinfo=UTC)) == "shear-tiles-2026-12"


def test_build_tile_holds_the_neighbourhood_maximum_of_the_index_rounded_down():
    fetch = FakeFetch()
    tile, stats = job.build_tile(RUN, hours=(6, 9), fetch=fetch)
    assert fetch.calls == [(RUN, 6), (RUN, 9)]
    assert tile.ti1.shape == (2, 3, 721, 1440) and tile.forecast_hours == [6, 9]
    assert stats["bytes"] == 2000 and stats["requests"] == 26
    m = tile.meta
    assert m["format"] == "shear-tile/1" and m["run"] == "2026-11-14T12:00Z" and m["units"] == "1e-7 s^-2"
    assert m["step"] == st.STEP and m["missing"] == 255 and m["neighbourhood"]["reach_deg"] == 0.5
    assert m["grid"]["ni"] == 1440 and m["grid"]["nj"] == 721 and m["grid"]["step_deg"] == 0.25
    assert [x["flight_level"] for x in m["layers"]] == [320, 363, 415]
    # One layer of one hour against the index computed here and the recon's neighbourhood.
    f = _fake_fields(RUN, 9)
    direct = 1e7 * ellrod.ti1(f[("UGRD", 250)], f[("VGRD", 250)], f[("HGT", 250)],
                              f[("UGRD", 200)], f[("VGRD", 200)], f[("HGT", 200)])
    assert np.nanmax(direct) > 8      # the fake jet is strong enough to matter
    back = st.ShearTile.from_bytes(tile.to_bytes())
    for lat, lon in ((41.3, 12.2), (47.0, -0.3), (33.1, 359.9), (-20.0, 100.0), (84.8, 5.0)):
        want = np.floor(_recon_nb(direct, lat, lon) / st.STEP) * st.STEP
        assert back.sample(lat, lon, 363, RUN + dt.timedelta(hours=9)) == (want, 363, 9)
    assert (tile.ti1[:, :, :18] == st.MISSING).all() and (tile.ti1[:, :, 20:-20] != st.MISSING).all()


def test_the_job_saves_ready_runs_oldest_first_skips_stored_ones_and_writes_a_heartbeat(tmp_path):
    store = LocalStore(tmp_path)
    runs = job.candidate_runs(NOW)
    store.put(job.group_of(runs[1]), st.tile_name(runs[1]), b"already here")
    not_ready = {runs[-1]}
    asked = []

    def ready(run):
        asked.append(run)
        return run not in not_ready

    fetch = FakeFetch()
    saved, skipped, stored = job.save_missing(store, NOW, max_runs=2, hours=(6,), ready=ready,
                                              fetch=fetch, pause=0)
    assert saved == [runs[0], runs[2]] and skipped == [] and stored == [runs[1]]
    assert runs[1] not in asked and runs[-1] in asked           # stored runs are not looked up again
    assert [c[0] for c in fetch.calls] == [runs[0], runs[2]]    # max_runs stops the third
    assert store.names("shear-tiles-2026-11") == {st.tile_name(r) for r in runs[:3]}
    assert store.get("shear-tiles-2026-11", st.tile_name(runs[1])) == b"already here"
    tile = st.ShearTile.from_bytes(store.get("shear-tiles-2026-11", st.tile_name(runs[2])))
    assert tile.run == runs[2] and tile.forecast_hours == [6]

    record = heartbeat.write(store, job.JOB, started=NOW, finished=NOW,
                             newest_run=max(stored + saved), saved=saved, skipped=skipped)
    assert heartbeat.read(store, "shear-tiles")["newest_run"] == "2026-11-13T06:00:00Z" == record["newest_run"]

    # The next start saves what is left and nothing twice.
    fetch2 = FakeFetch()
    saved2, _, stored2 = job.save_missing(store, NOW, hours=(6,), ready=ready, fetch=fetch2, pause=0)
    assert saved2 == runs[3:-1] and stored2 == runs[:3]


def test_a_run_with_a_missing_file_is_skipped_and_the_rest_are_saved(tmp_path):
    store = LocalStore(tmp_path)
    runs = job.candidate_runs(NOW)
    fetch = FakeFetch(missing={runs[-2]})
    saved, skipped, _ = job.save_missing(store, NOW, hours=(6,), ready=lambda run: run >= runs[-3],
                                         fetch=fetch, pause=0)
    assert saved == [runs[-3], runs[-1]] and skipped == [runs[-2]]
    assert st.tile_name(runs[-2]) not in store.names("shear-tiles-2026-11")


def test_main_writes_the_heartbeat_after_a_start_that_ends_well(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(job, "run_is_ready", lambda run: True)
    monkeypatch.setattr(job, "fetch_hour", FakeFetch())
    monkeypatch.setattr(job, "PAUSE_S", 0)
    monkeypatch.setattr("sys.argv", ["shear_tiles", "--store", f"dir:{tmp_path}", "--max-runs", "1",
                                     "--hours", "6,9", "--verify"])
    job.main()
    store = LocalStore(tmp_path)
    record = heartbeat.read(store, "shear-tiles")
    assert record["job"] == "shear-tiles" and len(record["saved"]) == 1 and record["skipped"] == []
    assert record["newest_run"] == record["saved"][0]
    out = capsys.readouterr().out
    assert "MISMATCH" not in out and out.count("  ok") == 18 and "peak memory" in out


def test_no_heartbeat_when_the_start_fails(tmp_path, monkeypatch):
    def broken(run, hour):
        raise ValueError("unexpected message")

    monkeypatch.setattr(job, "run_is_ready", lambda run: True)
    monkeypatch.setattr(job, "fetch_hour", broken)
    monkeypatch.setattr(job, "PAUSE_S", 0)
    monkeypatch.setattr("sys.argv", ["shear_tiles", "--store", f"dir:{tmp_path}", "--hours", "6"])
    with pytest.raises(ValueError):
        job.main()
    assert heartbeat.read(LocalStore(tmp_path), "shear-tiles") is None


def test_partial_hours_are_refused_for_the_release_store(monkeypatch):
    monkeypatch.setattr("sys.argv", ["shear_tiles", "--store", "releases", "--hours", "6,9"])
    with pytest.raises(SystemExit):
        job.main()
