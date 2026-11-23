"""Decide which UTC days and which part of the map to search."""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from .hints import STATES
from .ingest import parse_coords
from .model import CONUS, Clues, FlybyError, Hint, Media, PlaceNotFound

BBox = tuple[float, float, float, float]

USER_AGENT = "flyby/0.1 (personal research tool)"
USA_NAMES = {"usa", "us", "conus", "united states"}
MAX_RANGE_DAYS = 7
MAX_MAP_QUESTIONS = 6       # when checking the towns a video names: a few seconds at most
# The winner is judged against the wrong answers in the same search, so a search needs a crowd
# of them. A box the size of one town holds too few flights for that: the search always covers
# at least this much, and the town itself becomes a second test (Clues.focus).
MIN_SPAN_DEG = 3.0
GEOCODE_PAD_DEG = 0.2
FOCUS_MAX_SPAN_DEG = 1.5    # a named place smaller than this is a town, not a region
FOCUS_PAD_DEG = 0.25        # a microphone "in" a town can be this far outside its boundary, about 30 km
STREAM_TOL_S = 180.0        # a replay's own start time is good to a few minutes
TYPED_TOL_S = 600.0         # a start time given with no word on how sure it is
STATE_NAMES = {name.lower() for name in STATES.values()}


def _around(lat: float, lon: float, half_deg: float) -> BBox:
    # a degree of longitude shrinks towards the poles; widen it so the box is as far across as it is tall
    half_lon = half_deg / max(math.cos(math.radians(lat)), 0.2)
    return (max(lat - half_deg, -90.0), min(lat + half_deg, 90.0), max(lon - half_lon, -180.0), min(lon + half_lon, 180.0))


