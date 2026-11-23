import io
import json
import math
import urllib.error
import urllib.parse
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from flyby import clues as clues_mod
from flyby.clues import confirm_places, find_clues, geocode_area, local_days
from flyby.model import CONUS, FlybyError, Hint, Media, PlaceNotFound

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
    bbox, label, focus = geocode_area("Buffalo NY")
    assert label == "Buffalo, Erie County"
    latmin, latmax, lonmin, lonmax = bbox
    # wide enough to hold a crowd of wrong answers to judge the winner against
    assert latmax - latmin == pytest.approx(3.4)
    assert lonmax - lonmin == pytest.approx(3.4)
    assert (latmin + latmax) / 2 == pytest.approx((42.8256 + 42.9667) / 2)
    # the town itself, with room for a microphone just outside it, is the second test
    assert focus[:2] == pytest.approx((42.8256 - 0.25, 42.9667 + 0.25))
    assert focus[2] < -78.9120 - 0.25 and focus[3] > -78.7954 + 0.25
    assert seen["timeout"] == 10
    assert seen["req"].get_header("User-agent") == "flyby/0.1 (personal research tool)"
    assert "q=Buffalo+NY" in seen["req"].full_url and "limit=1" in seen["req"].full_url


def test_geocode_big_area_only_padded(monkeypatch):
    fake_urlopen(monkeypatch, "nominatim_state.json")
    seen = fake_urlopen(monkeypatch, "nominatim_state.json")
    bbox, _, focus = geocode_area("Ohio")
    assert bbox == pytest.approx((38.2034, 42.5270, -85.0203, -80.3183))
    assert focus is None                # a state is the search box, not a second test
    # asked for as a state: a plain search for "New York" or "Washington" returns the city
    assert "state=Ohio" in seen["req"].full_url and "country=United+States" in seen["req"].full_url


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
    assert c.area_label == "around Buffalo, Erie County" and c.focus_label == "Buffalo, Erie County"
    c = find_clues(m, area_arg="Buffalo", area_why='the title says "Buffalo webcam"', today=TODAY)
    assert c.area_reason.startswith('The title says "Buffalo webcam", so')
    c = find_clues(m, today=TODAY)
    # as far across as it is tall: 1.5 degrees of latitude, and the same distance in longitude at 40 N
    wide = 1.5 / math.cos(math.radians(40.0))
    assert c.bbox == pytest.approx((38.5, 41.5, -73.0 - wide, -73.0 + wide))
    assert c.focus == pytest.approx((39.75, 40.25, -73.0 - wide / 6, -73.0 + wide / 6))
    assert c.area_label == "near 40.000, -73.000" and "GPS" in c.area_reason
    typed = find_clues(media(uploaded=date(2024, 6, 1)), area_arg="41.3918, -82.5577", today=TODAY)
    assert typed.bbox[:2] == pytest.approx((39.8918, 42.8918)) and "You gave a position" in typed.area_reason
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


def test_a_typed_start_time_picks_the_days_and_carries_its_own_tolerance():
    began = datetime(2024, 6, 10, 23, 50, tzinfo=timezone.utc)
    c = find_clues(media(duration_s=1200), date_arg="2024-06-10", start_arg=began, start_tol_s=120.0, today=TODAY)
    # the exact days it ran through, not the local day and its neighbour
    assert c.days == [date(2024, 6, 10), date(2024, 6, 11)] and "You said it began at 23:50" in c.day_reason
    assert (c.start_utc, c.start_tol_s) == (began, 120.0)
    # the person's word beats the replay's own clock, which may have been cut
    replay = find_clues(media(started_utc=began - timedelta(hours=3)), start_arg=began, today=TODAY)
    assert replay.start_utc == began and replay.start_tol_s == 600.0
    assert find_clues(media(started_utc=began), today=TODAY).start_tol_s == 180.0


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


