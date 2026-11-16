"""Compact wind-shear index tiles: what the scheduled job saves and how to read it back.

One file per GFS run, a compressed ``.npz`` readable with numpy alone:

``ti1``             uint8, shape (forecast hours, layers, 721, 1440). Ellrod TI1 (vertical
                    wind shear x deformation) in steps of ``step`` x 1e-7 s^-2; 255 means no
                    data. Each grid point holds the highest value in its neighbourhood (see
                    below), so a reader takes ONE value at the grid point nearest its position.
``forecast_hours``  the forecast hour of each slice along the first axis
``layer_fls``       the flight level each slice along the second axis stands for
``meta``            a JSON string: the run, the encoding, the layers and the grid

The grid is GFS's global 0.25 degree grid: row 0 is 90N and rows run south, column 0 is 0E
and columns run east and wrap.

**Neighbourhood.** "Within 0.5 degree" means what the recon measured
(``docs/recon/fallback_skill.py``, its ``_nb`` value): take the grid point nearest the position,
then the highest value among the 5 x 5 grid points centred on it, that is 2 points (0.5 degree)
either side in latitude and in longitude, both ends included, wrapping in longitude, ignoring
points with no data. It is a square in latitude and longitude, not a circle of distance.
Measured from a grid point the reach is exactly 0.5 degree along each axis (0.75 is outside).
Measured from a position between grid points it is 0.5 degree give or take half a grid step,
because the position goes to its nearest grid point first: a value up to 0.375 degree away
along an axis is always found, one more than 0.625 degree away never.

**Quantisation.** Counts are rounded DOWN, so for any threshold that is a whole number of steps
(8 and 12 are), "stored value >= threshold" is true exactly when the unrounded value is.
Values of 127 and above are stored as 127 (count 254); nothing else clips. The index is a
wind-shear index with no severity calibration, and a tile never carries one.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
from dataclasses import dataclass

import numpy as np

from .ellrod import LAYERS

FORMAT = "shear-tile/1"
UNITS = "1e-7 s^-2"
STEP = 0.5          # x 1e-7 s^-2 per count; 254 counts reach 127
MISSING = 255
REACH = 2           # grid points either side: 2 x 0.25 = 0.5 degree
GRID_DEG = 0.25
NJ, NI = 721, 1440
MAX_LAT = 85.0      # the index is not computed poleward of this (see ellrod.py)
MAX_TIME_GAP_H = 1.5
FORECAST_HOURS = tuple(range(6, 55, 3))
LAYER_FLS = tuple(fl for _, fl in LAYERS)

_NAME = re.compile(r"^gfs_global_ti1_0p25_(\d{8}T\d{2})Z\.npz$")


def neighbourhood_max(values: np.ndarray, reach: int = REACH) -> np.ndarray:
    """Highest value among the (2 reach + 1)^2 grid points centred on each point.

    Columns wrap (longitude); rows do not. Points with no data (NaN) are ignored; a
    neighbourhood with no data at all stays NaN.
    """
    rows = values.shape[0]
    padded = np.pad(values, ((reach, reach), (0, 0)), constant_values=np.nan)
    out = values.copy()
    for d in range(2 * reach + 1):
        if d != reach:
            out = np.fmax(out, padded[d:d + rows])
    along = out
    for d in range(1, reach + 1):
        out = np.fmax(out, np.fmax(np.roll(along, d, 1), np.roll(along, -d, 1)))
    return out


def quantise(values: np.ndarray, step: float = STEP) -> np.ndarray:
    """Counts of ``step``, rounded down; 254 is the top count; NaN becomes 255."""
    counts = np.clip(np.floor(np.nan_to_num(values, nan=0.0) / step), 0, MISSING - 1)
    return np.where(np.isnan(values), MISSING, counts).astype(np.uint8)


def dequantise(counts: np.ndarray, step: float = STEP) -> np.ndarray:
    return np.where(counts == MISSING, np.nan, counts.astype(np.float64) * step)


def grid_index(lat: float, lon: float) -> tuple[int, int]:
    """(row, column) of the grid point nearest a position, found as the recon found it."""
    return int(round((90 - lat) / GRID_DEG)), int(round((lon % 360) / GRID_DEG)) % NI


def nearest_layer(flight_level: float, layer_fls=LAYER_FLS) -> int:
    """Index of the layer whose flight level is nearest; the lower one on a tie."""
    return min(range(len(layer_fls)), key=lambda q: abs(layer_fls[q] - flight_level))


@dataclass
class ShearTile:
    ti1: np.ndarray            # uint8 (hours, layers, 721, 1440)
    forecast_hours: list[int]
    layer_fls: list[int]
    meta: dict

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, ti1=self.ti1,
                            forecast_hours=np.asarray(self.forecast_hours, dtype=np.int16),
                            layer_fls=np.asarray(self.layer_fls, dtype=np.int16),
                            meta=np.asarray(json.dumps(self.meta, sort_keys=True)))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "ShearTile":
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            if meta.get("format") != FORMAT:
                raise ValueError(f"unknown tile format {meta.get('format')!r}")
            return cls(z["ti1"], z["forecast_hours"].tolist(), z["layer_fls"].tolist(), meta)

    @property
    def run(self) -> dt.datetime:
        return dt.datetime.strptime(self.meta["run"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)

    def sample(self, lat: float, lon: float, flight_level: float,
               valid_time: dt.datetime) -> tuple[float, int, int] | None:
        """(value, layer flight level, forecast hour) for a position and time, or None.

        ``value`` is the highest index within 0.5 degree (see the module text), in units of
        1e-7 s^-2, from the layer whose flight level is nearest ``flight_level`` and the
        forecast hour nearest ``valid_time``. None when no forecast hour is within 1.5 h of
        ``valid_time`` (1.5 h itself counts; of two equally near hours the earlier is used),
        when the latitude is poleward of 85 degrees, or when the cell has no data. Longitude
        may be anything from -180 to 360. ``valid_time`` must carry a time zone.
        """
        if valid_time.tzinfo is None:
            raise ValueError("valid_time needs a time zone")
        if not abs(lat) <= MAX_LAT:
            return None
        lead = (valid_time - self.run).total_seconds() / 3600
        h = min(range(len(self.forecast_hours)), key=lambda q: abs(self.forecast_hours[q] - lead))
        if abs(self.forecast_hours[h] - lead) > MAX_TIME_GAP_H:
            return None
        layer = nearest_layer(flight_level, self.layer_fls)
        row, col = grid_index(lat, lon)
        count = int(self.ti1[h, layer, row, col])
        if count == MISSING:
            return None
        return count * self.meta["step"], self.layer_fls[layer], self.forecast_hours[h]


def tile_name(run: dt.datetime) -> str:
    return f"gfs_global_ti1_0p25_{run:%Y%m%dT%H}Z.npz"


def run_of(name: str) -> dt.datetime:
    """The run a tile file name stands for; ValueError if it is not a tile name."""
    m = _NAME.match(name)
    if not m:
        raise ValueError(f"not a wind-shear tile name: {name!r}")
    return dt.datetime.strptime(m.group(1), "%Y%m%dT%H").replace(tzinfo=dt.timezone.utc)
