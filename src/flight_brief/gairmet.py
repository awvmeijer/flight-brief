"""G-AIRMET high-level turbulence areas (contiguous US only) as Hazards.

One G-AIRMET package holds snapshots valid at 0, 3, 6, 9 and 12 h; each snapshot describes
one moment, three hours apart. Here a snapshot stands for the 90 minutes either side of its
valid time, so the five together cover the package without gaps. Coordinates and levels
arrive as strings; levels are in hundreds of feet.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from .core.models import Hazard

HALF_WINDOW = timedelta(minutes=90)


def _when(value: Any) -> datetime:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _feet(value: Any) -> int | None:
    try:
        return int(str(value)) * 100
    except (TypeError, ValueError):
        return None  # "SFC", "FZL" or missing: no number to trust


def valid_time(record: dict[str, Any]) -> datetime:
    return _when(record["validTime"])


def from_gairmet(record: dict[str, Any], n: int = 0) -> Hazard:
    """One G-AIRMET area. `raw` is the source's own fields, quoted on the page as its words."""
    at = valid_time(record)
    ring = [(float(p["lon"]), float(p["lat"])) for p in record.get("coords") or []]
    words = [str(record.get("hazard") or ""), str(record.get("severity") or "")]
    if record.get("due_to"):
        words.append(f"DUE TO {record['due_to']}")
    issued = record.get("issueTime")
    return Hazard(
        hazard_id=f"GAIRMET-{record.get('tag')}-{at:%Y%m%dT%H%M}-{n}",
        kind=str(record.get("hazard") or "UNKNOWN"),
        source="gairmet",
        issued_at=_when(issued) if issued else None,
        valid_from=at - HALF_WINDOW,
        valid_to=at + HALF_WINDOW,
        base_ft=_feet(record.get("base")),
        top_ft=_feet(record.get("top")),
        rings=[ring] if len(set(ring)) >= 3 and record.get("geometryType") != "LINE" else [],
        raw=" ".join(w for w in words if w),
    )
