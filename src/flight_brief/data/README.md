`airports_extra.csv`: airports that VRS standing-data does not have, in the VRS airport columns.
The route-table build adds each row unless VRS has that code, so a row stops mattering once
VRS gains the airport.

Source: OurAirports (https://ourairports.com/data/, repository
https://github.com/davidmegginson/ourairports-data, The Unlicense: public domain). Code, name,
IATA code, country, position and elevation are copied from its `airports.csv` as read on
2026-11-14; `Location` is this project's own wording.

Rows are added one at a time when a real lookup fails, not in bulk.

`routes_extra.csv`: routes that VRS standing-data does not have, in the VRS route columns.
The build adds each row unless VRS has that callsign, so a row stops mattering once VRS gains
the route.

Source: none of these rows is copied from a database. Each is one fact typed by hand: which
airports a flight number flies between, as the airline's schedule had it on the date below,
read from public flight-status pages. Nothing is taken from adsbdb. The callsign is the
airline's ICAO code followed by the flight number, the same guess the resolver makes; it has
not been checked against what the aircraft transmits.

| Callsign | Route | Schedule read on | Note |
|---|---|---|---|
| VTA956 | Papeete to Totegegie (Gambier) | 2026-11-14, for Tue 17 Nov 2026 | Older pages show this number flying the other way; Air Tahiti reuses numbers between seasons |
