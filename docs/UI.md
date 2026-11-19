# Flight Brief: the interface

How the app looks, moves and speaks. `docs/DESIGN.md` says what the product is; this file says
how it is drawn. The mockups in `docs/ui/mockups/` are the approved reference (19 November 2026).

**In one line:** Breakwise's dark sheet and windows, one phosphor green, amber for what is
flagged, drawn as the head-up display of an airliner.

## Where it comes from

- **STRAAT**, the Breakwise design system: the night sheet, the window with its bar and foot,
  square corners, hairlines, mono for the machine's voice, amber as the only warning.
  `src/flight_brief/static/straat/straat.css` is a copy; never edit it. Changes to the system go
  to its own repository.
- **3DREAMS@SG dashboard:** the green, `#39ff14`, and Mapbox's `navigation-night-v1` map style.
- **A Boeing 787 head-up display:** green line symbology on dark. A heading tape, a speed tape, a
  level tape, a pitch ladder and the flight path vector (the circle with wings).

Two departures from STRAAT, both decided by Anthony:

1. The one accent is green, not blue. STRAAT's "no green" rule does not apply here.
2. There is one sheet, night. A HUD has no paper mode.

## Principles

1. **The answer first, the sources behind it.** The first window says how the ride is
   expected to be, in four words, and each word names its source. Each source keeps its own
   lane under "Show the evidence". There is still no score for a flight.
2. **Not knowing is drawn as carefully as knowing.** "Too early", "not covered" and "stale" have
   their own marks and their own window. They are never hidden and never amber: not knowing is
   not a warning.
3. **Amber means flagged, and nothing else is amber.** Green is the instrument: the route, the
   marks, focus, the one primary button. If a screen is mostly green, it has too much on it.
4. **The HUD is drawn with data.** A tape shows this flight's speed or level; the heading tape
   shows this route's track. No symbol is decoration.
5. **State is never colour alone.** Every state has a symbol and a word.
6. **A strength word always has a source.** Smooth, light, moderate and severe are written
   only by `ride.py`: from an official warning, from NOAA's forecast, or as an estimate that
   is drawn and labelled as one. Never "safe" or "unsafe", never a score. An official source's
   text appears only as a quotation, in mono, behind a green rule.

## Colour

All values are STRAAT night tokens (`--st-*`) except the accent family, overridden on `.fb` in
`static/fb.css`.

| role | value | use |
|---|---|---|
| ground | `#06090f` with the dot grid | the page |
| card, card-bar | `#0d1422`, `#111a2a` | windows and their bars |
| ink, body, muted | `#ffffff`, `#c9d3e0`, `#8d9bb2` | headings, text, labels |
| accent | `#39ff14` | route, HUD lines, focus, primary button, "nothing found" |
| on-accent | `#06090f` | text on a green fill |
| warn | `#e8962c` | flagged hours, flagged route stretches, a stale source |
| lines | white at 8%, 14%, 24% | hairlines; never a grey |

HUD strokes use the green at three strengths: 78% for the reading, 34% for scales, 14% for the
background grid. A soft glow (`drop-shadow`) is allowed on the flight path vector, the route and
the wordmark's die, and nowhere else.

## Type

