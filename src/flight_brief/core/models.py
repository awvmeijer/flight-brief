"""The domain objects the brief engine reads and writes.

`Waypoint`, `Hazard` and `Encounter` are copied unchanged from oceanic-flight-watch
(`src/oceanic_watch/models.py` at commit 962e41a, MIT, Copyright (c) 2026 Anthony Meijer).
`PassengerFlight` is new: that repo's `Flight` needs an aircraft, ETOPS minutes and alternates,
which a passenger does not know. Here everything the passenger did not type is a default, and
each default is listed in `assumptions` so the brief can print it.
"""

from datetime import datetime

from pydantic import BaseModel


class Waypoint(BaseModel):
    name: str
    lat: float
    lon: float


class Hazard(BaseModel):
    """An area to avoid between two flight levels for a time window, from any source."""

    hazard_id: str
    kind: str  # TS, TURB, ICE, VA, TC
    source: str
    issued_at: datetime | None = None
    valid_from: datetime
    valid_to: datetime
    base_ft: int | None = None  # None: from the surface
    top_ft: int | None = None  # None: top unknown, treated as unlimited
    rings: list[list[tuple[float, float]]]  # (lon, lat) rings; empty when the source gave none
    raw: str = ""

    def covers_level(self, altitude_ft: int) -> bool:
        low = self.base_ft or 0
        high = self.top_ft if self.top_ft is not None else 10**6
        return low <= altitude_ft <= high


class Encounter(BaseModel):
    flight_id: str
    hazard_id: str
    kind: str
    entry: datetime
    exit: datetime
    distance_nm: float
    minutes_to_entry: float
    flight_level: int
    hazard_base_ft: int | None
    hazard_top_ft: int | None


class PassengerFlight(BaseModel):
    """One leg as a passenger knows it: a code, two airports and a departure time."""

    flight_id: str
    origin: str
    destination: str
    departure: datetime  # UTC
    cruise_fl: int  # flight level, hundreds of feet
    groundspeed_kt: float
    route: list[Waypoint]  # origin, any fixes, destination
    assumptions: list[str] = []
