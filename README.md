# Flight Brief

A planned page where a passenger types a flight code and date and gets a weather and turbulence brief, with a public record of how the forecasts did. The design is [`docs/DESIGN.md`](docs/DESIGN.md); the data-source recon is [`docs/RECON.md`](docs/RECON.md).

This is version 0.2: a brief from the command line or from a local page, fetched live from NOAA's open services, with a wind-shear index worldwide and NOAA's GTG turbulence forecast over the contiguous US. On the page you find a flight by its route and date or by its number, and the page is drawn after [`docs/UI.md`](docs/UI.md). Not yet built: a hosted page, the fixed panel of flights, and the public scorecard (Phases 4 and 5 of the design).

## The brief

`brief` resolves a flight code to its airports, takes the departure time as typed, and prints what each source knows for each hour of the flight. By default it is live: it fetches what the brief needs from NOAA's Aviation Weather Center at that moment. With `--snapshots` it reads a snapshot directory offline instead.

```bash
uv run --extra routes python -m flight_brief.vrs --out data/vrs.sqlite   # once: build the route table
uv run brief UA29 2026-11-17 --dep 08:45                                  # live, for a flight in the next 54 hours
uv run brief SQ231 2026-11-04 --dep 08:00 --as-of 2026-11-03T23:00 --snapshots tests/fixtures/snapshots   # offline replay
```

- **Live:** without `--snapshots` the brief is issued now, for a flight that departs within the next 54 hours (30 with `--no-forecast`). One brief makes 3 to about 15 requests: one worldwide `sigmet` call, `taf` and `metar` for the two airports, the five G-AIRMET snapshots when the route crosses the contiguous US, and pilot reports for boxes along the part of the route flown within the next 6 hours, where the region has reports. What comes back is kept as snapshots in `--cache` (default `~/.cache/flight-brief/live`, or `$FLIGHT_BRIEF_CACHE`) and reused for 30 minutes, also by a brief for another flight. A request that fails or times out (10 seconds) does not stop the brief: that source reads "last updated ... ago". `--as-of` belongs to `--snapshots` and is refused without it. `FLIGHT_BRIEF_DEBUG=1` prints one line per request or cache hit on stderr. The cache is never cleared; delete the directory to start over.

- **Arguments:** the date and `--dep` are local at the origin, and `--dep` and `--arr` are gate times. However the groundspeed is found (`--arr`, `--gs`, `--aircraft` or the distance), the brief counts 30 minutes on the ground: take-off 20 minutes after the departure time, and at the gate 10 minutes after landing. Hour 1 starts at take-off. `--as-of` is the UTC time an offline brief is issued at. `--json` prints the brief record of `docs/DESIGN.md` instead of text. `--leg` picks a leg of a multi-leg number, `--airline` picks one when a code is shared, and `--from` with `--to` give the airports when a number does not map. `--fl` and `--gs` replace the assumed cruise level and groundspeed. `--aircraft` takes an ICAO type code such as `AT72` or `A320`, a family or maker's name such as `777`, `A350` or `Boeing 777-300ER`, or the word `turboprop`, `jet` or `piston`; a type that is not recognised is noted in the brief's assumptions and the distance decides: a turboprop or piston type sets the default cruise level and groundspeed from this project's own table of round numbers, while a jet type leaves the distance defaults as they are. `--arr` and `--gs` take precedence over the type for the speed, and `--fl` for the level. `--arr` is the local arrival time at the destination: with it the groundspeed is worked out from the two gate times less 30 minutes on the ground, so each hour lines up with the right stretch of the route; without it the arrival is an estimate that counts no winds.
- **Sources read:** SIGMETs from the `sigmet` feed, G-AIRMET high-level turbulence areas over the contiguous US, pilot reports within 100 nm and 4,000 ft of the route, and TAF and METAR for the two airports. On top of those come the two forecasts, read from the tiles the scheduled jobs publish (see below):
  - **Wind-shear index, worldwide.** For each flight hour the highest index value along that stretch, at cruise levels FL290 to FL440, up to 54 hours after the GFS run. At 8 or more the hour reads "Elevated wind shear", with the measured rates beside it: that flag catches 72% of rough-air pilot reports and also 35% of smooth ones (`docs/RECON.md`). It is an index, not a turbulence forecast, and names no intensity.
  - **GTG, contiguous US.** NOAA's turbulence forecast for hours over the US within 18 hours of its run, as an eddy dissipation rate. The hour is flagged at 0.22, which is this project's own number: NOAA's intensity names depend on the aircraft, so none is used, and the flag's hit rate has not been measured yet.
  - **Where the tiles come from:** a live brief downloads the newest tile of each kind from this repository's releases into `--tiles` (default `~/.cache/flight-brief/tiles`, or `$FLIGHT_BRIEF_TILES`): about 11 MB for the index and 5 MB for GTG, once per run. `--no-forecast` leaves both out. With `--snapshots` the forecasts are read only when `--tiles` names a folder, and nothing is downloaded. A tile that is missing reads "no tile available", never "nothing found"; a run more than 18 hours old (9 for GTG) is used and named as old.
  - **Where two forecasts speak for the same hour** the brief says whether they agree.
