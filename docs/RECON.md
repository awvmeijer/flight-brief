# Flight Brief: Phase 0 reconnaissance

What each source actually provides, checked against live documents and live responses on 12 November 2026 (09:30 to 14:30 UTC). Anything that differs from the approved design is listed first. "Measured" means measured in this session, from a live response or from the local snapshot store (`oceanic-flight-watch/data/snapshots/pirep`, 71 days, 3 September to 12 November 2026). "Research agent" marks items gathered by a delegated research run that I did not re-open myself; I say where I spot-checked them.

No product code was written, no repository created, nothing pushed. Probes were keyless with a generic User-Agent. The Tomorrow.io API was not called.

## Differences from what was assumed

| Design assumed | Found | Consequence |
|---|---|---|
| The WIFS page is unreachable; access unknown | It loads (HTTP 200 in under 1 s, measured). "WIFS is not an open system": every user needs approval by a State meteorological authority and the FAA | Access is an application with an uncertain outcome, not a signup |
| WAFS files might be on NOMADS under another name | **Withdrawn on purpose** on or about 17 January 2024 (Service Change Notice 23-111). Measured: none on NOMADS, none in the AWS GFS bucket | No keyless path to the global WAFS turbulence grid |
| Grid horizon "about 36 h, to confirm" | WAFS turbulence runs to **T+48**; the public US GTG grid to **T+18**; open GFS winds to T+384 | The knowability table needs three different horizons |
| No public NOAA turbulence grid | A **US-only** GTG grid at 3 km is public and keyless (NOMADS `dafs`) | US flights inside 18 h can show a real turbulence forecast |
| The fallback is "wind-shear context", strength unknown | The pre-2020 official WAFS product was itself this index (Ellrod TI1). Measured AUC 0.76 for moderate-or-greater against smooth reports | The fallback is weaker in claims (no severity) more than in measurable skill against pilot reports |
| Jet stream context could be shown from wind speed | Wind speed alone scores AUC 0.53 (no skill) | Never show wind speed as a turbulence hint; use shear times deformation |
| adsbdb gives airports, usable | Code is MIT, but the route data "may not be copied, published, or incorporated into other databases without the explicit permission of David J Taylor" | Do not cache adsbdb routes; use VRS standing-data (CC0) for callsign to airports |
| A schedule source exists for free | Amadeus self-service is decommissioned. AeroDataBox is the only source with clear terms: free tier non-commercial, 7-day retention limit | Saved briefs must not keep API departure times; see (b) |
| Track history per callsign, or bulk dumps | adsb.lol: live lookup by callsign, then a rolling trace of about the last 24 h **by aircraft hex**. History is bulk only, about 3.6 GB per day | Panel tracks must be captured within a day of each flight |
| Store ends 15 October; collector dead | The store now runs to 12 November with no missing day (measured: 24 files a day, 3,021 files) | The top-of-design warning is resolved |
| Eastern Pacific box is ocean | The `epac` box reaches 110°W, so it includes the US West. **54% of all cruise-level turbulence reports in the store sit over US land** | "Oceanic" density in the design was mostly US domestic traffic |
| 2,454 smooth-air vs 6,752 positive reports | 4,355 smooth (NEG) vs 14,046 positive: 23.7% of reports that mention turbulence; 16.6% at cruise level | A false-alarm measure on smooth reports is possible where reports are dense |
| Scoring unit: one route-hour, window 100 nm, 1 h, 4,000 ft | Only 26 to 28% of route-hours on US West routes, 5 to 8% across the North Pacific, under 1% in the South Pacific have any report in that window | Score per report first; route-hours second, with coverage shown |
| `isigmet` plus a separate US feed | `/api/data/sigmet` returns international and US domestic together. The spec marks `isigmet` and `airsigmet` `deprecated: true` | One call covers both; the existing collector should migrate |
| Tomorrow.io headroom: "a paid plan, or ask them" | No paid price is published; extra keys come "by your account manager"; commercial use on self-signup accounts appears prohibited (unconfirmed) | Tomorrow.io becomes optional in Phase 3; one email decides |
| Jobs run every 30 minutes on GitHub Actions | GitHub: runs "can be delayed", "some queued jobs may be dropped". Community measurements in 2026: median delay about 26 minutes, some runs missing | Nothing may depend on a 30-minute cadence |

## Verdicts

| Item | Verdict | In one line |
|---|---|---|
| (a) Turbulence grid, official WAFS | **Refuted** for a keyless path; **needs Anthony's action** for a WIFS key | NOAA pulled the global grid from public servers in January 2024; WIFS is approval-only |
| (a) Fallback from open GFS | **Confirmed** workable, weaker in what it may claim | Measured AUC 0.76 against pilot reports at 24 h lead; no severity, no mountain-wave or storm component |
| (a) Public US-only GTG grid | **Confirmed** (not in the design) | Real turbulence forecast, keyless, contiguous US, 18 h horizon |
| (b) Flight resolve | **Confirmed with conditions** | Times need a paid or limited API; adsbdb's route data may not be copied; no rule maps a flight number to a callsign |
| (c) Tomorrow.io terms | **Needs Anthony's action** | Legal and pricing pages are behind a bot check; attribution is clear, commercial use on the free key is probably prohibited, no price is published |
| (d) Scoring method | **Confirmed**, with a different unit than the design's | Score per pilot report, not per route-hour; of the three boxes collected, only the US and the North Pacific have enough reports |
| (e) US coverage | **Confirmed**, plus a change | `isigmet` omits US domestic SIGMETs; a new `sigmet` endpoint has both, and `isigmet` is flagged deprecated |
| (f) GitHub Actions cron | **Confirmed with conditions** | Free and capable enough; timing is not reliable, and the acceptable-use wording is a grey area |

## (a) Turbulence forecast grid

### Verdict

- **Keyless path to the global WAFS EDR grid: refuted.**
- **WIFS key for a non-airline individual: needs Anthony's action**, outcome uncertain, and not needed to proceed.
- **Fallback from open GFS 0.25 degree: confirmed** as downloadable, decodable and measurably useful. It is a wind-shear index, not an EDR forecast.
- **A scheduled GitHub Actions job can download and decode all of it**: confirmed on sizes and libraries; the decode was measured on macOS, not on a Linux runner.

### WIFS (AWC WAFS Internet File Service)

Sources: https://aviationweather.gov/wifs/ and the User's Guide, version 9.2, https://aviationweather.gov/wifs/users_guide/.

