"""Check a spot against every aircraft that flew past it, not only the ones the search used.

The search names a spot because two or three flights fit the sound there. Other aircraft
passed that spot during the recording too, and the tracks say exactly when each should have
been heard and how its tone should have fallen. At a wrong spot those tones are not there.
So this is a second test, on evidence the search never looked at.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .dayindex import mapped

R_MAX_M = 12_000.0      # the engine's own limit on how far away a pass can be heard
MIN_SWEEP = 0.06        # a tone that falls less than this share of its pitch looks like any steady hum
PITCHES = np.arange(60.0, 300.01, 0.5)     # propeller tone at rest, Hz: the band the detector searches
STEP_S = 1.0            # spacing of the points laid along a predicted tone
SLIDE_S = 5.0           # wrong-time copies of a tone are this far apart
GUARD_S = 90.0          # copies closer than this to the real time would still touch the real tone
MIN_SLIDES = 40         # fewer wrong-time copies than this cannot give a chance worth reporting
MIN_POINTS = 30
APART_S = 120           # two closest approaches of one flight nearer than this are one pass
SEEN_Z, SEEN_P = 4.0, 0.01     # how clear a tone must be to count as seen; see README for the measured false rate
TWIN_S, TWIN_SHARE = 15.0, 0.15    # two tracks this alike in time, distance and speed are one aircraft


@dataclass
class Seg:
    icao: str
    t: np.ndarray           # unix seconds, one a second
    enu: np.ndarray         # (N, 3) metres in the index's own frame
    speed: np.ndarray       # m/s


class Tracks:
    """A day index read straight off the disk. The engine's loader takes 6 GB of memory for a
    whole-USA day; this maps the same file and touches only the flights near one spot."""

    def __init__(self, path: Path):
        z = np.load(path)
        self.lat0, self.lon0, self.bucket_s = float(z["lat0"]), float(z["lon0"]), float(z["bucket_s"])
        self.off, self.icao = z["off"], z["icao"]
        self.sb, self.ss, self.sj, self.t, self.enu, self.spd = (mapped(path, n) for n in ("sb", "ss", "sj", "t", "enu", "spd"))

    def near(self, obs: np.ndarray, t0: float, t1: float, radius_m: float) -> list[Seg]:
        """Every flight with a position within radius of obs between unix times t0 and t1."""
        lo, hi = np.searchsorted(self.sb, [int(t0 // self.bucket_s) - 1, int(t1 // self.bucket_s) + 2])
        segs = np.asarray(self.ss[lo:hi])
        rows = self.off[segs] + np.asarray(self.sj[lo:hi])
        order = np.argsort(rows)                # file order, so the disk is read front to back
        # the index keeps one position every bucket_s, and a fast aircraft covers 2 km in that time
        close = np.linalg.norm(np.asarray(self.enu[rows[order]]) - obs, axis=1) < radius_m + 3000.0
        out = []
        for i in np.unique(segs[order][close]):
            sl = slice(int(self.off[i]), int(self.off[i + 1]))
            out.append(Seg(str(self.icao[i]), np.array(self.t[sl]), np.array(self.enu[sl]), np.array(self.spd[sl])))
        return out


@dataclass
class Pass:
    """One aircraft going past the spot, and whether its tone is in the recording."""
    icao: str
    heard_s: float              # seconds into the recording when it was closest
    slant_m: float
    speed_ms: float
    sweep: float = 0.0          # how far its tone falls across the window, as a share of its pitch
    p: float | None = None      # chance a tone this clear turns up at a wrong time; None = could not be tested
    z: float | None = None      # how many spreads above the wrong-time scores
    pitch_hz: float | None = None
    used: bool = False          # the search already used this flight, so it is no new evidence
    t_s: np.ndarray = field(default=None, repr=False)      # the predicted tone: seconds into the recording...
    ratio: np.ndarray = field(default=None, repr=False)    # ...and pitch heard / pitch made, for drawing it

    @property
    def seen(self) -> bool:
        return self.p is not None and self.p <= SEEN_P and self.z >= SEEN_Z


@dataclass
class Check:
    passes: list[Pass]          # every aircraft that passed within range, in the order heard
    others: list[Pass]          # the ones the search did not use and that could be tested, each aircraft once


def height_above(point: np.ndarray, obs: np.ndarray, lat0: float, lon0: float) -> float:
    """How far point is above obs, both given in an index's frame. The frame's up is the up at
    its origin. For a whole-USA index that is 2,000 km from New York and leans 18 degrees from
    the up there: read straight off the frame, an aircraft 5 km to one side comes out 1.5 km
    too high or too low."""
    from dopplertrack.geo import enu_to_lla
    return float(enu_to_lla(*point, lat0, lon0)[2] - enu_to_lla(*obs, lat0, lon0)[2])


def tone(t: np.ndarray, enu: np.ndarray, obs: np.ndarray, c: float) -> tuple[np.ndarray, np.ndarray]:
    """(time heard, pitch heard / pitch made) for each track position."""
    heard = t + np.linalg.norm(obs - enu, axis=1) / c
    return heard, 1.0 / np.gradient(heard, t)


def _test(sp, level: np.ndarray, heard: np.ndarray, ratio: np.ndarray, centre_s: float, half_s: float):
    """Is this predicted tone in the recording at the predicted time, more than at other times?

    The tone is laid over the recording at every pitch a propeller can have, here and at many
    wrong times. Each pitch is judged against its own wrong-time scores, so a pitch where a
    fan or a compressor hums all hour is not mistaken for an aircraft. Returns
    (sweep, p, z, pitch); p is None when the recording leaves no room for wrong-time copies."""
    every = max(int(round(STEP_S / sp.dt)), 1)
    mid = int(round((centre_s - sp.t[0]) / sp.dt))
    reach = int(half_s / sp.dt) // every * every
    cols = np.arange(mid - reach, mid + reach + 1, every)
    cols = cols[(cols >= 0) & (cols < len(sp.t))]
    r = np.interp(sp.t[cols], heard, ratio, left=np.nan, right=np.nan)
    cols, r = cols[np.isfinite(r)], r[np.isfinite(r)]
    if len(cols) < MIN_POINTS:
        return 0.0, None, None, None
    sweep = float(r.max() - r.min())
    if sweep < MIN_SWEEP:
        return sweep, None, None, None

    pos = (PITCHES[:, None] * r[None, :] - sp.f[0]) / sp.df
    inside = (pos >= 0) & (pos < len(sp.f) - 1)
    pos = np.where(inside, pos, 0.0)
    low = pos.astype(np.int64)
    frac = (pos - low).astype(np.float32)

    step = max(int(round(SLIDE_S / sp.dt)), 1)
    guard = max(GUARD_S, half_s) / sp.dt
    shifts = np.arange(-(cols[0] // step), (len(sp.t) - 1 - cols[-1]) // step + 1) * step
    shifts = np.r_[0, shifts[np.abs(shifts) > guard]]
    if len(shifts) - 1 < MIN_SLIDES:
        return sweep, None, None, None
    scores = np.empty((len(shifts), len(PITCHES)), np.float32)
    for a in range(0, len(shifts), 32):         # in batches, so the working arrays stay a few tens of MB
        at = cols[None, None, :] + shifts[a:a + 32, None, None]
        lo, hi = level[low[None], at], level[low[None] + 1, at]
        scores[a:a + 32] = np.where(inside[None], lo + (hi - lo) * frac[None], 0.0).mean(axis=2)

    wrong = scores[1:]
    centre = np.median(wrong, axis=0)
    spread = 1.4826 * np.median(np.abs(wrong - centre), axis=0) + 1e-6
    best = ((scores - centre) / spread).max(axis=1)
    p = (1 + int((best[1:] >= best[0]).sum())) / len(best)
    z = float((best[0] - best[1:].mean()) / (best[1:].std() + 1e-9))
    return sweep, p, z, float(PITCHES[int(((scores[0] - centre) / spread).argmax())])


def _one_aircraft(a: Pass, b: Pass) -> bool:
    return (abs(a.heard_s - b.heard_s) <= TWIN_S and abs(a.slant_m - b.slant_m) <= TWIN_SHARE * a.slant_m
            and abs(a.speed_ms - b.speed_ms) <= TWIN_SHARE * a.speed_ms)


def check(sp, tracks: Tracks, obs: np.ndarray, start_unix: float, used: set[str],
          c_at: Callable[[float], float]) -> Check:
    """Test every pass the tracks predict at obs, for a recording that began at start_unix.
    c_at gives the speed of sound for an aircraft that many metres above the listener: the
    pitch of a moving source is set by the air it is in, and that air is colder than the ground's."""
    from scipy.signal import find_peaks

    # per-frame median off: a gust or a truck lifts every pitch at once and is no tone
    level = sp.S - np.median(sp.S, axis=0, keepdims=True)
    duration = float(sp.t[-1])
    found: list[Pass] = []
    for seg in tracks.near(obs, start_unix, start_unix + duration, R_MAX_M):
        d = np.linalg.norm(seg.enu - obs, axis=1)
        heard, ratio = tone(seg.t, seg.enu, obs, c_at(height_above(seg.enu[int(np.argmin(d))], obs, tracks.lat0, tracks.lon0)))
        heard = heard - start_unix
        # a track that begins or ends at its nearest point is half a pass: find_peaks leaves those out
        for j in find_peaks(-d, height=-R_MAX_M, distance=APART_S)[0]:
            if not 0.0 <= heard[j] <= duration:
                continue
            one = Pass(icao=seg.icao, heard_s=float(heard[j]), slant_m=float(d[j]), speed_ms=float(seg.speed[j]),
                       used=seg.icao in used)
            # far, slow passes fall slowly: give them a longer look, up to the point the tone is flat again
            half = float(np.clip(1.5 * one.slant_m / max(one.speed_ms, 1.0), 40.0, 150.0))
            one.sweep, one.p, one.z, one.pitch_hz = _test(sp, level, heard, ratio, one.heard_s, half)
            near = (heard >= one.heard_s - half) & (heard <= one.heard_s + half)
            one.t_s, one.ratio = heard[near], ratio[near]
            found.append(one)
    found.sort(key=lambda q: q.heard_s)

    # the same aircraft can be in the data twice (its own broadcast and a ground radar's relay of it):
    # counted twice it would look like two confirmations, and a relay of a used flight is not new evidence
    others: list[Pass] = []
    for one in found:
        if one.p is None:
            continue
        if any(_one_aircraft(q, one) for q in found if q.used and q is not one):
            one.used = True
        if not one.used and not any(_one_aircraft(q, one) for q in others):
            others.append(one)
    return Check(passes=found, others=others)
