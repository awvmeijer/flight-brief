"""Save wind-shear index tiles (Ellrod TI1 from open GFS winds), worldwide.

For each GFS run the job fetches winds and heights at four pressure levels for
forecast hours 6 to 54, computes the index in three layers, and saves the
highest value within 0.5 degree of every grid point as one compact tile. Each
start saves every 00/06/12/18Z run of the last two days that is complete on
NOAA's public AWS bucket and not yet in the store, oldest first, so a late or
skipped schedule loses nothing.

    python -m flight_brief.jobs.shear_tiles --store releases
    python -m flight_brief.jobs.shear_tiles --store dir:/tmp/tiles --max-runs 1 --hours 6,9 --verify
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import urllib.error

import numpy as np

from ..forecast import ellrod, grib, heartbeat, sources
from ..forecast import shear_tiles as st
from ..forecast.rangeget import fetch_messages
from ..forecast.store import open_store

JOB = "shear-tiles"
RUN_HOURS = (0, 6, 12, 18)
LOOK_BACK_HOURS = 48
READY_HOUR = st.FORECAST_HOURS[-1]   # a run counts as ready when this hour's index is posted
PAUSE_S = 0.1   # after every request; the AWS bucket states no limit, this is manners

_NAME_OF = {param: name for name, param in sources.GFS_PARAM.items()}

# Sample points for the read-back check: (name, latitude, longitude). Two sit where the
# neighbourhood crosses the first or last column of the grid, one in each hemisphere's jet.
CHECK_POINTS = (("London", 51.47, -0.46), ("Mid-Atlantic", 50.0, -30.0), ("Denver", 39.86, -104.67),
                ("Tokyo", 35.55, 139.78), ("Fiji", -17.76, 179.9), ("Sydney", -33.95, 151.18))


def group_of(run: dt.datetime) -> str:
    return f"shear-tiles-{run:%Y-%m}"


def candidate_runs(now: dt.datetime) -> list[dt.datetime]:
    """The 00/06/12/18Z runs of the last two days, oldest first."""
    t = now.replace(minute=0, second=0, microsecond=0)
    out = []
    for back in range(LOOK_BACK_HOURS + 1):
        run = t - dt.timedelta(hours=back)
        if run.hour in RUN_HOURS:
            out.append(run)
    return sorted(out)


def run_is_ready(run: dt.datetime) -> bool:
    return sources.exists(sources.gfs_url(run, READY_HOUR) + ".idx")


def fetch_hour(run: dt.datetime, hour: int) -> tuple[dict, int, int, float]:
    """Winds and heights of one forecast hour: ({(variable, hPa): array}, bytes, requests, seconds)."""
    got = fetch_messages(sources.gfs_url(run, hour), sources.gfs_wants(), pause=PAUSE_S)
    fields = {}
    for i, m in enumerate(grib.decode(got.data)):
        if m.param not in _NAME_OF or m.type_of_level != "isobaricInhPa" or m.forecast_time != hour:
            raise ValueError(f"unexpected message {m.param} {m.type_of_level} "
                             f"+{m.forecast_time} h in {run} f{hour:03d}")
        if (m.data_date, m.data_time) != (int(f"{run:%Y%m%d}"), run.hour * 100):
            raise ValueError(f"file for {run} holds run {m.data_date} {m.data_time:04d}")
        if (m.grid_type, m.ny, m.nx) != ("regular_ll", st.NJ, st.NI):
            raise ValueError(f"unexpected grid {m.grid_type} {m.nx}x{m.ny}")
        if i == 0:
            lat, lon = grib.grid_latlon(m)
            if (lat[0, 0], lat[-1, 0], lon[0, 0], lon[0, 1]) != (90.0, -90.0, 0.0, st.GRID_DEG):
                raise ValueError("grid does not start at 90N 0E running south and east")
        fields[(_NAME_OF[m.param], m.level)] = m.values
    if set(fields) != {(v, lv) for v in sources.GFS_VARS for lv in sources.GFS_LEVELS_HPA}:
        raise ValueError(f"{run} f{hour:03d}: got {sorted(fields)}")
    return fields, len(got.data), got.requests, got.seconds


def layer_index(fields: dict, lo: int, hi: int) -> np.ndarray:
    """Ellrod TI1 for one layer in units of 1e-7 s^-2, at every grid point (not yet the neighbourhood)."""
    return 1e7 * ellrod.ti1(fields[("UGRD", lo)], fields[("VGRD", lo)], fields[("HGT", lo)],
                            fields[("UGRD", hi)], fields[("VGRD", hi)], fields[("HGT", hi)])


def build_tile(run: dt.datetime, hours=st.FORECAST_HOURS, *, fetch=None) -> tuple[st.ShearTile, dict]:
    """One forecast hour at a time, so peak memory is one hour's twelve fields plus the tile."""
    fetch = fetch or fetch_hour
    hours = list(hours)
    out = np.full((len(hours), len(ellrod.LAYERS), st.NJ, st.NI), st.MISSING, np.uint8)
    stats = {"bytes": 0, "requests": 0, "fetch_s": 0.0, "max": 0.0, "clipped": 0}
    for h_i, hour in enumerate(hours):
        fields, nbytes, requests, seconds = fetch(run, hour)
        stats["bytes"] += nbytes
        stats["requests"] += requests
        stats["fetch_s"] += seconds
        for l_i, ((lo, hi), _) in enumerate(ellrod.LAYERS):
            nb = st.neighbourhood_max(layer_index(fields, lo, hi))
            out[h_i, l_i] = st.quantise(nb)
            stats["max"] = max(stats["max"], float(np.nanmax(nb)))
            stats["clipped"] += int((nb >= st.STEP * (st.MISSING - 1)).sum())
        del fields
    meta = {
        "format": st.FORMAT,
        "source": "NOAA GFS 0.25 degree pressure-level winds and heights, public AWS bucket",
        "url_pattern": sources.gfs_url(run, 0).replace("f000", "fFFF"),
        "run": f"{run:%Y-%m-%dT%H:00Z}",
        "index": "Ellrod TI1: vertical wind shear x horizontal deformation. A wind-shear index, "
                 "not an eddy dissipation rate; it has no severity calibration.",
        "units": st.UNITS,
        "step": st.STEP, "missing": st.MISSING,
        "rounding": "down, so value >= t matches the unrounded value for any t that is a multiple of step",
        "clip": f"values of {st.STEP * (st.MISSING - 1):g} and above are stored as {st.STEP * (st.MISSING - 1):g}",
        "neighbourhood": {"reach_points": st.REACH, "reach_deg": st.REACH * st.GRID_DEG,
                          "shape": "square in latitude and longitude, both ends included, longitude wraps",
                          "text": "each grid point holds the highest value among the 5 x 5 grid points "
                                  "centred on it; a reader takes the grid point nearest its position"},
        "layers": [{"hpa": [lo, hi], "flight_level": fl} for (lo, hi), fl in ellrod.LAYERS],
        "grid": {"type": "regular_ll", "nj": st.NJ, "ni": st.NI, "step_deg": st.GRID_DEG,
                 "lat_first": 90.0, "lon_first": 0.0, "rows": "north to south",
                 "columns": "west to east, wrapping", "max_abs_lat": st.MAX_LAT},
        "saved_at": f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
    }
    return st.ShearTile(out, hours, list(st.LAYER_FLS), meta), stats


