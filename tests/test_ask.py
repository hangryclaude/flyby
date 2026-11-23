import io
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from rich.console import Console

from flyby import ask
from flyby.model import Answers, Area, FlybyError, Hint, Media

UTC = timezone.utc


def media(**kw) -> Media:
    base = dict(source="s", audio_path=Path("a.opus"), title="t", slug="t", duration_s=300.0, kind="upload")
    base.update(kw)
    return Media(**base)


def run(*lines, m=None, hints=(), given=None, extras=None):
    """Feed typed lines to the interview. Returns (answers, what was printed, the lines left over)."""
    stream = io.StringIO("".join(line + "\n" for line in lines))
    out = io.StringIO()
    answers = ask.interview(Console(file=out, width=100), m or media(), list(hints), given or Answers(), stream=stream,
                            extras=extras)
    return answers, out.getvalue(), stream.read()


DEFAULTS = replace(Answers(), still=True, real_sound=True, effort="normal")


def test_enter_everywhere_gives_defaults_only():
    answers, out, _ = run(*[""] * 12)
    assert answers == DEFAULTS
    assert "whole USA" in out
    assert "Here is what flyby will use" in out


def test_given_is_kept_and_returned_as_a_new_object():
    given = Answers(area="Ohio", bbox=(1.0, 2.0, 3.0, 4.0))
    answers, _, _ = run(*[""] * 12, given=given)
    assert answers.area == "Ohio" and answers.bbox == (1.0, 2.0, 3.0, 4.0)
    assert answers is not given and given.still is None


def test_place_hint_accepted_with_enter():
    hint = Hint("place", "NE Oklahoma", 'the title says "NE Oklahoma"', "likely")
    answers, out, _ = run(*[""] * 12, hints=[hint])
    assert answers.area == "NE Oklahoma"
    assert 'The title says "NE Oklahoma".' in out
    assert "20 times faster" in out


def test_strongest_place_hint_wins():
    weak = Hint("place", "Texas", "the tags say Texas", "weak")
    stated = Hint("place", "Ohio", "the title says Ohio", "stated")
    answers, _, _ = run(*[""] * 12, hints=[weak, stated])
    assert answers.area == "Ohio"


def test_latlon_hint_is_offered_as_the_file_position():
    hint = Hint("place", "36.15,-95.99", "the file carries a position", "stated")
    answers, out, _ = run(*[""] * 12, hints=[hint])
    assert answers.area == "36.15,-95.99"
    assert "near the position the file carries" in out


def test_place_hint_can_be_declined_or_replaced():
    hint = Hint("place", "NE Oklahoma", "the title says NE Oklahoma", "likely")
    # a "no" to the place on offer is kept as "the whole USA", so the offer cannot come back as the search area
    declined, out, _ = run("skip", *[""] * 12, hints=[hint])
    assert declined.area == "usa" and "Place:  the whole USA (slow)" in out
    assert run("Kansas", *[""] * 12, hints=[hint])[0].area == "Kansas"


def test_typed_place_and_usa_words():
    answers, out, _ = run("Ohio", *[""] * 12)
    assert answers.area == "Ohio" and "20 times faster" in out
    assert run("USA", *[""] * 12)[0].area is None


@pytest.mark.parametrize("text, want", [
    ("2026-09-08", "2026-09-08"),
    ("8 sep 2026", "2026-09-08"),
    ("sep 8 2026", "2026-09-08"),
    ("September 8th, 2026", "2026-09-08"),
    ("9/8/2026", "2026-09-08"),          # US order: month first
    ("9/8/26", "2026-09-08"),
    ("yesterday", "2026-11-21"),
    ("today", "2026-11-22"),
    ("sep 8", "2026-09-08"),
    ("dec 25", "2025-12-25"),            # no year: the latest such day that has come
    ("2026-09-07..2026-09-09", "2026-09-07..2026-09-09"),
    ("9/9/2026..9/7/2026", "2026-09-07..2026-09-09"),
    ("7 sep 2026 to 9 sep 2026", "2026-09-07..2026-09-09"),
    ("2026-09-08..2026-09-08", "2026-09-08"),
])
def test_parse_day_forms(text, want):
    assert ask.parse_day(text, today=date(2026, 11, 22)) == want