- **Switzer** 400 and 500 for everything read. 600 is the wordmark only.
- **Mono** (the system's) for the machine: window bars and feet, labels, codes, times in the
  HUD, quoted source text. Uppercase, 10.5 to 11px, tracked.
- Figures use tabular numerals.
- Switzer's licence forbids putting the font files in a public repository, so they are not in
  git. `static/straat/fonts/` is filled on Anthony's machine and deployment from the design
  package; any other copy falls back to Inter or the system sans.

## States

| state | symbol | drawn as | meaning |
|---|---|---|---|
| flagged | ◆ | amber line, amber tint | a source marks this hour |
| nothing found | ○ | green line, faint green | the source covers the hour and marks nothing |
| too early | ? | dashed hairline, muted | the source does not reach this hour yet |
| not covered | – | dotted hairline, muted | the source never covers this place |
| stale | ! | dashed amber | the source's data is older than its limit |

## The ride: four levels

| word | bar | colour | may come from |
|---|---|---|---|
| smooth | a tenth of the height | green | forecast, estimate |
| light | two fifths | green | forecast, estimate |
| moderate | seven tenths | amber | warning, forecast, estimate |
| severe | full height, glowing | amber | warning, forecast |
| not known yet | full height, hatched | hairline | no source reaches the stretch |

The height carries the level, so it never rests on colour. A solid bar is official (a warning
in force, or NOAA's forecast). A faint bar with a dashed top is the estimate. A small white
diamond over a bar marks a storm: a thunderstorm warning, or storm clouds forecast near
cruise level. No red: severe is amber at full height.

## Components

- **The ride** (first window of a brief): the headline sentence, the other sentences (storms,
  wind, what is not known), one bar per ten minutes grouped by flight hour with the hour's
  word and its source under it, the key, and what each of the four words means for a
  passenger.
- **Show the evidence:** one fold, closed until asked, that holds how the words were chosen,
  the hour cells, the lanes, the sources' own words, assumptions and licences. It opens
  without JavaScript.
- **World map** (`/map`): the estimate worldwide and forecast storm clouds, with a time step
  and three cruise levels.

- **Window** (`st-win`): bar with a die (green when its data is live, hollow when it holds only
  unknowns, amber when it holds a flag), a title and a count; body; foot with provenance. Green
  brackets on two corners.
- **HUD band:** between the two airports. Heading tape with the initial track, speed tape,
  level tape, pitch ladder, flight path vector. One line under it says what is assumed.
- **Hour cells:** one per flight hour, numbered from take-off. The large row is the hour's
  overall state; each lane repeats the row for one source, with the source's value in the cell.
- **Lane:** source name, one line on where the data is from and how old, eleven cells.
- **Flagged stretch:** time range and place in mono caps, the hours as a heading, what the source
  says, and how many sources speak.
- **Meter:** how often the index's flag falls on rough and on smooth reports. Two bars.
- **Unknown list:** one line per thing the brief cannot know, with a "check again" tag when a
  time is known.
- **Flight row:** departure and arrival times large, route and duration in mono caps, number in
  green mono, airline, aircraft.
- **Search:** two windows. Route and date first; flight number second. Suggestions drop from
  the field.
- **Route map:** Mapbox, style `navigation-night-v1`, globe projection, fog as on the 3DREAMS
  landing page. The route is a green line; hours the answer calls moderate or severe are
  amber and wider; hours not known yet are dashed; hour numbers at each hour's midpoint. Needs `MAPBOX_TOKEN`; without it the window says so.
- **Loading:** a quadcopter drawn in HUD lines holds its course while the horizon rolls and
  streaks pass. Under it, the kinds of source being read. It does not show which one is
  done, because one request reads them all. It is shown only while a brief is being made.

## Motion

- One easing (STRAAT's). State changes take 150 ms.
- The loading screen is the only loop, and it runs only while something is being fetched.
- `prefers-reduced-motion`: everything is still; the loading screen shows the list of sources.

## Layout

- Breakpoints 810 and 480. Content is 1180px wide at most.
- At phone width windows stack; each lane's name sits above its cells; the HUD band sits under
  the two airports.
- No horizontal scroll at 375px.

## Voice

Plain sentences, sentence case, the point first. "Nothing found", never "all clear". "One
source only", never "low confidence". Times always name their zone.

## Accessibility

- Text contrast 4.5:1, marks 3:1. Green on the card is 12:1; amber on the card is 7:1.
- Every cell has a text alternative ("hour 3 flagged"). HUD drawings have a one-sentence label
  with the values they show.
- Everything except the map and the suggestions works with JavaScript off.

## What the browser contacts

The app's own host, and Mapbox when a token is set (map tiles, style and the map script). Nothing
else: no analytics, no web fonts from a third party.
