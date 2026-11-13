import pickle, sys, collections, datetime as dt
recs, meta = pickle.load(open(sys.argv[1], "rb"))
POS = {"LGT": 1, "LGT-MOD": 2, "MOD": 3, "MOD-SEV": 4, "SEV": 5, "SEV-EXTM": 6, "EXTM": 6}
def cls(r):
    a = r.get("tbInt1") or ""
    if a == "NEG": return "NEG"
    if a in POS: return "MOG" if POS[a] >= 3 else "LGT"
    return None
def region(r):
    la, lo = r.get("lat"), r.get("lon")
    if la is None or lo is None: return "no position"
    if r["_box"] == "indian": return "Indian Ocean box"
    if r["_box"] == "epac":
        if -125 <= lo <= -110 and 30 <= la <= 50: return "US West (land, 125W-110W, 30-50N)"
        if lo <= -130 and la >= 50: return "Alaska / Gulf of Alaska (>=50N, W of 130W)"
        if -165 <= lo <= -150 and 15 <= la <= 25: return "Hawaii (15-25N, 165-150W)"
        if la >= 20: return "NE Pacific oceanic (>=20N, rest)"
        if la >= -10: return "Tropical E/C Pacific (10S-20N)"
        return "South Pacific E of 180 (<10S)"
    if r["_box"] == "wpac":
        if la >= 25 and lo >= 125: return "Japan / NW Pacific (>=25N, E of 125E)"
        if la >= 25: return "China / Korea side (>=25N, W of 125E)"
        if la >= -10: return "SE Asia / W tropical Pacific (10S-25N)"
        if lo >= 160: return "NZ / Tasman E of 160E (<10S)"
        return "Australia (<10S, W of 160E)"
    return "other"
days = len(meta["days"])
T = collections.defaultdict(lambda: collections.Counter())
for r in recs:
    g = region(r); c = cls(r); fl = r.get("fltLvl")
    T[g]["all"] += 1
    T[g][r.get("pirepType")] += 1
    if c: T[g]["tb"] += 1; T[g][c] += 1
    if c and fl is not None and fl >= 250:
        T[g]["tb_cruise"] += 1; T[g][c + "_cruise"] += 1
    if fl is not None and fl >= 250: T[g]["cruise_all"] += 1
tot = collections.Counter()
hdr = "%-46s %7s %7s %6s | %6s %5s %5s %5s %6s | %6s %5s %5s %5s %6s %7s" % ("region", "all", "AIREP", "PIREP", "tb", "LGT", "MOG", "NEG", "NEG%", "tb>=FL250", "LGT", "MOG", "NEG", "NEG%", "per day")
print(hdr)
for g, c in sorted(T.items(), key=lambda x: -x[1]["tb_cruise"]):
    for k, v in c.items(): tot[k] += v
    print("%-46s %7d %7d %6d | %6d %5d %5d %5d %5.1f%% | %9d %5d %5d %5d %5.1f%% %7.1f" % (g, c["all"], c["AIREP"], c["PIREP"] + c["Urgent PIREP"], c["tb"], c["LGT"], c["MOG"], c["NEG"], 100 * c["NEG"] / max(1, c["tb"]), c["tb_cruise"], c["LGT_cruise"], c["MOG_cruise"], c["NEG_cruise"], 100 * c["NEG_cruise"] / max(1, c["tb_cruise"]), c["tb_cruise"] / days))
c = tot
print("%-46s %7d %7d %6d | %6d %5d %5d %5d %5.1f%% | %9d %5d %5d %5d %5.1f%% %7.1f" % ("TOTAL", c["all"], c["AIREP"], c["PIREP"] + c["Urgent PIREP"], c["tb"], c["LGT"], c["MOG"], c["NEG"], 100 * c["NEG"] / max(1, c["tb"]), c["tb_cruise"], c["LGT_cruise"], c["MOG_cruise"], c["NEG_cruise"], 100 * c["NEG_cruise"] / max(1, c["tb_cruise"]), c["tb_cruise"] / days))
print("days in store:", days)
# who files the turbulence-bearing cruise reports
src = collections.Counter(); typ = collections.Counter(); flh = collections.Counter(); hod = collections.Counter(); carriers = collections.Counter()
for r in recs:
    if cls(r) and (r.get("fltLvl") or 0) >= 250:
        src[r.get("icaoId")] += 1; typ[r.get("pirepType")] += 1; flh[(r["fltLvl"] // 20) * 20] += 1
        hod[dt.datetime.fromtimestamp(r["obsTime"], dt.timezone.utc).hour] += 1
        if r.get("pirepType") == "AIREP":
            w = r["rawOb"].split(); cs = w[1] if w and w[0] == "ARP" and len(w) > 1 else (w[0] if w else "")
            carriers["".join(ch for ch in cs if ch.isalpha())[:3]] += 1
print("tb cruise by type", typ.most_common()); print("by office", src.most_common(8)); print("AIREP carriers", carriers.most_common(10))
print("by FL band", sorted(flh.items())); print("by UTC hour", [hod[h] for h in range(24)])
# 10-degree cells
cell = collections.Counter(); celln = collections.Counter(); cellm = collections.Counter()
for r in recs:
    c = cls(r)
    if c and (r.get("fltLvl") or 0) >= 250 and r.get("lat") is not None:
        k = (int(r["lat"] // 10) * 10, int(r["lon"] // 10) * 10); cell[k] += 1
        if c == "NEG": celln[k] += 1
        if c == "MOG": cellm[k] += 1
print("10-degree cells with turbulence-bearing cruise reports:", len(cell), "| cells holding 50%:", end=" ")
s = 0; n = 0
for k, v in cell.most_common():
    s += v; n += 1
    if s >= 0.5 * sum(cell.values()): break
print(n, "| holding 90%:", end=" ")
s = 0; n = 0
for k, v in cell.most_common():
    s += v; n += 1
    if s >= 0.9 * sum(cell.values()): break
print(n)
print("top cells (lat0,lon0): n, MOG, NEG, per day")
for k, v in cell.most_common(14): print("  ", k, v, cellm[k], celln[k], "%.2f" % (v / days))
