"""The parts of the command that turn options and the video's own words into answers."""
import argparse
import ast
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from flyby import cli
from flyby.model import CONUS, Answers, Area, FlybyError, Frame, Hint, Media, PlaceNotFound, Result
from flyby.pictures import Seen


def media(**kw) -> Media:
    base = dict(source="s", audio_path=None, title="t", slug="t", duration_s=6 * 3600.0, kind="upload")
    base.update(kw)
    return Media(**base)


def options(*argv) -> argparse.Namespace:
    return cli._parser().parse_args(list(argv))


def test_options_answer_questions_ahead_of_time():
    a = cli._given(options("x", "--area", "Ohio", "--date", "2026-09-08", "--start", "2026-09-08T20:17Z",
                           "--section", "2:00-4:00", "--effort", "quick"), media())
    assert (a.area, a.day, a.section, a.effort) == ("Ohio", "2026-09-08", (7200.0, 14400.0), "quick")
    assert a.start_utc == datetime(2026, 9, 8, 20, 17, tzinfo=timezone.utc)
    assert a.start_tol_s == 180.0           # --start promises "to a few minutes"
    assert cli._given(options("x", "--start", "2026-09-08T20:17", "--start-tol", "10m"), media()).start_tol_s == 600.0
    plain = cli._given(options("x"), media())
    assert plain == Answers() and plain.start_tol_s is None


@pytest.mark.parametrize("text, seconds", [("90", 90.0), ("90s", 90.0), ("10m", 600.0), ("1h", 3600.0), (" 2.5 m ", 150.0)])
def test_start_tolerance_forms(text, seconds):
    assert cli._seconds(text) == seconds


@pytest.mark.parametrize("argv", [("x", "--section", "9:00-10:00"), ("x", "--section", "banana"), ("x", "--section", "1"),
                                  ("x", "--start", "tea time"), ("x", "--start", "2026-09-08T20:17", "--start-tol", "soon")])
def test_options_that_cannot_be_read_say_what_form_to_use(argv):
    with pytest.raises(FlybyError):
        cli._given(options(*argv), media())


PLACE = Hint("place", "Huron, Ohio", 'the title says "Huron, Ohio"', "likely")
GUESS = Hint("place", "Florida", 'the description says "Park in Florida"', "weak")
DAY = Hint("day", "2026-09-08", 'the title says "September 8, 2026"', "likely")
START = Hint("start", "2026-09-08T23:45:00+00:00", 'the title says "7:45 PM EDT"', "likely")


def test_with_nobody_to_ask_the_videos_own_words_are_taken():
    a, why = cli._take_stated(Answers(), [PLACE, DAY], media())
    assert (a.area, a.day, why) == ("Huron, Ohio", "2026-09-08", 'the title says "Huron, Ohio"')
    # a written start time is better than a written day, and is trusted to ten minutes
    a, _ = cli._take_stated(Answers(), [DAY, START], media())
    assert a.day is None and a.start_utc == datetime(2026, 9, 8, 23, 45, tzinfo=timezone.utc) and a.start_tol_s == 600.0


def test_a_weak_guess_is_not_taken_unasked():
    a, why = cli._take_stated(Answers(), [GUESS], media())
    assert a.area is None and why is None


def test_what_the_person_or_the_source_states_beats_a_hint():
    a, why = cli._take_stated(Answers(area="Texas", day="2026-09-01"), [PLACE, DAY], media())
    assert (a.area, a.day, why) == ("Texas", "2026-09-01", None)
    assert cli._take_stated(Answers(), [PLACE], media(location_hint=(41.0, -82.0)))[0].area is None
    replay = media(started_utc=datetime(2026, 9, 8, 11, 45, tzinfo=timezone.utc))
    assert cli._take_stated(Answers(), [DAY, START], replay)[0] == Answers()


def test_no_link_and_nobody_to_ask_is_a_plain_error(capsys):
    assert cli.run([]) == 1                 # under pytest there is no terminal
    assert "Give a video link or a file" in capsys.readouterr().out


def test_the_engine_runs_at_the_air_temperature_it_is_given():
    show = "from flyby import enginecli; from dopplertrack.doppler import speed_of_sound; print(round(speed_of_sound(), 1))"
    run = lambda env: subprocess.run([sys.executable, "-c", show], env={**os.environ, **env}, capture_output=True, text=True).stdout
    assert run({"FLYBY_AIR_C": ""}).strip() == "340.3"
    assert run({"FLYBY_AIR_C": "-10.0"}).strip() == "325.2"


