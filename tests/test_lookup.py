import base64
import io
import re
import urllib.parse
from pathlib import Path

from PIL import Image

from flyby import lookup
from flyby.lookup import search_links, words_link, write_page
from flyby.model import Frame, Hint, Media, TextLine

THUMB = "https://i.ytimg.com/vi/34hs2t_6n2Y/maxresdefault.jpg"
# every face search site known to work by picture: none may ever appear on the page
FACE_SITES = ("pimeyes", "facecheck", "findclone", "search4faces", "faceagle", "betaface", "clearview", "lenso")


def _jpeg(path: Path, size=(1920, 1080)) -> Path:
    Image.new("RGB", size, (90, 120, 160)).save(path, "JPEG")
    return path


def _media(slug="34hs2t_6n2Y"):
    return Media("https://www.youtube.com/watch?v=34hs2t_6n2Y", None, "Main Street Railfan", slug, 42900.0, "live",
                 video={"has_video": True, "site": "youtube", "id": "34hs2t_6n2Y"})


def _page(tmp_path, frames, lines=(), hints=(), slug="34hs2t_6n2Y"):
    path = write_page(_media(slug), list(frames), list(lines), list(hints), tmp_path / "pages")
    return path, path.read_text(encoding="utf-8")


def test_search_links_send_the_whole_link_encoded():
    links = dict(search_links(THUMB + "?a=1&b=2"))
    assert set(links) == {"Google Lens", "Yandex", "Bing"}
    want = urllib.parse.quote(THUMB + "?a=1&b=2", safe="")
    assert want.startswith("https%3A%2F%2Fi.ytimg.com%2Fvi%2F")
    for link in links.values():
        assert want in link
        # an unencoded & would cut the picture's own link short at the site's end
        assert "&b=2" not in link
    assert links["Google Lens"] == "https://lens.google.com/uploadbyurl?url=" + want
    assert links["Yandex"] == "https://yandex.com/images/search?rpt=imageview&url=" + want
    assert links["Bing"].endswith("&q=imgurl:" + want)


def test_words_link_searches_the_exact_words_near_the_place():
    link = words_link("  Main Street   Railfan ", "Huron, Ohio")
    assert link.startswith("https://www.google.com/search?q=")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)["q"][0]
    assert query == '"Main Street Railfan" Huron, Ohio'


def test_words_link_without_a_place_and_with_quote_marks():
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(words_link('Joe\'s "Bait" & Tackle')).query)["q"][0]
    assert query == '"Joe\'s Bait & Tackle"'


def test_page_for_a_public_picture_searches_by_link_and_uploads_nothing(tmp_path):
    frame = Frame(_jpeg(tmp_path / "thumb.jpg"), None, public_url=THUMB)
    path, page = _page(tmp_path, [frame])
    assert path.name == "flyby-34hs2t-6n2y-pictures.html"
    for _, link in search_links(THUMB):
        assert html_attr(link) in page
    assert "<form" not in page
    assert "The site&#x27;s own cover picture" in page
    assert 'data-act="copy-link"' in page and "https://tineye.com/" in page
    assert "window.__ready = true" in page


def test_page_for_a_picture_only_on_this_mac_embeds_it_and_offers_upload(tmp_path):
    frame = Frame(_jpeg(tmp_path / "f.jpg"), 3725.0)
    _, page = _page(tmp_path, [frame])
    assert "1 h 02 min in" in page
    assert "uploadbyurl" not in page and "imgurl:" not in page
    sent = re.findall(r'<form [^>]*action="([^"]+)"', page)
    assert [urllib.parse.urlsplit(a.replace("&amp;", "&")).netloc for a in sent] == ["lens.google.com", "www.bing.com"]
    assert 'data-act="copy-picture"' in page
    for start in ("https://lens.google.com/", "https://yandex.com/images/", "https://www.bing.com/images/", "https://tineye.com/"):
        assert f'href="{start}"' in page
    # the picture rides inside the page, shrunk so its long side is at most 1280
    data = re.search(r'src="data:image/jpeg;base64,([^"]+)"', page).group(1)
    with Image.open(io.BytesIO(base64.b64decode(data))) as im:
        assert im.size == (1280, 720)


