"""Picture search: one page on this Mac that gets each still picture into the big
"search by picture" sites with as few clicks as possible, plus a web search for each
line of writing read off the pictures.

A picture the site already shows the public (a YouTube thumbnail) is searched by its
link, so nothing is uploaded. A picture taken on this Mac goes nowhere until the person
presses a button for one site; then only that picture goes, and only to that site.

There are no face search sites here, and there never will be. flyby finds places, not
people: a face search turns a video of a street into a hunt for whoever walked past.
"""
from __future__ import annotations

import base64
import html
import io
import re
import urllib.parse
from pathlib import Path

from PIL import Image

from .model import Frame, Hint, Media, TextLine

# Each of these was opened in headless Chrome with the test video's public thumbnail on
# 2026-11-22. Yandex and Bing showed results (Yandex read "Main Street Railfan Huron, Ohio"
# off it). Google Lens answered 303 to its own results page; Google then showed headless
# Chrome a robot check, which a person's browser does not get.
_BY_LINK = (
    ("Google Lens", "https://lens.google.com/uploadbyurl?url={}"),
    ("Yandex", "https://yandex.com/images/search?rpt=imageview&url={}"),
    ("Bing", "https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP&sbisrc=UrlPaste&q=imgurl:{}"),
)

# Where a person pastes a picture or a picture's link. TinEye's search-by-link address
# only ever showed headless Chrome a "checking your browser" wall, so it is not trusted
# to show results and TinEye is always reached this way.
_START = (
    ("Google Lens", "https://lens.google.com/"),
    ("Yandex", "https://yandex.com/images/"),
    ("Bing", "https://www.bing.com/images/"),
    ("TinEye", "https://tineye.com/"),
)

# One-click upload of a picture that exists only on this Mac:
# (site, form address, picture field, how, other fields). "file" posts the JPEG as a file;
# "base64" posts it as text, the way Bing's own page does.
# Pressed in headless Chrome on 2026-11-22 with a picture of the test video: Bing showed
# results naming "Main Street Railfan in Huron, Ohio"; Google Lens went to its results page
# and then the same robot check as above. Yandex only ever showed its empty start page
# (as "upfile" to /images/search, with and without format=html), so it has no upload button.
_UPLOAD = (
    ("Google Lens", "https://lens.google.com/v3/upload", "encoded_image", "file", ()),
    ("Bing", "https://www.bing.com/images/search?view=detailv2&iss=sbiupload&FORM=SBIHMP&sbisrc=ImgDropper&idpbck=1",
     "imageBin", "base64", (("cbir", "sbi"),)),
)

WORDS_SEARCH = "https://www.google.com/search?q={}"
LONG_SIDE = 1280   # big enough for a search site to match; small enough to keep the page light

CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#fdfdfc;color:#1b1b1b;font:19px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:46rem;margin:0 auto;padding:32px 18px 72px}
h1{font-size:2.1rem;line-height:1.2;margin:0 0 .3em}
h2{font-size:1.35rem;margin:2.2em 0 .5em}
p{margin:.6em 0}
.sub{color:#555;margin:0 0 1.4em}
.big{font-size:1.2rem;font-weight:600;background:#eef6ea;border:1px solid #9cc48d;border-radius:10px;padding:.7em 1em;margin:1em 0}
img{display:block;width:100%;height:auto;border-radius:8px;border:1px solid #ddd;margin:.6em 0}
.row{display:flex;flex-wrap:wrap;gap:10px;margin:.6em 0 1em}
.btn{display:inline-block;font-family:inherit;font-size:18px;font-weight:600;line-height:1.3;padding:.65em 1.1em;border-radius:10px;border:1px solid #0b57a4;background:#0b57a4;color:#fff;text-decoration:none;cursor:pointer}
.btn.plain{background:#fff;color:#0b57a4}
.how{color:#555;font-size:.95rem;margin:.2em 0}
ul.words{list-style:none;padding:0;margin:.5em 0}
ul.words li{margin:.5em 0;padding:.5em .8em;background:#f4f4f2;border:1px solid #ddd;border-radius:8px}
.said{font-weight:600;overflow-wrap:anywhere}
a{color:#0b57a4}
.done{color:#1d6b2a;font-weight:600}
footer{margin-top:3em;font-size:.9rem;color:#555;border-top:1px solid #ddd;padding-top:1em}
"""

# The page carries no picture data in its script: every value it needs is read off the
# element that was clicked, which html.escape already made safe.
SCRIPT = """
function said(el, text){ el.textContent = text; }
function dataUrlToBlob(url){
  var parts = url.split(','), bytes = atob(parts[1]), out = new Uint8Array(bytes.length);
  for (var i = 0; i < bytes.length; i++) out[i] = bytes.charCodeAt(i);
  return new Blob([out], {type: 'image/jpeg'});
}
function pngOf(img){
  var c = document.createElement('canvas');
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  c.getContext('2d').drawImage(img, 0, 0);
  return new Promise(function(done){ c.toBlob(done, 'image/png'); });
}
document.addEventListener('click', function(ev){
  var b = ev.target.closest('button');
  if (!b) return;
  var note = document.getElementById(b.dataset.note);
  var img = document.getElementById(b.dataset.img);
  if (b.dataset.act === 'copy-picture') {
    // the clipboard only takes PNG pictures, so the JPEG is redrawn as one
    navigator.clipboard.write([new ClipboardItem({'image/png': pngOf(img)})]).then(
      function(){ said(note, 'Picture copied. Open a site below and paste it there.'); },
      function(){ said(note, 'Copy did not work. Drag the picture onto the site instead.'); });
  } else if (b.dataset.act === 'copy-link') {
    navigator.clipboard.writeText(b.dataset.link).then(
      function(){ said(note, 'Link copied. Paste it into the site.'); },
      function(){ said(note, 'Copy did not work. The link is: ' + b.dataset.link); });
  } else if (b.dataset.act === 'upload') {
    var form = document.getElementById(b.dataset.form);
    var field = form.elements[0];
    if (field.type === 'file') {
      var t = new DataTransfer();
      t.items.add(new File([dataUrlToBlob(img.src)], 'picture.jpg', {type: 'image/jpeg'}));
      field.files = t.files;
    } else {
      field.value = img.src.split(',')[1];
    }
    form.submit();
    said(note, 'Sent to ' + b.dataset.site + '. Its answer opens in a new tab.');
  }
});
window.__ready = true;
"""


def _q(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def search_links(picture_url: str) -> list[tuple[str, str]]:
    """(site, link) pairs that search for a picture anyone can open, by its link alone."""
    return [(site, pattern.format(_q(picture_url))) for site, pattern in _BY_LINK]


def words_link(text: str, place: str | None = None) -> str:
    """A web search for the exact words read off a sign, near the place when one is known."""
    # a quote mark inside the words would end the exact-words search early
    words = " ".join(text.replace('"', " ").split())
    query = f'"{words}"' if words else ""
    if place:
        query = f"{query} {place}".strip()
    return WORDS_SEARCH.format(urllib.parse.quote_plus(query))


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _slug(raw: str) -> str:
    # the same cleaning as report._slug, so a video's pages sit side by side under one name
    return re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-") or "recording"


def _when(t_s: float | None) -> str:
    if t_s is None:
        return "The site's own cover picture"
    total = int(round(t_s))
    hours, minutes, seconds = total // 3600, total % 3600 // 60, total % 60
    if hours:
        return f"{hours} h {minutes:02d} min in"
    if minutes:
        return f"{minutes} min in"
    return f"{seconds} s in"


def _data_url(path: Path) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((LONG_SIDE, LONG_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _place(hints: list[Hint]) -> str | None:
    # a town name narrows a sign search well; a bare "lat,lon" means nothing to a web search
    for hint in hints:
        if hint.kind == "place" and hint.strength in ("stated", "likely") and not re.fullmatch(r"[-\d.,\s]+", hint.value):
            return hint.value
    return None


def _lines_for(frame: Frame, lines: list[TextLine]) -> list[TextLine]:
    if any(line.frame is not None for line in lines):
        return [line for line in lines if line.frame == frame.path]
    return [line for line in lines if line.t_s is not None and line.t_s == frame.t_s]


def _picture(n: int, frame: Frame, lines: list[TextLine], place: str | None) -> str:
    img, note = f"pic{n}", f"note{n}"
    out = [f"<section><h2>Picture {n}: {_e(_when(frame.t_s))}</h2>",
           f'<img id="{img}" src="{_data_url(frame.path)}" alt="Picture {n} from the video">']
    if frame.public_url:
        out.append('<p class="how">Anyone can open this picture, so each button sends only its link.</p>')
        out.append('<div class="row">')
        for site, link in search_links(frame.public_url):
            out.append(f'<a class="btn" target="_blank" rel="noopener noreferrer" href="{_e(link)}">Search {_e(site)}</a>')
        out.append("</div>")
        out.append('<p class="how">TinEye: copy the link, then paste it on TinEye.</p><div class="row">')
        out.append(f'<button class="btn plain" data-act="copy-link" data-link="{_e(frame.public_url)}" '
                   f'data-note="{note}">Copy picture link</button>')
        out.append('<a class="btn plain" target="_blank" rel="noopener noreferrer" href="https://tineye.com/">Open TinEye</a></div>')
    else:
        out.append('<p class="how">This picture is only on this Mac. A blue button sends it to that one site.</p>')
        out.append('<div class="row">')
        for k, (site, action, field, how, extra) in enumerate(_UPLOAD):
            form = f"form{n}_{k}"
            kind = 'type="file"' if how == "file" else 'type="hidden"'
            # the picture field must come first: the page's script fills form.elements[0]
            fields = f'<input {kind} name="{_e(field)}">' + "".join(
                f'<input type="hidden" name="{_e(name)}" value="{_e(value)}">' for name, value in extra)
            out.append(f'<form id="{form}" method="post" enctype="multipart/form-data" action="{_e(action)}" '
                       f'target="_blank" hidden>{fields}</form>')
            out.append(f'<button class="btn" data-act="upload" data-form="{form}" data-img="{img}" data-note="{note}" '
                       f'data-site="{_e(site)}">Search {_e(site)}</button>')
        out.append("</div>")
        out.append('<p class="how">Or copy it, open a site, and paste it there.</p><div class="row">')
        out.append(f'<button class="btn plain" data-act="copy-picture" data-img="{img}" data-note="{note}">Copy picture</button>')
        for site, link in _START:
            out.append(f'<a class="btn plain" target="_blank" rel="noopener noreferrer" href="{_e(link)}">Open {_e(site)}</a>')
        out.append("</div>")
    out.append(f'<p class="done" id="{note}" aria-live="polite"></p>')
    mine = _lines_for(frame, lines)
    if mine:
        out.append("<p>Writing on this picture:</p><ul class=\"words\">")
        for line in mine:
            out.append(f'<li><span class="said">{_e(line.text)}</span><br>'
                       f'<a target="_blank" rel="noopener noreferrer" href="{_e(words_link(line.text, place))}">Search these words</a></li>')
        out.append("</ul>")
    out.append("</section>")
    return "\n".join(out)


def write_page(media: Media, frames: list[Frame], lines: list[TextLine], hints: list[Hint], pages_dir: Path) -> Path:
    """Write the picture search page for one video and return its path."""
    place = _place(hints)
    body = [f"<h1>Pictures from {_e(media.title)}</h1>",
            '<p class="big">Press a search button under a picture. The site shows other places on the web '
            'where that picture, or the same spot, appears.</p>']
    body += [_picture(n, frame, lines, place) for n, frame in enumerate(frames, 1)]
    if not frames:
        body.append("<p>No pictures were taken from this video.</p>")
    words = f"those words, and the place {_e(place)}," if place else "those words"
    body.append("<footer><p>Nothing leaves this Mac until you press a button. A search button sends one picture, "
                "or only its link, to the one site named on it. "
                f'"Search these words" sends {words} to Google. Copy buttons put things on your own clipboard.</p>'
                "<p>There are no face searches here. flyby looks for places, not people.</p></footer>")
    doc = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           f"<title>Pictures: {_e(media.title)}</title><style>{CSS}</style></head>"
           f"<body><main>{''.join(body)}</main><script>{SCRIPT}</script></body></html>")
    pages_dir.mkdir(parents=True, exist_ok=True)
    path = pages_dir / f"flyby-{_slug(media.slug)}-pictures.html"
    path.write_text(doc, encoding="utf-8")
    return path
