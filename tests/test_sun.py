import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from flyby import sun
from flyby.model import CONUS

FIXTURES = Path(__file__).parent / "fixtures" / "sun"
REAL = sorted(FIXTURES.glob("*.json"))
HURON = (41.395, -82.555)
LOS_ANGELES = (34.05, -118.25)
START = datetime(2026, 10, 27, 21, 0, tzinfo=timezone.utc)


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


# The US Naval Observatory's own almanac (aa.usno.navy.mil/api/celnav, "hc": the sun's centre
# with no bending by the air), fetched 2026-11-22. An independent source from NOAA's equations.
USNO = [
    (41.395, -82.555, "2026-09-09T23:30:00", 2.826),
    (41.395, -82.555, "2026-10-28T23:00:22", -6.291),
    (34.05, -118.25, "2026-06-21T19:00:00", 73.988),
    (25.76, -80.19, "2026-12-21T12:00:00", -1.444),
    (47.61, -122.33, "2026-03-20T02:15:00", 0.164),
    (39.74, -104.99, "2027-01-15T15:30:00", 10.559),
    (-33.87, 151.21, "2026-07-01T02:00:00", 33.017),
]


@pytest.mark.parametrize("lat, lon, when, want", USNO)
def test_elevation_matches_the_naval_observatory(lat, lon, when, want):
    assert float(sun.elevation(lat, lon, utc(when))) == pytest.approx(want, abs=0.5)
    # it is in fact far closer than the half degree asked for
    assert float(sun.elevation(lat, lon, utc(when))) == pytest.approx(want, abs=0.05)


def test_elevation_takes_arrays_and_any_timezone():
    lats = np.array([[41.395, 34.05]])
    lons = np.array([[-82.555, -118.25]])
    eastern = utc("2026-10-28T23:00:22").astimezone(timezone(timedelta(hours=-4)))
    got = sun.elevation(lats, lons, eastern)
    assert got.shape == (1, 2)
    assert got[0, 0] == pytest.approx(-6.291, abs=0.05)
    assert got[0, 1] > 10          # still afternoon in Los Angeles


