"""Airport weather in plain words from a decoded TAF or METAR record.

The words are this project's own, so they never state an intensity: "rain", not the TAF's
"-RA" spelled out. The official text is quoted beside them unchanged.
"""

from typing import Any

WEATHER = (("TS", "thunderstorms"), ("FZ", "freezing"), ("SH", "showers"), ("RA", "rain"),
           ("DZ", "drizzle"), ("SN", "snow"), ("GR", "hail"), ("GS", "small hail"),
           ("PL", "ice pellets"), ("FG", "fog"), ("BR", "mist"), ("HZ", "haze"),
           ("FU", "smoke"), ("SQ", "squalls"), ("DU", "dust"), ("SA", "sand"),
           ("VA", "volcanic ash"))
COVER = {"OVC": "overcast", "OVX": "sky obscured", "BKN": "mostly cloudy",
         "SCT": "partly cloudy", "FEW": "a few clouds"}
ORDER = ["OVX", "OVC", "BKN", "SCT", "FEW"]


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


def taf_at(taf: dict[str, Any], at: float) -> str:
    """The TAF's forecast for one moment (unix seconds): what prevails, then what comes at times."""
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
            if period.get("wxString"):
                state["wxString"] = None if period["wxString"] == "NSW" else period["wxString"]
            if period.get("clouds"):
                state["clouds"] = period["clouds"]
        else:  # the first group or FM: everything is restated
            state = dict(period)
    words = ", ".join(describe(state)) or "no weather stated"
    return words + (f"; at times {', '.join(at_times)}" if at_times else "")


def metar_now(metar: dict[str, Any]) -> str:
    words = describe(metar)
    if isinstance(metar.get("temp"), int | float):
        words.append(f"{metar['temp']:.0f} C")
    return ", ".join(words) or "no weather stated"