@pytest.mark.parametrize("text", ["banana", "13/45/2026", "2026-02-30", "2027-01-01", "sep", "8 8 2026", ""])
def test_parse_day_rejects(text):
    with pytest.raises(ValueError):
        ask.parse_day(text, today=date(2026, 11, 22))


def test_day_typed_in_the_interview():
    answers, out, _ = run("", "8 sep 2026", "", "", "", "", "")
    assert answers.day == "2026-09-08"
    assert "only look at flights on that day" in out
    assert run("", "2026-09-07..2026-09-09", *[""] * 8)[0].day == "2026-09-07..2026-09-09"


def test_bad_day_is_explained_then_second_miss_is_unknown(monkeypatch):
    answers, out, left = run("", "banana", "nonsense", *[""] * 6)
    assert answers.day is None
    assert out.count("Try a form like this: 2026-09-08") == 1
    assert "I will take it as: I do not know" in out
    assert "flyby will try a few days" in out


def test_bad_day_then_good_day():
    answers, out, _ = run("", "banana", "8 sep 2026", *[""] * 8)
    assert answers.day == "2026-09-08"
    assert "Try a form like this" in out


def test_upload_day_is_shown_but_not_taken_as_the_answer():
    # Enter must leave the day open, so the search still tries the days before the upload
    answers, out, _ = run("", "", *[""] * 8, m=media(uploaded=date(2026, 9, 8)))
    assert answers.day is None
    assert "put online on 2026-09-08" in out and "the days before it" in out


def test_day_hint_beats_upload_day():
    hint = Hint("day", "2026-09-06", "the title says 6 Sep", "likely")
    answers, _, _ = run("", "", *[""] * 8, hints=[hint], m=media(uploaded=date(2026, 9, 8)))
    assert answers.day == "2026-09-06"


@pytest.mark.parametrize("day, typed, clock, sure, want, tol", [
    # 8:17 pm Eastern daylight time is UTC-4: the next UTC day
    ("2026-09-08", "8:17 pm", "1", "2", datetime(2026, 9, 9, 0, 17, tzinfo=UTC), 600.0),
    # January is standard time, UTC-5
    ("2026-01-15", "8:17 pm", "1", "2", datetime(2026, 1, 16, 1, 17, tzinfo=UTC), 600.0),
    ("2026-09-08", "8pm", "4", "1", datetime(2026, 9, 9, 3, 0, tzinfo=UTC), 120.0),    # Pacific, UTC-7
    ("2026-01-15", "20:17", "4", "3", datetime(2026, 1, 16, 4, 17, tzinfo=UTC), 3600.0),  # Pacific, UTC-8
    ("2026-09-08", "20:17", "7", "2", datetime(2026, 9, 8, 20, 17, tzinfo=UTC), 600.0),   # UTC
    ("2026-09-08", "9:05 am", "2", "2", datetime(2026, 9, 8, 14, 5, tzinfo=UTC), 600.0),  # Central, UTC-5
    ("2026-09-08", "12 am", "3", "2", datetime(2026, 9, 8, 6, 0, tzinfo=UTC), 600.0),     # Mountain, midnight
    ("2026-09-08", "6 am", "6", "2", datetime(2026, 9, 8, 16, 0, tzinfo=UTC), 600.0),     # Hawaii, UTC-10
])
def test_time_clock_sureness_gives_utc(day, typed, clock, sure, want, tol):
    answers, _, _ = run("", day, typed, clock, sure, "", "", "")
    assert answers.start_utc == want
    assert answers.start_utc.utcoffset() == timedelta(0)
    assert answers.start_tol_s == tol


def test_time_said_in_utc_skips_the_clock_question():
    answers, out, _ = run("", "2026-09-08", "20:17 utc", "2", "", "", "")
    assert answers.start_utc == datetime(2026, 9, 8, 20, 17, tzinfo=UTC)
    assert "Which clock" not in out


def test_time_skipped_or_unreadable_leaves_start_unset():
    answers, out, _ = run("", "2026-09-08", "", "", "", "")
    assert answers.start_utc is None and answers.start_tol_s is None
    answers, out, _ = run("", "2026-09-08", "late", "evening", "", "", "")
    assert answers.start_utc is None
    assert "Try a form like this: 8:17 pm" in out


