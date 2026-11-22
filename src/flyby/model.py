"""Shared data shapes. The modules only talk to each other through these."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# latmin, latmax, lonmin, lonmax: the engine's bbox order, kept everywhere in flyby
CONUS = (24.0, 50.0, -125.0, -66.0)


class FlybyError(Exception):
    """A problem the user can act on. The CLI prints the message without a traceback."""


class NoFlightData(FlybyError):
    """The flight archive has nothing usable for one day. The other days can still be searched."""


@dataclass
class Media:
    """What ingest learned about the thing the user sent."""
    source: str                    # URL or path exactly as given
    audio_path: Path               # local file ffmpeg can decode
    title: str
    slug: str                      # filesystem-safe short name: run folder and report page are named after it
    duration_s: float
    kind: str                      # "live" (replay of an ended stream), "upload" or "file"
    uploaded: date | None = None   # platform upload day (UTC); None for local files
    started_utc: datetime | None = None                # UTC time of the first audio sample, only when the source records it
    location_hint: tuple[float, float] | None = None   # (lat, lon) when the file or platform states one
    notes: list[str] = field(default_factory=list)     # plain-language facts worth showing the user


@dataclass
class Clues:
    """When and where to search."""
    days: list[date]               # UTC days to search, most likely first
    bbox: tuple[float, float, float, float]
    area_label: str                # "continental USA", "New York", ...
    day_reason: str                # one line: how the days were chosen
    area_reason: str               # one line: how the area was chosen
    start_utc: datetime | None = None   # recording start, when known to about a minute


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
    notes: list[str] = field(default_factory=list)        # caveats in plain language
    searched: list[str] = field(default_factory=list)     # one line per day searched, with its outcome
    work_dir: Path | None = None