def series(level, hours=3.0, step=20.0, colour=None, seed=1):
    """(t, brightness, colour) every `step` s, brightness from level(t), with a little noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, hours * 3600, step)
    b = np.array([level(x) for x in t]) + rng.normal(0, 0.005, len(t))
    c = np.array([colour(x) for x in t]) if colour else 0.06 + rng.normal(0, 0.004, len(t))
    return [(float(x), float(y), float(z)) for x, y, z in zip(t, b, c)]


def fade(at, top=0.45, bottom=0.30, over=1500.0):
    return lambda x: float(np.interp(x, [at - over / 2, at + over / 2], [top, bottom]))


def test_a_clean_dusk_is_found_at_the_halfway_point():
    found = sun.changes(series(fade(7200)), START)
    assert len(found) == 1
    assert found[0].kind == "dusk"
    assert found[0].t_s == pytest.approx(7200, abs=120)
    assert found[0].utc == START + timedelta(seconds=found[0].t_s)


def test_a_dawn_runs_the_other_way():
    found = sun.changes(series(fade(5400, top=0.30, bottom=0.45)), START)
    assert [c.kind for c in found] == ["dawn"]
    assert found[0].t_s == pytest.approx(5400, abs=120)


def test_a_cloud_a_train_and_headlights_are_not_a_change():
    def level(x):
        if 3000 <= x <= 3900:          # a cloud's shadow for a quarter of an hour
            return 0.30
        if 6000 <= x <= 6060:          # a train's lights
            return 0.9
        return 0.45
    assert sun.changes(series(level), START) == []


def test_no_change_and_too_few_pictures():
    assert sun.changes(series(lambda x: 0.45), START) == []
    assert sun.changes(series(lambda x: 0.30), START) == []
    assert sun.changes([(0, 0.4, 0.1), (10, 0.3, 0.0)], START) == []


def test_a_change_with_no_steady_side_is_not_counted():
    # it went dark ten minutes before the video ended: it might only be a cloud
    assert sun.changes(series(fade(3 * 3600 - 600, over=300)), START) == []


def test_a_switch_to_grey_night_vision():
    # the camera's infrared lamp makes the night picture brighter than the day, but it is grey
    def colour(x):
        return 0.12 if x < 6000 else 0.004
    found = sun.changes(series(lambda x: 0.40 if x < 6000 else 0.48, colour=colour), START)
    assert [c.kind for c in found] == ["dusk"]
    assert found[0].t_s == pytest.approx(6000, abs=60)


def load(path):
    d = json.loads(path.read_text())
    return d, datetime.fromisoformat(d["started_utc"])


def test_the_fixtures_are_real_replays():
    assert len(REAL) == 5
    for path in REAL:
        d, start = load(path)
        assert d["sources"][0].startswith("https://www.youtube.com/watch?v=")
        assert len(d["light"]) > 400


@pytest.mark.parametrize("path", REAL, ids=[p.stem for p in REAL])
def test_each_real_replay_has_one_change_inside_the_band(path):
    d, start = load(path)
    found = sun.changes(d["light"], start)
    assert [c.kind for c in found] == [path.stem.split("_")[0]]
    h = float(sun.elevation(d["place"]["lat"], d["place"]["lon"], found[0].utc))
    # measured -7.1 to -6.0; the band leaves room for other cameras
    assert -7.5 < h < -5.5
    assert sun.BAND[0] + 3 < h < sun.BAND[1] - 3


@pytest.mark.parametrize("path", REAL, ids=[p.stem for p in REAL])
def test_the_strip_holds_the_true_place(path):
    d, start = load(path)
    got = sun.strip(sun.changes(d["light"], start))
    latmin, latmax, lonmin, lonmax = got.bbox
    assert latmin <= d["place"]["lat"] <= latmax and lonmin <= d["place"]["lon"] <= lonmax
    assert 0.1 < got.share < 0.5 and type(got.share) is float
    assert not (lonmin <= LOS_ANGELES[1] <= lonmax)
    assert "UTC" in got.why and " W " in got.why + " "


def test_two_changes_narrow_the_strip_further():
    d1, s1 = load(FIXTURES / "dusk_2026-10-27.json")
    d2, s2 = load(FIXTURES / "dawn_2026-10-28.json")
    both = sun.changes(d1["light"], s1) + sun.changes(d2["light"], s2)
    one = sun.strip(both[:1])
    two = sun.strip(both)
    assert two.share <= one.share
    assert two.bbox[2] <= HURON[1] <= two.bbox[3]
    assert "1 more change like it" in two.why


def test_strip_gives_up_when_it_narrows_nothing_or_nothing_fits():
    assert sun.strip([]) is None
    noon = sun.Change("dusk", utc("2026-06-21T17:00:00"), 0.0)   # the sun high over the whole country
    assert sun.strip([noon]) is None
    # a strip that covers the whole of a small box narrows nothing
    dusk = sun.changes(json.loads((FIXTURES / "dusk_2026-10-27.json").read_text())["light"], utc("2026-10-27T21:10:21"))
    assert sun.strip(dusk, within=(41.0, 42.0, -83.0, -82.0)) is None


def test_strip_inside_a_smaller_box():
    d, start = load(FIXTURES / "dusk_2026-10-27.json")
    ohio = (38.0, 42.5, -85.0, -80.0)
    east = (35.0, 45.0, -95.0, -70.0)
    assert sun.strip(sun.changes(d["light"], start), within=ohio) is None
    got = sun.strip(sun.changes(d["light"], start), within=east)
    assert east[0] <= got.bbox[0] and got.bbox[3] <= east[3]
    assert got.share < 0.2


def plain(sentences):
    for s in sentences:
        assert s.endswith(".") and len(s) < 220
        for word in ("ADS-B", "frame", "elevation", "azimuth", "twilight", "CONUS"):
            assert word not in s


@pytest.mark.parametrize("path", REAL, ids=[p.stem for p in REAL])
def test_daylight_fits_at_the_true_place(path):
    d, start = load(path)
    said = sun.daylight(*HURON, d["light"], start)
    plain(said)
    text = " ".join(said)
    assert "Check the place and the time" not in text
    assert "That fits." in text


@pytest.mark.parametrize("path", REAL, ids=[p.stem for p in REAL])
def test_daylight_clashes_far_from_the_true_place(path):
    d, start = load(path)
    said = sun.daylight(*LOS_ANGELES, d["light"], start)
    plain(said)
    assert any("Check the place and the time" in s for s in said)


def test_daylight_night_picture_with_the_sun_high():
    noon = utc("2026-07-01T16:00:00")        # noon in Ohio
    said = sun.daylight(*HURON, series(lambda x: 0.05, hours=1), noon)
    plain(said)
    assert "looks like night" in said[0] and "Check the place and the time" in said[0]


def test_daylight_bright_colour_in_the_middle_of_the_night():
    night = utc("2026-07-01T06:00:00")       # 2 am in Ohio
    said = sun.daylight(*HURON, series(lambda x: 0.55, hours=1, colour=lambda x: 0.2), night)
    plain(said)
    assert "bright daylight" in said[0]


def test_daylight_a_grey_picture_at_midnight_fits():
    night = utc("2026-07-01T06:00:00")
    said = sun.daylight(*HURON, series(lambda x: 0.4, hours=1, colour=lambda x: 0.005), night)
    plain(said)
    assert said[0].startswith("The light fits")


def test_daylight_cannot_tell_when_the_sun_is_low_or_there_is_nothing():
    dusk = utc("2026-10-27T22:40:00")        # sun between 0 and -4 degrees for the next 20 min
    said = sun.daylight(*HURON, series(lambda x: 0.4, hours=0.3), dusk)
    plain(said)
    assert "says nothing" in said[0]
    assert sun.daylight(*HURON, [], dusk) == ["There are no pictures to check the light against."]


def test_daylight_mixed_light_is_not_a_clash():
    noon = utc("2026-07-01T16:00:00")
    said = sun.daylight(*HURON, series(lambda x: 0.05 if x < 1800 else 0.45, hours=1), noon)
    plain(said)
    assert "cannot tell" in said[0]
    assert not any("Check the place and the time" in s for s in said)


def test_a_dusk_where_the_sun_was_coming_up_does_not_fit():
    # 11:21 UTC is dawn in Ohio: the sun is 7 degrees under the horizon there, the right height, but rising
    dawn = utc("2026-10-27T11:21:00")
    assert float(sun.elevation(*HURON, dawn)) == pytest.approx(-7, abs=1.5)
    dusk_there = sun.Change("dusk", dawn, 0.0)
    assert sun.strip([dusk_there]) is None                      # nowhere in the USA was the sun going down then
    assert sun.strip([sun.Change("dawn", dawn, 0.0)]) is not None
    # the picture goes dark half an hour in, with a clock that puts that moment at dawn
    light = series(fade(1860.0), hours=1.5)
    said = sun.daylight(*HURON, light, dawn - timedelta(seconds=1860))
    assert any("went dark" in s and "coming up" in s and "Check the place and the time" in s for s in said)
    assert not any("That fits" in s for s in said)