def test_no_clock_chosen_leaves_start_unset():
    answers, out, _ = run("", "2026-09-08", "8 pm", "", "", "", "")
    assert answers.start_utc is None
    assert "can not use the time" in out


def test_time_not_asked_for_a_range_of_days():
    _, out, _ = run("", "2026-09-07..2026-09-09", *[""] * 6)
    assert "What time" not in out


def test_start_hint_accepted_with_enter_asks_only_sureness():
    hint = Hint("start", "2026-09-09T00:17:00Z", "the title says 8:17 pm EDT", "stated")
    answers, out, _ = run("", "2026-09-08", "", "", "", "", "", hints=[hint])
    assert answers.start_utc == datetime(2026, 9, 9, 0, 17, tzinfo=UTC)
    assert answers.start_tol_s == 120.0
    assert "00:17 UTC" in out and "Which clock" not in out


def test_known_start_is_a_fact_and_nothing_is_asked_about_day_or_time():
    known = datetime(2026, 9, 9, 0, 17, tzinfo=UTC)
    answers, out, _ = run("", "", "", "", m=media(started_utc=known))
    assert "Start: 2026-09-09 00:17 UTC  (the video says so)" in out
    assert "What day" not in out and "What time" not in out
    assert answers.start_utc is None and answers.start_tol_s is None and answers.day is None


def test_everything_known_asks_nothing():
    given = Answers(area="Ohio", day="2026-09-08", start_utc=datetime(2026, 9, 9, 0, 17, tzinfo=UTC),
                    start_tol_s=120.0, section=(0.0, 7200.0), still=False, real_sound=True, effort="thorough")
    answers, out, left = run("x", "y", "z", given=given, m=media(duration_s=5 * 3600.0))
    assert answers == given
    assert left == "x\ny\nz\n"                      # not one line was read
    assert "Place: Ohio  (you gave this)" in out


def test_each_known_fact_removes_exactly_its_question():
    stems = ["Where was this", "What day was it", "What time did", "Which part", "Did the camera", "Is the sound real",
             "How long may"]
    m = media(duration_s=5 * 3600.0)
    _, out, _ = run(*[""] * 12, m=m)
    assert [s for s in stems if s in out] == ["Where was this", "What day was it", "Which part", "Did the camera",
                                            "Is the sound real", "How long may"]
    _, out, _ = run(*[""] * 12, m=m, given=Answers(area="Ohio", day="2026-09-08", section=(0.0, 60.0), still=True))
    assert [s for s in stems if s in out] == ["What time did", "Is the sound real", "How long may"]


def test_section_question_only_for_long_media():
    long = media(duration_s=4 * 3600.0)
    answers, out, _ = run("", "", "2:00-4:00", "", "", "", m=long)
    assert answers.section == (7200.0, 14400.0)
    assert "This video is 4 h 00 min long." in out and "daytime" in out.lower()
    assert "Which part" not in run(*[""] * 8)[1]
    assert "Which part" not in run(*[""] * 8, m=media(duration_s=3 * 3600.0))[1]


def test_section_choices():
    long = media(duration_s=10 * 3600.0)
    assert run("", "", "2", *[""] * 4, m=long)[0].section == (0.0, 7200.0)
    assert run("", "", "1", *[""] * 4, m=long)[0].section is None
    assert run("", "", "", *[""] * 4, m=long)[0].section is None
    assert run("", "", "1:30-2:15", *[""] * 4, m=long)[0].section == (5400.0, 8100.0)
    assert run("", "", "9:00-12:00", *[""] * 4, m=long)[0].section == (32400.0, 36000.0)   # end clamped


def test_section_past_the_end_is_explained():
    _, out, _ = run("", "", "20:00-21:00", "", *[""] * 4, m=media(duration_s=4 * 3600.0))
    assert "only 4 h 00 min long" in out


def test_camera_and_sound_answers():
    answers, out, _ = run("", "", "no", "no", "")
    assert answers.still is False and answers.real_sound is False
    assert "needs a microphone that stays still" in out and "result will likely be empty" in out
    assert "can hide the engines" in out
    answers, _, _ = run("", "", "n", "y", "")
    assert answers.still is False and answers.real_sound is True


