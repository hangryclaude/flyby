"""The questions flyby asks before it searches. Every answer narrows the search. Enter alone always works."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, TextIO
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

from .clues import MAX_RANGE_DAYS
from .model import Answers, Area, FlybyError, Hint, Media
from .ui import ACCENT, MARGIN, indent

LONG_VIDEO_S = 3 * 3600
EMPTY_TRIES = 5        # a closed input must not make ask_source loop for ever

# the clocks people know their time on; zoneinfo then gets daylight saving right for the day
CLOCKS = [("Eastern", "America/New_York"), ("Central", "America/Chicago"), ("Mountain", "America/Denver"),
          ("Pacific", "America/Los_Angeles"), ("Alaska", "America/Anchorage"), ("Hawaii", "Pacific/Honolulu"),
          ("UTC", "UTC")]
SURENESS = [("to the minute", 120.0), ("within about 10 minutes", 600.0), ("within about an hour", 3600.0)]
EFFORTS = [("quick", "one search"), ("normal", "up to 6 searches"), ("thorough", "up to 12 searches")]

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_SKIP = {"?", "-", "skip", "idk", "unknown", "none", "not sure", "dont know", "don't know", "do not know"}
_WHOLE_USA = {"usa", "us", "u.s.", "u.s.a.", "united states", "america", "anywhere", "everywhere", "whole usa"}
_RANK = {"stated": 0, "likely": 1, "weak": 2}
_LATLON = re.compile(r"\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*")
_BOX = "\x00box"       # stands for "the area on offer" in the place question; nobody can type it


@dataclass
class Extras:
    """What the picture tools hand the questions. Any part may be missing."""
    area: Area | None = None                              # a part of the map the pictures point to
    open_pictures: Callable[[], None] | None = None       # opens the picture search page in the browser
    ask_ai: Callable[[], list[Hint]] | None = None        # lets an AI look at the pictures; gives back its guesses


def _today() -> date:
    return date.today()


# ---------------------------------------------------------------- reading what the person typed

def clean_source(text: str) -> str:
    """A file dragged into a terminal arrives in quotes, or with a backslash before each space."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1].strip()
    if text.startswith("file://"):
        return unquote(urlparse(text).path)
    if text.startswith(("http://", "https://")):
        return text
    return re.sub(r"\\(.)", r"\1", text)


def _month_number(word: str) -> int | None:
    word = word.strip(".")
    if word == "sept":
        return 9
    for i, name in enumerate(_MONTHS, 1):
        if word in (name, name[:3]):
            return i
    return None


def _one_day(text: str, today: date) -> date:
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.lower()).replace(",", " ")
    t = " ".join(w for w in t.split() if w not in ("of", "the"))
    if t == "today":
        found = today
    elif t == "yesterday":
        found = today - timedelta(days=1)
    else:
        year: int | None
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
            year, month, day = int(m[1]), int(m[2]), int(m[3])
        elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?", t):   # US order: month first
            month, day = int(m[1]), int(m[2])
            year = None if m[3] is None else int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        else:
            year, month, day = _words_day(t)
        found = _make_day(year, month, day, today)
    if found > today:
        raise ValueError("That day has not come yet.")
    return found


def _words_day(t: str) -> tuple[int | None, int, int]:
    words = t.replace(".", " ").split()
    months = [m for w in words if w.isalpha() and (m := _month_number(w))]
    numbers = [int(w) for w in words if w.isdigit()]
    if len(months) != 1 or len(words) != 1 + len(numbers) or not 1 <= len(numbers) <= 2:
        raise ValueError("I could not read that day.")
    years = [n for n in numbers if n >= 1000]
    if len(numbers) == 2 and len(years) != 1:
        raise ValueError("I could not read that day.")
    day = next((n for n in numbers if n not in years), None)
    if day is None:         # "october 2026": a month and a year, no day
        raise ValueError("I could not read that day.")
    return (years[0] if years else None), months[0], day


