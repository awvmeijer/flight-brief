"""Save GTG turbulence tiles for cruise levels over the contiguous US.

NOMADS keeps two days of GTG files, so the forecast's track record starts the
day this job first runs. Each invocation saves every 00/06/12/18Z run that is
complete on NOMADS and not yet in the store, oldest first, so a late or skipped
schedule loses nothing as long as the job runs again within about a day and a half.

    python -m flight_brief.jobs.gtg_tiles --store releases
    python -m flight_brief.jobs.gtg_tiles --store dir:/tmp/tiles --max-runs 1 --verify
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

import numpy as np

from ..forecast import grib, sources, tiles
from ..forecast.rangeget import fetch_messages
from ..forecast.store import open_store

FLIGHT_LEVELS = (300, 330, 360, 390, 420)
RUN_HOURS = (0, 6, 12, 18)
LOOK_BACK_HOURS = 48
PAUSE_S = 1.0   # after every request: under 60 a minute, half of NOMADS's limit

# Sample points for the read-back check: (name, latitude, longitude).
CHECK_POINTS = (("Denver", 39.86, -104.67), ("Chicago", 41.98, -87.90), ("Atlanta", 33.64, -84.43),
                ("Seattle", 47.45, -122.31), ("Dallas", 32.90, -97.04), ("New York", 40.64, -73.78))


def group_of(run: dt.datetime) -> str:
    return f"gtg-tiles-{run:%Y-%m}"


def candidate_runs(now: dt.datetime) -> list[dt.datetime]:
    """The 00/06/12/18Z runs of the last two days, oldest first."""
    t = now.replace(minute=0, second=0, microsecond=0)
    out = []
    for back in range(LOOK_BACK_HOURS + 1):
        run = t - dt.timedelta(hours=back)
        if run.hour in RUN_HOURS:
            out.append(run)
    return sorted(out)


def _fetch_hour(run: dt.datetime, hour: int, levels=FLIGHT_LEVELS):
    wants = [(sources.GTG_IDX_NAME[sources.GTG_EDR], sources.gtg_idx_level(fl)) for fl in levels]
    got = fetch_messages(sources.gtg_url(run, hour), wants, pause=PAUSE_S)
    by_level = {}
    for m in grib.decode(got.data):
        if m.param != sources.GTG_EDR or m.forecast_time != hour:
            raise ValueError(f"unexpected message {m.param} +{m.forecast_time} h in {run} f{hour:03d}")
        if (m.data_date, m.data_time) != (int(f"{run:%Y%m%d}"), run.hour * 100):
            raise ValueError(f"file for {run} holds run {m.data_date} {m.data_time:04d}")
        by_level[m.level] = m
    msgs = [by_level[sources.gtg_level_m(fl)] for fl in levels]
    return msgs, got


def build_tile(run: dt.datetime) -> tuple[tiles.Tile, dict]:
    hours = list(sources.GTG_FORECAST_HOURS)
    edr = None
    grid = None
    stats = {"bytes": 0, "requests": 0, "fetch_s": 0.0}
    for h_i, hour in enumerate(hours):
        msgs, got = _fetch_hour(run, hour)
        stats["bytes"] += len(got.data)
        stats["requests"] += got.requests
        stats["fetch_s"] += got.seconds
        for l_i, m in enumerate(msgs):
            q = tiles.quantise(tiles.block_max(m.values, tiles.BLOCK))
            if edr is None:
                edr = np.full((len(hours), len(FLIGHT_LEVELS)) + q.shape, tiles.MISSING, np.uint8)
                grid = grib.lambert_spec(m)
            edr[h_i, l_i] = q
    meta = {
        "format": tiles.FORMAT,
        "source": "NOAA/NCEP DAFS, Graphical Turbulence Guidance, 3 km contiguous US",
        "url_pattern": sources.gtg_url(run, 0).replace("f000", "fFFF"),
        "run": f"{run:%Y-%m-%dT%H:00Z}",
        "parameter": "0-19-30 combined eddy dissipation rate",
        "units": "m^2/3 s^-1",
        "step": tiles.STEP, "missing": tiles.MISSING, "block": tiles.BLOCK,
        "cell": "highest value in each block x block square of the native grid",
        "levels_m": [sources.gtg_level_m(fl) for fl in FLIGHT_LEVELS],
        "grid": grid,
        "saved_at": f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
    }
    return tiles.Tile(edr, hours, list(FLIGHT_LEVELS), meta), stats


def verify(store, run: dt.datetime, checks=((6, 360), (18, 300), (0, 420))) -> bool:
    """Read the saved tile back and compare it with a fresh decode from NOMADS.

    For each sample point the native grid point is found from the coordinates
    eccodes reports (not from this project's projection code), and the highest
    native value in that point's block must equal the tile's value to within
    half a quantisation step.
    """
    tile = tiles.Tile.from_bytes(store.get(group_of(run), tiles.tile_name(run)))
    k, ok = tile.meta["block"], True
    print(f"read back {tiles.tile_name(run)}: edr {tile.edr.shape}, "
          f"{100 * (tile.edr != tiles.MISSING).mean():.1f}% of cells have data")
    for hour, fl in checks:
        (m,), _ = _fetch_hour(run, hour, levels=(fl,))
        lat, lon = grib.grid_latlon(m)
        for name, plat, plon in CHECK_POINTS:
            j, i = np.unravel_index(np.argmin((lat - plat) ** 2 + (lon - plon) ** 2), lat.shape)
            block = m.values[j // k * k:(j // k + 1) * k, i // k * k:(i // k + 1) * k]
            direct = float(np.nanmax(block))
            saved = tile.sample(plat, plon, fl, hour)
            good = abs(saved - direct) <= tile.meta["step"] / 2 + 1e-9
            ok &= good
            print(f"  +{hour:02d} h FL{fl} {name:9s} direct {direct:.3f}  point {m.values[j, i]:.3f}  "
                  f"tile {saved:.3f}  {'ok' if good else 'MISMATCH'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True, help="'releases' or 'dir:<path>'")
    ap.add_argument("--max-runs", type=int, default=8)
    ap.add_argument("--verify", action="store_true",
                    help="read the newest saved run back and compare it with a direct decode")
    args = ap.parse_args()
    store = open_store(args.store)
    now = dt.datetime.now(dt.timezone.utc)
    print(grib.versions())

    todo, have = [], {}
    for run in candidate_runs(now):
        group = group_of(run)
        if group not in have:
            have[group] = store.names(group)
        if tiles.tile_name(run) in have[group]:
            continue
        time.sleep(PAUSE_S)
        if sources.exists(sources.gtg_url(run, sources.GTG_FORECAST_HOURS[-1]) + ".idx"):
            todo.append(run)
        else:
            print(f"{run:%Y-%m-%d %HZ}: not on NOMADS (expired or not posted yet)")
    print(f"{len(todo)} run(s) to save; taking at most {args.max_runs}")

    saved = []
    for run in todo[:args.max_runs]:
        t0 = time.monotonic()
        tile, stats = build_tile(run)
        data = tile.to_bytes()
        store.put(group_of(run), tiles.tile_name(run), data)
        saved.append(run)
        valid = tiles.dequantise(tile.edr[tile.edr != tiles.MISSING])
        print(f"{run:%Y-%m-%d %HZ}: saved {tiles.tile_name(run)} {len(data) / 1e6:.2f} MB | fetched "
              f"{stats['bytes'] / 1e6:.0f} MB in {stats['requests']} requests | "
              f"{time.monotonic() - t0:.0f} s | EDR p50 {np.percentile(valid, 50):.3f} "
              f"p99 {np.percentile(valid, 99):.3f} max {valid.max():.3f}")

    if args.verify and saved:
        if not verify(store, saved[-1]):
            sys.exit("read-back does not match a direct decode")
    print(f"peak memory {grib.peak_rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
