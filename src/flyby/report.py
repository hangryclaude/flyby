"""Turns a Result into what the user reads: a compact terminal summary and a shareable page."""
from __future__ import annotations

import base64
import html
import http.client
import io
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image
from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .checks import check_links
from .model import Clues, Flight, Frame, Media, Result
from .paths import pages_dir as default_pages_dir
from .ui import ACCENT, MARGIN, indent

USER_AGENT = "flyby/0.1 (personal research tool)"

# Only these verdicts may put a point on the map; the others claim nothing about where.
_POSITIONED = ("located", "not sure")

_PRINTED_CHECKS = 6          # the terminal gets the most useful few; the page has them all
_PICTURE_W = 960             # embedded pictures: wide enough to judge a street, small enough to keep the page light

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


def _sighting_row(x) -> list[str]:
    """One other aircraft: who, when, how far, and whether its tone is in the recording."""
    return [_flight_name(x.flight), x.flight.type_name or "?", _utc(x.flight.heard_utc), _km(x.flight.slant_m),
            "yes" if x.seen else "no"]


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


def print_result(console, media: Media, clues: Clues, result: Result, place: str | None = None, page: Path | None = None) -> None:
    colour = {"located": "green", "not sure": "yellow"}.get(result.verdict, "red")
    out = lambda r, by=2: console.print(indent(r, by), highlight=False)
    fact = lambda label, value: out(f"[dim]{label}:[/] {value}")
    console.print(indent(Panel(f"[bold {colour}]{escape(result.verdict.upper())}[/]  {escape(result.headline)}",
                               box=box.ROUNDED, border_style=colour, expand=False, padding=(0, 2))))

    if _has_position(result):
        label = "Location" if result.verdict == "located" else "Best guess (unconfirmed)"
        fact(label, f"[bold]{result.lat:.5f}, {result.lon:.5f}[/]")
        if place:
            fact("Near", escape(place))
        console.print(MARGIN + _maps_url(result.lat, result.lon), soft_wrap=True, markup=False, highlight=False, style=ACCENT)
        parts = [t for t in (
            f"p = {result.p:.2g}" if result.p is not None else None,
            _odds(result.p),
            _margin_text(result.margin_pct),
        ) if t]
        if parts:
            out(f"[dim]{escape('; '.join(parts))}[/]")
        console.print()
        out("[bold]Check it[/]")
        for c in check_links(result, clues, media)[:_PRINTED_CHECKS]:
            console.print(f"{MARGIN}  {c.name}: {c.url}", soft_wrap=True, markup=False, highlight=False)
    else:
        out("Nothing was found, so no location is shown.")
        for line in _HELP_LINES:
            out(f"[dim]-[/] {line}", 4)

    if result.day:
        fact("Day (UTC)", result.day.isoformat())
    start = result.recording_start_utc or clues.start_utc
    if start:
        fact("Recording started", f"{_utc(start)} UTC")

    def table(title: str, columns: tuple[str, ...], rows) -> None:
        t = Table(title=title, title_justify="left", title_style="bold", box=box.SIMPLE_HEAD, header_style="dim",
                  border_style="grey37", pad_edge=False)
        for col in columns:
            t.add_column(col)
        for row in rows:
            t.add_row(*row)
        console.print()
        console.print(indent(t))

    if result.flights and _has_position(result):
        table("Matched flights", ("Aircraft", "Type", "Heard (UTC)", "Distance km"),
              [(escape(_flight_name(f)), escape(f.type_name or "?"), _utc(f.heard_utc), _km(f.slant_m)) for f in result.flights])

    if result.sightings and _has_position(result):
        table("Other aircraft that passed this spot (not used by the search)",
              ("Aircraft", "Type", "Due (UTC)", "Distance km", "In the sound?"),
              [tuple(escape(cell) for cell in _sighting_row(x)) for x in result.sightings])

    if result.events:
        table("Flyovers measured", ("Time", "Hz", "m/s", "km", "Role"),
              [(_mmss(e.t_cpa), f"{e.f0:.0f}", f"{e.v:.0f}", _km(e.d), escape(e.role)) for e in result.events])

    if result.searched:
        console.print()
        out("[bold]Searched[/]")
        for line in result.searched:
            out(f"[dim]{escape(line)}[/]", 4)
    if result.notes:
        console.print()
    for note in result.notes:
        out(f"[yellow]Note:[/] {escape(note)}")
    if page:
        console.print()
        console.print(f"{MARGIN}Page: {page}", soft_wrap=True, markup=False, highlight=False, style="dim")


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
.checks{list-style:none;padding:0;margin:1em 0;display:grid;gap:12px}
.checks a.btn{display:block;font-weight:600;font-size:1.1rem;padding:.7em 1em}
.checks .look{display:block;font-size:.9rem;color:#555;margin:.3em 0 0 .2em}
.pics{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.pics figure{margin:0}
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


def _picture(frame: Frame) -> str:
    with Image.open(frame.path) as img:
        img = img.convert("RGB")
        img.thumbnail((_PICTURE_W, _PICTURE_W * 4))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    if frame.t_s is None:
        when = "The video's own cover picture"
    else:
        # replays of streams run for hours: 8:20:00 reads better than 500:00
        s = int(round(frame.t_s))
        when = (f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else _mmss(s)) + " into the video"
    return (f'<figure><img alt="Picture from the video, {_e(when)}" src="data:image/jpeg;base64,{data}">'
            f"<figcaption>{_e(when)}</figcaption></figure>")


def _pictures_href(pictures_page: Path | str, pages_dir: Path) -> str:
    if isinstance(pictures_page, str):
        return pictures_page
    # both pages sit in the same folder when flyby wrote them, so a plain name keeps the link working if the folder moves
    return pictures_page.name if pictures_page.parent == pages_dir else pictures_page.resolve().as_uri()


def _check_section(media: Media, clues: Clues, result: Result, frames, pictures_page, pages_dir: Path) -> str:
    items = "".join(
        f'<li><a class="btn" href="{_e(c.url)}">{_e(c.name)}</a><span class="look">{_e(c.look_for)}</span></li>'
        for c in check_links(result, clues, media)
    )
    parts = [f'<h2>Check it yourself</h2><ul class="checks">{items}</ul>']
    sharpest = sorted(frames, key=lambda f: f.sharpness, reverse=True)[:2]
    if sharpest:
        parts.append("<h2>Does Street View show this place?</h2>"
                     '<div class="pics">' + "".join(_picture(f) for f in sharpest) + "</div>")
    if pictures_page is not None:
        parts.append(f'<p><a class="btn" href="{_e(_pictures_href(pictures_page, pages_dir))}">'
                     "See every picture taken from the video</a></p>")
    return "".join(parts)


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-") or "recording"


def write_page(media: Media, clues: Clues, result: Result, place: str | None = None, pages_dir: Path | None = None,
               frames: tuple[Frame, ...] | list[Frame] = (), pictures_page: Path | str | None = None) -> Path:
    pages_dir = pages_dir or default_pages_dir()
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
        out.append(_check_section(media, clues, result, frames, pictures_page, pages_dir))
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

        if result.sightings:
            out.append("<h2>Other aircraft that passed this spot</h2>"
                       "<p>The search did not use these. Each should have left a tone at a set second. "
                       "A yes means that tone is in the recording.</p>"
                       + _table(["Aircraft", "Type", "Due (UTC)", "Distance km", "In the sound?"],
                                [[_e(cell) for cell in _sighting_row(x)] for x in result.sightings]))

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
