"""Read the publisher's own words about a video for clues to where and when it was filmed.

No network and no guessing models: plain patterns with guards. A missed clue costs the person one question;
an invented one sends the search to the wrong place, so every rule here leans towards saying nothing.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .model import Hint

HEAD_CHARS = 600                       # a description's first lines are about this video; the rest is boilerplate
ARCHIVE_START = date(2023, 1, 1)       # the flight archive has nothing earlier
RANK = {"stated": 0, "likely": 1, "weak": 2}

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}
COUNTRIES = {
    "canada": "Canada", "mexico": "Mexico", "uk": "United Kingdom", "u.k.": "United Kingdom",
    "united kingdom": "United Kingdom", "great britain": "United Kingdom", "england": "England",
    "scotland": "Scotland", "wales": "Wales", "ireland": "Ireland", "germany": "Germany", "france": "France",
    "spain": "Spain", "portugal": "Portugal", "italy": "Italy", "netherlands": "Netherlands",
    "belgium": "Belgium", "switzerland": "Switzerland", "austria": "Austria", "poland": "Poland",
    "sweden": "Sweden", "norway": "Norway", "denmark": "Denmark", "finland": "Finland", "iceland": "Iceland",
    "australia": "Australia", "new zealand": "New Zealand", "japan": "Japan", "south korea": "South Korea",
    "philippines": "Philippines", "brazil": "Brazil", "argentina": "Argentina", "south africa": "South Africa",
}
# names that are just as often a person, a team or the country Georgia: they need a place word next to them
AMBIGUOUS = {"georgia", "virginia", "washington", "indiana", "montana", "nevada", "carolina"}
COMPASS = (r"(?:north|south|east|west|northeast|northwest|southeast|southwest|northern|southern|eastern|western|"
           r"northeastern|northwestern|southeastern|southwestern|central|upstate|downstate|coastal|rural|"
           r"ne|nw|se|sw)")
PLACE_WORDS = {"in", "from", "at", "near", "across", "around", "over", "of", "through", "to", "visiting", "outside"}
# a capitalised word here after a state name does not turn it into something else ("Ohio Live Cam")
AFTER_OK = {"usa", "us", "u.s.", "u.s.a.", "live", "cam", "webcam", "camera", "sky", "skies", "sunrise", "sunset",
            "weather", "storm", "storms", "birds", "birding", "wildlife", "backyard", "airport", "airspace", "skyline"}
AFTER_BLOCK = {"post", "times", "roadhouse", "jones", "city", "tech", "state", "university", "college", "man",
               "nationals", "commanders", "wizards", "capitals", "rangers", "mavericks", "pacers", "gators",
               "seminoles", "panthers", "longhorns", "sooners", "buckeyes", "bulldogs", "huskies", "hoosiers",
               "goose", "geese", "warbler", "jay", "dry", "street", "st", "ave", "avenue", "avenues", "road",
               "boulevard", "blvd", "drive", "hall", "square", "bank", "company", "co", "inc", "fried", "chicken",
               "smoke", "rose", "o'keeffe", "o'keefe", "woolf", "lottery", "mint", "monument", "beach", "pale"}
BEFORE_BLOCK = {"george", "denzel", "booker", "kerry", "lake", "mount", "mt", "university", "new", "baja", "gulf",
                "via", "ms", "mrs", "miss", "mr", "dr", "aunt", "uncle", "lady", "sweet", "jack", "brookfield"}
# "-ing" words that lead into a place in a title. A list, because a rule would also cut Bowling Green,
# Spring Hill, Sterling Heights and King City down to their last word.
TITLE_VERBS = {"leaving", "landing", "visiting", "arriving", "departing", "approaching", "flying", "driving",
               "chasing", "railfanning", "camping", "fishing", "exploring", "touring", "watching", "spotting",
               "streaming", "filming", "recording", "passing", "crossing", "entering", "overlooking", "facing",
               "featuring", "showing", "walking", "riding", "sailing", "cruising", "hiking", "during"}
MAX_TOWN_WORDS = 4
CITY_STOP = {"am", "pm", "over", "into", "onto", "above",
             "live", "from", "in", "at", "near", "cam", "webcam", "the", "of", "and", "or", "but", "so", "to",
             "with", "by", "on", "for", "thanks", "thank", "yes", "no", "hi", "hello", "please", "sorry", "well",
             "ok", "okay", "me", "you", "him", "her", "it", "this", "that", "we", "us", "they", "today", "tonight",
             "yesterday", "morning", "evening", "night", "afternoon", "day", "part", "episode", "video", "stream",
             "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
             "dec", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
JUNK_LOCATIONS = {"unknown", "earth", "world", "worldwide", "internet", "online", "home", "n/a", "na", "none", "usa",
                  "us", "united states", "united states of america", "america", "somewhere"}
# phrases that contain a state or country name but point somewhere else
TRAPS = re.compile(r"gulf\s+of\s+mexico|new\s+england|new\s+south\s+wales|baja\s+california|indiana\s+jones|"
                   r"washington\s+post|new\s+york\s+times|texas\s+roadhouse|kentucky\s+fried", re.I)

ZONES = {
    "UTC": "UTC", "GMT": "UTC", "Z": "UTC",
    "EDT": "America/New_York", "EST": "America/New_York", "ET": "America/New_York",
    "CDT": "America/Chicago", "CST": "America/Chicago", "CT": "America/Chicago",
    "MDT": "America/Denver", "MST": "America/Denver", "MT": "America/Denver",
    "PDT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PT": "America/Los_Angeles",
    "AKDT": "America/Anchorage", "AKST": "America/Anchorage", "HST": "Pacific/Honolulu",
}
# "MT" and "PT" are also Mount and Point, so the two-letter zones only count after a clock time
SURE_ZONES = {z for z in ZONES if len(z) > 2}
_CLOCK_END = re.compile(r"(?:\d|[ap]\.?m\.?)\s*$", re.I)
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12}

_NAME_ALT = "|".join(re.escape(n).replace(r"\ ", r"\s+")
                     for n in sorted([s.lower() for s in STATES.values()] + list(COUNTRIES), key=len, reverse=True))
NAME_RE = re.compile(rf"(?<![A-Za-z.])(?:(?P<compass>{COMPASS})\.?\s+)?(?P<name>{_NAME_ALT})(?![A-Za-z])", re.I)
_STATE_ALT = "|".join(re.escape(s).replace(r"\ ", r"\s+") for s in sorted(STATES.values(), key=len, reverse=True))
_CITY_WORD = r"(?:[A-Z][A-Za-z.'’-]*|d['’][A-Z][A-Za-z]+)"
# small words inside a town's name: Isle of Palms, Fond du Lac, Truth or Consequences, Lake of the Ozarks
CITY_GLUE = ("of", "or", "du", "de", "la", "del", "the")
_CITY_GLUE = rf"(?:\s+(?:{'|'.join(CITY_GLUE)})){{0,2}}"
CITY_RE = re.compile(rf"(?<![A-Za-z])(?P<city>{_CITY_WORD}(?:{_CITY_GLUE}\s+{_CITY_WORD}){{0,5}}),\s*"
                     rf"(?:(?P<code>[A-Z]{{2}})|(?P<state>(?i:{_STATE_ALT})))(?![A-Za-z])")
GEORGIA_COUNTRY = re.compile(r"tbilisi|batumi|kutaisi|caucasus", re.I)
DC_RE = re.compile(r"\bWashington,?\s+D\.?C\.?(?![A-Za-z])", re.I)
LIVE_RE = re.compile(r"\blive\s+(?:from|at|in|on)\b|\blive[\s-]*(?:cam|camera|webcam)\b|\bweb[\s-]?cam\b|"
                     r"\bcam\s+live\b", re.I)

_NUM = r"\d{1,3}(?:\.\d+)?"
MAP_RE = re.compile(r"(?:@|[?&](?:q|ll|query|center)=)(-?\d{1,2}\.\d+)(?:,|%2C)\s*(-?\d{1,3}\.\d+)")
DMS_RE = re.compile(r"(?<![\w.])([NS])?\s*(\d{1,2})\s*°\s*(\d{1,2})\s*['′’]\s*"
                    r"(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|”|''))?\s*([NS])?[\s,;/]+([EW])?\s*(\d{1,3})\s*°\s*(\d{1,2})\s*['′’]\s*"
                    r"(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|”|''))?\s*([EW])?(?![A-Za-z])")
LETTER_RE = re.compile(rf"(?<![\w.])([NS])?\s*({_NUM})\s*(°)?\s*([NS])?[\s,;/]+([EW])?\s*({_NUM})\s*(°)?\s*([EW])?"
                       r"(?![\w.])")
DEC_RE = re.compile(r"(?<![\w.\-/:])(-?\d{1,2}\.(\d+))\s*(°)?\s*,\s*(-?\d{1,3}\.(\d+))\s*(°)?(?![\w.\-/:%])")

_MON = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DATE_RES = [
    re.compile(rf"(?<![A-Za-z]){_MON}\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})(?!\d)", re.I),
    re.compile(rf"(?<![\d])(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}\.?,?\s+(?P<year>\d{{4}})(?!\d)", re.I),
    re.compile(r"(?<![\d\-/.])(?P<year>\d{4})-(?P<num>\d{1,2})-(?P<day>\d{1,2})(?![\d\-])"),
    re.compile(r"(?<![\d/.])(?P<num>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4}|\d{2})(?![\d/])"),
    re.compile(r"(?<![\d.])(?P<a>\d{1,2})\.(?P<b>\d{1,2})\.(?P<year>\d{4})(?![\d.])"),
]
_ZONE_ALT = "|".join(sorted(ZONES, key=len, reverse=True))
TIME_RE = re.compile(rf"[\s,]*(?:at\s+|@\s*|T)?(?P<h>\d{{1,2}}):(?P<m>\d{{2}})(?::(?P<s>\d{{2}}))?\s*"
                     rf"(?P<ampm>[ap]\.?m\.?)?\s*(?P<zone>(?-i:{_ZONE_ALT}))(?![A-Za-z])", re.I)


def read_hints(about: dict[str, str], uploaded: date | None = None) -> list[Hint]:
    """Clues from the title, description, channel, location and tags, strongest first, one per place or day."""
    found: list[tuple[Hint, str | None]] = []      # (hint, the state a city hint lies in)
    title = (about.get("title") or "").strip()
    desc = (about.get("description") or "").strip()
    live = bool(LIVE_RE.search(title))

    if title:
        top = "stated" if live else "likely"
        found += [(_place_hint(v, "the title says", _quote(title, a, b), top, towns), st)
                  for v, kind, st, a, b, towns in _places(title)]
        found += [(h, None) for h in _when(title, "the title says", top, uploaded)]
    location = (about.get("location") or "").strip()
    if location:
        found += _location(location)
    if desc:
        for v, kind, st, a, b, towns in _places(desc):
            strength = "likely" if a < HEAD_CHARS and kind in ("coord", "city") else "weak"
            found.append((_place_hint(v, "the description says", _quote(desc, a, b), strength, towns), st))
        found += [(h, None) for h in _when(desc[:HEAD_CHARS], "the description says", "likely", uploaded)]
    channel = (about.get("channel") or "").strip()
    if channel:
        found += [(_place_hint(v, "the channel name is", channel, "weak", towns), st)
                  for v, kind, st, a, b, towns in _places(channel)]
    for tag in (t.strip() for t in (about.get("tags") or "").split(",")):
        if tag:
            found += [(_place_hint(v, "a tag says", tag, "weak", towns), st)
                      for v, kind, st, a, b, towns in _places(tag, whole=True)]
    return _settle(found)


def read_places(texts: list[tuple[str, str, str]]) -> list[Hint]:
    """Place clues in short lines read off the picture, strongest first, one per place.

    texts: (line, because, strength) for each line. Finds what the title reader finds, and also a town
    with its state after it and no comma, when the state ends the line ("Main Street Railfan - Huron OHIO"):
    signs and captions leave the comma out far more often than titles do.
    """
    found: list[tuple[Hint, str | None]] = []
    for text, because, strength in texts:
        text = text.strip()
        hits = _places(text) + _bare_town(text)
        found += [(Hint("place", v, because, strength, towns), st) for v, kind, st, a, b, towns in hits]
    return _settle(found)


def _place_hint(value: str, said: str, quote: str, strength: str, towns: tuple[str, ...] = ()) -> Hint:
    return Hint("place", value, f'{said} "{quote}"', strength, towns)


def _location(text: str) -> list[tuple[Hint, str | None]]:
    """The publisher's own place tag. Whatever it names is stated, even a park a map search must find."""
    because = f'the video\'s place is set to "{text}"'
    hits = _places(text, whole=True)
    if hits:
        return [(Hint("place", v, because, "stated", towns), st) for v, kind, st, a, b, towns in hits]
    if text.lower().strip(" .") in JUNK_LOCATIONS:
        return []
    name = text.title() if not any(c.islower() for c in text) else text
    return [(Hint("place", name, because, "stated"), None)]


