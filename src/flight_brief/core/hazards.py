"""Turn source records into Hazards. Each source's quirks are handled here, once.

Copied from oceanic-flight-watch (`src/oceanic_watch/geo/hazards.py` at commit 962e41a, MIT,
Copyright (c) 2026 Anthony Meijer). Unchanged apart from imports.
"""

import re
from datetime import UTC, datetime
from typing import Any

from .models import Hazard

KINDS = {"CONV": "TS"}  # a US convective SIGMET is a thunderstorm area
TOPS = [(re.compile(pattern), feet) for pattern, feet in (  # how SIGMET text states a top
    (r"\bSFC/(?:FL)?(?:\d{3}-)?(\d{3})\b", 100),  # SFC/060, SFC/FL100, SFC/FL060-090
    (r"\bTOP (?:BLW )?FL(\d{3})\b", 100),  # TOP FL450, TOP BLW FL150
    (r"\bFL\d{2,3}/(?:FL)?(\d{3})\b", 100),  # FL220/320
    (r"\b(?:SFC|\d{4,5})/(\d{4,5})FT\b", 1),  # SFC/5000FT, 2000/10000FT
)]


def top_in_text(raw: str) -> int | None:
    """The top a SIGMET's text states, in feet: the highest if several, None if open or unclear.

    aviationweather.gov leaves `top` empty for some wordings, and an empty top counts as
    unlimited. Only exact wordings are read, because a top read too low would hide a hazard.
    """
    text = " ".join(raw.split())
    if "ABV FL" in text:
        return None
    found = [int(m) * feet for pattern, feet in TOPS for m in pattern.findall(text)]
    return max(found, default=None)


def from_sigmet(record: dict[str, Any]) -> Hazard:
    """An aviationweather.gov SIGMET as a Hazard, from the `sigmet` feed or the older `isigmet`.

    Handles what the 30-day backfill showed: `coords` as one ring or, for geom AREAS, a list of
    rings; rings left open; longitudes past 180; a top given only in the text, or not at all.
    A ring with fewer than three distinct points cannot be placed, so it is dropped here and the
    Hazard keeps its raw text; a Hazard with no rings at all is reported as unplaced by the
    caller, never as clear.
    US domestic convective SIGMETs, which only `sigmet` carries, come as hazard CONV with no base.
    """
    coords = record.get("coords") or []
    rings_in = coords if coords and isinstance(coords[0], list) else [coords]
    rings = [[(float(p["lon"]), float(p["lat"])) for p in ring] for ring in rings_in]
    kind = str(record.get("hazard") or "UNKNOWN")
    raw = str(record.get("rawSigmet") or "")
    return Hazard(
        hazard_id=f"{record.get('firId')}-{record.get('seriesId')}-{record.get('validTimeFrom')}",
        kind=KINDS.get(kind, kind),
        source="sigmet",
        issued_at=record.get("receiptTime"),
        valid_from=datetime.fromtimestamp(record["validTimeFrom"], UTC),
        valid_to=datetime.fromtimestamp(record["validTimeTo"], UTC),
        base_ft=record.get("base"),
        top_ft=record.get("top") or top_in_text(raw),
        rings=[ring for ring in rings if len(set(ring)) >= 3],
        raw=raw,
    )
