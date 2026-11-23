"""Shared data shapes. The modules only talk to each other through these."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# latmin, latmax, lonmin, lonmax: the engine's bbox order, kept everywhere in flyby
CONUS = (24.0, 50.0, -125.0, -66.0)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def plain(text: str) -> str:
    """Words from a stranger's video with the control characters taken out. They end up printed in a
    terminal, where an escape code could clear the screen or redraw a line the person trusts."""
    return _CONTROL.sub("", text)


class FlybyError(Exception):
    """A problem the user can act on. The CLI prints the message without a traceback."""


class PlaceNotFound(FlybyError):
    """The map service answered, and knows no place of that name."""


class NoFlightData(FlybyError):
    """The flight archive has nothing usable for one day. The other days can still be searched."""


@dataclass
class Media:
    """What ingest learned about the thing the user sent."""
    source: str                    # URL or path exactly as given
    audio_path: Path | None        # local file ffmpeg can decode; None until a link's audio has been fetched
    title: str
    slug: str                      # filesystem-safe short name: run folder and report page are named after it
    duration_s: float
    kind: str                      # "live" (replay of an ended stream), "upload" or "file"
    uploaded: date | None = None   # platform upload day (UTC); None for local files
    started_utc: datetime | None = None                # UTC time of the first audio sample, only when the source records it
    location_hint: tuple[float, float] | None = None   # (lat, lon) when the file or platform states one
    notes: list[str] = field(default_factory=list)     # plain-language facts worth showing the user
    # the publisher's own words, for the clue reader: any of title, description, channel, location, tags
    about: dict[str, str] = field(default_factory=dict)
    # what the picture tools need. Always "has_video". A link also has "site", "id", "thumbnails" (the site's
    # own public pictures, widest first) and, when the site has one, "storyboard": its strip of small frames
    # through the whole video, as {"urls", "rows", "columns", "tile_w", "tile_h", "seconds_per_tile"}.
    video: dict = field(default_factory=dict)


@dataclass
class Hint:
    """Something the video itself suggests about where or when it was made."""
    kind: str                      # "place" | "day" | "start" | "clock"
    value: str                     # place: a name a map search can find, or "lat,lon"; day: YYYY-MM-DD; start: ISO time in UTC;
                                   # clock: when second 0 was by a clock in the picture, as local ISO time with no zone
    because: str                   # a few words the person can check: 'the title says "NE Oklahoma"'
    strength: str                  # "stated" (the publisher says so outright) | "likely" | "weak"
    # for a "Town, State" read off a line of text: every way to read the town's name, longest first.
    # A map search picks the real one (clues.confirm_places); "LIVE Bowling Green" is not a town, "Green" is the wrong one.
    towns: tuple[str, ...] = ()
    from_picture: bool = False     # read off the picture, not said by the publisher: only a guess once the map cannot back it


@dataclass
class Frame:
    """One still picture from the video."""
    path: Path                     # a JPEG on this Mac
    t_s: float | None              # seconds into the whole video; None for the site's own thumbnail
    brightness: float = 0.0        # 0 black to 1 white: the mean of (0.299 R + 0.587 G + 0.114 B) / 255
    colour: float = 0.0            # 0 is grey (a night-vision picture): the mean of (max(R,G,B) - min(R,G,B)) / 255
    sharpness: float = 0.0         # more fine detail scores higher; only compared between frames of one video
    public_url: str | None = None  # a link anyone can open to this same picture, when the site has one


@dataclass
class TextLine:
    """One line of writing read off a frame."""
    text: str
    conf: float                    # the reader's own confidence, 0 to 1
    x: float                       # left edge, 0 to 1 across the picture
    y: float                       # top edge, 0 to 1 down the picture
    w: float
    h: float
    t_s: float | None = None       # the frame's time in the video
    frame: Path | None = None      # the frame it was read off


@dataclass
class Area:
    """A part of the map that a clue points to: smaller than the USA, bigger than a town."""
    bbox: tuple[float, float, float, float]
    label: str                     # "the Eastern time zone"
    why: str                       # one plain sentence the person can check
    sure: bool = False             # True when it rests on a clock; False for something softer, like the light


@dataclass
class Answers:
    """What the person told flyby, by flag or by answering its questions. None = not known."""
    area: str | None = None        # place to search, as typed
    bbox: tuple[float, float, float, float] | None = None
    box_label: str | None = None   # for a box that a clue gave and nobody typed: its name ("the Eastern time zone")
    box_why: str | None = None     # and the sentence that says where it came from
    day: str | None = None         # YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD
    start_utc: datetime | None = None    # when the recording began
    start_tol_s: float | None = None     # how far off that time may be, seconds
    section: tuple[float, float] | None = None   # the part of a long video to use, seconds from its start
    still: bool | None = None      # the microphone stayed in one place
    real_sound: bool | None = None       # the sound is what the microphone heard, with no music laid over it
    effort: str | None = None      # "quick" (one search) | "normal" | "thorough" | "N searches"; None = not said


@dataclass
class Clues:
    """When and where to search."""
    days: list[date]               # UTC days to search, most likely first
    bbox: tuple[float, float, float, float]
    area_label: str                # "continental USA", "New York", ...
    day_reason: str                # one line: how the days were chosen
    area_reason: str               # one line: how the area was chosen
    start_utc: datetime | None = None   # recording start, when known
    start_tol_s: float = 180.0     # how far off start_utc may be
    # a smaller place inside bbox that the answer should fall in (a town, a GPS tag). The search
    # box stays wide so there are enough wrong answers to judge the winner against.
    focus: tuple[float, float, float, float] | None = None
    focus_label: str | None = None


@dataclass
class Event:
    """One flyover measured in the audio."""
    t_cpa: float                   # seconds into the recording when the closest pass was heard
    f0: float                      # Hz, the propeller tone at rest
    v: float                       # m/s
    d: float                       # m, slant distance at the closest pass
    score: float                   # dB above background along the fitted curve
    role: str                      # "pair" (drives the search) or "extra" (confirms it)
    json_path: Path | None = None  # the engine's event file


@dataclass
class Flight:
    """An aircraft matched to an event."""
    icao: str                      # 24-bit address, hex
    heard_utc: datetime | None = None   # when its closest pass was heard at the answer
    slant_m: float | None = None
    speed_ms: float | None = None
    registration: str | None = None
    type_name: str | None = None


@dataclass
class Sighting:
    """Another aircraft that flew past the answer, and whether its tone is in the recording."""
    flight: Flight
    heard_s: float                 # seconds into the recording when it was closest
    seen: bool                     # its tone is there at the predicted second, and not at other times
    clearer_than: float            # share of the wrong times tried that its tone beats, 0 to 1


@dataclass
class Result:
    """What the search concluded. `verdict` decides what the report may claim."""
    verdict: str                   # "located" | "not sure" | "no match" | "no flyovers"
    headline: str                  # one plain sentence
    lat: float | None = None       # None unless verdict is "located" or "not sure"
    lon: float | None = None
    p: float | None = None         # engine's chance that a wrong answer would lead by this much
    margin_pct: float | None = None     # winner's lead over the best other flight pair
    day: date | None = None        # the UTC day that produced the answer
    recording_start_utc: datetime | None = None   # the recording clock the match implies
    events: list[Event] = field(default_factory=list)
    flights: list[Flight] = field(default_factory=list)
    runners_up: list[tuple[float, float, float]] = field(default_factory=list)   # (lat, lon, score), best first
    figures: list[Path] = field(default_factory=list)     # PNGs: predicted curves over the spectrogram
    sightings: list[Sighting] = field(default_factory=list)   # aircraft the search did not use, checked afterwards
    air_c: float | None = None     # air temperature used for the speed of sound, when the weather was looked up
    notes: list[str] = field(default_factory=list)        # caveats in plain language
    searched: list[str] = field(default_factory=list)     # one line per day searched, with its outcome
    work_dir: Path | None = None
