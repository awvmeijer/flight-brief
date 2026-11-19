import io
import tarfile
from pathlib import Path

import pytest

from flight_brief import vrs
from flight_brief.resolve import COULD_NOT_MAP, normalise, resolve, typed_leg

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(("typed", "code"), [
    ("SQ322", "SQ322"), ("sq 322", "SQ322"), ("SQ0322", "SQ322"), ("SQ-0012", "SQ12"),
    ("BA10A", "BA10A"), ("ba0010a", "BA10A"), ("U28001", "U28001"), ("9W0123", "9W123"),
    ("SIA322", "SIA322"), ("QF0001", "QF1"), ("AA000", "AA0"), ("EZY12AB", "EZY12AB"),
])
def test_normalise_strips_leading_zeros_and_keeps_a_suffix(typed: str, code: str) -> None:
    assert str(normalise(typed)) == code


@pytest.mark.parametrize("typed", ["", "322", "SQ", "SQ12345", "SQ1ABC", "SQ12A3", "hello", "S-"])
def test_text_that_is_not_a_flight_code_is_refused(typed: str) -> None:
    assert normalise(typed) is None


def test_a_plain_number_maps_to_one_leg(table: vrs.RouteTable) -> None:
    found = resolve("SQ0322", table)
    assert (found.status, found.code, found.callsign) == ("mapped", "SQ322", "SIA322")
    assert found.airline == "Singapore Airlines"
    (leg,) = found.legs
    assert (leg.origin.code, leg.destination.code) == ("WSSS", "EGLL")
    assert leg.origin.tz == "Asia/Singapore" and leg.origin.iata == "SIN"


def test_the_icao_form_maps_too(table: vrs.RouteTable) -> None:
    assert resolve("SIA322", table).callsign == "SIA322"


def test_a_multi_leg_number_gives_each_leg_in_order(table: vrs.RouteTable) -> None:
    legs = resolve("QF1", table).legs
    assert [(leg.index, leg.origin.code, leg.destination.code) for leg in legs] == [
        (1, "YSSY", "WSSS"), (2, "WSSS", "EGLL")]


def test_a_shared_iata_code_with_one_matching_airline_maps(table: vrs.RouteTable) -> None:
    # SQ is Singapore Airlines and Singapore Airlines Cargo; only SIA has a 322.
    assert [a.code for a in table.airlines("SQ")] == ["SIA", "SQC"]
    assert resolve("SQ322", table).status == "mapped"


def test_a_shared_iata_code_with_two_matching_airlines_asks_which(table: vrs.RouteTable) -> None:
    found = resolve("BA10A", table)  # British Airways and its Shuttle both fly a 10A
    assert found.status == "choose_airline"
    assert [c.callsign for c in found.candidates] == ["BAW10A", "SHT10A"]
    picked = resolve("BA10A", table, airline="SHT")
    assert picked.status == "mapped" and picked.callsign == "SHT10A"


def test_an_easyjet_style_number_does_not_map_and_asks_for_airports(table: vrs.RouteTable) -> None:
    found = resolve("U28001", table)  # flies under an alphanumeric callsign, not EZY8001
    assert found.status == "could_not_map" and found.message == COULD_NOT_MAP
    assert found.legs == []


def test_an_unknown_airline_does_not_map(table: vrs.RouteTable) -> None:
    assert resolve("ZZ1", table).status == "could_not_map"


def test_text_that_is_not_a_code_is_invalid_before_any_lookup(table: vrs.RouteTable) -> None:
    assert resolve("drop table", table).status == "invalid"


def test_typed_airports_accept_iata_and_icao(table: vrs.RouteTable) -> None:
    leg = typed_leg(table, "sin", "EGLL")
    assert not isinstance(leg, str) and (leg.origin.code, leg.destination.code) == ("WSSS", "EGLL")
    assert isinstance(typed_leg(table, "SIN", "XXX"), str)
    assert isinstance(typed_leg(table, "SIN", "WSSS"), str)


def test_the_table_builds_from_the_repository_archive_without_unpacking(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(FIXTURES / "vrs", arcname="standing-data-main")
    buf.seek(0)
    counts = vrs.build(vrs.from_archive(buf), tmp_path / "t.sqlite", lambda lat, lon: None)
    assert counts["airlines"] == 10 and counts["routes"] >= 20 and counts["airports"] >= 20
    built = vrs.RouteTable(tmp_path / "t.sqlite")
    assert built.route("SIA322") == ["WSSS", "EGLL"] and built.meta("licence") == vrs.LICENCE


def test_a_missing_table_says_how_to_build_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="flight_brief.vrs"):
        vrs.RouteTable(tmp_path / "none.sqlite")


def test_an_airport_vrs_lacks_comes_from_the_supplement(table: vrs.RouteTable) -> None:
    for code in ("GMR", "NTGJ"):
        gambier = table.airport(code)
        assert gambier is not None and gambier.code == "NTGJ" and gambier.iata == "GMR"
        assert (gambier.lat, gambier.lon) == pytest.approx((-23.08, -134.89), abs=0.01)
        assert gambier.tz == "Pacific/Gambier" and gambier.country == "PF"
    assert "OurAirports" in (table.meta("supplement") or "")
    leg = typed_leg(table, "PPT", "GMR")
    assert not isinstance(leg, str) and (leg.origin.code, leg.destination.code) == ("NTAA", "NTGJ")


