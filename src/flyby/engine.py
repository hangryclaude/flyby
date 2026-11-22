"""Search the flight data for the spot that would have heard these flyovers, and judge the winner.

The engine always has a best-scoring spot, even on the wrong day or in the wrong country. So
the judging here is the part that keeps flyby honest: a location is only reported together
with the chance that a wrong answer would have looked as good.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from .dayindex import build_day_index, cached_index, flights_file, index_box
from .measure import Flyover
from .model import CONUS, Clues, Event, Flight, FlybyError, Media, NoFlightData, Result
from .runner import STALL_S, run_engine

# The engine calls p < 0.1 "located", and its author saw 2 of 20 searches with no right answer
# pass that bar. flyby asks for ten times better before it names a place.
LOCATED_P = 0.01
HINT_P = 0.10
MAX_SEARCHES = 6
MAX_EXTRAS = 2
CLOCK_TOL_S = 180.0         # how far a stated start time may be off and still count as agreeing
MATCHED_SHARE = 0.5         # a further flyover is matched when a real flight explains half its brightness


@dataclass
class Judgement:
    row: dict               # the chosen flight pair at its best point: a row of the engine's table
    p: float                # chance a wrong answer would look this good, in this one search
    rank: int               # 1 = the engine's own winner
    n_pairs: int
    margin_pct: float | None    # the winner's lead over the best pair that shares no flight with it
    clock_agrees: bool | None   # None when nothing says when the recording began
    runners_up: list[tuple[float, float, float]]


def plan(flyovers: list[Flyover], days: list[date], limit: int = MAX_SEARCHES):
    """The searches to run, most promising first: (day, earlier pass, later pass, extras)."""
    top = flyovers[:3]
    pairs = [(top[i], top[j]) for i, j in ((0, 1), (0, 2), (1, 2)) if j < len(top)]
    order = sorted((di + pi, pi, di) for di in range(len(days)) for pi in range(len(pairs)))
    out = []
    for _, pi, di in order[:limit]:
        a, b = sorted(pairs[pi], key=lambda f: f.t)     # the engine's proven runs give the earlier pass first
        extras = [f for f in flyovers if f is not a and f is not b][:MAX_EXTRAS]
        out.append((days[di], a, b, extras))
    return out


def _lead_p(gap: float, tail_width: float, k: int) -> float:
    """Chance that a wrong winner leads the best other wrong pair by `gap`.

    The engine reports exp(-gap / m), which takes the tail width m as known. It is measured
    from only k scores, and from the ones just below the winner, which runs a little narrow.
    For an exponential tail the spacings between the top scores are independent (Renyi), so
    the chance can be written out: with t = gap / m it is the product over i = 2..k+1 of
    1 / (1 + t (i - 1) / (i k)).
    With further flyovers k is 15 and the difference is not small: where the engine says
    p = 0.1 this says 0.16, the rate its author saw wrong answers pass at, and where it says
    0.001 this says 0.0075. On a plain two-flyover table k is 100 and 0.1 becomes 0.11.
    One thing is left out. The engine sets aside pairs that share a flight with the winner,
    which widens the gap a little: on a table built from only 15 flights each way, 1.2% of
    pure-noise searches come in under 0.01. Whole-country tables lose 1 to 3 pairs that way."""
    if gap <= 0 or tail_width <= 0:     # a crowd with no spread (all ties) measures nothing
        return 1.0
    i = np.arange(2, k + 2)
    return float(np.prod(1.0 / (1.0 + (gap / tail_width) * (i - 1) / (i * k))))


def judge(table, t_cpa1: float, start_unix: float | None = None) -> Judgement | None:
    """Pick the answer from one search's table and say how far it can be trusted.

    Without a start time this is the engine's own test: how far the winner leads the crowd of
    wrong flight pairs scored on the same audio.

    A known start time adds a second, independent test. Every flight pair implies a start time
    for the recording, and a wrong pair's is as good as random, so only a small share q of the
    pairs agree with the stated one. If the winner agrees, both tests passed: p = q x p_score.
    If a lower pair is the best one that agrees, p is the chance that any of the agreeing pairs
    would rank that high by luck. Either route can win, so each pays double."""
    from dopplertrack.verdict import verdict
    table = table[np.isfinite(table.score)]     # one NaN score would make every figure below NaN
    v = verdict(table)
    if not v.n_pairs:
        return None
    best = table.sort_values("score", ascending=False).drop_duplicates(["icao1", "icao2"]).reset_index(drop=True)
    judged = v.verdict != "insufficient"
    p_score = _lead_p(v.gap, v.tail_width, v.k) if judged else 1.0
    pick, p, agrees0 = 0, p_score, None
    if start_unix is not None:
        agrees = np.abs(best.t_heard1.to_numpy() - t_cpa1 - start_unix) <= CLOCK_TOL_S
        agrees0 = bool(agrees[0])
        m, n = int(agrees.sum()), len(best)
        if m:
            r = int(np.argmax(agrees))
            # r > 0: the chance that the best of m random pairs out of n lands in the top r + 1
            p_clock = p_score * m / n if r == 0 else 1 - float(np.prod((n - r - 1 - np.arange(m)) / (n - np.arange(m))))
            if p_clock < p_score:
                pick, p = r, p_clock
        p = min(1.0, 2 * p)
    row = best.iloc[pick]
    margin = 100 * v.gap / (row.score - v.gap) if judged and pick == 0 and row.score > v.gap > 0 else None
    return Judgement(row=row.to_dict(), p=float(p), rank=pick + 1, n_pairs=len(best), margin_pct=margin,
                     clock_agrees=None if start_unix is None else bool(pick != 0 or agrees0),
                     runners_up=[(float(o.lat), float(o.lon), float(o.score)) for o in best.drop(index=pick).head(5).itertuples()])


def _rows(csv: Path) -> int:
    with open(csv) as fh:
        return max(sum(1 for _ in fh) - 1, 0)


def _best_per_pair(csv: Path):
    """One row per flight pair, at its best spot. The engine's table lists every candidate spot,
    half a gigabyte for a whole-USA search, and nothing after the search needs more than this."""
    import pandas as pd
    best = None
    for chunk in pd.read_csv(csv, dtype={"icao1": str, "icao2": str}, chunksize=250_000, low_memory=False):
        best = pd.concat([best, chunk]).sort_values("score", ascending=False).drop_duplicates(["seg1", "seg2"])
    return best.reset_index(drop=True)


def _keep(path: Path, write) -> None:
    """All or nothing: a kill part-way through must not leave half a file under the real name."""
    part = path.with_name(path.name + ".part")
    write(part)
    part.replace(path)


def search(run: Path, spec: Path, day: date, index: Path, bbox, a: Flyover, b: Flyover,
           extras: list[Flyover], workers: int, ui) -> tuple[Path, int] | None:
    """Run the engine's search for one day and one pair. Returns the table to judge and how many
    flight pairs were scored, or None when no two flights that day fit the passes at all."""
    tag = f"{day}_e{a.rank}-e{b.rank}"
    folder = run / "search"
    folder.mkdir(exist_ok=True)
    raw, raw_extra = folder / f"{tag}.raw.csv", folder / f"{tag}.raw_extra.csv"
    pairs, with_extras = folder / f"{tag}.pairs.csv", folder / f"{tag}.extra.csv"
    narrowed = tuple(bbox) != index_box(index)      # narrowing to the index's own box would only cost time
    args = ["search", str(spec), "--events", str(a.path), str(b.path), "--day", str(index),
            "--workers", str(workers), "--out", str(raw)]
    if narrowed:
        args += ["--bbox", *(str(x) for x in bbox)]
    if extras:
        args += ["--extra", *(str(f.path) for f in extras), "--out-extra", str(raw_extra)]
    stamp = folder / f"{tag}.done.json"
    # what the result depends on and nothing else: a rerun on fewer cores must not search again
    key = {"index": index.name, "bbox": list(bbox) if narrowed else None, "events": [f.fit for f in (a, b, *extras)]}
    saved = json.loads(stamp.read_text()) if stamp.exists() else {}
    if {k: saved.get(k) for k in key} != key:       # else an identical search already ran
        steps = (("join: bucket", "pairing up flights"), ("lens: pair", "laying out candidate spots"),
                 ("rescored", "checking the other flyovers"), ("scored", "scoring spots against the audio"))

        def on_text(line: str) -> None:
            for marker, words in steps:
                m = re.search(re.escape(marker) + r" (\d+)/(\d+)", line)
                if m:
                    ui.progress(int(m.group(1)), int(m.group(2)), words)
                    return

        for stale in (pairs, with_extras, stamp):
            stale.unlink(missing_ok=True)
        try:
            try:
                run_engine(args, run / "logs" / f"search_{tag}.log", on_text, stall_s=STALL_S)
            except FlybyError:
                # with extras, the engine falls over on a search that found no flight pair at all;
                # it has written the empty table by then, and an empty table is a real answer
                if not (raw.exists() and _rows(raw) == 0):
                    raise
            n_pairs = 0
            if _rows(raw):
                best = _best_per_pair(raw)
                _keep(pairs, lambda part: best.to_csv(part, index=False))
                n_pairs = len(best.drop_duplicates(["icao1", "icao2"]))
                if extras and raw_extra.exists() and _rows(raw_extra):
                    rescored = _best_per_pair(raw_extra)
                    _keep(with_extras, lambda part: rescored.to_csv(part, index=False))
        finally:
            # half a gigabyte for a whole-USA search: gone whether it finished, failed or was stopped
            raw.unlink(missing_ok=True)
            raw_extra.unlink(missing_ok=True)
        saved = {**key, "pairs": n_pairs}
        _keep(stamp, lambda part: part.write_text(json.dumps(saved)))
    if not saved["pairs"]:
        return None
    return (with_extras if extras and with_extras.exists() else pairs), saved["pairs"]


def _index_box(bbox):
    """The box a day index is built for. Anywhere in the lower 48 shares one index per day,
    so a later search of another state does not stream the day again."""
    if CONUS[0] <= bbox[0] and bbox[1] <= CONUS[1] and CONUS[2] <= bbox[2] and bbox[3] <= CONUS[3]:
        return CONUS
    # outward to the 0.1 degree grid the index file is named on, so the name never claims less or more than was built
    return (max(math.floor(bbox[0] * 10) / 10, -90.0), min(math.ceil(bbox[1] * 10) / 10, 90.0),
            max(math.floor(bbox[2] * 10) / 10, -180.0), min(math.ceil(bbox[3] * 10) / 10, 180.0))


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _utc(unix: float) -> datetime:
    return datetime.fromtimestamp(unix, tz=timezone.utc)


def explain(run: Path, spec: Path, index: Path, a: Flyover, b: Flyover, extras: list[Flyover], row: dict, ui):
    """Pin the answer down on a 50 m grid and gather what the report shows: which aircraft,
    when each was heard, and a picture of each predicted curve over the recording."""
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from dopplertrack import pipeline
    from dopplertrack.audio import Spectrogram
    from dopplertrack.doppler import speed_of_sound
    from dopplertrack.geo import lla_to_enu
    from dopplertrack.plots import plot_modelled
    from dopplertrack.score import EventWindow, modelled_curve
    from dopplertrack.search import F0_SCALES, DayIndex, pick_flight
    from dopplertrack.terrain import Terrain
    from dopplertrack.tracks import EventFit

    ui.start("Pinning the spot down")
    sp = Spectrogram.load(spec)
    idx = DayIndex.load(index)
    if idx.icao[int(row["seg1"])] != row["icao1"] or idx.icao[int(row["seg2"])] != row["icao2"]:
        raise FlybyError(f"the search results in {run} were not made from {index.name}; delete the run folder and try again")
    elevation = Terrain().elevation
    c = speed_of_sound()
    fit = lambda f: EventFit(**f.fit)
    window = lambda f: EventWindow.from_fit(sp, fit(f), 1)

    def spot(lat: float, lon: float):
        return lla_to_enu(lat, lon, max(float(elevation(np.array([lat]), np.array([lon]))[0]), 0.0), idx.lat0, idx.lon0)

    # an extra with no flight nearby would stop the engine's refine, so only matched ones go in
    obs0 = spot(float(row["lat"]), float(row["lon"]))
    matched = []
    for f in extras:
        k, sc = pick_flight(idx, window(f), obs0, a.t - float(row["t_heard1"]), float(row["shift"]), c)
        if k >= 0 and sc >= MATCHED_SHARE * f.fit["score"]:
            matched.append(f)
    fine = pipeline.refine(sp, fit(a), fit(b), idx, pd.Series(row), spacing_m=50.0, half_m=600.0, elevation=elevation,
                           extra=[fit(f) for f in matched], log=lambda msg: None)
    lat, lon, shift = float(fine.lat[0]), float(fine.lon[0]), float(fine["shift"][0])
    obs = spot(lat, lon)

    seg1 = idx.segs[int(row["seg1"])]
    d1 = np.linalg.norm(seg1.enu - obs, axis=1)
    j1 = int(np.argmin(d1))
    t0_map = a.t - float(seg1.t[j1] + d1[j1] / c)       # recording time = unix time + t0_map + shift
    heard = [(a, seg1), (b, idx.segs[int(row["seg2"])])]
    for f in list(matched):
        k, _ = pick_flight(idx, window(f), obs, t0_map, shift, c)
        if k < 0:
            matched.remove(f)
        else:
            heard.append((f, idx.segs[k]))

    known = json.loads(flights_file(index).read_text()) if flights_file(index).exists() else {}
    flights, figures = [], []
    (run / "figures").mkdir(exist_ok=True)
    for f, seg in heard:
        d = np.linalg.norm(seg.enu - obs, axis=1)
        j = int(np.argmin(d))
        reg, typ, desc = known.get(seg.icao, [None, None, None])
        flights.append(Flight(icao=seg.icao, heard_utc=_utc(float(seg.t[j] + d[j] / c)), slant_m=float(d[j]),
                              speed_ms=float(seg.speed[j]), registration=reg, type_name=desc or typ))
        to, ratio = modelled_curve(seg, obs, c)
        scale = float(F0_SCALES[int(np.argmax(window(f).sweep_scores(to + t0_map, ratio, [shift], F0_SCALES)[0]))])
        fig, ax = plt.subplots(figsize=(9, 3.2), dpi=110)
        plot_modelled(sp, fit(f), seg, obs, t0_map + shift, f0_scale=scale, ax=ax,
                      label=f"Flyover at {_mmss(f.t)}: {reg or seg.icao}" + (f", {desc or typ}" if desc or typ else ""))
        ax.set_xlabel("seconds into the recording")
        fig.tight_layout()
        path = run / "figures" / f"e{f.rank}.png"
        fig.savefig(path)
        plt.close(fig)
        figures.append(path)
    ui.done("Spot pinned down on a 50 m grid")
    return lat, lon, _utc(-(t0_map + shift)), flights, figures, matched


def _events(flyovers: list[Flyover], a: Flyover | None, b: Flyover | None, extras: list[Flyover]) -> list[Event]:
    role = lambda f: "pair" if f is a or f is b else "extra" if any(f is x for x in extras) else "not used"
    return [Event(t_cpa=f.t, f0=f.fit["f0"], v=f.fit["v"], d=f.fit["d"], score=f.strength, role=role(f), json_path=f.path)
            for f in sorted(flyovers, key=lambda f: f.t)]


def locate(media: Media, clues: Clues, flyovers: list[Flyover], dropped: dict[str, int], run: Path,
           days_root: Path, ui, workers: int | None = None, max_searches: int = MAX_SEARCHES) -> Result:
    import pandas as pd

    notes = list(media.notes)
    if dropped:
        notes.append("Other sounds were set aside: " + ", ".join(f"{n} x {why}" for why, n in dropped.items()) + ".")
    if len(flyovers) < 2:
        heard = "No aircraft could be picked out of the audio." if not flyovers else \
            "Only one aircraft could be picked out of the audio, and the method needs two."
        return Result(verdict="no flyovers", headline=heard, events=_events(flyovers, None, None, []),
                      notes=notes, work_dir=run)

    spec = run / "rec.npz"
    # two thirds of the cores, at low priority (see runner): a search runs for many minutes
    # and the machine has to stay usable meanwhile
    workers = workers or max((os.cpu_count() or 2) * 2 // 3, 1)
    start_unix = clues.start_utc.timestamp() if clues.start_utc else None
    box = _index_box(clues.bbox)
    searched: list[str] = []
    no_data: set[date] = set()
    best = None             # (judgement, day, index, a, b, extras)
    done = 0
    for day, a, b, extras in plan(flyovers, clues.days, max_searches):
        if day in no_data:
            continue
        index = cached_index(day, box, days_root)
        if index is None:
            ui.start(f"Streaming the flight tracks for {day} (about 4 GB, once per day: the index is kept)")
            try:
                index = build_day_index(day, box, days_root,
                                        lambda got, total, n: ui.progress(got, total, f"{got / 1e9:.1f} of {total / 1e9:.1f} GB"))
            except NoFlightData as e:
                no_data.add(day)
                searched.append(f"{day}: {e}")
                ui.done(f"{day}: no flight data")
                continue
            ui.done(f"Flight tracks for {day} ready")

        what = f"{day}, passes at {_mmss(a.t)} and {_mmss(b.t)}"
        ui.start(f"Searching {clues.area_label}: {what}")
        found = search(run, spec, day, index, clues.bbox, a, b, extras, workers, ui)
        done += 1
        j = judge(pd.read_csv(found[0], dtype={"icao1": str, "icao2": str}, low_memory=False), a.t, start_unix) if found else None
        if j is None:
            searched.append(f"{what}: no two flights that day fit both passes")
            ui.done(f"{what}: no two flights fit both passes")
            continue
        outcome = "strong match" if j.p * done <= LOCATED_P else "weak match" if j.p * done <= HINT_P else "nothing stood out"
        searched.append(f"{what}: {outcome} among {found[1]:,} flight pairs")
        ui.done(f"{what}: {outcome}")
        if best is None or j.p < best[0].p:
            best = (j, day, index, a, b, extras)
        if j.p * done <= LOCATED_P:
            break

    if best is None:
        why = "the flight archive has no data for the days searched" if not done else "no flights in the data fit these flyovers"
        return Result(verdict="no match", headline=f"No location found: {why}.", events=_events(flyovers, None, None, []),
                      notes=notes, searched=searched, work_dir=run)

    j, day, index, a, b, extras = best
    p = min(1.0, j.p * done)        # every search is another chance for a wrong answer to look good
    if p > HINT_P:
        return Result(verdict="no match", headline="No location found: nothing in the flight data stands out for these flyovers.",
                      p=p, events=_events(flyovers, a, b, extras), notes=notes, searched=searched, work_dir=run)

    lat, lon, started, flights, figures, matched = explain(run, spec, index, a, b, extras, j.row, ui)
    located = p <= LOCATED_P
    if j.clock_agrees is False:
        notes.append(f"This match means the recording began at {started:%H:%M} UTC on {started:%Y-%m-%d}, "
                     f"but the source says {clues.start_utc:%H:%M} UTC on {clues.start_utc:%Y-%m-%d}. One of the two is wrong.")
    elif j.clock_agrees:
        notes.append("The start time this match implies agrees with the start time the source states.")
    if extras:
        notes.append(f"{len(matched)} of {len(extras)} further flyovers also match a real flight at this spot."
                     if matched else "None of the further flyovers matches a real flight at this spot.")
    if not located:
        notes.append("This is a lead, not an answer. Check it against what the video shows before trusting it.")
    return Result(verdict="located" if located else "not sure",
                  headline=f"Recorded at {lat:.5f}, {lon:.5f}." if located else "A weak match only: this is a best guess.",
                  lat=lat, lon=lon, p=p, margin_pct=j.margin_pct, day=day, recording_start_utc=started,
                  events=_events(flyovers, a, b, extras), flights=flights, runners_up=j.runners_up, figures=figures,
                  notes=notes, searched=searched, work_dir=run)
