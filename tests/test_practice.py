"""Practice mode: what flyby says about a run when the true spot is known."""
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from flyby import cli, practice as practice_mod
from flyby.model import CONUS, Clues, Media, Result
from flyby.practice import _covered, metres_apart, practice
from flyby.verify import Pass

TRUTH = (40.876466, -73.524951)
START = datetime(2026, 9, 8, 20, 17, 30, tzinfo=timezone.utc)


def media(**kw) -> Media:
    base = dict(source="s", audio_path=None, title="t", slug="t", duration_s=3600.0, kind="upload")
    base.update(kw)
    return Media(**base)


def clues(**kw) -> Clues:
    base = dict(days=[date(2026, 9, 8)], bbox=CONUS, area_label="continental USA", day_reason="d", area_reason="a")
    base.update(kw)
    return Clues(**base)


def a_pass(heard_s, km, seen=False, testable=True) -> Pass:
    return Pass(icao="a1b2c3", heard_s=heard_s, slant_m=km * 1000.0, speed_ms=60.0,
                p=(0.001 if seen else 0.5) if testable else None, z=(9.0 if seen else 0.0) if testable else None)


def flyover(t):
    return SimpleNamespace(t=t)


def run(monkeypatch, result, passes=(), flyovers=(), c=None, index=True):
    """practice() with the flight data stood in for by a list of passes."""
    asked = []
    monkeypatch.setattr(practice_mod, "cached_index", lambda day, box, root: asked.append(day) or (Path("x.npz") if index else None))
    monkeypatch.setattr(practice_mod, "_passes", lambda truth, start, spec, indexes: (
        list(passes), [q for q in passes if q.p is not None]))
    notes = practice(TRUTH, media(), c or clues(start_utc=START), result, list(flyovers), Path("rec.npz"), Path("days"))
    return notes, asked


def test_metres_apart():
    assert metres_apart(TRUTH, TRUTH) == 0.0
    assert metres_apart(TRUTH, (40.87634, -73.52521)) == pytest.approx(26.0, abs=1.0)       # the reference clip's answer
    assert metres_apart((40.0, -100.0), (41.0, -100.0)) == pytest.approx(111_320.0)


@pytest.mark.parametrize("verdict, where, words", [
    ("located", (40.87634, -73.52521), "This run worked."),
    ("not sure", (40.87634, -73.52521), "The lead was right"),
    ("located", (41.5, -73.5), "flyby was sure, and it was wrong"),
    ("not sure", (41.5, -73.5), "right not to be sure"),
])
def test_an_answer_is_measured_against_the_true_spot(monkeypatch, verdict, where, words):
    notes, _ = run(monkeypatch, Result(verdict=verdict, headline="h", lat=where[0], lon=where[1]), passes=[a_pass(100.0, 2.0)])
    assert words in notes[0] and ("26 m" in notes[0] or "69 km" in notes[0])
    assert not any(n.startswith("Practice: so") for n in notes)


def test_no_match_with_nothing_in_the_data_says_so(monkeypatch):
    notes, asked = run(monkeypatch, Result(verdict="no match", headline="h"), flyovers=[flyover(358.0)])
    assert "named no place" in notes[0] and "0 aircraft passes within 12 km" in notes[1]
    assert notes[-1] == "Practice: so there was nothing in the flight data to find here."
    assert asked == [date(2026, 9, 8)]           # an hour from 20:17 UTC stays inside one day


def test_no_match_with_aircraft_that_cannot_be_heard(monkeypatch):
    passes = [a_pass(890.0, 3.7), a_pass(1997.0, 3.0), a_pass(2834.0, 1.6), a_pass(3312.0, 7.4, testable=False)]
    notes, _ = run(monkeypatch, Result(verdict="no match", headline="h"), passes, [flyover(358.0), flyover(1375.0), flyover(2306.0)])
    assert "4 aircraft passes within 12 km" in notes[1] and "2 of them within 3 km" in notes[1]
    assert "0 of the 3 passes that could be tested" in notes[2] and "Luck alone gives about 0.0" in notes[2]
    assert "0 of the 3 flyovers flyby measured" in notes[3] and "Luck alone gives about 0.6" in notes[3]
    assert "cannot be made out" in notes[-1]