def test_vrs_wins_over_the_supplement_when_it_has_the_airport(tmp_path: Path) -> None:
    header = "Code,Name,ICAO,IATA,Location,CountryISO2,Latitude,Longitude,AltitudeFeet\n"
    files = [("airports", header + "NTGJ,Named by VRS,NTGJ,GMR,Rikitea,PF,-23.1,-134.9,7\n")]
    counts = vrs.build(files, tmp_path / "t.sqlite", lambda lat, lon: None)
    built = vrs.RouteTable(tmp_path / "t.sqlite").airport("GMR")
    assert counts["airports"] == 1 and built is not None and built.name == "Named by VRS"


def test_a_route_vrs_lacks_comes_from_the_supplement(table: vrs.RouteTable) -> None:
    found = resolve("VT956", table)
    assert found.status == "mapped" and found.callsign == "VTA956" and found.airline == "Air Tahiti"
    assert [(x.origin.code, x.destination.code) for x in found.legs] == [("NTAA", "NTGJ")]
    assert "1 route" in (table.meta("supplement_routes") or "")


def test_vrs_wins_over_the_supplement_when_it_has_the_callsign(tmp_path: Path) -> None:
    files = [("routes", "Callsign,Code,Number,AirlineCode,AirportCodes\n"
                        "VTA956,VTA,956,VTA,NTGJ-NTAA\n")]
    counts = vrs.build(files, tmp_path / "t.sqlite", lambda lat, lon: None)
    built = vrs.RouteTable(tmp_path / "t.sqlite")
    assert counts["routes"] == 1 and built.route("VTA956") == ["NTGJ", "NTAA"]
    assert "0 routes" in (built.meta("supplement_routes") or "")


# --- flight numbers between two airports, and airports by name -----------------------------

def test_flights_between_lists_every_number_on_the_pair_by_airline_then_number(
        wide: vrs.RouteTable) -> None:
    found = wide.flights_between("EHAM", "SBGR")
    assert [(f.number, f.airline, f.callsign, f.leg) for f in found] == [
        ("CV7", "Cargolux", "CLX7", 2),  # VHHH-EHAM-SBGR-EBLG: the pair is its second leg
        ("KL791", "KLM", "KLM791", 1), ("KL793", "KLM", "KLM793", 1),
        ("JJ8079", "TAM Brazilian Airlines", "TAM8079", 1),
        ("TB9101", "TUI fly Belgium", "JAF9101", 1)]
    # the other direction is another list, and stops that are not consecutive do not count:
    # XYZ12 flies EHAM-EDDF-SBGR
    assert [f.number for f in wide.flights_between("SBGR", "EHAM")] == ["KL792"]
    assert "XYZ12" not in [f.callsign for f in found]
    # an airline with no IATA code keeps its ICAO code
    assert [f.number for f in wide.flights_between("eham", "eddf")] == ["KL1765", "XYZ12"]
    assert wide.flights_between("EHAM", "WSSS") == []


def test_flights_between_reads_the_slice_too(table: vrs.RouteTable) -> None:
    assert [(f.number, f.leg) for f in table.flights_between("WSSS", "EGLL")] == [
        ("BA12", 1), ("QF1", 2), ("SQ322", 1)]


@pytest.mark.parametrize(("typed", "first", "rest"), [
    ("amsterdam", "AMS", ["QSX"]),            # the city by its whole name before a name that holds it
    ("AMS", "AMS", []), ("ams", "AMS", []), ("eham", "AMS", []),
    ("sao paulo", "GRU", ["CGH", "SAO", "OLC"]),   # no accents typed; the busiest airport first
    ("São Paulo", "GRU", ["CGH", "SAO", "OLC"]),
    ("SÃO PAULO", "GRU", ["CGH", "SAO", "OLC"]),
    ("liege", "LGG", []), ("frankfurt", "FRA", ["HHN"]), ("schiphol", "AMS", []),
    ("guarulhos", "GRU", []), ("frankfurt am main", "FRA", []),
    ("london", "LHR", ["LGW"]), ("LHR", "LHR", []), ("heathrow", "LHR", []),
])
def test_find_airports_matches_what_a_passenger_types(
        wide: vrs.RouteTable, typed: str, first: str, rest: list[str]) -> None:
    found = [a.iata for a in wide.find_airports(typed)]
    assert found[0] == first and sorted(found[1:]) == sorted(rest)


def test_find_airports_lists_only_airports_with_an_iata_code_and_keeps_to_the_limit(
        wide: vrs.RouteTable) -> None:
    assert all(a.iata for a in wide.find_airports("amsterdam"))  # not the heliport
    assert "EHHA" not in [a.code for a in wide.find_airports("amsterdam")]
    assert len(wide.find_airports("sao paulo", limit=2)) == 2
    for nothing in ("", "  ", "zz", "xq", "qqqqqq", "-", "'"):
        assert wide.find_airports(nothing) == []
    # two letters are too few for a name, and enough only for a city's whole name
    assert wide.find_airports("am") == []


def test_best_airport_is_the_one_airport_when_there_is_no_real_choice(
        wide: vrs.RouteTable) -> None:
    def best(text: str) -> str | None:
        found = wide.best_airport(text)
        return found.iata if found else None

    assert best("amsterdam") == "AMS"  # the only airport in a city of that name
    assert best("GRU") == "GRU" and best("sbgr") == "GRU" and best("Frankfurt") == "FRA"
    assert best("sao paulo") is None and best("london") is None  # several, as good as each other
    assert best("nowhere at all") is None
