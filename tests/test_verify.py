"""A made-up hour of noise with one real aircraft tone in it, and a made-up day of flights."""
import numpy as np
import pytest
from dopplertrack.audio import Spectrogram

from flyby.verify import MIN_SWEEP, Tracks, check, tone

C = 340.0
START = 1_788_898_649.0
OBS = np.array([0.0, 0.0, 0.0])
PITCH = 110.0


def straight(when_s, offset_m, speed=100.0, height=600.0, heading=(1.0, 0.0), half_s=400):
    """A level pass: closest to the listener at START + when_s, offset_m to the side."""
    t = START + when_s + np.arange(-half_s, half_s + 1.0)
    along = np.outer(t - (START + when_s), np.array(heading) * speed)
    side = np.array([-heading[1], heading[0]]) * offset_m
    enu = np.c_[along + side, np.full(len(t), height)]
    return t, enu, np.full(len(t), speed)


def day_index(path, flights):
    """Written the way the engine writes a day index: 1 Hz tracks, plus one sample per 10 s bucket."""
    off = np.r_[0, np.cumsum([len(t) for _, (t, _, _) in flights])].astype(np.int64)
    sb, ss, sj = [], [], []
    for i, (_, (t, _, _)) in enumerate(flights):
        b = np.arange(np.ceil((t[0] - 5) / 10), np.floor((t[-1] - 5) / 10) + 1, dtype=np.int64)
        j = np.rint((b + 0.5) * 10 - t[0]).astype(np.int64)
        sb.append(b); ss.append(np.full(len(b), i, np.int32)); sj.append(j.astype(np.int32))
    sb, ss, sj = (np.concatenate(x) for x in (sb, ss, sj))
    order = np.argsort(sb, kind="stable")
    np.savez(path, lat0=40.0, lon0=-100.0, bucket_s=10.0, off=off, t=np.concatenate([t for _, (t, _, _) in flights]),
             enu=np.concatenate([e for _, (_, e, _) in flights]), spd=np.concatenate([v for _, (_, _, v) in flights]),
             icao=np.array([name for name, _ in flights]), trace_idx=np.arange(len(flights), dtype=np.int32),
             sb=sb[order], ss=ss[order], sj=sj[order])
    return Tracks(path)


def recording(tones=(), hum_hz=None, seconds=1800, seed=3):
    """Noise, plus a bright line along each (track, pitch) as the listener at OBS would hear it."""
    rng = np.random.default_rng(seed)
    t = 1.0 + 0.25 * np.arange(seconds * 4)
    f = 40.0 + 0.5 * np.arange(820)
    S = rng.normal(0.0, 3.0, (len(f), len(t))).astype(np.float32)
    for (track_t, enu, _), pitch in tones:
        heard, ratio = tone(track_t, enu, OBS, C)
        at = np.interp(t, heard - START, pitch * ratio, left=np.nan, right=np.nan)
        ok = np.isfinite(at)
        rows = np.rint((at[ok] - f[0]) / 0.5).astype(int)
        S[rows, np.flatnonzero(ok)] += 14.0
    if hum_hz:
        S[int((hum_hz - f[0]) / 0.5)] += 14.0
    return Spectrogram(t, f, S)


def test_tone_is_high_coming_and_low_going():
    t, enu, _ = straight(0.0, 1000.0)
    heard, ratio = tone(t, enu, OBS, C)
    assert ratio[0] == pytest.approx(C / (C - 100.0), rel=0.01) and ratio[-1] == pytest.approx(C / (C + 100.0), rel=0.01)
    assert np.all(np.diff(ratio) < 0) and np.all(np.diff(heard) > 0)
    mid = len(t) // 2
    assert heard[mid] - t[mid] == pytest.approx(np.hypot(1000.0, 600.0) / C)     # sound takes that long to arrive


