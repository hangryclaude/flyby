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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .dayindex import (SMALL_SHARE, SQUEEZE_GB, _tag, build_day_index, cached_index, cut_index, flights_file, free_memory_gb,
                       grid_box, index_box, is_small_part)
from .measure import Flyover
from .model import CONUS, Clues, Event, Flight, FlybyError, Media, NoFlightData, Result, Sighting
from .runner import STALL_S, one_at_a_time, run_engine
from .weather import CRUISE_M, STILL_AIR_C, Air, fetch_air

# The engine calls p < 0.1 "located", and its author saw 2 of 20 searches with no right answer
# pass that bar. flyby asks for ten times better before it names a place.
LOCATED_P = 0.01
HINT_P = 0.10
MAX_SEARCHES = 6
MAX_EXTRAS = 2
CLOCK_TOL_S = 180.0         # how far a stated start time may be off and still count as agreeing
MATCHED_SHARE = 0.5         # a further flyover is matched when a real flight explains half its brightness
WEATHER_MAX_SPAN_DEG = 14.0     # one temperature cannot stand for a box wider than a large state
NEAR_DEG = 0.5                  # the answer is pinned down with the flights this close to it
NEAR_CUT_BYTES = 300 << 20      # an index over this size is cut down to those first


@dataclass
class Judgement:
    row: dict               # the chosen flight pair at its best point: a row of the engine's table
    p: float                # chance a wrong answer would look this good, in this one search
    rank: int               # 1 = the engine's own winner
    n_pairs: int
    margin_pct: float | None    # the winner's lead over the best pair that shares no flight with it
    clock_agrees: bool | None   # None when nothing says when the recording began
    runners_up: list[tuple[float, float, float]]
    place_agrees: bool | None = None    # None when no town-sized place was named


def plan(flyovers: list[Flyover], days: list[date], limit: int = MAX_SEARCHES):
    """The searches to run, most promising first: (day, earlier pass, later pass, extras)."""
    # a longer run may also pair up the fourth-clearest pass
    combos = ((0, 1), (0, 2), (1, 2)) + (((0, 3), (1, 3), (2, 3)) if limit > MAX_SEARCHES else ())
    pairs = [(flyovers[i], flyovers[j]) for i, j in combos if j < len(flyovers)]
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


def judge(table, t_cpa1: float, start_unix: float | None = None, tol_s: float = CLOCK_TOL_S,
          focus: tuple[float, float, float, float] | None = None) -> Judgement | None:
    """Pick the answer from one search's table and say how far it can be trusted.

    With nothing else known this is the engine's own test: how far the winner leads the crowd
    of wrong flight pairs scored on the same audio.

    Whatever is known about the recording adds a second, independent test. Every flight pair
    implies a start time and a spot, and a wrong pair's are as good as random: only a small
    share q of the pairs agree with a stated start time (within tol_s), or fall inside a named
    town (focus). If the winner agrees, both tests passed: p = q x p_score. If a lower pair is
    the best one that agrees, p is the chance that any of the agreeing pairs would rank that
    high by luck. The start time, the place and both together are each looked at, next to the
    plain test, and the best look is paid for by multiplying by the number of looks."""
    from dopplertrack.verdict import verdict
    table = table[np.isfinite(table.score)]     # one NaN score would make every figure below NaN
    v = verdict(table)
    if not v.n_pairs:
        return None
    best = table.sort_values("score", ascending=False).drop_duplicates(["icao1", "icao2"]).reset_index(drop=True)
    judged = v.verdict != "insufficient"
    p_score = _lead_p(v.gap, v.tail_width, v.k) if judged else 1.0
    in_time = in_place = None
    if start_unix is not None:
        in_time = np.abs(best.t_heard1.to_numpy() - t_cpa1 - start_unix) <= tol_s
    if focus is not None:
        lat, lon = best.lat.to_numpy(), best.lon.to_numpy()
        in_place = (lat >= focus[0]) & (lat <= focus[1]) & (lon >= focus[2]) & (lon <= focus[3])
    told = [x for x in (in_time, in_place) if x is not None]
    if len(told) == 2:
        told.append(in_time & in_place)
    looks = [(p_score, 0)]
    n = len(best)
    for agrees in told:
        m = int(agrees.sum())
        if m:
            r = int(np.argmax(agrees))
            # r > 0: the chance that the best of m random pairs out of n lands in the top r + 1
            looks.append((p_score * m / n if r == 0 else 1 - float(np.prod((n - r - 1 - np.arange(m)) / (n - np.arange(m)))), r))
    p, pick = min(looks)
    p = min(1.0, (1 + len(told)) * p)
    row = best.iloc[pick]
    margin = 100 * v.gap / (row.score - v.gap) if judged and pick == 0 and row.score > v.gap > 0 else None
    return Judgement(row=row.to_dict(), p=float(p), rank=pick + 1, n_pairs=n, margin_pct=margin,
                     clock_agrees=None if in_time is None else bool(in_time[pick]),
                     place_agrees=None if in_place is None else bool(in_place[pick]),
                     runners_up=[(float(o.lat), float(o.lon), float(o.score)) for o in best.drop(index=pick).head(5).itertuples()])


