`airports_extra.csv`: airports that VRS standing-data does not have, in the VRS airport columns.
The route-table build adds each row unless VRS has that code, so a row stops mattering once
VRS gains the airport.

Source: OurAirports (https://ourairports.com/data/, repository
https://github.com/davidmegginson/ourairports-data, The Unlicense: public domain). Code, name,
IATA code, country, position and elevation are copied from its `airports.csv` as read on
2026-11-14; `Location` is this project's own wording.

Rows are added one at a time when a real lookup fails, not in bulk.