def test_an_aircraft_the_search_never_used_is_seen_and_a_silent_one_is_not(tmp_path):
    real, silent = straight(900.0, 2000.0), straight(400.0, 3000.0, speed=70.0, heading=(0.0, 1.0))
    tracks = day_index(tmp_path / "day.npz", [("a11111", real), ("a22222", silent), ("a33333", straight(900.0, 60_000.0))])
    result = check(recording([(real, PITCH)]), tracks, OBS, START, used=set(), c_at=lambda h: C)
    by = {q.icao: q for q in result.passes}
    assert set(by) == {"a11111", "a22222"}          # the third flight never came within range
    assert by["a11111"].seen and by["a11111"].pitch_hz == pytest.approx(PITCH, abs=1.0)
    assert by["a11111"].heard_s == pytest.approx(900.0 + np.hypot(2000.0, 600.0) / C, abs=1.0)
    assert not by["a22222"].seen and by["a22222"].p > 0.05
    assert [q.icao for q in result.others] == ["a22222", "a11111"]       # in the order they were due


def test_the_right_tone_at_the_wrong_time_is_not_seen(tmp_path):
    real = straight(900.0, 2000.0)
    tracks = day_index(tmp_path / "day.npz", [("a11111", real)])
    late = check(recording([(real, PITCH)]), tracks, OBS, START - 300.0, used=set(), c_at=lambda h: C)   # clock 5 minutes out
    assert not late.passes[0].seen


def test_a_flight_the_search_used_is_no_new_evidence_and_neither_is_its_twin(tmp_path):
    real = straight(900.0, 2000.0)
    relay = (real[0], real[1] + np.array([40.0, -30.0, 0.0]), real[2])      # the same aircraft, as a ground radar reports it
    tracks = day_index(tmp_path / "day.npz", [("a11111", real), ("~2af405", relay)])
    result = check(recording([(real, PITCH)]), tracks, OBS, START, used={"a11111"}, c_at=lambda h: C)
    assert len(result.passes) == 2 and all(q.used for q in result.passes) and result.others == []
    # with neither used, the pair counts once
    assert len(check(recording([(real, PITCH)]), tracks, OBS, START, used=set(), c_at=lambda h: C).others) == 1


def test_a_steady_hum_is_not_mistaken_for_a_far_slow_aircraft(tmp_path):
    far, flat = straight(900.0, 11_000.0, speed=30.0), straight(500.0, 11_500.0, speed=20.0)
    tracks = day_index(tmp_path / "day.npz", [("a44444", far), ("a55555", flat)])
    result = check(recording(hum_hz=120.0), tracks, OBS, START, used=set(), c_at=lambda h: C)
    by = {q.icao: q for q in result.passes}
    # a tone that barely falls is tested, and the hum that is there all hour does not pass for it
    assert by["a44444"].p is not None and by["a44444"].p > 0.05 and not by["a44444"].seen
    # one that falls less still cannot be told from a hum at all, so it is not tested
    assert by["a55555"].sweep < MIN_SWEEP and by["a55555"].p is None
    assert [q.icao for q in result.others] == ["a44444"]


def test_a_recording_too_short_for_wrong_time_copies_is_not_tested(tmp_path):
    real = straight(150.0, 2000.0)
    tracks = day_index(tmp_path / "day.npz", [("a11111", real)])
    result = check(recording([(real, PITCH)], seconds=300), tracks, OBS, START, used=set(), c_at=lambda h: C)
    assert result.passes[0].p is None and result.others == []


def test_colder_air_at_height_is_used_for_that_aircraft(tmp_path):
    real = straight(900.0, 2000.0, height=1500.0)
    tracks = day_index(tmp_path / "day.npz", [("a11111", real)])
    heights = []
    check(recording([(real, PITCH)]), tracks, OBS, START, used=set(), c_at=lambda h: heights.append(h) or C)
    assert heights == [pytest.approx(1500.0, abs=1.0)]


def test_height_is_measured_along_the_up_at_the_spot_not_the_up_of_a_far_frame():
    from dopplertrack.geo import lla_to_enu
    from flyby.verify import height_above
    lat0, lon0 = 39.0, -97.0                    # the middle of the USA, where a whole-USA index has its origin
    spot = lla_to_enu(40.8765, -73.5250, 30.0, lat0, lon0)             # Oyster Bay, 2,000 km east of it
    aircraft = lla_to_enu(40.8765, -73.4650, 1230.0, lat0, lon0)       # 5 km east of the spot, 1,200 m above it
    assert height_above(aircraft, spot, lat0, lon0) == pytest.approx(1200.0, abs=0.01)
    assert abs((aircraft[2] - spot[2]) - 1200.0) > 1000.0              # what the frame's own up says