@pytest.mark.parametrize("name", ["Huron, Ohio", "Huron, OH", " huron ,oh "])
def test_a_town_in_a_state_is_asked_for_as_a_town(monkeypatch, name):
    # in plain words "Huron, Ohio" comes back as Huron County, 30 km from the town
    seen = fake_urlopen(monkeypatch)
    _, label, focus = geocode_area(name)
    asked = urllib.parse.parse_qs(urllib.parse.urlsplit(seen["req"].full_url).query)
    assert (asked["city"], asked["state"], asked["country"]) == ([name.split(",")[0].strip()], ["Ohio"], ["United States"])
    assert "q" not in asked and label == "Buffalo, Ohio" and focus is not None


def test_a_place_that_is_no_town_is_asked_for_again_in_plain_words(monkeypatch):
    asked = []

    def opener(req, timeout=None):
        asked.append(urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query))
        return io.BytesIO(b"[]" if "city" in asked[-1] else (FIX / "nominatim_buffalo.json").read_bytes())

    monkeypatch.setattr(clues_mod.urllib.request, "urlopen", opener)
    monkeypatch.setattr(clues_mod.time, "sleep", lambda s: None)
    _, label, _ = geocode_area("Port Canaveral, Florida")
    assert len(asked) == 2 and asked[1]["q"] == ["Port Canaveral, Florida"] and label == "Buffalo, Erie County"


def map_that_knows(monkeypatch, *towns, error=None):
    """A map service that knows only these towns. Returns the town names it was asked for, in order."""
    asked = []

    def opener(req, timeout=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        asked.append(query["city"][0])
        if error:
            raise error
        hit = next((t for t in towns if t.lower() == query["city"][0].lower()), None)
        return io.BytesIO(json.dumps([{"name": hit, "display_name": hit, "boundingbox": ["1", "2", "3", "4"]}] if hit else []).encode())

    monkeypatch.setattr(clues_mod.urllib.request, "urlopen", opener)
    monkeypatch.setattr(clues_mod.time, "sleep", lambda s: None)
    return asked


def town_hint(value, *towns, strength="stated"):
    return Hint("place", value, f'the title says "{towns[0]}"', strength, towns)


def test_the_map_picks_the_longest_reading_that_is_a_real_town(monkeypatch):
    # by rule this title read as "Green, Kentucky", a real hamlet 300 km from Bowling Green
    asked = map_that_knows(monkeypatch, "Bowling Green", "Green")
    day = Hint("day", "2026-09-08", "the title says so", "likely")
    out = confirm_places([town_hint("Green, Kentucky", "LIVE Bowling Green", "Bowling Green", "Green"), day])
    assert asked == ["LIVE Bowling Green", "Bowling Green"]
    assert (out[0].value, out[0].strength, out[0].towns) == ("Bowling Green, Kentucky", "stated", ()) and out[1] is day


def test_the_maps_own_spelling_of_the_town_is_used(monkeypatch):
    asked = []

    def opener(req, timeout=None):
        asked.append(req.full_url)
        return io.BytesIO(json.dumps([{"name": "Mount Vernon"}]).encode())

    monkeypatch.setattr(clues_mod.urllib.request, "urlopen", opener)
    assert confirm_places([town_hint("Mt. Vernon, Ohio", "Mt. Vernon", "Vernon")])[0].value == "Mount Vernon, Ohio"
    assert len(asked) == 1 and "state=Ohio" in asked[0]


def test_a_town_the_map_does_not_know_leaves_the_state(monkeypatch):
    asked = map_that_knows(monkeypatch)
    out = confirm_places([town_hint("Photographing Huron, Ohio", "Photographing Huron", "Huron"),
                          Hint("place", "Ohio", "a tag says so", "weak")])
    assert asked == ["Photographing Huron", "Huron"]
    assert [(h.value, h.strength) for h in out] == [("Ohio", "stated")] and "only the state" in out[0].because


def test_with_no_map_to_ask_the_hints_are_left_as_the_rules_read_them(monkeypatch):
    map_that_knows(monkeypatch, error=urllib.error.URLError("offline"))
    hints = [town_hint("Green, Kentucky", "LIVE Bowling Green", "Bowling Green", "Green")]
    assert confirm_places(hints) is hints


def test_the_map_is_asked_only_a_few_questions(monkeypatch):
    asked = map_that_knows(monkeypatch)
    many = [town_hint(f"Town{i}, Ohio", f"A B C Town{i}", f"B C Town{i}", f"C Town{i}", f"Town{i}") for i in range(4)]
    out = confirm_places(many)
    assert len(asked) <= clues_mod.MAX_MAP_QUESTIONS + 3 and len(out) >= 2      # the last hints are left unchecked


def test_a_place_no_map_knows_has_its_own_error(monkeypatch):
    monkeypatch.setattr(clues_mod.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"[]"))
    with pytest.raises(PlaceNotFound):
        geocode_area("zzzz")
    fake_urlopen(monkeypatch, error=urllib.error.URLError("offline"))
    with pytest.raises(FlybyError) as caught:
        geocode_area("zzzz")
    assert not isinstance(caught.value, PlaceNotFound)


