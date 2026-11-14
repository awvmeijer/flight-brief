# Flight Brief

A planned page where a passenger types a flight code and date and gets a weather and turbulence brief, with a public record of how the forecasts did. The design is [`docs/DESIGN.md`](docs/DESIGN.md); the data-source recon is [`docs/RECON.md`](docs/RECON.md).

So far the repo holds the docs, the core package and brief engine with a command-line brief (Phase 1), and the two early pieces of the forecast layer (Phase 2). The forecast layer is not yet read by the brief, and the page is not started.

## The brief (Phase 1)

`brief` resolves a flight code to its airports, takes the departure time as typed, and prints what each source knows for each hour of the flight, read offline from a snapshot directory.

```bash
uv run --extra routes python -m flight_brief.vrs --out data/vrs.sqlite   # once: build the route table
uv run brief SQ231 2026-11-04 --dep 08:00 --as-of 2026-11-03T23:00 --snapshots tests/fixtures/snapshots
```

- **Arguments:** the date and `--dep` are local at the origin. `--as-of` is the UTC time the brief is issued at. `--json` prints the brief record of `docs/DESIGN.md` instead of text. `--leg` picks a leg of a multi-leg number, `--airline` picks one when a code is shared, and `--from` with `--to` give the airports when a number does not map. `--fl` and `--gs` replace the assumed cruise level and groundspeed.
- **Sources read:** SIGMETs from the `sigmet` feed, G-AIRMET high-level turbulence areas over the contiguous US, pilot reports within 100 nm and 4,000 ft of the route, and TAF and METAR for the two airports. The wind-shear index and GTG arrive with Phase 2, so `shear` and `gtg` are null in every record.
- **Snapshots:** the layout of `oceanic-flight-watch`'s store, `<source>/<YYYY-MM-DD>/<HHMM>Z[_<tag>].json.gz`. That store has no G-AIRMETs; `python -m flight_brief.awc gairmet --at <time> --out <dir>` saves them in the same layout.
- **Route table:** built from [VRS standing-data](https://github.com/vradarserver/standing-data) (CC0), with each airport's time zone added by timezonefinder (boundary data from timezone-boundary-builder, ODbL). It is about 37 MB and is not committed.
- **Wording:** the code never writes a severity word. An official source's own text is carried in `quoted` fields and printed inside quotation marks.

The modules under `src/flight_brief/core/` are copied from `oceanic-flight-watch` (MIT, same author); each file says what changed.

## What runs

| Workflow | When | What it does |
|---|---|---|
| `decode-check` | pull requests, manual | Unit tests, then a live check on a Linux runner: fetches GFS 0.25 degree and GTG messages by byte range, decodes them with eccodes, computes the Ellrod TI1 index, prints timing and peak memory |
| `gtg-tiles` | 01:47, 07:47, 13:47, 19:47 UTC | Saves NOAA's GTG turbulence forecast for the contiguous US as compact tiles, then reads the newest one back and compares it with a direct decode at six points |

## GTG tiles

NOMADS keeps two days of GTG files, so the tiles are the only history of that forecast this project will have.

- **Which runs:** 00, 06, 12 and 18 UTC, forecast hours 0 to 18. A late or skipped job catches up on the next start, as long as that is within about a day and a half.
- **What is kept:** combined eddy dissipation rate (GRIB2 parameter 0-19-30) at FL300, 330, 360, 390 and 420, as the highest value in each 12 km cell (4 x 4 points of the 3 km grid), in 8-bit steps of 0.005 m^2/3 s^-1.
- **What is not kept:** the 3 km detail, the other 46 levels, and the split into clear-air, mountain-wave and convective turbulence.
- **Where:** assets of the release `gtg-tiles-YYYY-MM` in this repository, one file per run, about 4.6 MB each. Long-term storage is still open in the design.
- **Format:** a numpy `.npz`, described in [`src/flight_brief/forecast/tiles.py`](src/flight_brief/forecast/tiles.py).

Reading one:

```python
from flight_brief.forecast.tiles import Tile

tile = Tile.from_bytes(open("gtg_conus_edr_12km_20261114T12Z.npz", "rb").read())
tile.sample(39.86, -104.67, flight_level=360, forecast_hour=6)   # EDR near Denver
```

## Working commands

```bash
uv sync
uv run pytest
uv run brief SQ231 2026-11-04 --dep 08:00 --as-of 2026-11-03T23:00 --snapshots tests/fixtures/snapshots
uv run python -m flight_brief.jobs.decode_check
uv run python -m flight_brief.jobs.gtg_tiles --store dir:/tmp/tiles --max-runs 1 --verify
```

Sources are keyless NOAA services only: GFS from the public AWS bucket, GTG from NOMADS, with a pause after every NOMADS request.
