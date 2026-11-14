"""Decode check: can this machine fetch and decode what the Phase 2 jobs need?

Fetches twelve GFS 0.25 degree messages (one forecast hour) from AWS and the
cruise-level messages of one GTG file from NOMADS, by byte range; decodes them
with eccodes; computes the Ellrod TI1 index; prints timing and peak memory.
Exits non-zero if anything does not look like the recon's measurement.

    python -m flight_brief.jobs.decode_check
"""
from __future__ import annotations

import datetime as dt
import platform
import sys
import time

import numpy as np

from ..forecast import ellrod, grib, sources
from ..forecast.rangeget import fetch_messages

CHECK_FLIGHT_LEVELS = (340,)
CHECK_FORECAST_HOUR = 6


def check_gfs(now: dt.datetime) -> None:
    run = sources.latest_run(now, 6, sources.gfs_url, CHECK_FORECAST_HOUR)
    if run is None:
        sys.exit("no GFS run found on AWS")
    url = sources.gfs_url(run, CHECK_FORECAST_HOUR)
    got = fetch_messages(url, sources.gfs_wants(), pause=0.1)
    print(f"GFS  {url}")
    print(f"     fetched {got.messages} messages, {len(got.data) / 1e6:.1f} MB, "
          f"{got.requests} requests, {got.seconds:.1f} s")

    t0 = time.perf_counter()
    msgs = grib.decode(got.data)
    t_decode = time.perf_counter() - t0
    by_param = {v: k for k, v in sources.GFS_PARAM.items()}
    fields = {(by_param[m.param], m.level): m.values for m in msgs}
    m0 = msgs[0]
    print(f"     decoded {len(msgs)} messages in {t_decode:.2f} s | grid {m0.grid_type} "
          f"{m0.nx}x{m0.ny} | run {m0.data_date} {m0.data_time:04d} +{m0.forecast_time} h")
    assert len(fields) == 12 and (m0.nx, m0.ny) == (1440, 721), "unexpected GFS content"

    t0 = time.perf_counter()
    lines = []
    for (lo, hi), fl in ellrod.LAYERS:
        t = ellrod.ti1(fields[("UGRD", lo)], fields[("VGRD", lo)], fields[("HGT", lo)],
                       fields[("UGRD", hi)], fields[("VGRD", hi)], fields[("HGT", hi)]) * 1e7
        ok = t[np.isfinite(t)]
        p50, p95, p99 = np.percentile(ok, [50, 95, 99])
        lines.append(f"     Ellrod TI1 {lo}-{hi} hPa (about FL{fl}), units 1e-7 s^-2: "
                     f"p50 {p50:.2f}  p95 {p95:.2f}  p99 {p99:.2f}  max {ok.max():.1f} | "
                     f"share >=8: {100 * (ok >= 8).mean():.2f}%  >=12: {100 * (ok >= 12).mean():.2f}%")
        assert 0.1 < p50 < 5 and p99 < 100, "TI1 outside the range the recon saw"
    t_index = time.perf_counter() - t0
    print("\n".join(lines))
    print(f"     index for three layers computed in {t_index:.2f} s")


def check_gtg(now: dt.datetime) -> None:
    run = sources.latest_run(now, 1, sources.gtg_url, sources.GTG_FORECAST_HOURS[-1])
    if run is None:
        sys.exit("no complete GTG run found on NOMADS")
    url = sources.gtg_url(run, CHECK_FORECAST_HOUR)
    params = (sources.GTG_EDR, sources.GTG_CAT, sources.GTG_MWT, sources.GTG_CIT)
    wants = [(sources.GTG_IDX_NAME[p], sources.gtg_idx_level(fl))
             for p in params for fl in CHECK_FLIGHT_LEVELS]
    got = fetch_messages(url, wants)
    print(f"GTG  {url}")
    print(f"     fetched {got.messages} messages, {len(got.data) / 1e6:.1f} MB, "
          f"{got.requests} requests, {got.seconds:.1f} s")

    t0 = time.perf_counter()
    msgs = grib.decode(got.data)
    t_decode = time.perf_counter() - t0
    m0 = msgs[0]
    print(f"     decoded {len(msgs)} messages in {t_decode:.2f} s | grid {m0.grid_type} "
          f"{m0.nx}x{m0.ny} | run {m0.data_date} {m0.data_time:04d} +{m0.forecast_time} h")
    assert {m.param for m in msgs} == set(params), "GTG fields not found by parameter number"
    assert (m0.nx, m0.ny) == (1799, 1059) and m0.grid_type == "lambert", "unexpected GTG grid"
    for m in msgs:
        ok = m.values[np.isfinite(m.values)]
        p50, p99 = np.percentile(ok, [50, 99])
        print(f"     param {m.discipline}-{m.category}-{m.number} at {m.level} m "
              f"({m.type_of_level}): {ok.size} points  p50 {p50:.3f}  p99 {p99:.3f}  "
              f"max {ok.max():.3f}  m^2/3 s^-1")
        assert ok.size > 1e6 and 0 <= ok.min() and ok.max() < 2, "EDR outside a plausible range"


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    print(f"{platform.system()} {platform.machine()} | python {platform.python_version()} | "
          f"{grib.versions()}")
    check_gfs(now)
    check_gtg(now)
    print(f"peak memory {grib.peak_rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