def save_missing(store, now: dt.datetime, *, max_runs: int = 8, hours=st.FORECAST_HOURS,
                 ready=None, fetch=None, pause: float | None = None):
    """Save every ready run of the last two days that the store lacks, oldest first.

    Returns (saved, skipped, already stored), each a list of runs.
    """
    ready = ready or run_is_ready
    pause = PAUSE_S if pause is None else pause
    todo, have, stored = [], {}, []
    for run in candidate_runs(now):
        group = group_of(run)
        if group not in have:
            have[group] = store.names(group)
        if st.tile_name(run) in have[group]:
            stored.append(run)
            continue
        time.sleep(pause)
        if ready(run):
            todo.append(run)
        else:
            print(f"{run:%Y-%m-%d %HZ}: not on AWS yet")
    print(f"{len(todo)} run(s) look available; saving at most {max_runs}")

    saved, skipped = [], []
    for run in todo:
        if len(saved) >= max_runs:
            break
        t0 = time.monotonic()
        try:
            tile, stats = build_tile(run, hours, fetch=fetch)
        except urllib.error.HTTPError as e:
            # The index of the last hour can be up a moment before an earlier file is.
            # Skip the run; the next start picks it up.
            if e.code not in (403, 404):
                raise
            print(f"{run:%Y-%m-%d %HZ}: skipped, a file is missing on AWS ({e.url})")
            skipped.append(run)
            continue
        data = tile.to_bytes()
        store.put(group_of(run), st.tile_name(run), data)
        saved.append(run)
        # From a histogram of the counts, one forecast hour at a time: the whole tile as
        # floating point would be 400 MB.
        hist = sum(np.bincount(x.reshape(-1), minlength=256) for x in tile.ti1)[:st.MISSING]
        share = np.cumsum(hist) / hist.sum()
        p50, p99 = (st.STEP * int(np.searchsorted(share, q)) for q in (0.5, 0.99))
        above = {t: 100 * hist[int(t / st.STEP):].sum() / hist.sum() for t in (8, 12)}
        print(f"{run:%Y-%m-%d %HZ}: saved {st.tile_name(run)} {len(data) / 1e6:.2f} MB | fetched "
              f"{stats['bytes'] / 1e6:.0f} MB in {stats['requests']} requests | "
              f"{time.monotonic() - t0:.0f} s | index p50 {p50:.1f} "
              f"p99 {p99:.1f} max {stats['max']:.1f} | "
              f"at or above 8: {above[8]:.1f}%  12: {above[12]:.1f}% | "
              f"{stats['clipped']} cell(s) clipped")
    return saved, skipped, stored


