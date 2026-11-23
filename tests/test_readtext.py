import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from flyby import readtext
from flyby.areacodes import AREA_CODES
from flyby.model import FlybyError, Frame, TextLine
from flyby.readtext import read, understand

# the measured test video: a Huron, Ohio rail camera whose stream began at 11:45:32 UTC
STARTED = datetime(2026, 9, 9, 11, 45, 32, tzinfo=timezone.utc)
CAPTION = "Main Street Railfan - Huron OHIO"


def line(text, t_s, x=0.70, y=0.02, w=0.28, h=0.04, frame=None):
    return TextLine(text, 1.0, x, y, w, h, t_s, Path(frame or f"/f/{t_s}.jpg"))


def scout_lines():
    """What Vision read off the two scout pictures, at second 9000 and second 30000."""
    return [line("2026-09-09 10.15:13 Wed", 9000.0), line(CAPTION, 9000.0, x=0.01, y=0.93, w=0.4),
            line("2026-09-09 16:05:13 Wed", 30000.0), line(CAPTION, 30000.0, x=0.01, y=0.93, w=0.4)]


# ---- the clock ----

@pytest.mark.parametrize("first, second, expected", [
    ("2026-09-09 10:15:13", "2026-09-09 16:05:13", datetime(2026, 9, 9, 7, 45, 13)),
    ("2026-09-09 10.15:13 Wed", "2026-09-09 16.05.13 Wed", datetime(2026, 9, 9, 7, 45, 13)),
    ("09/09/2026 10:15:13 AM", "09/09/2026 04:05:13 PM", datetime(2026, 9, 9, 7, 45, 13)),
    ("09-09-2026 Wed 10:15:13", "09-09-2026 Wed 16:05:13", datetime(2026, 9, 9, 7, 45, 13)),
    ("Wed Sep 9 2026 10:15:13", "Wed Sep 9 2026 16:05:13", datetime(2026, 9, 9, 7, 45, 13)),
    ("10:15:13 9 September 2026", "16:05:13 9 September 2026", datetime(2026, 9, 9, 7, 45, 13)),
])
def test_each_clock_form(first, second, expected):
    clock = understand([line(first, 9000.0), line(second, 30000.0)]).clock
    assert clock is not None and clock.local_start == expected and clock.frames == 2
    assert clock.seen == first and clock.at_s == 9000.0


def test_one_picture_is_not_enough():
    assert understand([line("2026-09-09 10:15:13", 9000.0)]).clock is None


def test_two_clocks_must_move_on_as_the_video_did():
    # 6 h 10 min apart on the clock, 5 h 50 min apart in the video: a replay cut, or two cameras
    assert understand([line("2026-09-09 10:15:13", 9000.0), line("2026-09-09 16:25:13", 30000.0)]).clock is None
    # a clock that drifts 2 s still counts; 5 s does not
    assert understand([line("2026-09-09 10:15:13", 9000.0), line("2026-09-09 16:05:15", 30000.0)]).clock
    assert understand([line("2026-09-09 10:15:13", 9000.0), line("2026-09-09 16:05:18", 30000.0)]).clock is None


def test_the_same_picture_twice_is_one_picture():
    lines = [line("2026-09-09 10:15:13", 9000.0), line("2026-09-09 10:15:13", 9000.0, frame="/g/copy.jpg")]
    assert understand(lines).clock is None


def test_the_site_thumbnail_has_no_time_so_it_cannot_count():
    assert understand([line("2026-09-09 10:15:13", None), line("2026-09-09 16:05:13", 30000.0)]).clock is None


def test_month_or_day_first_is_never_guessed():
    # 03/04 could be March 4 or 3 April, and both readings move on as the video did
    assert understand([line("03/04/2026 10:15:13", 9000.0), line("03/04/2026 16:05:13", 30000.0)]).clock is None