def test_the_page_loads_nothing_from_outside(tmp_path):
    frames = [Frame(_jpeg(tmp_path / "a.jpg"), None, public_url=THUMB), Frame(_jpeg(tmp_path / "b.jpg"), 40.0)]
    _, page = _page(tmp_path, frames)
    assert re.findall(r'src="(?!data:)', page) == []
    assert "<link" not in page


def test_writing_is_shown_under_its_own_picture_with_a_search(tmp_path):
    a, b = _jpeg(tmp_path / "a.jpg"), _jpeg(tmp_path / "b.jpg")
    lines = [TextLine("Main Street Railfan - Huron OHIO", 0.9, 0, 0.9, 0.4, 0.05, 9000.0, a),
             TextLine("Shell", 0.8, 0.5, 0.5, 0.1, 0.05, 30000.0, b)]
    hints = [Hint("place", "41.39,-82.55", "a tag", "stated"), Hint("place", "Huron, Ohio", "the sign", "likely")]
    _, page = _page(tmp_path, [Frame(a, 9000.0), Frame(b, 30000.0)], lines, hints)
    first, second = page.split("<h2>Picture 2")
    assert "Main Street Railfan - Huron OHIO" in first and "Shell" not in first
    assert "Shell" in second
    assert html_attr(words_link("Shell", "Huron, Ohio")) in second
    assert page.count(">Search these words</a>") == 2
    assert "8 h 20 min in" in second


def test_weak_or_numeric_place_hints_are_left_out_of_word_searches(tmp_path):
    a = _jpeg(tmp_path / "a.jpg")
    lines = [TextLine("Shell", 0.8, 0, 0, 0.1, 0.1, 5.0, a)]
    hints = [Hint("place", "Ohio", "a guess", "weak"), Hint("place", "41.39, -82.55", "a tag", "stated")]
    _, page = _page(tmp_path, [Frame(a, 5.0)], lines, hints)
    assert html_attr(words_link("Shell")) in page
    assert "5 s in" in page


def test_a_sign_that_reads_script_is_shown_as_text(tmp_path):
    a = _jpeg(tmp_path / "a.jpg")
    lines = [TextLine('<script>alert("x")</script>', 0.5, 0, 0, 0.1, 0.1, 5.0, a)]
    _, page = _page(tmp_path, [Frame(a, 5.0)], lines, slug="<script>")
    assert page.count("<script>") == 1
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page


def test_slug_is_cleaned_like_the_report_page(tmp_path):
    path, _ = _page(tmp_path, [], slug="My Video!! 2026")
    assert path.name == "flyby-my-video-2026-pictures.html"
    path, page = _page(tmp_path, [], slug="???")
    assert path.name == "flyby-recording-pictures.html"
    assert "No pictures were taken" in page


def test_no_face_search_site_appears_anywhere(tmp_path):
    frames = [Frame(_jpeg(tmp_path / "a.jpg"), None, public_url=THUMB), Frame(_jpeg(tmp_path / "b.jpg"), 40.0)]
    _, page = _page(tmp_path, frames)
    source = Path(lookup.__file__).read_text(encoding="utf-8").lower()
    for site in FACE_SITES:
        assert site not in page.lower()
        assert site not in source
    assert "no face search" in page.lower()


def test_footer_says_what_leaves_this_mac(tmp_path):
    _, page = _page(tmp_path, [Frame(_jpeg(tmp_path / "a.jpg"), 40.0)])
    footer = page.split("<footer>")[1]
    assert "Nothing leaves this Mac until you press a button" in footer
    assert "Google" in footer


def html_attr(link: str) -> str:
    return link.replace("&", "&amp;")