def _settle(found: list[tuple[Hint, str | None]]) -> list[Hint]:
    best: dict[tuple[str, str], Hint] = {}
    for hint, _ in found:
        key = (hint.kind, hint.value)
        # on a tie the first source wins: the title is read first and quotes the most
        if key not in best or RANK[hint.strength] < RANK[best[key].strength]:
            best[key] = hint
    # a bare town name next to "Town, State" is the same town: the fuller name is the one a map
    # search can find, and it is as sure as the surer of the two
    for (kind, value), bare in list(best.items()):
        full = next((h for (k, v), h in best.items()
                     if kind == k == "place" and "," not in value and v.lower().startswith(value.lower() + ",")), None)
        if full:
            del best[(kind, value)]
            if RANK[bare.strength] < RANK[full.strength]:
                best[("place", full.value)] = replace(full, because=f"{bare.because}, and {full.because}", strength=bare.strength)
    # a state adds nothing next to a town in it that is at least as sure
    city_states = {}
    for hint, state in found:
        if state and hint.value != state:
            rank = RANK[best[("place", hint.value)].strength]
            city_states[state] = min(rank, city_states.get(state, 9))
    kept = [h for h in best.values()
            if not (h.kind == "place" and h.value in city_states and city_states[h.value] <= RANK[h.strength])]
    # Two towns or states said equally surely ("Tampa, FL to Boston, MA") settle nothing, so neither is
    # taken unasked. A position or a named venue is no rival to the town it is in, so only towns and
    # states are weighed. Rival towns in one state still leave the state sure.
    state_of = {hint.value: state for hint, state in found if state}
    places = [h for h in kept if h.kind == "place" and h.value in state_of]
    top = min((RANK[h.strength] for h in places), default=RANK["weak"])
    rivals = [h for h in places if RANK[h.strength] == top]
    if top < RANK["weak"] and len(rivals) > 1:
        kept = [replace(h, strength="weak") if any(h is r for r in rivals) else h for h in kept]
        states = {state_of[h.value] for h in rivals}
        if len(states) == 1:
            state = states.pop()
            kept = [h for h in kept if not (h.kind == "place" and h.value == state)]
            kept.append(Hint("place", state, f"{rivals[0].because}, and {rivals[1].because}", rivals[0].strength))
    return sorted(kept, key=lambda h: RANK[h.strength])


