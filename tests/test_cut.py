"""The small index cut out of a big one must give a search exactly what the big one would have."""
import fcntl
import json

import numpy as np
import pytest
from dopplertrack.geo import enu_to_lla, lla_to_enu
from dopplertrack.search import DayIndex

from flyby.dayindex import cut_index, flights_file
from flyby.model import NoFlightData
from flyby.runner import one_at_a_time
from flyby.verify import Tracks
from test_verify import START, day_index, straight

LAT0, LON0 = 40.0, -100.0       # the frame test_verify's day_index writes


def flight_at(lat, lon, when_s=900.0, **kw):
    """A level pass whose closest point to (lat, lon) is right over it."""
    t, enu, spd = straight(when_s, 0.0, **kw)
    return t, enu + lla_to_enu(lat, lon, 0.0, LAT0, LON0), spd


@pytest.fixture
def big(tmp_path):
    flights = [("a00001", flight_at(40.0, -100.0)),                 # in the box
               ("a00002", flight_at(47.0, -90.0)),                  # far away
               ("a00003", flight_at(40.67, -100.0)),                # 8 km outside the box: heard from its edge
               ("a00004", flight_at(40.2, -99.5, when_s=4000.0)),   # in the box, later
               ("a00005", flight_at(33.0, -110.0))]
    path = tmp_path / "2026-09-08_24.0_50.0_-125.0_-66.0.npz"
    day_index(path, flights)
    flights_file(path).write_text(json.dumps({name: [name.upper(), "C172", "Cessna 172"] for name, _ in flights}))
    return path


BOX = (39.5, 40.6, -100.5, -99.0)


def test_cut_keeps_whole_flights_in_and_near_the_box_in_the_same_frame(big, tmp_path):
    cut = cut_index(big, BOX, tmp_path / "2026-09-08_39.5_40.6_-100.5_-99.0.npz")
    small, whole = DayIndex.load(cut), DayIndex.load(big)       # the engine's own loader reads both
    assert list(small.icao) == ["a00001", "a00003", "a00004"]
    assert list(np.load(cut)["parent_seg"]) == [0, 2, 3]
    assert (small.lat0, small.lon0, small.bucket_s) == (whole.lat0, whole.lon0, whole.bucket_s)
    for k, parent in enumerate((0, 2, 3)):
        for a, b in ((small.segs[k].t, whole.segs[parent].t), (small.segs[k].enu, whole.segs[parent].enu),
                     (small.segs[k].speed, whole.segs[parent].speed)):
            assert np.array_equal(a, b)
    # the bucket samples the search joins on: still sorted, and each still points at the same position
    assert np.all(np.diff(small.sb) >= 0)
    assert np.array_equal(small.spos, small.enu[small.off[small.ss] + small.sj])
    mine = np.isin(whole.ss, [0, 2, 3])
    assert np.array_equal(small.sb, whole.sb[mine]) and np.array_equal(small.spos, whole.spos[mine])
    assert json.loads(flights_file(cut).read_text()).keys() == {"a00001", "a00003", "a00004"}


def test_a_search_of_the_box_finds_the_same_flights_in_the_cut_as_in_the_whole(big, tmp_path):
    cut = cut_index(big, BOX, tmp_path / "cut.npz")
    small, whole = Tracks(cut), Tracks(big)
    rng = np.random.default_rng(4)
    spots = [(rng.uniform(BOX[0], BOX[1]), rng.uniform(BOX[2], BOX[3])) for _ in range(40)]
    spots += [(BOX[1], -100.0), (BOX[0], BOX[2]), (BOX[1], BOX[3])]     # the edges are where a margin is needed
    met = set()
    for lat, lon in spots:
        obs = lla_to_enu(lat, lon, 0.0, LAT0, LON0)
        a = small.near(obs, START, START + 5000.0, 12_000.0)
        b = whole.near(obs, START, START + 5000.0, 12_000.0)
        assert [s.icao for s in a] == [s.icao for s in b]
        assert all(np.array_equal(x.enu, y.enu) and np.array_equal(x.t, y.t) for x, y in zip(a, b))
        met |= {s.icao for s in a}
    assert met == {"a00001", "a00003", "a00004"}        # the one in the margin is heard from the box's edge


def test_a_box_with_no_flights_is_no_data_and_leaves_no_file(big, tmp_path):
    out = tmp_path / "empty.npz"
    with pytest.raises(NoFlightData):
        cut_index(big, (26.0, 27.0, -82.0, -81.0), out)
    assert not out.exists() and not list(tmp_path.glob("*.part.npz"))


def test_the_frame_helpers_agree_on_where_the_box_is():
    # cut_index turns positions back into latitude and longitude with the engine's own function
    lat, lon, _ = enu_to_lla(*lla_to_enu(47.0, -90.0, 500.0, LAT0, LON0), LAT0, LON0)
    assert (lat, lon) == (pytest.approx(47.0), pytest.approx(-90.0))


def test_only_one_search_holds_the_memory_at_a_time(tmp_path):
    waited = []
    with one_at_a_time(tmp_path, lambda: waited.append("first")):
        assert not waited
        other = open(tmp_path / "busy.lock", "w")       # what a second flyby would do
        with pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        other.close()
    with one_at_a_time(tmp_path, lambda: waited.append("second")):     # given up when the first ended
        assert not waited
