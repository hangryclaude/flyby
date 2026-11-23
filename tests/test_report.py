import base64
import functools
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from rich.console import Console

from flyby import checks, report
from flyby.model import Clues, Event, Flight, Frame, Media, Result

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def no_zone_lookup(monkeypatch):
    # the sun link asks a web service for the time zone; these tests stay offline
    monkeypatch.setattr(checks, "_zone", lambda lat, lon: "America/New_York")
    # the flight replay only reaches a few weeks back, so the test result's day must count as recent
    monkeypatch.setattr(report, "check_links", functools.partial(checks.check_links, today=date(2026, 1, 10)))


def _media(title="Plane video"):
    return Media("https://example.com/v", Path("a.wav"), title, "plane-video", 120.0, "upload")


def _clues():
    return Clues([date(2026, 1, 2)], (24.0, 50.0, -125.0, -66.0), "continental USA", "upload day", "whole US")


def _result(verdict="located", figures=()):
    return Result(
        verdict=verdict,
        headline="Found it near Oyster Bay",
        lat=40.87654, lon=-73.53211, p=0.001, margin_pct=42.0,
        day=date(2026, 1, 2),
        recording_start_utc=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc),
        events=[Event(65.0, 180.0, 60.0, 900.0, 12.0, "pair")],
        flights=[Flight("a1b2c3", datetime(2026, 1, 2, 15, 1, 5, tzinfo=timezone.utc), 1500.0, 60.0, "N123AB", "Cessna 172")],
        runners_up=[(40.1, -73.1, 5.0)],
        figures=list(figures),
        notes=["note one"],
        searched=["2026-01-02: 1 pair found"],
    )


def test_located_page_has_coordinates_and_map(tmp_path):
    page = report.write_page(_media(), _clues(), _result(), place="Oyster Bay, New York", pages_dir=tmp_path)
    text = page.read_text()
    assert page == tmp_path / "flyby-plane-video.html"
    assert text.startswith("<!doctype html>")
    assert "40.87654, -73.53211" in text
    assert 'id="map"' in text
    assert "leaflet/1.9.4" in text
    assert "Oyster Bay, New York" in text
    assert "map_action=pano" in text and "maps.apple.com" in text


def test_no_match_page_hides_coordinates_and_map(tmp_path):
    res = _result("no match")
    page = report.write_page(_media(), _clues(), res, place="Oyster Bay", pages_dir=tmp_path)
    text = page.read_text()
    assert "40.87654" not in text and "-73.53211" not in text
    assert 'id="map"' not in text and "leaflet" not in text.lower()
    assert "Nothing was found" in text
    assert "--date" in text and "2026-01-02: 1 pair found" in text
    assert "Oyster Bay" not in text.split("<footer>")[0].replace(res.headline, "")


def test_not_sure_is_labelled_unconfirmed(tmp_path):
    text = report.write_page(_media(), _clues(), _result("not sure"), pages_dir=tmp_path).read_text()
    assert "not confirmed" in text and "40.87654" in text


def test_script_in_title_is_escaped(tmp_path):
    media = _media("<script>alert(1)</script>")
    res = _result()
    res.notes = ["<img src=x onerror=1>"]
    text = report.write_page(media, _clues(), res, place="<b>x</b>", pages_dir=tmp_path).read_text()
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<img src=x" not in text and "<b>x</b>" not in text


def test_figures_embedded(tmp_path):
    fig = tmp_path / "f.png"
    fig.write_bytes(PNG)
    text = report.write_page(_media(), _clues(), _result(figures=[fig]), pages_dir=tmp_path).read_text()
    assert "data:image/png;base64," + base64.b64encode(PNG).decode() in text
    assert "bright line" in text


def test_slug_is_sanitised(tmp_path):
    media = _media()
    media.slug = "../Evil Name"
    page = report.write_page(media, _clues(), _result(), pages_dir=tmp_path)
    assert page.parent == tmp_path and page.name == "flyby-evil-name.html"


def _render(result, **kw):
    console = Console(record=True, width=100, file=io.StringIO())
    report.print_result(console, _media(), _clues(), result, **kw)
    return console.export_text()


