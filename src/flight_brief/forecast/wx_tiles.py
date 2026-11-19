"""Compact wind and storm-cloud tiles: saved by the wind-shear job beside its own tile.

One file per GFS run, a compressed ``.npz`` readable with numpy alone:

``u``, ``v``        int8, shape (forecast hours, layers, 181, 360). Wind toward the east and
                    toward the north, in steps of ``wind_step_kt`` knots; -128 means no data.
                    Each layer is the mean of the two pressure levels the wind-shear index
                    uses for it, taken at every whole degree of GFS's 0.25 degree grid.
``top``             uint8, shape (forecast hours, 361, 720). The highest convective cloud top
                    within 0.5 degree, as a flight level in steps of ``top_step_fl``; 0 means
                    the model has no convective cloud there; 255 means no data (the field was
                    not in the run).
``forecast_hours``  the forecast hour of each slice along the first axis
``layer_fls``       the flight level each wind layer stands for
``meta``            a JSON string: the run, the encodings and the grids

Both grids start at 90N 0E, rows run south, columns run east and wrap.

**What the cloud top is.** GFS's "pressure at the convective cloud top level": how high the
model's own storm clouds reach in a 0.25 degree cell. It is a model field at coarse
resolution: it says where the model expects deep convection, not where a storm cell will be.
The pressure is turned into a flight level with the standard atmosphere, the way an altimeter
set to 1013 hPa would.

**Neighbourhood.** As in the wind-shear tile: the highest value among the 5 x 5 grid points
of the 0.25 degree grid centred on a point (0.5 degree either side). Every second point is
kept, so a reader takes the nearest 0.5 degree point and sees tops up to 0.5 to 0.75 degree
away. Winds are not spread: a reader takes the nearest whole-degree point.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
from dataclasses import dataclass

import numpy as np

from .shear_tiles import LAYER_FLS, MAX_TIME_GAP_H, NI, NJ, nearest_layer, neighbourhood_max

FORMAT = "wx-tile/1"
KT_PER_MS = 1.943844
WIND_STEP_KT = 2.0     # 127 counts reach 254 kt, above any wind GFS has at these levels
WIND_MISSING = -128
WIND_EVERY = 4         # every fourth point of the 0.25 degree grid: whole degrees
WIND_DEG = 1.0
WNJ, WNI = (NJ - 1) // WIND_EVERY + 1, NI // WIND_EVERY
TOP_STEP_FL = 5        # 254 counts would reach FL1270; real tops stay under FL600
TOP_NONE = 0
TOP_MISSING = 255
TOP_EVERY = 2          # every second point: 0.5 degree
TOP_DEG = 0.5
TNJ, TNI = (NJ - 1) // TOP_EVERY + 1, NI // TOP_EVERY

_NAME = re.compile(r"^gfs_global_wx_(\d{8}T\d{2})Z\.npz$")


def pressure_to_fl(pa: np.ndarray) -> np.ndarray:
    """Flight level (hundreds of feet) of a pressure in Pa, in the standard atmosphere."""
    hpa = np.asarray(pa, dtype=np.float64) / 100.0
    with np.errstate(invalid="ignore", divide="ignore"):
        below = 145366.45 * (1.0 - (hpa / 1013.25) ** 0.190284)             # up to 11 km
        above = 36089.24 + 20805.8 * np.log(226.32 / hpa)                   # isothermal layer
    return np.where(hpa > 226.32, below, above) / 100.0


def quantise_wind(ms: np.ndarray) -> np.ndarray:
    """m/s on the 0.25 degree grid -> counts of 2 kt at whole degrees, nearest count."""
    kt = ms[::WIND_EVERY, ::WIND_EVERY] * KT_PER_MS
    counts = np.clip(np.rint(np.nan_to_num(kt, nan=0.0) / WIND_STEP_KT), -127, 127)
    return np.where(np.isnan(kt), WIND_MISSING, counts).astype(np.int8)


def quantise_top(pa: np.ndarray) -> np.ndarray:
    """Convective cloud top pressure (Pa, NaN where there is no such cloud) on the 0.25
    degree grid -> counts of FL5 at 0.5 degree, each the highest top within 0.5 degree.
    Rounded to the nearest count; a real top is never stored as 0."""
    fl = np.where(np.isnan(pa), 0.0, pressure_to_fl(np.where(np.isnan(pa), 101325.0, pa)))
    near = neighbourhood_max(fl)[::TOP_EVERY, ::TOP_EVERY]
    counts = np.clip(np.rint(near / TOP_STEP_FL), 1, TOP_MISSING - 1)
    return np.where(near <= 0, TOP_NONE, counts).astype(np.uint8)


@dataclass
class WxTile:
    u: np.ndarray              # int8 (hours, layers, 181, 360)
    v: np.ndarray
    top: np.ndarray            # uint8 (hours, 361, 720)
    forecast_hours: list[int]
    layer_fls: list[int]
    meta: dict

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, u=self.u, v=self.v, top=self.top,
                            forecast_hours=np.asarray(self.forecast_hours, dtype=np.int16),
                            layer_fls=np.asarray(self.layer_fls, dtype=np.int16),
                            meta=np.asarray(json.dumps(self.meta, sort_keys=True)))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "WxTile":
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            if meta.get("format") != FORMAT:
                raise ValueError(f"unknown tile format {meta.get('format')!r}")
            return cls(z["u"], z["v"], z["top"], z["forecast_hours"].tolist(),
                       z["layer_fls"].tolist(), meta)

    @property
    def run(self) -> dt.datetime:
        return dt.datetime.strptime(self.meta["run"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)

    def _hour(self, valid_time: dt.datetime) -> int | None:
        if valid_time.tzinfo is None:
            raise ValueError("valid_time needs a time zone")
        lead = (valid_time - self.run).total_seconds() / 3600
        h = min(range(len(self.forecast_hours)), key=lambda q: abs(self.forecast_hours[q] - lead))
        return h if abs(self.forecast_hours[h] - lead) <= MAX_TIME_GAP_H else None

    def wind(self, lat: float, lon: float, flight_level: float,
             valid_time: dt.datetime) -> tuple[float, float, int, int] | None:
        """(toward east kt, toward north kt, layer flight level, forecast hour), or None.

        The whole-degree point nearest the position, the layer nearest ``flight_level`` and
        the forecast hour nearest ``valid_time``; None when no hour is within 1.5 h or the
        point has no data.
        """
        h = self._hour(valid_time)
        if h is None or not abs(lat) <= 90:
            return None
        layer = nearest_layer(flight_level, self.layer_fls)
        row = int(round((90 - lat) / WIND_DEG))
        col = int(round((lon % 360) / WIND_DEG)) % WNI
        u, v = int(self.u[h, layer, row, col]), int(self.v[h, layer, row, col])
        if WIND_MISSING in (u, v):
            return None
        step = self.meta["wind_step_kt"]
        return u * step, v * step, self.layer_fls[layer], self.forecast_hours[h]

    def storm_top(self, lat: float, lon: float,
                  valid_time: dt.datetime) -> tuple[int | None, int] | None:
        """(highest convective cloud top within about 0.5 degree as a flight level, forecast
        hour). The flight level is None where the model has no convective cloud. The whole
        result is None when no hour is within 1.5 h or the run carried no such field."""
        h = self._hour(valid_time)
        if h is None or not abs(lat) <= 90:
            return None
        row = int(round((90 - lat) / TOP_DEG))
        col = int(round((lon % 360) / TOP_DEG)) % TNI
        count = int(self.top[h, row, col])
        if count == TOP_MISSING:
            return None
        return (None if count == TOP_NONE else count * self.meta["top_step_fl"]), self.forecast_hours[h]


def tile_name(run: dt.datetime) -> str:
    return f"gfs_global_wx_{run:%Y%m%dT%H}Z.npz"


def run_of(name: str) -> dt.datetime:
    m = _NAME.match(name)
    if not m:
        raise ValueError(f"not a wind and storm-cloud tile name: {name!r}")
    return dt.datetime.strptime(m.group(1), "%Y%m%dT%H").replace(tzinfo=dt.timezone.utc)


def empty(hours: int, layers: int = len(LAYER_FLS)) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (np.full((hours, layers, WNJ, WNI), WIND_MISSING, np.int8),
            np.full((hours, layers, WNJ, WNI), WIND_MISSING, np.int8),
            np.full((hours, TNJ, TNI), TOP_MISSING, np.uint8))