def _share(k: int, planned: int) -> float:
    """The part of the 1-in-100 allowance that the k-th of `planned` searches may use.

    Every search is one more chance for a wrong answer to look good, and flyby stops at the
    first strong match, so the chances add up over a run. The plan is fixed before the first
    search and puts the likeliest day and pair first. So the first search gets the largest
    share and each later one less, as 1/(k(k+1)), scaled so the shares of the whole plan add
    up to 1. A wrong "located" then stays under 1 run in 100 however many searches are allowed.
    (Multiplying by the number run so far, as this did before, let through 2.4 in 100 over six
    searches and 3.1 in 100 over twelve.)"""
    return (planned + 1) / (planned * k * (k + 1))


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
           extras: list[Flyover], workers: int, ui, air_c: float | None = None) -> tuple[Path, int] | None:
    """Run the engine's search for one day and one pair. Returns the table to judge and how many
    flight pairs were scored, or None when no two flights that day fit the passes at all.
    air_c: the air temperature at flying height, when the weather was looked up."""
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
    key = {"index": index.name, "bbox": list(bbox) if narrowed else None, "events": [f.fit for f in (a, b, *extras)],
           "air_c": air_c}
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
                with one_at_a_time(run.parent, lambda: ui.progress(0, 1, "waiting for another flyby search to finish")):
                    _warn_if_tight(index, ui)
                    run_engine(args, run / "logs" / f"search_{tag}.log", on_text, stall_s=STALL_S, air_c=air_c)
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


def _warn_if_tight(index: Path, ui) -> None:
    """Say so when the search will not fit in what is free. It still runs: macOS squeezes other
    apps to make room, and both get slower."""
    if not index.exists():
        return
    # measured: a whole-USA day's 5.8 GB index is held as 7.3 GB and twelve workers add 7.7 GB;
    # New York's 0.8 GB index ran in 1.5 GB
    need = index.stat().st_size / 2 ** 30 * 2.6
    free = free_memory_gb()
    if free is not None and need > SQUEEZE_GB and free < need:
        ui.note(f"Memory is tight: this search holds about {need:.0f} GB and {free:.0f} GB is free. "
                "It will run, but slower, and other apps will slow down too. Naming a state (--area) needs far less.")


def _index_box(bbox):
    """The box to stream a day's flights for. A search of most of the lower 48 gets the
    whole-USA index, which then serves every search of that day. A named area gets its own
    small one: the whole-USA index takes 14 GB of memory to build and 6 GB of disk a day."""
    inside = CONUS[0] <= bbox[0] and bbox[1] <= CONUS[1] and CONUS[2] <= bbox[2] and bbox[3] <= CONUS[3]
    if inside and (bbox[1] - bbox[0]) * (bbox[3] - bbox[2]) >= SMALL_SHARE * (CONUS[1] - CONUS[0]) * (CONUS[3] - CONUS[2]):
        return CONUS
    return grid_box(bbox)


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _utc(unix: float) -> datetime:
    return datetime.fromtimestamp(unix, tz=timezone.utc)


@dataclass
class Pinned:
    lat: float
    lon: float
    started: datetime           # when the recording began, by this match
    flights: list[Flight]       # the aircraft behind each flyover used
    figures: list[Path]
    matched: list[Flyover]      # the further flyovers a real flight explains
    sightings: list[Sighting]   # other aircraft that passed the spot, checked against the recording
    air_c: float | None
    heights_m: tuple[float, float] = (0.0, 0.0)     # how far above the spot the two aircraft of the pair passed


