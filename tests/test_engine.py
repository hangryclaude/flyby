import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from flyby.engine import CLOCK_TOL_S, LOCATED_P, _index_box, _share, judge, plan
from flyby.measure import Flyover
from flyby.model import CONUS

START = 1_788_898_649.0     # 2026-09-08T20:17:29Z, the reference clip's start
T_CPA1 = 2155.8


def table(scores, starts=None):
    """A search table with one row per flight pair. starts: the recording start each pair implies."""
    n = len(scores)
    starts = np.full(n, START + 40_000.0) if starts is None else np.asarray(starts, float)
    return pd.DataFrame({"icao1": [f"a{i:05x}" for i in range(n)], "icao2": [f"b{i:05x}" for i in range(n)],
                         "score": scores, "lat": np.linspace(30, 45, n), "lon": np.linspace(-120, -70, n),
                         "t_heard1": starts + T_CPA1})


def crowd(n=400, seed=1):
    return np.sort(np.random.default_rng(seed).exponential(1.0, n))[::-1] + 5.0


def test_clear_winner_gets_a_small_p_and_a_margin():
    scores = np.r_[crowd()[0] + 30.0, crowd()]
    j = judge(table(scores), T_CPA1)
    assert j.rank == 1 and j.p < 0.001 and j.clock_agrees is None
    assert j.margin_pct == pytest.approx(100 * 30.0 / crowd()[0], rel=1e-6)
    assert j.row["icao1"] == "a00000"
    assert len(j.runners_up) == 5 and j.runners_up[0][2] == pytest.approx(crowd()[0])


def test_winner_that_barely_leads_is_not_trusted():
    rest = crowd()[1:]
    assert judge(table(np.r_[rest[0] + 0.05, rest]), T_CPA1).p > 0.9


def test_a_lucky_lead_in_pure_noise_stays_below_the_located_bar():
    # this crowd's best pair leads by chance: the engine's own p is 0.09, which it calls "located"
    j = judge(table(crowd()), T_CPA1)
    assert 0.05 < j.p < 0.15 and j.p > LOCATED_P


def test_empty_table_has_no_answer():
    assert judge(table([]), T_CPA1) is None


def test_too_few_pairs_to_judge_means_no_evidence():
    assert judge(table([9.0, 3.0, 2.0]), T_CPA1).p == 1.0


def test_agreeing_start_time_multiplies_the_evidence():
    scores = np.r_[crowd()[0] + 2.0, crowd()]
    plain = judge(table(scores), T_CPA1)
    starts = np.full(len(scores), START + 40_000.0)
    starts[[0, 7]] = START + 30.0, START - CLOCK_TOL_S + 1      # the winner and one other pair agree
    j = judge(table(scores, starts), T_CPA1, START)
    assert j.rank == 1 and j.clock_agrees is True
    assert j.p == pytest.approx(2 * plain.p * 2 / len(scores))


def test_lower_pair_that_agrees_with_the_start_time_is_chosen():
    scores = crowd()
    starts = np.full(len(scores), START + 40_000.0)
    starts[2] = START - 60.0
    j = judge(table(scores, starts), T_CPA1, START)
    assert j.rank == 3 and j.clock_agrees is True and j.margin_pct is None
    assert j.p == pytest.approx(2 * (1 - (1 - 3 / len(scores)) ** 1))
    assert j.row["icao1"] == "a00002"


def test_start_time_that_nothing_agrees_with_costs_double_and_is_flagged():
    scores = np.r_[crowd()[0] + 3.0, crowd()]
    plain = judge(table(scores), T_CPA1)
    j = judge(table(scores), T_CPA1, START)
    assert j.rank == 1 and j.clock_agrees is False
    assert j.p == pytest.approx(min(1.0, 2 * plain.p))


def test_agreeing_pair_far_down_the_list_does_not_beat_a_strong_winner():
    scores = np.r_[crowd()[0] + 30.0, crowd()]
    starts = np.full(len(scores), START + 40_000.0)
    starts[300] = START
    j = judge(table(scores, starts), T_CPA1, START)
    assert j.rank == 1 and j.clock_agrees is False and j.p < 0.001