def test_a_picture_can_settle_month_or_day_first():
    lines = [line("03/04/2026 10:15:13", 9000.0), line("03/04/2026 16:05:13", 30000.0),
             line("13/04/2026 10:15:13", 9000.0 + 10 * 86400)]          # day 13: this camera is day-first
    clock = understand(lines).clock
    assert clock is not None and clock.local_start == datetime(2026, 4, 3, 7, 45, 13)


def test_the_clock_crossing_midnight_settles_the_order():
    # 03/04 then 03/05 a day later: March 4 to 5 is one day, 3 April to 3 May is a month
    lines = [line("03/04/2026 23:00:00", 0.0), line("03/05/2026 23:00:00", 86400.0)]
    assert understand(lines).clock.local_start == datetime(2026, 3, 4, 23, 0, 0)


@pytest.mark.parametrize("text", ["", "   ", "10:15:13", "2026-13-45 10:15:13", "99/99/2026 25:61:61",
                                  "13:15:13 PM 2026-09-09", "1999-01-01 10:15:13", "€€€ ✈︎ 12.5.3", "a" * 5000])
def test_odd_text_never_raises(text):
    reading = understand([line(text, 9000.0), line(text, 30000.0)], STARTED, date(2026, 9, 10))
    assert reading.clock is None and reading.lines


# ---- the time zone ----

def test_the_zone_from_the_measured_numbers():
    reading = understand(scout_lines(), STARTED)
    assert reading.zone.name == "Eastern" and reading.zone.utc_offset_h == -4.0
    assert reading.clock_gap_s == -19
    lat0, lat1, lon0, lon1 = reading.zone.bbox
    assert lat0 <= 41.395 <= lat1 and lon0 <= -82.555 <= lon1


def test_a_winter_date_is_on_standard_time():
    started = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    lines = [line("2026-01-15 07:00:05", 0.0), line("2026-01-15 08:00:05", 3600.0)]
    reading = understand(lines, started)
    assert reading.zone.name == "Eastern" and reading.zone.utc_offset_h == -5.0 and reading.clock_gap_s == 5


def test_two_zones_on_one_clock_share_a_name_and_a_box():
    # in summer Arizona keeps standard time, so it reads the same as California
    started = datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc)
    lines = [line("2026-07-01 13:00:00", 0.0), line("2026-07-01 14:00:00", 3600.0)]
    zone = understand(lines, started).zone
    assert zone.name == "Arizona or Pacific" and zone.utc_offset_h == -7.0
    assert zone.bbox[2] <= -124 and zone.bbox[3] >= -109.5           # California's coast to Arizona's east edge


def test_an_offset_no_us_zone_uses_is_no_zone():
    started = datetime(2026, 9, 9, 11, 45, 32, tzinfo=timezone.utc)
    lines = [line("2026-09-09 21:15:13", 9000.0), line("2026-09-10 03:05:13", 30000.0)]     # UTC+7
    reading = understand(lines, started)
    assert reading.clock is not None and reading.zone is None and reading.clock_gap_s is None


def test_a_clock_far_from_a_half_hour_step_is_no_zone():
    lines = [line("2026-09-09 10:27:13", 9000.0), line("2026-09-09 16:17:13", 30000.0)]     # 12 min off
    assert understand(lines, STARTED).zone is None


# ---- hints ----

def test_the_scout_pictures_give_a_town_and_a_day():
    hints = [(h.kind, h.value, h.strength) for h in understand(scout_lines(), STARTED, date(2026, 9, 10)).hints]
    assert hints == [("place", "Huron, Ohio", "likely"), ("day", "2026-09-09", "likely")]


def test_the_reasons_are_ones_the_person_can_check():
    by_kind = {h.kind: h for h in understand(scout_lines()).hints}
    assert by_kind["place"].because == f'a caption on the picture says "{CAPTION}"'
    assert by_kind["place"].towns == ("Huron",)
    assert by_kind["day"].because == 'the clock in the picture read "2026-09-09 10.15:13 Wed" at 2:30:00 into the video'
    assert by_kind["clock"].value == "2026-09-09T07:45:13" and by_kind["clock"].strength == "likely"