def test_yes_no_garbage_then_unknown():
    answers, out, _ = run("", "", "maybe", "perhaps", "", "")
    assert answers.still is None
    assert "Try a form like this: yes" in out
    assert "Summary" not in out and "Camera: not known" in out


def test_effort_choices():
    assert run(*[""] * 4, "1")[0].effort == "quick"
    assert run(*[""] * 4, "3")[0].effort == "thorough"
    assert run(*[""] * 4, "")[0].effort == "normal"
    answers, out, _ = run(*[""] * 4, "9", "9")
    assert answers.effort == "normal" and "Pick a number from 1 to 3." in out


def test_summary_lists_what_will_be_used():
    _, out, _ = run("Ohio", "2026-09-08", "8:17 pm", "1", "2", "", "", "3")
    tail = out[out.index("Here is what flyby will use"):]
    for line in ["Place:  Ohio", "Day:    2026-09-08", "Start:  2026-09-09 00:17 UTC, within about 10 minutes",
                 "Part:   all of it", "Camera: stayed in one place", "Sound:  real", "Effort: thorough"]:
        assert line in tail


def test_questions_fit_one_line():
    _, out, _ = run(*[""] * 12, m=media(duration_s=5 * 3600.0))
    questions = [line[:line.index("]") + 1] for line in out.splitlines() if "?" in line and "]" in line]
    assert len(questions) >= 6
    # 70 for the words, 4 for the margin and the prompt mark in front of them
    assert all(len(q) <= 74 for q in questions), questions


@pytest.mark.parametrize("typed, want", [
    (r"/Users/a/My\ Video\ 1.mp4", "/Users/a/My Video 1.mp4"),
    ("'/Users/a/My Video.mp4'", "/Users/a/My Video.mp4"),
    ('"/Users/a/My Video.mp4"  ', "/Users/a/My Video.mp4"),
    (r"/Users/a/clip\ \(2\).mov", "/Users/a/clip (2).mov"),
    ("  https://youtu.be/abc123  ", "https://youtu.be/abc123"),
    (r"https://example.com/a\b", r"https://example.com/a\b"),
    ("file:///Users/a/My%20Video.mp4", "/Users/a/My Video.mp4"),
])
def test_ask_source_cleans_what_was_dragged(typed, want):
    assert ask.ask_source(Console(file=io.StringIO(), width=100), stream=io.StringIO(typed + "\n")) == want


def test_ask_source_loops_until_something_is_typed():
    out = io.StringIO()
    got = ask.ask_source(Console(file=out, width=100), stream=io.StringIO("\n\n   \nhttps://youtu.be/x\n"))
    assert got == "https://youtu.be/x"
    assert out.getvalue().count("I need a link or a file") == 3


def test_ask_source_gives_up_on_a_closed_input():
    with pytest.raises(FlybyError, match="link or file"):
        ask.ask_source(Console(file=io.StringIO(), width=100), stream=io.StringIO(""))


@pytest.mark.parametrize("text", ["october 2026", "aug 2026", "2026 sep"])
def test_a_month_and_a_year_with_no_day_is_asked_again_not_a_crash(text):
    with pytest.raises(ValueError):
        ask.parse_day(text)


def test_a_stretch_of_days_too_long_to_search_is_asked_again():
    answers, out, _ = run("", "2026-09-01..2026-09-20", "2026-09-07..2026-09-09", *[""] * 8)
    assert answers.day == "2026-09-07..2026-09-09" and "more than 7 days" in out


def test_the_files_own_position_is_offered_before_words_in_a_title():
    hint = Hint("place", "Ohio", 'the title says "Ohio"', "likely")
    answers, out, _ = run("", *[""] * 12, hints=[hint], m=media(location_hint=(40.0, -83.0)))
    assert answers.area == "40.0000,-83.0000" and "The file carries a position" in out
    # and a "no" to it is kept, so the search is not quietly narrowed to it anyway
    assert run("skip", *[""] * 12, m=media(location_hint=(40.0, -83.0)))[0].area == "usa"