def test_a_looser_start_time_is_weaker_evidence():
    scores = np.r_[crowd()[0] + 2.0, crowd()]
    starts = np.full(len(scores), START + 40_000.0)
    starts[0], starts[5:25] = START + 30.0, START + 1500.0      # twenty wrong pairs fall within the hour, none within minutes
    tight = judge(table(scores, starts), T_CPA1, START, 120.0)
    loose = judge(table(scores, starts), T_CPA1, START, 3600.0)
    assert tight.clock_agrees and loose.clock_agrees
    assert loose.p == pytest.approx(21 * tight.p)


def test_a_named_town_is_a_second_test_like_the_start_time():
    scores = np.r_[crowd()[0] + 2.0, crowd()]
    plain = judge(table(scores), T_CPA1)
    n = len(scores)
    town = (29.9, 30.2, -121.0, -119.0)         # holds the winner (30, -120) and the pairs right next to it
    inside = int(((np.linspace(30, 45, n) <= 30.2) & (np.linspace(-120, -70, n) <= -119.0)).sum())
    j = judge(table(scores), T_CPA1, focus=town)
    assert j.rank == 1 and j.place_agrees is True and j.clock_agrees is None
    assert j.p == pytest.approx(2 * plain.p * inside / n)
    elsewhere = judge(table(scores), T_CPA1, focus=(10.0, 11.0, 10.0, 11.0))
    assert elsewhere.rank == 1 and elsewhere.place_agrees is False
    assert elsewhere.p == pytest.approx(min(1.0, 2 * plain.p))


def test_start_time_and_town_together_pay_for_four_looks_and_take_the_best():
    scores = np.r_[crowd()[0] + 2.0, crowd()]
    plain = judge(table(scores), T_CPA1)
    n = len(scores)
    starts = np.full(n, START + 40_000.0)
    starts[[0, 7]] = START + 30.0, START + 60.0
    town = (29.9, 30.2, -121.0, -119.0)
    both = judge(table(scores, starts), T_CPA1, START, CLOCK_TOL_S, town)
    assert both.clock_agrees and both.place_agrees
    assert both.p == pytest.approx(4 * plain.p * 1 / n)         # only the winner is right on both counts
    # the town is wrong (a map search can be), the start time still counts
    off = judge(table(scores, starts), T_CPA1, START, CLOCK_TOL_S, (10.0, 11.0, 10.0, 11.0))
    assert off.rank == 1 and off.clock_agrees and off.place_agrees is False
    assert off.p == pytest.approx(4 * plain.p * 2 / n)          # all four looks were taken, so all four are paid for


def test_a_thorough_run_also_pairs_up_the_fourth_pass():
    four = [flyover(k, 100.0 * k, 10.0 - k) for k in (1, 2, 3, 4)]
    usual = plan(four, [date(2026, 9, 8)])
    assert [(a.rank, b.rank) for _, a, b, _ in usual] == [(1, 2), (1, 3), (2, 3)]
    thorough = plan(four, [date(2026, 9, 8)], limit=12)
    assert [(a.rank, b.rank) for _, a, b, _ in thorough] == [(1, 2), (1, 3), (2, 3), (1, 4), (2, 4), (3, 4)]


def flyover(rank, t, strength):
    return Flyover(rank, strength, {"t_cpa": t, "f0": 100.0, "v": 80.0, "d": 3000.0, "score": 8.0}, Path(f"e{rank}.json"))


def test_plan_tries_the_likeliest_day_and_pair_first():
    f = [flyover(1, 2155.8, 15.7), flyover(2, 3073.8, 10.0), flyover(3, 781.5, 8.2), flyover(6, 1409.3, 5.7)]
    days = [date(2026, 9, 8), date(2026, 9, 9)]
    got = [(d, a.rank, b.rank, [x.rank for x in extras]) for d, a, b, extras in plan(f, days)]
    assert got == [
        (days[0], 1, 2, [3, 6]),
        (days[1], 1, 2, [3, 6]),
        (days[0], 3, 1, [2, 6]),      # the earlier pass goes first
        (days[1], 3, 1, [2, 6]),
        (days[0], 3, 2, [1, 6]),
        (days[1], 3, 2, [1, 6]),
    ]
    assert len(plan(f, days, limit=2)) == 2


