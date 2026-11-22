from datetime import date
from pathlib import Path

import pytest

from flyby.dayindex import cached_index, flights_file, index_box
from flyby.measure import sweep, why_not

C = 340.3


def det(t, f0, v, d, burst=0.0):
    return {"t_cpa": t, "f0": f0, "v": v, "d": d, "burst": burst}


def fit(t, f0, v, d, centre=None):
    centre = t if centre is None else centre
    return {"t_cpa": t, "t_cpa_emit": t - d / C, "f0": f0, "v": v, "d": d, "t_start": centre - 55.0, "t_end": centre + 55.0}


# detect's fit and flyby's tight refit for the four real aircraft in the reference clip
REAL = [
    (det(2155.6, 112.9, 138.77, 6264), fit(2155.8, 112.8, 139, 6313, 2155.6)),
    (det(3068.4, 140.1, 78.66, 1587), fit(3073.8, 132.5, 144, 6172, 3068.4)),
    (det(790.1, 92.3, 50.02, 3205, 0.014), fit(781.5, 94.0, 69, 6050, 790.1)),
    (det(1408.8, 83.3, 49.78, 526), fit(1409.3, 82.9, 42, 333, 1408.8)),
]


@pytest.mark.parametrize("d, f", REAL)
def test_real_flyovers_from_the_reference_clip_are_kept(d, f):
    assert why_not(d, f, C) is None


@pytest.mark.parametrize("d, f, reason", [
    (det(210.4, 72.5, 35.0, 15000, 0.078), None, "no clear bend (a steady hum)"),        # fit sat on both clamps
    (det(502.5, 178.8, 35.44, 557, 0.0875), None, "no clear bend (a steady hum)"),       # just off the speed clamp
    (det(692.7, 105.8, 148.22, 349, 0.2), None, "broadband noise (a train or truck)"),
    (det(100.0, 90.0, 80.0, 2000), None, "too close to the start or end"),
    (det(1000.0, 90.0, 80.0, 2000), fit(1049.0, 90.0, 80.0, 2000, 1000.0), "unstable fit"),
    (det(1000.0, 90.0, 80.0, 2000), fit(1001.0, 101.0, 80.0, 2000, 1000.0), "unstable fit"),
    (det(1000.0, 90.0, 80.0, 2000), fit(1001.0, 90.0, 240.0, 2000, 1000.0), "not aircraft-like"),
    (det(1000.0, 90.0, 80.0, 2000), fit(1001.0, 90.0, 80.0, 14000, 1000.0), "not aircraft-like"),
    (det(1000.0, 90.0, 60.0, 9000), fit(1001.0, 90.0, 26.0, 11000, 1000.0), "no clear bend (a steady hum)"),
])
def test_other_sounds_are_set_aside_with_a_reason(d, f, reason):
    assert why_not(d, f, C) == reason


def test_sweep_is_large_for_a_fast_close_pass_and_tiny_for_a_distant_slow_one():
    assert sweep(fit(2155.8, 112.8, 139, 6313), C) == pytest.approx(0.60, abs=0.02)
    assert sweep(fit(1000.0, 72.5, 35.0, 15000), C) < 0.03


def test_cached_index_takes_a_wider_box_and_ignores_strangers(tmp_path: Path):
    day = date(2026, 9, 8)
    usa = tmp_path / "2026-09-08_24.0_50.0_-125.0_-66.0.npz"
    for name in (usa.name, "2026-09-08_conus.npz", "2026-09-08_30.0_31.0_-90.0_-89.0.part.npz", "2026-09-09_24.0_50.0_-125.0_-66.0.npz"):
        (tmp_path / name).write_bytes(b"")
    assert index_box(usa) == (24.0, 50.0, -125.0, -66.0)
    assert cached_index(day, (40.0, 41.5, -74.5, -72.5), tmp_path) == usa
    assert cached_index(day, (24.0, 50.0, -125.0, -66.0), tmp_path) == usa
    assert cached_index(day, (50.5, 52.0, -1.0, 1.0), tmp_path) is None
    assert cached_index(date(2026, 9, 7), (40.0, 41.5, -74.5, -72.5), tmp_path) is None
    assert flights_file(usa).name == "2026-09-08_24.0_50.0_-125.0_-66.0.flights.json"


def test_a_whole_usa_index_is_refused_before_any_download_when_memory_is_short(tmp_path: Path, monkeypatch):
    from flyby import dayindex
    from flyby.model import CONUS, FlybyError, NoFlightData

    def no_network(day):
        raise NoFlightData("reached the archive")

    monkeypatch.setattr(dayindex, "release_assets", no_network)
    monkeypatch.setattr(dayindex, "free_memory_gb", lambda: 5.0)
    with pytest.raises(FlybyError, match="only 5 GB is free") as stopped:
        dayindex.build_day_index(date(2026, 9, 8), CONUS, tmp_path)
    assert not isinstance(stopped.value, NoFlightData)
    # a small box builds a small index, and a Mac with room to spare is not held up
    with pytest.raises(NoFlightData):
        dayindex.build_day_index(date(2026, 9, 8), (50.8, 51.9, -0.8, 0.6), tmp_path)
    monkeypatch.setattr(dayindex, "free_memory_gb", lambda: 30.0)
    with pytest.raises(NoFlightData):
        dayindex.build_day_index(date(2026, 9, 8), CONUS, tmp_path)
