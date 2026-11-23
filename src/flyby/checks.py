"""Links that let the person check an answer with other instruments, each opened at the spot and the moment.

Every link form here was confirmed on 2026-11-22, from the service's own documentation where it
has one, and by loading it in headless Chrome at the reference spot in Huron, Ohio:

- Google Street View, satellite and pin: Google's "Maps URLs" (api=1). A spot with no Street View
  within a few metres says so; the same link at a busy street opens the panorama.
- Apple Look Around and place: Apple's "Unified Map URLs". In Chrome the web map shows the place;
  on a Mac or iPhone the link opens Maps itself.
- Bing aerial: Microsoft's "Create a Custom Map URL". Its bird's eye styles (o, b) fall back to the
  road map on bing.com now, so they are left out.
- Flight replay: tar1090's README-query (icao, showTrace, startTime, endTime, timestamp). adsb.lol
  replays a past day: 41 days back loaded the track, 52 days back loaded nothing. ADS-B Exchange
  refuses past days without an account; airplanes.live and adsb.fi block headless loads, so neither
  is confirmed.
- SunCalc: read from suncalc.org's own script. The time in the link is the clock at the spot, not
  UTC: the same link gave the same sun with the machine set to UTC and to Tokyo, and the page
  showed UTC-4 for Huron in September.
- OpenStreetMap, Mapillary and KartaView: no written link form found; each loaded centred on the spot.
- YouTube t=: the player started at exactly that second.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from .model import Clues, Media, Result

ZONE_URL = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "flyby/0.1 (personal research tool)"
REPLAY_DAYS = 40            # adsb.lol's map replayed a track 41 days old and nothing at 52
REPLAY_WINDOW = timedelta(minutes=10)

# Only these verdicts put a point on the map; the others claim nothing about where.
_POSITIONED = ("located", "not sure")


@dataclass
class Check:
    """One link the person can open to compare the answer with something flyby did not use."""
    name: str          # "Street View"
    url: str
    look_for: str      # one short sentence: what to compare


def _url(base: str, params: dict, fragment: str = "") -> str:
    # commas and colons stay readable: these are the exact forms the services were checked with
    query = urllib.parse.urlencode(params, safe=",:~")
    return base + "?" + query + (f"#{fragment}" if fragment else "")


@lru_cache(maxsize=64)
def _zone(lat: float, lon: float) -> str | None:
    """The time zone name at a spot, from Open-Meteo, or None when it cannot be had."""
    url = _url(ZONE_URL, {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            name = json.load(resp)["timezone"]
        ZoneInfo(name)
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        # a sun link without the right clock is still worth having; it says the UTC time instead
        return None
    return name


def _moment(result: Result, clues: Clues) -> datetime | None:
    """The first plane heard, else the start of the recording: a time the video really shows."""
    heard = sorted(f.heard_utc for f in result.flights if f.heard_utc)
    when = heard[0] if heard else (result.recording_start_utc or clues.start_utc)
    return when.astimezone(timezone.utc) if when else None


def _hm(when: datetime) -> str:
    return when.strftime("%H:%M")


def _sun(lat: float, lon: float, moment: datetime | None, day: date | None) -> Check | None:
    base = "https://www.suncalc.org/#/"
    spot = f"{lat:.5f},{lon:.5f},17"
    if moment is None:
        if day is None:
            return None
        return Check("Sun and shadows", f"{base}{spot}/{day:%Y.%m.%d}",
                     "Pick the time of the video: do the shadows point the way the sun says?")
    # rounded like every other question flyby puts to Open-Meteo: about 10 km, which a time zone outlasts
    name = _zone(round(lat, 1), round(lon, 1))
    if name is None:
        return Check("Sun and shadows", f"{base}{spot}/{moment:%Y.%m.%d}",
                     f"Set the clock to {_hm(moment)} UTC in local time: do the shadows in the video point the same way?")
    local = moment.astimezone(ZoneInfo(name))
    return Check("Sun and shadows", f"{base}{spot}/{local:%Y.%m.%d}/{_hm(local)}/1/3",
                 f"The sun at {_hm(local)} local time: do the shadows in the video point the same way?")


def _replay(result: Result, today: date) -> list[Check]:
    out = []
    for f in result.flights:
        if f.heard_utc is None:
            continue
        heard = f.heard_utc.astimezone(timezone.utc)
        if (today - heard.date()).days > REPLAY_DAYS:
            continue
        # the replay only shows one UTC day, so the window stops at its edges
        start = max(heard - REPLAY_WINDOW, datetime.combine(heard.date(), time(0, 0, 0), timezone.utc))
        end = min(heard + REPLAY_WINDOW, datetime.combine(heard.date(), time(23, 59, 59), timezone.utc))
        url = _url("https://adsb.lol/", {
            "icao": f.icao.lower(), "showTrace": heard.date().isoformat(),
            "startTime": start.strftime("%H:%M:%S"), "endTime": end.strftime("%H:%M:%S"),
            "timestamp": int(heard.timestamp()),
        })
        who = f.registration or f.icao
        out.append(Check(f"Flight replay: {who}", url,
                         f"Its track should pass close to the pin at {_hm(heard)} UTC."))
    return out


def _video(media: Media, moment: datetime | None) -> Check | None:
    video = media.video or {}
    if video.get("site") != "youtube" or not video.get("id") or moment is None or media.started_utc is None:
        return None
    second = int(round((moment - media.started_utc.astimezone(timezone.utc)).total_seconds()))
    if second < 0 or (media.duration_s and second > media.duration_s):
        return None
    url = _url("https://www.youtube.com/watch", {"v": video["id"], "t": f"{second}s"})
    return Check("The video at that moment", url, "Listen: the first plane should pass in the next few seconds.")


def check_links(result: Result, clues: Clues, media: Media, *, today: date | None = None) -> list[Check]:
    """Links to check the answer with, most useful first. Empty when the answer has no position."""
    if result.verdict not in _POSITIONED or result.lat is None or result.lon is None:
        return []
    lat, lon = result.lat, result.lon
    here = f"{lat:.5f},{lon:.5f}"
    today = today or datetime.now(timezone.utc).date()
    moment = _moment(result, clues)

    checks = [Check("Street View", _url("https://www.google.com/maps/@", {"api": 1, "map_action": "pano", "viewpoint": here}),
                    "Do the roads, poles and buildings match the video? No pictures here: step onto the nearest road.")]
    video = _video(media, moment)
    if video:
        checks.append(video)
    checks.append(Check("Satellite", _url("https://www.google.com/maps/@", {
        "api": 1, "map_action": "map", "center": here, "zoom": 19, "basemap": "satellite"}),
        "From above: do the roads, rails and water sit where the video shows them?"))
    checks += _replay(result, today)
    sun = _sun(lat, lon, moment, result.day)
    if sun:
        checks.append(sun)
    checks += [
        Check("Apple Look Around", _url("https://maps.apple.com/look-around", {"coordinate": here}),
              "Apple's own street pictures, often newer than Google's."),
        Check("Mapillary", _url("https://www.mapillary.com/app/", {"lat": f"{lat:.5f}", "lng": f"{lon:.5f}", "z": 17}),
              "Street photos taken by the public: look for the same corner."),
        Check("KartaView", f"https://kartaview.org/map/@{here},17z",
              "More street photos by the public, sometimes where Google has none."),
        Check("Bing aerial", _url("https://www.bing.com/maps", {"cp": f"{lat:.5f}~{lon:.5f}", "lvl": 19, "style": "a"}),
              "A second picture from above, taken on another day."),
        Check("Google Maps", _url("https://www.google.com/maps/search/", {"api": 1, "query": here}),
              "Names of the roads and places next to the pin."),
        Check("Apple Maps", _url("https://maps.apple.com/place", {"coordinate": here, "name": "flyby", "map": "satellite"}),
              "The pin on Apple's map."),
        Check("OpenStreetMap", _url("https://www.openstreetmap.org/", {"mlat": f"{lat:.5f}", "mlon": f"{lon:.5f}"},
                                    f"map=18/{lat:.5f}/{lon:.5f}"),
              "Paths, rail lines and fences drawn by hand."),
    ]
    return checks
