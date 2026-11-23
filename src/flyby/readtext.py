"""Read the writing burned into or standing in the picture: a camera's clock, its caption, signs.

The reading is Apple's Vision framework through a small Swift helper, built on first use. The
understanding is plain patterns with guards, like the title reader: a clock counts only when two
pictures agree with each other and with the video, and a date that could be month-first or
day-first is left unread rather than guessed.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .areacodes import AREA_CODES
from .hints import ARCHIVE_START, MONTHS, RANK, read_places
from .model import FlybyError, Frame, Hint, TextLine, plain

SWIFT_SOURCE = r"""
// Reads the text in each image named on the command line with Apple's Vision framework.
// One JSON object per line: {"file": ..., "lines": [{"text": ..., "conf": ..., "x": ..., "y": ..., "w": ..., "h": ...}]}
import Foundation
import Vision
import ImageIO

for path in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: path)
    var lines: [[String: Any]] = []
    if let src = CGImageSourceCreateWithURL(url as CFURL, nil), let image = CGImageSourceCreateImageAtIndex(src, 0, nil) {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        // a clock or a sign is not a sentence: correcting it towards words breaks numbers and names
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["en-US"]
        try? VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        for obs in request.results ?? [] {
            guard let top = obs.topCandidates(1).first else { continue }
            let b = obs.boundingBox
            // Vision measures from the bottom; flyby measures from the top
            lines.append(["text": top.string, "conf": Double(top.confidence),
                          "x": Double(b.minX), "y": Double(1 - b.maxY), "w": Double(b.width), "h": Double(b.height)])
        }
    }
    let out: [String: Any] = ["file": path, "lines": lines]
    if let data = try? JSONSerialization.data(withJSONObject: out), let text = String(data: data, encoding: .utf8) {
        print(text)
    }
}
"""
BUILD_TIMEOUT_S = 90
READ_TIMEOUT_S = 30                 # for the first picture; each one after adds PER_FRAME_S
PER_FRAME_S = 5
CLOCK_TOL_S = 3.0                   # how far two clocks may disagree with the video and still count as one clock
ZONE_TOL_S = 300                    # a camera clock set by hand can be minutes off; more than this is not a zone
HALF_DAY_S = 43200.0
MIN_CONF = 0.5                      # the reader's own doubt: a line it is less sure of than this names no place
CAPTION_BAND = 0.2                  # captions sit in the top or bottom fifth
SAME_PLACE = 0.03                   # how far a caption may move between pictures, as a share of the picture

# (name, zone, a generous box around the states on that clock)
US_ZONES = [
    ("Eastern", "America/New_York", (24.0, 49.0, -90.0, -66.0)),
    ("Central", "America/Chicago", (25.0, 49.5, -105.0, -82.0)),
    ("Mountain", "America/Denver", (31.0, 49.5, -117.5, -100.0)),
    ("Arizona", "America/Phoenix", (31.0, 37.5, -115.0, -109.0)),
    ("Pacific", "America/Los_Angeles", (32.0, 49.5, -125.0, -114.0)),
    ("Alaska", "America/Anchorage", (51.0, 72.0, -170.0, -129.0)),
    ("Hawaii", "Pacific/Honolulu", (18.5, 22.5, -160.5, -154.5)),
]

# a "." may stand where a ":" was: the reader sees the two dots of a colon as one when the font is thin
TIME_RE = re.compile(r"(?<![\d.:])(?P<h>\d{1,2})[:.](?P<m>\d{2})[:.](?P<s>\d{2})(?![\d:])"
                     r"(?:\s*(?P<ampm>[AaPp])\.?\s?[Mm]\.?(?![A-Za-z]))?")
_MON = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
YMD_RE = re.compile(r"(?<![\d.])(?P<y>\d{4})[-/.](?P<a>\d{1,2})[-/.](?P<b>\d{1,2})(?![\d])")
NUM_RE = re.compile(r"(?<![\d.])(?P<a>\d{1,2})[-/.](?P<b>\d{1,2})[-/.](?P<y>\d{4})(?![\d])")
NAMED_RES = [
    re.compile(rf"(?<![A-Za-z]){_MON}\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})(?!\d)", re.I),
    re.compile(rf"(?<!\d)(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+(?P<y>\d{{4}})(?!\d)", re.I),
]
PHONE_RE = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?(?:\((?P<a1>[2-9]\d{2})\)\s?|(?P<a2>[2-9]\d{2})[\s.-])"
                      r"[2-9]\d{2}[\s.-]\d{4}(?![\d-])")


@dataclass
class Clock:
    local_start: datetime      # naive: what the camera's own clock showed at second 0 of the video
    seen: str                  # the clock as read off one frame
    at_s: float                # that frame's time in the video
    frames: int                # how many frames agreed
    half_day: bool = False     # it showed no AM or PM and no hour past 12, so it may be 12 hours out


@dataclass
class Zone:
    name: str                  # "Eastern": the zones on this clock that day. Empty when only `also` fits
    utc_offset_h: float        # -4.0
    bbox: tuple[float, float, float, float]     # around every zone named, `also` included
    also: str = ""             # zones that fit only if the camera skips the clock change


@dataclass
class Reading:
    lines: list[TextLine]
    hints: list[Hint]          # strongest first
    clock: Clock | None = None
    zone: Zone | None = None
    clock_gap_s: float | None = None    # camera clock minus the stream's clock, once the zone's hours are taken off


# ---- reading ----

def read(frames: list[Frame], bin_dir: Path) -> list[TextLine]:
    """Every line of writing on each picture, in the order the pictures were given."""
    if not frames:
        return []
    helper = _helper(bin_dir)
    paths = [str(f.path) for f in frames]
    done = subprocess.run([str(helper), *dict.fromkeys(paths)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=READ_TIMEOUT_S + PER_FRAME_S * len(frames))
    if done.returncode != 0:
        raise FlybyError("The text reader stopped before it finished reading the pictures.")
    found = {}
    for row in done.stdout.splitlines():
        if row.strip():
            item = json.loads(row)
            found[item["file"]] = item["lines"]
    out = []
    for frame, path in zip(frames, paths):
        for ln in found.get(path, []):
            out.append(TextLine(plain(ln["text"]), float(ln["conf"]), float(ln["x"]), float(ln["y"]), float(ln["w"]),
                                float(ln["h"]), frame.t_s, frame.path))
    return out


def _helper(bin_dir: Path) -> Path:
    """The built reader, named after its source so a changed source is built again."""
    tag = hashlib.sha256(SWIFT_SOURCE.encode()).hexdigest()[:10]
    helper = bin_dir / f"flyby-ocr-{tag}"
    if helper.exists():
        return helper
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        raise FlybyError("Reading the writing in the picture needs Apple's Swift tools. "
                         "Install them with: xcode-select --install")
    bin_dir.mkdir(parents=True, exist_ok=True)
    source = bin_dir / f"flyby-ocr-{tag}.swift"
    source.write_text(SWIFT_SOURCE)
    # built under another name and moved into place, so a build cut short never looks finished
    partial = bin_dir / f"flyby-ocr-{tag}.part"
    done = subprocess.run([swiftc, "-O", str(source), "-o", str(partial)], stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=BUILD_TIMEOUT_S)
    if done.returncode != 0 or not partial.exists():
        raise FlybyError("The text reader could not be built with Swift, so the writing in the picture "
                         "was not read.")
    partial.replace(helper)
    return helper


# ---- understanding ----

def understand(lines: list[TextLine], started_utc: datetime | None = None, uploaded: date | None = None) -> Reading:
    """What the writing says about where and when: the camera's clock, its time zone, and place words."""
    clock = _clock(lines)
    zone = gap = None
    if clock and started_utc:
        zone, gap, shift = _zone(clock, started_utc)
        if zone:
            # the stream's own clock has settled which half of the day the camera's clock meant
            clock = replace(clock, local_start=clock.local_start + timedelta(seconds=shift), half_day=False)
    hints = _place_hints(lines)
    if clock:
        at = int(clock.at_s)
        because = f'the clock in the picture read "{clock.seen}" at {at // 3600}:{at % 3600 // 60:02d}:{at % 60:02d} into the video'
        day = clock.local_start.date()
        if day >= ARCHIVE_START and (uploaded is None or day <= uploaded):
            hints.append(Hint("day", day.isoformat(), because, "likely"))
        if started_utc is None:
            # with no AM or PM on it, only a person can say which half of the day it was
            hints.append(Hint("clock", clock.local_start.isoformat(), because, "weak" if clock.half_day else "likely"))
    hints.sort(key=lambda h: RANK[h.strength])
    return Reading(lines, hints, clock, zone, gap)