def _quote(text: str, start: int, end: int) -> str:
    """The match plus up to two words before it, so the person can find it in the text."""
    lead = re.search(r"(?:[^\s|()\[\]:;!?•–—-]+\s+){0,2}$", text[:start])
    if lead and start - lead.start() <= 40:
        start = lead.start()
    return " ".join(text[start:end].split())


# ---- places ----

def _places(text: str, whole: bool = False) -> list[tuple[str, str, str | None, int, int, tuple[str, ...]]]:
    """(value, kind, state, start, end, readings) for each place in text. kind is coord, city, state or
    country. readings: for a city, every way to read its name (see Hint.towns).

    whole: text is one short field (a tag, the location), so case does not matter and a lone name stands as itself.
    """
    out: list[tuple[str, str, str | None, int, int, tuple[str, ...]]] = []
    masked = text
    for value, a, b in _coords(text):
        out.append((value, "coord", None, a, b, ()))
        masked = _mask(masked, a, b)
    for value, state, a, b, towns in _cities(masked, whole):
        out.append((value, "city", state, a, b, towns))
        masked = _mask(masked, a, b)
    for m in TRAPS.finditer(masked):
        masked = _mask(masked, m.start(), m.end())
    for value, kind, a, b in _names(masked, whole):
        out.append((value, kind, value if kind == "state" else None, a, b, ()))
    return sorted(out, key=lambda p: p[3])