def test_with_the_stream_start_there_is_no_clock_question():
    assert "clock" not in {h.kind for h in understand(scout_lines(), STARTED).hints}


def test_a_day_after_the_upload_is_a_wrong_clock():
    hints = understand(scout_lines(), STARTED, uploaded=date(2026, 9, 1)).hints
    assert "day" not in {h.kind for h in hints}


def test_a_line_seen_once_is_a_sign():
    hints = understand([line("Huron OHIO", 9000.0, y=0.5)]).hints
    assert [(h.value, h.strength, h.because) for h in hints] == [
        ("Huron, Ohio", "weak", 'a sign in the picture says "Huron OHIO"')]


def test_the_same_line_mid_picture_is_still_a_sign():
    hints = understand([line("Huron OHIO", 9000.0, y=0.5), line("Huron OHIO", 30000.0, y=0.5)]).hints
    assert [h.strength for h in hints] == ["weak"]


def test_a_line_that_moves_is_not_a_caption():
    hints = understand([line("Huron OHIO", 9000.0, y=0.9), line("Huron OHIO", 30000.0, x=0.2, y=0.9)]).hints
    assert [h.strength for h in hints] == ["weak"]


def test_a_caption_at_the_top_counts_too():
    hints = understand([line("Sandusky, OH", 9000.0, y=0.05), line("Sandusky, OH", 30000.0, y=0.06)]).hints
    assert [(h.value, h.strength) for h in hints] == [("Sandusky, Ohio", "likely")]


@pytest.mark.parametrize("text, state, code", [
    ("CALL 419-555-0134", "Ohio", "419"),
    ("Rentals (212) 555 0100", "New York", "212"),
    ("+1 907.555.0199", "Alaska", "907"),
])
def test_a_phone_number_gives_its_state(text, state, code):
    hints = understand([line(text, 9000.0, y=0.5)]).hints
    assert [(h.value, h.strength) for h in hints] == [(state, "weak")]
    assert hints[0].because == f"a phone number in the picture starts with {code}, an area code in {state}"


@pytest.mark.parametrize("text", ["CALL 800-555-0134", "1-888-555-0100", "2026-09-09 10:15:13", "ID 4195550134"])
def test_toll_free_and_other_numbers_give_nothing(text):
    assert understand([line(text, 9000.0, y=0.5)]).hints == []


def test_a_phone_adds_nothing_next_to_a_town_in_its_state():
    lines = scout_lines() + [line("CALL 419-555-0134", 9000.0, y=0.5)]
    assert [h.value for h in understand(lines, STARTED).hints if h.kind == "place"] == ["Huron, Ohio"]


def test_the_area_code_table():
    assert AREA_CODES["419"] == "Ohio" and AREA_CODES["202"] == "District of Columbia"
    assert AREA_CODES["808"] == "Hawaii" and AREA_CODES["907"] == "Alaska"
    assert not {"800", "833", "844", "855", "866", "877", "888"} & AREA_CODES.keys()
    assert len(set(AREA_CODES.values())) == 51


# ---- reading ----

def stub_helper(bin_dir, script):
    tag = hashlib.sha256(readtext.SWIFT_SOURCE.encode()).hexdigest()[:10]
    helper = bin_dir / f"flyby-ocr-{tag}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    helper.write_text(f"#!{sys.executable}\n{script}")
    helper.chmod(0o755)
    return helper