def _clock(lines: list[TextLine]) -> Clock | None:
    """The camera's clock at second 0, when at least two pictures agree on it."""
    # order -> [(seconds the clock was ahead of the video, frame time, line)]
    points: dict[str, list[tuple[float, float, str]]] = {"sure": [], "mdy": [], "dmy": []}
    proved: set[str] = set()
    epoch = datetime(2000, 1, 1)
    for ln in lines:
        if ln.t_s is None:
            continue
        readings = _clock_readings(ln.text)
        orders = {order for order, _ in readings}
        if len(orders & {"mdy", "dmy"}) == 1:
            proved |= orders & {"mdy", "dmy"}
        for order, when in readings:
            points[order].append(((when - epoch).total_seconds() - ln.t_s, ln.t_s, ln.text.strip()))
    found = {order: c for order, pts in points.items() if (c := _agree(pts)) is not None}
    if "mdy" in found and "dmy" in found and abs(found["mdy"][0] - found["dmy"][0]) > CLOCK_TOL_S:
        # the pictures fit both month-first and day-first: settle it only when one picture could only be one
        if len(proved) != 1:
            found.pop("mdy"), found.pop("dmy")
        else:
            found.pop(({"mdy", "dmy"} - proved).pop())
    if not found:
        return None
    # the surest kind of date wins a tie
    offset, members = max(found.values(), key=lambda c: len(c[1]))
    first = min(members, key=lambda p: p[1])
    return Clock(epoch + timedelta(seconds=round(offset)), first[2], first[1], len({p[1] for p in members}),
                 all(_half_day_open(text) for _, _, text in members))


