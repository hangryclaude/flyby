"""The air the sound came through, from Open-Meteo's free weather archive. No key.

The pitch a moving aircraft is heard at depends on the speed of sound in the air around it,
and that follows the temperature up there, not the temperature on the ground. On the
reference clip the ground was at 26 C. One matched aircraft passed 1,050 m up, in air at
16 C; the other passed 5,500 m up, in air far below freezing; a helicopter 270 m up was in
air at 23 C.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
USER_AGENT = "flyby/0.1 (personal research tool)"
LEVELS = (1000, 925, 850, 700, 600, 500, 400)   # hPa: from the ground up to about 7 km, where turboprops cruise
CRUISE_M = 1000.0                   # a usual height above the ground for the small aircraft the method hears
STILL_AIR_C = 15.0                  # what the engine assumes when nothing is known


@dataclass(frozen=True)
class Air:
    """Temperature against height above the ground, at one place and hour."""
    heights_m: tuple[float, ...]
    temps_c: tuple[float, ...]

    def temp_at(self, height_m: float) -> float:
        return float(np.interp(height_m, self.heights_m, self.temps_c))


@lru_cache(maxsize=64)
def _hour(lat: float, lon: float, day: str, hour: int) -> Air | None:
    names = ["temperature_2m"] + [f"{kind}_{level}hPa" for level in LEVELS for kind in ("temperature", "geopotential_height")]
    url = URL + "?" + urllib.parse.urlencode({"latitude": lat, "longitude": lon, "start_date": day, "end_date": day,
                                              "hourly": ",".join(names), "timezone": "UTC"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.load(resp)
        ground, hourly = float(body["elevation"]), body["hourly"]
        points = [(2.0, hourly["temperature_2m"][hour])]
        for level in LEVELS:
            heights, temps = hourly.get(f"geopotential_height_{level}hPa"), hourly.get(f"temperature_{level}hPa")
            if heights and temps and heights[hour] is not None:
                points.append((heights[hour] - ground, temps[hour]))
    except (OSError, http.client.HTTPException, ValueError, KeyError, IndexError, TypeError):
        # no network, a connection dropped half way, a day the archive does not hold, or a reply
        # in another shape: all mean "not known". The temperature is never worth losing a search for.
        return None
    # a pressure level below the ground is the model's guess at air that is not there
    points = sorted((h, t) for h, t in points if t is not None and (h == 2.0 or h > 50.0))
    if len(points) < 2:
        return None
    return Air(tuple(h for h, _ in points), tuple(float(t) for _, t in points))


def fetch_air(lat: float, lon: float, when: datetime) -> Air | None:
    """The temperature profile nearest that place and hour, or None when it cannot be had.
    The place is rounded to a tenth of a degree: weather is the same across 10 km, and the
    archive has no need to know the exact spot."""
    when = when.astimezone(timezone.utc)
    return _hour(round(lat, 1), round(lon, 1), when.date().isoformat(), when.hour)
