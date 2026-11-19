from pathlib import Path

import pytest

from flight_brief import vrs
from vrs_helpers import EXTRA, EXTRA_ZONES

FIXTURES = Path(__file__).parent / "fixtures"
# Time zones for the airports in the VRS slice, as timezonefinder gives them.
ZONES = {"WS": "Asia/Singapore", "EG": "Europe/London", "YS": "Australia/Sydney",
         "KL": "America/Los_Angeles", "KS": "America/Los_Angeles", "KJ": "America/New_York",
         "KD": "America/Denver", "KO": "America/Chicago", "RJ": "Asia/Tokyo",
         "PH": "Pacific/Honolulu", "NZ": "Pacific/Auckland", "VH": "Asia/Hong_Kong",
         "FA": "Africa/Johannesburg", "KM": "America/New_York", "KA": "America/New_York",
         "KDFW": "America/Chicago", "NTAA": "Pacific/Tahiti", "NTGJ": "Pacific/Gambier"}


@pytest.fixture(autouse=True)
def no_tile_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test reaches GitHub for forecast tiles: every release file is "not there"."""
    from flight_brief.forecast import layer
    monkeypatch.setattr(layer, "http_get", lambda url, timeout=60.0: None)
    monkeypatch.setenv("FLIGHT_BRIEF_TILES", str(tmp_path / "no-tiles"))


@pytest.fixture(scope="session")
def table(tmp_path_factory: pytest.TempPathFactory) -> vrs.RouteTable:
    """The route table built from the committed VRS slice."""
    return _build(tmp_path_factory.mktemp("vrs") / "vrs.sqlite", [])


@pytest.fixture(scope="session")
def wide(tmp_path_factory: pytest.TempPathFactory) -> vrs.RouteTable:
    """The same slice with the rows of `vrs_helpers.EXTRA` added: more airports, some with
    accents, more routes, and the aircraft families the name tests ask about."""
    return _build(tmp_path_factory.mktemp("vrs-wide") / "vrs.sqlite", EXTRA)


def _build(out: Path, extra: list[tuple[str, str]]) -> vrs.RouteTable:
    files = [*vrs.from_folder(FIXTURES / "vrs"), *extra]
    zones = {**ZONES, **EXTRA_ZONES}
    codes = {}
    for kind, text in [*files, ("airports", vrs.EXTRA_AIRPORTS.read_text(encoding="utf-8"))]:
        if kind == "airports":
            for row in vrs._rows(text):
                codes[(float(row["Latitude"]), float(row["Longitude"]))] = row["Code"]
    vrs.build(files, out, lambda lat, lon: zones.get(codes[(lat, lon)]) or zones[codes[(lat, lon)][:2]])
    return vrs.RouteTable(out)