def test_plan_with_two_flyovers_has_one_pair_and_no_extras():
    f = [flyover(1, 500.0, 9.0), flyover(2, 100.0, 7.0)]
    [(day, a, b, extras)] = plan(f, [date(2026, 9, 8)])
    assert (a.rank, b.rank, extras) == (2, 1, [])


def test_a_named_area_gets_its_own_small_index_and_most_of_the_usa_gets_the_national_one():
    # a state: its own index, a few percent of the memory and the disk
    assert _index_box((40.47, 45.02, -79.76, -71.86)) == (40.4, 45.1, -79.8, -71.8)
    assert _index_box((50.5, 52.0, -1.0, 1.0)) == (50.5, 52.0, -1.0, 1.0)
    assert _index_box(CONUS) == CONUS
    assert _index_box((25.0, 49.0, -124.0, -90.0)) == CONUS         # the western two thirds


def test_lead_p_is_honest_when_nothing_is_right():
    # 1500 searches of pure noise, shaped like the engine's 200-pair extras table. The engine's own
    # exp(-gap/m) calls 16% of these p < 0.1 and 2.6% p < 0.01; an honest p calls about 10% and 1%.
    rng = np.random.default_rng(7)
    ps = np.array([judge(table(5.0 + rng.exponential(1.0, 200)), T_CPA1).p for _ in range(1500)])
    assert 0.075 < (ps <= 0.10).mean() < 0.125
    assert (ps <= 0.01).mean() < 0.02


def test_index_box_outside_the_lower_48_rounds_outward_to_its_file_name():
    from flyby.dayindex import _covers, _tag, index_box
    box = _index_box((50.88, 51.89, -0.71, 0.53))
    assert box == (50.8, 51.9, -0.8, 0.6)
    assert index_box(Path(f"2026-09-08_{_tag(box)}.npz")) == box
    assert _covers(box, (50.88, 51.89, -0.71, 0.53))


def test_a_crowd_of_ties_or_a_nan_score_is_never_evidence():
    # no spread among the wrong pairs measures nothing, however far the winner is ahead
    assert judge(table(np.r_[1.0, np.zeros(40)]), T_CPA1).p == 1.0
    scores = np.r_[crowd()[0] + 30.0, crowd()]
    clean = judge(table(scores), T_CPA1)
    dirty = table(np.r_[scores, np.nan])
    assert judge(dirty, T_CPA1).p == clean.p and math.isfinite(clean.p)


def test_lead_p_stays_close_when_pairs_share_flights():
    # the engine sets aside pairs that share a flight with the winner, which the formula leaves out;
    # 15 flights each way is a small table, where that matters most
    rng = np.random.default_rng(3)
    grid = pd.DataFrame({"icao1": np.repeat([f"a{i}" for i in range(15)], 15), "icao2": np.tile([f"b{i}" for i in range(15)], 15),
                         "lat": 40.0, "lon": -100.0, "t_heard1": START + 40_000.0 + T_CPA1})
    ps = np.array([judge(grid.assign(score=5.0 + rng.exponential(1.0, 225)), T_CPA1).p for _ in range(1200)])
    assert (ps <= 0.01).mean() < 0.025 and 0.07 < (ps <= 0.10).mean() < 0.15


class Quiet:
    def __getattr__(self, name):
        return lambda *a, **k: None


INDEX = Path("2026-09-08_24.0_50.0_-125.0_-66.0.npz")


def measured(rank, t, folder):
    return Flyover(rank=rank, strength=20.0 - rank, path=folder / f"e{rank}.json",
                   fit={"t_cpa": t, "f0": 100.0, "v": 60.0, "d": 1500.0, "score": 9.0})