def _half_day_open(text: str) -> bool:
    """A time that could be either half of the day: no AM or PM, and an hour a 12-hour clock also shows.
    "07:45:13" is not one: a clock that writes the 0 is a 24-hour clock."""
    t = TIME_RE.search(text)
    return t is not None and not t["ampm"] and 1 <= int(t["h"]) <= 12 and not t["h"].startswith("0")


def _agree(points: list[tuple[float, float, str]]) -> tuple[float, list[tuple[float, float, str]]] | None:
    """The biggest set of readings from at least two pictures that put second 0 within a few seconds."""
    points = sorted(points)
    best: list[tuple[float, float, str]] = []
    for i, (offset, _, _) in enumerate(points):
        group = [p for p in points[i:] if p[0] - offset <= CLOCK_TOL_S]
        if len({p[1] for p in group}) > len({p[1] for p in best}):
            best = group
    if len({p[1] for p in best}) < 2:
        return None
    return statistics.median(p[0] for p in best), best


def _clock_readings(text: str) -> list[tuple[str, datetime]]:
    """Every (order, time) a line can be read as: order is "sure", "mdy" or "dmy"."""
    t = TIME_RE.search(text)
    if t is None:
        return []
    rest = text[:t.start()] + " " * (t.end() - t.start()) + text[t.end():]
    days: list[tuple[str, int, int, int]] = []      # (order, year, month, day)
    for m in YMD_RE.finditer(rest):
        days.append(("sure", int(m["y"]), int(m["a"]), int(m["b"])))
    for m in NUM_RE.finditer(rest):
        a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
        days += [("mdy", y, a, b), ("dmy", y, b, a)]
    for rx in NAMED_RES:
        for m in rx.finditer(rest):
            days.append(("sure", int(m["y"]), MONTHS[m["mon"][:3].lower()], int(m["d"])))
    hour, minute, sec = int(t["h"]), int(t["m"]), int(t["s"])
    if t["ampm"]:
        if not 1 <= hour <= 12:
            return []
        hour = hour % 12 + (12 if t["ampm"].lower() == "p" else 0)
    out = []
    for order, y, mo, d in days:
        try:
            out.append((order, datetime(y, mo, d, hour, minute, sec)))
        except ValueError:
            continue                      # month 13, 31 June, hour 25: that reading of the line is not a clock
    return [(o, w) for o, w in out if 2000 <= w.year <= 2100]