def test_read_with_a_stub_helper(tmp_path):
    stub_helper(tmp_path / "bin", (
        "import json, sys\n"
        "for p in sys.argv[1:]:\n"
        "    print(json.dumps({'file': p, 'lines': [{'text': 'seen in ' + p.rsplit('/', 1)[-1], 'conf': 0.9,"
        " 'x': 0.1, 'y': 0.2, 'w': 0.3, 'h': 0.04}]}))\n"))
    frames = [Frame(tmp_path / "a.jpg", 9000.0), Frame(tmp_path / "thumb-1.jpg", None)]
    got = read(frames, tmp_path / "bin")
    assert [(ln.text, ln.t_s, ln.frame, ln.conf, ln.y) for ln in got] == [
        ("seen in a.jpg", 9000.0, tmp_path / "a.jpg", 0.9, 0.2),
        ("seen in thumb-1.jpg", None, tmp_path / "thumb-1.jpg", 0.9, 0.2)]


def test_read_nothing_runs_nothing(tmp_path):
    assert read([], tmp_path / "bin") == [] and not (tmp_path / "bin").exists()


def test_a_reader_that_fails_says_so(tmp_path):
    stub_helper(tmp_path / "bin", "import sys\nsys.exit(3)\n")
    with pytest.raises(FlybyError, match="text reader"):
        read([Frame(tmp_path / "a.jpg", 0.0)], tmp_path / "bin")


def test_no_swift_tools_says_how_to_get_them(tmp_path, monkeypatch):
    monkeypatch.setattr(readtext.shutil, "which", lambda name: None)
    with pytest.raises(FlybyError, match="xcode-select --install"):
        read([Frame(tmp_path / "a.jpg", 0.0)], tmp_path / "bin")


def test_a_failed_build_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(readtext, "SWIFT_SOURCE", "this is not swift")
    with pytest.raises(FlybyError, match="could not be built"):
        read([Frame(tmp_path / "a.jpg", 0.0)], tmp_path / "bin")


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="needs Apple's Swift tools")
def test_the_real_reader_reads_a_made_picture(tmp_path):
    rng = np.random.default_rng(7)
    # dark, uneven and noisy like a night street, so the reader cannot lean on a clean page
    base = np.linspace(20, 70, 1280)[None, :, None] + rng.normal(0, 12, (720, 1280, 3))
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 44)
    draw.text((760, 20), "2026-09-09 10:15:13", fill="white", font=font)
    draw.text((20, 650), CAPTION, fill="white", font=font)
    path = tmp_path / "made.jpg"
    img.save(path, quality=90)
    got = read([Frame(path, 9000.0)], tmp_path / "bin")
    texts = {ln.text: ln for ln in got}
    assert "2026-09-09 10:15:13" in texts and CAPTION in texts
    clock_line = texts["2026-09-09 10:15:13"]
    assert clock_line.x > 0.5 and clock_line.y < 0.1 and clock_line.t_s == 9000.0
    assert texts[CAPTION].y > 0.85
    # built once, then reused
    assert len(list((tmp_path / "bin").glob("flyby-ocr-*"))) == 2       # the helper and its source
    assert read([Frame(path, 9000.0)], tmp_path / "bin")


# ---- found by review: clues stronger than their evidence ----

def test_a_camera_that_skips_the_clock_change_keeps_both_zones_open():
    # a Pacific camera left on winter time shows UTC-8 in July, which is what Alaska's clocks show then
    started = datetime(2026, 7, 10, 20, 0, 0, tzinfo=timezone.utc)
    zone = understand([line("2026-07-10 12:00:00", 0.0), line("2026-07-10 13:00:00", 3600.0)], started).zone
    assert (zone.name, zone.also) == ("Alaska", "Pacific")
    assert zone.bbox[0] <= 34.0 and zone.bbox[3] >= -118.0          # Los Angeles is inside the box
    # Eastern summer time has no such double: no US zone shows UTC-4 in winter
    assert understand(scout_lines(), STARTED).zone.also == ""
    # in winter the double is a camera left on summer time
    winter = understand([line("2026-01-15 07:00:05", 0.0), line("2026-01-15 08:00:05", 3600.0)],
                        datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)).zone
    assert (winter.name, winter.also) == ("Eastern", "Central")
    # and an offset that fits only a camera that skipped the change still names its zone
    alaska = understand([line("2026-07-10 11:00:00", 0.0), line("2026-07-10 12:00:00", 3600.0)], started).zone
    assert (alaska.name, alaska.also) == ("", "Alaska")