def _make_day(year: int | None, month: int, day: int, today: date) -> date:
    try:
        if year is not None:
            return date(year, month, day)
        found = date(today.year, month, day)
        # no year typed: the latest such day that has already come
        return found if found <= today else date(today.year - 1, month, day)
    except ValueError:
        raise ValueError("That day is not on the calendar.") from None


def parse_day(text: str, today: date | None = None) -> str:
    """One day or a range 'A..B' in the --date grammar. Raises ValueError with a plain sentence."""
    today = today or _today()
    parts = re.split(r"\s*(?:\.\.|\bto\b)\s*", text.strip(), maxsplit=1, flags=re.I)
    days = sorted(_one_day(p, today) for p in parts)
    if days[0] == days[-1]:
        return days[0].isoformat()
    return f"{days[0].isoformat()}..{days[-1].isoformat()}"


def parse_time(text: str) -> tuple[int, int, bool]:
    """(hour, minute, said_utc) from '20:17', '8:17 pm' or '8pm'. A bare '8' is too vague to take."""
    t = text.lower().replace(".", "").strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)?\s*(utc|gmt|z)?", t)
    if not m or (m[2] is None and m[3] is None):
        raise ValueError("I could not read that time.")
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3]:
        if not 1 <= hour <= 12:
            raise ValueError("With am or pm the hour is 1 to 12.")
        hour = hour % 12 + (12 if m[3] == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ValueError("That time is not on the clock.")
    return hour, minute, m[4] is not None


def parse_section(text: str, duration_s: float) -> tuple[float, float] | str:
    """A part of a long video: '1' is all of it, '2' the first 2 hours, or hours and minutes like 2:00-4:00."""
    t = text.lower().strip()
    if t == "1":
        return "all"
    if t == "2":
        return (0.0, 7200.0)
    m = re.fullmatch(r"(\d+)(?::(\d{2}))?\s*(?:-|–|to)\s*(\d+)(?::(\d{2}))?", t)
    if not m:
        raise ValueError("I could not read that part.")
    start = int(m[1]) * 3600 + int(m[2] or 0) * 60
    end = int(m[3]) * 3600 + int(m[4] or 0) * 60
    if start >= duration_s:
        raise ValueError(f"The video is only {_length(duration_s)} long.")
    if end <= start:
        raise ValueError("The end must come after the start.")
    return (float(start), float(min(end, duration_s)))


def _parse_yes_no(text: str) -> bool:
    t = text.lower().strip()
    if t in ("y", "yes", "yeah", "yep", "true"):
        return True
    if t in ("n", "no", "nope", "false"):
        return False
    raise ValueError("Please answer yes or no.")


# ---------------------------------------------------------------- showing things

def _length(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _hm(seconds: float) -> str:
    return f"{int(seconds // 3600)}:{int(seconds % 3600 // 60):02d}"


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _best(hints: list[Hint], kind: str) -> Hint | None:
    return min((h for h in hints if h.kind == kind), key=lambda h: _RANK.get(h.strength, 3), default=None)


class _Talk:
    """All the printing and asking, so the questions below read as questions."""

    def __init__(self, console: Console, stream: TextIO | None):
        self.console = console
        self.stream = stream

    def say(self, text: str, style: str | None = None) -> None:
        self.console.print(indent(escape(text)), style=style, highlight=False)

    def good(self, text: str) -> None:
        self.say(f"Good. {text}", "green")

    def fact(self, label: str, value: str, source: str) -> None:
        self.console.print(indent(f"[dim]{escape(label)}:[/] [bold]{escape(value)}[/]  [dim]({escape(source)})[/]"),
                           highlight=False)

    def gap(self) -> None:
        self.console.print()

    def raw(self, question: str, default: str = "") -> str:
        """The typed line. Enter alone gives the default, or an empty line when there is none."""
        typed = Prompt.ask(f"{MARGIN}[{ACCENT}]›[/] [bold]{escape(question)}[/]", console=self.console, stream=self.stream,
                           default=default, show_default=bool(default)).strip()
        # rich only applies the default at end of input, not to a typed empty line, when it reads from a stream
        return typed or default

    def ask(self, question: str, parse: Callable[[str], Any], form: str, default: str = "") -> Any:
        """The parsed answer, or None for "I do not know". One retry after a miss, then it counts as unknown."""
        for attempt in range(2):
            text = self.raw(question, default)
            if not text or text.lower() in _SKIP:
                return None
            try:
                return parse(text)
            except ValueError as miss:
                self.say(str(miss))
                if attempt == 0:
                    self.say(f"Try a form like this: {form}")
        self.say("I could not read that. I will take it as: I do not know.")
        return None

    def choose(self, question: str, options: list[str], default: int | None = None) -> int | None:
        """A numbered list. Returns the number picked (from 1), or None."""
        for i, option in enumerate(options, 1):
            self.console.print(indent(f"[{ACCENT}]{i}[/]  {escape(option)}", 4), highlight=False)

        def number(text: str) -> int:
            if text.isdigit() and 1 <= int(text) <= len(options):
                return int(text)
            raise ValueError(f"Pick a number from 1 to {len(options)}.")

        return self.ask(question, number, "2", str(default) if default else "")

    def yes_no(self, question: str, default: bool = True) -> bool | None:
        return self.ask(question + " [yes or no]", _parse_yes_no, "yes", "yes" if default else "no")


# ---------------------------------------------------------------- the questions

def _from_pictures(t: _Talk, x: Extras) -> Hint | None:
    """Nothing the video says names a place, so offer what the pictures can do.
    Returns the AI's best guess, when the person asked for one."""
    if x.open_pictures:
        t.say("Nothing the video says names a place.")
        t.say("A picture search can find where a picture was taken.")
        if t.yes_no("Open the picture search page?", default=False):
            x.open_pictures()
            t.say("It is open in your browser. Look there, then come back here.")
        t.gap()
    if x.ask_ai:
        t.say("An AI can look at 3 pictures and guess the place.")
        t.say("The pictures go to Claude, on your own plan.")
        if t.yes_no("Let an AI look?", default=False):
            guesses = x.ask_ai()
            if not guesses:
                t.say("The AI could not tell.")
            t.gap()
            return _best(guesses, "place")
        t.gap()
    return None


def _where(t: _Talk, media: Media, hints: list[Hint], a: Answers, x: Extras) -> None:
    if a.area or a.bbox:
        t.fact("Place", a.area or "the box you gave", "you gave this")
        return
    value = label = ""
    hint = None if media.location_hint else _best(hints, "place") or _from_pictures(t, x)
    if media.location_hint:
        value = "{:.4f},{:.4f}".format(*media.location_hint)
        label = "near the position the file carries"
        t.say("The file carries a position.")
        t.say("Press Enter to search there. Type skip if you do not know.")
    elif hint:
        value, label = hint.value, hint.value
        if _LATLON.fullmatch(hint.value):
            label = "near the position the file carries"
        t.say(f"{_sentence(hint.because)}." + (" This is only a guess." if hint.strength == "weak" else ""))
        t.say("Press Enter to search there. Type skip if you do not know.")
    elif x.area:
        value, label = _BOX, x.area.label
        t.say(x.area.why)
        t.say("Press Enter to search only there. Or type a place if you know one.")
    else:
        t.say("A state, a city or a country all work.")
        t.say("A small place makes the search much faster.")

    def parse(text: str) -> str | None:
        if text.lower() in _WHOLE_USA or text.lower() in ("n", "no"):
            return None
        return value if label and text == label else text

    a.area = t.ask("Where was this filmed?" + ("" if label else " [Ohio]"), parse, "Ohio", label)
    if a.area == _BOX:
        a.area, a.bbox, a.box_label, a.box_why = None, x.area.bbox, x.area.label, x.area.why
        t.good("That is a smaller map than the whole USA, so the search is faster.")
    elif a.area:
        t.good("That makes the search about 20 times faster.")
    else:
        if label:
            a.area = "usa"      # said no to the place on offer: it must not come back as the search area
        t.say("No place. flyby will search the whole USA.")
        t.say("That is slow: about 20 minutes.")


def _day(t: _Talk, media: Media, hints: list[Hint], a: Answers) -> None:
    if a.day:
        t.fact("Day", a.day, "you gave this")
        return
    if media.started_utc:
        return        # the video states the moment, so the day is known
    default = ""
    if hint := _best(hints, "day"):
        default = hint.value
        t.say(f"{_sentence(hint.because)}.")
    elif media.uploaded:
        t.say(f"It was put online on {media.uploaded.isoformat()}. It may have been filmed before that.")
        t.say("If you do not know the day, press Enter. flyby then tries that day and the days before it.")
    t.say("You can type 2026-09-08, 9/8/2026 or yesterday.")
    t.say("9/8/2026 means September 8. For a few days: 2026-09-07..2026-09-09.")
    def parse(text: str) -> str | None:
        day = parse_day(text)
        if day and ".." in day:
            first, last = (date.fromisoformat(p) for p in day.split(".."))
            if (last - first).days + 1 > MAX_RANGE_DAYS:
                raise ValueError(f"That is more than {MAX_RANGE_DAYS} days. Each day is a search of its own.")
        return day

    a.day = t.ask("What day was it filmed? [8 sep 2026]", parse, "2026-09-08", default)
    if not a.day:
        t.say("No day. flyby will try a few days. That takes longer.")
    elif ".." in a.day:
        first, last = (date.fromisoformat(p) for p in a.day.split(".."))
        t.good(f"flyby will look at {(last - first).days + 1} days.")
    else:
        t.good("flyby will only look at flights on that day.")


def _single_day(a: Answers) -> date | None:
    if a.day and ".." not in a.day:
        return date.fromisoformat(a.day)
    return None


def _day_span(a: Answers) -> tuple[date, date] | None:
    """The first and last day the person gave, when they gave any."""
    if not a.day:
        return None
    first, _, last = a.day.partition("..")
    return date.fromisoformat(first), date.fromisoformat(last or first)


def _start(t: _Talk, media: Media, hints: list[Hint], a: Answers) -> None:
    if media.started_utc:
        t.fact("Start", _utc_text(media.started_utc), "the video says so")
        return
    if a.start_utc:
        t.fact("Start", _utc_text(a.start_utc), "you gave this")
        if a.start_tol_s is None:
            _sureness(t, a, 2)
        return
    day = _single_day(a)
    hint = _best(hints, "start")
    hint_time = None
    if hint:
        hint_time = datetime.fromisoformat(hint.value.replace("Z", "+00:00"))
        if hint_time.tzinfo is None:
            hint_time = hint_time.replace(tzinfo=timezone.utc)
        hint_time = hint_time.astimezone(timezone.utc)
        # an evening in the USA is the next day in UTC, so one day apart still agrees
        span = _day_span(a)
        if span and not span[0] - timedelta(days=1) <= hint_time.date() <= span[1] + timedelta(days=1):
            hint = hint_time = None
    # a clock burned into the picture gives the time of day, but not the time zone it is set to
    seen = None if hint else _best(hints, "clock")
    seen_time = datetime.fromisoformat(seen.value) if seen else None
    span = _day_span(a)
    if seen and span and not span[0] - timedelta(days=1) <= seen_time.date() <= span[1] + timedelta(days=1):
        seen = seen_time = None     # the person gave another day: a camera whose date was never set
    if day is None and hint_time is None and seen is None:
        return        # a time of day means nothing without a day
    t.gap()
    half_day = bool(seen) and seen.strength == "weak"       # the clock showed no AM or PM: only a person can tell
    label = f"{hint_time:%H:%M} UTC" if hint_time else f"{seen_time:%H:%M:%S}" if seen and not half_day else ""
    if hint:
        t.say(f"{_sentence(hint.because)}.")
        t.say("Press Enter to use that. Type skip if you do not know.")
    elif half_day:
        other = seen_time + timedelta(hours=12 if seen_time.hour < 12 else -12)
        t.say(f"{_sentence(seen.because)}.")
        t.say(f"That clock shows no AM or PM. So the video began at {seen_time:%H:%M} or at {other:%H:%M}.")
        t.say("Type the right one. Type skip if you do not know.")
    elif seen:
        t.say(f"{_sentence(seen.because)}.")
        t.say(f"So the video began at {seen_time:%H:%M:%S} on {seen_time.date().isoformat()}, by that clock.")
        t.say("Press Enter to use that. Type skip if you do not trust that clock.")
    else:
        t.say("This is the time the recording began.")
        t.say("The time on the phone that filmed it is best.")
    typed = t.ask("What time did the video start? [8:17 pm]",
                  lambda text: "hint" if text == label else parse_time(text), "8:17 pm", label)
    if typed is None:
        t.say("No time. flyby will search the whole day.")
        return
    if typed == "hint" and hint:
        a.start_utc = hint_time
        _sureness(t, a, 1 if hint.strength == "stated" else 2)
        return
    if typed == "hint":
        clock = _clock(t, "A camera shows local time. flyby needs to know which clock it is set to.")
        if clock is None:
            t.say("No clock. flyby can not use the time.")
            return
        zone = timezone.utc if CLOCKS[clock - 1][1] == "UTC" else ZoneInfo(CLOCKS[clock - 1][1])
        a.start_utc = seen_time.replace(tzinfo=zone).astimezone(timezone.utc)
        _sureness(t, a, 2)      # a camera's clock is set by hand and drifts
        return
    hour, minute, said_utc = typed
    if day is None:
        t.say("A time needs a day. flyby can not use this time.")
        return
    clock = len(CLOCKS) if said_utc else _clock(t)
    if clock is None:
        t.say("No clock. flyby can not use the time.")
        return
    zone = timezone.utc if CLOCKS[clock - 1][1] == "UTC" else ZoneInfo(CLOCKS[clock - 1][1])
    a.start_utc = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(timezone.utc)
    _sureness(t, a, 2)


def _clock(t: _Talk, lead: str = "Phones show local time. flyby needs to know which clock.") -> int | None:
    t.gap()
    t.say(lead)
    return t.choose("Which clock was that time on? [1]", [name for name, _ in CLOCKS])


def _sureness(t: _Talk, a: Answers, default: int) -> None:
    if a.start_tol_s is not None:
        t.fact("Sure", _tolerance(a.start_tol_s), "you gave this")
        return
    t.gap()
    pick = t.choose("How sure are you of that time? [2]", [name for name, _ in SURENESS], default)
    if pick is None:
        t.say("OK. flyby will allow about 10 minutes.")
        pick = 2
    a.start_tol_s = SURENESS[pick - 1][1]
    t.good(f"flyby will trust that time {SURENESS[pick - 1][0]}. That throws out most wrong answers.")


def _section(t: _Talk, media: Media, a: Answers) -> None:
    if a.section:
        t.fact("Part", f"{_hm(a.section[0])}-{_hm(a.section[1])}", "you gave this")
        return
    if media.duration_s <= LONG_VIDEO_S:
        return
    t.gap()
    t.say(f"This video is {_length(media.duration_s)} long.")
    t.say("Long recordings take long to search.")
    t.say("Daytime hours have the most aircraft.")
    t.say("  1  all of it")
    t.say("  2  the first 2 hours")
    t.say("Or type a part, in hours from the start of the video.")
    picked = t.ask("Which part should flyby use? [2:00-4:00]", lambda text: parse_section(text, media.duration_s),
                   "2:00-4:00", "1")
    if isinstance(picked, tuple):
        a.section = picked
        t.good(f"flyby will use {_hm(picked[0])} to {_hm(picked[1])}. That is much faster than all of it.")
    else:
        t.say("flyby will use all of it. That takes longer.")


def _still(t: _Talk, a: Answers) -> None:
    if a.still is not None:
        t.fact("Camera", "stayed in one place" if a.still else "moved", "you gave this")
        return
    a.still = t.yes_no("Did the camera stay in one place the whole time?")
    if a.still is False:
        t.say("This method needs a microphone that stays still.")
        t.say("The result will likely be empty. flyby will carry on.")
    elif a.still:
        t.good("A still microphone is what the method needs.")


def _sound(t: _Talk, a: Answers) -> None:
    if a.real_sound is not None:
        t.fact("Sound", "real" if a.real_sound else "has music or a voice", "you gave this")
        return
    a.real_sound = t.yes_no("Is the sound real, with no music or voice laid over it?")
    if a.real_sound is False:
        t.say("Music or a voice can hide the engines.")
        t.say("The result may be empty. flyby will carry on.")
    elif a.real_sound:
        t.good("flyby can trust the engine sound.")


def _effort(t: _Talk, a: Answers) -> None:
    names = [name for name, _ in EFFORTS]
    if a.effort:
        t.fact("Effort", a.effort, "you gave this")
        return
    t.gap()
    t.say("More searches are more sure, and take longer.")
    pick = t.choose("How long may flyby work? [2]", [f"{name}: {what}" for name, what in EFFORTS], 2)
    a.effort = names[(pick or 2) - 1]
    if pick is not None:
        t.good(f"flyby will use {EFFORTS[pick - 1][1]}.")


def _summary(t: _Talk, media: Media, a: Answers) -> None:
    t.gap()
    t.say("Here is what flyby will use:", "bold")
    place = (a.box_label or "the box you gave") if a.bbox else a.area if a.area and a.area != "usa" else "the whole USA (slow)"
    t.say(f"  Place:  {place}")
    t.say(f"  Day:    {a.day or ('from the video' if media.started_utc else 'not known')}")
    moment = media.started_utc or a.start_utc
    if moment:
        sure = "" if media.started_utc or a.start_tol_s is None else f", {_tolerance(a.start_tol_s)}"
        t.say(f"  Start:  {_utc_text(moment)}{sure}")
    t.say(f"  Part:   {_hm(a.section[0]) + '-' + _hm(a.section[1]) if a.section else 'all of it'}")
    t.say(f"  Camera: {'stayed in one place' if a.still else 'moved' if a.still is False else 'not known'}")
    t.say(f"  Sound:  {'real' if a.real_sound else 'has music or a voice' if a.real_sound is False else 'not known'}")
    t.say(f"  Effort: {a.effort or 'normal'}")


def _tolerance(seconds: float) -> str:
    return next((name for name, s in SURENESS if s == seconds), f"within {int(seconds)} s")


# ---------------------------------------------------------------- public

def ask_source(console: Console, stream: TextIO | None = None) -> str:
    """A video link or a file, for when flyby is started with nothing."""
    t = _Talk(console, stream)
    t.gap()
    t.say("Paste a video link, or drag a file here.", "dim")
    for _ in range(EMPTY_TRIES):
        source = clean_source(t.raw("Video link or file? [https://youtu.be/abc123]"))
        if source:
            return source
        t.say("I need a link or a file to start.")
    raise FlybyError("No video link or file was given.")


def interview(console: Console, media: Media, hints: list[Hint], given: Answers,
              stream: TextIO | None = None, extras: Extras | None = None) -> Answers:
    """Ask for what the video does not say. Anything already in `given` is kept and not asked again."""
    t = _Talk(console, stream)
    a = replace(given)
    x = extras or Extras()
    t.say("A few questions. Each answer makes the search faster or more sure.", "dim")
    t.say("Press Enter to take the answer shown. With none shown, Enter means: I do not know.", "dim")
    t.gap()
    _where(t, media, hints, a, x)
    t.gap()
    _day(t, media, hints, a)
    _start(t, media, hints, a)
    _section(t, media, a)
    t.gap()
    _still(t, a)
    t.gap()
    _sound(t, a)
    _effort(t, a)
    _summary(t, media, a)
    return a
