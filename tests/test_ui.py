"""The terminal look: the title, the live NOW / WHY / NEXT block, and the rules its words are held to."""
import ast
import io
import re
import string
import textwrap
from pathlib import Path

import pytest
from rich.console import Console

from flyby import cli, ui
from flyby.captions import SAID, STEPS, Caption

SRC = Path(ui.__file__).parent
SOURCES = sorted(p for p in SRC.glob("*.py") if p.name != "ui.py")
NOW_MAX = NEXT_MAX = ui.BODY
WHY_MAX, WHY_WORDS, WHY_LINES = 2 * ui.BODY, 12, 2
LONGEST_WORD = 11
# words a person who has never built software would have to look up
JARGON = ("ads-b", "adsb", "frame", "spectrogram", "doppler", "index", "bbox", "metadata", "geocod", "thumbnail",
          "storyboard", "parse", "algorithm", "latitude", "longitude", "coordinate", "timestamp", "fetch", "cache",
          "query", "render", "decode", "detect")
SHORT_JARGON = ("ocr", "utc", "api", "cpa", "gps", "url")      # whole words only: "capital" is not jargon
COLOUR = re.compile(r"\x1b\[(?:[0-9;]*;)?(?:3[0-7]|38|4[0-7]|48|9[0-7]|10[0-7])(?:;[0-9;]*)?m")


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text.strip())


def words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'-]*", text.lower())


def terminal(width=100, **kw) -> Console:
    return Console(file=io.StringIO(), width=width, height=40, force_terminal=True, color_system="truecolor",
                   _environ={}, **kw)


def typed(console: Console) -> str:
    return console.file.getvalue()


def lines(live: ui.Live) -> list[str]:
    """The live block as plain text, one string per row."""
    out = io.StringIO()
    Console(file=out, width=live.console.width, color_system=None).print(live.block())
    return [row.rstrip() for row in out.getvalue().splitlines()]


def calls(name: str):
    """Every `ui.<name>(...)` call in flyby's own code."""
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "ui":
                yield path.name, node


# ---- the words

@pytest.mark.parametrize("key", STEPS)
def test_every_caption_keeps_the_writing_rules(key):
    c = STEPS[key]
    assert len(c.now) <= NOW_MAX, f"NOW is {len(c.now)} characters"
    assert len(c.then) <= NEXT_MAX, f"NEXT is {len(c.then)} characters"
    assert len(c.why) <= WHY_MAX, f"WHY is {len(c.why)} characters"
    for sentence in sentences(c.why):
        assert len(sentence.split()) <= WHY_WORDS, f"{len(sentence.split())} words: {sentence}"
    # 88 characters do not always break into two lines of 44
    assert len(textwrap.wrap(c.why, ui.BODY)) <= WHY_LINES, textwrap.wrap(c.why, ui.BODY)
    assert c.why.endswith(".") and not c.now.endswith(".") and not c.then.endswith(".")
    assert not fields(c.why) and not fields(c.then)        # their length is only known when nothing is filled in


@pytest.mark.parametrize("key", STEPS)
def test_every_caption_is_in_plain_words(key):
    c = STEPS[key]
    for word in words(" ".join((c.now, c.why, c.then)).replace("{", " ").replace("}", " ")):
        assert not word.startswith(JARGON) and word not in SHORT_JARGON, f'"{word}" is jargon'
        assert len(word) <= LONGEST_WORD, f'"{word}" is a long word'


def test_the_steps_the_picture_tools_will_call_have_captions():
    assert {"frames", "light", "read", "sun", "guess", "pictures_page", "daylight"} <= set(STEPS)


def test_every_step_in_the_code_has_a_caption_and_fills_its_fields():
    seen = set()
    for where, call in calls("step"):
        if not isinstance(call.args[0], ast.Constant):
            continue            # the command's teller: its keys come from SAID, which has a test of its own
        key = call.args[0].value
        seen.add(key)
        assert key in STEPS, f"{where}: no caption for {key!r}"
        given = {kw.arg for kw in call.keywords} - {"then"}
        assert given == fields(STEPS[key].now), f"{where}: {key} is given {sorted(given)}"
    assert {"towns", "practice", "place", "spectrogram", "listen", "passes", "tracks", "cut_day", "search",
            "pin", "others"} <= seen