- **Probes (measured):** `GET /wifs/api` 200; `GET /wifs/api/collections` **401** `{"code":401,"message":"Not authorized","description":"Not authorized"}`; `GET /wifs/api/openapi` 200; `GET /wifs/data/` (legacy) 401.
- **Eligibility:** "WIFS is not an open system and requires all users to be approved for access by both the: ICAO State Meteorological Authority or the designated WIFS Approval Official registered with the ICAO Region and FAA as the ICAO designated Meteorological Authority for the U.S. and as a WAFS Provider State."
- "WIFS access is restricted to those individuals (or activities) who support meteorological services for the conduct or development of international air navigation."
- User categories include "International Air Carrier Operators", "Meteorological Service Provider Vendors which provide meteorological services for International Air Carrier Operators" and "Other Aeronautical Users (i.e., Any entity that is deemed by the State and the WIFS Provider State to have a need for WAFS services.)". A passenger page fits only the last, if any.
- "WIFS Users are advised that their account may be terminated if it is determined the account is being used for purposes other than the original intent in support of international air navigation."
- **Region:** "WIFS provides primary service for North American, Central America, and Caribbean (NACC); and South America (SAM) Regions. WIFS and SADIS share primary responsibility in the Asia and Pacific (APAC) region". Elsewhere the UK's SADIS is primary and runs a "cost recovery scheme"; a WIFS account there is backup only, limited "to a maximum of one day in seven".
- **Cost:** no fee is stated for WIFS. SADIS charges (amount not looked up).
- **Redistribution:** the guide has no clause on redistribution or public display (searched for redistrib, third party, onward, licence, commercial). Unknown either way.
- **Keys:** "Keys are valid for one year and up to five keys may be active at any one time." Header `X-API-Key`. Accounts "may be disabled after six months of inactivity".
- **Limits:** "All requests are rate limited to 100 requests per minute and all API endpoints should not be consumed more frequently than 1 request/min per thread. Data requests are limited to 5000 individual requests per day".
- **Product:** collection `kwbc_wafshzds_blended_turb_0p25`, "the harmonized turbulence forecast for multiple flight levels at a 0.25 degree horizontal resolution". "Turbulence severity FL100 to FL450 at 1000ft vertical intervals" (36 levels). Hourly from 6 to 24 h, 3-hourly from 27 to 48 h; "Icing, Turbulence and Cumulonimbus data stop at T+48". Runs at 00, 06, 12, 18 UTC. "The API only makes the most recently produced set of model data available." Items are GRIB2 with three time steps each, global or eight regional tiles, for example `/collections/kwbc_wafshzds_blended_turb_0p25/items/YLDYG042_048FLALL`.

### Keyless public paths: NOMADS and AWS

