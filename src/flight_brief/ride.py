"""The plain answer: how the ride is expected to be, in four words, with where each word
comes from.

This is the only module that writes a strength word (smooth, light, moderate, severe) in the
project's own voice. Every word it writes carries its source:

- **warning**: an official warning in force on that stretch. A SIGMET for turbulence or
  mountain waves is issued for severe turbulence; a G-AIRMET high-level turbulence area is
  issued for moderate turbulence. The word is the issuing office's, by the product's definition.
- **forecast**: NOAA's GTG forecast (contiguous US). The word is NOAA's own name for the
  forecast value for the aircraft's weight class (`NOAA_EDR`).
- **estimate**: this project's wind-shear index, where no official forecast exists. Its cut
  points are set so that, where both exist, the estimate says light and moderate as often as
  NOAA's forecast does (docs/recon/estimate_calibration.py). It never says severe.

What it never writes: "safe", "unsafe", a score for the flight, or a word without a source.
Where no source speaks, the answer is "not known yet", never smooth.

The flight is cut into stretches of ten minutes. A stretch takes the official forecast where
one speaks and the estimate only where none does; a warning in force can only raise it.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

WORDS = ("smooth", "light", "moderate", "severe")
Source = Literal["warning", "forecast", "estimate"]
Weight = Literal["light", "medium", "heavy"]
SLOT_MIN = 10

# NOAA Aviation Weather Center, Graphical Forecasts for Aviation help, "Estimated EDR
# Threshold" by aircraft weight class (maximum take-off weight): light, moderate, severe.
# https://aviationweather.gov/gfa/help/ read on 19 November 2026.
NOAA_EDR: dict[str, tuple[float, float, float]] = {
    "light": (0.13, 0.16, 0.36),    # under 15,500 lb
    "medium": (0.15, 0.20, 0.44),   # 15,500 to 300,000 lb: A320, B737
    "heavy": (0.17, 0.24, 0.54),    # over 300,000 lb: A330 to A380, B747, B777
}
NOAA_EDR_SOURCE = "NOAA Aviation Weather Center, estimated EDR thresholds by aircraft weight class"

# The wind-shear index value from which the estimate says light, and moderate. Measured over
# the contiguous US on 13 to 18 November 2026: along 42,600 simulated ten-minute stretches,
# the index reaches these values as often as NOAA's GTG forecast reaches its own light and
# moderate thresholds for the same weight class (docs/recon/estimate_match.json).
ESTIMATE_CUTS: dict[str, tuple[float, float]] = {
    "light": (8.0, 12.0), "medium": (10.5, 20.0), "heavy": (14.0, 30.0)}

# What each word means for a passenger. Own words after the FAA's definitions of turbulence
# intensity (Aeronautical Information Manual, table 7-1-10).
MEANING = {
    "smooth": "No bumps worth noticing.",
    "light": "Slight bumps. Drinks stay in the cup; walking is easy.",
    "moderate": "Clear bumps and a pull against the seat belt. Loose things move; walking is hard.",
    "severe": "Hard jolts. Loose things are thrown about; walking is not possible. Crews plan "
              "routes to stay out of it.",
}
MEANING_SOURCE = "FAA Aeronautical Information Manual, turbulence reporting criteria"

# Warnings that name a strength by what they are.
WARNING_LEVEL = {"TURB": 3, "MTW": 3, "TURB-HI": 2}
STORM_KINDS = ("TS", "TC")
STORM_MARGIN_FL = 50   # a forecast storm cloud counts from 5,000 ft under the cruise level


class Slot(BaseModel):
    start: datetime
    end: datetime
    level: int | None = None          # index into WORDS; None: not known
    source: Source | None = None
    storm: Literal["warning", "forecast"] | None = None
    storm_known: bool = False         # a storm source speaks for this stretch
    unknown: str | None = None        # why no level: too_early, not_covered, stale

    @property
    def word(self) -> str | None:
        return None if self.level is None else WORDS[self.level]


def noaa_level(edr: float, weight: str) -> int:
    lgt, mod, sev = NOAA_EDR[weight]
    return 3 if edr >= sev else 2 if edr >= mod else 1 if edr >= lgt else 0


def estimate_level(index: float, weight: str) -> int:
    lgt, mod = ESTIMATE_CUTS[weight]
    return 2 if index >= mod else 1 if index >= lgt else 0


def slot_level(*, warning_kinds: Sequence[str], edr: float | None, index: float | None,
               weight: str) -> tuple[int | None, Source | None]:
    """The level of one stretch and the source that names it.

    The official forecast where it speaks, else the estimate; a warning in force raises it.
    On a tie the more official source is named.
    """
    level: int | None = None
    source: Source | None = None
    if edr is not None:
        level, source = noaa_level(edr, weight), "forecast"
    elif index is not None:
        level, source = estimate_level(index, weight), "estimate"
    warned = max((WARNING_LEVEL[k] for k in warning_kinds if k in WARNING_LEVEL), default=None)
    if warned is not None and (level is None or warned >= level):
        level, source = warned, "warning"
    return level, source


def storm_from_top(top_fl: int | None, cruise_fl: int) -> bool:
    return top_fl is not None and top_fl >= cruise_fl - STORM_MARGIN_FL


# --- sentences --------------------------------------------------------------------------------

SOURCE_WORDS = {"warning": "official warning", "forecast": "NOAA forecast", "estimate": "estimate"}


def duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if not h:
        return f"{m} minutes"
    return f"{h} h" if not m else f"{h} h {m} min"


def hour_span(hours: Sequence[int]) -> str:
    """`[6, 7, 9]` -> "hours 6 to 7 and 9"; the hours of the flight, counted from take-off."""
    runs: list[list[int]] = []
    for h in sorted(set(hours)):
        if runs and h == runs[-1][-1] + 1:
            runs[-1].append(h)
        else:
            runs.append([h])
    parts = [str(r[0]) if len(r) == 1 else f"{r[0]} to {r[-1]}" for r in runs]
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return ("hour " if len(runs) == 1 and len(runs[0]) == 1 else "hours ") + text


def _hour_of(slot: Slot, takeoff: datetime) -> int:
    return int((slot.start - takeoff).total_seconds() // 3600) + 1


def _minutes(slots: Sequence[Slot]) -> int:
    return round(sum((s.end - s.start).total_seconds() for s in slots) / 60)


def _sources(slots: Sequence[Slot]) -> str:
    order = [s for s in ("warning", "forecast", "estimate") if any(x.source == s for x in slots)]
    return " and ".join(SOURCE_WORDS[s] for s in order)


def answer(slots: Sequence[Slot], takeoff: datetime) -> list[str]:
    """The sentences at the top of a brief. The first is the headline."""
    known = [s for s in slots if s.level is not None]
    unknown = [s for s in slots if s.level is None]
    out: list[str] = []
    if not known:
        out.append("Too early to say how the ride will be.")
    else:
        by = {q: [s for s in known if s.level == q] for q in range(4)}
        worst = max(q for q in range(4) if by[q])
        share_smooth = _minutes(by[0]) / _minutes(known)
        whole = "the whole flight" if not unknown else "the part that can be known"
        if worst == 0:
            out.append(f"Smooth for {whole} ({_sources(known)}).")
        else:
            lead = ("Mostly smooth" if share_smooth >= 0.6
                    else "Smooth for part of the flight" if share_smooth >= 0.25
                    else "Bumpy for most of the flight")
            top = by[worst]
            out.append(
                f"{lead}. {WORDS[worst].capitalize()} turbulence for about "
                f"{duration(_minutes(top))}, in {hour_span([_hour_of(s, takeoff) for s in top])} "
                f"({_sources(top)}).")
            for q in range(worst - 1, 0, -1):
                if by[q]:
                    out.append(f"{WORDS[q].capitalize()} for about {duration(_minutes(by[q]))} "
                               f"({_sources(by[q])}).")
    warned = [s for s in slots if s.storm == "warning"]
    clouds = [s for s in slots if s.storm == "forecast"]
    spoken = [s for s in slots if s.storm_known]
    if warned:
        out.append("Thunderstorm warning in force in "
                   f"{hour_span([_hour_of(s, takeoff) for s in warned])}.")
    if clouds:
        out.append("Storm clouds forecast near cruise level in "
                   f"{hour_span([_hour_of(s, takeoff) for s in clouds])}.")
    if not warned and not clouds and spoken:
        out.append("No storm warning and no forecast storm clouds near cruise level"
                   + ("." if len(spoken) == len(slots) else " on the part that can be known."))
    if unknown and known:
        out.append(f"Not known yet: {hour_span([_hour_of(s, takeoff) for s in unknown])}.")
    return out


def wind_sentence(along_kt: Sequence[float]) -> str | None:
    """`along_kt`: wind along the route per stretch, positive from behind."""
    if not along_kt:
        return None
    mean = sum(along_kt) / len(along_kt)
    size = int(round(abs(mean) / 5.0)) * 5
    if size < 10:
        return "Little headwind or tailwind on average."
    return f"{'Tailwind' if mean > 0 else 'Headwind'} about {size} kt on average."


KEY = ("Solid bars are official: a warning in force, or NOAA's forecast. Faint bars with a "
       "dashed top are this project's estimate, which is less certain. A question mark means "
       "nobody can know yet. A diamond over a bar marks a storm: a thunderstorm warning in "
       "force, or storm clouds forecast near cruise level.")
WEIGHT_EXAMPLES = {"light": "a small propeller aircraft", "medium": "an A320 or a Boeing 737",
                   "heavy": "an A330, a Boeing 777 or larger"}


def explain(weight: str, assumed: bool) -> list[str]:
    """How the words were chosen, for the evidence part of a brief."""
    lgt, mod, sev = NOAA_EDR[weight]
    cut_l, cut_m = ESTIMATE_CUTS[weight]
    size = (f"No aircraft type was given, so the words are for a medium-size airliner "
            f"({WEIGHT_EXAMPLES['medium']}). A larger aircraft feels the same air less."
            if assumed else
            f"The words are for a {weight} aircraft ({WEIGHT_EXAMPLES[weight]}): the same air "
            "feels different in a larger or smaller one.")
    return [
        size,
        "Official warning: a SIGMET for turbulence or mountain waves is issued for severe "
        "turbulence, and a G-AIRMET high-level turbulence area for moderate turbulence. Where "
        "one is in force on your route, that is the word.",
        f"NOAA forecast (contiguous US, 18 hours ahead): NOAA's own names for its forecast "
        f"value, the eddy dissipation rate. For this aircraft size: light from {lgt:.2f}, "
        f"moderate from {mod:.2f}, severe from {sev:.2f}.",
        f"Estimate (everywhere else, 54 hours ahead): this project's wind-shear index, computed "
        f"from NOAA's wind forecast. It says light from index {cut_l:g} and moderate from "
        f"{cut_m:g}, set so that over the US it says each word as often as NOAA's forecast "
        "does. It never says severe.",
        ESTIMATE_CHECK,
        "Each bar is ten minutes of flight and shows the strongest value forecast on that "
        "stretch. Turbulence comes in patches: a bar says what is forecast there, not that "
        "every minute of it will feel that way.",
    ]


# How the estimate compares with NOAA's forecast where both exist, and with what pilots
# reported (medium weight class; docs/recon/estimate_match.json and the table printed by
# docs/recon/estimate_calibration.py from 34,190 cruise-level pilot reports, 14 October to
# 19 November 2026).
ESTIMATE_CHECK = (
    "How far to trust the estimate: over the US, where it can be compared with NOAA's "
    "forecast, the two give the same word on about 3 stretches in 4 and are within one word "
    "of each other on 19 in 20. When the estimate says moderate, NOAA's forecast says "
    "moderate for the same stretch about 3 times in 10. Against 34,190 pilot reports: where "
    "the estimate said smooth, 15% of the reports were of moderate turbulence or more; where "
    "it said light, 31%; where it said moderate, 44%. Pilots report bumps far more often than "
    "smooth air, so these shares are higher than what a flight meets.")
