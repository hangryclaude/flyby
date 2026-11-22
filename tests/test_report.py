import base64
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

from rich.console import Console

from flyby import report
from flyby.model import Clues, Event, Flight, Media, Result

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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
