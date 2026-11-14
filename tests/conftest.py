from pathlib import Path

import pytest

from flight_brief import vrs

FIXTURES = Path(__file__).parent / "fixtures"
# Time zones for the airports in the VRS slice, as timezonefinder gives them.
ZONES = {"WS": "Asia/Singapore", "EG": "Europe/London", "YS": "Australia/Sydney",
         "KL": "America/Los_Angeles", "KS": "America/Los_Angeles", "KJ": "America/New_York",
         "KD": "America/Denver", "KO": "America/Chicago", "RJ": "Asia/Tokyo",
         "PH": "Pacific/Honolulu", "NZ": "Pacific/Auckland", "VH": "Asia/Hong_Kong",
         "FA": "Africa/Johannesburg", "KM": "America/New_York", "KA": "America/New_York",
         "KDFW": "America/Chicago"}


@pytest.fixture(scope="session")
def table(tmp_path_factory: pytest.TempPathFactory) -> vrs.RouteTable:
    """The route table built from the committed VRS slice."""
    out = tmp_path_factory.mktemp("vrs") / "vrs.sqlite"
    files = list(vrs.from_folder(FIXTURES / "vrs"))
    codes = {}
    for kind, text in files:
        if kind == "airports":
            for row in vrs._rows(text):
                codes[(float(row["Latitude"]), float(row["Longitude"]))] = row["Code"]
    vrs.build(files, out, lambda lat, lon: ZONES.get(codes[(lat, lon)]) or ZONES[codes[(lat, lon)][:2]])
    return vrs.RouteTable(out)
