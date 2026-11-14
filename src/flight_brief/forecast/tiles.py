"""Compact GTG tiles: what the scheduled job saves and how to read it back.

One file per GTG run, a compressed ``.npz`` readable with numpy alone:

``edr``             uint8, shape (forecast hours, flight levels, ny, nx). Combined eddy
                    dissipation rate (GRIB2 parameter 0-19-30) in steps of ``step``
                    m^2/3 s^-1; 255 means no data. Each cell holds the highest value in a
                    ``block`` x ``block`` square of the native 3 km grid, because the brief
                    and the scorecard both ask for the highest value near a point.
``forecast_hours``  the forecast hour of each slice along the first axis
``flight_levels``   the flight level of each slice along the second axis
``meta``            a JSON string: the run, the encoding and the native grid's projection

Row 0 is the southernmost row and column 0 the westernmost, as in the source file.
"""
from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass

import numpy as np

FORMAT = "gtg-tile/1"
STEP = 0.005      # m^2/3 s^-1 per count; 254 counts reach 1.27, above any EDR GTG issues
BLOCK = 4         # 4 x 3 km = 12 km cells
MISSING = 255


def block_max(values: np.ndarray, k: int) -> np.ndarray:
    """Highest value in each k x k block; edge blocks are partial; all-NaN blocks stay NaN."""
    ny, nx = values.shape
    padded = np.pad(values, ((0, -ny % k), (0, -nx % k)), constant_values=np.nan)
    blocks = padded.reshape(padded.shape[0] // k, k, padded.shape[1] // k, k)
    filled = np.where(np.isnan(blocks), -np.inf, blocks).max(axis=(1, 3))
    return np.where(np.isinf(filled), np.nan, filled)


def quantise(values: np.ndarray, step: float = STEP) -> np.ndarray:
    counts = np.clip(np.rint(np.nan_to_num(values, nan=0.0) / step), 0, MISSING - 1)
    return np.where(np.isnan(values), MISSING, counts).astype(np.uint8)


def dequantise(counts: np.ndarray, step: float = STEP) -> np.ndarray:
    return np.where(counts == MISSING, np.nan, counts.astype(np.float64) * step)


def lambert_xy(spec: dict, lat, lon):
    """Fractional (column, row) on the native grid for a latitude and longitude."""
    r = spec["earth_radius_m"]
    p1, p2 = math.radians(spec["lat_1"]), math.radians(spec["lat_2"])
    if abs(p1 - p2) < 1e-9:
        n = math.sin(p1)
    else:
        n = (math.log(math.cos(p1) / math.cos(p2))
             / math.log(math.tan(math.pi / 4 + p2 / 2) / math.tan(math.pi / 4 + p1 / 2)))
    f = math.cos(p1) * math.tan(math.pi / 4 + p1 / 2) ** n / n

    def project(la, lo):
        rho = r * f / np.tan(np.pi / 4 + np.radians(la) / 2) ** n
        theta = n * np.radians((np.asarray(lo) - spec["lon_v"] + 180.0) % 360.0 - 180.0)
        return rho * np.sin(theta), -rho * np.cos(theta)

    x0, y0 = project(spec["lat_first"], spec["lon_first"])
    x, y = project(np.asarray(lat, dtype=float), np.asarray(lon, dtype=float))
    return (x - x0) / spec["dx_m"], (y - y0) / spec["dy_m"]


@dataclass
class Tile:
    edr: np.ndarray            # uint8 (hours, levels, ny, nx)
    forecast_hours: list[int]
    flight_levels: list[int]
    meta: dict

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, edr=self.edr,
                            forecast_hours=np.asarray(self.forecast_hours, dtype=np.int16),
                            flight_levels=np.asarray(self.flight_levels, dtype=np.int16),
                            meta=np.asarray(json.dumps(self.meta, sort_keys=True)))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Tile":
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            if meta.get("format") != FORMAT:
                raise ValueError(f"unknown tile format {meta.get('format')!r}")
            return cls(z["edr"], z["forecast_hours"].tolist(), z["flight_levels"].tolist(), meta)

    def cell(self, lat: float, lon: float) -> tuple[int, int] | None:
        """(row, column) of the tile cell containing a point, or None outside the grid."""
        col, row = lambert_xy(self.meta["grid"], lat, lon)
        i, j = int(np.rint(col)), int(np.rint(row))
        g = self.meta["grid"]
        if not (0 <= i < g["nx"] and 0 <= j < g["ny"]):
            return None
        k = self.meta["block"]
        return j // k, i // k

    def sample(self, lat: float, lon: float, flight_level: int, forecast_hour: int) -> float:
        """EDR in the cell containing the point; NaN outside the grid or where there is no data."""
        rc = self.cell(lat, lon)
        if rc is None:
            return float("nan")
        h = self.forecast_hours.index(forecast_hour)
        lv = self.flight_levels.index(flight_level)
        return float(dequantise(self.edr[h, lv, rc[0], rc[1]], self.meta["step"]))


def tile_name(run) -> str:
    return f"gtg_conus_edr_12km_{run:%Y%m%dT%H}Z.npz"
