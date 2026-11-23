"""What the picture adds to a run: still frames, the writing in them, the light through the video,
and a page for picture search.

Everything here is an extra. A video with no picture, a site that will not hand over a frame, a Mac
that cannot build the text reader: the search goes on without it, and a note says what was skipped.
"""
from __future__ import annotations

import http.client
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import frames as frames_mod
from . import lookup, readtext, sun
from .ingest import CUT_REPLAY_HOURS
from .model import Area, FlybyError, Frame, Hint, Media, TextLine

PICTURES = 6            # still frames taken from the video
SHOWN = 4               # most clues named in the terminal; the page shows them all
GAP_WORTH_SAYING_S = 5.0
DARK_SHARE = 0.99       # this share of a video's light samples darker than frames.DARK: dark from start to end
FLAT = 0.004            # sharpness under this is a picture with no shapes in it. A black slide measures 0.002
_RANK = {"stated": 0, "likely": 1, "weak": 2}
# an extra must never stop a run: these are the ways a tool, a site or the network can let one down
_LET_DOWN = (FlybyError, OSError, subprocess.SubprocessError, http.client.HTTPException)


@dataclass
class Seen:
    """What the pictures gave."""
    frames: list[Frame] = field(default_factory=list)       # taken from the video, in time order
    thumbs: list[Frame] = field(default_factory=list)       # the site's own pictures: anyone can open their links
    light: list[tuple[float, float, float]] = field(default_factory=list)   # (second, brightness, colour) through the video
    lines: list[TextLine] = field(default_factory=list)     # the writing read off the pictures
    hints: list[Hint] = field(default_factory=list)         # what the writing says about the place, the day and the clock
    areas: list[Area] = field(default_factory=list)         # parts of the map the pictures point to, the sure ones first
    clock_gap_s: float | None = None    # the camera's clock minus the stream's clock
    notes: list[str] = field(default_factory=list)
    page: Path | None = None            # the picture search page


def merge(first: list[Hint], more: list[Hint]) -> list[Hint]:
    """One list, strongest first. Of two hints that say the same thing, the earlier one stays."""
    out, had = [], set()
    for hint in sorted([*first, *more], key=lambda h: _RANK.get(h.strength, 3)):      # sorted keeps ties in order
        key = (hint.kind, hint.value.lower())
        if key not in had:
            had.add(key)
            out.append(hint)
    return out


def narrowest(areas: list[Area], sure_only: bool = False) -> Area | None:
    """The part of the map all the areas agree on. With nobody to ask, only the sure ones count:
    a wrong area makes the search miss, and nobody is there to catch it."""
    use = sorted((a for a in areas if a.sure or not sure_only), key=lambda a: not a.sure)
    if not use:
        return None
    box, kept = use[0].bbox, [use[0]]
    for area in use[1:]:
        cut = (max(box[0], area.bbox[0]), min(box[1], area.bbox[1]), max(box[2], area.bbox[2]), min(box[3], area.bbox[3]))
        if cut[0] >= cut[1] or cut[2] >= cut[3]:
            # they do not overlap, so one of them is wrong. Two sure ones cancel out: nothing says which
            # to believe. A less sure one gives way to what the surer ones already agree on
            if area.sure:
                return None
            continue
        box = cut
        kept.append(area)
    if len(kept) == 1:
        return kept[0]
    return Area(box, " and ".join(a.label for a in kept), " ".join(a.why for a in kept), all(a.sure for a in kept))


def _sentence(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}."


def _why_not(e: Exception) -> str:
    return str(e) if isinstance(e, FlybyError) else f"The network or a tool let it down ({type(e).__name__})."


