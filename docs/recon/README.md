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