def test_a_clock_with_no_am_or_pm_is_settled_by_the_streams_own_clock():
    # the camera showed 3:00:00 in the afternoon: 15:00 Eastern summer time is 19:00 UTC
    started = datetime(2026, 9, 9, 19, 0, 0, tzinfo=timezone.utc)
    reading = understand([line("9/9/2026 3:00:00", 0.0), line("9/9/2026 3:10:00", 600.0)], started)
    assert reading.zone.name == "Eastern" and reading.clock_gap_s == 0
    assert reading.clock.local_start == datetime(2026, 9, 9, 15, 0, 0) and not reading.clock.half_day
    assert [h.value for h in reading.hints if h.kind == "day"] == ["2026-09-09"]


def test_a_clock_with_no_am_or_pm_and_no_stream_clock_is_only_a_weak_clue():
    def clock_hints(first, second):
        return [(h.value, h.strength) for h in understand([line(first, 0.0), line(second, 600.0)]).hints if h.kind == "clock"]

    assert clock_hints("9/9/2026 3:00:00", "9/9/2026 3:10:00") == [("2026-09-09T03:00:00", "weak")]
    assert clock_hints("9/9/2026 10:00:00", "9/9/2026 10:10:00") == [("2026-09-09T10:00:00", "weak")]
    # a clock that writes the 0, shows an hour past 12, or says AM or PM leaves no doubt
    assert clock_hints("9/9/2026 03:00:00", "9/9/2026 03:10:00") == [("2026-09-09T03:00:00", "likely")]
    assert clock_hints("9/9/2026 12:55:00", "9/9/2026 13:05:00") == [("2026-09-09T12:55:00", "likely")]
    assert clock_hints("9/9/2026 3:00:00 PM", "9/9/2026 3:10:00 PM") == [("2026-09-09T15:00:00", "likely")]


def test_a_state_name_alone_in_a_caption_is_only_a_guess():
    def places(text, conf=1.0):
        lines = [TextLine(text, conf, 0.1, 0.9, 0.3, 0.04, t, Path(f"/f/{t}.jpg")) for t in (0.0, 10.0)]
        return [(h.value, h.strength) for h in understand(lines).hints if h.kind == "place"]

    # a power company, a score: a state's name is in all of them
    assert places("ALABAMA POWER") == [("Alabama", "weak")]
    assert places("Alabama 24 Georgia 21") == [("Alabama", "weak")]
    assert places("Washington 31, Oregon 28") == [("Oregon", "weak")]
    # a town with its state, or a position, is something the map can check
    assert places(CAPTION) == [("Huron, Ohio", "likely")]
    assert places("41.3918, -82.5577") == [("41.39180,-82.55770", "likely")]
    # a line the reader itself doubts names nothing
    assert places(CAPTION, conf=0.3) == []
    assert all(h.from_picture for h in understand(scout_lines()).hints if h.kind == "place")


def test_control_characters_never_come_out_of_the_reader(tmp_path, monkeypatch):
    reply = json.dumps({"file": "/f/a.jpg", "lines": [{"text": "Huron \u001b[2JOHIO\u0007", "conf": 1.0, "x": 0, "y": 0, "w": 1, "h": 1}]})
    monkeypatch.setattr(readtext, "_helper", lambda bin_dir: tmp_path / "helper")
    monkeypatch.setattr(readtext.subprocess, "run", lambda *a, **kw: readtext.subprocess.CompletedProcess(a, 0, reply + "\n", ""))
    got = read([Frame(Path("/f/a.jpg"), 1.0)], tmp_path)
    assert [ln.text for ln in got] == ["Huron [2JOHIO"]