- **Snapshots:** live and offline use one layout, that of `oceanic-flight-watch`'s store, `<source>/<YYYY-MM-DD>/<HHMM>Z[_<tag>].json.gz`. That store has no G-AIRMETs; `python -m flight_brief.awc gairmet --at <time> --out <dir>` saves them in the same layout.
- **Route table:** built from [VRS standing-data](https://github.com/vradarserver/standing-data) (CC0), including its aircraft type codes, with each airport's time zone added by timezonefinder (boundary data from timezone-boundary-builder, ODbL). Airports VRS does not have come from a small supplement, `src/flight_brief/data/airports_extra.csv`, copied from [OurAirports](https://ourairports.com/data/) (public domain, The Unlicense); so far one, Totegegie in the Gambier Islands. Routes VRS does not have are in `routes_extra.csv` beside it: each is one fact typed by hand from the airline's schedule, not copied from a database; so far one, VT956 Papeete to Gambier. It is about 37 MB and is not committed.
- **The plain answer:** each ten minutes of the flight gets one of four words, smooth, light, moderate or severe, and the source that names it (`src/flight_brief/ride.py`, the `ride` block of a brief):
  - **official warning:** a SIGMET for turbulence or mountain waves (issued for severe turbulence) or a G-AIRMET high-level turbulence area (issued for moderate) in force on that stretch;
  - **NOAA forecast:** GTG's value named with [NOAA's own thresholds](https://aviationweather.gov/gfa/help/) for the aircraft's weight class (medium when no aircraft type is given);
  - **estimate:** where no official forecast exists, the wind-shear index. Its cut points are set so that over the US it says light and moderate as often as GTG does on the same stretches; it never says severe, and it is drawn and labelled as an estimate. How well it agrees with GTG and with pilot reports is in [`docs/recon/README.md`](docs/recon/README.md).
  - Where nothing speaks, the answer is "not known yet", never smooth. Where GTG speaks, the estimate is not used. A warning can only raise the word.
- **Storms, wind and crosswind:** a thunderstorm warning on the route is told as a storm; with the wind and storm-cloud tile (below) the brief also says where the model's storm clouds reach within 5,000 ft of the cruise level, and the headwind or tailwind per hour. At each airport it gives the crosswind on the runway best aligned with the forecast wind (runway directions from [OurAirports](https://ourairports.com/data/), public domain); the runway in use is not known in advance.
- **Wording:** a strength word stands only in what `ride.py` writes, always with its source, and in an official source's own text, which is carried in `quoted` fields and printed inside quotation marks. Never "safe", "unsafe" or a score.

The modules under `src/flight_brief/core/` are copied from `oceanic-flight-watch` (MIT, same author); each file says what changed.

## The page

`brief-web` serves the same brief as a page, server-rendered on the standard library. The design is in [`docs/UI.md`](docs/UI.md): Breakwise's dark sheet, one green, drawn as a head-up display.

```bash
uv run brief-web --port 8000 --saved data/saved      # live: open http://127.0.0.1:8000/
uv run brief-web --port 8000 --snapshots tests/fixtures/snapshots --as-of 2026-11-03T23:00 --saved data/saved
# offline demo: open http://127.0.0.1:8000/f/SQ231/2026-11-04/1?dep=08:00
```

Without `--snapshots` the page is live, like the command line: it fetches from NOAA on request, keeps what it fetched for 30 minutes, and downloads the forecast tiles (`--tiles`, `--no-forecast` as on the command line). It is meant to run on your own machine: it serves one request at a time and is not hosted anywhere yet.

Two optional keys, read from the environment and from nowhere else:

| Variable | What it turns on | Without it |
|---|---|---|
| `AERODATABOX_KEY` | Scheduled flights from [AeroDataBox](https://aerodatabox.com/) through RapidAPI: the flights of a route on a day with their times and aircraft, and the time of a flight number | A route lists the flight numbers the route table knows, and you type the departure time |
| `MAPBOX_TOKEN` | The route on a Mapbox globe, style `navigation-night-v1` | The route window says the map is not set up |

- **Finding a flight:** `/` has two forms. The first takes where from, where to (a city, an airport name or a code) and the date, and goes to `/r/{FROM}/{TO}/{date}`, which lists that day's flights; each one links to its brief with the time, the aircraft and both airports already in the address. Several airports that fit what was typed become a list to choose from. The second form takes a flight number and a date; with a schedule key the departure time may be left empty.
- **Schedule use:** one route search is two calls of 2 units each, one flight-number look-up is 2 units, and the free plan has 400 units a month. A whole airport-day is kept in `<cache>/schedule` and reused for 6 hours, so another destination from the same airport costs nothing; files older than 7 days are deleted, which is AeroDataBox's retention limit. A ledger per month stops calling 20 units short of 400 and the page falls back to the route table. Their free plan is for non-commercial use and requires the credit the page shows. None of this has been run against the live service yet: it is built from their published specification.
- **Addresses:** a brief lives at `/f/{code}/{date}/{leg}?dep=HH:MM`, one per flight and leg, with the code in capitals. `/go` is the flight-number form's target and redirects there; a number with several legs or several departures becomes a list of links. Optional parameters: `arr`, `aircraft`, `from` and `to` (the two airports), `fl`, `gs` and `airline`, each meaning what the command line's option means.
- **What it shows:** the flight between its two airports as head-up-display symbology (initial track, groundspeed, cruise level); then the window "The ride": the answer in sentences, one bar per ten minutes of flight grouped by hour, and what each of the four words means for a passenger; the route map, coloured by that answer; a window for what the brief cannot know yet; and the two airports with their crosswind. Everything else sits under one fold, "Show the evidence", which opens without JavaScript: how the words were chosen, the hour strip, one lane per source with a cell per hour, what each source says in words, the assumptions, and every file the brief read.
- **World map:** `/map` shows the estimate worldwide and the model's storm clouds reaching FL300 or higher, for each forecast hour of the newest run and three cruise levels. It needs `MAPBOX_TOKEN`. It shows the estimate only; NOAA's official forecast for the US is on a different grid and is not on this map.
- **Scripts:** the page works without JavaScript except for the map. `static/app.js` adds airport suggestions (from `/airports?q=`) and the loading screen shown while a brief is made; `static/map.js` draws the map. With a Mapbox token the visitor's browser contacts Mapbox for the map; otherwise it contacts no other host.
- **Fonts:** the design uses Switzer, whose licence forbids putting the files in a public repository, so they are not in git. Copy `switzer-*.woff2` from the design package into `src/flight_brief/static/straat/fonts/`; without them the page falls back to Inter or the system font.
- **Data:** with `--snapshots` it reads that store as known at `--as-of`. Without it the page is live and calls `flight_brief.live.fetch` to fill `--cache`.
- **Saved briefs:** with `--saved`, every brief that renders is written once with its inputs to `<dir>/<code>/<date>/leg<leg>-<issued>-<hash>.json`, where the hash is of what was typed. `/f/{code}/{date}/{leg}/saved/{issued}-{hash}` draws that record again without reading or fetching anything, and says it is a saved brief and when it was issued.
- **Limits:** every value is checked against a strict pattern before anything else happens; 30 brief, route and look-up pages per address per 10 minutes, kept in memory; those pages carry `noindex`; a Content-Security-Policy allows the app's own files and Mapbox's, and no inline style or script. `--behind-proxy` takes the address from `X-Forwarded-For`. The server is `wsgiref`, one request at a time.

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
- **What is not kept:** the value at the grid point alone, other levels, and forecast hours past 54.
- **A second, small tile per run** (`gfs_global_wx_<run>Z.npz`, about 4 MB, same release): the winds of the three layers at every whole degree in 2-knot steps, and how high the model's convective clouds reach within 0.5 degree, as a flight level in steps of 5. The cloud top is a model field at coarse resolution: it says where the model expects deep convection, not where a storm cell will be. The brief does not read this tile yet. Format: [`src/flight_brief/forecast/wx_tiles.py`](src/flight_brief/forecast/wx_tiles.py).
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
uv run pytest                      # no network
uv run pytest -m network           # the tests that fetch from NOAA
uv run brief UA29 2026-11-17 --dep 08:45   # live: your flight, local date and time at the origin
FLIGHT_BRIEF_DEBUG=1 uv run brief UA29 2026-11-17 --dep 08:45 --cache /tmp/brief-cache   # show each request
uv run brief SQ231 2026-11-04 --dep 08:00 --as-of 2026-11-03T23:00 --snapshots tests/fixtures/snapshots
uv run brief-web --port 8000 --snapshots tests/fixtures/snapshots --as-of 2026-11-03T23:00
uv run python -m flight_brief.jobs.decode_check
uv run python -m flight_brief.jobs.gtg_tiles --store dir:/tmp/tiles --max-runs 1 --verify
uv run python -m flight_brief.jobs.shear_tiles --store dir:/tmp/tiles --max-runs 1 --hours 6,30,54 --verify
```

Sources are keyless NOAA services only: the Aviation Weather Center API for the live brief (at most 100 requests a minute, one plain call each), GFS from the public AWS bucket, GTG from NOMADS, with a pause after every NOMADS request.
