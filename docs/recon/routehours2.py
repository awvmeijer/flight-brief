import pickle, sys, math, bisect, collections, datetime as dt
recs, meta = pickle.load(open(sys.argv[1], "rb"))
POS = {"LGT": 1, "LGT-MOD": 2, "MOD": 3, "MOD-SEV": 4, "SEV": 5, "SEV-EXTM": 6, "EXTM": 6}
R = []
for r in recs:
    a = r.get("tbInt1") or ""
    c = "NEG" if a == "NEG" else ("MOG" if POS.get(a, 0) >= 3 else ("LGT" if a in POS else None))
    if c and r.get("lat") is not None and r.get("fltLvl") is not None and r.get("obsTime"):
        R.append((r["obsTime"], math.radians(r["lat"]), math.radians(r["lon"]), r["fltLvl"], c))
R.sort(); T = [x[0] for x in R]
AP = {"KSFO": (37.619, -122.375), "KLAX": (33.942, -118.408), "KSEA": (47.449, -122.309), "PHNL": (21.318, -157.922), "PANC": (61.174, -149.996),
      "RJAA": (35.765, 140.386), "YSSY": (-33.946, 151.177), "NZAA": (-37.008, 174.792), "WSSS": (1.350, 103.994), "KLAS": (36.080, -115.152), "KPDX": (45.589, -122.597)}
ROUTES = [("KLAX", "KSEA"), ("KSFO", "KSEA"), ("KLAS", "KPDX"), ("KSFO", "PHNL"), ("KLAX", "PHNL"), ("KSEA", "PANC"), ("KSFO", "RJAA"), ("KLAX", "RJAA"), ("PHNL", "RJAA"), ("KLAX", "YSSY"), ("YSSY", "NZAA"), ("RJAA", "WSSS")]
NM = 3440.065
def vec(la, lo): la, lo = math.radians(la), math.radians(lo); return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
def gc(a, b, step_nm):
    va, vb = vec(*AP[a]), vec(*AP[b]); d = math.acos(max(-1, min(1, sum(x * y for x, y in zip(va, vb))))); n = max(1, int(d * NM / step_nm)); pts = []
    for i in range(n + 1):
        f = i / n; A = math.sin((1 - f) * d) / math.sin(d); B = math.sin(f * d) / math.sin(d)
        x, y, z = (A * p + B * q for p, q in zip(va, vb)); pts.append((math.atan2(z, math.hypot(x, y)), math.atan2(y, x), f * d * NM))
    return pts, d * NM
def dist_nm(la1, lo1, la2, lo2):
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * NM * math.asin(min(1, math.sqrt(h)))
GS = 480.0; FL = 360
WINDOWS = {"100nm/90min/4000ft": (100, 5400, 40)}
t0 = int(dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc).timestamp()); ndays = 69
print("reports with turbulence field, position and level:", len(R))
for wname, (dnm, dts, dfl) in WINDOWS.items():
    print("== window", wname, "| assumed cruise FL%d, %d kt, departures every 3 h for %d days" % (FL, GS, ndays))
    print("%-10s %6s %5s | %8s %8s %8s %8s %8s | %s" % ("route", "nm", "hours", "rt-hours", "any tb", "smooth", "MOG", "both", "median reports per verifiable route-hour"))
    for a, b in ROUTES:
        pts, total = gc(a, b, 40.0)
        pts = [p for p in pts if 100 <= p[2] <= total - 100]        # drop climb and descent
        nh = int(math.ceil(total / GS)); tot = any_ = neg = mog = both = 0; counts = []
        for d in range(ndays):
            for dep_h in range(0, 24, 3):
                dep = t0 + d * 86400 + dep_h * 3600
                hours = collections.defaultdict(set)
                for la, lo, s in pts:
                    t = dep + s / GS * 3600; h = int(s // GS)
                    i = bisect.bisect_left(T, t - dts); j = bisect.bisect_right(T, t + dts)
                    hours[h]
                    for k in range(i, j):
                        _, rla, rlo, rfl, c = R[k]
                        if abs(rfl - FL) <= dfl and dist_nm(la, lo, rla, rlo) <= dnm: hours[h].add((k, c))
                for h, m in hours.items():
                    tot += 1
                    cs = {c for _, c in m}
                    if m: any_ += 1; counts.append(len(m))
                    if cs and cs <= {"NEG", "LGT"}: neg += 1
                    if "MOG" in cs: mog += 1
                    if "MOG" in cs and "NEG" in cs: both += 1
        counts.sort(); med = counts[len(counts) // 2] if counts else 0
        print("%-10s %6.0f %5d | %8d %7.1f%% %7.1f%% %7.1f%% %7.1f%% | %d" % (a[1:] + "-" + b[1:], total, nh, tot, 100 * any_ / tot, 100 * neg / tot, 100 * mog / tot, 100 * both / tot, med))
