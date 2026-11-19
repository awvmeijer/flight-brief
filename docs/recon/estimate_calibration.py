"""Calibration of the worldwide estimate: which wind-shear index values the brief may call
smooth, light or moderate.

Three steps, each its own command. All of them use the project's own tile code, so a value
here is the value a brief reads from a tile.

    reports   sample the index (24 to 27 h lead) at pilot-report positions -> estimate_rows.csv
    match     over the contiguous US, where NOAA's GTG forecast and the index both exist, take
              the highest value of each along the same simulated ten-minute stretches and match their
              distributions -> estimate_match.json
    table     the cut points, and how pilot reports fall on each side of them -> stdout, JSON

    PIREP_SNAPSHOTS=<oceanic-flight-watch>/data/snapshots/pirep \\
        uv run python docs/recon/estimate_calibration.py reports estimate_rows.csv   # then gzip -9
    uv run python docs/recon/estimate_calibration.py match <folder of downloaded tiles> docs/recon/estimate_match.json
    uv run python docs/recon/estimate_calibration.py table docs/recon/estimate_rows.csv.gz docs/recon/estimate_match.json

Nothing is kept from the GRIB files. Requests go to NOAA's public AWS bucket with the job's
generic User-Agent, at most four at a time.
"""
from __future__ import annotations

import bisect
import concurrent.futures as cf
import csv
import datetime as dt
import gzip
import json
import math
import os
import pathlib
import sys
import threading
import time

import numpy as np

from flight_brief.forecast import ellrod, grib, tiles
from flight_brief.forecast import shear_tiles as st
from flight_brief.jobs import shear_tiles as job

UTC = dt.timezone.utc
START = dt.datetime(2026, 10, 14, tzinfo=UTC)   # the US-wide collection starts here
FL = (290, 440)                                 # the index's cruise range in the brief
WINDOW_S = 5400                                 # a report within 90 minutes of a valid time
POS = {"LGT": 1, "LGT-MOD": 2, "MOD": 3, "MOD-SEV": 4, "SEV": 5, "SEV-EXTM": 6, "EXTM": 6}
# NOAA Aviation Weather Center, GFA help, "Estimated EDR Threshold": light, moderate, severe.
NOAA_EDR = {"light": (0.13, 0.16, 0.36), "medium": (0.15, 0.20, 0.44), "heavy": (0.17, 0.24, 0.54)}


def cls(intensity: str) -> str | None:
    if intensity == "NEG":
        return "NEG"
    return "MOG" if POS.get(intensity, 0) >= 3 else ("LGT" if intensity in POS else None)


def load_reports(root: str) -> list[tuple]:
    """(obs time, lat, lon, flight level, class, stated intensity), unique, at cruise level."""
    seen = {}
    for day in sorted(os.listdir(root)):
        for fn in sorted(os.listdir(os.path.join(root, day))):
            if not fn.endswith(".json.gz"):
                continue
            try:
                with gzip.open(os.path.join(root, day, fn)) as fh:
                    payload = json.load(fh).get("payload") or []
            except (OSError, ValueError):
                continue
            for r in payload:
                seen.setdefault((r.get("obsTime"), r.get("rawOb")), r)
    out = []
    for r in seen.values():
        c = cls(r.get("tbInt1") or "")
        if (c and r.get("lat") is not None and r.get("lon") is not None and r.get("obsTime")
                and r.get("fltLvl") is not None and FL[0] <= r["fltLvl"] <= FL[1]
                and r["obsTime"] >= START.timestamp()):
            out.append((r["obsTime"], r["lat"], r["lon"], r["fltLvl"], c, r["tbInt1"]))
    return sorted(out)


def tile_values(fields: dict) -> list[np.ndarray]:
    """Per layer, the value a tile would hold at every grid point (neighbourhood, rounded down)."""
    return [st.dequantise(st.quantise(st.neighbourhood_max(job.layer_index(fields, lo, hi))))
            for (lo, hi), _ in ellrod.LAYERS]