def _zone(clock: Clock, started_utc: datetime) -> tuple[Zone | None, float | None, float]:
    """The US time zone the camera's clock is on, from how far it is from the stream's own start.
    The third value is the seconds to add to a clock that showed no AM or PM and was half a day out."""
    if started_utc.tzinfo is not None:
        started_utc = started_utc.astimezone(timezone.utc).replace(tzinfo=None)
    instant = started_utc.replace(tzinfo=timezone.utc)
    for shift in ((0.0, HALF_DAY_S, -HALF_DAY_S) if clock.half_day else (0.0,)):
        ahead = (clock.local_start - started_utc).total_seconds() + shift
        steps = round(ahead / 1800)
        gap = ahead - steps * 1800
        if abs(gap) > ZONE_TOL_S:
            break
        hours = timedelta(hours=steps / 2)
        on, also = [], []
        for name, tz, box in US_ZONES:
            zone = ZoneInfo(tz)
            if instant.astimezone(zone).utcoffset() == hours:
                on.append((name, box))
            # many cameras never follow the clock change: they show their zone's winter time all year,
            # or its summer time if that is when they were set
            elif hours in {datetime(instant.year, month, 1, tzinfo=timezone.utc).astimezone(zone).utcoffset() for month in (1, 7)}:
                also.append((name, box))
        boxes = [box for _, box in on + also]
        if boxes:
            around = (min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes), max(b[3] for b in boxes))
            return Zone(" or ".join(n for n, _ in on), steps / 2, around, " or ".join(n for n, _ in also)), gap, shift
    return None, None, 0.0


def _place_hints(lines: list[TextLine]) -> list[Hint]:
    texts: dict[str, tuple[str, str, str]] = {}
    for ln in lines:
        if ln.conf < MIN_CONF:
            continue
        key = " ".join(ln.text.split()).casefold()
        if not key or (key in texts and texts[key][2] == "likely"):
            continue
        text = " ".join(ln.text.split())
        if _caption(ln, lines):
            texts[key] = (text, f'a caption on the picture says "{text}"', "likely")
        elif key not in texts:
            texts[key] = (text, f'a sign in the picture says "{text}"', "weak")
    # A state's name alone is in too many company names, team names and scores ("ALABAMA POWER") to be more
    # than a guess. A town with its state is something the map can check
    hints = [replace(h, from_picture=True, strength=h.strength if "," in h.value else "weak")
             for h in read_places(list(texts.values()))]
    for code, line in _phones(lines):
        state = AREA_CODES[code]
        if not any(h.value == state or h.value.endswith(", " + state) for h in hints):
            hints.append(Hint("place", state, f"a phone number in the picture starts with {code}, an area code in {state}",
                              "weak", from_picture=True))
    return hints


def _caption(line: TextLine, lines: list[TextLine]) -> bool:
    """The same words at the same place on at least two pictures, near the top or bottom edge."""
    middle = line.y + line.h / 2
    if CAPTION_BAND <= middle <= 1 - CAPTION_BAND:
        return False
    key = " ".join(line.text.split()).casefold()
    frames = {(o.frame, o.t_s) for o in lines
              if " ".join(o.text.split()).casefold() == key
              and abs(o.x - line.x) <= SAME_PLACE and abs(o.y - line.y) <= SAME_PLACE}
    return len(frames) >= 2


def _phones(lines: list[TextLine]) -> list[tuple[str, str]]:
    """(area code, line) for each US phone number, first sighting of each code only."""
    out: dict[str, str] = {}
    for ln in lines:
        for m in PHONE_RE.finditer(ln.text):
            code = m["a1"] or m["a2"]
            if code in AREA_CODES and code not in out:
                out[code] = ln.text
    return list(out.items())
