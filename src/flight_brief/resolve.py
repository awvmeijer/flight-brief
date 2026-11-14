"""From what a passenger types to a route: flight code, airline, callsign, airports.

The order is the design's: normalise the code, map the airline through VRS `airlines.csv`
(one IATA code can mean several airlines), look the callsign up in the VRS route table, and
otherwise say "could not map" so the user types the two airports. No rule turns a flight
number into an ATC callsign, so "could not map" is a normal answer, common on European
short-haul. The route table has no times: the user always types the departure time.
"""

import re
from typing import Literal

from pydantic import BaseModel

from .vrs import Airport, RouteTable

# VRS's own rule: an ICAO or IATA airline code, then a number that starts with a digit.
CODE = re.compile(r"^([A-Z]{3}|[A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])(\d[A-Z0-9]*)$")
# After trimming: at most four characters, of which the last one or two may be letters.
NUMBER = re.compile(r"^(\d{1,4}|\d{1,3}[A-Z]|\d{1,2}[A-Z]{2})$")
COULD_NOT_MAP = ("Couldn't match that flight number to a route. "
                 "Enter the two airports and a departure time")


class FlightCode(BaseModel):
    airline: str  # as typed: IATA (two characters) or ICAO (three letters)
    number: str  # leading zeros stripped, suffix letters kept

    def __str__(self) -> str:
        return self.airline + self.number


class Leg(BaseModel):
    index: int  # 1-based
    origin: Airport
    destination: Airport


class Candidate(BaseModel):
    airline: str
    name: str
    callsign: str
    airports: list[str]


class Resolution(BaseModel):
    status: Literal["mapped", "choose_airline", "could_not_map", "invalid"]
    code: str | None = None  # normalised, e.g. SQ322
    callsign: str | None = None
    airline: str | None = None
    legs: list[Leg] = []
    candidates: list[Candidate] = []
    message: str = ""


def normalise(text: str) -> FlightCode | None:
    """`sq 0322` -> SQ322, `ba10a` -> BA10A. None when the text is not a flight code."""
    match = CODE.match(re.sub(r"[\s\-]", "", text).upper())
    if not match:
        return None
    number = match.group(2).lstrip("0")
    if not number or number.isalpha():
        number = "0" + number
    if not NUMBER.match(number):
        return None
    return FlightCode(airline=match.group(1), number=number)


def resolve(text: str, table: RouteTable, airline: str | None = None) -> Resolution:
    """Map a typed flight code to its legs. `airline` picks one when the code is shared."""
    code = normalise(text)
    if code is None:
        return Resolution(status="invalid",
                          message=f"'{text.strip()}' does not look like a flight number. "
                                  "Use the airline code and number, for example SQ322")
    found = []
    for line in table.airlines(code.airline):
        callsign = line.code + code.number
        airports = table.route(callsign)
        if airports and (airline is None or airline.upper() in (line.code, line.icao)):
            found.append(Candidate(airline=line.code, name=line.name, callsign=callsign,
                                   airports=airports))
    if not found:
        return Resolution(status="could_not_map", code=str(code), message=COULD_NOT_MAP)
    if len(found) > 1:
        names = ", ".join(f"{c.name} ({c.airline})" for c in found)
        return Resolution(status="choose_airline", code=str(code), candidates=found,
                          message=f"{code} can be {names}. Say which airline")
    (one,) = found
    places = [table.airport(a) for a in one.airports]
    if any(p is None for p in places):
        return Resolution(status="could_not_map", code=str(code), message=COULD_NOT_MAP)
    legs = [Leg(index=i + 1, origin=a, destination=b)
            for i, (a, b) in enumerate(zip(places, places[1:], strict=False)) if a and b]
    return Resolution(status="mapped", code=str(code), callsign=one.callsign, airline=one.name,
                      legs=legs)


def typed_leg(table: RouteTable, origin: str, destination: str) -> Leg | str:
    """A leg from two airport codes the user typed (IATA or ICAO), or the reason it failed."""
    places = []
    for code in (origin, destination):
        place = table.airport(code.strip().upper())
        if place is None:
            return f"Unknown airport code '{code}'. Use a three-letter or four-letter code"
        places.append(place)
    if places[0].code == places[1].code:
        return "The two airports are the same"
    return Leg(index=1, origin=places[0], destination=places[1])
