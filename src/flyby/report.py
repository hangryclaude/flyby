"""Turns a Result into what the user reads: a compact terminal summary and a shareable page."""
from __future__ import annotations

import base64
import html
import http.client
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from rich.markup import escape
from rich.table import Table

from .model import Clues, Flight, Media, Result

PAGES_DIR = Path("/Users/angus/dev/pages")
USER_AGENT = "flyby/0.1 (personal research tool)"

# Only these verdicts may put a point on the map; the others claim nothing about where.
_POSITIONED = ("located", "not sure")

_HELP_LINES = [
    "Use a longer recording: more flyovers means a more reliable match.",
    "Give the exact day with --date if you know it.",
    "Give a smaller --area if you know roughly where it was.",
]


def place_name(lat: float, lon: float) -> str | None:
    # street-level lookup, town-level answer: at zoom 14 the reference spot in Oyster Bay came back as the next village
    query = urllib.parse.urlencode({"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "format": "jsonv2", "zoom": 16})
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{query}", headers={"User-Agent": USER_AGENT}
    )
    # A name is a convenience; any failure here must not cost the user the answer.
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.load(resp)
        addr = data.get("address") or {}
        local = next(
            (addr[k] for k in ("city", "town", "village", "hamlet", "suburb", "neighbourhood", "county") if addr.get(k)),
            None,
        )
        region = addr.get("state") or addr.get("country")
        parts = [x for x in (local, region) if x]
        if parts:
            return ", ".join(parts)
        shown = [s.strip() for s in str(data.get("display_name", "")).split(",") if s.strip()]
        return ", ".join(shown[:2]) or None
    except (OSError, ValueError, AttributeError, TypeError, http.client.HTTPException):
        return None


def _has_position(result: Result) -> bool:
    return result.verdict in _POSITIONED and result.lat is not None and result.lon is not None


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def _utc(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "?"


def _km(metres: float | None) -> str:
    return f"{metres / 1000:.1f}" if metres is not None else "?"


def _flight_name(f: Flight) -> str:
    return f.registration or f.icao


def _odds(p: float | None) -> str | None:
    if p is None:
        return None
    if p <= 0:
        return "a wrong answer would essentially never look this good"
    n = max(1, round(1 / p))
    return f"a wrong answer would look this good about 1 time in {n:,}"


def _margin_text(margin_pct: float | None) -> str | None:
    if margin_pct is None:
        return None
    return f"the best match leads the next best flight pair by {margin_pct:.0f}%"


def _maps_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps?q={lat:.5f},{lon:.5f}"


def _street_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat:.5f},{lon:.5f}"


def _apple_url(lat: float, lon: float) -> str:
    return f"https://maps.apple.com/?ll={lat:.5f},{lon:.5f}&q=flyby"


def print_result(console, media: Media, clues: Clues, result: Result, place: str | None = None, page: Path | None = None) -> None:
    colour = {"located": "green", "not sure": "yellow"}.get(result.verdict, "red")
    console.print(f"[bold {colour}]{escape(result.verdict.upper())}[/]  {escape(result.headline)}")

    if _has_position(result):
        label = "Location" if result.verdict == "located" else "Best guess (unconfirmed)"
        console.print(f"{label}: [bold]{result.lat:.5f}, {result.lon:.5f}[/]")
        if place:
            console.print(f"Near: {escape(place)}")
        console.print(_maps_url(result.lat, result.lon), soft_wrap=True, markup=False, highlight=False)
        parts = [t for t in (
            f"p = {result.p:.2g}" if result.p is not None else None,
            _odds(result.p),
            _margin_text(result.margin_pct),
        ) if t]
        if parts:
            console.print(escape("; ".join(parts)))
    else:
        console.print("Nothing was found, so no location is shown.")
        for line in _HELP_LINES:
            console.print(f"  - {line}")

    if result.day:
        console.print(f"Day (UTC): {result.day.isoformat()}")
    start = result.recording_start_utc or clues.start_utc
    if start:
        console.print(f"Recording started: {_utc(start)} UTC")

    if result.flights and _has_position(result):
        table = Table(title="Matched flights")
        for col in ("Aircraft", "Type", "Heard (UTC)", "Distance km"):
            table.add_column(col)
        for f in result.flights:
            table.add_row(escape(_flight_name(f)), escape(f.type_name or "?"), _utc(f.heard_utc), _km(f.slant_m))
        console.print(table)

    if result.events:
        table = Table(title="Flyovers measured")
        for col in ("Time", "Hz", "m/s", "km", "Role"):
            table.add_column(col)
        for e in result.events:
            table.add_row(_mmss(e.t_cpa), f"{e.f0:.0f}", f"{e.v:.0f}", _km(e.d), escape(e.role))
        console.print(table)

    if result.searched:
        console.print("Searched:")
        for line in result.searched:
            console.print(f"  {escape(line)}")
    for note in result.notes:
        console.print(f"Note: {escape(note)}")
    if page:
        console.print(f"Page: {page}", soft_wrap=True, markup=False, highlight=False)


_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#fdfdfc;color:#1b1b1b;font:18px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:70ch;margin:0 auto;padding:32px 16px 64px}
h1{font-size:2.2rem;line-height:1.2;margin:0 0 .4em}
h2{font-size:1.25rem;margin:2em 0 .5em}
.sub{color:#555;margin:0 0 1.5em}
.coords{font:600 1.4rem ui-monospace,Menlo,monospace;margin:.2em 0}
.guess{background:#fff6dd;border:1px solid #e3c45f;padding:.6em .9em;border-radius:8px}
.none{background:#f4f4f2;border:1px solid #ccc;padding:.8em 1em;border-radius:8px}
.btns{display:flex;flex-wrap:wrap;gap:10px;margin:1em 0}
a.btn,button{font:inherit;padding:.55em 1em;border:1px solid #1b1b1b;border-radius:8px;background:#fff;color:#1b1b1b;text-decoration:none;cursor:pointer}
a.btn:hover,button:hover{background:#eee}
#map{height:360px;border:1px solid #ccc;border-radius:8px;margin:1em 0}
table{border-collapse:collapse;width:100%;font-size:.9rem;display:block;overflow-x:auto}
th,td{text-align:left;padding:.4em .7em;border-bottom:1px solid #ddd;white-space:nowrap}
figure{margin:1.2em 0}
figure img{width:100%;height:auto;border:1px solid #ddd}
figcaption{font-size:.85rem;color:#555}
footer{margin-top:3em;font-size:.8rem;color:#555;border-top:1px solid #ddd;padding-top:1em}
"""

_CAPTION = (
    "The bright line is the plane's tone in the recording. "
    "The thin line is what this spot should have heard from the real flight."
)


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _figure(path: Path) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return (
        f'<figure><img alt="Spectrogram with predicted flight curve" src="data:image/png;base64,{data}">'
        f"<figcaption>{_CAPTION}</figcaption></figure>"
    )


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-") or "recording"


def write_page(media: Media, clues: Clues, result: Result, place: str | None = None, pages_dir: Path = PAGES_DIR) -> Path:
    pos = _has_position(result)
    out: list[str] = []
    out.append(f"<h1>{_e(result.headline)}</h1>")
    out.append(f'<p class="sub">Verdict: {_e(result.verdict)}</p>')

    map_js = ""
    if pos:
        lat, lon = result.lat, result.lon
        coord = f"{lat:.5f}, {lon:.5f}"
        if result.verdict == "not sure":
            out.append('<p class="guess"><strong>Best guess, not confirmed.</strong> Treat this as a lead, not an answer.</p>')
        out.append(f'<p class="coords"><span id="coords">{coord}</span></p>')
        out.append('<div class="btns"><button type="button" id="copy">Copy coordinates</button></div>')
        if place:
            out.append(f"<p>Near {_e(place)}</p>")
        out.append(
            '<div class="btns">'
            f'<a class="btn" href="{_e(_maps_url(lat, lon))}">Google Maps</a>'
            f'<a class="btn" href="{_e(_street_url(lat, lon))}">Street View</a>'
            f'<a class="btn" href="{_e(_apple_url(lat, lon))}">Apple Maps</a></div>'
        )
        out.append('<div id="map" role="img" aria-label="Map of the answer"></div>')
        map_data = {"lat": lat, "lon": lon, "runners": [[float(a), float(b)] for a, b, _ in result.runners_up]}
        map_js = json.dumps(map_data)
    else:
        out.append(
            '<div class="none"><strong>Nothing was found.</strong> '
            "No location is shown because the search did not find one it could stand behind.</div>"
        )
        out.append("<h2>What would help</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in _HELP_LINES) + "</ul>")

    if pos:
        out.append("<h2>How sure is this</h2>")
        bits: list[str] = []
        if result.p is not None:
            bits.append(f"<li>p = {result.p:.2g}</li>")
            odds = _odds(result.p)
            bits.append(f"<li>In plain words: {_e(odds)}.</li>")
        mt = _margin_text(result.margin_pct)
        if mt:
            bits.append(f"<li>{_e(mt[0].upper() + mt[1:])}.</li>")
        bits.append(f"<li>{len(result.events)} flyover(s) measured in the audio.</li>")
        out.append("<ul>" + "".join(bits) + "</ul>")
        if result.day:
            out.append(f"<p>Day (UTC): {result.day.isoformat()}</p>")
        start = result.recording_start_utc or clues.start_utc
        if start:
            out.append(f"<p>Recording started: {_utc(start)} UTC</p>")

        for fig in result.figures:
            out.append(_figure(fig))

        if result.flights:
            rows = [
                [_e(_flight_name(f)), _e(f.type_name or "?"), _utc(f.heard_utc), _km(f.slant_m)]
                for f in result.flights
            ]
            out.append("<h2>Matched flights</h2>" + _table(["Aircraft", "Type", "Heard (UTC)", "Distance km"], rows))

    if result.events:
        rows = [
            [_mmss(ev.t_cpa), f"{ev.f0:.0f}", f"{ev.v:.0f}", _km(ev.d), _e(ev.role)]
            for ev in result.events
        ]
        out.append("<h2>Flyovers measured</h2>" + _table(["Time", "Hz", "m/s", "km", "Role"], rows))

    if result.searched:
        out.append("<h2>What was searched</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in result.searched) + "</ul>")
    if result.notes:
        out.append("<h2>Notes</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in result.notes) + "</ul>")

    out.append(
        f"<footer>Source: {_e(media.source)}<br>Title: {_e(media.title)}<br>"
        "Flight tracks: adsb.lol (ODbL). Engine: dopplertrack (MIT). "
        "Map: &copy; OpenStreetMap contributors.</footer>"
    )

    script = ""
    if pos:
        script = (
            "<script>\n"
            f"var DATA={map_js};\n"
            "var cb=document.getElementById('copy');\n"
            "if(cb){cb.addEventListener('click',function(){var t=document.getElementById('coords').textContent;"
            "if(navigator.clipboard){navigator.clipboard.writeText(t).then(function(){cb.textContent='Copied'});}});}\n"
            # Offline the page still reads fine: coordinates and links above are plain HTML.
            "if(window.L){var m=L.map('map').setView([DATA.lat,DATA.lon],12);"
            "L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,"
            "attribution:'&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors'}).addTo(m);"
            "L.marker([DATA.lat,DATA.lon]).addTo(m);"
            "DATA.runners.forEach(function(r){L.circleMarker(r,{radius:5,color:'#888',fillColor:'#aaa',fillOpacity:.8}).addTo(m);});}\n"
            "</script>"
        )

    leaflet_head = (
        '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">'
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>'
        if pos else ""
    )
    doc = (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>flyby: {_e(media.title)}</title>{leaflet_head}<style>{_CSS}</style></head>"
        f"<body><main>{''.join(out)}</main>{script}</body></html>\n"
    )
    pages_dir.mkdir(parents=True, exist_ok=True)
    path = pages_dir / f"flyby-{_slug(media.slug)}.html"
    path.write_text(doc, encoding="utf-8")
    return path
