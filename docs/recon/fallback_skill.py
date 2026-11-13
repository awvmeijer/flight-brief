"""Recon measurement: sample open-GFS wind-shear diagnostics (24-27 h lead) at pilot-report positions.
Writes one CSV row per turbulence-bearing cruise report. Nothing is kept from the GRIB files."""
import sys, pickle, math, time, csv, bisect, datetime as dt, urllib.request, concurrent.futures as cf
import numpy as np, eccodes as ec
UA = "flight-brief-recon/0.1 (research; keyless probe)"
S3 = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
LEVELS = (300, 250, 200, 150); VARS = ("UGRD", "VGRD", "HGT", "TMP")
recs, meta = pickle.load(open(sys.argv[1], "rb")); out = sys.argv[2]; step_days = int(sys.argv[3])
POS = {"LGT": 1, "LGT-MOD": 2, "MOD": 3, "MOD-SEV": 4, "SEV": 5, "SEV-EXTM": 6, "EXTM": 6}
R = []
for r in recs:
    a = r.get("tbInt1") or ""
    c = "NEG" if a == "NEG" else ("MOG" if POS.get(a, 0) >= 3 else ("LGT" if a in POS else None))
    if c and r.get("lat") is not None and r.get("fltLvl") is not None and 290 <= r["fltLvl"] <= 440 and r.get("obsTime"):
        R.append((r["obsTime"], r["lat"], r["lon"], r["fltLvl"], c, r.get("pirepType"), r["_box"], a))
R.sort(); T = [x[0] for x in R]
def get(url, rng=None):
    h = {"User-Agent": UA}
    if rng: h["Range"] = "bytes=%d-%d" % rng
    for attempt in range(3):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=90).read()
        except Exception as e:
            if attempt == 2: raise
            time.sleep(2 + 3 * attempt)
def fetch(run, fh):
    base = "%s/gfs.%s/%02d/atmos/gfs.t%02dz.pgrb2.0p25.f%03d" % (S3, run.strftime("%Y%m%d"), run.hour, run.hour, fh)
    idx = [l.split(":") for l in get(base + ".idx").decode().splitlines() if l.strip()]
    offs = [int(r[1]) for r in idx]; want = {}
    for i, r in enumerate(idx):
        if r[3] in VARS and r[4].endswith(" mb") and int(float(r[4].split()[0])) in LEVELS and r[4].split()[0].isdigit():
            want[(r[3], int(r[4].split()[0]))] = (offs[i], offs[i + 1] - 1)
    assert len(want) == len(LEVELS) * len(VARS), (base, len(want))
    nbytes = 0; F = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for k, b in zip(want, ex.map(lambda rg: get(base, rg), want.values())):
            nbytes += len(b); g = ec.codes_new_from_message(b)
            ni, nj = ec.codes_get(g, "Ni"), ec.codes_get(g, "Nj")
            F[k] = ec.codes_get_values(g).reshape(nj, ni).astype(np.float32); ec.codes_release(g)
    return F, nbytes
nj, ni = 721, 1440; a = 6.371e6
lat = np.linspace(90, -90, nj); cos = np.cos(np.deg2rad(lat))[:, None].astype(np.float32); cos[np.abs(lat) > 85] = np.nan
dlam = np.deg2rad(0.25); dphi = np.deg2rad(0.25)
def ddx(f): return (np.roll(f, -1, 1) - np.roll(f, 1, 1)) / (2 * a * cos * dlam)
def ddy(f): return -np.gradient(f, axis=0) / (a * dphi)
def diags(F, lo, hi):
    u = 0.5 * (F[("UGRD", lo)] + F[("UGRD", hi)]); v = 0.5 * (F[("VGRD", lo)] + F[("VGRD", hi)])
    dz = F[("HGT", hi)] - F[("HGT", lo)]
    vws = np.hypot(F[("UGRD", hi)] - F[("UGRD", lo)], F[("VGRD", hi)] - F[("VGRD", lo)]) / dz
    dst = ddx(u) - ddy(v); dsh = ddx(v) + ddy(u); deff = np.hypot(dst, dsh); cvg = -(ddx(u) + ddy(v))
    th_lo = F[("TMP", lo)] * (1000.0 / lo) ** 0.2857; th_hi = F[("TMP", hi)] * (1000.0 / hi) ** 0.2857
    n2 = 9.81 / (0.5 * (th_lo + th_hi)) * (th_hi - th_lo) / dz
    inv_ri = vws ** 2 / np.maximum(n2, 1e-6)
    return {"ti1": vws * deff * 1e7, "ti2": vws * (deff + cvg) * 1e7, "vws": vws * 1e3, "def": deff * 1e5, "spd": np.hypot(u, v), "invri": inv_ri}
LAYERS = [((300, 250), 320), ((250, 200), 363), ((200, 150), 415)]
NAMES = ["ti1", "ti2", "vws", "def", "spd", "invri"]
start = dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc); end = dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc)
jobs = []; d = start
while d <= end:
    for vh, runh, fh in ((15, 12, 27), (18, 18, 24), (21, 18, 27), (24, 0, 24)):
        valid = d + dt.timedelta(hours=vh); run = valid - dt.timedelta(hours=fh)
        assert run.hour == runh % 24
        jobs.append((valid, run, fh))
    d += dt.timedelta(days=step_days)
w = csv.writer(open(out, "w", newline="")); w.writerow(["valid", "lead_h", "obs_time", "lat", "lon", "fl", "cls", "raw_int", "type", "box", "layer_fl"] + [n + s for n in NAMES for s in ("_pt", "_nb", "_col")])
t0 = time.time(); tot_bytes = 0; nrows = 0; fails = 0
for j, (valid, run, fh) in enumerate(jobs):
    vt = valid.timestamp(); i0 = bisect.bisect_left(T, vt - 5400); i1 = bisect.bisect_right(T, vt + 5400)
    if i1 <= i0: continue
    try: F, nb = fetch(run, fh)
    except Exception as e:
        fails += 1; print("FAIL", valid, repr(e)[:120], flush=True); continue
    tot_bytes += nb
    D = [diags(F, lo, hi) for (lo, hi), _ in LAYERS]
    for k in range(i0, i1):
        ot, la, lo_, fl, c, typ, box, raw = R[k]
        li = min(range(3), key=lambda q: abs(LAYERS[q][1] - fl))
        iy = int(round((90 - la) / 0.25)); ix = int(round((lo_ % 360) / 0.25)) % ni
        ys = slice(max(0, iy - 2), min(nj, iy + 3)); xs = [(ix + q) % ni for q in range(-2, 3)]
        row = [valid.strftime("%Y-%m-%dT%HZ"), fh, ot, la, lo_, fl, c, raw, typ, box, LAYERS[li][1]]
        for n in NAMES:
            pt = float(D[li][n][iy, ix]); nbv = float(np.nanmax(D[li][n][ys][:, xs]))
            col = float(np.nanmax([np.nanmax(D[q][n][ys][:, xs]) for q in range(max(0, li - 1), min(3, li + 2))]))
            row += ["%.4g" % pt, "%.4g" % nbv, "%.4g" % col]
        w.writerow(row); nrows += 1
    if j % 10 == 0: print("job %d/%d valid %s rows %d bytes %.0f MB elapsed %.0fs" % (j + 1, len(jobs), valid, nrows, tot_bytes / 1e6, time.time() - t0), flush=True)
print("DONE jobs=%d fails=%d rows=%d bytes=%.0f MB seconds=%.0f" % (len(jobs), fails, nrows, tot_bytes / 1e6, time.time() - t0), flush=True)