def _draw_sighting(sp, q, title: str, path: Path) -> None:
    """The tone an aircraft the search never used should have made, over what was recorded."""
    import matplotlib.pyplot as plt
    from dopplertrack.tracks import local_whiten
    curve = q.pitch_hz * q.ratio
    view = local_whiten(sp.crop(q.t_s[0], q.t_s[-1], max(curve.min() * 0.7, sp.f[0]), min(curve.max() * 1.3, sp.f[-1])))
    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=110)
    ax.imshow(view.S, origin="lower", aspect="auto", extent=[view.t[0], view.t[-1], view.f[0], view.f[-1]],
              vmin=0, vmax=10, cmap="magma")
    ax.plot(q.t_s, curve, "c-", lw=0.9)
    ax.set_xlim(view.t[0], view.t[-1])
    ax.set_ylim(view.f[0], view.f[-1])
    ax.set_title(title)
    ax.set_ylabel("Hz")
    ax.set_xlabel("seconds into the recording")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def explain(run: Path, spec: Path, index: Path, a: Flyover, b: Flyover, extras: list[Flyover], row: dict, ui,
            air: Air | None = None) -> Pinned:
    """Pin the answer down on a 50 m grid and gather what the report shows: which aircraft,
    when each was heard, a picture of each predicted curve over the recording, and whether
    the other aircraft that passed the spot can be seen in the recording too."""
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
    from .verify import Tracks, check, height_above

    ui.step("pin")
    sp = Spectrogram.load(spec)
    stale = FlybyError(f"the search results in {run} were not made from {index.name}; delete the run folder and try again")
    pair = np.array([int(row["seg1"]), int(row["seg2"])])
    near = index
    if index.stat().st_size > NEAR_CUT_BYTES:
        # from here on only the flights around the answer matter, and the engine reads an index whole
        lat, lon = float(row["lat"]), float(row["lon"])
        wide = NEAR_DEG / math.cos(math.radians(lat))
        near = cut_index(index, (lat - NEAR_DEG, lat + NEAR_DEG, lon - wide, lon + wide), run / "near.npz")
        parent = np.load(near)["parent_seg"]
        was, pair = pair, np.searchsorted(parent, pair)
        if (pair >= len(parent)).any() or (parent[pair] != was).any():
            raise stale
        row = {**row, "seg1": int(pair[0]), "seg2": int(pair[1])}
    idx = DayIndex.load(near)
    if pair.max() >= len(idx.icao) or idx.icao[pair[0]] != row["icao1"] or idx.icao[pair[1]] != row["icao2"]:
        raise stale
    elevation = Terrain().elevation
    fit = lambda f: EventFit(**f.fit)
    window = lambda f: EventWindow.from_fit(sp, fit(f), 1)

    def spot(lat: float, lon: float):
        return lla_to_enu(lat, lon, max(float(elevation(np.array([lat]), np.array([lon]))[0]), 0.0), idx.lat0, idx.lon0)

    obs0 = spot(float(row["lat"]), float(row["lon"]))

    def height(seg) -> float:
        """How far above the listener this flight was at its closest."""
        return height_above(seg.enu[int(np.argmin(np.linalg.norm(seg.enu - obs0, axis=1)))], obs0, idx.lat0, idx.lon0)

    # the pitch of a moving source is set by the air it flies in, so the temperature is taken up there
    # (the engine pins the spot down with one speed of sound for both aircraft, so it gets the middle of the two)
    heights = (height(idx.segs[int(row["seg1"])]), height(idx.segs[int(row["seg2"])]))
    air_c = round(sum(air.temp_at(h) for h in heights) / 2, 1) if air else None
    c = speed_of_sound(STILL_AIR_C if air_c is None else air_c)

    # an extra with no flight nearby would stop the engine's refine, so only matched ones go in
    matched = []
    for f in extras:
        k, sc = pick_flight(idx, window(f), obs0, a.t - float(row["t_heard1"]), float(row["shift"]), c)
        if k >= 0 and sc >= MATCHED_SHARE * f.fit["score"]:
            matched.append(f)
    fine = pipeline.refine(sp, fit(a), fit(b), idx, pd.Series(row), spacing_m=50.0, half_m=600.0, elevation=elevation,
                           c=c, extra=[fit(f) for f in matched], log=lambda msg: None)
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
        plot_modelled(sp, fit(f), seg, obs, t0_map + shift, f0_scale=scale, ax=ax, c=c,
                      label=f"Flyover at {_mmss(f.t)}: {reg or seg.icao}" + (f", {desc or typ}" if desc or typ else ""))
        ax.set_xlabel("seconds into the recording")
        fig.tight_layout()
        path = run / "figures" / f"e{f.rank}.png"
        fig.savefig(path)
        plt.close(fig)
        figures.append(path)
    ui.done("Spot pinned down on a 50 m grid")

    ui.step("others")
    start_unix = -(t0_map + shift)
    c_at = (lambda h: speed_of_sound(air.temp_at(max(h, 0.0)))) if air else (lambda h: c)
    seen = check(sp, Tracks(near), obs, start_unix, {seg.icao for _, seg in heard}, c_at)
    sightings = []
    for q in seen.others:
        reg, typ, desc = known.get(q.icao, [None, None, None])
        sightings.append(Sighting(flight=Flight(icao=q.icao, heard_utc=_utc(start_unix + q.heard_s), slant_m=q.slant_m,
                                                speed_ms=q.speed_ms, registration=reg, type_name=desc or typ),
                                  heard_s=q.heard_s, seen=q.seen, clearer_than=1.0 - q.p))
        if q.seen:
            path = run / "figures" / f"other-{q.icao.lstrip('~')}-{int(q.heard_s)}.png"
            _draw_sighting(sp, q, f"Not used by the search, at {_mmss(q.heard_s)}: {reg or q.icao}"
                           + (f", {desc or typ}" if desc or typ else ""), path)
            figures.append(path)
    ui.done(f"{sum(x.seen for x in sightings)} of {len(sightings)} other aircraft can be seen in the recording")
    return Pinned(lat, lon, _utc(start_unix), flights, figures, matched, sightings, air_c, heights)