def verify(store, run: dt.datetime, *, fetch=None) -> bool:
    """Read the saved tile back and compare it with a fresh computation from AWS.

    For three (forecast hour, layer) pairs the messages are fetched again and the
    index recomputed. At each sample point the highest value is taken the way the
    recon took it (nearest grid point, two points either side in each direction,
    by direct indexing rather than this project's neighbourhood code) and must
    equal the tile's value after the same rounding down.
    """
    fetch = fetch or fetch_hour
    tile = st.ShearTile.from_bytes(store.get(group_of(run), st.tile_name(run)))
    step, ok = tile.meta["step"], True
    top = step * (st.MISSING - 1)
    print(f"read back {st.tile_name(run)}: ti1 {tile.ti1.shape}, hours {tile.forecast_hours}, "
          f"{100 - 100 * np.count_nonzero(tile.ti1 == st.MISSING) / tile.ti1.size:.1f}% of cells have data")
    hours = tile.forecast_hours
    checks = sorted({(hours[0], 0), (hours[len(hours) // 2], 1), (hours[-1], 2)})
    for hour, l_i in checks:
        (lo, hi), fl = ellrod.LAYERS[l_i]
        fields, *_ = fetch(run, hour)
        direct = layer_index(fields, lo, hi)
        for name, plat, plon in CHECK_POINTS:
            iy = int(round((90 - plat) / 0.25))
            ix = int(round((plon % 360) / 0.25)) % st.NI
            ys = slice(max(0, iy - 2), min(st.NJ, iy + 3))
            xs = [(ix + q) % st.NI for q in range(-2, 3)]
            near = float(np.nanmax(direct[ys][:, xs]))
            want = min(np.floor(near / step) * step, top)
            got = tile.sample(plat, plon, fl, run + dt.timedelta(hours=hour))
            good = got is not None and got == (want, fl, hour)
            ok &= good
            print(f"  +{hour:02d} h FL{fl} {name:12s} direct {near:6.2f}  point {direct[iy, ix]:6.2f}  "
                  f"tile {'none' if got is None else format(got[0], '6.2f')}  {'ok' if good else 'MISMATCH'}")
    return ok


def _hours(text: str) -> list[int]:
    hours = sorted({int(h) for h in text.split(",")})
    if not set(hours) <= set(st.FORECAST_HOURS):
        raise argparse.ArgumentTypeError(f"forecast hours must be among {list(st.FORECAST_HOURS)}")
    return hours


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True, help="'releases' or 'dir:<path>'")
    ap.add_argument("--max-runs", type=int, default=8)
    ap.add_argument("--hours", type=_hours, default=list(st.FORECAST_HOURS),
                    help="comma-separated forecast hours, for a quick rehearsal (not with --store releases)")
    ap.add_argument("--verify", action="store_true",
                    help="read the newest saved run back and compare it with a direct computation")
    args = ap.parse_args()
    if args.store == "releases" and args.hours != list(st.FORECAST_HOURS):
        ap.error("--hours makes a partial tile, which must not take a full tile's place in the releases")
    store = open_store(args.store)
    now = dt.datetime.now(dt.timezone.utc)
    print(grib.versions())

    saved, skipped, stored = save_missing(store, now, max_runs=args.max_runs, hours=args.hours)
    if args.verify and saved:
        if not verify(store, saved[-1]):
            sys.exit("read-back does not match a direct computation")
    # Written last, so a start that failed anywhere above leaves the old heartbeat in place.
    record = heartbeat.write(store, JOB, started=now, finished=dt.datetime.now(dt.timezone.utc),
                             newest_run=max(stored + saved, default=None), saved=saved, skipped=skipped)
    print(f"heartbeat: finished {record['finished_at']}, newest run in the store {record['newest_run']}")
    print(f"peak memory {grib.peak_rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
