"""The schedule client against a fake gateway: no test here sends anything. The replies are
shaped after AeroDataBox's published OpenAPI description, not after a recorded answer."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from flight_brief import schedule, vrs
from flight_brief.schedule import Schedule, Scheduled, Unavailable

DAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
GRU = {"icao": "SBGR", "iata": "GRU", "name": "São Paulo Guarulhos",
       "municipalityName": "São Paulo", "timeZone": "America/Sao_Paulo"}
CDG = {"icao": "LFPG", "iata": "CDG", "name": "Paris Charles de Gaulle",
       "municipalityName": "Paris", "timeZone": "Europe/Paris"}


def leg(number: str, dep: str, arr: str | None, to: dict[str, Any] = GRU, **more: Any
        ) -> dict[str, Any]:
    """One departure from Amsterdam as a board with `withLeg=true` has it: the airport asked
    about is left out of `departure`. `dep` and `arr` are UTC, HH:MM on the day; an arrival
    earlier than the departure is on the next day."""
    def both(hhmm: str, offset: int, later: bool = False) -> dict[str, str]:
        at = datetime.fromisoformat(f"2026-11-20T{hhmm}+00:00") + timedelta(days=later)
        local = at + timedelta(hours=offset)
        return {"utc": f"{at:%Y-%m-%d %H:%M}Z",
                "local": f"{local:%Y-%m-%d %H:%M}{offset:+03d}:00"}

    item: dict[str, Any] = {
        "departure": {"scheduledTime": both(dep, 1), "terminal": "2", "quality": ["Basic"]},
        "arrival": {"airport": to, "quality": ["Basic"]},
        "number": number, "callSign": None, "status": "Expected",
        "codeshareStatus": "IsOperator", "isCargo": False,
        "aircraft": {"model": "Boeing 777-300ER"}, "airline": {"name": "KLM", "iata": "KL",
                                                               "icao": "KLM"}}
    if arr:
        item["arrival"]["scheduledTime"] = both(arr, -3 if to is GRU else 1, later=arr < dep)
    item.update(more)
    return item


MORNING = {"departures": [
    leg("KL 793", "09:20", "21:10", callSign="KLM793"),
    leg("AF 1241", "06:00", "07:20", to=CDG, aircraft={"model": "Airbus A220-300"},
        airline={"name": "Air France"}),
    leg("KL 791", "11:15", "23:05"),  # 12:15 in Amsterdam: the second call has it too
], "arrivals": None}
AFTERNOON = {"departures": [
    leg("KL 791", "11:15", "23:05"),
    leg("JJ 8079", "20:40", "08:25", airline={"name": "LATAM Brasil"}, aircraft=None),
    # partial data, as their documentation warns: no arrival time, no airline, no aircraft
    {"departure": {"scheduledTime": {"utc": "2026-11-20T13:00:00Z",
                                     "local": "2026-11-20T14:00:00+01:00"}},
     "arrival": {"airport": {"iata": "GRU", "name": "Guarulhos"}}, "number": "TB9101",
     "status": "Unknown", "codeshareStatus": "Unknown", "isCargo": False},
    # none of these is a flight to list
    leg("MP 6161", "15:00", "03:00", isCargo=True),
    leg("DL 9374", "11:15", "23:05", codeshareStatus="IsCodeshared"),
    leg("KL 795", "16:00", "04:00", status="Canceled"),
    leg("???", "17:00", "05:00"),
    {"number": "KL 797", "status": "Expected", "codeshareStatus": "IsOperator",
     "isCargo": False, "arrival": {"airport": GRU}},                      # no departure time
    {"number": "KL 799", "status": "Expected", "codeshareStatus": "IsOperator",
     "isCargo": False, "departure": {"scheduledTime": {"utc": "soon", "local": "later"}},
     "arrival": {"airport": GRU}},
    leg("KL 801", "18:00", "06:00", to={"name": "Somewhere"}),            # no airport code
    "not a flight", None,
]}


class Gateway:
    """A fake `get`: answers from a list in turn and keeps what it was asked."""

    def __init__(self, *replies: Any) -> None:
        self.replies, self.asked = list(replies), []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        self.asked.append((url, headers))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            return reply
        return 200, json.dumps(reply)


class Clock:
    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def ams(wide: vrs.RouteTable) -> vrs.Airport:
    return wide.airport("EHAM")  # type: ignore[return-value]


@pytest.fixture
def gru(wide: vrs.RouteTable) -> vrs.Airport:
    return wide.airport("SBGR")  # type: ignore[return-value]


def test_a_day_of_departures_is_two_calls_filtered_sorted_and_read_with_gaps(
        tmp_path: Path, ams: vrs.Airport, gru: vrs.Airport) -> None:
    gateway = Gateway(MORNING, AFTERNOON)
    found = Schedule("k3y", tmp_path, gateway, Clock()).departures(ams, gru, DAY)
    assert isinstance(found, list)
    assert [(f.number, f.dep_local, f.arr_local) for f in found] == [
        ("KL793", "10:20", "18:10"), ("KL791", "12:15", "20:05"), ("TB9101", "14:00", None),
        ("JJ8079", "21:40", "05:25")]
    first = found[0]
    assert first == Scheduled(
        number="KL793", airline="KLM", callsign="KLM793", dep_local="10:20",
        dep_utc=datetime(2026, 11, 20, 9, 20, tzinfo=UTC), arr_local="18:10",
        arr_utc=datetime(2026, 11, 20, 21, 10, tzinfo=UTC), aircraft="Boeing 777-300ER",
        origin="EHAM", destination="SBGR", day="2026-11-20")
    partial = found[2]  # only an IATA code for the destination, and nothing else
    assert (partial.airline, partial.aircraft, partial.arr_utc, partial.destination) == (
        "", None, None, "GRU")
    assert found[3].aircraft is None and found[3].arr_utc == datetime(2026, 11, 21, 8, 25,
                                                                     tzinfo=UTC)

    urls = [url for url, _ in gateway.asked]
    query = ("?direction=Departure&withLeg=true&withCancelled=false&withCodeshared=false"
             "&withCargo=false&withPrivate=false")
    assert urls == [
        "https://aerodatabox.p.rapidapi.com/flights/airports/icao/EHAM/"
        f"2026-11-20T00:00/2026-11-20T11:59{query}",
        "https://aerodatabox.p.rapidapi.com/flights/airports/icao/EHAM/"
        f"2026-11-20T12:00/2026-11-20T23:59{query}"]
    for _, headers in gateway.asked:
        assert headers["X-RapidAPI-Key"] == "k3y"
        assert headers["X-RapidAPI-Host"] == "aerodatabox.p.rapidapi.com"
        assert headers["User-Agent"] == "flight-brief/0.2"


def test_the_airport_day_is_kept_whole_so_asking_again_costs_nothing(
        tmp_path: Path, ams: vrs.Airport, gru: vrs.Airport, wide: vrs.RouteTable) -> None:
    gateway, clock = Gateway(MORNING, AFTERNOON), Clock()
    sched = Schedule("k3y", tmp_path, gateway, clock)
    first = sched.departures(ams, gru, DAY)
    assert sched.units_spent() == 4 and len(gateway.asked) == 2
    # the same question, and another destination from the same airport on the same day
    clock.at += timedelta(hours=5, minutes=59)
    assert sched.departures(ams, gru, DAY) == first
    paris = vrs.Airport(code="LFPG", name="Paris Charles de Gaulle Airport", icao="LFPG",
                        iata="CDG", location="Paris", country="FR", lat=49.01, lon=2.55,
                        tz="Europe/Paris")
    other = sched.departures(ams, paris, DAY)
    assert isinstance(other, list) and [f.number for f in other] == ["AF1241"]
    assert sched.departures(ams, wide.airport("EDDF"), DAY) == []  # type: ignore[arg-type]
    assert len(gateway.asked) == 2 and sched.units_spent() == 4
    # a new process reads the same file
    assert Schedule("k3y", tmp_path, Gateway(), clock).departures(ams, gru, DAY) == first
    # after six hours it is asked again, and another day is another question
    clock.at += timedelta(minutes=2)
    gateway.replies += [MORNING, AFTERNOON, (204, ""), (204, "")]
    assert sched.departures(ams, gru, DAY) == first and len(gateway.asked) == 4
    assert sched.departures(ams, gru, DAY + timedelta(days=1)) == []
    assert len(gateway.asked) == 6 and sched.units_spent() == 12


def test_no_call_is_sent_that_would_pass_the_months_margin(
        tmp_path: Path, ams: vrs.Airport, gru: vrs.Airport) -> None:
    clock = Clock()
    gateway = Gateway(MORNING, AFTERNOON, [], MORNING, AFTERNOON)
    sched = Schedule("k3y", tmp_path, gateway, clock, monthly_units=30, margin=20)
    assert sched.units_left() == 10
    assert isinstance(sched.departures(ams, gru, DAY), list)            # 4
    assert sched.flight("KL791", DAY) == Unavailable(reason="not_found")  # 2
    assert isinstance(sched.departures(ams, gru, DAY + timedelta(days=1)), list)  # 4
    assert (sched.units_spent(), sched.units_left()) == (10, 0)
    # 10 of 30 spent and 20 kept back: nothing more is sent, a day of departures or one number
    assert sched.departures(ams, gru, DAY + timedelta(days=2)) == Unavailable(reason="quota")
    assert sched.flight("KL793", DAY) == Unavailable(reason="quota")
    assert len(gateway.asked) == 5 and sched.units_spent() == 10
    # what is kept still answers
    assert isinstance(sched.departures(ams, gru, DAY), list)
    # a day of departures needs both its calls: with one call's worth left it is not started
    half = Schedule("k3y", tmp_path / "half", Gateway(), clock, monthly_units=22, margin=20)
    assert half.departures(ams, gru, DAY) == Unavailable(reason="quota")
    assert half.units_spent() == 0
    # the ledger is per calendar month
    assert json.loads((tmp_path / "units-2026-11.json").read_text()) == {"spent": 10}
    clock.at = datetime(2026, 12, 1, 0, 5, tzinfo=UTC)
    assert sched.units_spent() == 0
    gateway.replies += [(204, ""), (204, "")]
    assert sched.departures(ams, gru, date(2026, 12, 2)) == []
    assert json.loads((tmp_path / "units-2026-12.json").read_text()) == {"spent": 4}


def test_every_write_deletes_what_is_older_than_seven_days(
        tmp_path: Path, ams: vrs.Airport, gru: vrs.Airport) -> None:
    clock = Clock()
    gateway = Gateway(MORNING, AFTERNOON, [], MORNING, AFTERNOON)
    sched = Schedule("k3y", tmp_path, gateway, clock)
    sched.departures(ams, gru, DAY)
    clock.at += timedelta(days=3)
    sched.flight("KL791", DAY)
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    names = lambda: sorted(p.name for p in tmp_path.iterdir())  # noqa: E731
    assert names() == ["board-EHAM-2026-11-20.json", "broken.json",
                       "number-KL791-2026-11-20.json", "units-2026-11.json"]
    # seven days after the first file was written, the next write removes it and the file
    # that cannot be read; the younger file and the ledger stay
    clock.at = NOW + timedelta(days=7)
    sched.departures(ams, gru, DAY + timedelta(days=7))
    assert names() == ["board-EHAM-2026-11-27.json", "number-KL791-2026-11-20.json",
                       "units-2026-11.json"]


def test_without_a_key_nothing_is_sent_or_written(
        tmp_path: Path, ams: vrs.Airport, gru: vrs.Airport) -> None:
    for key in (None, ""):
        gateway = Gateway()
        sched = Schedule(key, tmp_path / "s", gateway, Clock())
        assert sched.departures(ams, gru, DAY) == Unavailable(reason="no_key")
        assert sched.flight("KL791", DAY) == Unavailable(reason="no_key")
        assert gateway.asked == [] and sched.units_spent() == 0
    assert not (tmp_path / "s").exists()


@pytest.mark.parametrize(("reply", "reason"), [
    (TimeoutError("timed out"), "failed"), (OSError("no route to host"), "failed"),
    ((500, "oops"), "failed"), ((401, ""), "failed"), ((403, ""), "failed"),
    ((200, "<html>not json"), "failed"), ((200, '"a string"'), "failed"),
    ((429, ""), "quota"),
])
def test_a_call_that_fails_never_raises_and_is_still_counted(
        tmp_path: Path, ams: vrs.Airport, gru: vrs.Airport, reply: Any, reason: str) -> None:
    gateway = Gateway(reply, reply)
    sched = Schedule("k3y", tmp_path, gateway, Clock())
    assert sched.departures(ams, gru, DAY) == Unavailable(reason=reason)
    # the day stops at its first failed call, which was sent and so is counted
    assert len(gateway.asked) == 1 and sched.units_spent() == 2
    assert sched.flight("KL791", DAY) == Unavailable(reason=reason)
    assert sched.units_spent() == 4
    assert [p.name for p in tmp_path.iterdir()] == ["units-2026-11.json"]  # nothing kept


def test_one_flight_number_on_one_day(tmp_path: Path) -> None:
    ams = {"icao": "EHAM", "iata": "AMS", "name": "Amsterdam Schiphol"}
    full = leg("KL 791", "11:15", "23:05")
    full["departure"]["airport"] = ams
    other_day = json.loads(json.dumps(full).replace("2026-11-20", "2026-11-19")
                           .replace("2026-11-21", "2026-11-20"))
    gateway = Gateway([other_day, full])
    sched = Schedule("k3y", tmp_path, gateway, Clock())
    found = sched.flight("kl 0791", DAY)
    assert isinstance(found, list)
    (one,) = found
    assert (one.number, one.origin, one.destination, one.dep_local, one.arr_local,
            one.aircraft) == ("KL791", "EHAM", "SBGR", "12:15", "20:05", "Boeing 777-300ER")
    (asked,) = gateway.asked
    assert asked[0] == ("https://aerodatabox.p.rapidapi.com/flights/number/KL791/2026-11-20"
                        "?dateLocalRole=Departure")
    # kept: the same number again, typed another way, costs nothing
    assert sched.flight("KL791", DAY) == found and sched.units_spent() == 2
    # a number the source does not know: not found, and that answer is kept too
    for unknown, reply in (("ZZ1", (204, "")), ("ZZ2", (404, "")), ("ZZ3", [])):
        gateway.replies.append(reply)
        assert sched.flight(unknown, DAY) == Unavailable(reason="not_found")
        assert sched.flight(unknown, DAY) == Unavailable(reason="not_found")
    assert len(gateway.asked) == 4 and sched.units_spent() == 8
    assert sched.flight("not a number", DAY) == Unavailable(reason="not_found")
    assert len(gateway.asked) == 4


def test_a_board_without_legs_is_read_too(tmp_path: Path, ams: vrs.Airport,
                                          gru: vrs.Airport) -> None:
    # Without `withLeg` each flight has one `movement`: the time here, the airport there.
    item = {"movement": {"airport": GRU, "scheduledTime": {"utc": "2026-11-20 11:15Z",
                                                           "local": "2026-11-20 12:15+01:00"}},
            "number": "KL 791", "status": "Expected", "codeshareStatus": "IsOperator",
            "isCargo": False, "airline": {"name": "KLM"}}
    found = Schedule("k3y", tmp_path, Gateway({"departures": [item]}, {"departures": None}),
                     Clock()).departures(ams, gru, DAY)
    assert isinstance(found, list)
    assert [(f.number, f.origin, f.destination, f.dep_local, f.arr_local) for f in found] == [
        ("KL791", "EHAM", "SBGR", "12:15", None)]


def test_the_default_get_is_not_reached_in_tests(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path, ams: vrs.Airport,
                                                 gru: vrs.Airport) -> None:
    # The default `get` is `http_get`; a failure to open is a failed call, not an error.
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("no network in tests")

    monkeypatch.setattr(schedule.urllib.request, "urlopen", refuse)
    assert Schedule("k3y", tmp_path).departures(ams, gru, DAY) == Unavailable(reason="failed")
