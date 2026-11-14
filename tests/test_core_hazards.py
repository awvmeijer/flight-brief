from datetime import UTC, datetime

import pytest

from flight_brief.core import hazards

BASE = {"firId": "NFFF", "seriesId": "03", "validTimeFrom": 1791000000, "validTimeTo": 1791021600,
        "hazard": "TS", "base": None, "top": 45000, "spd": "10", "rawSigmet": "NFFF SIGMET 03 ..."}


def test_single_open_ring_past_180_is_kept_as_given() -> None:
    hz = hazards.from_sigmet(BASE | {"geom": "AREA", "coords": [
        {"lon": 178.0, "lat": -7.0}, {"lon": 182.4, "lat": -7.0}, {"lon": 182.4, "lat": -5.0}]})
    assert hz.rings == [[(178.0, -7.0), (182.4, -7.0), (182.4, -5.0)]]
    assert hz.hazard_id == "NFFF-03-1791000000" and hz.kind == "TS"
    assert hz.valid_from == datetime.fromtimestamp(1791000000, UTC)
    assert hz.base_ft is None and hz.top_ft == 45000 and hz.raw.startswith("NFFF")


def test_multi_area_sigmet_keeps_each_ring_and_drops_degenerate_ones() -> None:
    ring = [{"lon": 1.0, "lat": 1.0}, {"lon": 2.0, "lat": 1.0}, {"lon": 2.0, "lat": 2.0}]
    line = [{"lon": 5.0, "lat": 5.0}, {"lon": 6.0, "lat": 6.0}, {"lon": 5.0, "lat": 5.0}]
    hz = hazards.from_sigmet(BASE | {"geom": "AREAS", "coords": [ring, line]})
    assert len(hz.rings) == 1


def test_no_coordinates_gives_a_hazard_without_rings() -> None:
    hz = hazards.from_sigmet(BASE | {"geom": None, "coords": None, "hazard": None})
    assert hz.rings == [] and hz.kind == "UNKNOWN"


def test_us_convective_sigmet_is_a_thunderstorm_area_from_the_surface() -> None:
    # As the `sigmet` endpoint returned it on 13 Nov 2026: hazard CONV, no base, top in feet.
    hz = hazards.from_sigmet({
        "icaoId": "KKCI", "firId": "KMKC", "firName": "US EAST", "seriesId": "18E",
        "hazard": "CONV", "qualifier": "", "sigmetType": "OBS", "base": None, "top": 40000,
        "validTimeFrom": 1794542100, "validTimeTo": 1794549300, "geom": "AREA",
        "coords": [{"lon": -81.0, "lat": 27.0}, {"lon": -79.5, "lat": 27.2},
                   {"lon": -79.8, "lat": 25.4}, {"lon": -81.0, "lat": 27.0}],
        "rawSigmet": "WSUS31 KKCI 130355 SIGE CONVECTIVE SIGMET 18E"})
    assert (hz.kind, hz.source, hz.hazard_id) == ("TS", "sigmet", "KMKC-18E-1794542100")
    assert hz.base_ft is None and hz.top_ft == 40000 and len(hz.rings) == 1
    assert hz.covers_level(36000) and not hz.covers_level(41000)


def no_top(raw: str) -> dict[str, object]:
    return BASE | {"top": None, "geom": None, "coords": None, "rawSigmet": raw}


def test_low_level_alaska_turbulence_takes_its_top_from_the_text() -> None:
    # As `sigmet` returned it on 20 Oct 2026: the text says SFC/060, the record has no base or top.
    hz = hazards.from_sigmet(no_top(
        "WSAK01 PAWU 202121\nPAZA SIGMET INDIA 1 VALID 202121/210121 PAWU-\nPAZA ANCHORAGE FIR \n"
        "SEV TURB FCST AREA WI 100 NM N SSR - 70 NM E LVD - 40 NM SW LVD - 70 \n"
        "NM SE YAK - 100 NM N SSR SFC/060 MOV E 30KT NC=\nAREAS OF LLWS\nTS"))
    assert hz.top_ft == 6000 and hz.covers_level(3000) and not hz.covers_level(36000)


@pytest.mark.parametrize(("text", "top"), [
    ("SEV ICE FCST S OF LINE N5721 E08810 SFC/FL100 STNR NC=", 10000),
    ("SEV TURB FCST WI N4135 W00025 SFC/FL060-090 STNR NC=", 9000),
    ("SEV ICE FCST WI N5446 E00802 SFC/5000FT INTSF", 5000),
    ("SEV ICE FCST E OF E070 2000/10000FT MOV SE 20KMH NC=", 10000),
    ("EMBD TS OBS WI N0355 W09534 TOP\nFL450 STNR INTSF=", 45000),
    ("SEV MTW FCST E OF LINE S4800 W07500 TOP BLW FL150 STNR NC=", 15000),
    ("SEV TURB FCST E OF LINE N4455 E02230 FL220/320 MOV ESE WKN=", 32000),
    ("VA CLD OBS SFC/090 MOV E 25KT NC FCST AT 1315Z WI N4834 E16058 SFC/120=", 12000),
])
def test_a_top_stated_in_the_text_is_used_when_the_record_has_none(text: str, top: int) -> None:
    assert hazards.from_sigmet(no_top(text)).top_ft == top


@pytest.mark.parametrize("text", [
    "EMBD TS OBS N OF LINE S0600 E06915 TOP ABV FL410 STNR NC=",
    "SEV TURB FCST WI S2524 W06155 ABV FL100 STNR NC=",
    "EMBD TS OBS WI N0355 W09534 TOP FL50 STNR INTSF=",
    "EMBD TS FCST WI S1905 W06833 TOP FL1800/350 STNR NC=",
    "TC NEOGURI OBS AT 0600Z CB WI 150NM OF CENTRE",
])
def test_an_open_or_unreadable_top_stays_unknown_so_every_level_is_covered(text: str) -> None:
    hz = hazards.from_sigmet(no_top(text))
    assert hz.top_ft is None and hz.covers_level(45000)


def test_the_record_top_wins_over_the_text() -> None:
    hz = hazards.from_sigmet(BASE | {"geom": None, "coords": None, "rawSigmet": "TS TOP FL300"})
    assert hz.top_ft == 45000
