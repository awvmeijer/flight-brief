# Recon scripts and results

The throwaway scripts behind the measurements in [`../RECON.md`](../RECON.md), and the per-report results of the skill measurement. They are kept so the recon's numbers can be re-derived. They are not product code: no tests, no packaging, hard-coded dates.

## Files

| File | What it does | Recon section |
|---|---|---|
| `pirep_load.py` | Reads the pilot-report snapshots of the `oceanic-flight-watch` store, removes duplicates (same observation time and raw text), writes a pickle | (d) |
| `pirep_regions.py` | Counts reports by region, intensity, type, carrier, hour and 10-degree cell | (d) |
| `routehours.py` | Share of simulated route-hours with any report in a match window, for three windows | (d) |
| `routehours2.py` | The same for the 100 nm, 90 minute, 4,000 ft window | (d) |
| `fallback_skill.py` | Samples wind-shear diagnostics from open GFS at pilot-report positions and writes one CSV row per report | (a) |
| `fallback_rows.csv` | Its output from the recon run: 2,829 reports, 140 forecasts | (a) |
| `auc.py` | AUC, bootstrap intervals and operating points from that CSV | (a) |
| `rangeget.py` | Fetches selected GRIB2 messages by byte range, using the `.idx` file | (a) |
| `decode.py` | Decodes GRIB2 with eccodes, computes the Ellrod TI1 index, prints timing and memory | (a) |

## Running them

```
uv run --no-project --with eccodes --with numpy python -I <script> <arguments>
```

| Step | Command |
|---|---|
| Load the reports | `PIREP_SNAPSHOTS=<store>/data/snapshots/pirep python pirep_load.py pirep.pkl` |
| Density by region | `python pirep_regions.py pirep.pkl` |
| Route-hour coverage | `python routehours.py pirep.pkl` |
| Skill measurement | `python fallback_skill.py pirep.pkl rows.csv 2` (the 2 means every second day) |
| Skill figures | `python auc.py fallback_rows.csv` |
| Fetch messages | `python rangeget.py <url without .idx> out.grib2 "UGRD:250 mb" "VGRD:250 mb"` |
| Decode | `python decode.py gfs a.grib2 b.grib2` (needs UGRD, VGRD and HGT at 200, 225, 250, 275 and 300 mb), or `python decode.py gtg out.grib2` |

`auc.py fallback_rows.csv` needs no download and no store. Checked on 13 November 2026, it prints the recon's figures: AUC 0.764 for `ti1_nb`, moderate-or-greater against smooth, interval 0.739 to 0.790.

`fallback_skill.py` downloads about 1.6 GB by byte range from NOAA's public AWS bucket and took about 12 minutes in the recon.

## Columns of `fallback_rows.csv`

- `valid`, `lead_h`: the forecast's valid time and its lead in hours.
- `obs_time`, `lat`, `lon`, `fl`: the report's time (epoch seconds), position and flight level.
- `cls`: `MOG` (moderate or greater), `LGT` (light) or `NEG` (smooth). `raw_int` is the intensity as reported.
- `type`, `box`: report type, and the store's collection box.
- `layer_fl`: the flight level of the forecast layer used (320, 363 or 415).
- Six diagnostics, each in three forms: `ti1` and `ti2` (Ellrod indices, 1e-7 s^-2), `vws` (vertical wind shear, 1e-3 s^-1), `def` (deformation, 1e-5 s^-1), `spd` (wind speed, m/s), `invri` (inverse Richardson number).
  - `_pt`: the value at the nearest grid point.
  - `_nb`: the highest value within 0.5 degree. This is the form the recon reports.
  - `_col`: the highest value within 0.5 degree across the nearest layer and the layers next to it.

## What differs from what was run

One line. `pirep_load.py` had the snapshot directory written in as a path on the machine it ran on; it now reads it from `PIREP_SNAPSHOTS`. Every other file is byte-identical to what the recon ran.

## Limits

- The store keeps growing, so `pirep_load.py` now counts more reports than the recon's 126,156.
- The dates in `fallback_skill.py` and the route-hour scripts are fixed to the recon's period, 4 September to 11 November 2026.
- The store has three boxes only: West Pacific, East Pacific to 110°W, and Indian Ocean.

## The estimate's cut points (19 November 2026)

Version 0.3 names a strength where no official forecast exists: an estimate from the wind-shear index (`src/flight_brief/ride.py`). `estimate_calibration.py` is how its cut points were set and checked. Unlike the scripts above it uses the project's own tile code, so it runs inside the project: `uv run python docs/recon/estimate_calibration.py <step> ...` (its first lines list the three steps).

| File | What it is |
|---|---|
| `estimate_calibration.py` | The three steps: `reports`, `match`, `table` |
| `estimate_match.json` | Result of `match`: the cut points and how the estimate and GTG agree |
| `estimate_rows.csv.gz` | Result of `reports`: 34,190 pilot reports with the tile value at each (`valid, lead_h, obs_time, lat, lon, fl, cls, stated, layer_fl, index`) |

**How the cut points were set (`match`).** Over the contiguous US both NOAA's GTG forecast and the index exist. From the tiles published by this repository's jobs for 13 to 18 November 2026 (26 GTG runs, 18 index runs, 142 pairs of forecast hours valid at the same time), 42,600 simulated ten-minute stretches of flight (80 nm, a point every 20 nm, random start and heading, a random GTG level) were sampled from both, taking the highest value along each, as the brief does. The index cut for a word is the value the index reaches as often as GTG reaches NOAA's own threshold for that word.

| Weight class | NOAA's EDR for light, moderate | Share of stretches at or above | Index at the same share |
|---|---|---|---|
| light | 0.13, 0.16 | 28.1%, 15.5% | 8.0, 12.0 |
| medium | 0.15, 0.20 | 18.9%, 6.3% | 10.5, 20.0 |
| heavy | 0.17, 0.24 | 12.5%, 2.3% | 14.0, 30.0 |

Agreement of the two on the same stretch, medium class: the same word on 76.7%, within one word on 95.3%; rank correlation of the two values 0.61. Of the stretches the estimate calls moderate, GTG calls 30% moderate, 35% light and 35% smooth, and the reverse is about the same. So the estimate says each word about as often as the official forecast, but not always in the same place.

**How it stands against pilot reports (`reports`, `table`).** 34,190 cruise-level reports (FL290 to FL440) with a stated intensity from 14 October to 19 November 2026, each within 90 minutes of a 24 or 27 hour forecast, the index read as a tile holds it (highest within 0.5 degree, nearest layer). Medium class:

| Estimate says | Reports | Smooth | Light | Moderate or more |
|---|---|---|---|---|
| smooth (index under 10.5) | 21,164 | 30% | 56% | 15% |
| light (10.5 to under 20) | 7,187 | 12% | 57% | 31% |
| moderate (20 and up) | 5,839 | 7% | 49% | 44% |

Pilots report bumps far more often than smooth air (22% of these reports are smooth), so the shares say how the three words rank, not how often a flight meets turbulence. Only 59 reports were severe or more, which is one reason the estimate never says severe.

**Limits.** Six days of tiles, one season, one region: the cut points are a first setting, to be redone when more tiles have accumulated. The pilot reports are almost all from the US. GTG is itself a forecast, not the truth. The matching ignores that the two grids differ in resolution (GTG 12 km blocks, the index 0.5 degree neighbourhoods).
