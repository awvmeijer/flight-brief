import eccodes as ec
import numpy as np
import pytest

from flight_brief.forecast import grib


def _message(category, number, level, values, missing_at=None):
    """A small GRIB2 message built from the sample that ships with eccodes."""
    gid = ec.codes_grib_new_from_samples("regular_ll_pl_grib2")
    try:
        ec.codes_set(gid, "Ni", 4)
        ec.codes_set(gid, "Nj", 3)
        ec.codes_set(gid, "latitudeOfFirstGridPointInDegrees", 10.0)
        ec.codes_set(gid, "latitudeOfLastGridPointInDegrees", 8.0)
        ec.codes_set(gid, "longitudeOfFirstGridPointInDegrees", 250.0)
        ec.codes_set(gid, "longitudeOfLastGridPointInDegrees", 253.0)
        ec.codes_set(gid, "iDirectionIncrementInDegrees", 1.0)
        ec.codes_set(gid, "jDirectionIncrementInDegrees", 1.0)
        ec.codes_set(gid, "discipline", 0)
        ec.codes_set(gid, "parameterCategory", category)
        ec.codes_set(gid, "parameterNumber", number)
        ec.codes_set(gid, "level", level)
        ec.codes_set(gid, "dataDate", 20261114)
        ec.codes_set(gid, "dataTime", 1000)
        ec.codes_set(gid, "forecastTime", 6)
        ec.codes_set(gid, "bitsPerValue", 16)
        values = np.asarray(values, dtype=float).copy()
        if missing_at is not None:
            ec.codes_set(gid, "bitmapPresent", 1)
            ec.codes_set(gid, "missingValue", 1e30)
            values[missing_at] = 1e30
        ec.codes_set_values(gid, values)
        return ec.codes_get_message(gid)
    finally:
        ec.codes_release(gid)


def test_decode_reads_concatenated_messages_and_names_them_by_number():
    a = np.arange(12) / 100.0
    b = 9000.0 + np.arange(12)  # heights above 9000 m must survive: they are not "missing"
    msgs = grib.decode(_message(19, 50, 300, a) + _message(3, 5, 250, b))
    assert [m.param for m in msgs] == [(0, 19, 50), (0, 3, 5)]
    assert [(m.nx, m.ny, m.level) for m in msgs] == [(4, 3, 300), (4, 3, 250)]
    assert (msgs[0].data_date, msgs[0].data_time, msgs[0].forecast_time) == (20261114, 1000, 6)
    assert msgs[0].values.shape == (3, 4)
    assert np.allclose(msgs[0].values.ravel(), a, atol=1e-4)
    assert np.allclose(msgs[1].values.ravel(), b, atol=1e-2)


def test_bitmap_gaps_decode_as_nan():
    m, = grib.decode(_message(19, 30, 300, np.full(12, 0.25), missing_at=5))
    flat = m.values.ravel()
    assert np.isnan(flat[5]) and np.isfinite(np.delete(flat, 5)).all()


def test_grid_latlon_rows_run_north_to_south_and_longitude_is_signed():
    m, = grib.decode(_message(19, 30, 300, np.zeros(12)))
    lat, lon = grib.grid_latlon(m)
    assert lat[:, 0].tolist() == [10.0, 9.0, 8.0]
    assert lon[0].tolist() == [-110.0, -109.0, -108.0, -107.0]


def test_split_rejects_bytes_that_are_not_grib():
    with pytest.raises(ValueError, match="no GRIB header"):
        grib.split_messages(b"<html>404</html>")
