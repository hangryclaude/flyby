"""Practice on a recording whose true spot is known (--known LAT,LON).

A "no match" on its own teaches nothing. With the true spot flyby can say which of three
things happened: no aircraft in the flight data came near it, aircraft came near but cannot
be heard in the recording, or they can be heard and the search still missed them.
The search itself never sees the true spot: it runs blind first, and this runs after it.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .dayindex import cached_index
from .measure import Flyover
from .model import Clues, Media, Result
from .verify import R_MAX_M, SEEN_P, Pass, Tracks, _one_aircraft, check
from .weather import STILL_AIR_C, fetch_air

CLOSE_M = 3000.0        # a light aircraft nearer than this is usually loud enough to measure
SAME_S = 90.0           # a measured flyover and a pass in the data this close in time are taken for one aircraft
BOX_DEG = 0.2           # the flight data must reach this far around the true spot to count the passes
GOOD_M = 300.0          # the plan's bar for "located": this close to the true spot
BUSY_SHARE = 0.25       # with passes covering more of the recording than this, lining up in time says nothing


def _covered(times: list[float], duration_s: float) -> float:
    """The share of the recording that lies within SAME_S of one of these times."""
    reach, last = 0.0, 0.0
    for t in sorted(times):
        a, b = max(t - SAME_S, last, 0.0), min(t + SAME_S, duration_s)
        if b > a:
            reach, last = reach + b - a, b
    return reach / duration_s if duration_s else 0.0


def metres_apart(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Between two (lat, lon) points. Flat-earth, which is good to a metre over the distances that matter here."""
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111_320.0, (a[1] - b[1]) * 111_320.0 * math.cos(lat))


def _far(metres: float) -> str:
    return f"{metres:,.0f} m" if metres < 2000 else f"{metres / 1000:,.0f} km"


def _passes(truth: tuple[float, float], start: datetime, spec: Path, indexes: list[Path]) -> tuple[list[Pass], list[Pass]]:
    """(every aircraft pass the data puts in range of the true spot while the recording ran,
    the ones whose tone could be looked for), each aircraft once."""
    from dopplertrack.audio import Spectrogram
    from dopplertrack.doppler import speed_of_sound
    from dopplertrack.geo import lla_to_enu
    from dopplertrack.terrain import Terrain

    lat, lon = truth
    sp = Spectrogram.load(spec)
    ground = max(float(Terrain().elevation(np.array([lat]), np.array([lon]))[0]), 0.0)
    air = fetch_air(lat, lon, start)
    c_at = lambda height: speed_of_sound(air.temp_at(height) if air else STILL_AIR_C)
    passes: list[Pass] = []
    tested: list[Pass] = []
    for index in indexes:
        tracks = Tracks(index)
        obs = np.array(lla_to_enu(lat, lon, ground, tracks.lat0, tracks.lon0), dtype=float)
        got = check(sp, tracks, obs, start.timestamp(), set(), c_at)
        # a flight in the air at midnight is in both days' data
        passes += [q for q in got.passes if not any(_one_aircraft(p, q) for p in passes)]
        tested += [q for q in got.others if not any(_one_aircraft(p, q) for p in tested)]
    return passes, tested


def practice(truth: tuple[float, float], media: Media, clues: Clues, result: Result, flyovers: list[Flyover],
             spec: Path, days_root: Path) -> list[str]:
    """Plain sentences on how the run did against the true spot, and why."""
    lat, lon = truth
    notes = []
    off = metres_apart(truth, (result.lat, result.lon)) if result.lat is not None else None
    if off is None:
        notes.append("Practice: flyby named no place, so there is no answer to measure against the true spot.")
    else:
        sure, right = result.verdict == "located", off <= GOOD_M
        notes.append(f"Practice: the answer is {_far(off)} from the true spot. " + (
            "This run worked." if sure and right else
            "The lead was right, though flyby was not sure of it." if right else
            "flyby was sure, and it was wrong. That is the failure that matters most: keep this run." if sure else
            "The lead was wrong, and flyby was right not to be sure of it."))
    south, north, west, east = clues.bbox
    if not (south <= lat <= north and west <= lon <= east):
        notes.append("Practice: the true spot is outside the area that was searched, so this search could not have found it.")

    # the clock the person or the source gave; failing that, the one a match this close implies
    start = clues.start_utc or (result.recording_start_utc if off is not None and off <= GOOD_M else None)
    if start is None:
        notes.append("Practice: the recording's start time is not known, so flyby cannot say which aircraft passed "
                     "the true spot while it ran. Give --start to find out.")
        return notes
    start = start.astimezone(timezone.utc)
    wide = BOX_DEG / math.cos(math.radians(lat))
    box = (lat - BOX_DEG, lat + BOX_DEG, lon - wide, lon + wide)
    days = sorted({start.date(), (start + timedelta(seconds=media.duration_s)).date()})
    indexes = [index for day in days if (index := cached_index(day, box, days_root)) is not None]
    if not indexes:
        notes.append("Practice: no flight data on this Mac covers the true spot on that day, so the passes there cannot be counted.")
        return notes

    passes, tested = _passes(truth, start, spec, indexes)
    close = [q for q in passes if q.slant_m <= CLOSE_M]
    seen = [q for q in tested if q.seen]
    real = [f for f in flyovers if any(abs(f.t - q.heard_s) <= SAME_S for q in passes)]
    notes.append(f"Practice: the flight data has {len(passes)} aircraft passes within {R_MAX_M / 1000:.0f} km of the true spot "
                 f"while the recording ran, {len(close)} of them within {CLOSE_M / 1000:.0f} km.")
    # both counts are set beside what luck alone gives, or a busy sky makes any recording look good
    by_luck = SEEN_P * len(tested)
    heard = len(seen) >= 2 and len(seen) >= 5 * by_luck
    if tested:
        notes.append(f"Practice: {len(seen)} of the {len(tested)} passes that could be tested can be seen in the sound "
                     f"at the second the tracks predict. Luck alone gives about {by_luck:.1f}.")
    busy = _covered([q.heard_s for q in passes], media.duration_s)
    if flyovers and passes:
        notes.append(f"Practice: {len(real)} of the {len(flyovers)} flyovers flyby measured came within {SAME_S:.0f} s of one of "
                     f"those passes. Luck alone gives about {busy * len(flyovers):.1f}.")

    if off is not None:
        return notes        # the distance already says how the run did
    if not passes:
        notes.append("Practice: so there was nothing in the flight data to find here.")
    elif not heard:
        notes.append("Practice: so aircraft were in the data, but they cannot be made out in this recording.")
    elif len(real) < 2 or busy > BUSY_SHARE:
        notes.append("Practice: so aircraft can be made out in the sound, but flyby did not measure two of them cleanly. "
                     "A longer part of the recording is the thing to try.")
    else:
        notes.append("Practice: so the aircraft are in the data and in the sound, and the search still missed. This one is worth a closer look.")
    return notes
