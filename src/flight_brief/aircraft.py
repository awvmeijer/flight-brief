"""An aircraft type the passenger typed, turned into a default cruise level and groundspeed.

Nothing this project may copy says which aircraft flies a given flight number: VRS
standing-data has no type per route. So the type is typed, and it is optional.

What a type code is comes from VRS standing-data's `model-type` files (CC0 1.0): each ICAO
type designator with its engine kind and species. The speeds and levels below are not from a
source. They are this project's own round numbers, close to each kind's usual cruise, and the
brief says so wherever it prints one.
"""

import re
from typing import Literal

from pydantic import BaseModel

from .vrs import RouteTable

Kind = Literal["jet", "turboprop", "piston"]
# Own round numbers by engine kind: (groundspeed in kt, cruise flight level). A jet has none
# here: its defaults stay the distance bands of `brief.BANDS`.
BY_KIND: dict[Kind, tuple[float, int] | None] = {
    "jet": None, "turboprop": (275.0, 240), "piston": (140.0, 80)}
# Own round numbers for types that sit far from their kind's figure.
BY_TYPE: dict[str, tuple[float, int]] = {
    "DH8D": (360.0, 240), "SB20": (360.0, 300),          # fast turboprops
    "AT43": (290.0, 240), "AT44": (290.0, 240), "AT45": (290.0, 240), "AT46": (290.0, 240),
    "C208": (170.0, 100), "DHC6": (160.0, 100), "L410": (200.0, 140), "D228": (200.0, 100),
}
ENGINE: dict[str, Kind] = {"J": "jet", "T": "turboprop", "P": "piston"}
ALIASES = {"ATR72": "AT72", "ATR42": "AT45", "Q400": "DH8D", "DASH8": "DH8D"}
HELP = "Use an ICAO type code such as AT72 or A320, or the word turboprop, jet or piston"


class Aircraft(BaseModel):
    code: str  # the ICAO type designator, or the word typed
    kind: Kind
    groundspeed_kt: float | None  # None: no figure of its own, the distance bands apply
    cruise_fl: int | None

    @property
    def label(self) -> str:
        return self.code if self.code == self.kind else f"{self.code}, a {self.kind}"


def lookup(text: str, table: RouteTable) -> Aircraft:
    """The aircraft a passenger typed. Raises ValueError with a message for the user."""
    code = re.sub(r"[\s\-]", "", text).upper()
    code = ALIASES.get(code, code)
    if code.lower() in BY_KIND:
        kind: Kind = code.lower()  # type: ignore[assignment]
        own = BY_KIND[kind]
        return Aircraft(code=kind, kind=kind, groundspeed_kt=own[0] if own else None,
                        cruise_fl=own[1] if own else None)
    found = table.model_type(code)
    if found is None:
        raise ValueError(f"'{text.strip()}' is not an aircraft type this brief knows. {HELP}")
    engine, species = found
    if species != "L" or engine not in ENGINE:
        raise ValueError(f"'{code}' is not an aeroplane this brief has a default speed for. "
                         f"{HELP}, or give the speed with --gs")
    kind = ENGINE[engine]
    own = BY_TYPE.get(code) or BY_KIND[kind]
    return Aircraft(code=code, kind=kind, groundspeed_kt=own[0] if own else None,
                    cruise_fl=own[1] if own else None)