def test_what_a_caller_says_comes_next_keeps_the_rules_too():
    said = [node.value for path in SOURCES for call in ast.walk(ast.parse(path.read_text())) if isinstance(call, ast.Call)
            for kw in call.keywords if kw.arg == "then"
            for node in ast.walk(kw.value) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert len(said) >= 4
    for then in said:
        assert len(then) <= NEXT_MAX and not then.endswith("."), then


def test_no_step_is_started_with_words_written_at_the_call():
    # the words live in captions.py; only a sentence handed on from another module may be shown as it comes
    for where, call in calls("start"):
        assert isinstance(call.args[0], ast.Name), f"{where}: ui.start with its own words, line {call.lineno}"


def test_every_sentence_ingest_reports_has_a_caption():
    told = re.findall(r'\blog\("([^"]+)"\)', (SRC / "ingest.py").read_text())
    assert len(told) >= 4
    for sentence in told:
        assert SAID[sentence.rstrip(".")] in STEPS
    assert set(SAID.values()) <= set(STEPS)


# ---- a pipe, a log file, the tests

def test_a_pipe_gets_one_line_per_step():
    console = Console(file=io.StringIO(), width=100)
    plain = ui.make_ui(console)
    assert type(plain) is ui.Plain
    plain.section("Search")
    plain.step("tracks", day="2026-09-08")
    plain.progress(1, 2, "half way")
    plain.done("Flight tracks for 2026-09-08 ready")
    plain.start("Something with no caption")
    plain.note("A note")
    plain.close()
    assert [row.rstrip() for row in typed(console).splitlines()] == [
        "", "  ▍Search",
        "  · Streaming the flight tracks for 2026-09-08...",
        "  ✓ Flight tracks for 2026-09-08 ready",
        "  · Something with no caption...",
        "    A note"]


def test_a_pipe_never_gets_the_big_title():
    console = Console(file=io.StringIO(), width=120)
    ui.header(console)
    assert typed(console) == f"\n  flyby  find where a video was filmed\n  {'─' * 46}\n"


# ---- the title

def test_a_wide_terminal_gets_flyby_in_block_letters():
    console = terminal(60)
    ui.header(console)
    shown = Console(file=io.StringIO(), width=60, color_system=None)
    shown.print(ui.Text.from_ansi(typed(console)))
    rows = typed(shown).splitlines()
    assert [row[2:].rstrip() for row in rows[1:5]] == [row.rstrip() for row in ui.TITLE]
    assert rows[5] == "  find where a video was filmed" and set(rows[6].strip()) == {"─"}


def test_a_narrow_terminal_keeps_the_one_line_header():
    console = terminal(59)
    ui.header(console)
    assert "█" not in typed(console) and "flyby" in typed(console) and "find where a video was filmed" in typed(console)


def test_the_block_letters_are_one_even_shape():
    assert len({len(row) for row in ui.TITLE}) == 1 and len(ui.TITLE[0]) + len(ui.MARGIN) <= ui.TITLE_COLS
    assert set("".join(ui.TITLE)) <= set("█▀▄ ")
    # five letters: four gaps that are empty in every row
    empty = [all(row[x] == " " for row in ui.TITLE) for x in range(len(ui.TITLE[0]))]
    assert len(re.findall("1+", "".join("01"[e] for e in empty).strip("1"))) == 4


# ---- the live block

def test_a_step_shows_what_why_and_what_next():
    live = ui.make_ui(terminal(100))
    assert type(live) is ui.Live
    live.step("listen")
    live.progress(48, 100)
    rows = lines(live)
    live.close()
    assert rows[0] == "  NOW   Listening for aircraft"
    assert re.fullmatch(r"        \S  ━+╸?\s+48%  0:00", rows[1]), rows[1]
    assert rows[2:] == ["  WHY   Each aircraft leaves a falling tone. flyby",
                        "        needs two.",
                        "  NEXT  Measure each pass"]


def test_the_engine_s_progress_words_get_a_line_of_their_own():
    live = ui.Live(terminal(100))
    live.step("search", area="Ohio", what="2026-09-08, passes at 1:23 and 4:56")
    live.progress(1, 3, "scoring spots against the audio")
    rows = lines(live)
    live.close()
    assert rows[0] == "  NOW   Searching Ohio: 2026-09-08, passes at 1:23"       # a long line wraps inside the column
    assert rows[1] == "        and 4:56"
    assert " 33%" in rows[2] and rows[3] == "        scoring spots against the audio"
    assert rows[4].startswith("  WHY   ") and rows[-1].startswith("  NEXT  ")


def test_a_step_with_no_count_yet_shows_no_percent():
    live = ui.Live(terminal(100))
    live.step("pin")
    rows = lines(live)
    live.close()
    assert "%" not in rows[1] and rows[1].endswith("0:00")


def test_the_percent_is_never_ahead_of_the_work():
    live = ui.Live(terminal(100))
    live.step("listen")
    live.progress(999, 1000)
    assert " 99%" in lines(live)[1]
    live.progress(7, 5)
    assert "100%" in lines(live)[1]
    live.close()


def test_a_caller_can_say_what_comes_next():
    live = ui.Live(terminal(100))
    live.step("towns", then="Ask you what you know about the video")
    rows = lines(live)
    live.close()
    assert rows[-1] == "  NEXT  Ask you what you know about the video"


def test_start_shows_now_alone():
    live = ui.Live(terminal(100))
    live.start("Doing a thing with no caption")
    rows = lines(live)
    live.close()
    assert rows[0] == "  NOW   Doing a thing with no caption" and len(rows) == 2
    assert not any("WHY" in row or "NEXT" in row for row in rows)


@pytest.mark.parametrize("width", [40, 60, 70, 100, 200])
@pytest.mark.parametrize("key", STEPS)
def test_the_block_fits_the_terminal_and_keeps_short_lines(key, width):
    live = ui.Live(terminal(width))
    c = STEPS[key]
    live.step(key, **{name: "2026-09-08" for name in fields(c.now)})
    live.progress(5, 9, "waiting for another flyby search to finish")
    rows = lines(live)
    live.close()
    assert max(map(len, rows)) <= min(width - 1, len(ui.MARGIN) + 6 + ui.BODY)
    assert [row[:8].strip() for row in rows if row[:8].strip()] == ["NOW", "WHY", "NEXT"]
    if width >= ui.TITLE_COLS:
        assert len(rows) <= 7       # NOW, the bar, the engine's words, two of WHY, NEXT; one more if NOW wraps


@pytest.mark.parametrize("width", [34, 50, 60, 100])
def test_the_block_never_fills_a_line_to_the_right_edge(width):
    # seen in a real terminal: Ctrl-C types "^C" after the block; on a full line that wraps to the next row,
    # the block is then rubbed out one row short, and its NOW line is left on the screen
    live = ui.Live(terminal(width))
    live.step("search", area="within 40 km of Huron, Ohio", what="2026-09-09, passes at 12:41 and 15:07")
    live.progress(1, 2, "waiting for another flyby search to finish")
    out = io.StringIO()
    Console(file=out, width=width, color_system=None).print(live.block())
    live.close()
    assert max(map(len, out.getvalue().splitlines())) < width


def test_the_block_is_gone_when_the_step_ends_and_one_line_stays():
    console = terminal(100)
    live = ui.Live(console)
    live.step("listen")
    live.area.refresh()
    assert "NOW" in typed(console) and live.area.is_started
    area = live.area
    live.done("3 flyovers measured")
    assert live.area is None and not area.is_started
    # a block that goes away is rubbed out line by line, then the line that stays is written
    after = typed(console).rsplit("\x1b[2K", 1)[1]
    assert "✓" in after and "3 flyovers measured" in after and "NOW" not in after


def test_a_long_step_says_how_long_it_took():
    console = terminal(100)
    live = ui.Live(console)
    live.step("search", area="Ohio", what="2026-09-08")
    live.began -= 75
    live.done("strong match")
    live.step("pin")
    live.done("Spot pinned down")
    out = typed(console)
    assert "(1 min 15 s)" in out and out.count(" s)") == 1         # the quick step is not timed


def test_the_block_is_closed_before_a_heading_and_on_close():
    # the questions print between steps with the same console: nothing may be left spinning over them
    for end in (lambda live: live.section("Questions"), lambda live: live.close()):
        live = ui.Live(terminal(100))
        live.step("towns")
        area = live.area
        end(live)
        assert live.area is None and not area.is_started
        live.close()            # Ctrl-C and the finally both close: a second close is fine
        live.progress(1, 2)     # and a late progress call from the engine is dropped
        assert live.area is None


def test_a_note_in_the_middle_of_a_step_leaves_the_step_running():
    # the memory warning comes in the middle of a search that still has many minutes to run
    console = terminal(100)
    live = ui.Live(console)
    live.step("search", area="Ohio", what="2026-09-08")
    live.note("Memory is tight")
    assert live.area is not None and live.area.is_started and "Memory is tight" in typed(console)
    live.progress(1, 2, "pairing up flights")
    assert " 50%" in lines(live)[1]
    live.close()


def test_no_color_is_kept_and_the_block_still_reads():
    console = Console(file=io.StringIO(), width=100, height=40, force_terminal=True, color_system="truecolor",
                      _environ={"NO_COLOR": "1"})
    ui.header(console)
    live = ui.Live(console)
    live.step("listen")
    live.progress(1, 2)
    live.area.refresh()
    live.done("Done")
    out = typed(console)
    assert not COLOUR.search(out)
    assert all(word in out for word in ("NOW", "WHY", "NEXT", "50%", "✓"))
    coloured = terminal(100)
    ui.header(coloured)
    assert COLOUR.search(typed(coloured))       # the check above can fail


# ---- the sentences ingest hands to the command

class Told:
    def __init__(self):
        self.calls = []

    def step(self, key, then=None, **words):
        self.calls.append(("step", key, then))

    def start(self, text):
        self.calls.append(("start", text))


def test_ingest_s_sentences_become_steps_from_the_table():
    told = Told()
    log = cli._teller(told)
    log("Asking the site about this video...")
    log("Downloading the audio (this can take a while for a long video)...")
    log("Some new thing ingest does...")
    assert told.calls == [("step", "site", None), ("step", "download", None), ("start", "Some new thing ingest does")]


def test_a_download_that_is_cut_afterwards_says_so():
    told = Told()
    log = cli._teller(told, cut_next=True)
    log("Downloading the audio (this can take a while for a long video)...")
    log("Cutting that part out of the audio...")
    assert told.calls == [("step", "download", "Cut out the part you chose"), ("step", "cut", None)]


def test_a_caption_is_three_strings():
    assert Caption("a", "b", "c") == Caption(now="a", why="b", then="c")