def test_a_start_with_no_time_of_day_is_refused():
    # a bare day used to be read as midnight, to within three minutes
    with pytest.raises(FlybyError, match="no time of day"):
        cli._given(options("x", "--start", "2026-09-08"), media())


def test_a_day_and_a_start_time_that_disagree_are_refused():
    with pytest.raises(FlybyError, match="different days"):
        cli._given(options("x", "--date", "2026-09-01", "--start", "2026-09-08T20:17"), media())
    # an evening in the USA is already the next day in UTC: that agrees
    a = cli._given(options("x", "--date", "2026-09-08", "--start", "2026-09-09T00:17"), media())
    assert a.day == "2026-09-08" and a.start_utc == datetime(2026, 9, 9, 0, 17, tzinfo=timezone.utc)
    assert cli._given(options("x", "--date", "2026-09-07..2026-09-09", "--start", "2026-09-08T20:17"), media()).start_utc


def test_a_number_of_searches_answers_the_effort_question(capsys):
    assert cli._given(options("x", "--searches", "2"), media()).effort == "2 searches"
    assert cli._given(options("x", "--searches", "2", "--effort", "quick"), media()).effort == "quick"
    with pytest.raises(SystemExit):
        options("x", "--searches", "0")
    assert "1 or more" in capsys.readouterr().err


