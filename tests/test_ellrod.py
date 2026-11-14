import numpy as np

from flight_brief.forecast.ellrod import EARTH_RADIUS_M, ti1

NJ, NI = 181, 360
LAT = np.linspace(90, -90, NJ)[:, None] * np.ones((1, NI))


def _flat(value):
    return np.full((NJ, NI), float(value))


def test_uniform_shear_without_deformation_is_zero():
    # The wind differs between the two levels, but each level is the same everywhere.
    t = ti1(_flat(10), _flat(0), _flat(9000), _flat(30), _flat(0), _flat(10000))
    assert np.allclose(t[10:-10], 0.0, atol=1e-20)


def test_solid_body_rotation_matches_the_closed_form():
    # u = U cos(lat), v = 0 in the layer mean. The scheme's shearing term is
    # du/dy = -U sin(lat) / a, and stretching is zero, so TI1 = shear * U |sin(lat)| / a.
    U, dz, du = 40.0, 1500.0, 15.0
    u_mean = U * np.cos(np.deg2rad(LAT))
    t = ti1(u_mean - du / 2, _flat(0), _flat(9000), u_mean + du / 2, _flat(0), _flat(9000 + dz))
    want = (du / dz) * U * np.abs(np.sin(np.deg2rad(LAT))) / EARTH_RADIUS_M
    mid = slice(10, -10)
    assert np.allclose(t[mid], want[mid], rtol=1e-3)


def test_polar_rows_are_not_a_number():
    t = ti1(_flat(10), _flat(0), _flat(9000), _flat(30), _flat(0), _flat(10000))
    assert np.isnan(t[0]).all() and np.isnan(t[-1]).all() and np.isfinite(t[NJ // 2]).all()