def engine_table(scores):
    spots = pd.concat([table(scores).assign(seg1=range(len(scores)), seg2=range(len(scores)), shift=0.0)] * 3)
    return spots.assign(score=spots.score - np.repeat([0.0, 1.0, 2.0], len(scores)))    # three spots a pair, best first


def test_search_keeps_one_small_table_and_is_not_repeated_on_fewer_cores(tmp_path, monkeypatch):
    from flyby import engine
    ran = []

    def fake_engine(args, log_path, on_text=None, stall_s=None, air_c=None):
        ran.append(args)
        engine_table(np.r_[40.0, crowd()]).to_csv(args[args.index("--out") + 1], index=False)

    monkeypatch.setattr(engine, "run_engine", fake_engine)
    a, b = measured(1, 600.0, tmp_path), measured(2, 1500.0, tmp_path)
    found = engine.search(tmp_path, tmp_path / "rec.npz", date(2026, 9, 8), INDEX, CONUS, a, b, [], 12, Quiet())
    assert found == (tmp_path / "search" / "2026-09-08_e1-e2.pairs.csv", 401)
    kept = pd.read_csv(found[0])
    assert len(kept) == 401 and kept.score.max() == 40.0
    assert sorted(p.name for p in (tmp_path / "search").iterdir()) == ["2026-09-08_e1-e2.done.json", "2026-09-08_e1-e2.pairs.csv"]
    assert engine.search(tmp_path, tmp_path / "rec.npz", date(2026, 9, 8), INDEX, CONUS, a, b, [], 4, Quiet()) == found
    assert len(ran) == 1
    # a different measurement of the same flyover is a different search
    b.fit["v"] = 61.0
    engine.search(tmp_path, tmp_path / "rec.npz", date(2026, 9, 8), INDEX, CONUS, a, b, [], 4, Quiet())
    assert len(ran) == 2


def test_search_that_fails_leaves_no_half_gigabyte_behind(tmp_path, monkeypatch):
    from flyby import engine
    from flyby.model import FlybyError

    def crashing_engine(args, log_path, on_text=None, stall_s=None, air_c=None):
        engine_table(crowd()).to_csv(args[args.index("--out") + 1], index=False)
        raise FlybyError("the engine's search step stopped with an error")

    monkeypatch.setattr(engine, "run_engine", crashing_engine)
    a, b = measured(1, 600.0, tmp_path), measured(2, 1500.0, tmp_path)
    with pytest.raises(FlybyError):
        engine.search(tmp_path, tmp_path / "rec.npz", date(2026, 9, 8), INDEX, CONUS, a, b, [], 12, Quiet())
    assert list((tmp_path / "search").iterdir()) == []


def test_search_with_no_flight_pair_is_an_answer_not_an_error(tmp_path, monkeypatch):
    from flyby import engine
    from flyby.model import FlybyError
    ran = []

    def empty_engine(args, log_path, on_text=None, stall_s=None, air_c=None):
        ran.append(args)
        engine_table(crowd()).head(0).to_csv(args[args.index("--out") + 1], index=False)
        raise FlybyError("KeyError: 'seg1'")      # what the engine does with extras and nothing to rescore

    monkeypatch.setattr(engine, "run_engine", empty_engine)
    a, b, c = (measured(k, 600.0 * k, tmp_path) for k in (1, 2, 3))
    for _ in range(2):
        assert engine.search(tmp_path, tmp_path / "rec.npz", date(2026, 9, 8), INDEX, CONUS, a, b, [c], 12, Quiet()) is None
    assert len(ran) == 1