def test_local_days():
    assert local_days("2026-09-08") == [date(2026, 9, 8)]
    assert local_days("2026-09-07..2026-09-09") == [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]
    for bad in ("garbage", "2026-09-09..2026-09-01", "2026-09-01..2026-09-30"):
        with pytest.raises(FlybyError):
            local_days(bad)


def test_a_tolerance_the_person_gave_also_loosens_a_streams_own_start_time():
    live = media(kind="live", started_utc=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc))
    assert find_clues(live, bbox_arg=(1.0, 2.0, 3.0, 4.0), today=TODAY).start_tol_s == clues_mod.STREAM_TOL_S
    assert find_clues(live, bbox_arg=(1.0, 2.0, 3.0, 4.0), start_tol_s=1200.0, today=TODAY).start_tol_s == 1200.0


def test_a_day_that_cannot_be_read_is_an_error_even_beside_a_start_time():
    start = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)
    with pytest.raises(FlybyError, match="not a date"):
        find_clues(media(), date_arg="garbage", start_arg=start, bbox_arg=(1.0, 2.0, 3.0, 4.0), today=TODAY)


def test_a_box_that_a_clue_gave_is_named_and_explained():
    zone = (24.0, 50.0, -90.5, -66.0)
    c = find_clues(media(started_utc=datetime(2026, 9, 9, 11, 45, tzinfo=timezone.utc)), bbox_arg=zone,
                   box=("the Eastern time zone", "The clock in the picture is 4 hours behind the stream's clock."),
                   today=date(2026, 11, 22))
    assert c.bbox == zone and c.area_label == "the Eastern time zone" and c.focus is None
    assert c.area_reason == ("The clock in the picture is 4 hours behind the stream's clock. "
                             "So only the Eastern time zone is searched.")


def test_a_box_the_person_typed_keeps_its_own_words():
    c = find_clues(media(started_utc=datetime(2026, 9, 9, 11, 45, tzinfo=timezone.utc)), bbox_arg=(40.0, 42.0, -84.0, -80.0),
                   today=date(2026, 11, 22))
    assert c.area_label == "custom box" and "yourself" in c.area_reason


def test_a_town_off_the_picture_that_the_map_does_not_know_leaves_only_a_guess(monkeypatch):
    # "Lufthansa Frankfurt, DE" on the side of an aircraft: no such town in Delaware, and the state is no better
    map_that_knows(monkeypatch)
    seen = replace(town_hint("Lufthansa Frankfurt, Delaware", "Lufthansa Frankfurt", "Frankfurt", strength="likely"),
                   from_picture=True)
    assert [(h.value, h.strength) for h in confirm_places([seen])] == [("Delaware", "weak")]
