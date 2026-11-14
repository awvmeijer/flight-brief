Snapshot files for the replay tests, in the layout of `oceanic-flight-watch`'s store.

- `sigmet`, `taf`, `metar`, `pirep`: unchanged copies from that store (NOAA Aviation Weather
  Center data, public domain) for the moments the test briefs are issued at:
  2026-11-03 00:00, 01:00, 18:00 and 23:00 UTC, 2026-11-08 01:00 UTC and 2026-11-14 03:00 UTC.
  Pilot reports are copied for the boxes each test route crosses only.
- `gairmet`: fetched on 2026-11-14 with `python -m flight_brief.awc gairmet --at <time>`,
  because that store does not collect G-AIRMETs.