def _mask(text: str, a: int, b: int) -> str:
    return text[:a] + " " * (b - a) + text[b:]


def _shouting(text: str) -> bool:
    return not any(c.islower() for c in text)


def _coords(text: str) -> list[tuple[str, int, int]]:
    out = []
    masked = text
    for rx, read in ((MAP_RE, _read_map), (DMS_RE, _read_dms), (LETTER_RE, _read_letters), (DEC_RE, _read_decimal)):
        for m in rx.finditer(masked):
            pair = read(m)
            if pair and -90 <= pair[0] <= 90 and -180 <= pair[1] <= 180 and pair != (0.0, 0.0):
                out.append((f"{pair[0]:.5f},{pair[1]:.5f}", m.start(), m.end()))
                masked = _mask(masked, m.start(), m.end())
    return out


def _read_map(m: re.Match) -> tuple[float, float]:
    return float(m.group(1)), float(m.group(2))


def _read_dms(m: re.Match) -> tuple[float, float] | None:
    ns, dlat, mlat, slat, ns2, ew, dlon, mlon, slon, ew2 = m.groups()
    ns, ew = ns or ns2, ew or ew2
    # each half needs its letter exactly once, or it is not a position at all
    if not ns or not ew or (m.group(1) and ns2) or (m.group(6) and ew2) or int(mlat) >= 60 or int(mlon) >= 60:
        return None
    lat = int(dlat) + int(mlat) / 60 + float(slat or 0) / 3600
    lon = int(dlon) + int(mlon) / 60 + float(slon or 0) / 3600
    return (-lat if ns == "S" else lat), (-lon if ew == "W" else lon)