def locate_with(tables, tmp_path, monkeypatch, days=(date(2026, 9, 8), date(2026, 9, 9))):
    """Run locate() with the engine stubbed out: each search returns the next table."""
    from datetime import datetime, timezone
    from flyby import engine
    from flyby.model import Clues, Media
    queue, ran = list(tables), []

    def fake_search(run, spec, day, index, bbox, a, b, extras, workers, ui, air_c=None):
        ran.append((day, a.rank, b.rank))
        scores = queue.pop(0)
        if scores is None:
            return None
        path = tmp_path / f"t{len(ran)}.csv"
        table(scores).to_csv(path, index=False)
        return path, len(scores)

    monkeypatch.setattr(engine, "cached_index", lambda day, box, root: INDEX)
    monkeypatch.setattr(engine, "search", fake_search)
    monkeypatch.setattr(engine, "fetch_air", lambda lat, lon, when: None)       # no weather lookups in tests
    monkeypatch.setattr(engine, "explain", lambda *a: engine.Pinned(
        40.0, -100.0, datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc), [], [], [], [], None))
    media = Media("https://example.com/v", Path("a.wav"), "Clip", "clip", 3600.0, "upload")
    clues = Clues(list(days), CONUS, "continental USA", "day", "area")
    flyovers = [measured(k, 600.0 * k, tmp_path) for k in (1, 2, 3)]
    return engine.locate(media, clues, flyovers, {}, tmp_path, tmp_path, Quiet()), ran


def test_the_shares_of_a_whole_plan_add_up_to_one_and_the_first_is_the_largest():
    for planned in (1, 2, 6, 12):
        shares = [_share(k, planned) for k in range(1, planned + 1)]
        assert sum(shares) == pytest.approx(1.0) and shares == sorted(shares, reverse=True)
    assert _share(1, 1) == 1.0                  # one search allowed: nothing to share
    assert _share(1, 6) == pytest.approx(7 / 12) and _share(6, 6) == pytest.approx(1 / 36)


def test_locate_stops_at_the_first_strong_match_and_charges_for_every_search(tmp_path, monkeypatch):
    noise, strong = crowd(seed=5), np.r_[crowd()[0] + 30.0, crowd()]
    result, ran = locate_with([noise, None, strong, noise, noise, noise], tmp_path, monkeypatch)
    assert len(ran) == 3 and result.verdict == "located" and result.lat == 40.0
    # it was the third of six planned searches, and a search that found no pair still used its turn
    assert result.p == pytest.approx(judge(table(strong), 600.0).p / _share(3, 6))
    assert len(result.searched) == 3 and "no two flights" in result.searched[1]


def test_locate_names_no_place_when_nothing_stands_out(tmp_path, monkeypatch):
    result, ran = locate_with([crowd(seed=s) for s in (5, 6, 7, 8, 9, 10)], tmp_path, monkeypatch)
    assert len(ran) == 6 and result.verdict == "no match"
    assert result.lat is None and result.lon is None and not result.flights and not result.figures
    assert result.p == pytest.approx(min(1.0, min(judge(table(crowd(seed=s)), 600.0).p / _share(k, 6)
                                                  for k, s in enumerate((5, 6, 7, 8, 9, 10), 1))))


def test_a_match_that_six_searches_could_produce_by_luck_is_only_a_lead(tmp_path, monkeypatch):
    # alone this table would be "located" (p under 0.01); as the second of six searches it is not
    fair = np.r_[crowd()[0] + 9.0, crowd()]
    alone = judge(table(fair), 600.0).p
    assert 0.002 < alone < 0.01
    result, ran = locate_with([crowd(seed=5), fair, crowd(seed=6), crowd(seed=7), crowd(seed=8), crowd(seed=9)], tmp_path, monkeypatch)
    assert len(ran) == 6 and result.verdict == "not sure" and result.p == pytest.approx(alone / _share(2, 6))
    # the later a search comes in the plan, the more it has to show: as the last of six this is nothing
    result, _ = locate_with([crowd(seed=5), crowd(seed=6), crowd(seed=7), crowd(seed=8), crowd(seed=9), fair], tmp_path, monkeypatch)
    assert result.verdict == ("not sure" if alone * 36 <= 0.1 else "no match")


def test_a_run_of_wrong_searches_calls_located_less_than_once_in_a_hundred():
    # each wrong search has a p spread evenly from 0 to 1; a run is located if any search clears its share
    rng = np.random.default_rng(11)
    for planned in (6, 12):
        p = rng.uniform(size=(400_000, planned))
        bars = LOCATED_P * np.array([_share(k, planned) for k in range(1, planned + 1)])
        assert (p <= bars).any(axis=1).mean() < 0.0105
