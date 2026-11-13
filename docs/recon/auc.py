import sys, csv, numpy as np
rows = list(csv.DictReader(open(sys.argv[1])))
print("rows", len(rows), "| valid times", len({r["valid"] for r in rows}), "| classes", {c: sum(r["cls"] == c for r in rows) for c in ("MOG", "LGT", "NEG")})
def rank(x):
    o = np.argsort(x, kind="mergesort"); r = np.empty(len(x)); xs = x[o]; i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]: j += 1
        r[o[i:j + 1]] = (i + j) / 2 + 1; i = j + 1
    return r
def auc(score, y):
    ok = np.isfinite(score); s, y = score[ok], y[ok]; n1 = y.sum(); n0 = len(y) - n1
    if n1 == 0 or n0 == 0: return float("nan")
    return (rank(s)[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
def ci(score, y, n=400, seed=1):
    rng = np.random.default_rng(seed); k = len(y); v = []
    for _ in range(n):
        i = rng.integers(0, k, k); v.append(auc(score[i], y[i]))
    return np.nanpercentile(v, [2.5, 97.5])
cls = np.array([r["cls"] for r in rows]); lat = np.array([float(r["lat"]) for r in rows]); lon = np.array([float(r["lon"]) for r in rows]); typ = np.array([r["type"] for r in rows]); box = np.array([r["box"] for r in rows])
NAMES = ["ti1", "ti2", "vws", "def", "spd", "invri"]
X = {n + s: np.array([float(r[n + s]) for r in rows]) for n in NAMES for s in ("_pt", "_nb", "_col")}
ens = (rank(X["ti1_nb"]) + rank(X["invri_nb"]) + rank(X["spd_nb"]) + rank(X["def_nb"])) / 4; X["ens4_nb"] = ens
ens2 = (rank(X["ti1_col"]) + rank(X["invri_col"]) + rank(X["spd_col"]) + rank(X["def_col"])) / 4; X["ens4_col"] = ens2
def table(mask, label):
    print("== %s" % label)
    for nulls, nl in ((("NEG",), "MOG vs smooth (NEG)"), (("NEG", "LGT"), "MOG vs NEG+LGT")):
        m = mask & (np.isin(cls, nulls) | (cls == "MOG")); y = (cls[m] == "MOG").astype(int)
        print("  %s: n=%d (MOG %d, null %d)" % (nl, m.sum(), y.sum(), len(y) - y.sum()))
        if y.sum() < 20 or len(y) - y.sum() < 20: print("    too few"); continue
        out = []
        for k in ("ti1_pt", "ti1_nb", "ti1_col", "ti2_nb", "vws_nb", "def_nb", "spd_nb", "invri_nb", "ens4_nb", "ens4_col"):
            a = auc(X[k][m], y); out.append((k, a))
        print("    " + "  ".join("%s %.3f" % (k, a) for k, a in out))
        for k in ("ti1_nb", "ens4_nb"):
            lo, hi = ci(X[k][m], y); print("    %s 95%% bootstrap interval %.3f to %.3f" % (k, lo, hi))
allm = np.ones(len(rows), bool)
table(allm, "all matched cruise reports (FL290-440), lead 24-27 h, +/-90 min")
usw = (box == "epac") & (lon >= -125) & (lon <= -110) & (lat >= 30) & (lat <= 50)
table(usw, "US West land strip (125W-110W, 30-50N)")
table(~usw, "everything else (oceanic Pacific, Alaska, Japan side)")
table(typ == "AIREP", "AIREPs only"); table(typ != "AIREP", "PIREPs only")
# operating point for TI1 neighbourhood value, MOG vs NEG
m = np.isin(cls, ("NEG", "MOG")); y = cls[m] == "MOG"; s = X["ti1_nb"][m]
print("== operating points, ti1_nb (1e-7 s^-2), MOG vs NEG")
for th in (2, 4, 6, 8, 12, 16):
    print("  threshold %2d: hit rate on MOG %.2f | false-alarm rate on smooth %.2f" % (th, (s[y] >= th).mean(), (s[~y] >= th).mean()))
print("  median ti1_nb: MOG %.2f, LGT %.2f, NEG %.2f" % (np.nanmedian(X["ti1_nb"][cls == "MOG"]), np.nanmedian(X["ti1_nb"][cls == "LGT"]), np.nanmedian(X["ti1_nb"][cls == "NEG"])))