def _events(flyovers: list[Flyover], a: Flyover | None, b: Flyover | None, extras: list[Flyover]) -> list[Event]:
    role = lambda f: "pair" if f is a or f is b else "extra" if any(f is x for x in extras) else "not used"
    return [Event(t_cpa=f.t, f0=f.fit["f0"], v=f.fit["v"], d=f.fit["d"], score=f.strength, role=role(f), json_path=f.path)
            for f in sorted(flyovers, key=lambda f: f.t)]


def _when(clues: Clues, media: Media, day: date) -> datetime:
    """The hour to ask the weather archive about for a search of that day."""
    if clues.start_utc is not None:
        middle = (clues.start_utc + timedelta(seconds=media.duration_s / 2)).astimezone(timezone.utc)
        if middle.date() == day:
            return middle
    # no clock: 20:00 UTC is afternoon across the lower 48, and the air aloft changes little over a day
    return datetime(day.year, day.month, day.day, 20, tzinfo=timezone.utc)


def _search_air_c(clues: Clues, media: Media, day: date) -> float | None:
    """The air temperature at flying height over the search box, or None to leave the engine at 15 C."""
    south, north, west, east = clues.bbox
    if north - south > WEATHER_MAX_SPAN_DEG or east - west > WEATHER_MAX_SPAN_DEG:
        return None
    air = fetch_air((south + north) / 2, (west + east) / 2, _when(clues, media, day))
    # whole degrees: the search is kept and reused by temperature, and a tenth of a degree changes nothing
    return float(round(air.temp_at(CRUISE_M))) if air else None


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
    searched: list[str] = []
    no_data: set[date] = set()
    best = None             # (p for the whole run, judgement, day, index, a, b, extras)
    done = 0
    steps = plan(flyovers, clues.days, max_searches)
    for day, a, b, extras in steps:
        if day in no_data:
            continue
        index = cached_index(day, clues.bbox, days_root)
        try:
            if index is None:
                ui.step("tracks", day=day)
                with one_at_a_time(run.parent, lambda: ui.progress(0, 1, "waiting for another flyby search to finish")):
                    index = build_day_index(day, _index_box(clues.bbox), days_root,
                                            lambda got, total, n: ui.progress(got, total, f"{got / 1e9:.1f} of {total / 1e9:.1f} GB"))
                ui.done(f"Flight tracks for {day} ready")
            elif is_small_part(clues.bbox, index):
                # the engine holds a whole index in memory for the length of a search: 7 GB for a whole-USA day
                ui.step("cut_day", day=day)
                index = cut_index(index, clues.bbox, days_root / f"{day}_{_tag(grid_box(clues.bbox))}.npz")
                ui.done(f"Flights of {day} across the search area ready")
        except NoFlightData as e:
            no_data.add(day)
            searched.append(f"{day}: {e}")
            ui.done(f"{day}: no flight data")
            continue

        what = f"{day}, passes at {_mmss(a.t)} and {_mmss(b.t)}"
        ui.step("search", area=clues.area_label, what=what)
        found = search(run, spec, day, index, clues.bbox, a, b, extras, workers, ui, _search_air_c(clues, media, day))
        done += 1
        j = judge(pd.read_csv(found[0], dtype={"icao1": str, "icao2": str}, low_memory=False), a.t, start_unix,
                  clues.start_tol_s, clues.focus) if found else None
        if j is None:
            searched.append(f"{what}: no two flights that day fit both passes")
            ui.done(f"{what}: no two flights fit both passes")
            continue
        p_run = min(1.0, j.p / _share(done, len(steps)))
        outcome = "strong match" if p_run <= LOCATED_P else "weak match" if p_run <= HINT_P else "nothing stood out"
        searched.append(f"{what}: {outcome} among {found[1]:,} flight pairs")
        ui.done(f"{what}: {outcome}")
        if best is None or p_run < best[0]:
            best = (p_run, j, day, index, a, b, extras)
        if p_run <= LOCATED_P:
            break

    if best is None:
        why = "the flight archive has no data for the days searched" if not done else "no flights in the data fit these flyovers"
        return Result(verdict="no match", headline=f"No location found: {why}.", events=_events(flyovers, None, None, []),
                      notes=notes, searched=searched, work_dir=run)

    p, j, day, index, a, b, extras = best
    if p > HINT_P:
        return Result(verdict="no match", headline="No location found: nothing in the flight data stands out for these flyovers.",
                      p=p, events=_events(flyovers, a, b, extras), notes=notes, searched=searched, work_dir=run)

    air = fetch_air(float(j.row["lat"]), float(j.row["lon"]), _utc(float(j.row["t_heard1"])))
    pin = explain(run, spec, index, a, b, extras, j.row, ui, air)
    started, matched = pin.started, pin.matched
    located = p <= LOCATED_P
    if j.clock_agrees is False:
        notes.append(f"This match means the recording began at {started:%H:%M} UTC on {started:%Y-%m-%d}, "
                     f"but the start time given is {clues.start_utc:%H:%M} UTC on {clues.start_utc:%Y-%m-%d}. One of the two is wrong.")
    elif j.clock_agrees:
        notes.append("The start time this match implies agrees with the start time given.")
    if j.place_agrees is False:
        notes.append(f"The answer is not in or near {clues.focus_label}, the place given. One of the two is wrong.")
    elif j.place_agrees:
        notes.append(f"The answer is in or near {clues.focus_label}, the place given.")
    if extras:
        notes.append(f"{len(matched)} of {len(extras)} further flyovers also match a real flight at this spot."
                     if matched else "None of the further flyovers matches a real flight at this spot.")
    if pin.sightings:
        n_seen = sum(x.seen for x in pin.sightings)
        notes.append(f"{len(pin.sightings)} other aircraft flew past this spot during the recording. "
                     + (f"{n_seen} of them can be seen in the sound at the second the tracks predict. "
                        "At a wrong spot that happens for about 1 aircraft in 100." if n_seen else
                        "None of them can be seen in the sound. That is common: most are too far or too quiet."))
    if pin.air_c is not None:
        low, high = (f"{round(h, -1 if h < 1000 else -2):,.0f} m" for h in sorted(pin.heights_m))
        notes.append(f"The two matched aircraft passed about {low} and {high} above this spot. The air at those heights "
                     f"averaged {pin.air_c:.0f} C (Open-Meteo), and the spot was pinned down with the speed of sound for that.")
    if not located:
        notes.append("This is a lead, not an answer. Check it against what the video shows before trusting it.")
    return Result(verdict="located" if located else "not sure",
                  headline=f"Recorded at {pin.lat:.5f}, {pin.lon:.5f}." if located else "A weak match only: this is a best guess.",
                  lat=pin.lat, lon=pin.lon, p=p, margin_pct=j.margin_pct, day=day, recording_start_utc=started,
                  events=_events(flyovers, a, b, extras), flights=pin.flights, runners_up=j.runners_up, figures=pin.figures,
                  sightings=pin.sightings, air_c=pin.air_c, notes=notes, searched=searched, work_dir=run)