def see(media: Media, folder: Path, bin_dir: Path, pages: Path, ui) -> Seen:
    """Look at the video's picture. `folder` keeps what was taken, so a second run fetches nothing."""
    seen = Seen()
    if not media.video.get("has_video"):
        return seen
    told = 0

    def tell() -> None:
        """Show the notes added since the last step. Only once a step's own line is done: a note ends the live line."""
        nonlocal told
        for note in seen.notes[told:]:
            ui.note(note)
        told = len(seen.notes)

    ui.step("frames", then="Measure how light the video is")
    try:
        seen.frames = frames_mod.grab(media, folder, PICTURES, progress=ui.progress)
    except _LET_DOWN as e:
        seen.notes.append(f"No pictures were taken from the video. {_why_not(e)}")
    try:
        seen.thumbs = frames_mod.thumbnails(media, folder)     # the site's own pictures still serve when the video's do not come
    except _LET_DOWN as e:
        seen.notes.append(f"The site's own pictures were not fetched. {_why_not(e)}")
    n = len(seen.frames)
    ui.done(f"{n} picture{'s' if n != 1 else ''} taken from the video" if n else "No pictures could be taken from the video")
    tell()

    have = bool(seen.frames or seen.thumbs)
    ui.step("light", then="Read the writing in the pictures" if have else None)
    try:
        seen.light = frames_mod.light(media, folder, progress=ui.progress)
    except _LET_DOWN as e:
        seen.notes.append(f"The light through the video was not measured. {_why_not(e)}")
    ui.done("Light measured through the video" if seen.light else "The light through the video could not be measured")
    tell()
    # Sound posted under a black slide is common: nothing to see, and its light says nothing about the sun.
    # A real night view is dark too, but it has shapes in it (lights, a skyline), and its light is kept
    dark = bool(seen.light) and sum(b < frames_mod.DARK for _, b, _ in seen.light) >= DARK_SHARE * len(seen.light)
    black = dark and all(_blank(f) for f in seen.frames)
    if black:
        seen.light = []

    timed = bool(media.started_utc and seen.light)      # only a known clock puts the light on the map
    if have:
        ui.step("read", then="Look for a sunset or sunrise in the light" if timed else None if black
                else "Build the picture search page")
        try:
            seen.lines = readtext.read([*seen.frames, *seen.thumbs], bin_dir)
            _take(seen, readtext.understand(seen.lines, media.started_utc, media.uploaded),
                  cut=media.duration_s / 3600 >= CUT_REPLAY_HOURS)
        except _LET_DOWN as e:
            seen.notes.append(f"The writing in the pictures was not read. {_why_not(e)}")
        n = len(seen.lines)
        ui.done(f"{n} line{'s' if n != 1 else ''} of writing read off the pictures" if n else "No writing found in the pictures")
        for hint in seen.hints[:SHOWN]:
            ui.note(_sentence(hint.because))
        for area in seen.areas:
            ui.note(area.why)
    _drop_empty(seen, black)
    have = bool(seen.frames or seen.thumbs)
    tell()

    if timed:
        ui.step("sun", then="Build the picture search page" if have else None)
        found = sun.changes(seen.light, media.started_utc)
        strip = sun.strip(found) if found else None
        if strip:
            seen.areas.append(Area(strip.bbox, "the strip where the sun was that low", strip.why, sure=False))
        ui.done("The sun's light narrows the map" if strip else "The sun's light does not narrow the map")
        if strip:
            ui.note(strip.why)

    if have:
        ui.step("pictures_page")
        seen.page = lookup.write_page(media, [*seen.thumbs, *seen.frames], seen.lines, seen.hints, pages)
        ui.done("Picture search page ready")
        ui.note(str(seen.page))
    return seen


def _blank(frame: Frame) -> bool:
    """Nothing to see: dark, and with no shapes in it."""
    return frame.brightness < frames_mod.DARK and frame.sharpness < FLAT


def _drop_empty(seen: Seen, black: bool) -> None:
    """Leave out every picture with nothing in it: black, and no writing read off it.
    Nothing to search the web for, nothing for a person or an AI to look at."""
    def empty(frame: Frame) -> bool:
        return _blank(frame) and not any(line.frame == frame.path for line in seen.lines)

    dropped = sum(empty(f) for f in seen.frames)
    seen.frames = [f for f in seen.frames if not empty(f)]
    seen.thumbs = [f for f in seen.thumbs if not empty(f)]
    if black:
        seen.notes.append("The video's picture is black apart from some writing, so its light is not used." if seen.frames else
                          "The video's picture is black from start to end, so there is nothing in it to use.")
    elif dropped:
        seen.notes.append("1 picture was black, so it was left out." if dropped == 1 else
                          f"{dropped} pictures were black, so they were left out.")


def _take(seen: Seen, reading, cut: bool = False) -> None:
    """Keep what the writing says, in plain words. `cut`: the replay is long enough to have lost its start."""
    seen.hints = list(reading.hints)
    if zone := reading.zone:
        hours = abs(zone.utc_offset_h)
        why = (f"The clock in the picture runs {hours:g} hour{'s' if hours != 1 else ''} "
               f"{'behind' if zone.utc_offset_h < 0 else 'ahead of'} UTC.")
        if zone.name:
            why += f" That is {zone.name} time."
        if zone.also:
            why += (f" It could be {zone.also} time" if zone.name else f" That is {zone.also} time") \
                   + " on a camera that skips the clock change."
        # a replay cut by a whole hour would show a clock one zone further east than the camera really is
        if cut:
            why += " That holds only if the replay did not lose its first hours."
        names = " or ".join(n for n in (zone.name, zone.also) if n)
        seen.areas.append(Area(zone.bbox, f"the {names} time zone", why, sure=not cut))
    if reading.clock_gap_s is not None:
        seen.clock_gap_s = reading.clock_gap_s
        if abs(reading.clock_gap_s) >= GAP_WORTH_SAYING_S:
            seen.notes.append(f"The camera's own clock runs {abs(reading.clock_gap_s):.0f} s "
                              f"{'behind' if reading.clock_gap_s < 0 else 'ahead of'} the stream's clock.")


def cross_checks(seen: Seen, lat: float, lon: float, video_start: datetime | None) -> list[str]:
    """After an answer: do the pictures agree with it? `video_start` is when second 0 of the whole video was."""
    notes = []
    for area in seen.areas:
        if area.sure:
            inside = area.bbox[0] <= lat <= area.bbox[1] and area.bbox[2] <= lon <= area.bbox[3]
            notes.append(f"Clock check: the answer lies in {area.label}, as the camera's clock says." if inside else
                         f"Clock check: the answer lies outside {area.label}, where the camera's clock puts it. "
                         "One of the two is wrong.")
    if video_start and seen.light:
        notes += [f"Sun check: {line}" for line in sun.daylight(lat, lon, seen.light, video_start)]
    return notes
