import io
import math
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from flyby import clues as clues_mod
from flyby.clues import find_clues, geocode_area
from flyby.model import CONUS, FlybyError, Media

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2024, 6, 20)


def media(**kw) -> Media:
    base = dict(source="s", audio_path=Path("a.opus"), title="t", slug="t", duration_s=300.0, kind="upload")
    base.update(kw)
    return Media(**base)


def fake_urlopen(monkeypatch, fixture="nominatim_buffalo.json", error=None):
    seen = {}

    def opener(req, timeout=None):
        seen["req"], seen["timeout"] = req, timeout
        if error:
            raise error
        return io.BytesIO((FIX / fixture).read_bytes())

    monkeypatch.setattr(clues_mod.urllib.request, "urlopen", opener)
    return seen


def test_geocode_widens_small_box_and_labels(monkeypatch):
    seen = fake_urlopen(monkeypatch)
    bbox, label = geocode_area("Buffalo NY")
    assert label == "Buffalo, Erie County"
    latmin, latmax, lonmin, lonmax = bbox
    assert latmax - latmin == pytest.approx(1.4)
    assert lonmax - lonmin == pytest.approx(1.4)
    assert (latmin + latmax) / 2 == pytest.approx((42.8256 + 42.9667) / 2)
    assert seen["timeout"] == 10
    assert seen["req"].get_header("User-agent") == "flyby/0.1 (personal research tool)"
    assert "q=Buffalo+NY" in seen["req"].full_url and "limit=1" in seen["req"].full_url


def test_geocode_big_area_only_padded(monkeypatch):
    fake_urlopen(monkeypatch, "nominatim_state.json")
    bbox, _ = geocode_area("Ohio")
    assert bbox == pytest.approx((38.2034, 42.5270, -85.0203, -80.3183))


def test_geocode_failures_suggest_bbox(monkeypatch):
    fake_urlopen(monkeypatch, error=urllib.error.URLError("offline"))
    with pytest.raises(FlybyError, match="--bbox"):
        geocode_area("x")
    monkeypatch.setattr(clues_mod.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"[]"))
    with pytest.raises(FlybyError, match="--bbox"):
        geocode_area("zzzz")


def test_area_priority(monkeypatch):
    m = media(uploaded=date(2024, 6, 1), location_hint=(40.0, -73.0))
    box = (1.0, 2.0, 3.0, 4.0)
    c = find_clues(m, area_arg="usa", bbox_arg=box, today=TODAY)
    assert (c.bbox, c.area_label) == (box, "custom box")
    c = find_clues(m, area_arg="United States", today=TODAY)
    assert (c.bbox, c.area_label) == (CONUS, "continental USA")
    fake_urlopen(monkeypatch)
    c = find_clues(m, area_arg="Buffalo", today=TODAY)
    assert c.area_label == "Buffalo, Erie County"
    c = find_clues(m, today=TODAY)
    # as far across as it is tall: 0.75 degrees of latitude, and the same distance in longitude at 40 N
    assert c.bbox == pytest.approx((39.25, 40.75, -73.0 - 0.75 / math.cos(math.radians(40.0)), -73.0 + 0.75 / math.cos(math.radians(40.0))))
    assert c.area_label == "near the GPS position in the file"
    c = find_clues(media(uploaded=date(2024, 6, 1)), today=TODAY)
    assert c.bbox == CONUS and "Nothing" in c.area_reason


def test_date_single_west_and_east():
    west = find_clues(media(), date_arg="2024-06-10", today=TODAY)  # CONUS centre is west of 0
    assert west.days == [date(2024, 6, 10), date(2024, 6, 11)]
    east = find_clues(media(), date_arg="2024-06-10", bbox_arg=(40.0, 50.0, 0.0, 20.0), today=TODAY)
    assert east.days == [date(2024, 6, 10), date(2024, 6, 9)]
    assert west.start_utc is None
    # a known start time survives --date: it is the second test of any match
    began = datetime(2024, 6, 10, 20, 0, tzinfo=timezone.utc)
    replay = find_clues(media(started_utc=began), date_arg="2024-06-10", today=TODAY)
    assert replay.start_utc == began and replay.days == west.days


def test_date_range_orders_locals_first():
    c = find_clues(media(), date_arg="2024-06-10..2024-06-12", today=TODAY)
    assert c.days == [date(2024, 6, d) for d in (10, 11, 12, 13)]
    e = find_clues(media(), date_arg="2024-06-10..2024-06-12", bbox_arg=(0.0, 1.0, 5.0, 6.0), today=TODAY)
    assert e.days == [date(2024, 6, d) for d in (10, 11, 12, 9)]


def test_date_errors():
    with pytest.raises(FlybyError, match="7 days"):
        find_clues(media(), date_arg="2024-06-01..2024-06-08", today=TODAY)
    with pytest.raises(FlybyError):
        find_clues(media(), date_arg="June 1st", today=TODAY)
    find_clues(media(), date_arg="2024-06-01..2024-06-07", today=TODAY)  # exactly 7 is fine


def test_started_utc_spans_midnight():
    start = datetime(2024, 6, 10, 23, 50, tzinfo=timezone.utc)
    c = find_clues(media(started_utc=start, duration_s=1200, uploaded=date(2024, 6, 15)), today=TODAY)
    assert c.days == [date(2024, 6, 10), date(2024, 6, 11)]
    assert c.start_utc == start


def test_started_utc_long_replay_lists_every_day():
    start = datetime(2024, 6, 10, 20, 0, tzinfo=timezone.utc)
    c = find_clues(media(started_utc=start, duration_s=30 * 3600), today=TODAY)
    assert c.days == [date(2024, 6, 10), date(2024, 6, 11), date(2024, 6, 12)]


def test_uploaded_counts_back():
    c = find_clues(media(uploaded=date(2024, 6, 10)), today=TODAY)
    assert c.days == [date(2024, 6, 10), date(2024, 6, 9), date(2024, 6, 8)]
    c = find_clues(media(uploaded=date(2024, 6, 10)), days_back=0, today=TODAY)
    assert c.days == [date(2024, 6, 10)]


def test_archive_lag_drops_recent_days():
    c = find_clues(media(uploaded=TODAY), today=TODAY)
    assert c.days == [TODAY - timedelta(days=1), TODAY - timedelta(days=2)]
    assert "a day late" in c.day_reason
    with pytest.raises(FlybyError):
        find_clues(media(uploaded=TODAY), days_back=0, today=TODAY)


def test_no_day_clue():
    with pytest.raises(FlybyError, match="--date"):
        find_clues(media(), today=TODAY)


def test_today_defaults_to_utc_now():
    d = datetime.now(timezone.utc).date() - timedelta(days=10)
    assert find_clues(media(uploaded=d)).days[0] == d
