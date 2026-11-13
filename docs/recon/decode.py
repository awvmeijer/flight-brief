import sys, time, resource
import numpy as np
import eccodes as ec
print("eccodes lib", ec.codes_get_api_version(), "| python binding", getattr(ec, "__version__", "?"), "| numpy", np.__version__)

def read(path):
    out = []; t0 = time.time()
    with open(path, "rb") as f:
        while True:
            g = ec.codes_grib_new_from_file(f)
            if g is None: break
            k = {}
            for key in ("discipline", "parameterCategory", "parameterNumber", "shortName", "name", "units",
                        "typeOfLevel", "level", "gridType", "Ni", "Nj", "Nx", "Ny", "packingType",
                        "dataDate", "dataTime", "forecastTime", "stepRange"):
                try: k[key] = ec.codes_get(g, key)
                except Exception: k[key] = None
            v = ec.codes_get_values(g)
            k["values"] = v
            out.append(k); ec.codes_release(g)
    return out, time.time() - t0

mode = sys.argv[1]
if mode == "gtg":
    msgs, dt = read(sys.argv[2])
    print("GTG messages %d decoded in %.2fs" % (len(msgs), dt))
    for m in msgs:
        v = m["values"]; nx = m["Nx"] or m["Ni"]; ny = m["Ny"] or m["Nj"]
        ok = v[v < 9000]
        print(" d=%s cat=%s num=%s short=%s name=%r units=%s lvl=%s %s grid=%s %sx%s pack=%s date=%s %04d step=%s | n=%d min=%.3f p50=%.3f p95=%.3f p99=%.3f max=%.3f"
              % (m["discipline"], m["parameterCategory"], m["parameterNumber"], m["shortName"], m["name"], m["units"],
                 m["typeOfLevel"], m["level"], m["gridType"], nx, ny, m["packingType"], m["dataDate"], m["dataTime"], m["stepRange"],
                 ok.size, ok.min(), np.percentile(ok, 50), np.percentile(ok, 95), np.percentile(ok, 99), ok.max()))
else:
    allm = []; tdec = 0
    for p in sys.argv[2:]:
        m, dt = read(p); allm += m; tdec += dt
    print("GFS messages %d decoded in %.2fs" % (len(allm), tdec))
    m0 = allm[0]; ni, nj = m0["Ni"], m0["Nj"]
    print(" grid=%s %dx%d pack=%s date=%s %04d step=%s" % (m0["gridType"], ni, nj, m0["packingType"], m0["dataDate"], m0["dataTime"], m0["stepRange"]))
    F = {(m["shortName"], m["level"]): m["values"].reshape(nj, ni) for m in allm}
    print(" fields:", sorted(F))
    t0 = time.time()
    lat = np.linspace(90, -90, nj); a = 6.371e6
    dlam = np.deg2rad(360.0 / ni); dphi = np.deg2rad(180.0 / (nj - 1))
    cos = np.cos(np.deg2rad(lat))[:, None]; cos = np.where(np.abs(lat)[:, None] > 85, np.nan, cos)
    def ddx(f): return (np.roll(f, -1, 1) - np.roll(f, 1, 1)) / (2 * a * cos * dlam)
    def ddy(f): return -np.gradient(f, axis=0) / (a * dphi)          # rows run north to south
    def ti1(lo, mid, hi):                                             # pressure levels, hPa: lo is the lower (higher pressure)
        u, v = F[("u", mid)], F[("v", mid)]
        dz = F[("gh", hi)] - F[("gh", lo)]
        vws = np.hypot(F[("u", hi)] - F[("u", lo)], F[("v", hi)] - F[("v", lo)]) / dz
        dst = ddx(u) - ddy(v); dsh = ddx(v) + ddy(u)
        return vws * np.hypot(dst, dsh), vws, dz
    for lo, mid, hi in ((275, 250, 225), (300, 250, 200)):
        t, vws, dz = ti1(lo, mid, hi)
        ok = t[np.isfinite(t)] * 1e7
        print(" Ellrod TI1 at %d hPa from %d/%d hPa (mean layer depth %.0f m): units 1e-7 s^-2  p50=%.2f p90=%.2f p95=%.2f p99=%.2f p99.9=%.2f max=%.2f | share >=4: %.2f%%  >=8: %.2f%%  >=12: %.2f%%"
              % (mid, lo, hi, np.nanmean(dz), *np.percentile(ok, [50, 90, 95, 99, 99.9]), ok.max(), 100 * (ok >= 4).mean(), 100 * (ok >= 8).mean(), 100 * (ok >= 12).mean()))
    print(" index computed in %.2fs" % (time.time() - t0))
print("peak RSS %.0f MB" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)))
