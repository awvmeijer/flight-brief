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
    assert counts["airlines"] == 9 and counts["routes"] >= 20 and counts["airports"] >= 20
    built = vrs.RouteTable(tmp_path / "t.sqlite")
    assert built.route("SIA322") == ["WSSS", "EGLL"] and built.meta("licence") == vrs.LICENCE


def test_a_missing_table_says_how_to_build_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="flight_brief.vrs"):
        vrs.RouteTable(tmp_path / "none.sqlite")