def test_a_start_time_in_the_title_does_not_replace_the_day_the_person_gave():
    hint = Hint("start", "2026-09-09T00:17:00+00:00", 'the title says "Sep 8, 2026 8:17 PM EST"', "likely")
    # their day is a week earlier: the title's time is not offered, so Enter cannot adopt it
    answers, out, _ = run("", *[""] * 12, hints=[hint], given=Answers(day="2026-09-01"))
    assert answers.start_utc is None and answers.day == "2026-09-01" and "Press Enter to use that" not in out
    # the same evening, which is the next day in UTC, still agrees
    answers, _, _ = run("", *[""] * 12, hints=[hint], given=Answers(day="2026-09-08"))
    assert answers.start_utc == datetime(2026, 9, 9, 0, 17, tzinfo=timezone.utc)


def test_a_tolerance_given_ahead_is_not_asked_again():
    hint = Hint("start", "2026-09-09T00:17:00+00:00", 'the title says "8:17 PM EST"', "likely")
    answers, out, _ = run("", *[""] * 12, hints=[hint], given=Answers(day="2026-09-08", start_tol_s=3600.0))
    assert answers.start_tol_s == 3600.0 and "How sure are you" not in out


def test_an_effort_given_ahead_is_not_asked_again():
    answers, out, _ = run(*[""] * 12, given=Answers(effort="2 searches"))
    assert answers.effort == "2 searches" and "How long may flyby work" not in out and "Effort: 2 searches" in out


