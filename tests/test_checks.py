from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from flyby import checks
from flyby.model import Clues, Flight, Media, Result

TODAY = date(2026, 1, 10)


@pytest.fixture(autouse=True)
def eastern(monkeypatch):
    monkeypatch.setattr(checks, "_zone", lambda lat, lon: "America/New_York")


def _media(**video):
    m = Media("https://www.youtube.com/watch?v=abc", Path("a.wav"), "Plane video", "plane-video", 3600.0, "live")
    m.video = {"has_video": True, **video}
    return m


def _clues():
    return Clues([date(2026, 1, 2)], (24.0, 50.0, -125.0, -66.0), "continental USA", "upload day", "whole US")


def _result(verdict="located"):
    return Result(
        verdict=verdict, headline="Found it", lat=40.87654, lon=-73.53211, day=date(2026, 1, 2),
        recording_start_utc=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc),
        flights=[Flight("a1b2c3", datetime(2026, 1, 2, 15, 1, 5, tzinfo=timezone.utc), 1500.0, 60.0, "N123AB", "Cessna 172")],
    )


def _links(result=None, media=None, today=TODAY):
    return {c.name: c.url for c in checks.check_links(result or _result(), _clues(), media or _media(), today=today)}


def test_exact_links_for_a_known_result():
    links = _links()
    assert list(links) == ["Street View", "Satellite", "Flight replay: N123AB", "Sun and shadows", "Apple Look Around",
                           "Mapillary", "KartaView", "Bing aerial", "Google Maps", "Apple Maps", "OpenStreetMap"]
    assert links["Street View"] == "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=40.87654,-73.53211"
    assert links["Satellite"] == ("https://www.google.com/maps/@?api=1&map_action=map&center=40.87654,-73.53211"
                                  "&zoom=19&basemap=satellite")
    assert links["Flight replay: N123AB"] == ("https://adsb.lol/?icao=a1b2c3&showTrace=2026-01-02"
                                             "&startTime=14:51:05&endTime=15:11:05&timestamp=1767366065")
    # 15:01 UTC is 10:01 in New York in January
    assert links["Sun and shadows"] == "https://www.suncalc.org/#/40.87654,-73.53211,17/2026.01.02/10:01/1/3"
    assert links["Apple Look Around"] == "https://maps.apple.com/look-around?coordinate=40.87654,-73.53211"
    assert links["Mapillary"] == "https://www.mapillary.com/app/?lat=40.87654&lng=-73.53211&z=17"
    assert links["KartaView"] == "https://kartaview.org/map/@40.87654,-73.53211,17z"
    assert links["Bing aerial"] == "https://www.bing.com/maps?cp=40.87654~-73.53211&lvl=19&style=a"
    assert links["Google Maps"] == "https://www.google.com/maps/search/?api=1&query=40.87654,-73.53211"
    assert links["Apple Maps"] == "https://maps.apple.com/place?coordinate=40.87654,-73.53211&name=flyby&map=satellite"
    assert links["OpenStreetMap"] == ("https://www.openstreetmap.org/?mlat=40.87654&mlon=-73.53211"
                                      "#map=18/40.87654/-73.53211")


def test_every_check_says_what_to_look_for():
    for c in checks.check_links(_result(), _clues(), _media(), today=TODAY):
        assert c.look_for and c.look_for.endswith((".", "?"))


def test_no_position_gives_nothing():
    assert checks.check_links(_result("no match"), _clues(), _media()) == []
    assert checks.check_links(_result("no flyovers"), _clues(), _media()) == []
    res = _result()
    res.lat = None
    assert checks.check_links(res, _clues(), _media()) == []


def test_not_sure_still_gets_links():
    assert "Street View" in _links(_result("not sure"))


def test_old_flight_has_no_replay():
    # the public replay stops about six weeks back
    assert not any(name.startswith("Flight replay") for name in _links(today=date(2026, 3, 1)))


def test_replay_window_stops_at_the_day_edge():
    res = _result()
    res.flights[0].heard_utc = datetime(2026, 1, 2, 0, 3, 0, tzinfo=timezone.utc)
    q = parse_qs(urlsplit(_links(res)["Flight replay: N123AB"]).query)
    assert q["startTime"] == ["00:00:00"] and q["endTime"] == ["00:13:00"]


def test_odd_text_is_escaped_in_links():
    res = _result()
    res.flights[0].icao = "a1&b=<x>"
    res.flights[0].registration = None
    url = _links(res, media=_media(site="youtube", id="a&b c"))["Flight replay: a1&b=<x>"]
    assert "<" not in url and "&b=" not in url
    assert parse_qs(urlsplit(url).query)["icao"] == ["a1&b=<x>"]


def test_video_link_at_the_first_plane():
    media = _media(site="youtube", id="34hs2t_6n2Y")
    media.started_utc = datetime(2026, 1, 2, 14, 30, 0, tzinfo=timezone.utc)
    cs = checks.check_links(_result(), _clues(), media, today=TODAY)
    assert cs[1].name == "The video at that moment"
    assert cs[1].url == "https://www.youtube.com/watch?v=34hs2t_6n2Y&t=1865s"


def test_no_video_link_without_a_start_time_or_past_the_end():
    media = _media(site="youtube", id="34hs2t_6n2Y")
    assert "The video at that moment" not in _links(media=media)
    media.started_utc = datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone.utc)   # 5 h before, video is 1 h
    assert "The video at that moment" not in _links(media=media)


def test_sun_without_a_time_zone_says_utc(monkeypatch):
    monkeypatch.setattr(checks, "_zone", lambda lat, lon: None)
    sun = next(c for c in checks.check_links(_result(), _clues(), _media(), today=TODAY) if c.name == "Sun and shadows")
    assert sun.url == "https://www.suncalc.org/#/40.87654,-73.53211,17/2026.01.02"
    assert "15:01 UTC" in sun.look_for


def test_sun_uses_summer_time():
    res = _result()
    res.flights[0].heard_utc = datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc)
    assert "/2026.09.09/12:00/" in _links(res, today=date(2026, 9, 10))["Sun and shadows"]


def test_zone_lookup_fails_soft(monkeypatch):
    monkeypatch.undo()

    def boom(req, timeout):
        raise OSError("offline")

    monkeypatch.setattr(checks.urllib.request, "urlopen", boom)
    checks._zone.cache_clear()
    assert checks._zone(1.0, 2.0) is None