- **Service Change Notice 23-111** (14 December 2023): "Effective on or about January 17, 2024 at 1200 Coordinated Universal Time (UTC), the National Centers for Environmental Prediction (NCEP) Central Operations (NCO) will stop distributing selected aviation-hazard products from the World Area Forecast System (WAFS). Users should switch to accessing these products through the WAFS Internet File Service (WIFS) ... This update is occurring as a result of International Civil Aviation Organization (ICAO), Annex 3 requirements and international agreements." Removed from NOMADS and FTPPRD: `gfs.tCCz.awf_0p25.f0FF.grib2`, `wafsgfs_[L,P]_tCCz_intdskFF.grib2`. https://www.weather.gov/media/notification/pdf_2023_24/scn23-111_wafs_products_change.pdf
- **Service Change Notice 22-104** (2 November 2022): "The 0.25 degree WAFS US unblended hazard data will be removed from NOMADS due to a restricted access agreement." https://www.weather.gov/media/notification/pdf2/scn22-104_gfs.v16.3.0_aaa.pdf
- **Measured, NOMADS:** `https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.20261112/00/atmos/` lists 2,830 entries; none matches `wafs`, `awf`, `turb`, `edr` or `gtg`. `com/` has no `wafs/` directory (`com/wafs/` returns 403, the same as a made-up path). `com/wfs/` is the space-weather WAM-IPE model.
- **Measured, AWS:** bucket `noaa-gfs-bdp-pds`, prefix `gfs.20261112/00/atmos/`: 2,828 keys, none WAFS; prefix `wafs`: 0 keys.
- **ECMWF open data** (https://data.ecmwf.int/forecasts/, measured from the 12 November 00Z index, 191 messages): pressure-level parameters are `d, gh, q, r, t, u, v, vo, w, z` at 14 levels. **No turbulence parameter.** It does carry `u, v, gh, t` at 150, 200, 250 and 300 hPa (7.5 MB per forecast step), enough to compute the same index from a second model.
- **Quirk:** NOMADS sends a malformed HTTP/2 `content-length`; curl needs `--http1.1`. `ftpprd.ncep.noaa.gov` timed out and was not checked.

### Public, US only: GTG in the Domestic Aviation Forecast System

- **Path:** `https://nomads.ncep.noaa.gov/pub/data/nccf/com/dafs/prod/dafs.YYYYMMDD/dafs.tCCz.gtg.3km.conus.fFFF.grib2`, with `.idx`. Keyless.
- **Measured (11 November):** 24 runs a day, forecast hours 0 to 18, 142 to 164 MB per file, posted 53 to 88 minutes after run time. Two days are kept online.
- **Fields (measured, 205 messages per file):** `MXEDPRM` once (entire atmosphere), then `EDPARM`, `CATEDR`, `MWTURB` and parameter 0-19-50, each on 51 levels from 30 m to 15,240 m above mean sea level (every 1,000 ft). NCEP table 4.2-0-19 names 0-19-50 "Convectively-induced turbulence (CIT) (eddy dissipation rate)", `CITEDR`. Units m^2/3 s^-1.
- **Byte ranges work:** 6 messages, 5.7 MB, 7 s (measured). Median message 0.70 MB.
- **Decode (measured, eccodes 2.50.1):** 6 messages on a Lambert grid of 1799 x 1059 in 0.54 s, peak memory 266 MB. eccodes names 0-19-28 (`mwt`) and 0-19-29 (`cat`) but returns `unknown` for 0-19-30, 0-19-31 and 0-19-50, so a job must select by discipline, category and number, not by short name.
- **Sample values (12Z run, 6 h forecast, 10,363 m):** combined EDR median 0.091, 99th percentile 0.214, maximum 0.578.

### Fallback: a wind-shear index from open GFS

**Inputs (measured).** `gfs.tCCz.pgrb2.0p25.fFFF` on NOMADS and on AWS (`noaa-gfs-bdp-pds`, archive back to at least September 2026) has `UGRD`, `VGRD`, `TMP`, `HGT` at 100 to 500 hPa every 50 hPa; `pgrb2b.0p25` adds the 25 hPa levels between. Neither file has a turbulence field. Messages are 0.58 to 0.72 MB each and can be fetched by byte range from the `.idx`.

**Decode (measured, macOS, eccodes 2.50.1 with the bundled `eccodeslib` 2.50.0.34, numpy 2.5.4):** 20 messages on the 1440 x 721 grid decoded in 0.28 s; the index computed in 0.04 s; peak memory 344 MB.

**Skill against pilot reports (measured).** Method: 140 GFS forecasts at 24 to 27 h lead, every second day from 4 September to 11 November, valid at 15, 18, 21 and 00 UTC. Each cruise-level report (FL290 to FL440) within 90 minutes of a valid time was compared with the index in the nearest of three layers (300-250, 250-200, 200-150 hPa). 2,829 reports: 941 moderate or greater (MOG), 1,416 light, 472 smooth (NEG). Download: 1,573 MB in 702 s from AWS, no failures.

| Diagnostic | AUC, MOG vs smooth | AUC, MOG vs smooth + light |
|---|---|---|
| Ellrod TI1 (shear x deformation), highest value within 0.5 degree | **0.764** (95% bootstrap 0.739 to 0.790) | 0.658 (0.639 to 0.680) |
| Ellrod TI1, value at the grid point | 0.703 | 0.617 |
| Ellrod TI2 (adds convergence), within 0.5 degree | 0.777 | 0.661 |
| Inverse Richardson number, within 0.5 degree | 0.766 | 0.643 |
| Vertical shear alone | 0.740 | 0.645 |
| Wind speed alone | 0.530 | 0.526 |
| US West land only, TI1 within 0.5 degree (783 reports) | 0.748 | 0.643 |
| Pacific, Alaska, Japan side, TI1 within 0.5 degree (630 reports) | 0.781 | 0.679 |

Operating points for TI1 within 0.5 degree (units 1e-7 s^-2): at 8, it flags 72% of MOG reports and 35% of smooth reports; at 12, 56% and 19%. Medians: MOG 13.8, light 9.0, smooth 5.4.

Limits of this measurement: reports cluster in space and time, so the bootstrap intervals are too narrow; pilots report rough air far more readily than smooth air; the regions are the US West and the North Pacific only; one season.

**How much weaker, plainly.**
- **It is the old official product.** NOAA Technical Memorandum OAR GSD-60 (March 2018, https://doi.org/10.7289/V5/TM-OAR-GSD-60): the pre-2020 WAFS grid was "a 1.25˚ gridded product of clear-air turbulence (CAT) potential produced using the Ellrod TI1 Index (Ellrod and Knapp, 1992), which combines vertical wind shear and deformation."
- **The published gap is modest against pilot reports.** Same memo, for the GTG-based replacement: "within the ± 1.5-hr window, the AUC for the GTG-G Maximum EDR on the 1.25 ˚-grid ... was 0.784 compared with 0.722 for PIREP comparisons". "GTG-G forecast products generally outperformed the WAFS CAT potential", but "the performance gap between GTG-G and WAFS was greater for EDR comparisons than for PIREP comparisons", and at 200 hPa over North America and the North Pacific "the WAFS AUC was slightly greater than GTG-G for PIREP comparisons". The memo's numbers and mine are not like for like (different years, regions and definitions of a non-event).
- **What the fallback cannot do:** give a severity (it has no EDR calibration, so no "light", "moderate" or "severe"); see mountain-wave turbulence; see turbulence from thunderstorms; blend two centres' models. Flagging at the 72% hit level also flags a third of smooth reports.
- **So:** against the only yardstick this project has, the fallback is close to the official grid. In what it may honestly say, it is a category below: "elevated wind shear here", never "moderate turbulence forecast".

### Can a GitHub Actions job run it?

- **Open-GFS index:** 16 messages per forecast hour is 11.2 MB (measured average over 140 files). Forecast hours 6 to 54 every 3 h is 17 files, about 190 MB per run, four runs a day (arithmetic from the measured size). At the measured 5 s per file the whole run is about 90 s on this Mac.
- **US GTG:** 19 forecast hours x 5 cruise levels x 0.7 MB is about 67 MB per run (arithmetic).
- **Decoder:** `pip install eccodes eccodeslib numpy`; `eccodeslib` publishes `manylinux_2_28` wheels for x86_64 and aarch64 (9 MB, checked on PyPI). No system packages. `wgrib2` is not needed. **Not tested on Linux.**
- **Runner:** 4 vCPU, 16 GB RAM, 14 GB SSD on public repositories (research agent, from the GitHub runners reference). The job needs under 0.5 GB of memory.
- **AWS over NOMADS** for the job: same files, no HTTP/2 quirk, no rate concern found.

### What it changes in the design

- Phase 2 is the open-GFS index worldwide, labelled wind-shear context, with the threshold and its measured hit and false-flag rates printed on the page. Use the highest value within 0.5 degree of the route, not the point value.
- Add the public GTG grid as a second source for flight hours over the contiguous US within 18 h. That gives real source disagreement there: official GTG against the open index.
- Optional third source: the same index from ECMWF open data, which makes "2 of 3 sources agree" possible worldwide without WIFS.
- Knowability table: GTG speaks for US hours within 18 h; the index speaks for any hour within the horizon chosen (54 h covers a T-24 h brief of a 14-hour flight).
- The scorecard no longer depends on WIFS. It scores the open index everywhere reports exist, and GTG over the US.
- WIFS becomes an upgrade to apply for, not a gate.

## (b) Flight resolve

### Verdict

**Confirmed with conditions.** Airports from a callsign are free and open. Scheduled times need AeroDataBox (or the user typing the time). No rule turns a flight number into an ATC callsign, so track lookup works for some airlines only.

### Scheduled departure time

Research agent, pages fetched 12 November 2026. I re-opened the AeroDataBox terms and pricing pages and confirm the quotes below.

| Source | Endpoint | Free tier | Cheapest paid | Commercial use | Retention |
|---|---|---|---|---|---|
| **AeroDataBox** | `GET /flights/{searchBy}/{searchParam}/{dateLocal}` (2 units per call) | RapidAPI Basic: 400 units a month, so about 200 lookups; non-commercial, attribution required | $7.50 a month (API.Market) or $8 (RapidAPI), 5,000 units | "Allowed" on all paid plans | 7 days by default |
| aviationstack | `/v1/flights?flight_iata=&flight_date=`; future dates need the airport first | 100 requests a month, real-time only, "Non-Commercial Use" | $49.99 a month | Paid only | No clause found |
| FlightAware AeroAPI | `GET /flights/{ident}` (2 days ahead), `GET /schedules/{date_start}/{date_end}` | "up to $5 free per month", personal or academic only | $100 a month minimum | Standard tier | Shown only at purchase |
| AirLabs | `/api/v9/flight?flight_iata=` (nearest flight, no date) | 1,000 queries, "Personal Use" | $49 a month | Paid only | No clause found |
| Flightradar24 API | flight summary: actual times only, no schedule | none | $9 a month | per FAQ, yes | 30 days |
| Amadeus self-service | none | n/a | n/a | n/a | "decommissioned on July 17th" |
| OAG, Cirium | flight-instances API | 1,000 calls per 30 days (OAG) | contact sales | n/a | not public |
| OpenSky (comparison) | no schedules | research only | written licence | "non-profit research and educational use only" | n/a |
| VRS standing-data | CSV, callsign to airports | free | free | CC0 | none: **no times** |
| US DOT/BTS on-time data | `CRSDepTime` | free | free | not verified | historical US domestic only |

**No open dataset has scheduled times for future dates.**

AeroDataBox terms ("Last updated: October 11, 2026", https://aerodatabox.com/terms/, checked by me):
- 5.3: "you can only use the Services and/or the Contents and/or Derived Works for commercial purposes when the Contents were retrieved through a channel that required payment to AeroDataBox".
- 5.5: "in no event shall you retain a copy of Contents for a period exceeding the greater of: seven (7) consecutive calendar days; or" a `Cache-Control` max-age, or a plan's own period.
- 5.8, kept indefinitely: "Reference identifiers ... such as an IATA or ICAO code of an airport, airline, or aircraft type; a flight number or designator; an aircraft registration or ICAO 24-bit address".
- 5.8 also allows "Delivered outputs" to be kept, but "Retained Contents shall not be pooled, indexed, queried, or otherwise used as a source of Contents for any person, record, feature, or purpose other than the item in which they are retained and its owner."
- Pricing page: "You only need to attribute AeroDataBox as a source when you are using free/trial plan to retrieve the data from us."

My reading, not legal advice: the design's "every brief saved with its inputs" may keep the flight number and airport codes forever, but not an API-supplied departure time beyond 7 days. The clean fix is already in the design: the form takes a departure time, pre-filled when a source knows it. Store the time the user submitted, as user input. Panel flights can have their times entered by hand.

### Callsign to airports

- **adsbdb** (measured). `GET https://api.adsbdb.com/v0/callsign/{callsign}` returns `response.flightroute` with `callsign, callsign_icao, callsign_iata, airline{name, icao, iata, country, country_iso, callsign}, origin{...}, destination{...}`; airports carry `icao_code, iata_code, latitude, longitude, elevation, municipality, name, country_iso_name, country_name`. It accepts the IATA form (`SQ322` returns `callsign_icao: "SIA322"`). Unknown: HTTP 404 `{"response":"unknown callsign"}`. **No times, no dates.** A two-leg number returns one leg only (`QFA1`: YSSY to WSSS). An optional `midpoint` exists "for a small number of flightroutes".
- **adsbdb licence.** The code is MIT. The README says: "The flight route data is the work of David Taylor, Edinburgh and Jim Mason, Glasgow, and may not be copied, published, or incorporated into other databases without the explicit permission of David J Taylor, Edinburgh." A 7-day cache of routes is a copy in another database.
- **VRS standing-data** (research agent): https://github.com/vradarserver/standing-data, licence file "CC0 1.0 Universal", committed daily. Route columns `Callsign, Code, Number, AirlineCode, AirportCodes`; `airlines.csv` has 5,965 rows, 1,435 with an IATA code. This is the open replacement for adsbdb.

### Flight number to callsign rules

Research agent unless marked. ICAO originals are paywalled; quotes come from FAA and UK CAA reproductions.

- **Format:** "Aircraft identification must not exceed seven alphanumeric characters and be either: The ICAO designator for the aircraft operating agency, followed by the flight identification (for example, KLM511, NGA213, JTR25)" (FAA AIM Appendix 4). EUROCONTROL: "Normal format: 3 letter ICAO AO designator followed by 1 to 4 alphanumeric characters (Flight Id)", with agreed shapes "n, nn, nnn, nnnn", "nA, nnA, nnnA", "nAA, nnAA".
- **Airline code:** map the IATA code to the ICAO designator through a table. IATA codes are "Not unique" (VRS): 326 of 1,015 IATA codes sit on more than one row; `BA` maps to BAW and SHT, `U2` to EZY and UFS. Codes may contain a digit (`U2`, `9W`, `3K`).
- **Leading zeros:** strip them. ICAO APAC guidance lists "Additional leading zeros in Flight ID (e.g. ABC0123 instead of ABC123)" as an error; VRS: "The leading zeros are then stripped from the number."
- **Suffix letters:** an "optional one-letter 'operational suffix'" may follow the number; "It is common to append an 'A' to the FlightId if the FlightId is 'used up' for instance by a return from airborne" (ACI). No fixed meanings were found for D or P.
- **Alphanumeric ATC callsigns: no deterministic rule.** UK CAA CAP 704 recommends "a more random system of RTF call sign/flight number allocation different from the allocated aircraft commercial flight schedule number e.g. Operator ticket/ flight number AB 555 RTF Call sign ABC 5LF". EUROCONTROL de-conflicts callsigns "prior to the start of the summer and winter season", so they change. Share of VRS route rows with a letter in the number: easyJet 85%, Ryanair 83%, British Airways 52%, American 7%, Singapore Airlines 1%.
- **Measured by me:** of 21 airline-style callsigns at cruise within 60 nm of Frankfurt at 14:00 UTC, 9 ended in letters (`EZY15FL`, `RYR6KW`, `BAW85U`, ...).
- **Operating carrier:** regional and franchise flights fly under the operator's designator (CFE, ENY, SKW), and codeshares under the operating flight number. AeroDataBox returns `callSign` and `IsCodeshared`, which is the only checked source that publishes the pairing on a cheap plan.

### Tracks: adsb.lol

Measured against https://api.adsb.lol/api/openapi.json (version 0.0.2).

- `GET /v2/callsign/{callsign}`: aircraft **currently airborne** with that callsign; returns `hex, flight, r, t, lat, lon, alt_baro, gs, ...`. An aircraft over Germany was found by its callsign. Three long-haul Pacific callsigns (`UAL1`, `SIA21`, `QFA7`) returned `total: 0` at 14:00 UTC; I did not establish whether they were airborne and out of coverage or not flying.
- `GET /1/trace/full/{icao_hex}`: "Every point the aircraft reported for the day is returned". **Keyed by hex, no date parameter.** Measured: 504 points from 11 November 13:03 to 12 November 13:50 UTC, two flight identifiers in it, gaps up to 713 minutes, 15.8 KB.
- `POST /api/0/routeset`: returned HTTP 201 with an empty body three times. Not verified.
- **No per-callsign history.** History is bulk: `github.com/adsblol/globe_history_2026`, daily releases of about 3.6 GB in two parts.
- **Limits:** "No key: 500 requests/day per IP"; a free "adhoc" key gives 1,000 a day through a request form. `/v2/` and `/1/trace/` are metered.
- **Licence:** "The license for the API as well as all data ADSB.lol makes public is ODbL". ODbL summary: "Attribute: You must attribute any public use of the database, or works produced from the database"; "Share-Alike: If you publicly use any adapted version of this database, or works produced from an adapted database, you must also offer that adapted database under the ODbL"; "Keep open". Commercial use is allowed. The code can stay MIT; a published table of median tracks must be offered under ODbL, with attribution on any page that draws one.

### What it changes in the design

- Resolver order: normalise the input; map airline code through VRS `airlines.csv`; look the callsign up in VRS routes for airports; ask AeroDataBox for the time only if the user wants it pre-filled; always let the user type airports and time.
- "Could not map" is a normal state, common for easyJet, Ryanair and BA short-haul.
- Do not use adsbdb unless David J Taylor gives permission.
- Saved briefs keep the departure time as user input, not as API content.
- Panel tracks: for each panel flight, one `/v2/callsign` call while it is airborne to learn the hex, then one `/1/trace/full` call within a day. About 40 metered calls a day for 20 flights. Oceanic stretches will be gaps. Choose panel flights with numeric callsigns.

## (c) Tomorrow.io terms

### Verdict

**Needs Anthony's action.** Tomorrow.io's legal, pricing and support pages sit behind a bot check and could not be read today, by fetch, by a headless browser, or through the web archive (HTTP 429). The documentation site was readable. So the clauses that decide this are known only from search-index fragments and from the October recon in `oceanic-flight-watch`, which agree with each other but were not confirmed on a live page. No official price for any paid plan was found.

The Tomorrow.io API was not called.

### Evidence

Three levels: **docs** (read live on docs.tomorrow.io, the quotes below re-checked by me), **index** (a fragment of the official page relayed by a search engine, unconfirmed), **third party**.

- **Governing document:** "Tomorrow.io Subscription Terms" at https://www.tomorrow.io/legal/terms-of-service/ (the docs link to it). Index: last updated 19 October 2023.
- **Attribution.** Read live in NASA's copy of the clause (https://science.nasa.gov/earth-science/csda/vendor-tomorrow-io/): the subscriber "agrees that any portion of the Solution that incorporates and presents Datafeed shall prominently display the message "Powered by Tomorrow.io" in a legible manner near such data or any information derived from it. This message shall open a link to https://www.tomorrow.io/ when clicked or touched." Index: the official terms carry the same sentence. So **derived categories need the attribution too, as a link**.
- **Storing the feed.** Index: the subscriber may not "store or otherwise collect or copy the unaltered Datafeed, unless otherwise expressly provided for in the Order or for permitted evaluation of DaaS", and "Datafeed may only be stored for the duration of the Initial Term". This matches the October recon.
- **Commercial use.** Index: "Commercial use is strictly prohibited in the case of evaluation, proof of concept, or in connection with self-generated accounts originated on Company's website". Third party (OSTechNix, 17 September 2026): "Commercial use is not allowed on the free tier". **Not confirmed on the live page, and decisive for any later business.**
- **Public display:** no dedicated clause was found. The attribution clause presupposes that subscribers present data and information derived from it.
- **Free plan limits.** Docs give no numbers: "According to your plan, you are limited to a certain amount of requests per hour and day to any of the API endpoints." Index (support article): 500 a day, 25 an hour, 3 a second; one monitored location and one alert. These match what both existing repositories record.
- **Second key.** Docs: "Multiple API keys can be created by your account manager, to support different development environments or different use cases." and "In case you have multiple API keys, each one is assigned with its own rate limits and tokens." A free account has no account manager; whether it can get a second key is not stated.
- **More quota.** Docs: "If you need a higher rate limit, please reach out to sales@tomorrow.io". No price is published. Third-party anecdote (a GitHub discussion, September 2024): after asking, one open project's hourly limit was raised from 25 to 499 with the daily limit still 500.
- **Cheapest paid plan.** No official name, price or quota found. Third-party figures contradict each other: $29 a month, $50 a month, $500 a month, or "quoted by sales". Treat the price as unknown and sales-gated.
- **Open-source or showcase programme:** none found.
- **Flight-level data.** Docs (https://docs.tomorrow.io/reference/aviation): `turbAloft` is "Turbulence aloft (WAFS product)", available "[-7d, +36h]" at 9 altitudes from 10,000 to 45,000 ft. The page shows no plan label; release notes speak of "aviation customers". This confirms the premise that their turbulence layer is repackaged WAFS, and it is not on the free plan as far as anything readable shows.

### What it changes in the design

- **A public, non-commercial page showing derived categories with a linked "Powered by Tomorrow.io" fits what is known,** but is not confirmed. Keep the design's rules: derived categories only, no raw values stored or published.
- **Commercial use on the current key is probably prohibited.** That does not block the open-source phase. It does mean "business" needs a signed order with Tomorrow.io, at a price nobody publishes.
- **Headroom has no self-serve answer.** There is no second key to buy and no listed upgrade. The realistic options are one email to sales, or trimming the bot's and the collector's budgets on the shared key.
- **Make Tomorrow.io optional for Phase 3.** The airport layer already works on TAF and METAR alone. Launch the soft test without Tomorrow.io if the email gets no answer; add it when headroom exists.

## (d) Scoring method

### Verdict

**Confirmed that a scorecard is possible, with a different unit than the design proposes, and, within the three boxes the store collects, only in two regions.** Europe, the Atlantic and the US east of 110°W were never collected and are unmeasured.

### What the store holds (measured)

3,021 snapshot files, 134,384 records, **126,156 unique reports** over 71 days (key: observation time plus raw text).

- **Only 18,401 (14.6%) say anything about turbulence.** 93,000 are AIREPs, and 90,315 of those are automatic position reports with wind and temperature only (`ANZ29 1012S 15706W 1159 F340 MS39 034/09`). Silence is not a smooth-air report.
- **Explicit smooth air (`tbInt1 = "NEG"`): 4,355, which is 23.7%** of reports that mention turbulence. At cruise level (FL250 and above): 1,695 of 10,207, **16.6%**.
- Intensities (`tbInt1`): MOD 5,768; LGT 5,545; NEG 4,355; LGT-MOD 2,488; MOD-SEV 125; SEV 114; EXTM 5; SEV-EXTM 1.
- **One airline files 96% of the AIREPs that mention turbulence** (United: 2,512 of 2,627 at cruise level).
- Reports cluster in US daytime: over the 71 days, each UTC hour from 13 to 23 holds about 600 cruise-level turbulence reports, each hour from 04 to 11 about 240.
- Fields used: `tbInt1` (intensity), `tbType1`, `tbFreq1`, `fltLvl`, `lat`, `lon`, `obsTime`, `pirepType` (`PIREP`, `AIREP`, `Urgent PIREP`), `rawOb`.

Cruise-level reports that mention turbulence, by region:

| Region | Reports | MOG | Smooth | Smooth share | Per day |
|---|---|---|---|---|---|
| US West, land (125W to 110W, 30 to 50N) | 5,517 | 2,210 | 557 | 10.1% | 77.7 |
| Alaska and Gulf of Alaska | 1,497 | 374 | 430 | 28.7% | 21.1 |
| Japan and north-west Pacific | 1,420 | 430 | 395 | 27.8% | 20.0 |
| North-east Pacific, oceanic | 1,259 | 522 | 238 | 18.9% | 17.7 |
| Hawaii | 269 | 75 | 46 | 17.1% | 3.8 |
| South-east Asia and west tropical Pacific | 125 | 37 | 12 | 9.6% | 1.8 |
| Tropical east and central Pacific | 44 | 16 | 4 | 9.1% | 0.6 |
| New Zealand and Tasman | 31 | 10 | 5 | 16.1% | 0.4 |
| Australia | 13 | 2 | 2 | 15.4% | 0.2 |
| Indian Ocean box | 13 | 8 | 3 | 23.1% | 0.2 |
| South Pacific east of 180 | 9 | 4 | 3 | 33.3% | 0.1 |
| **Total** | **10,207** | **3,693** | **1,695** | **16.6%** | **143.8** |

Three 10-degree cells hold half of these reports; 23 cells hold 90%. The South Pacific has 61,000 reports and 40 that mention turbulence.

### Route-hours with any report in the window (measured)

Simulated flights at FL360 and 480 kt, departing every 3 hours for 69 days, climb and descent excluded.

| Route | 100 nm, 1 h, 4,000 ft | 100 nm, 90 min, 4,000 ft | 150 nm, 90 min, 4,000 ft | 50 nm, 30 min, 2,000 ft |
|---|---|---|---|---|
| Los Angeles to Seattle | 27.5% | 33.8% | 46.6% | 8.3% |
| San Francisco to Seattle | 27.0% | 30.6% | 40.4% | 9.2% |
| Las Vegas to Portland | 26.4% | 32.6% | 45.1% | 7.1% |
| Seattle to Anchorage | 12.9% | 16.4% | 21.6% | 4.6% |
| San Francisco to Honolulu | 7.6% | 10.1% | 14.2% | 1.8% |
| Los Angeles to Tokyo | 6.2% | 8.3% | 12.7% | 1.0% |
| San Francisco to Tokyo | 4.7% | 6.4% | 11.0% | 0.7% |
| Honolulu to Tokyo | 1.8% | 2.7% | 3.9% | 0.3% |
| Tokyo to Singapore | 0.5% | 0.7% | 1.4% | 0.1% |
| Los Angeles to Sydney | 0.5% | 0.7% | 1.4% | 0.1% |
| Sydney to Auckland | 0.1% | 0.1% | 0.2% | 0.0% |

In the design's window, about half of the verifiable US route-hours have a moderate-or-greater report and half have only smooth or light ones. The median verifiable route-hour has one or two reports.

### Proposal

- **Region.** Score the contiguous US and the North Pacific corridor (US West coast, Alaska, Hawaii, Japan). Every other region measured is printed as "not scored: fewer than 2 reports a day mention turbulence"; regions never collected, such as Europe and the Atlantic, as "not scored: not measured yet". The store covers the US only west of 110°W; a US-wide box must be collected first (AWC gives 30 days back).
- **Primary unit: one pilot report.** Every cruise-level report (FL290 to FL440) that states an intensity is one sample. Compare it with the forecast issued at fixed leads (24 h and 6 h) for the valid time nearest the report. This is how NOAA's own assessment scores, and it uses all 144 reports a day instead of the few near a panel route.
- **Match window for a report.** Time: within 90 minutes of a forecast valid time (the memo above used ±1.5 h and ±3 h). Space: highest forecast value within 0.5 degree, about 30 nm (measured: this lifts AUC from 0.70 to 0.76, and NOAA notes "location errors in PIREPs"). Altitude: the forecast layer nearest the report, about ±2,000 ft.
- **Scores to publish,** each with its sample count: hit rate on moderate-or-greater reports at the page's flag threshold; false-flag rate on smooth reports; AUC; all per region and per source (open index, GTG where it exists).
- **Secondary unit: a panel route-hour,** counted only when a report falls within 100 nm, 90 minutes and 4,000 ft. Publish coverage next to every figure and do not show a route with fewer than 30 matched route-hours.
- **The 500 route-hour target** is reachable only with a US-heavy panel: at 27 to 34% coverage a two-hour US flight yields about 0.6 scored route-hours per brief; a ten-hour Pacific flight about 0.6 as well. Twenty panel flights at one lead time give about 12 scored route-hours a day, so 500 takes about six weeks (arithmetic from the measured coverage).

### Limits to print on the scorecard

- Pilots report rough air far more readily than smooth air (2,210 MOG against 557 smooth over the US West). So the share of flags that were false cannot be measured, only the share of smooth reports that were flagged.
- Intensity is a pilot's judgement and depends on the aircraft.
- One airline dominates oceanic reports.
- Position and time are approximate.
- No report means not scored, never "smooth".

## (e) US coverage

### Verdict

**Confirmed: `isigmet` omits US domestic SIGMETs.** New: a unified endpoint includes them, and the spec flags the two older endpoints as deprecated.

### Evidence

Source: https://aviationweather.gov/data/schema/openapi.yaml, version "v4.0", and live calls at 13:56 UTC (all `format=json`, base `https://aviationweather.gov/api/data/`).

- **`isigmet`** (`deprecated: true`): "Decoded international SIGMETs. This does not include SIGMETs issued by the United States in domestic format (i.e. convective and non-convective domestic SIGMETS)." Measured: 132 records at 13:55 UTC, 133 a minute later; the 11 US-issued ones were all for oceanic regions (`KZWY` New York Oceanic, `KZAK` Oakland Oceanic, `KZHU` Houston Oceanic).
- **`sigmet`** (current): "Decoded SIGMETs. This includes SIGMETs issued by the United States in domestic format (i.e. convective and non-convective domestic) and international SIGMETs." Parameters `hazard` (`ts, conv, turb, mtw, ice, ds, ss, tc, va`), `level`, `date`. Measured: 136 records, of which 4 are US convective SIGMETs. The international part was nearly the same as `isigmet` fetched 2 s later: one record only in `sigmet`, two (Canadian mountain-wave) only in `isigmet`. The cause was not investigated.
  - **Fields:** `icaoId, firId, firName, receiptTime, issueTime, validTime, validTimeFrom, validTimeTo, sigmetType, seriesId, hazard, qualifier, base, top, geom, coords, dir, spd, chng, rawSigmet`.
  - **`sigmetType`** is `OBS` or `FCST` (65 and 71 of the 136).
  - **US convective records:** `icaoId: "KKCI"`, `firId: "KMKC"`, `firName` "US EAST", "US CENTRAL" or "US WEST", `hazard: "CONV"`, `sigmetType: "OBS"`, `base: null`, `top` in feet (38000), `spd` as a string, closed ring of `{lon, lat}` numbers. Validity 2 h in the four live records.
  - **History:** `date=20261020_1800` returned 149 records including 3 US convective ones.
  - **Not seen:** a US non-convective (turbulence) domestic SIGMET; none was in force, so its `hazard` and `firId` values are unverified.
- **`airsigmet`** (`deprecated: true`): "Domestic SIGMETs for the contiguous United States." Fields `icaoId, alphaChar, seriesId, receiptTime, creationTime, validTimeFrom, validTimeTo, airSigmetType, hazard, severity, altitudeHi1, altitudeHi2, altitudeLow1, altitudeLow2, movementDir, movementSpd, postProcessFlag, rawAirSigmet, coords`. Measured: 4 records, all `hazard: "CONVECTIVE"`. The schema says `airSigmetType` is "always SIGMET".
- **`gairmet`**: "Decoded G-AIRMETs for the contiguous United States." Parameters `product` (`sierra, tango, zulu`), `hazard` (`turb-hi, turb-lo, llws, sfc-wind, ifr, mtn-obs, ice, fzlvl`), `date`, `fore` (0, 3, 6, 9, 12).
  - **Fields:** `tag, forecastHour, validTime, hazard, geometryType, latlonpairs, frequency, severity, due_to, status, top, base, fzltop, fzlbase, level, receiptTime, issueTime, expireTime, product, geom, coords`.
  - **Geometry:** `geometryType` `AREA` or `LINE`; `coords` is a ring of `{lat, lon}` whose values are **strings**; closed in the sample.
  - **Levels:** `top` and `base` are strings in hundreds of feet (`"340"`, `"210"`). High-level turbulence is `hazard: "TURB-HI"`, `severity: "MOD"`.
  - **Validity:** one snapshot per call. Without `fore`, the call returned the 6 h snapshot. Measured package: issued 08:45 UTC (amended 12:41 and 13:21), snapshots valid 09, 12, 15, 18, 21 UTC, `expireTime` 15:00. **Horizon 12 h.**
  - **History:** `date=20261020_1800` worked; a date 42 days back returned HTTP 400 `{"status":"error","error":"Data is available for up to 30 days for date"}`.
- **`airmet`**: "Decoded domestic AIRMETs (Alaska only)." Fields `region, receiptTime, validTimeFrom, validTimeTo, airmetType, hazard, base, top`. **No geometry** in the JSON. 41 records.

### What it changes in the design

- Use `sigmet`, not `isigmet`, for the worldwide call. The US "not covered" state is not needed for SIGMETs.
- G-AIRMET `TURB-HI` adds official moderate-turbulence areas over the contiguous US, but only 12 h ahead: one more source that says "too early to know" at T-24 h.
- A new parser is small: the `sigmet` record shape matches `isigmet` plus three fields; G-AIRMET needs string-to-number conversion.
- Alaska AIRMETs have no polygons, so Alaska stays "not covered" for AIRMET-level hazards.

## (f) GitHub Actions cron as the job host

### Verdict

**Confirmed with conditions.** Free and large enough for a public repository. The schedule is not dependable to the half hour, and the terms leave a data-collection job in a grey area.

### Evidence

Research agent, from docs.github.com on 12 November 2026. I re-fetched the events page and the Actions terms and confirm the quotes marked (checked).

- **Timing (checked):** "The `schedule` event can be delayed during periods of high loads of GitHub Actions workflow runs. High load times include the start of every hour. If the load is sufficiently high enough, some queued jobs may be dropped. To decrease the chance of delay, schedule your workflow to run at a different time of the hour."
- "If GitHub Actions services are temporarily unavailable, then a workflow run is discarded if it has not been queued within 30 minutes of being triggered."
- **Shortest interval (checked):** "The shortest interval you can run scheduled workflows is once every 5 minutes." Runs come from the default branch only. UTC by default; a `timezone` field now exists.
- **Unofficial measurements, 2026:** a ten-day test of one daily job found a median delay of 25 m 56 s and a maximum of 10 h 05 m; a community thread reports a 30-minute schedule firing at 3:37, 5:09, 6:30, 7:44; another reports dropped runs that leave no trace in the UI. Small samples.
- **Auto-disable (checked):** "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days." "Repository activity" is not defined. Re-enable in the UI, with `gh workflow enable`, or by committing a change to the `cron` line.
- **Actor:** a schedule "can stop running even though it is still enabled" when the "associated `actor` account is suspended, deleted, or deprovisioned".
- **Cost and limits:** "Use of the standard GitHub-hosted runners is free and unlimited on public repositories." Job limit 6 hours; 20 concurrent jobs on Free; `GITHUB_TOKEN` 1,000 API requests an hour.
- **Storage:** caches are evicted after 7 days unused, 10 GB per repository; artifacts 90 days. Repositories should stay "ideally less than 1 GB"; GitHub advises "Store programmatically generated files outside of Git, such as in object storage." Pushes made with `GITHUB_TOKEN` do not start other workflows.
- **Acceptable use (checked):** Actions "should not be used for" ... "Any activity that places a burden on our servers, where that burden is disproportionate to the benefits provided to users (for example, don't use Actions as a content delivery network or as part of a serverless application, but a low benefit Action could be ok if it's also low burden)" and, on GitHub-hosted runners, "any other activity unrelated to the production, testing, deployment, or publication of the software project associated with the repository where GitHub Actions are used." Nothing names cron or data collection.

### What it changes in the design

- **Do not rely on "every 30 min".** Warnings: fetch `sigmet` on request with a short timeout (one worldwide call, measured about 120 KB) and cache it in the store, instead of trusting a job. Reports for scoring: a daily job that reads back through AWC's 30 days, so a late or dropped run loses nothing.
- **Grid job:** four runs a day, each tolerant of an hour's delay; schedule at an odd minute; add `workflow_dispatch` so it can be started by hand or by an outside pinger.
- **Store writes go to the external store** (Turso or object storage), not to Git.
- **Heartbeat:** each job writes its run time to the store; the page's "source stale" state covers the rest.
- **60-day rule:** real commits keep it alive during development; afterwards it needs a human touch every two months or a re-enable. Avoid keepalive actions: the best-known one was disabled by GitHub staff for a terms violation (reason unpublished).
- **Terms risk:** the jobs build this repository's own published scorecard and are light (about 1 GB of downloads and a few minutes a day). That is defensible as part of the project, not guaranteed. If it is ever refused, the same scripts run on any small cron host.

## Go / no-go

**Go for approach B, in a changed form; approach C is not needed as a fallback.** The official WAFS turbulence grid cannot be had without an approval that a passenger page may not get, so B must not wait for it. But the gap it was feared to leave is filled: an index computed from open GFS data separates rough from smooth pilot reports with a measured AUC of 0.76 at 24 hours' lead, the public GTG grid adds a real turbulence forecast over the contiguous US within 18 hours, and a job on a free runner can fetch and decode both. The page therefore has content at the hour people look, provided it says "elevated wind shear" and never names a severity outside the US. The scorecard is stronger than planned: it needs no gated data, it can score two sources against each other, and it now has a measured method. What remains uncertain is not the data: demand is untested (the three-flight assignment still gates Phase 1), flights whose callsign cannot be mapped will be common in Europe, and within the three boxes collected nothing can be verified outside the US and the North Pacific.

## What Anthony must do

1. **Decide whether to apply for WIFS** (optional, in parallel). "Register" on https://aviationweather.gov/wifs/, select WIFS API. The comments must "Include a description of the intended use of the WIFS service indicating how that use relates to developing or conducting meteorological services supporting international air navigation." Ask explicitly whether derived categories may be shown on a public page. Contact wifs.admin@noaa.gov. If based outside the Americas and Asia-Pacific, expect to be sent to SADIS.
2. **Schedule times:** start with the user typing the time. If pre-fill is wanted, AeroDataBox's free RapidAPI plan covers about 200 lookups a month for non-commercial use with attribution; a paid plan ($7.50 or $8 a month) is needed before any commercial use.
3. **adsbdb:** either ask David J Taylor for permission, or drop it for VRS standing-data (recommended).
4. **adsb.lol:** optionally request the free "adhoc" key for 1,000 requests a day.
5. **Collect US-wide pilot reports** before the scorecard: the current boxes stop at 110°W.
6. **Tomorrow.io, read the terms yourself:** open https://www.tomorrow.io/legal/terms-of-service/ in your own browser and confirm three clauses: commercial use on self-generated accounts, storage of the unaltered Datafeed, and the attribution sentence.
7. **Tomorrow.io, one email to sales@tomorrow.io** with three questions: may a free self-signup key power a public, non-commercial, open-source page that shows derived airport-weather categories with attribution; can the hourly limit be raised or a second key issued, and at what price; what is the cheapest plan that permits commercial use, and its price.

## Not verified, and open risks

- The decode ran on macOS only. A Linux runner was not tested.
- The skill measurement covers one autumn, the US West and the North Pacific. Winter jet-stream and mountain-wave seasons may differ, and the index has no mountain-wave term.
- The fallback was scored against pilot reports only. NOAA's memo says the official grid's lead is larger against instrument measurements, so services that hold the official grid are ahead on that measure.
- GTG's own skill was not measured here: only two days of files are online, and the store's reports were not matched against them.
- WIFS redistribution terms are unknown.
- Tomorrow.io's Subscription Terms, plan prices and free-plan numbers were not read on a live page.
- A US non-convective domestic SIGMET was not observed in `sigmet`.
- adsb.lol `routeset` returned an empty body; its behaviour is unknown.
- How AWC's public page https://aviationweather.gov/wafs/ obtains the WAFS turbulence it draws was not traced. It can be linked to; it should not be scraped.
- Most schedule-source terms other than AeroDataBox's were read by the research agent only. FlightAware's licence text is shown only at purchase.
- What counts as "repository activity" for GitHub's 60-day rule is undocumented.
- DWD ICON and UK Met Office open data were not checked for a turbulence field.
- Pilot-report density was measured only in the store's three boxes: West Pacific, East Pacific to 110°W, and Indian Ocean. Europe, the Atlantic and the US east of 110°W were never collected, so "only two regions can be scored" holds for those boxes only. (Corrected on 13 November 2026: the first version of this document stated it without that limit.)

## Follow-ups outside this recon (not done)

- `oceanic-flight-watch` polls `isigmet`, which the AWC spec now flags as deprecated. It should move to `sigmet`.
- Its `epac` report box includes the US West land strip; anything that calls that box "oceanic" is mislabelled.

## How this was done

- **Requests:** aviationweather.gov about 30 calls, 2 s apart. NOMADS about 45 calls, 2 to 3 s apart. AWS S3 about 2,400 byte-range calls for the skill measurement. adsbdb 12 calls. adsb.lol 9 calls, 7 of them metered.
- **Not done:** no signup, no form, no key requested, no `.env` read. Both existing repositories were read only (`docs/RECON.md`, `config/settings.toml`, `data/snapshots/pirep`).
- **Scripts** were throwaway. They and the one-row-per-report results of the skill measurement are in `docs/recon/`, copied into this repository on 13 November 2026. Nothing else was kept from the GRIB files.