def staged(monkeypatch, tmp_path, hints=(), unknown_place=None, map_down=False):
    """cli.run with every slow step stood in for. Returns the steps in the order they ran."""
    steps: list[tuple] = []
    m = media(uploaded=date(2026, 9, 10))

    def clues(media, area_arg=None, **said):
        steps.append(("clues", area_arg))
        if area_arg is not None and map_down:
            raise FlybyError(f"Could not look up '{area_arg}' (HTTP Error 429).")
        if area_arg is not None and area_arg == unknown_place:
            raise PlaceNotFound(f'No place named "{area_arg}" was found.')
        return SimpleNamespace(day_reason="the day", area_reason="the area")

    def fetch(media, runs, log, section):
        steps.append(("fetch", section))
        return replace(media, audio_path=tmp_path / "audio.opus")

    def locate(media, clues, *rest, **kw):
        steps.append(("search", list(media.notes)))
        return Result(verdict="no match", headline="No location found.")

    monkeypatch.setattr(cli, "prepare_env", lambda: None)
    monkeypatch.setattr(cli, "data_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "probe", lambda source, log: m)
    monkeypatch.setattr(cli, "read_hints", lambda about, uploaded: list(hints))
    monkeypatch.setattr(cli, "find_clues", clues)
    monkeypatch.setattr(cli, "fetch", fetch)
    monkeypatch.setattr(cli, "measure", lambda audio, work, ui: ([], []))
    monkeypatch.setattr(cli.engine, "locate", locate)
    monkeypatch.setattr(cli, "write_page", lambda *a, **kw: tmp_path / "page.html")
    monkeypatch.setattr(cli, "print_result", lambda *a: None)
    return steps


def test_the_day_and_place_are_settled_before_anything_is_downloaded(monkeypatch, tmp_path, capsys):
    steps = staged(monkeypatch, tmp_path, unknown_place="Atlantis")
    assert cli.run(["x", "--area", "Atlantis", "--section", "2:00-4:00"]) == 1
    assert steps == [("clues", "Atlantis")] and "Atlantis" in capsys.readouterr().out      # no download, no search


def test_a_place_read_off_the_video_that_no_map_knows_does_not_stop_the_search(monkeypatch, tmp_path, capsys):
    steps = staged(monkeypatch, tmp_path, hints=[PLACE], unknown_place="Huron, Ohio")
    assert cli.run(["x"]) == 3
    assert [s[0] for s in steps] == ["clues", "clues", "fetch", "search"]
    assert steps[0][1] == "Huron, Ohio" and steps[1][1] is None             # tried, then the whole USA
    assert any("no map search could find" in note for note in steps[-1][1])


def test_a_map_service_that_cannot_be_reached_is_an_error_not_a_whole_usa_search(monkeypatch, tmp_path, capsys):
    # "no such place" and "the map did not answer" are different: only the first is a reason to search wider
    steps = staged(monkeypatch, tmp_path, hints=[PLACE], map_down=True)
    assert cli.run(["x"]) == 1
    assert steps == [("clues", "Huron, Ohio")] and "Could not look up" in capsys.readouterr().out


def test_a_day_that_cannot_be_read_is_refused_even_when_a_start_time_is_given():
    for bad in ("garbage", "2026-09-01..2026-09-30", "2026-09-09..2026-09-01"):
        with pytest.raises(FlybyError):
            cli._given(options("x", "--date", bad, "--start", "2026-09-08T20:17"), media())


def test_the_towns_a_video_names_are_checked_on_the_map_only_when_they_will_be_used(monkeypatch, tmp_path):
    town = Hint("place", "Green, Kentucky", 'the title says "LIVE Bowling Green, KY"', "stated",
                ("LIVE Bowling Green", "Bowling Green", "Green"))
    asked = []

    def confirm(hints):
        asked.append(hints)
        return [replace(hints[0], value="Bowling Green, Kentucky", towns=())]

    steps = staged(monkeypatch, tmp_path, hints=[town])
    monkeypatch.setattr(cli, "confirm_places", confirm)
    assert cli.run(["x"]) == 3
    assert len(asked) == 1 and steps[0] == ("clues", "Bowling Green, Kentucky")
    assert cli.run(["x", "--area", "Ohio"]) == 3            # the person said where: nothing to look up
    assert len(asked) == 1 and steps[-3] == ("clues", "Ohio")


def test_the_progress_line_is_closed_before_the_questions(monkeypatch, tmp_path):
    # in a real terminal a line left spinning sat on top of the first question
    town = Hint("place", "Huron, Ohio", 'the title says "Huron, OH"', "stated", ("Huron",))
    events = []

    class Spy:
        def section(self, title): pass
        def step(self, key, **words): events.append(("step", key))
        def start(self, text): events.append(("start", text))
        def done(self, text): events.append(("done", text))
        def progress(self, *a): pass
        def note(self, text): pass
        def close(self): pass

    staged(monkeypatch, tmp_path, hints=[town])
    monkeypatch.setattr(cli, "make_ui", lambda console: Spy())
    monkeypatch.setattr(cli, "confirm_places", lambda hints: hints)
    cli.run(["x"])
    at = events.index(("step", "towns"))
    assert events[at + 1][0] == "done"


# ---------------------------------------------------------------- the picture tools, wired in

EASTERN = Area((24.0, 49.0, -90.0, -66.0), "the Eastern time zone",
               "The clock in the picture runs 4 hours behind UTC. That is Eastern time.", sure=True)
STRIP = Area((24.0, 50.0, -100.0, -80.0), "the strip where the sun was that low", "The picture goes dark at 00:41 UTC.")
STREAM_START = datetime(2026, 9, 9, 11, 45, 32, tzinfo=timezone.utc)


def pictured(monkeypatch, tmp_path, seen, result=None, hints=(), searched=(40.0, 43.0, -84.0, -81.0)):
    """staged(), for a video that has a picture, with the picture tools stood in for.
    Returns what each step was handed."""
    got = {"see": 0}
    staged(monkeypatch, tmp_path, hints=hints)
    m = media(uploaded=date(2026, 9, 10), started_utc=STREAM_START, video={"has_video": True})

    def see(media, folder, bin_dir, pages, ui):
        got["see"] += 1
        got["folder"] = folder
        return seen

    def clues(media, area_arg=None, **said):
        got["clues"] = dict(area_arg=area_arg, **said)
        return SimpleNamespace(day_reason="the day", area_reason="the area", bbox=searched, area_label="around Huron, Ohio",
                               start_utc=media.started_utc)

    def locate(media, clues, *rest, **kw):
        got["notes"] = list(media.notes)
        return result or Result(verdict="no match", headline="No location found.")

    def page(*a, **kw):
        got["page"] = kw
        return tmp_path / "page.html"

    monkeypatch.setattr(cli, "probe", lambda source, log: m)
    monkeypatch.setattr(cli, "see", see)
    monkeypatch.setattr(cli, "find_clues", clues)
    monkeypatch.setattr(cli.engine, "locate", locate)
    monkeypatch.setattr(cli, "write_page", page)
    monkeypatch.setattr(cli, "place_name", lambda lat, lon: None)
    return got


def test_no_pictures_skips_every_picture_tool(monkeypatch, tmp_path):
    got = pictured(monkeypatch, tmp_path, Seen())
    assert cli.run(["x", "--no-pictures"]) == 3
    assert got["see"] == 0 and got["page"] == {"frames": [], "pictures_page": None}


def test_a_caption_on_the_picture_names_the_place_when_nothing_else_does(monkeypatch, tmp_path):
    caption = Hint("place", "Huron, Ohio", 'a caption on the picture says "Main Street Railfan - Huron OHIO"', "likely")
    seen = Seen(hints=[caption], notes=["The camera's own clock runs 19 s behind the stream's clock."],
                page=tmp_path / "pictures.html")
    got = pictured(monkeypatch, tmp_path, seen)
    assert cli.run(["x", "--section", "2:00-4:00"]) == 3
    assert got["clues"]["area_arg"] == "Huron, Ohio" and got["clues"]["area_why"] == caption.because
    assert "The camera's own clock runs 19 s behind the stream's clock." in got["notes"]
    assert got["folder"] == tmp_path / "runs" / "t" / "pictures"          # the whole video's folder, not the part's
    assert got["page"]["pictures_page"] == tmp_path / "pictures.html"


def test_with_nobody_to_ask_only_a_sure_area_narrows_the_search(monkeypatch, tmp_path):
    got = pictured(monkeypatch, tmp_path, Seen(areas=[EASTERN, STRIP]))
    assert cli.run(["x"]) == 3
    assert got["clues"]["area_arg"] is None and got["clues"]["bbox_arg"] == EASTERN.bbox      # the light is not trusted alone
    assert got["clues"]["box"] == (EASTERN.label, EASTERN.why)
    got = pictured(monkeypatch, tmp_path, Seen(areas=[STRIP]))
    assert cli.run(["x"]) == 3
    assert got["clues"]["bbox_arg"] is None and got["clues"]["box"] is None


def test_a_place_the_person_gave_beats_the_area_and_a_clash_is_said(monkeypatch, tmp_path):
    got = pictured(monkeypatch, tmp_path, Seen(areas=[EASTERN]), searched=(32.0, 35.0, -119.0, -116.0))
    assert cli.run(["x", "--area", "Los Angeles"]) == 3
    assert got["clues"]["area_arg"] == "Los Angeles" and got["clues"]["bbox_arg"] is None
    assert any("is outside the Eastern time zone" in note for note in got["notes"])
    got = pictured(monkeypatch, tmp_path, Seen(areas=[EASTERN]))          # Huron lies inside it: nothing to say
    assert cli.run(["x", "--area", "Huron, Ohio"]) == 3
    assert not any("outside" in note for note in got["notes"])
    got = pictured(monkeypatch, tmp_path, Seen(areas=[EASTERN]), searched=CONUS)
    assert cli.run(["x", "--area", "usa"]) == 3
    assert not any("outside" in note for note in got["notes"])


def test_two_clocks_far_apart_loosen_the_start_time(monkeypatch, tmp_path):
    got = pictured(monkeypatch, tmp_path, Seen(clock_gap_s=-19.0))
    cli.run(["x"])
    assert got["clues"]["start_tol_s"] == 180.0 and not any("two clocks" in note for note in got["notes"])
    got = pictured(monkeypatch, tmp_path, Seen(clock_gap_s=-400.0))
    cli.run(["x"])
    assert got["clues"]["start_tol_s"] == 430.0
    assert "Because the two clocks disagree, the start time is trusted only to within 430 s." in got["notes"]
    got = pictured(monkeypatch, tmp_path, Seen(clock_gap_s=-400.0))        # the person's own word on the time stands
    cli.run(["x", "--start", "2026-09-09T11:45", "--start-tol", "90s"])
    assert got["clues"]["start_tol_s"] == 90.0


def test_an_answer_is_checked_against_the_pictures(monkeypatch, tmp_path):
    frames = [Frame(tmp_path / "frame-000600.jpg", 600.0)]
    seen = Seen(frames=frames, areas=[EASTERN], light=[(0.0, 0.5, 0.2)], page=tmp_path / "pictures.html")
    located = Result(verdict="located", headline="Found.", lat=41.39, lon=-82.55)
    got = pictured(monkeypatch, tmp_path, seen, result=located)
    handed = {}

    def checks(seen, lat, lon, began):
        handed.update(lat=lat, lon=lon, began=began)
        return ["Sun check: the sun was 31 degrees up. That fits."]

    monkeypatch.setattr(cli, "cross_checks", checks)
    assert cli.run(["x", "--section", "2:00-4:00", "--no-open"]) == 0
    # the search used the part from 2:00, but the pictures are timed from second 0 of the whole video
    assert handed == dict(lat=41.39, lon=-82.55, began=STREAM_START)
    assert located.notes == ["Sun check: the sun was 31 degrees up. That fits."]
    assert got["page"] == {"frames": frames, "pictures_page": tmp_path / "pictures.html"}


def test_no_answer_means_nothing_to_check(monkeypatch, tmp_path):
    seen = Seen(areas=[EASTERN], light=[(0.0, 0.5, 0.2)])
    pictured(monkeypatch, tmp_path, seen)
    monkeypatch.setattr(cli, "cross_checks", lambda *a: pytest.fail("there is no answer to check"))
    assert cli.run(["x"]) == 3


def test_the_ai_guess_runs_only_when_asked_for_and_is_never_the_search_area_unasked(monkeypatch, tmp_path):
    frames = [Frame(tmp_path / "frame-000600.jpg", 600.0, 0.5, 0.2, 1.0)]
    guess = Hint("place", "Huron, Ohio", 'an AI looked at the pictures and says "a caption reads Huron OHIO"', "weak", ("Huron",))
    calls = []
    monkeypatch.setattr(cli, "ai_guess", lambda frames, folder: calls.append(folder) or [guess])
    monkeypatch.setattr(cli, "confirm_places", lambda hints: hints)
    got = pictured(monkeypatch, tmp_path, Seen(frames=frames))
    cli.run(["x"])
    assert calls == []
    cli.run(["x", "--ai-guess"])
    assert calls == [tmp_path / "runs" / "t" / "pictures" / "sent"]
    assert got["clues"]["area_arg"] is None            # a weak guess, and nobody here to weigh it


def test_an_ai_that_fails_is_a_note_and_the_run_goes_on(monkeypatch, tmp_path, capsys):
    frames = [Frame(tmp_path / "frame-000600.jpg", 600.0, 0.5, 0.2, 1.0)]

    def broken(frames, folder):
        raise FlybyError("The AI took too long to answer, so its guess was skipped.")

    monkeypatch.setattr(cli, "ai_guess", broken)
    got = pictured(monkeypatch, tmp_path, Seen(frames=frames))
    assert cli.run(["x", "--ai-guess"]) == 3
    assert "The AI took too long to answer, so its guess was skipped." in got["notes"]
    got = pictured(monkeypatch, tmp_path, Seen())
    assert cli.run(["x", "--ai-guess"]) == 3
    assert "--ai-guess needs pictures" in capsys.readouterr().out


def test_no_program_flyby_starts_is_handed_the_keyboard():
    # ffmpeg sets the terminal raw to listen for "q". Four at once leave it raw, and then Enter no longer
    # ends a line: after the picture step the questions could not be answered. Seen in a real terminal.
    for path in sorted(Path(cli.__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            starts = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                      and node.func.attr in ("run", "Popen", "call", "check_call", "check_output"))
            if starts:
                given = {k.arg for k in node.keywords}
                assert given & {"stdin", "input"}, f"{path.name}:{node.lineno} starts a program without stdin= or input="


def test_a_map_that_did_not_answer_is_not_called_a_check(monkeypatch, tmp_path, capsys):
    town = Hint("place", "Huron, Ohio", 'the title says "Huron, Ohio"', "likely", ("Huron",))
    staged(monkeypatch, tmp_path, hints=[town])
    monkeypatch.setattr(cli, "confirm_places", lambda hints: hints)        # no map: the hints come back as they were read
    cli.run(["x"])
    out = capsys.readouterr().out
    assert "Not every town name could be checked on the map" in out and "Town names checked" not in out


def test_an_ai_guess_without_pictures_is_refused_in_plain_words(monkeypatch, tmp_path, capsys):
    steps = staged(monkeypatch, tmp_path)
    assert cli.run(["x", "--ai-guess", "--no-pictures"]) == 1
    assert steps == [] and "--ai-guess needs the pictures" in capsys.readouterr().out
