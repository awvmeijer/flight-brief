"""GRIB2 decoding with eccodes."""
from __future__ import annotations

import resource
import sys
from dataclasses import dataclass

import eccodes as ec
import numpy as np

_KEYS = ("discipline", "parameterCategory", "parameterNumber", "typeOfLevel", "level",
         "gridType", "dataDate", "dataTime", "forecastTime")


@dataclass
class Message:
    discipline: int
    category: int
    number: int
    type_of_level: str
    level: int
    grid_type: str
    nx: int
    ny: int
    data_date: int      # YYYYMMDD
    data_time: int      # HHMM
    forecast_time: int  # hours, for the files this project reads
    values: np.ndarray  # shape (ny, nx), float64, NaN where missing
    handle_bytes: bytes

    @property
    def param(self) -> tuple[int, int, int]:
        """(discipline, category, number): the only reliable way to name a field.

        eccodes has no short name for several GTG parameters (0-19-30, 0-19-31,
        0-19-50 decode as ``unknown``), so nothing here selects by short name.
        """
        return (self.discipline, self.category, self.number)


def split_messages(data: bytes) -> list[bytes]:
    """Split concatenated GRIB2 messages using the length in each header."""
    out, pos = [], 0
    while pos < len(data):
        if data[pos:pos + 4] != b"GRIB":
            raise ValueError(f"no GRIB header at byte {pos}")
        if data[pos + 7] != 2:
            raise ValueError(f"GRIB edition {data[pos + 7]} at byte {pos}, expected 2")
        length = int.from_bytes(data[pos + 8:pos + 16], "big")
        out.append(data[pos:pos + length])
        pos += length
    return out


def decode(data: bytes) -> list[Message]:
    out = []
    for raw in split_messages(data):
        gid = ec.codes_new_from_message(raw)
        try:
            k = {key: ec.codes_get(gid, key) for key in _KEYS}
            if k["gridType"] == "regular_ll":
                nx, ny = ec.codes_get(gid, "Ni"), ec.codes_get(gid, "Nj")
            else:
                nx, ny = ec.codes_get(gid, "Nx"), ec.codes_get(gid, "Ny")
            ec.codes_set(gid, "missingValue", 1e30)
            values = ec.codes_get_values(gid).reshape(ny, nx)
            values[values >= 1e29] = np.nan  # bitmap gaps
        finally:
            ec.codes_release(gid)
        out.append(Message(k["discipline"], k["parameterCategory"], k["parameterNumber"],
                           k["typeOfLevel"], k["level"], k["gridType"], nx, ny,
                           k["dataDate"], k["dataTime"], k["forecastTime"], values, raw))
    return out


def grid_latlon(msg: Message) -> tuple[np.ndarray, np.ndarray]:
    """Latitude and longitude of every grid point, shape (ny, nx), longitude in -180..180."""
    gid = ec.codes_new_from_message(msg.handle_bytes)
    try:
        lat = ec.codes_get_array(gid, "latitudes").reshape(msg.ny, msg.nx)
        lon = ec.codes_get_array(gid, "longitudes").reshape(msg.ny, msg.nx)
    finally:
        ec.codes_release(gid)
    return lat, (lon + 180.0) % 360.0 - 180.0


def versions() -> str:
    return (f"eccodes lib {ec.codes_get_api_version()} | python binding "
            f"{getattr(ec, '__version__', '?')} | numpy {np.__version__}")


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024 if sys.platform == "darwin" else 1024)
