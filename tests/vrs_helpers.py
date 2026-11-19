"""Rows for the wider test table (`conftest.wide`), in VRS's own file kinds and column names.

The committed slice under `fixtures/vrs` is VRS's rows unchanged. These are typed by hand from
the full table, with only the columns `vrs.build` reads, for what the slice does not reach:
finding an airport by a city's name, flight numbers between two airports, and aircraft
families. The engine kinds are VRS's, including the light aircraft that share three
characters with an airliner family (A33P, A35) and the piston-engined early Dash types.
"""

AIRPORTS = """Code,Name,ICAO,IATA,Location,CountryISO2,Latitude,Longitude
EHAM,Amsterdam Airport Schiphol,EHAM,AMS,Amsterdam,NL,52.3086,4.7639
SYAM,New Amsterdam Airport,SYAM,QSX,New Amsterdam,GY,6.2444,-57.4742
EHHA,Amsterdam Heliport,EHHA,,Amsterdam,NL,52.4133,4.8097
SBGR,Guarulhos - Governador André Franco Montoro International Airport,SBGR,GRU,São Paulo,BR,-23.4356,-46.4731
SBSP,Congonhas Airport,SBSP,CGH,São Paulo,BR,-23.6261,-46.6564
SBMT,Campo de Marte Airport,SBMT,SAO,São Paulo,BR,-23.5091,-46.6378
SDCG,Senadora Eunice Micheles Airport,SDCG,OLC,São Paulo De Olivença,BR,-3.4679,-68.9204
EDDF,Frankfurt am Main Airport,EDDF,FRA,Frankfurt-am-Main,DE,50.0333,8.5706
EDFH,Frankfurt-Hahn Airport,EDFH,HHN,Hahn,DE,49.9487,7.2639
EBLG,Liège Airport,EBLG,LGG,Liège,BE,50.6374,5.4432
"""
EXTRA_ZONES = {"EH": "Europe/Amsterdam", "SY": "America/Guyana", "SB": "America/Sao_Paulo",
               "SD": "America/Manaus", "ED": "Europe/Berlin", "EB": "Europe/Brussels"}

AIRLINES = """Code,Name,ICAO,IATA
KLM,KLM,KLM,KL
TAM,TAM Brazilian Airlines,TAM,JJ
JAF,TUI fly Belgium,JAF,TB
CLX,Cargolux,CLX,CV
XYZ,Zebra Charter,XYZ,
"""

ROUTES = """Callsign,Code,Number,AirlineCode,AirportCodes
KLM791,KLM,791,KLM,EHAM-SBGR
KLM793,KLM,793,KLM,EHAM-SBGR
KLM792,KLM,792,KLM,SBGR-EHAM
TAM8079,TAM,8079,TAM,EHAM-SBGR
JAF9101,JAF,9101,JAF,EHAM-SBGR
CLX7,CLX,7,CLX,VHHH-EHAM-SBGR-EBLG
XYZ12,XYZ,12,XYZ,EHAM-EDDF-SBGR
KLM1765,KLM,1765,KLM,EHAM-EDDF
"""

# ICAO, engine, species
_TYPES = """
B772 J L|B773 J L|B77L J L|B77W J L|B737 J L|B739 J L|B37M J L|B38M J L|B39M J L
B788 J L|B789 J L|B78X J L|B744 J L|B748 J L
A35 P L|A358 J L|A359 J L|A35K J L|A321 J L|A32P P L|A20N J L
A332 J L|A333 J L|A33E E L|A33P P L|BCS1 J L|BCS3 J L
E170 J L|E190 J L|E195 J L|E145 J L|CRJ2 J L|CRJ7 J L|CRJ9 J L|F100 J L|SU95 J L
AT45 T L|AT75 T L|DH8A T L|DH8C T L|DH80 P L|DH82 P L|SF34 T L|C208 T L|C206 P L
"""
MODEL_TYPES = "ICAO,EngineTypeCode,SpeciesCode\n" + "\n".join(
    ",".join(row.split()) for row in _TYPES.replace("|", "\n").splitlines() if row.strip()) + "\n"

EXTRA = [("airports", AIRPORTS), ("airlines", AIRLINES), ("routes", ROUTES),
         ("model-type", MODEL_TYPES)]