def _read_letters(m: re.Match) -> tuple[float, float] | None:
    ns, lat, deg1, ns2, ew, lon, deg2, ew2 = m.groups()
    if bool(ns) == bool(ns2) or bool(ew) == bool(ew2):
        return None
    # bare whole numbers with letters ("5 N, 3 W") are too often something else; ask for a decimal or a degree sign
    if not (("." in lat and "." in lon) or (deg1 and deg2)):
        return None
    la, lo = float(lat), float(lon)
    return (-la if (ns or ns2) == "S" else la), (-lo if (ew or ew2) == "W" else lo)


def _read_decimal(m: re.Match) -> tuple[float, float] | None:
    lat, lat_dec, deg1, lon, lon_dec, deg2 = m.groups()
    # scores and prices ("3.5, 2.25") have few decimals; a position worth giving has at least 3, or degree signs
    places = 2 if (deg1 and deg2) else 3
    if len(lat_dec) < places or len(lon_dec) < places:
        return None
    return float(lat), float(lon)


def _readings(run: str, shouting: bool) -> tuple[str, ...]:
    """Every way to read the words before the comma as a town's name, longest first."""
    words = run.split()
    out = []
    for i in range(max(len(words) - MAX_TOWN_WORDS, 0), len(words)):
        if words[i].lower() not in CITY_GLUE:
            name = " ".join(words[i:])
            out.append(name.title() if shouting else name)
    return tuple(out)


def _cities(text: str, whole: bool) -> list[tuple[str, str, int, int, tuple[str, ...]]]:
    out = []
    for m in DC_RE.finditer(text):
        out.append(("Washington, DC", "District of Columbia", m.start(), m.end(), ()))
    text = DC_RE.sub(lambda m: " " * len(m.group()), text)
    shouting = _shouting(text)
    pos = 0
    while (m := CITY_RE.search(text, pos)) is not None:
        pos = m.start() + 1
        if m.group("code"):
            state = STATES.get(m.group("code"))
            # in all-caps text ", IN THE SNOW" looks like a code; only trust one that ends the phrase
            if state is None or (shouting and re.match(r"\s*[A-Za-z]", text[m.end():])):
                continue
        else:
            state = _canon_state(m.group("state"))
            if not (whole or shouting or _capitalised(m.group("state"))):
                continue
        city, start = _trim_city(m.group("city"), m.start("city"), text[:m.start("city")])
        if city is not None and _canon_state(city) and not m.group("code"):
            # "Kansas, Oklahoma" is a list of states. Starting again inside it turned
            # "New York, New York" into the town of York.
            pos = m.end()
            continue
        # with a code after it a state's name is a town: New York, NY and Indiana, PA
        if city is None or city.lower() in COUNTRIES:
            continue
        if re.match(r"[\s-]*based\b", text[m.end():], re.I):
            continue        # "a Houston, TX based pilot" says where the pilot lives
        if state == "Georgia" and GEORGIA_COUNTRY.search(text):
            continue
        if shouting:
            city = city.title()
        if state == "District of Columbia" and city == "Washington":
            out.append(("Washington, DC", state, start, m.end(), ()))
        else:
            out.append((f"{city}, {state}", state, start, m.end(), _readings(m.group("city"), shouting)))
        pos = m.end()
    return out