def test_print_result_located():
    out = _render(_result(), place="Oyster Bay, New York", page=Path("/x/p.html"))
    assert "LOCATED" in out
    assert "40.87654, -73.53211" in out
    assert "google.com/maps?q=40.87654,-73.53211" in out
    assert "1 time in 1,000" in out
    assert "N123AB" in out and "01:05" in out and "/x/p.html" in out


def test_print_result_no_match_hides_position():
    out = _render(_result("no flyovers"))
    assert "NO FLYOVERS" in out
    assert "40.87654" not in out and "google.com/maps" not in out
    assert "--date" in out and "--area" in out


def test_place_name(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        seen["url"], seen["ua"], seen["timeout"] = req.full_url, req.get_header("User-agent"), timeout
        return Resp(json.dumps({"address": {"town": "Oyster Bay", "state": "New York"}}).encode())

    monkeypatch.setattr(report.urllib.request, "urlopen", fake)
    assert report.place_name(40.87, -73.53) == "Oyster Bay, New York"
    assert seen["ua"] == report.USER_AGENT and seen["timeout"] == 6
    assert "zoom=16" in seen["url"] and "format=jsonv2" in seen["url"]


def test_place_name_fails_soft(monkeypatch):
    def boom(req, timeout):
        raise OSError("offline")

    monkeypatch.setattr(report.urllib.request, "urlopen", boom)
    assert report.place_name(1.0, 2.0) is None


def _frame(tmp_path, name, sharpness, t_s, size=(1920, 1080), colour=(200, 40, 40)):
    path = tmp_path / name
    Image.new("RGB", size, colour).save(path, "JPEG")
    return Frame(path, t_s, 0.5, 0.3, sharpness)


def test_check_section_follows_the_coordinates(tmp_path):
    text = report.write_page(_media(), _clues(), _result(), place="Oyster Bay", pages_dir=tmp_path).read_text()
    coords, section, mapdiv = text.find('id="coords"'), text.find("Check it yourself"), text.find('id="map"')
    assert 0 < coords < section < mapdiv
    assert 'href="https://www.google.com/maps/@?api=1&amp;map_action=pano&amp;viewpoint=40.87654,-73.53211"' in text
    assert "adsb.lol/?icao=a1b2c3" in text and "suncalc.org" in text and "maps.apple.com" in text
    assert "Do the roads, poles and buildings match the video?" in text
    assert "Does Street View show this place?" not in text and "data:image/jpeg" not in text
    assert "See every picture" not in text


def test_check_section_with_frames_shows_the_two_sharpest(tmp_path):
    frames = [_frame(tmp_path, "a.jpg", 1.0, 10.0), _frame(tmp_path, "b.jpg", 9.0, 30125.0),
              _frame(tmp_path, "c.jpg", 5.0, None, colour=(10, 200, 10))]
    pics = tmp_path / "flyby-plane-video-pictures.html"
    text = report.write_page(_media(), _clues(), _result(), pages_dir=tmp_path, frames=frames, pictures_page=pics).read_text()
    assert "Does Street View show this place?" in text
    assert text.count("data:image/jpeg;base64,") == 2
    assert "8:22:05 into the video" in text and "The video&#x27;s own cover picture" in text
    assert "00:10 into the video" not in text
    assert 'href="flyby-plane-video-pictures.html"' in text
    for chunk in text.split("data:image/jpeg;base64,")[1:]:
        with Image.open(io.BytesIO(base64.b64decode(chunk.split('"')[0]))) as img:
            assert img.size == (960, 540)


def test_check_section_absent_without_a_position(tmp_path):
    frames = [_frame(tmp_path, "a.jpg", 1.0, 10.0)]
    text = report.write_page(_media(), _clues(), _result("no match"), pages_dir=tmp_path, frames=frames).read_text()
    assert "Check it yourself" not in text and "data:image/jpeg" not in text


def test_check_links_are_escaped_on_the_page(tmp_path):
    res = _result()
    res.flights[0].registration = '<b>"x"</b>'
    text = report.write_page(_media(), _clues(), res, pages_dir=tmp_path).read_text()
    assert "<b>" not in text and "Flight replay: &lt;b&gt;&quot;x&quot;&lt;/b&gt;" in text


def test_print_result_has_a_short_check_list():
    out = _render(_result())
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "Check it")
    links = [line for line in lines[start + 1:start + 8] if "https://" in line]
    assert len(links) == 6
    assert "Street View: https://www.google.com/maps/@?api=1&map_action=pano" in links[0]
