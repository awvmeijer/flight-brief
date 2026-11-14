"""Ellrod TI1 turbulence index (Ellrod and Knapp, 1992) on a global lat-lon grid.

TI1 = vertical wind shear x horizontal deformation, in s^-2. The recon and the
page quote it in units of 1e-7 s^-2. It is a wind-shear index, not an eddy
dissipation rate: it has no severity calibration.

The finite differences are the ones the recon's skill measurement used
(docs/recon/fallback_skill.py), so its hit and false-flag rates carry over.
"""
from __future__ import annotations

import numpy as np

EARTH_RADIUS_M = 6.371e6

# (lower level, upper level) in hPa, and the flight level each layer stands for.
LAYERS = (((300, 250), 320), ((250, 200), 363), ((200, 150), 415))


def _derivatives(nj: int, ni: int):
    lat = np.linspace(90.0, -90.0, nj)
    cos = np.cos(np.deg2rad(lat))[:, None]
    cos = np.where(np.abs(lat)[:, None] > 85, np.nan, cos)  # the polar rows are not usable
    dlam = np.deg2rad(360.0 / ni)
    dphi = np.deg2rad(180.0 / (nj - 1))

    def ddx(f):
        return (np.roll(f, -1, 1) - np.roll(f, 1, 1)) / (2 * EARTH_RADIUS_M * cos * dlam)

    def ddy(f):  # rows run north to south
        return -np.gradient(f, axis=0) / (EARTH_RADIUS_M * dphi)

    return ddx, ddy


def ti1(u_lo, v_lo, gh_lo, u_hi, v_hi, gh_hi) -> np.ndarray:
    """TI1 in s^-2 for the layer between two pressure levels.

    Arguments are (nj, ni) arrays on a global grid whose rows run from 90N to
    90S and whose columns wrap in longitude: winds in m/s, geopotential height
    in m. ``lo`` is the lower level (higher pressure). Rows poleward of 85
    degrees are NaN.
    """
    nj, ni = u_lo.shape
    ddx, ddy = _derivatives(nj, ni)
    u = 0.5 * (u_lo + u_hi)
    v = 0.5 * (v_lo + v_hi)
    shear = np.hypot(u_hi - u_lo, v_hi - v_lo) / (gh_hi - gh_lo)
    stretching = ddx(u) - ddy(v)
    shearing = ddx(v) + ddy(u)
    return shear * np.hypot(stretching, shearing)