def _one_decoder_at_a_time() -> None:
    """The GRIB library is not promised to be safe in threads: downloads run side by side,
    decoding does not."""
    lock = threading.Lock()
    for name in ("decode", "grid_latlon"):
        inner = getattr(grib, name)

        def locked(*args, _inner=inner):
            with lock:
                return _inner(*args)
        setattr(grib, name, locked)


def cmd_reports(out_path: str) -> None:
    _one_decoder_at_a_time()
    reports = load_reports(os.environ["PIREP_SNAPSHOTS"])
    times = [r[0] for r in reports]
    print(f"{len(reports)} cruise reports with a stated intensity since {START:%Y-%m-%d}", flush=True)
    parts = pathlib.Path(out_path + ".parts")
    parts.mkdir(exist_ok=True)
    jobs, valid = [], START
    last = dt.datetime.fromtimestamp(times[-1], UTC)
    while valid <= last + dt.timedelta(hours=2):
        lead = 24 if valid.hour % 6 == 0 else 27
        i0 = bisect.bisect_left(times, valid.timestamp() - WINDOW_S)
        i1 = bisect.bisect_right(times, valid.timestamp() + WINDOW_S)
        if i1 > i0:
            jobs.append((valid, lead, i0, i1))
        valid += dt.timedelta(hours=3)
    print(f"{len(jobs)} valid times to fetch", flush=True)

    def one(spec):
        valid, lead, i0, i1 = spec
        part = parts / f"{valid:%Y%m%dT%H}.csv"
        if part.exists():
            return 0
        try:
            fields, nbytes, *_ = job.fetch_hour(valid - dt.timedelta(hours=lead), lead)
        except Exception as exc:  # noqa: BLE001 - a missing file is counted, not fatal
            print(f"FAIL {valid:%Y-%m-%d %HZ} {exc!r}"[:160], flush=True)
            return -1
        layers = tile_values(fields)
        rows = []
        for obs, lat, lon, fl, c, raw in reports[i0:i1]:
            if abs(lat) > st.MAX_LAT:
                continue
            li = st.nearest_layer(fl)
            row, col = st.grid_index(lat, lon)
            v = layers[li][row, col]
            if not math.isnan(v):
                rows.append((f"{valid:%Y-%m-%dT%HZ}", lead, int(obs), lat, lon, fl, c, raw,
                             st.LAYER_FLS[li], f"{v:g}"))
        tmp = part.with_suffix(".tmp")
        with tmp.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
        tmp.rename(part)
        return nbytes

    t0, done, fails, total = time.time(), 0, 0, 0
    with cf.ThreadPoolExecutor(4) as pool:
        for n in pool.map(one, jobs):
            done += 1
            fails += n < 0
            total += max(n, 0)
            if done % 10 == 0:
                print(f"{done}/{len(jobs)} valid times, {total / 1e6:.0f} MB, "
                      f"{time.time() - t0:.0f} s, {fails} failed", flush=True)
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["valid", "lead_h", "obs_time", "lat", "lon", "fl", "cls", "stated", "layer_fl", "index"])
        n = 0
        for part in sorted(parts.glob("*.csv")):
            for row in csv.reader(part.open()):
                w.writerow(row)
                n += 1
    print(f"DONE {n} rows, {fails} valid times failed, {total / 1e6:.0f} MB, {time.time() - t0:.0f} s")


# --- match: the index against GTG over the contiguous US ------------------------------------

SEG_NM = 80.0       # ten minutes of flight at a jet's groundspeed: one bar of the brief
SEG_STEP_NM = 20.0  # the brief's own spacing of route points
PER_PAIR = 300      # simulated stretches per pair of tiles


