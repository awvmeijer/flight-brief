# Flight Brief

A planned page where a passenger types a flight code and date and gets a weather and turbulence brief, with a public record of how the forecasts did. The design is [`docs/DESIGN.md`](docs/DESIGN.md); the data-source recon is [`docs/RECON.md`](docs/RECON.md).

So far the repo holds the docs, the core package and brief engine with a command-line brief (Phase 1), and three early pieces of the forecast layer (Phase 2). The forecast layer is not yet read by the brief, and the page is not started.

## The brief (Phase 1)

`brief` resolves a flight code to its airports, takes the departure time as typed, and prints what each source knows for each hour of the flight, read offline from a snapshot directory.

```bash
uv run --extra routes python -m flight_brief.vrs --out data/vrs.sqlite   # once: build the route table
uv run brief SQ231 2026-11-04 --dep 08:00 --as-of 2026-11-03T23:00 --snapshots tests/fixtures/snapshots
```

- **Arguments:** the date and `--dep` are local at the origin, and `--dep` and `--arr` are gate times. However the groundspeed is found (`--arr`, `--gs`, `--aircraft` or the distance), the brief counts 30 minutes on the ground: take-off 20 minutes after the departure time, and at the gate 10 minutes after landing. Hour 1 starts at take-off. `--as-of` is the UTC time the brief is issued at. `--json` prints the brief record of `docs/DESIGN.md` instead of text. `--leg` picks a leg of a multi-leg number, `--airline` picks one when a code is shared, and `--from` with `--to` give the airports when a number does not map. `--fl` and `--gs` replace the assumed cruise level and groundspeed. `--aircraft` takes an ICAO type code such as `AT72` or `A320`, or the word `turboprop`, `jet` or `piston`: a turboprop or piston type sets the default cruise level and groundspeed from this project's own table of round numbers, while a jet type leaves the distance defaults as they are. `--arr` and `--gs` take precedence over the type for the speed, and `--fl` for the level. `--arr` is the local arrival time at the destination: with it the groundspeed is worked out from the two gate times less 30 minutes on the ground, so each hour lines up with the right stretch of the route; without it the arrival is an estimate that counts no winds.
- **Sources read:** SIGMETs from the `sigmet` feed, G-AIRMET high-level turbulence areas over the contiguous US, pilot reports within 100 nm and 4,000 ft of the route, and TAF and METAR for the two airports. The wind-shear index and GTG arrive with Phase 2, so `shear` and `gtg` are null in every record.
- **Snapshots:** the layout of `oceanic-flight-watch`'s store, `<source>/<YYYY-MM-DD>/<HHMM>Z[_<tag>].json.gz`. That store has no G-AIRMETs; `python -m flight_brief.awc gairmet --at <time> --out <dir>` saves them in the same layout.
- **Route table:** built from [VRS standing-data](https://github.com/vradarserver/standing-data) (CC0), including its aircraft type codes, with each airport's time zone added by timezonefinder (boundary data from timezone-boundary-builder, ODbL). Airports VRS does not have come from a small supplement, `src/flight_brief/data/airports_extra.csv`, copied from [OurAirports](https://ourairports.com/data/) (public domain, The Unlicense); so far one, Totegegie in the Gambier Islands. Routes VRS does not have are in `routes_extra.csv` beside it: each is one fact typed by hand from the airline's schedule, not copied from a database; so far one, VT956 Papeete to Gambier. It is about 37 MB and is not committed.
- **Wording:** the code never writes a severity word. An official source's own text is carried in `quoted` fields and printed inside quotation marks.

The modules under `src/flight_brief/core/` are copied from `oceanic-flight-watch` (MIT, same author); each file says what changed.

## What runs

| Workflow | When | What it does |
|---|---|---|
| `decode-check` | pull requests, manual | Unit tests, then a live check on a Linux runner: fetches GFS 0.25 degree and GTG messages by byte range, decodes them with eccodes, computes the Ellrod TI1 index, prints timing and peak memory |
| `gtg-tiles` | 01:47, 07:47, 13:47, 19:47 UTC | Saves NOAA's GTG turbulence forecast for the contiguous US as compact tiles, then reads the newest one back and compares it with a direct decode at six points |
| `shear-tiles` | 04:37, 10:37, 16:37, 22:37 UTC | Computes the Ellrod TI1 wind-shear index from each GFS run, worldwide, and saves the highest value within 0.5 degree of every grid point as one compact tile, then reads the newest one back and compares it with a direct computation at six points |

## GTG tiles

NOMADS keeps two days of GTG files, so the tiles are the only history of that forecast this project will have.

- **Which runs:** 00, 06, 12 and 18 UTC, forecast hours 0 to 18. A late or skipped job catches up on the next start, as long as that is within about a day and a half.
- **What is kept:** combined eddy dissipation rate (GRIB2 parameter 0-19-30) at FL300, 330, 360, 390 and 420, as the highest value in each 12 km cell (4 x 4 points of the 3 km grid), in 8-bit steps of 0.005 m^2/3 s^-1.
- **What is not kept:** the 3 km detail, the other 46 levels, and the split into clear-air, mountain-wave and convective turbulence.
- **Where:** assets of the release `gtg-tiles-YYYY-MM` in this repository, one file per run, about 4.6 MB each. Long-term storage is still open in the design.
- **Format:** a numpy `.npz`, described in [`src/flight_brief/forecast/tiles.py`](src/flight_brief/forecast/tiles.py).
- **Heartbeat:** after every start that ends well, the job overwrites `gtg-tiles.json` in the release `heartbeats` with when it finished and the newest run the store holds. The page's "source stale" state will read it; a job that keeps failing shows as an old heartbeat. See [`heartbeat.py`](src/flight_brief/forecast/heartbeat.py).

Reading one:

```python
from flight_brief.forecast.tiles import Tile

tile = Tile.from_bytes(open("gtg_conus_edr_12km_20261114T12Z.npz", "rb").read())
tile.sample(39.86, -104.67, flight_level=360, forecast_hour=6)   # EDR near Denver
```

## Wind-shear index tiles

Worldwide, the page will show a wind-shear index computed from open GFS winds. It is an index of wind shear, not a turbulence forecast, and it carries no severity: the brief only says whether it is at or above a threshold, with the rates the recon measured for that threshold.

- **Which runs:** GFS 00, 06, 12 and 18 UTC from NOAA's public AWS bucket, forecast hours 6 to 54 every 3 h (17 hours). A run is taken once its hour-54 index is posted, about 4 h after run time. Each start saves every run of the last two days that the store lacks, oldest first.
- **What is kept:** Ellrod TI1 (vertical wind shear times deformation) in units of 1e-7 s^-2, for three layers: 300-250 hPa (about FL320), 250-200 hPa (FL363) and 200-150 hPa (FL415), on the 0.25 degree grid, in 8-bit steps of 0.5. Values are rounded down, so the thresholds 8 and 12 compare exactly as the unrounded values would. Values of 127 and above are stored as 127. There is no value poleward of 85 degrees.
- **Within 0.5 degree:** each grid point holds the highest value among the 5 x 5 grid points centred on it: two points (0.5 degree) either side in latitude and in longitude, both ends included, wrapping in longitude. A reader takes one value at the grid point nearest its position. This is the neighbourhood of the recon's measurement ([`docs/recon/fallback_skill.py`](docs/recon/fallback_skill.py)), so its rates carry over. It is a square, and because a position goes to its nearest grid point first, the reach from the position itself is 0.5 degree give or take 0.125.
- **What is not kept:** the winds themselves, the value at the grid point alone, other levels, and forecast hours past 54.
- **Where:** assets of the release `shear-tiles-YYYY-MM` in this repository, one file per run, about 11 MB each (about 1.3 GB a month). Long-term storage is still open in the design.
- **Format:** a numpy `.npz`, described in [`src/flight_brief/forecast/shear_tiles.py`](src/flight_brief/forecast/shear_tiles.py).
- **Heartbeat:** `shear-tiles.json` in the release `heartbeats`, written as for the GTG job.

Reading one:

```python
import datetime as dt
from flight_brief.forecast.shear_tiles import ShearTile

tile = ShearTile.from_bytes(open("gfs_global_ti1_0p25_20261114T12Z.npz", "rb").read())
when = dt.datetime(2026, 11, 15, 9, 20, tzinfo=dt.timezone.utc)
tile.sample(51.47, -0.46, flight_level=370, valid_time=when)   # (value, layer flight level, forecast hour) or None
```

`sample` uses the layer whose flight level is nearest and the forecast hour nearest the time, and returns `None` when no forecast hour is within 1.5 h, when the latitude is poleward of 85 degrees, or when the cell has no data.

## Working commands

```bash
uv sync
uv run pytest
uv run brief SQ231 2026-11-04 --dep 08:00 --as-of 2026-11-03T23:00 --snapshots tests/fixtures/snapshots
uv run python -m flight_brief.jobs.decode_check
uv run python -m flight_brief.jobs.gtg_tiles --store dir:/tmp/tiles --max-runs 1 --verify
uv run python -m flight_brief.jobs.shear_tiles --store dir:/tmp/tiles --max-runs 1 --hours 6,30,54 --verify
```

Sources are keyless NOAA services only: GFS from the public AWS bucket, GTG from NOMADS, with a pause after every NOMADS request.
