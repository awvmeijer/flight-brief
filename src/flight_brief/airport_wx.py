"""Airport weather in plain words from a decoded TAF or METAR record.

The words are this project's own, so they never state an intensity: "rain", not the TAF's
"-RA" spelled out. The official text is quoted beside them unchanged.

The crosswind is geometry on the stated wind and the runway directions of the route table.
Which runway is in use is not known here, so the runway named is the one the wind favours.
"""

import math
from collections.abc import Sequence
from typing import Any, Literal, NamedTuple

from .vrs import Runway

WEATHER = (("TS", "thunderstorms"), ("FZ", "freezing"), ("SH", "showers"), ("RA", "rain"),
           ("DZ", "drizzle"), ("SN", "snow"), ("GR", "hail"), ("GS", "small hail"),
           ("PL", "ice pellets"), ("FG", "fog"), ("BR", "mist"), ("HZ", "haze"),
           ("FU", "smoke"), ("SQ", "squalls"), ("DU", "dust"), ("SA", "sand"),
           ("VA", "volcanic ash"))
COVER = {"OVC": "overcast", "OVX": "sky obscured", "BKN": "mostly cloudy",
         "SCT": "partly cloudy", "FEW": "a few clouds"}
ORDER = ["OVX", "OVC", "BKN", "SCT", "FEW"]
LONG_RUNWAY_FT = 5000  # runways at least this long are preferred when an airport has any
# The wind now stands for a take-off or landing at most this many hours off, from an
# observation at most this old.
METAR_REACH_H = 3.0


def weather_words(wx: str | None) -> list[str]:
    """`+TSRA BR` -> thunderstorms, rain, mist. Intensity signs are dropped, never spelled out."""
    found: list[str] = []
    for token in (wx or "").replace("+", " ").replace("-", " ").split():
        token = token.removeprefix("VC")
        for i in range(0, len(token) - 1, 2):
            word = dict(WEATHER).get(token[i:i + 2])
            if word and word not in found:
                found.append(word)
    return found


def _sky(clouds: list[dict[str, Any]] | None, cover: str | None = None) -> str | None:
    covers = [c.get("cover") for c in clouds or []] + [cover]
    for name in ORDER:
        if name in covers:
            return COVER[name]
    return "clear" if any(c in ("SKC", "CLR", "NSC", "CAVOK", "NCD") for c in covers) else None


def _wind(spd: Any, gust: Any) -> list[str]:
    out = []
    if isinstance(spd, int | float):
        out.append(f"wind {spd:.0f} kt" if spd else "calm")
    if isinstance(gust, int | float):
        out.append(f"gusts {gust:.0f} kt")
    return out


def _vis(visib: Any) -> list[str]:
    if isinstance(visib, int | float) and visib < 3:
        return [f"visibility {visib:g} miles"]
    return []


def describe(state: dict[str, Any]) -> list[str]:
    sky = _sky(state.get("clouds"), state.get("cover"))
    return [*weather_words(state.get("wxString")), *([sky] if sky else []),
            *_wind(state.get("wspd"), state.get("wgst")), *_vis(state.get("visib"))]


def _taf_state(taf: dict[str, Any], at: float) -> tuple[dict[str, Any], list[str]]:
    """What prevails at one moment (unix seconds), and the words for what comes at times."""
    state: dict[str, Any] = {}
    at_times: list[str] = []
    for period in taf.get("fcsts") or []:
        change = period.get("fcstChange")
        if period["timeFrom"] > at:
            continue
        if change in ("TEMPO", "PROB"):
            if at < period["timeTo"]:
                at_times += [w for w in describe(period) if w not in at_times]
        elif change == "BECMG":
            for key in ("wspd", "wgst", "visib"):
                if period.get(key) is not None:
                    state[key] = period[key]
            if period.get("wspd") is not None:
                state["wgst"] = period.get("wgst")
                state["wdir"] = period.get("wdir")
            if period.get("wxString"):
                state["wxString"] = None if period["wxString"] == "NSW" else period["wxString"]
            if period.get("clouds"):
                state["clouds"] = period["clouds"]
        else:  # the first group or FM: everything is restated
            state = dict(period)
    return state, at_times


def taf_at(taf: dict[str, Any], at: float) -> str:
    """The TAF's forecast for one moment (unix seconds): what prevails, then what comes at times."""
    state, at_times = _taf_state(taf, at)
    words = ", ".join(describe(state)) or "no weather stated"
    return words + (f"; at times {', '.join(at_times)}" if at_times else "")


