"""Byte-range fetch of selected GRIB2 messages using a wgrib2-style .idx file.
usage: rangeget.py <base_url_without_.idx> <out_file> <VAR:level> [<VAR:level> ...]
"""
import sys, time, urllib.request
UA = "flight-brief-recon/0.1 (research; keyless probe)"
base, out, wants = sys.argv[1], sys.argv[2], [w.split(":", 1) for w in sys.argv[3:]]
def get(url, rng=None):
    h = {"User-Agent": UA}
    if rng: h["Range"] = "bytes=%d-%s" % (rng[0], "" if rng[1] is None else rng[1])
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120).read()
t0 = time.time()
idx = [l.split(":") for l in get(base + ".idx").decode().splitlines() if l.strip()]
offs = [int(r[1]) for r in idx] + [None]
total = 0; n = 0
with open(out, "wb") as f:
    for i, r in enumerate(idx):
        if any(r[3] == v and r[4] == lv for v, lv in wants):
            end = None if offs[i + 1] is None else offs[i + 1] - 1
            b = get(base, (offs[i], end)); f.write(b); total += len(b); n += 1
            time.sleep(0.6)
print("messages=%d bytes=%d seconds=%.1f" % (n, total, time.time() - t0))
