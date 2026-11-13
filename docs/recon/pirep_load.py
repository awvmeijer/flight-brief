import gzip, json, os, sys, collections, pickle
D = os.environ["PIREP_SNAPSHOTS"]  # the oceanic-flight-watch store: <repo>/data/snapshots/pirep
def load():
    seen = {}; files = 0; errs = 0; raw = 0; per_box_raw = collections.Counter(); origins = collections.Counter(); statuses = collections.Counter()
    days = sorted(os.listdir(D))
    for day in days:
        for fn in sorted(os.listdir(os.path.join(D, day))):
            if not fn.endswith(".json.gz"): continue
            try: o = json.load(gzip.open(os.path.join(D, day, fn)))
            except Exception as e: errs += 1; continue
            files += 1; origins[o.get("origin")] += 1; statuses[o.get("status")] += 1
            box = fn.split("_")[-1].split(".")[0]
            for r in (o.get("payload") or []):
                raw += 1; per_box_raw[box] += 1
                k = (r.get("obsTime"), r.get("rawOb"))
                if k not in seen:
                    r["_box"] = box; seen[k] = r
    return list(seen.values()), dict(files=files, errs=errs, raw=raw, per_box_raw=per_box_raw, origins=origins, statuses=statuses, days=days)
if __name__ == "__main__":
    recs, meta = load()
    pickle.dump((recs, meta), open(sys.argv[1], "wb"))
    print("files", meta["files"], "unreadable", meta["errs"], "raw records", meta["raw"], "unique", len(recs))
    print("origins", dict(meta["origins"]), "statuses", dict(meta["statuses"]))
    print("raw per box", dict(meta["per_box_raw"]))
    print("days", meta["days"][0], "..", meta["days"][-1], len(meta["days"]))