def _map_search(query: dict[str, str], name: str) -> list[dict]:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({**query, "format": "jsonv2", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # URLError covers HTTP errors too; a bad body is just as unusable as no network
        raise FlybyError(f"Could not look up '{name}' ({exc}). Pass --bbox LATMIN LATMAX LONMIN LONMAX instead.") from exc


def geocode_area(name: str) -> tuple[BBox, str, BBox | None]:
    """(box to search, label, the place itself when it is a town-sized part of that box)."""
    town, _, state = (part.strip() for part in name.rpartition(","))
    state = STATES.get(state.upper(), state)
    hits, label = [], None
    if name.strip().lower() in STATE_NAMES:
        # "New York" and "Washington" are a city to a map search unless it is told they are states
        hits = _map_search({"state": name, "country": "United States"}, name)
    elif town and state.lower() in STATE_NAMES:
        # asked in plain words, "Huron, Ohio" comes back as Huron County, which is not where the town of Huron is
        hits = _map_search({"city": town, "state": state, "country": "United States"}, name)
        if hits:
            label = f"{hits[0]['display_name'].split(',')[0].strip()}, {state.title()}"
        else:
            time.sleep(1.1)     # the map service asks for no more than one question a second
    if not hits:
        hits = _map_search({"q": name}, name)
    if not hits:
        raise PlaceNotFound(f"No place found called '{name}'. Try a bigger place name or pass --bbox.")

    south, north, west, east = (float(v) for v in hits[0]["boundingbox"])
    label = label or ", ".join(p.strip() for p in hits[0]["display_name"].split(",")[:2])
    focus = None
    if north - south < FOCUS_MAX_SPAN_DEG and east - west < FOCUS_MAX_SPAN_DEG:
        pad = FOCUS_PAD_DEG
        focus = (south - pad, north + pad, west - pad / max(math.cos(math.radians(south)), 0.2),
                 east + pad / max(math.cos(math.radians(south)), 0.2))
    if north - south < MIN_SPAN_DEG:
        mid = (north + south) / 2
        south, north = mid - MIN_SPAN_DEG / 2, mid + MIN_SPAN_DEG / 2
    if east - west < MIN_SPAN_DEG:
        mid = (east + west) / 2
        west, east = mid - MIN_SPAN_DEG / 2, mid + MIN_SPAN_DEG / 2
    pad = GEOCODE_PAD_DEG
    bbox = (max(south - pad, -90.0), min(north + pad, 90.0), max(west - pad, -180.0), min(east + pad, 180.0))
    return bbox, label, focus


def _pick_area(media: Media, area_arg: str | None, bbox_arg: BBox | None, why: str | None, box: tuple[str, str] | None = None):
    """(box, label, one-line reason, focus box or None, focus label or None)."""
    if bbox_arg is not None:
        if box:
            return bbox_arg, box[0], f"{box[1]} So only {box[0]} is searched.", None, None
        return bbox_arg, "custom box", "You gave the search box yourself, so that is the area searched.", None, None
    spot = parse_coords(area_arg) if area_arg else media.location_hint
    if spot is not None:
        lat, lon = spot
        said = "You gave a position" if area_arg else "The file carries its own GPS position"
        return (_around(lat, lon, MIN_SPAN_DEG / 2), f"near {lat:.3f}, {lon:.3f}",
                f"{said}, so the search covers about 150 km around it and expects the answer within about 30 km of it.",
                _around(lat, lon, FOCUS_PAD_DEG), "that position")
    if area_arg is not None:
        if area_arg.strip().lower() in USA_NAMES:
            return CONUS, "continental USA", "You asked for the continental USA, so the whole of it is searched.", None, None
        bbox, label, focus = geocode_area(area_arg)
        said = f"{why[:1].upper()}{why[1:]}" if why else f"You named {label}"
        if focus is None:
            return bbox, label, f"{said}, so the search covers {label} and a margin around it.", None, None
        return (bbox, f"around {label}", f"{said}, so the search covers about 150 km around {label} "
                "and expects the answer close to it.", focus, label)
    return (CONUS, "continental USA", "Nothing in the video says where it was made, so the whole continental USA is searched.",
            None, None)


def confirm_places(hints: list[Hint], pause_s: float = 1.1) -> list[Hint]:
    """Let the map say which reading of each "Town, State" hint is a real town.

    A title gives "LIVE Bowling Green, KY". Cutting words off by rule goes wrong both ways:
    "LIVE Bowling Green" is no town, and "Green, Kentucky" is a real hamlet 300 km from Bowling
    Green. So every reading is asked of the map, longest first, and the first real town is the
    one meant. A hint whose town the map does not know keeps its state, which is still true.
    With no map to ask, the hints come back as the rules read them."""
    out: list[Hint] = []
    asked = 0
    for h in hints:
        if h.kind != "place" or not h.towns or asked >= MAX_MAP_QUESTIONS:
            out.append(h)
            continue
        state = h.value.rsplit(", ", 1)[-1]
        town = None
        try:
            for reading in h.towns:
                if asked:
                    time.sleep(pause_s)     # the map service asks for no more than one question a second
                asked += 1
                hits = _map_search({"city": reading, "state": state, "country": "United States"}, h.value)
                if hits:
                    town = hits[0].get("name") or reading
                    break
        except FlybyError:
            return hints
        if town:
            out.append(replace(h, value=f"{town}, {state}", towns=()))
        else:
            # the publisher's own words still name the state. Writing in a picture may be an advert or a lorry
            out.append(replace(h, value=state, towns=(), strength="weak" if h.from_picture else h.strength,
                               because=f"{h.because} (the map knows no town of that name, so only the state is used)"))
    seen: set[tuple[str, str]] = set()
    return [h for h in out if not ((h.kind, h.value) in seen or seen.add((h.kind, h.value)))]


def local_days(text: str) -> list[date]:
    """The days a --date names: one day, or every day of a range A..B."""
    if ".." not in text:
        return [_parse_day(text)]
    first_s, last_s = text.split("..", 1)
    first, last = _parse_day(first_s), _parse_day(last_s)
    if last < first:
        raise FlybyError("The date range ends before it starts.")
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise FlybyError(f"That date range is longer than {MAX_RANGE_DAYS} days. Narrow it down.")
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise FlybyError(f"'{text}' is not a date. Use YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD for a range.") from None


def _dedupe(days: list[date]) -> list[date]:
    return list(dict.fromkeys(days))


def _days_from(start_utc: datetime, duration_s: float) -> list[date]:
    first = start_utc.astimezone(timezone.utc).date()
    last = (start_utc + timedelta(seconds=duration_s)).astimezone(timezone.utc).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def find_clues(media: Media, date_arg: str | None = None, area_arg: str | None = None,
               bbox_arg: BBox | None = None, days_back: int = 2, today: date | None = None,
               start_arg: datetime | None = None, start_tol_s: float | None = None, area_why: str | None = None,
               box: tuple[str, str] | None = None) -> Clues:
    """start_arg: when the person says the recording began (it beats the source's own time: they
    may know the replay was cut). area_why: a few words on where area_arg came from, when it was
    read off the video rather than typed. box: (name, the sentence that says where it came from)
    for a bbox_arg that a clue gave, such as a time zone read off the camera's clock."""
    bbox, area_label, area_reason, focus, focus_label = _pick_area(media, area_arg, bbox_arg, area_why, box)
    today = today or datetime.now(timezone.utc).date()
    # kept even when --date picks the days: a known start time is a second test of any match
    start_utc = start_arg or media.started_utc
    # a tolerance the person gave also loosens a stream's own start time: they may know the replay was cut
    tol = start_tol_s or (TYPED_TOL_S if start_arg else STREAM_TOL_S)

    if start_arg is not None:
        if date_arg:
            local_days(date_arg)        # the start time picks the days, but a day that cannot be read is still an error
        days = _days_from(start_arg, media.duration_s)
        day_reason = (f"You said it began at {start_arg.astimezone(timezone.utc):%H:%M} UTC on {days[0]}, "
                      "so the search covers the days it ran through.")
    elif date_arg:
        local = local_days(date_arg)
        what = f"the local days {local[0]} to {local[-1]}" if len(local) > 1 else f"the local day {local[0]}"
        # a local day straddles two UTC days; which neighbour depends on the side of Greenwich
        west = (bbox[2] + bbox[3]) / 2 < 0
        shift = timedelta(days=1 if west else -1)
        days = _dedupe(local + [d + shift for d in local])
        day_reason = (f"You gave {what} in the search area, which also touches the UTC day "
                      f"{'after' if west else 'before'}, so both are searched.")
    elif start_utc is not None:
        days = _days_from(start_utc, media.duration_s)
        day_reason = (f"The recording says it began at {start_utc.astimezone(timezone.utc):%H:%M} UTC on "
                      f"{days[0]}, so the search covers the days it ran through.")
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

    return Clues(days=kept, bbox=bbox, area_label=area_label, day_reason=day_reason, area_reason=area_reason,
                 start_utc=start_utc, start_tol_s=tol, focus=focus, focus_label=focus_label)
