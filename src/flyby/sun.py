"""Where the sun was, and what that says about the place.

Two uses. A video that runs through dusk or dawn, with a known clock, pins the moment the
sun reached a certain height, and that is only true along one strip of the country. And once
there is an answer, the light in the picture should fit the sun at that spot and time.

No network here: the sun's position is worked out from NOAA's solar equations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from .model import CONUS


def elevation(lat, lon, when: datetime):
    """The sun's height above the horizon in degrees, seen from (lat, lon) at `when`.

    NOAA's solar position equations (the ones behind its solar calculator), for the centre of
    the sun with no bending by the air: near the horizon the sun looks about half a degree
    higher than this. lat and lon may be numpy arrays of the same shape."""
    when = when.astimezone(timezone.utc)
    jd = when.timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    l0 = np.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0)
    m = np.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    centre = (np.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
              + np.sin(2 * m) * (0.019993 - 0.000101 * t) + np.sin(3 * m) * 0.000289)
    omega = np.radians(125.04 - 1934.136 * t)
    apparent = np.radians(np.degrees(l0) + centre - 0.00569 - 0.00478 * np.sin(omega))
    obliquity = np.radians(23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
                           + 0.00256 * np.cos(omega))
    decl = np.arcsin(np.sin(obliquity) * np.sin(apparent))
    y = np.tan(obliquity / 2) ** 2
    eq_time_min = 4 * np.degrees(y * np.sin(2 * l0) - 2 * e * np.sin(m) + 4 * e * y * np.sin(m) * np.cos(2 * l0)
                                 - 0.5 * y * y * np.sin(4 * l0) - 1.25 * e * e * np.sin(2 * m))
    minutes = when.hour * 60 + when.minute + (when.second + when.microsecond / 1e6) / 60.0
    hour_angle = np.radians((minutes + eq_time_min + 4 * np.asarray(lon, dtype=float)) / 4.0 - 180.0)
    phi = np.radians(np.asarray(lat, dtype=float))
    cos_zenith = np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(hour_angle)
    return np.degrees(np.arcsin(np.clip(cos_zenith, -1.0, 1.0)))


def _falling(lat, lon, when: datetime):
    """Whether the sun was on its way down there at that moment."""
    return elevation(lat, lon, when + timedelta(minutes=10)) < elevation(lat, lon, when)


# How the picture is judged day-like or night-like. Many cameras hold brightness steady by
# day and only give way once dusk is well on; many then switch to grey night vision.
SMOOTH_S = 420.0          # a running median this long wipes out a passing train, headlights or a cloud's shadow
STEADY_S = 1200.0         # each side of a change must hold for this long, or it is not a change
RAMP_S = 900.0            # the fade itself takes up to twice this; the steady stretches are measured outside it
MIN_BRIGHT_STEP = 0.08    # day and night brightness must differ by this much to read a change off brightness
MIN_COLOUR_STEP = 0.04    # the same for colour, used when the night side is grey
GREY = 0.025              # colour below this is a grey picture: night vision


@dataclass
class Change:
    """The picture turned from day to night (dusk) or night to day (dawn)."""
    kind: str          # "dusk" | "dawn"
    utc: datetime
    t_s: float         # seconds into the video


def _smooth(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    half = SMOOTH_S / 2
    lo = np.searchsorted(t, t - half, side="left")
    hi = np.searchsorted(t, t + half, side="right")
    return np.array([np.median(v[a:b]) for a, b in zip(lo, hi)])


def _steps(t: np.ndarray, s: np.ndarray, min_step: float, grey: bool) -> list[tuple[str, float]]:
    """Each lasting move from one steady level to another, with the time it crossed halfway.

    The two levels are measured right beside each moment, not over the whole video: a camera's
    daytime level drifts over 12 hours by more than its last hour of day differs from night."""
    # moments that pass, grouped when they are one change: [kind, first, last, biggest step, halfway level]
    groups: list[list] = []
    slack = STEADY_S / 10
    for tc in t:
        # a change too near either end of the video has no steady stretch on that side to prove it
        if tc - RAMP_S - STEADY_S < t[0] - slack or tc + RAMP_S + STEADY_S > t[-1] + slack:
            continue
        before = s[(t >= tc - RAMP_S - STEADY_S) & (t <= tc - RAMP_S)]
        after = s[(t >= tc + RAMP_S) & (t <= tc + RAMP_S + STEADY_S)]
        if len(before) < 3 or len(after) < 3:
            continue
        hi, lo = np.median(before), np.median(after)
        step = hi - lo
        if abs(step) < min_step or np.ptp(before) > abs(step) / 3 or np.ptp(after) > abs(step) / 3:
            continue
        if grey and min(hi, lo) >= GREY:
            continue
        kind = "dusk" if step > 0 else "dawn"
        if groups and groups[-1][0] == kind and tc - groups[-1][2] <= 2 * RAMP_S:
            g = groups[-1]
            g[2] = float(tc)
            if abs(step) > g[3]:
                g[3], g[4] = abs(step), (hi + lo) / 2
        else:
            groups.append([kind, float(tc), float(tc), abs(step), (hi + lo) / 2])
    out = []
    for kind, first, last, _, mid in groups:
        # where the picture itself crossed halfway, nearest the middle of the moments that passed
        centre = (first + last) / 2
        near = np.flatnonzero((t >= first - RAMP_S) & (t <= last + RAMP_S))
        cross = [float(t[i] + (mid - s[i]) / (s[i + 1] - s[i]) * (t[i + 1] - t[i]))
                 for i in near[:-1] if (s[i] - mid) * (s[i + 1] - mid) <= 0 and s[i] != s[i + 1]]
        out.append((kind, min(cross, key=lambda x: abs(x - centre)) if cross else centre))
    return out


def changes(light: list[tuple[float, float, float]], started_utc: datetime) -> list[Change]:
    """Every moment the picture goes from day-like to night-like and stays so, or the reverse.

    `light` is (seconds into the video, brightness, colour) as on `model.Frame`. A switch to
    grey night vision is read off colour, which flips sharply; otherwise off brightness."""
    if len(light) < 3:
        return []
    rows = np.array(sorted(light), dtype=float)
    t = rows[:, 0]
    colour = _smooth(t, rows[:, 2])
    found = _steps(t, colour, MIN_COLOUR_STEP, grey=True)
    if not found:
        found = _steps(t, _smooth(t, rows[:, 1]), MIN_BRIGHT_STEP, grey=False)
    start = started_utc.astimezone(timezone.utc)
    return [Change(kind, datetime.fromtimestamp(start.timestamp() + t_s, timezone.utc), t_s) for kind, t_s in found]


# The sun's height when the picture changes, measured on real replays of the Main Street
# Railfan camera in Huron, Ohio (41.395 N 82.555 W), whose clock is known:
#   dusk 2026-10-26 23:01:25 UTC  -6.02 degrees      dawn 2026-10-27 11:21:35 UTC  -7.08
#   dusk 2026-10-27 22:59:42 UTC  -5.95              dawn 2026-10-28 11:28:10 UTC  -6.07
#   dusk 2026-10-28 23:02:36 UTC  -6.71
# so -7.1 to -6.0 on that camera. Run over the six whole 12 h replays those come from, it
# finds those three dusks and nothing else. Only one camera was measured: others give way to
# the dark at other light levels, and some switch to night vision early, so the band leaves
# four degrees or more either side.
BAND = (-11.0, -2.0)
GRID = 0.25               # degrees between the points tried
MAX_SHARE = 0.8           # a box this much of the area searched would narrow nothing


@dataclass
class Strip:
    """The part of the map where the sun was at the right height at every change."""
    bbox: tuple[float, float, float, float]
    share: float       # of the continental USA's area, 0 to 1
    why: str           # one plain sentence for the person


def _area(box: tuple[float, float, float, float]) -> float:
    latmin, latmax, lonmin, lonmax = box
    return (np.sin(np.radians(latmax)) - np.sin(np.radians(latmin))) * (lonmax - lonmin)


def _lat(lat: float) -> str:
    return f"{abs(lat):.0f} {'N' if lat >= 0 else 'S'}"


def _lon(lon: float) -> str:
    return f"{abs(lon):.0f} {'W' if lon < 0 else 'E'}"


def strip(found: list[Change], within=CONUS) -> Strip | None:
    """The box around every place where the sun was between BAND's heights at each change.
    None when no place fits, or when the box would cover most of `within`."""
    if not found:
        return None
    latmin, latmax, lonmin, lonmax = within
    lats = np.arange(latmin, latmax + GRID / 2, GRID)
    lons = np.arange(lonmin, lonmax + GRID / 2, GRID)
    lat, lon = np.meshgrid(lats, lons, indexing="ij")
    keep = np.ones(lat.shape, dtype=bool)
    for c in found:
        el = elevation(lat, lon, c.utc)
        # a dusk is the sun going down. The same height on its way up is the far side of the night,
        # and a clock half a day out would put the strip there
        keep &= (el >= BAND[0]) & (el <= BAND[1]) & (_falling(lat, lon, c.utc) == (c.kind == "dusk"))
    if not keep.any():
        return None
    half = GRID / 2
    box = (max(latmin, float(lat[keep].min()) - half), min(latmax, float(lat[keep].max()) + half),
           max(lonmin, float(lon[keep].min()) - half), min(lonmax, float(lon[keep].max()) + half))
    if _area(box) > MAX_SHARE * _area(within):
        return None
    first = found[0]
    went = "dark" if first.kind == "dusk" else "light"
    when = f"{first.utc:%H:%M} UTC on {first.utc:%Y-%m-%d}"
    more = f" (and {len(found) - 1} more change{'s' if len(found) > 2 else ''} like it)" if len(found) > 1 else ""
    span = "" if (box[0], box[1]) == (latmin, latmax) else f" and {_lat(box[0])} to {_lat(box[1])}"
    why = (f"The picture went {went} at {when}{more}, when the sun was just under the horizon "
           f"only in a strip from {_lon(box[2])} to {_lon(box[3])}{span}.")
    return Strip(box, float(min(1.0, _area(box) / _area(CONUS))), why)


# Clear-cut light only. Street lights, snow and night vision fool anything finer.
UP = 8.0                  # the sun this high gives daylight, even under heavy cloud
DOWN = -12.0              # the sun this low leaves the sky dark
NIGHT_COLOUR = 0.02       # a picture this grey is night vision
NIGHT_BRIGHT = 0.12       # or this dark is night
DAY_COLOUR = 0.10         # a picture this coloured and this bright is daylight
DAY_BRIGHT = 0.35
MOST = 0.8                # share of the pictures that must agree before a clash is claimed
FEW = 5                   # fewer pictures than this in a part of the video say nothing


def _when(seconds: float) -> str:
    seconds = int(round(seconds))
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def daylight(lat: float, lon: float, light: list[tuple[float, float, float]], started_utc: datetime) -> list[str]:
    """Plain sentences on whether the light in the picture fits the sun at (lat, lon).
    A clash is claimed only when it is clear-cut; otherwise the light fits, or it cannot tell."""
    if not light:
        return ["There are no pictures to check the light against."]
    start = started_utc.astimezone(timezone.utc).timestamp()
    rows = np.array(sorted(light), dtype=float)
    t, bright, colour = rows[:, 0], rows[:, 1], rows[:, 2]
    el = np.array([float(elevation(lat, lon, datetime.fromtimestamp(start + s, timezone.utc))) for s in t])
    night = (colour < NIGHT_COLOUR) | (bright < NIGHT_BRIGHT)
    day = (colour >= DAY_COLOUR) & (bright >= DAY_BRIGHT)
    up, down = el >= UP, el <= DOWN
    out, clash = [], False
    if up.sum() >= FEW and night[up].mean() >= MOST:
        clash = True
        out.append(f"The sun was well up there for {_when(float(np.ptp(t[up])))} of the video, "
                   "but the picture looks like night. Check the place and the time.")
    if down.sum() >= FEW and day[down].mean() >= MOST:
        clash = True
        out.append(f"The sun was far below the horizon there for {_when(float(np.ptp(t[down])))} of the video, "
                   "but the picture looks like bright daylight. Check the place and the time.")
    for c in changes(light, started_utc):
        h = float(elevation(lat, lon, c.utc))
        went = "dark" if c.kind == "dusk" else "light"
        if h >= UP or h <= -18.0:
            clash = True
            where = f"{h:.0f} degrees up" if h > 0 else f"{abs(h):.0f} degrees under the horizon"
            out.append(f"The picture went {went} {_when(c.t_s)} in, but the sun there was {where}. "
                       "Check the place and the time.")
        elif bool(_falling(lat, lon, c.utc)) != (c.kind == "dusk"):
            # the right height on the wrong side of the night
            clash = True
            out.append(f"The picture went {went} {_when(c.t_s)} in, but the sun there was "
                       f"{'coming up' if c.kind == 'dusk' else 'going down'}. Check the place and the time.")
        elif BAND[0] <= h <= BAND[1]:
            out.append(f"The picture went {went} {_when(c.t_s)} in, as the sun there was {abs(h):.0f} degrees "
                       "under the horizon. That fits.")
    if clash:
        return out
    if up.sum() < FEW and down.sum() < FEW and not out:
        return ["The sun was low the whole time (dawn or dusk), so the light says nothing either way."]
    fits_up = up.sum() < FEW or night[up].mean() < 1 - MOST
    fits_down = down.sum() < FEW or day[down].mean() < 1 - MOST
    if fits_up and fits_down:
        parts = []
        if up.sum() >= FEW:
            parts.append("is not dark while the sun was up")
        if down.sum() >= FEW:
            parts.append("is not bright daylight while the sun was down")
        if parts:
            out.insert(0, f"The light fits the sun at that spot: the picture {' and '.join(parts)}.")
        return out
    out.insert(0, "The light is mixed, so flyby cannot tell whether it fits the sun at that spot.")
    return out
