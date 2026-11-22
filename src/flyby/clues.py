"""Decide which UTC days and which part of the map to search."""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .model import CONUS, Clues, FlybyError, Media

BBox = tuple[float, float, float, float]

USER_AGENT = "flyby/0.1 (personal research tool)"
USA_NAMES = {"usa", "us", "conus", "united states"}
MAX_RANGE_DAYS = 7
HINT_RADIUS_DEG = 0.75
MIN_SPAN_DEG = 1.0
GEOCODE_PAD_DEG = 0.2


def geocode_area(name: str) -> tuple[BBox, str]:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": name, "format": "jsonv2", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            hits = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # URLError covers HTTP errors too; a bad body is just as unusable as no network
        raise FlybyError(f"Could not look up '{name}' ({exc}). Pass --bbox LATMIN LATMAX LONMIN LONMAX instead.") from exc
    if not hits:
        raise FlybyError(f"No place found called '{name}'. Try a bigger place name or pass --bbox.")

    south, north, west, east = (float(v) for v in hits[0]["boundingbox"])
    # a small town's box is too tight to hold the flights that were audible from it
    if north - south < MIN_SPAN_DEG:
        mid = (north + south) / 2
        south, north = mid - MIN_SPAN_DEG / 2, mid + MIN_SPAN_DEG / 2
    if east - west < MIN_SPAN_DEG:
        mid = (east + west) / 2
        west, east = mid - MIN_SPAN_DEG / 2, mid + MIN_SPAN_DEG / 2
    pad = GEOCODE_PAD_DEG
    bbox = (max(south - pad, -90.0), min(north + pad, 90.0), max(west - pad, -180.0), min(east + pad, 180.0))
    label = ", ".join(p.strip() for p in hits[0]["display_name"].split(",")[:2])
    return bbox, label


def _pick_area(media: Media, area_arg: str | None, bbox_arg: BBox | None) -> tuple[BBox, str, str]:
    if bbox_arg is not None:
        return bbox_arg, "custom box", "You gave the search box yourself, so that is the area searched."
    if area_arg is not None:
        if area_arg.strip().lower() in USA_NAMES:
            return CONUS, "continental USA", "You asked for the continental USA, so the whole of it is searched."
        bbox, label = geocode_area(area_arg)
        return bbox, label, f"You named {label}, so the search covers that place and a margin around it."
    if media.location_hint is not None:
        lat, lon = media.location_hint
        r = HINT_RADIUS_DEG
        # a degree of longitude shrinks towards the poles; widen it so the box is as far across as it is tall
        r_lon = r / max(math.cos(math.radians(lat)), 0.2)
        return ((max(lat - r, -90.0), min(lat + r, 90.0), max(lon - r_lon, -180.0), min(lon + r_lon, 180.0)),
                "near the GPS position in the file",
                "The file carries its own GPS position, so the search stays within about 80 km of it.")
    return CONUS, "continental USA", "Nothing in the video says where it was made, so the whole continental USA is searched."


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise FlybyError(f"'{text}' is not a date. Use YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD for a range.") from None


def _dedupe(days: list[date]) -> list[date]:
    return list(dict.fromkeys(days))


def find_clues(media: Media, date_arg: str | None = None, area_arg: str | None = None,
               bbox_arg: BBox | None = None, days_back: int = 2, today: date | None = None) -> Clues:
    bbox, area_label, area_reason = _pick_area(media, area_arg, bbox_arg)
    today = today or datetime.now(timezone.utc).date()
    # kept even when --date picks the days: a known start time is a second test of any match
    start_utc = media.started_utc

    if date_arg:
        if ".." in date_arg:
            first_s, last_s = date_arg.split("..", 1)
            first, last = _parse_day(first_s), _parse_day(last_s)
            if last < first:
                raise FlybyError("The date range ends before it starts.")
            if (last - first).days + 1 > MAX_RANGE_DAYS:
                raise FlybyError(f"That date range is longer than {MAX_RANGE_DAYS} days. Narrow it down.")
            local = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            what = f"the local days {first} to {last}"
        else:
            local = [_parse_day(date_arg)]
            what = f"the local day {local[0]}"
        # a local day straddles two UTC days; which neighbour depends on the side of Greenwich
        west = (bbox[2] + bbox[3]) / 2 < 0
        shift = timedelta(days=1 if west else -1)
        days = _dedupe(local + [d + shift for d in local])
        day_reason = (f"You gave {what} in the search area, which also touches the UTC day "
                      f"{'after' if west else 'before'}, so both are searched.")
    elif start_utc is not None:
        end = start_utc + timedelta(seconds=media.duration_s)
        n = (end.astimezone(timezone.utc).date() - start_utc.astimezone(timezone.utc).date()).days + 1
        first = start_utc.astimezone(timezone.utc).date()
        days = [first + timedelta(days=i) for i in range(n)]
        day_reason = (f"The recording says it began at {start_utc.astimezone(timezone.utc):%H:%M} UTC on "
                      f"{first}, so the search covers the days it ran through.")
    elif media.uploaded is not None:
        days = [media.uploaded - timedelta(days=i) for i in range(days_back + 1)]
        day_reason = (f"The video was uploaded on {media.uploaded} and may have been recorded earlier, "
                      f"so the search starts there and goes back {days_back} days.")
    else:
        raise FlybyError("Nothing in the video says which day it was recorded. Pass --date YYYY-MM-DD.")

    # the flight archive is published about a day late
    cutoff = today - timedelta(days=1)
    kept = [d for d in days if d <= cutoff]
    if len(kept) < len(days):
        day_reason += (f" Days after {cutoff} were left out because the flight archive is published "
                       "about a day late.")
    if not kept:
        raise FlybyError(f"Flight data is only available up to {cutoff}, and every day to search is later than that. "
                         "Try again tomorrow or give an earlier --date.")

    return Clues(days=kept, bbox=bbox, area_label=area_label, day_reason=day_reason,
                 area_reason=area_reason, start_utc=start_utc)