def test_a_start_time_in_the_title_is_not_offered_outside_the_days_the_person_gave():
    hint = Hint("start", "2026-01-05T14:30:00+00:00", 'the title says "Jan 5, 2026 9:30 AM EST"', "likely")
    answers, out, _ = run("", "2026-09-07..2026-09-09", *[""] * 10, hints=[hint])
    assert answers.day == "2026-09-07..2026-09-09" and answers.start_utc is None and "Press Enter to use that" not in out
    # inside the range it is offered and can be taken
    inside = Hint("start", "2026-09-08T14:30:00+00:00", 'the title says "Sep 8, 2026 10:30 AM EDT"', "likely")
    answers, out, _ = run("", "2026-09-07..2026-09-09", *[""] * 10, hints=[inside])
    assert answers.start_utc == datetime(2026, 9, 8, 14, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------- what the pictures add

EASTERN = Area((24.0, 50.0, -90.5, -66.0), "the Eastern time zone",
               "The clock in the picture runs 4 hours behind UTC. That is Eastern time.", sure=True)


def test_an_area_from_the_pictures_is_offered_when_nothing_names_a_place():
    answers, out, _ = run(*[""] * 12, extras=ask.Extras(area=EASTERN))
    assert answers.area is None and answers.bbox == EASTERN.bbox
    assert (answers.box_label, answers.box_why) == (EASTERN.label, EASTERN.why)
    assert "That is Eastern time." in out and "Press Enter to search only there." in out
    assert "Place:  the Eastern time zone" in out and "the whole USA (slow)" not in out


def test_a_typed_place_beats_the_area_on_offer():
    answers, _, _ = run("Ohio", *[""] * 12, extras=ask.Extras(area=EASTERN))
    assert answers.area == "Ohio" and answers.bbox is None and answers.box_label is None


def test_saying_no_to_the_area_on_offer_searches_the_whole_usa():
    answers, out, _ = run("skip", *[""] * 12, extras=ask.Extras(area=EASTERN))
    assert answers.area == "usa" and answers.bbox is None
    assert "whole USA" in out


def test_a_named_place_comes_before_the_area_and_no_picture_question_is_asked():
    called = []
    extras = ask.Extras(area=EASTERN, open_pictures=lambda: called.append("page"), ask_ai=lambda: called.append("ai") or [])
    hint = Hint("place", "Huron, Ohio", 'a caption on the picture says "Huron OHIO"', "likely")
    answers, out, _ = run(*[""] * 12, hints=[hint], extras=extras)
    assert answers.area == "Huron, Ohio" and answers.bbox is None and called == []
    assert "picture search" not in out and "AI" not in out


def test_the_picture_search_page_opens_only_on_a_yes():
    opened = []
    answers, out, _ = run("yes", "Ohio", *[""] * 12, extras=ask.Extras(open_pictures=lambda: opened.append(1)))
    assert opened == [1] and answers.area == "Ohio"
    assert "Nothing the video says names a place." in out and "It is open in your browser." in out
    opened.clear()
    answers, _, _ = run(*[""] * 12, extras=ask.Extras(open_pictures=lambda: opened.append(1)))
    assert opened == [] and answers.area is None


def test_an_ai_guess_is_asked_for_and_offered_as_a_guess():
    guess = Hint("place", "Huron, Ohio", 'an AI looked at the pictures and says "a caption reads Huron OHIO"', "weak")
    asked = []
    extras = ask.Extras(ask_ai=lambda: asked.append(1) or [guess], area=EASTERN)
    answers, out, _ = run("y", "", *[""] * 12, extras=extras)
    assert asked == [1] and answers.area == "Huron, Ohio" and answers.bbox is None
    assert "The pictures go to Claude, on your own plan." in out and "This is only a guess." in out


def test_an_ai_is_not_asked_without_a_yes_and_an_empty_guess_falls_back_to_the_area():
    asked = []
    answers, _, _ = run(*[""] * 12, extras=ask.Extras(ask_ai=lambda: asked.append(1) or []))
    assert asked == [] and answers.area is None
    answers, out, _ = run("yes", "", *[""] * 12, extras=ask.Extras(ask_ai=lambda: [], area=EASTERN))
    assert "The AI could not tell." in out and answers.bbox == EASTERN.bbox


CAMERA = Hint("clock", "2026-09-09T07:45:13", 'the clock in the picture read "2026-09-09 10:15:13" at 2:30:00 into the video',
              "likely")


def test_a_camera_clock_gives_the_start_once_its_time_zone_is_known():
    #        where  day          start  clock      sure
    lines = ["", "2026-09-09", "", "1", "1", *[""] * 8]
    answers, out, _ = run(*lines, hints=[CAMERA])
    assert answers.start_utc == datetime(2026, 9, 9, 11, 45, 13, tzinfo=UTC)       # 07:45:13 Eastern summer time
    assert answers.start_tol_s == 120.0
    assert "So the video began at 07:45:13 on 2026-09-09, by that clock." in out
    assert "A camera shows local time." in out


def test_a_camera_clock_with_no_time_zone_is_not_used():
    answers, out, _ = run("", "2026-09-09", "", "skip", *[""] * 8, hints=[CAMERA])
    assert answers.start_utc is None and "No clock." in out


def test_a_camera_clock_on_another_day_than_the_person_gave_is_left_out():
    answers, out, _ = run("", "2026-06-01", *[""] * 10, hints=[CAMERA])
    assert answers.start_utc is None and "by that clock" not in out


def test_a_start_the_video_states_comes_before_the_camera_clock():
    said = Hint("start", "2026-09-09T12:00:00+00:00", 'the title says "Sep 9, 2026 8:00 am EDT"', "stated")
    answers, out, _ = run("", "2026-09-09", "", "1", *[""] * 8, hints=[CAMERA, said])
    assert answers.start_utc == datetime(2026, 9, 9, 12, 0, tzinfo=UTC) and "by that clock" not in out


def test_a_person_can_type_their_own_time_over_the_camera_clock():
    answers, _, _ = run("", "2026-09-09", "8:17 pm", "1", "1", *[""] * 8, hints=[CAMERA])
    assert answers.start_utc == datetime(2026, 9, 10, 0, 17, tzinfo=UTC)


def test_a_camera_clock_with_no_am_or_pm_is_asked_about_not_taken():
    unsure = Hint("clock", "2026-09-09T03:00:00", 'the clock in the picture read "9/9/2026 3:00:00" at 0:00:00 into the video',
                  "weak")
    #        where  day          start      clock  sure
    lines = ["", "2026-09-09", "3:00 pm", "1", "1", *[""] * 8]
    answers, out, _ = run(*lines, hints=[unsure])
    assert "That clock shows no AM or PM. So the video began at 03:00 or at 15:00." in out
    assert "by that clock" not in out
    assert answers.start_utc == datetime(2026, 9, 9, 19, 0, tzinfo=UTC)            # 15:00 Eastern summer time
    # Enter alone takes neither reading
    answers, out, _ = run("", "2026-09-09", *[""] * 10, hints=[unsure])
    assert answers.start_utc is None and "No time." in out
