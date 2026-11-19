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
ALIASES = {"ATR72": "AT72", "ATR42": "AT45", "Q400": "DH8D", "DASH8": "DH8D", "A220": "BCS3"}
# Names that no rule below reaches: the turboprops, whose designators do not follow from
# their names, and whose own figures in `BY_TYPE` are worth finding. First match wins.
NAMED: list[tuple[re.Pattern[str], str]] = [(re.compile(p), code) for p, code in (
    (r"ATR?72", "AT72"), (r"ATR?42", "AT45"),
    (r"(DHC8|DASH8)Q?[- ]?4|Q400", "DH8D"), (r"(DHC8|DASH8)Q?[- ]?3|Q300", "DH8C"),
    (r"(DHC8|DASH8)Q?[- ]?2|Q200", "DH8B"), (r"(DHC8|DASH8)Q?[- ]?1|Q100", "DH8A"),
    (r"DHC8|DASH8", "DH8D"), (r"DHC6|TWINOTTER", "DHC6"), (r"SAAB2000", "SB20"),
    (r"SAAB340", "SF34"), (r"A220", "BCS3"), (r"CSERIES|CS300", "BCS3"), (r"CS100", "BCS1"),
    (r"SUPERJET|SSJ", "SU95"))]
# Makers' names, dropped before the model is read. A maker whose aircraft carry one letter
# in front of the model number gives that letter back when the number came without it.
MAKERS = {"BOEING": "B", "AIRBUS": "A", "EMBRAER": "E", "BOMBARDIER": "", "CANADAIR": "",
          "DE": "", "HAVILLAND": "", "CANADA": "", "MCDONNELL": "", "DOUGLAS": "",
          "AEROSPATIALE": "", "ALENIA": "", "FOKKER": "F", "BRITISH": "", "AEROSPACE": "",
          "SUKHOI": "", "COMAC": "", "MITSUBISHI": "", "CESSNA": "C", "INDUSTRIE": ""}
HELP = "Use an ICAO type code such as AT72 or A320, or the word turboprop, jet or piston"


class UnknownAircraft(ValueError):
    """What was typed is not an aircraft type this project can place. The message is for the
    user; the caller may go on without a type."""


class Aircraft(BaseModel):
    code: str  # the ICAO type designator, the family as typed (777), or the word typed
    kind: Kind
    groundspeed_kt: float | None  # None: no figure of its own, the distance bands apply
    cruise_fl: int | None

    @property
    def label(self) -> str:
        return self.code if self.code == self.kind else f"{self.code}, a {self.kind}"


def _of_kind(code: str, kind: Kind) -> Aircraft:
    own = BY_TYPE.get(code) or BY_KIND[kind]
    return Aircraft(code=code, kind=kind, groundspeed_kt=own[0] if own else None,
                    cruise_fl=own[1] if own else None)


def _designator(code: str, table: RouteTable) -> Aircraft | None:
    """The aircraft with exactly this ICAO type designator, or None when there is none.
    Raises when the designator is something else than an aeroplane with a known engine."""
    found = table.model_type(code)
    if found is None:
        return None
    engine, species = found
    if species != "L" or engine not in ENGINE:
        raise UnknownAircraft(f"'{code}' is not an aeroplane this brief has a default speed "
                              f"for. {HELP}, or give the groundspeed instead")
    return _of_kind(code, ENGINE[engine])


def _family(name: str, like: str, table: RouteTable) -> Aircraft | None:
    """A family, from every designator in the table that fits `like`: the family's engine
    kind when they agree. The variants that end in a digit are asked first, because a few
    unrelated light aircraft share three characters with an airliner family (A33P beside
    A332)."""
    rows = table.model_family(like)
    kinds = ({e for code, e, s in rows if code[-1].isdigit() and s == "L"}
             or {e for code, e, s in rows if s == "L"})
    if len(kinds) != 1 or (engine := kinds.pop()) not in ENGINE:
        return None
    return _of_kind(name, ENGINE[engine])


def lookup(text: str, table: RouteTable) -> Aircraft:
    """The aircraft a passenger typed, or a schedule's name for it: an ICAO designator
    (B773), a family (777, A350, 737 MAX), a maker and model (Boeing 777-300ER, ATR 72-600)
    or one of the words jet, turboprop and piston. Raises `UnknownAircraft`, a ValueError
    with a message for the user, when it is none of these.

    Only the engine kind is needed from a family: a jet has no figure of its own, and the
    turboprops that have one are reached by name."""
    code = re.sub(r"[\s\-]", "", text).upper()
    code = ALIASES.get(code, code)
    if code.lower() in BY_KIND:
        return _of_kind(code.lower(), code.lower())  # type: ignore[arg-type]
    if found := _designator(code, table):
        return found
    words = re.sub(r"[^A-Z0-9]+", " ", text.upper()).split()
    letter = next((MAKERS[w] for w in words if MAKERS.get(w)), "")
    model = "".join(w for w in words if w not in MAKERS)
    for pattern, designator in NAMED:
        if pattern.match(model) and (found := _designator(designator, table)):
            return found
    model = re.sub(r"^(ERJ|EMB)(?=\d)", "E", model)
    if model[:1].isdigit() and letter:
        model = letter + model  # Embraer 190 is E190, as Fokker 100 is F100
    # Designators are four characters and a family shares the first three (B772, B773,
    # B77W), so 777 is read as B77 and A350 as A35. The 737 MAX has its own: B37M to B39M.
    if boeing := re.match(r"B?(7[0-8]7)(MAX)?", model):
        found = _family(boeing[1] + (" MAX" if boeing[2] else ""),
                        "B3_M" if boeing[2] else f"B{boeing[1][:2]}_", table)
    elif airbus := re.match(r"A?(3[0-8]0)", model):
        found = _family(f"A{airbus[1]}", f"A{airbus[1][:2]}_", table)
    elif len(model) >= 4 and re.match(r"[A-Z]{1,3}\d|[A-Z]\d{2}", model):
        # The first four characters are the designator in E190-E2, CRJ900 and A321neo.
        exact = table.model_type(model[:4])
        found = (_of_kind(model[:4], ENGINE[exact[0]])
                 if exact and exact[1] == "L" and exact[0] in ENGINE
                 else _family(model[:4], f"{model[:3]}_", table))
    else:
        found = None
    if found:
        return found
    raise UnknownAircraft(f"'{text.strip()}' is not an aircraft type this brief knows. {HELP}")
