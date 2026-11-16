"""The forecast layer in the brief: GTG over the contiguous US, read from tiles."""

import datetime as dt
from pathlib import Path

import numpy as np

from brief_helpers import AA273, SEVERITY, SQ231, brief, own_strings, utc
from flight_brief import vrs
from flight_brief.brief import GTG_FLAG
from flight_brief.forecast import layer, tiles
from flight_brief.forecast.layer import TileSource
from flight_brief.render import render
from test_tiles import GRID

LEVELS = [300, 330, 360, 390, 420]


def gtg_tile(run: dt.datetime, counts: int = 0) -> tiles.Tile:
    """A whole-grid GTG tile holding one value everywhere (in 0.005 steps)."""
    ny, nx = -(-GRID["ny"] // tiles.BLOCK), -(-GRID["nx"] // tiles.BLOCK)
    edr = np.full((19, len(LEVELS), ny, nx), counts, np.uint8)
    return tiles.Tile(edr, list(range(19)), LEVELS,
                      {"format": tiles.FORMAT, "step": tiles.STEP, "block": tiles.BLOCK,
                       "grid": GRID, "run": run.isoformat()})


def put_gtg(root: Path, run: dt.datetime, counts: int = 0) -> TileSource:
    path = root / f"gtg-tiles-{run:%Y-%m}" / tiles.tile_name(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gtg_tile(run, counts).to_bytes())
    return TileSource(root)


RUN = utc("2026-11-14T00:00")  # the newest GTG run posted by AA273's issue time, 03:00 UTC


def test_runs_before_counts_only_runs_already_posted() -> None:
    at = utc("2026-11-14T01:00")
    assert layer.runs_before(at, layer.GTG_LAG, 2) == [utc("2026-11-13T18:00"),
                                                      utc("2026-11-13T12:00")]
    assert layer.runs_before(utc("2026-11-14T01:30"), layer.GTG_LAG, 1) == [RUN]


def test_tile_source_downloads_once_and_keeps_the_file(tmp_path: Path) -> None:
    calls: list[str] = []
    data = gtg_tile(RUN).to_bytes()

    def get(url: str) -> bytes | None:
        calls.append(url)
        return data if url.endswith(tiles.tile_name(RUN)) else None

    src = TileSource(tmp_path, download=True, get=get)
    found = src.gtg(utc("2026-11-14T03:00"))
    assert found is not None and found[1] == RUN and found[2] == calls[-1]
    assert TileSource(tmp_path).gtg(utc("2026-11-14T03:00")) is not None  # read from the folder
    assert len(calls) == 1


def test_tile_source_falls_back_to_an_older_run_and_reports_a_failed_fetch(tmp_path: Path) -> None:
    older = RUN - dt.timedelta(hours=6)
    data = gtg_tile(older).to_bytes()

    def get(url: str) -> bytes | None:
        if url.endswith(tiles.tile_name(RUN)):
            raise OSError("timed out")
        return data if url.endswith(tiles.tile_name(older)) else None

    src = TileSource(tmp_path, download=True, get=get)
    found = src.gtg(utc("2026-11-14T03:00"))
    assert found is not None and found[1] == older
    assert src.errors and "timed out" in src.errors[0]


def test_a_us_route_gets_a_gtg_line_for_its_hours_over_the_us(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    b = brief(table, *AA273, tiles=put_gtg(tmp_path, RUN))
    us_hours = [h for h in b.hours if "gtg" in h.sources]
    assert us_hours, "AA273 flies over the contiguous US"
    for h in us_hours:
        assert h.sources["gtg"].state == "nothing_found"
        assert h.gtg is not None and h.gtg["edr"] == 0 and h.gtg["run"] == RUN.isoformat()
    assert "GTG speak for" in b.summary
    assert any(p.source == "gtg" and not p.stale for p in b.provenance)


def test_gtg_at_or_over_the_flag_flags_the_hour_and_prints_the_number(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    counts = round(0.30 / tiles.STEP)
    b = brief(table, *AA273, tiles=put_gtg(tmp_path, RUN, counts))
    h = next(h for h in b.hours if "gtg" in h.sources)
    assert h.sources["gtg"].state == "flagged" and h.state == "flagged"
    assert "0.30" in h.sources["gtg"].text and f"{GTG_FLAG:.2f}" in h.sources["gtg"].text
    assert h.gtg is not None and h.gtg["flagged"] and h.gtg["category"] is None
    assert "GTG forecast" in render(b)


def test_no_gtg_tile_leaves_the_line_out_with_a_note(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    b = brief(table, *AA273, tiles=TileSource(tmp_path))
    assert all("gtg" not in h.sources for h in b.hours)
    assert any(p.source == "gtg" and p.stale and "left out" in (p.note or "")
               for p in b.provenance)
    assert "GTG line left out" in render(b)


def test_a_route_outside_the_us_has_no_gtg_line(table: vrs.RouteTable, tmp_path: Path) -> None:
    run = utc("2026-11-03T18:00")
    b = brief(table, *SQ231, tiles=put_gtg(tmp_path, run))
    assert all("gtg" not in h.sources and h.gtg is None for h in b.hours)
    assert all(p.source != "gtg" for p in b.provenance)


def test_gtg_beyond_18_hours_is_too_early_with_a_time_to_check_again(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    code, day, clock, _ = AA273
    early = utc("2026-11-13T09:00")  # about 18 h before departure; newest run is 06Z
    b = brief(table, code, day, clock, early, tiles=put_gtg(tmp_path, utc("2026-11-13T06:00")))
    late = [h for h in b.hours if "gtg" in h.sources and h.sources["gtg"].state == "too_early"]
    assert late
    for h in late:
        check = h.sources["gtg"].check_after
        assert check == h.end - dt.timedelta(hours=layer.GTG_REACH_H) + layer.GTG_LAG


def test_gtg_does_not_cover_a_low_cruise_level(table: vrs.RouteTable, tmp_path: Path) -> None:
    b = brief(table, *AA273, tiles=put_gtg(tmp_path, RUN), cruise_fl=240)
    h = next(h for h in b.hours if "gtg" in h.sources)
    assert h.sources["gtg"].state == "not_covered"


def test_with_tiles_the_brief_reaches_as_far_as_the_index(
        table: vrs.RouteTable, tmp_path: Path) -> None:
    code, day, clock, _ = AA273
    b = brief(table, code, day, clock, utc("2026-11-12T06:00"), tiles=TileSource(tmp_path))
    assert b.status == "ok"  # about 46 h out: inside 54 h
    assert brief(table, code, day, clock, utc("2026-11-12T06:00")).status == "too_far_out"
    far = brief(table, code, day, clock, utc("2026-11-11T06:00"), tiles=TileSource(tmp_path))
    assert far.status == "too_far_out" and "54 hours" in (far.message or "")


def test_the_forecast_lines_name_no_intensity(table: vrs.RouteTable, tmp_path: Path) -> None:
    b = brief(table, *AA273, tiles=put_gtg(tmp_path, RUN, round(0.30 / tiles.STEP)))
    for h in b.hours:
        for key in ("gtg", "shear"):
            if key in h.sources:
                assert not SEVERITY.search(h.sources[key].text)
    assert not any(SEVERITY.search(s) for s in own_strings(b.record()))