# a spaced dash, a bar or a bullet ends one phrase on a sign; a hyphen inside a name (Wilkes-Barre) does not
_PHRASE_BREAK = re.compile(r"\s[-–—]\s|[|•·–—]")
BARE_RE = re.compile(rf"(?<![\w'’.-])(?P<city>{_CITY_WORD}(?:{_CITY_GLUE}\s+{_CITY_WORD}){{0,5}})\s+"
                     rf"(?:(?P<code>[A-Z]{{2}})|(?P<state>(?i:{_STATE_ALT})))[\s.!]*$")
_COMPASS_RE = re.compile(rf"{COMPASS}", re.I)


def _bare_town(text: str) -> list[tuple[str, str, str | None, int, int, tuple[str, ...]]]:
    """A town and its state with no comma between them, when the state ends the text."""
    breaks = [m.end() for m in _PHRASE_BREAK.finditer(text)]
    start = breaks[-1] if breaks else 0
    phrase = text[start:]
    m = BARE_RE.search(phrase)
    if m is None:
        return []
    state = STATES.get(m.group("code")) if m.group("code") else _canon_state(m.group("state"))
    if state is None or (m.group("state") and not _capitalised(m.group("state"))):
        return []
    run = m.group("city")
    last = run.split()[-1]
    # "Upstate New York" and "West Virginia" are a part of a state or the state itself, not a town in it
    if _COMPASS_RE.fullmatch(last) or (m.group("state") and _canon_state(f"{last} {m.group('state')}")):
        return []
    if m.group("state") and _canon_state(run):
        return []                         # "Kansas Ohio" is two states
    city, at = _trim_city(run, start + m.start("city"), phrase[:m.start("city")])
    if city is None or city.lower() in COUNTRIES or (len(city.split()) == 1 and city.lower() in TITLE_VERBS):
        return []
    shouting = _shouting(phrase)
    if shouting:
        city = city.title()
    if state == "District of Columbia" and city == "Washington":
        return [("Washington, DC", "city", state, at, start + m.end(), ())]
    return [(f"{city}, {state}", "city", state, at, start + m.end(), _readings(run, shouting))]


def _trim_city(run: str, offset: int, lead: str = "") -> tuple[str | None, int]:
    """The last one to three words before the comma, cut at words that start a title, not a town name.
    This is the reading used when no map can be asked (see Hint.towns for the better way).
    lead: the text before the run, to tell a time zone ("8 PM ET Huron") from a name ("MT VERNON")."""
    words = list(re.finditer(r"\S+", run))
    keep: list[re.Match] = []
    for i, w in enumerate(reversed(words)):
        token = w.group()
        low = token.lower().strip(".'’")
        before = run[:w.start()] if w.start() else lead
        zone = token in SURE_ZONES or (token in ZONES and bool(_CLOCK_END.search(before)))
        # a full stop before the town ends the sentence before it ("I love Ohio. Huron, OH"), except "St." and kin
        ends_sentence = i > 0 and token.endswith(".") and low not in ("st", "ft", "mt", "pt", "ste")
        # Cape May and Del Mar end in a month's name; a month on its own is part of a date
        month_end = i == 0 and len(words) > 1 and low[:3] in MONTHS and low in CITY_STOP
        if (low in CITY_STOP and not month_end) or zone or (i > 0 and low in TITLE_VERBS) or ends_sentence or len(keep) == 3:
            break
        keep.insert(0, w)
    if not keep or (len(keep) == 1 and keep[0].group().lower().strip(".'’") in CITY_STOP):
        return None, offset
    return " ".join(w.group() for w in keep), offset + keep[0].start()


def _capitalised(phrase: str) -> bool:
    return all(w[0].isupper() for w in phrase.split() if w.lower() != "of")


def _canon_state(name: str) -> str | None:
    low = " ".join(name.lower().split())
    for full in STATES.values():
        if full.lower() == low:
            return full
    return None