def _segments(rng: np.random.Generator, n: int, seg_nm: float) -> tuple[np.ndarray, np.ndarray]:
    """n great-circle stretches of flight, starting inside the contiguous US."""
    lat0 = np.radians(rng.uniform(27, 47, n))[:, None]
    lon0 = np.radians(rng.uniform(-120, -75, n))[:, None]
    brg = rng.uniform(0, 2 * np.pi, n)[:, None]
    d = np.linspace(0, seg_nm / 3440.065, int(round(seg_nm / SEG_STEP_NM)) + 1)[None, :]
    lat = np.arcsin(np.sin(lat0) * np.cos(d) + np.cos(lat0) * np.sin(d) * np.cos(brg))
    lon = lon0 + np.arctan2(np.sin(brg) * np.sin(d) * np.cos(lat0), np.cos(d) - np.sin(lat0) * np.sin(lat))
    return np.degrees(lat), np.degrees(lon)


def cmd_match(folder: str, out_path: str, seg_nm: str = str(SEG_NM)) -> None:
    seg = float(seg_nm)
    root = pathlib.Path(folder)
    shear = {st.run_of(p.name): p for p in root.rglob("gfs_global_ti1_0p25_*.npz")}
    gtg = sorted(root.rglob("gtg_conus_edr_12km_*.npz"))
    print(f"{len(gtg)} GTG tiles, {len(shear)} index tiles")
    rng = np.random.default_rng(20261119)
    pairs, used, loaded = [], 0, {}
    for path in gtg:
        g = tiles.Tile.from_bytes(path.read_bytes())
        g_run = dt.datetime.strptime(g.meta["run"][:13], "%Y-%m-%dT%H").replace(tzinfo=UTC)
        grid, block = g.meta["grid"], g.meta["block"]
        for h_i, fh in enumerate(g.forecast_hours):
            valid = g_run + dt.timedelta(hours=fh)
            runs = [r for r in shear if (valid - r) / dt.timedelta(hours=1) in st.FORECAST_HOURS]
            if not runs:
                continue
            s_run = max(runs)   # the newest index run that holds this hour
            if s_run not in loaded:
                if len(loaded) >= 3:
                    loaded.pop(next(iter(loaded)))
                loaded[s_run] = st.ShearTile.from_bytes(shear[s_run].read_bytes())
            s = loaded[s_run]
            s_h = s.forecast_hours.index(int((valid - s_run) / dt.timedelta(hours=1)))
            lat, lon = _segments(rng, PER_PAIR, seg)
            col, row = tiles.lambert_xy(grid, lat, lon)
            i, j = np.rint(col).astype(int), np.rint(row).astype(int)
            inside = ((i >= 0) & (i < grid["nx"]) & (j >= 0) & (j < grid["ny"])).all(axis=1)
            lv = rng.integers(0, len(g.flight_levels), PER_PAIR)
            layer = np.array([st.nearest_layer(g.flight_levels[q]) for q in lv])
            edr = g.edr[h_i, lv[:, None], np.clip(j, 0, grid["ny"] - 1) // block,
                        np.clip(i, 0, grid["nx"] - 1) // block]
            r_i = np.rint((90 - lat) / st.GRID_DEG).astype(int)
            c_i = np.rint((lon % 360) / st.GRID_DEG).astype(int) % st.NI
            ti1 = s.ti1[s_h, layer[:, None], r_i, c_i]
            ok = inside & (edr != tiles.MISSING).all(axis=1) & (ti1 != st.MISSING).all(axis=1)
            for e, t in zip(edr[ok].max(axis=1) * g.meta["step"], ti1[ok].max(axis=1) * s.meta["step"]):
                pairs.append((float(e), float(t)))
            used += 1
    e = np.array([p[0] for p in pairs])
    t = np.array([p[1] for p in pairs])
    print(f"{len(pairs)} simulated stretches of {seg:g} nm from {used} pairs of forecast hours")
    result = {"pairs": len(pairs), "tile_hours": used, "segment_nm": seg, "point_spacing_nm": SEG_STEP_NM,
              "rank_correlation": round(float(_spearman(e, t)), 3), "classes": {}}
    for name, (lgt, mod, sev) in NOAA_EDR.items():
        share = {k: float((e >= v).mean()) for k, v in (("light", lgt), ("moderate", mod), ("severe", sev))}
        cuts = {k: float(np.quantile(t, 1 - share[k])) for k in ("light", "moderate")}
        est = np.digitize(t, [cuts["light"], cuts["moderate"]])
        off = np.digitize(e, [lgt, mod])
        table = [[int(((off == a) & (est == b)).sum()) for b in range(3)] for a in range(3)]
        result["classes"][name] = {
            "edr": {"light": lgt, "moderate": mod, "severe": sev},
            "share_of_stretches_at_or_above": {k: round(v, 4) for k, v in share.items()},
            "index_at_the_same_share": {k: round(v, 1) for k, v in cuts.items()},
            "stretches_by_official_then_estimate": table,
            "same_level": round(float((off == est).mean()), 3),
            "within_one_level": round(float((abs(off - est) <= 1).mean()), 3),
        }
        print(name, json.dumps(result["classes"][name]))
    pathlib.Path(out_path).write_text(json.dumps(result, indent=1) + "\n")


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


# --- table: pilot reports on each side of the cut points --------------------------------------

def cmd_table(rows_path: str, match_path: str) -> None:
    match = json.loads(pathlib.Path(match_path).read_text())
    opener = gzip.open if rows_path.endswith(".gz") else open
    with opener(rows_path, "rt", newline="") as fh:
        rows = list(csv.DictReader(fh))
    v = np.array([float(r["index"]) for r in rows])
    c = np.array([r["cls"] for r in rows])
    sev = np.array([POS.get(r["stated"], 0) >= 5 for r in rows])
    print(f"{len(rows)} reports: {(c == 'NEG').sum()} smooth, {(c == 'LGT').sum()} light, "
          f"{(c == 'MOG').sum()} moderate or more ({sev.sum()} of them severe or more)")
    print("\nindex band      reports   smooth   light   moderate+")
    edges = [0, 2, 4, 6, 8, 10, 12, 16, 20, 25, 30, 40, 1e9]
    for lo, hi in zip(edges, edges[1:]):
        m = (v >= lo) & (v < hi)
        if m.sum():
            print(f"{lo:>4g} to {hi if hi < 1e9 else 'up':>4}  {m.sum():>7}   "
                  + "   ".join(f"{100 * (c[m] == k).mean():5.1f}%" for k in ("NEG", "LGT", "MOG")))
    out = {}
    for name, block in match["classes"].items():
        cut = block["index_at_the_same_share"]
        level = np.digitize(v, [cut["light"], cut["moderate"]])
        out[name] = {"cuts": cut, "levels": {}}
        print(f"\n{name} aircraft: light from {cut['light']}, moderate from {cut['moderate']}")
        for q, word in enumerate(("smooth", "light", "moderate")):
            m = level == q
            shares = {k: round(float((c[m] == k).mean()), 3) for k in ("NEG", "LGT", "MOG")} if m.sum() else {}
            out[name]["levels"][word] = {"reports": int(m.sum()), **shares,
                                         "share_of_all_moderate_or_more": round(float((m & (c == "MOG")).sum() / max(1, (c == "MOG").sum())), 3),
                                         "share_of_all_smooth": round(float((m & (c == "NEG")).sum() / max(1, (c == "NEG").sum())), 3)}
            print(f"  estimate {word:9s} {m.sum():>6} reports: " + ", ".join(f"{k} {100 * s:.0f}%" for k, s in shares.items()))
    print("\n" + json.dumps(out, indent=1))


if __name__ == "__main__":
    {"reports": cmd_reports, "match": cmd_match, "table": cmd_table}[sys.argv[1]](*sys.argv[2:])
