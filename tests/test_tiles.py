import datetime as dt

import numpy as np
import pytest

from flight_brief.forecast import tiles
from flight_brief.forecast.store import LocalStore
from flight_brief.jobs import gtg_tiles

# The GTG grid's projection, as read from a file on 2026-11-14.
GRID = {"projection": "lambert_conformal", "earth_radius_m": 6371229.0, "lat_1": 38.5, "lat_2": 38.5,
        "lat_d": 38.5, "lon_v": 262.5, "lat_first": 21.138123, "lon_first": 237.280472,
        "dx_m": 3000.0, "dy_m": 3000.0, "nx": 1799, "ny": 1059}


def test_block_max_takes_the_highest_ignores_gaps_and_keeps_partial_edges():
    v = np.arange(30, dtype=float).reshape(5, 6)
    v[0, 0] = np.nan
    v[4, 4:] = np.nan
    b = tiles.block_max(v, 4)
    assert b.shape == (2, 2)
    assert b[0, 0] == 21 and b[0, 1] == 23 and b[1, 0] == 27
    assert np.isnan(b[1, 1])  # a block with no data stays empty


def test_quantise_round_trip_is_within_half_a_step_and_marks_gaps():
    v = np.array([0.0, 0.0024, 0.0026, 0.214, 0.578, 1.27, 5.0, np.nan])
    q = tiles.quantise(v)
    assert q.dtype == np.uint8 and q[-1] == tiles.MISSING and q[-2] == 254  # clipped, not "missing"
    back = tiles.dequantise(q)
    assert np.isnan(back[-1])
    assert np.all(np.abs(back[:6] - v[:6]) <= tiles.STEP / 2 + 1e-12)


def test_lambert_places_the_first_point_at_the_origin_and_the_centre_meridian_mid_grid():
    col, row = tiles.lambert_xy(GRID, GRID["lat_first"], GRID["lon_first"])
    assert abs(col) < 1e-6 and abs(row) < 1e-6
    col, _ = tiles.lambert_xy(GRID, 38.5, -97.5)   # on the central meridian
    assert abs(col - (GRID["nx"] - 1) / 2) < 1.0   # the grid is symmetric about it


def _tile():
    edr = np.full((2, 2, 265, 450), tiles.MISSING, np.uint8)
    edr[1, 0, 100, 200] = 40
    meta = {"format": tiles.FORMAT, "step": tiles.STEP, "block": 4, "grid": GRID}
    return tiles.Tile(edr, [0, 6], [300, 330], meta)


def test_tile_survives_a_store_round_trip_and_samples_the_right_cell(tmp_path):
    store = LocalStore(tmp_path)
    store.put("g", "t.npz", _tile().to_bytes())
    assert store.names("g") == {"t.npz"} and store.names("absent") == set()
    t = tiles.Tile.from_bytes(store.get("g", "t.npz"))
    assert t.forecast_hours == [0, 6] and t.flight_levels == [300, 330] and t.meta["grid"] == GRID
    # Find a latitude/longitude inside cell (row 100, column 200) by searching, then sample it.
    lats, lons = np.meshgrid(np.arange(25, 50, 0.02), np.arange(-125, -70, 0.02), indexing="ij")
    col, row = tiles.lambert_xy(GRID, lats, lons)
    inside = (np.rint(row) // 4 == 100) & (np.rint(col) // 4 == 200)
    lat, lon = lats[inside][0], lons[inside][0]
    assert t.sample(lat, lon, 300, 6) == pytest.approx(0.2)
    assert np.isnan(t.sample(lat, lon, 330, 6))      # no data in that slice
    assert np.isnan(t.sample(10.0, -150.0, 300, 6))  # outside the grid


def test_unknown_format_is_refused():
    t = _tile()
    t.meta["format"] = "something-else/9"
    with pytest.raises(ValueError, match="unknown tile format"):
        tiles.Tile.from_bytes(t.to_bytes())


def test_candidate_runs_are_the_synoptic_hours_of_two_days_oldest_first():
    now = dt.datetime(2026, 11, 14, 13, 47, tzinfo=dt.timezone.utc)
    runs = gtg_tiles.candidate_runs(now)
    assert runs[0] == dt.datetime(2026, 11, 12, 18, tzinfo=dt.timezone.utc)
    assert runs[-1] == dt.datetime(2026, 11, 14, 12, tzinfo=dt.timezone.utc)
    assert len(runs) == 8 and all(r.hour in (0, 6, 12, 18) for r in runs)
    assert gtg_tiles.group_of(runs[0]) == "gtg-tiles-2026-11"
    assert tiles.tile_name(runs[-1]) == "gtg_conus_edr_12km_20261114T12Z.npz"