def test_no_match_with_aircraft_that_can_be_heard(monkeypatch):
    passes = [a_pass(360.0, 2.0, seen=True), a_pass(2400.0, 2.5, seen=True)]
    one, _ = run(monkeypatch, Result(verdict="no match", headline="h"), passes, [flyover(358.0)])
    assert "1 of the 1 flyovers" in one[3] and "did not measure two" in one[-1]
    two, _ = run(monkeypatch, Result(verdict="no match", headline="h"), passes, [flyover(358.0), flyover(2375.0)])
    assert "2 of the 2 flyovers" in two[3] and "worth a closer look" in two[-1]


def test_a_busy_sky_is_not_mistaken_for_aircraft_that_were_heard(monkeypatch):
    # Port Canaveral: 43 passes in 65 minutes, so any sound is within 90 s of one, and 1 "seen" in 43 is what luck gives
    passes = [a_pass(90.0 * k, 9.0, seen=(k == 7)) for k in range(43)]
    notes, _ = run(monkeypatch, Result(verdict="no match", headline="h"), passes, [flyover(146.0), flyover(3567.0)])
    assert "1 of the 43 passes" in notes[2] and "Luck alone gives about 0.4" in notes[2]
    assert "2 of the 2 flyovers" in notes[3] and "Luck alone gives about 2.0" in notes[3]
    assert "cannot be made out" in notes[-1]
    # the same sky with four clear aircraft in it: heard, but lining up in time still proves nothing
    passes = [a_pass(90.0 * k, 9.0, seen=(k % 10 == 0)) for k in range(40)]
    notes, _ = run(monkeypatch, Result(verdict="no match", headline="h"), passes, [flyover(146.0), flyover(3567.0)])
    assert "did not measure two" in notes[-1]


def test_covered():
    assert _covered([], 3600.0) == 0.0
    assert _covered([1800.0], 3600.0) == pytest.approx(180 / 3600)
    assert _covered([0.0, 3600.0], 3600.0) == pytest.approx(180 / 3600)          # half of each window is off the end
    assert _covered([1000.0, 1060.0], 3600.0) == pytest.approx(240 / 3600)       # overlapping windows count once
    assert _covered([90.0 * k for k in range(41)], 3600.0) == pytest.approx(1.0)


def test_what_cannot_be_known_is_said(monkeypatch):
    blind = Result(verdict="no match", headline="h")
    notes, _ = run(monkeypatch, blind, c=clues())                       # no start time anywhere
    assert "start time is not known" in notes[-1] and "--start" in notes[-1]
    notes, _ = run(monkeypatch, blind, index=False)
    assert "no flight data on this Mac covers the true spot" in notes[-1]
    notes, _ = run(monkeypatch, blind, c=clues(start_utc=START, bbox=(24.0, 30.0, -90.0, -80.0)))
    assert "outside the area that was searched" in notes[1]


def test_a_match_close_to_the_true_spot_supplies_the_clock(monkeypatch):
    found = Result(verdict="located", headline="h", lat=40.87634, lon=-73.52521, recording_start_utc=START)
    notes, asked = run(monkeypatch, found, passes=[a_pass(100.0, 2.0, seen=True)], c=clues())
    assert asked == [date(2026, 9, 8)] and "1 aircraft passes" in notes[1]
    # a match far from the true spot implies a clock that means nothing there
    far = Result(verdict="located", headline="h", lat=41.5, lon=-73.5, recording_start_utc=START)
    notes, asked = run(monkeypatch, far, c=clues())
    assert asked == [] and "start time is not known" in notes[-1]


def test_a_recording_across_midnight_uses_both_days(monkeypatch):
    late = clues(start_utc=datetime(2026, 9, 8, 23, 30, tzinfo=timezone.utc))
    _, asked = run(monkeypatch, Result(verdict="no match", headline="h"), c=late)
    assert asked == [date(2026, 9, 8), date(2026, 9, 9)]


def test_known_must_be_a_position(capsys):
    assert cli.run(["x", "--known", "Huron, Ohio"]) == 1
    assert "--known Huron, Ohio is not a position" in capsys.readouterr().out