def _names(text: str, whole: bool) -> list[tuple[str, str, int, int]]:
    out = []
    shouting = _shouting(text)
    for m in NAME_RE.finditer(text):
        raw = m.group("name")
        low = " ".join(raw.lower().split())
        state = _canon_state(raw)
        compass = m.group("compass")
        start = m.start("compass") if compass else m.start("name")
        joined = _canon_state(f"{compass} {raw}") if compass else None
        if joined:
            # the pattern reads "West Virginia" as west + Virginia; the two words together are the state
            state, low, raw, compass = joined, joined.lower(), f"{compass} {raw}", None
        elif compass and len(compass) == 2 and not compass.isupper() and not whole:
            compass, start = None, m.start("name")      # "ne" in lower case is a word, not north-east
        if not (whole or shouting or _capitalised(raw)):
            continue
        if low in ("uk", "u.k.") and raw.upper() != raw:
            continue
        if not _fits(text, start, m.end(), low, bool(compass), whole, shouting):
            continue
        if low == "georgia" and GEORGIA_COUNTRY.search(text):
            continue
        out.append((state, "state", start, m.end()) if state else (COUNTRIES[low], "country", start, m.end()))
    return out


def _fits(text: str, start: int, end: int, low: str, compass: bool, whole: bool, shouting: bool) -> bool:
    """Whether the words around a state or country name leave it meaning that place."""
    prev = re.search(r"([A-Za-z][\w'’.]*)\s+$", text[:start])
    nxt = re.match(r"\s+([A-Za-z][\w'’.]*)", text[end:])
    prev_w = prev.group(1).lower().rstrip(".") if prev else None
    next_w = nxt.group(1).lower() if nxt else None
    if prev_w in BEFORE_BLOCK:
        return False
    if next_w is not None and next_w not in AFTER_OK:
        if next_w.rstrip(".") in AFTER_BLOCK:
            # "Washington State" is the state; "Ohio State" is a university
            if not (low == "washington" and next_w == "state"):
                return False
        elif not shouting and nxt.group(1)[0].isupper():
            return False                  # "Indiana Jones", "Texas Roadhouse": a name of its own
    if low not in AMBIGUOUS:
        return True
    if compass or prev_w in PLACE_WORDS or next_w in ("usa", "u.s.", "u.s.a.", "state"):
        return True
    if whole:
        rest = re.sub(r"(?i)[\s,]*(?:usa|u\.s\.a?\.?|united states)?[\s.]*$", "", text[end:])
        return not text[:start].strip() and not rest
    return bool(re.match(r"\s*,\s*(?:usa|u\.s\.)", text[end:], re.I))


# ---- days and start times ----

def _when(text: str, said: str, strength: str, uploaded: date | None) -> list[Hint]:
    out = []
    for rx in DATE_RES:
        for m in rx.finditer(text):
            day = _read_date(m)
            if day is None or day < ARCHIVE_START or (uploaded is not None and day > uploaded):
                continue
            out.append((m.start(), Hint("day", day.isoformat(), f'{said} "{m.group()}"', strength)))
            t = TIME_RE.match(text, m.end())
            if t:
                start = _read_time(day, t)
                if start is not None:
                    quote = " ".join((m.group() + t.group()).split())
                    out.append((m.start(), Hint("start", start.isoformat(), f'{said} "{quote}"', "likely")))
    return [h for _, h in sorted(out, key=lambda p: p[0])]


def _read_date(m: re.Match) -> date | None:
    parts = m.groupdict()
    year = int(parts["year"])
    year += 2000 if year < 100 else 0
    if parts.get("mon"):
        month, day = MONTHS[parts["mon"][:3].lower()], int(parts["day"])
    elif parts.get("num"):
        month, day = int(parts["num"]), int(parts["day"])
    else:
        a, b = int(parts["a"]), int(parts["b"])
        if a <= 12 and b <= 12:
            return None                   # 11.12.2026 is November 12 in the USA and 11 December in Europe
        month, day = (a, b) if b > 12 else (b, a)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _read_time(day: date, m: re.Match) -> datetime | None:
    hour, minute, sec = int(m.group("h")), int(m.group("m")), int(m.group("s") or 0)
    ampm = (m.group("ampm") or "").replace(".", "").lower()
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59 or sec > 59:
        return None
    local = datetime(day.year, day.month, day.day, hour, minute, sec, tzinfo=ZoneInfo(ZONES[m.group("zone")]))
    return local.astimezone(timezone.utc)
