"""Where the grids are, and which messages a job wants from them."""
from __future__ import annotations

import datetime as dt
import urllib.error

from .rangeget import GFS_AWS, NOMADS, http_get

# --- GTG: Graphical Turbulence Guidance, contiguous US, 3 km Lambert grid --------------------

# GRIB2 parameter numbers, discipline 0, category 19 (NCEP table 4.2-0-19).
GTG_MWT = (0, 19, 28)   # mountain-wave turbulence (EDR)
GTG_CAT = (0, 19, 29)   # clear-air turbulence (EDR)
GTG_EDR = (0, 19, 30)   # combined eddy dissipation rate
GTG_CIT = (0, 19, 50)   # convectively induced turbulence (EDR)

# How wgrib2 spells each one in the .idx file. The .idx is only used to find
# byte ranges; after decoding, fields are checked by number (see grib.Message.param).
GTG_IDX_NAME = {
    GTG_MWT: "MWTURB",
    GTG_CAT: "CATEDR",
    GTG_EDR: "EDPARM",
    GTG_CIT: "var discipline=0 master_table=2 parmcat=19 parm=50",
}

GTG_FORECAST_HOURS = range(0, 19)


def gtg_level_m(flight_level: int) -> int:
    """Height above mean sea level, in whole metres, as the file labels it."""
    return int(flight_level * 100 * 0.3048)


def gtg_idx_level(flight_level: int) -> str:
    return f"{gtg_level_m(flight_level)} m above mean sea level"


def gtg_url(run: dt.datetime, forecast_hour: int) -> str:
    return (f"{NOMADS}/dafs/prod/dafs.{run:%Y%m%d}/"
            f"dafs.t{run:%H}z.gtg.3km.conus.f{forecast_hour:03d}.grib2")


# --- GFS 0.25 degree pressure levels, from NOAA's public AWS bucket --------------------------

GFS_LEVELS_HPA = (300, 250, 200, 150)
GFS_VARS = ("UGRD", "VGRD", "HGT")
GFS_PARAM = {"UGRD": (0, 2, 2), "VGRD": (0, 2, 3), "HGT": (0, 3, 5)}


def gfs_url(run: dt.datetime, forecast_hour: int) -> str:
    return (f"{GFS_AWS}/gfs.{run:%Y%m%d}/{run:%H}/atmos/"
            f"gfs.t{run:%H}z.pgrb2.0p25.f{forecast_hour:03d}")


def gfs_wants() -> list[tuple[str, str]]:
    return [(v, f"{lv} mb") for v in GFS_VARS for lv in GFS_LEVELS_HPA]


# --- Finding a run that is fully posted -----------------------------------------------------

def exists(url: str) -> bool:
    try:
        http_get(url, (0, 0), attempts=1, timeout=30)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return False
        raise


def latest_run(now: dt.datetime, step_hours: int, url_of, last_hour: int, *, look_back: int = 6):
    """Newest run whose last forecast hour has an index file, or None."""
    run = now.replace(minute=0, second=0, microsecond=0)
    run -= dt.timedelta(hours=run.hour % step_hours)
    for _ in range(look_back):
        if exists(url_of(run, last_hour) + ".idx"):
            return run
        run -= dt.timedelta(hours=step_hours)
    return None