def taf_wind(taf: dict[str, Any], at: float) -> tuple[Any, Any, Any] | None:
    """(direction, speed, gust) of the wind the TAF has prevailing at one moment (unix
    seconds), as the record gives them; None when it states no wind for then."""
    state, _ = _taf_state(taf, at)
    if not isinstance(state.get("wspd"), int | float):
        return None
    return state.get("wdir"), state["wspd"], state.get("wgst")


def metar_now(metar: dict[str, Any]) -> str:
    words = describe(metar)
    if isinstance(metar.get("temp"), int | float):
        words.append(f"{metar['temp']:.0f} C")
    return ", ".join(words) or "no weather stated"


# --- crosswind -----------------------------------------------------------------------------

class RunwayWind(NamedTuple):
    """The wind against one runway end. Speeds are knots, not rounded."""
    runway: str | None  # None when the wind is calm or variable: no end is favoured
    crosswind_kt: float  # across the runway, never negative
    headwind_kt: float | None  # along it; negative is a tailwind; None when variable
    gust_crosswind_kt: float | None  # the same for the gust, when one is stated
    gust_headwind_kt: float | None
    variable: bool = False


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def is_variable(wdir: Any, wspd: Any) -> bool:
    """A wind with a speed and no one direction: "VRB", or direction 0 with a speed (a wind
    from the north is written 360)."""
    speed = _number(wspd)
    if not speed:
        return False
    return (isinstance(wdir, str) and wdir.strip().upper() == "VRB") or _number(wdir) == 0.0


def crosswind(wdir: Any, wspd: Any, wgst: Any, runways: Sequence[Runway]) -> RunwayWind | None:
    """The wind split along and across the best-aligned runway end: the one with the largest
    headwind, among the runways of at least 5,000 ft when the airport has any and among all
    of them otherwise. Ends that tie go to the longer runway, then the lower name.

    `wdir` is degrees true or "VRB", `wspd` and `wgst` knots, as AWC decodes a TAF or METAR.
    A calm wind gives zeros and no runway. A variable wind gives no runway either: its whole
    speed may blow across any of them, so that is the crosswind. None when there is no speed,
    or a direction that cannot be read, or a wind with a direction and no runway to set it
    against."""
    speed, gust = _number(wspd), _number(wgst)
    if speed is None:
        return None
    if gust is not None and gust <= speed:
        gust = None
    if speed == 0 and gust is None:
        return RunwayWind(None, 0.0, 0.0, None, None)
    if is_variable(wdir, speed) or (speed == 0 and _number(wdir) in (None, 0.0)):
        return RunwayWind(None, speed, None, gust, None, variable=True)
    direction = _number(wdir)
    if direction is None or not runways:
        return None
    long = [r for r in runways if (r.length_ft or 0) >= LONG_RUNWAY_FT]

    def head(r: Runway) -> float:
        return round(math.cos(math.radians(direction - r.heading)), 9)

    best = min(long or runways, key=lambda r: (-head(r), -(r.length_ft or 0), r.ident))
    along = math.cos(math.radians(direction - best.heading))
    across = abs(math.sin(math.radians(direction - best.heading)))
    return RunwayWind(best.ident, speed * across, speed * along,
                      None if gust is None else gust * across,
                      None if gust is None else gust * along)


def wind_words(wdir: Any, wspd: Any, wgst: Any) -> str:
    """The wind as stated: "250 at 20 kt, gusts 30", "variable at 4 kt", "calm"."""
    speed, gust = _number(wspd), _number(wgst)
    if speed is None:
        return "not stated"
    gusts = f", gusts {gust:.0f}" if gust is not None and gust > speed else ""
    if speed == 0 and not gusts:
        return "calm"
    direction = _number(wdir)
    if is_variable(wdir, speed) or direction is None or speed == 0:
        return f"variable at {speed:.0f} kt{gusts}"
    return f"{direction:03.0f} at {speed:.0f} kt{gusts}"


UNKNOWN_RUNWAY = "The runway in use is not known in advance."


def crosswind_text(found: RunwayWind, basis: Literal["TAF", "METAR"]) -> str:
    """The crosswind in this project's words: numbers only, never a word for how much."""
    wind = "forecast wind" if basis == "TAF" else "wind now"
    gust = found.gust_crosswind_kt
    if found.variable:
        gusts = f" (gusts {gust:.0f})" if gust is not None else ""
        return (f"The {wind} is variable in direction at {found.crosswind_kt:.0f} kt{gusts}, "
                f"so that much can blow across any runway. {UNKNOWN_RUNWAY}")
    if found.runway is None:
        return f"The {wind} is calm, so there is no crosswind."
    gusts = f" (gusts {gust:.0f})" if gust is not None else ""
    return (f"Crosswind about {found.crosswind_kt:.0f} kt{gusts} on runway {found.runway}, "
            f"the runway best aligned with the {wind}. {UNKNOWN_RUNWAY}")
