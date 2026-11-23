"""Day indexes: one UTC day of flight tracks, cut down to what a search needs.

The engine can stream a day by itself, but over a single connection, and GitHub serves a
release asset at about 1 MB/s per connection. A 4 GB day took an hour that way. Here the same
bytes come down as ranges over several connections and are handed to the engine's tar reader
in order, so the pipe is filled and still nothing but the finished index touches the disk.
"""
from __future__ import annotations

import collections
import http.client
import io
import json
import math
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

from .model import CONUS, FlybyError, NoFlightData

API = "https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/v{tag}-planes-readsb-prod-0"
UA = {"User-Agent": "flyby/0.1 (personal research tool)"}
CHUNK = 8 << 20
CONNS = 8
BUILD_PEAK_GB = 14      # measured: a whole-USA day while the engine assembles its index
SQUEEZE_GB = 6          # measured on a 48 GB Mac: 8 GB past full ran slow but steady, 15 GB past full thrashed
PAD_DEG = 0.3           # a pass is heard from 12 km and a track is sampled every 10 s; 0.3 degrees is 23 km at 45 N
SMALL_SHARE = 0.25      # a search box under this share of an index's own box gets a small index cut for it


def free_memory_gb() -> float | None:
    """Memory that can be had without squeezing other apps: free pages plus cached files.
    kern.memorystatus_level is no use for this. It also counts idle app memory, which must be
    compressed before it can be handed over: it read 20 GB on a Mac with 0.2 GB free and
    21 GB already compressed."""
    if sys.platform != "darwin":
        return None
    out = subprocess.run(["vm_stat"], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True).stdout
    page = int(re.search(r"page size of (\d+) bytes", out).group(1))
    pages = {m[1]: int(m[2]) for m in re.finditer(r"^(.+?):\s+(\d+)\.$", out, re.M)}
    return sum(pages[k] for k in ("Pages free", "Pages speculative", "Pages purgeable", "File-backed pages")) * page / 2 ** 30


def release_assets(day: date) -> list[tuple[str, int]]:
    """(url, size) of the day's tar parts, in the order they concatenate."""
    url = API.format(year=day.year, tag=day.strftime("%Y.%m.%d"))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoFlightData(f"the flight archive has no data for {day} (it starts in 2023 and runs a few hours behind)") from None
        if e.code in (403, 429):
            raise FlybyError("GitHub is rate-limiting this network; wait an hour and run again") from None
        raise
    parts = sorted((a for a in rel["assets"] if ".tar" in a["name"]), key=lambda a: a["name"])
    if not parts:
        raise NoFlightData(f"the flight archive's entry for {day} has no data files yet")
    return [(a["browser_download_url"], a["size"]) for a in parts]


def _get_range(url: str, a: int, b: int, tries: int = 6) -> bytes:
    want = b - a + 1
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={a}-{b}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                # a 200 means the server ignored Range and is about to send the whole 2 GB part
                if r.status == 206:
                    data = r.read()
                    if len(data) == want:
                        return data
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass
        time.sleep(min(30, 2 ** k))
    raise FlybyError(f"the flight archive download kept failing at byte {a}; check the connection and run again")


class RangedStream(io.RawIOBase):
    """A day's tar parts as one read-only stream, fetched ahead as ranges over several connections."""

    def __init__(self, assets: list[tuple[str, int]], conns: int = CONNS, chunk: int = CHUNK):
        self.jobs = [(url, a, min(a + chunk, size) - 1) for url, size in assets for a in range(0, size, chunk)]
        self.size = sum(size for _, size in assets)
        self.total = 0          # bytes handed to the reader so far
        self.pool = ThreadPoolExecutor(conns)
        self.ahead = conns * 2  # bounds memory: at most this many chunks are in flight or waiting
        self.pending: collections.deque = collections.deque()
        self.next = 0
        self.buf = memoryview(b"")

    def readable(self):
        return True

    def _fill(self):
        while self.next < len(self.jobs) and len(self.pending) < self.ahead:
            self.pending.append(self.pool.submit(_get_range, *self.jobs[self.next]))
            self.next += 1

    def readinto(self, b):
        if not len(self.buf):
            self._fill()
            if not self.pending:
                return 0
            self.buf = memoryview(self.pending.popleft().result())
            self._fill()
        n = min(len(b), len(self.buf))
        b[:n] = self.buf[:n]
        self.buf = self.buf[n:]
        self.total += n
        return n

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().close()


def _tag(bbox) -> str:
    return "_".join(f"{x:.1f}" for x in bbox)


def _covers(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] >= inner[1] and outer[2] <= inner[2] and outer[3] >= inner[3]


def _area(box) -> float:
    return (box[1] - box[0]) * (box[3] - box[2])


def grid_box(bbox) -> tuple[float, float, float, float]:
    """bbox moved outward to the 0.1 degree grid an index file is named on, so the name never claims less than was built."""
    return (max(math.floor(bbox[0] * 10) / 10, -90.0), min(math.ceil(bbox[1] * 10) / 10, 90.0),
            max(math.floor(bbox[2] * 10) / 10, -180.0), min(math.ceil(bbox[3] * 10) / 10, 180.0))


def _padded(bbox) -> tuple[float, float, float, float]:
    """bbox plus the margin a flight outside it can still be heard across."""
    wide = PAD_DEG / max(math.cos(math.radians(max(abs(bbox[0]), abs(bbox[1])))), 0.2)
    return bbox[0] - PAD_DEG, bbox[1] + PAD_DEG, bbox[2] - wide, bbox[3] + wide


def is_small_part(bbox, index: Path) -> bool:
    """Is bbox so much smaller than the box this index was built for that a cut of it is worth making?"""
    have = index_box(index)
    return bool(have) and _area(grid_box(bbox)) < SMALL_SHARE * _area(have)


def index_box(index: Path) -> tuple[float, ...] | None:
    """The box an index file was built for, read back from its name."""
    try:
        box = tuple(float(x) for x in index.stem.split("_")[1:])
    except ValueError:
        return None
    return box if len(box) == 4 else None


def cached_index(day: date, bbox, root: Path) -> Path | None:
    """The smallest index on disk that serves this day and box. A wider box will do: the search narrows it."""
    fits = [(_area(have), p) for p in sorted(root.glob(f"{day}_*.npz"))
            if not p.name.endswith(".part.npz") and (have := index_box(p)) and _covers(have, bbox)]
    return min(fits)[1] if fits else None


def mapped(path: Path, name: str) -> np.memmap:
    """One array of an uncompressed .npz, mapped instead of read."""
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(name + ".npy")
    if info.compress_type != zipfile.ZIP_STORED:
        raise ValueError(f"{path.name} is compressed, so it cannot be mapped")
    with open(path, "rb") as fh:
        fh.seek(info.header_offset + 26)
        n_name, n_extra = struct.unpack("<HH", fh.read(4))
        fh.seek(info.header_offset + 30 + n_name + n_extra)
        major, _ = np.lib.format.read_magic(fh)
        read = np.lib.format.read_array_header_1_0 if major == 1 else np.lib.format.read_array_header_2_0
        shape, fortran, dtype = read(fh)
        offset = fh.tell()
    return np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=shape, order="F" if fortran else "C")


def cut_index(src: Path, bbox, out: Path) -> Path:
    """Write the part of a day index that a search of bbox can touch, as a small index of its own.

    The engine reads an index whole and holds it for the length of a search: 7 GB for a
    whole-USA day. A state needs a few percent of that. Every flight with a position in the
    box, or within the margin a pass can be heard across, is kept whole, in the same frame and
    the same order, so a search of the box pairs and scores exactly what it would have.
    The source is mapped, not read, so making the cut takes little memory itself.
    parent_seg in the cut gives, for each flight kept, its number in the source."""
    from dopplertrack.geo import enu_to_lla

    z = np.load(src)
    off, lat0, lon0 = z["off"], float(z["lat0"]), float(z["lon0"])
    sb, ss, sj, t, enu, spd = (mapped(src, n) for n in ("sb", "ss", "sj", "t", "enu", "spd"))
    seg_of = np.asarray(ss)
    rows = off[seg_of] + np.asarray(sj)
    south, north, west, east = _padded(bbox)
    keep = np.zeros(len(off) - 1, bool)
    order = np.argsort(rows)            # file order, so the disk is read front to back
    for part in np.array_split(order, max(len(order) // 2_000_000, 1)):     # in pieces: a whole-USA day has 15 million samples
        e, n, u = np.asarray(enu[rows[part]]).T
        lat, lon, _ = enu_to_lla(e, n, u, lat0, lon0)
        keep[seg_of[part[(lat >= south) & (lat <= north) & (lon >= west) & (lon <= east)]]] = True
    kept = np.flatnonzero(keep)
    if not len(kept):
        raise NoFlightData(f"no propeller flights were recorded over that area on {src.stem.split('_')[0]}")

    new_off = np.r_[0, np.cumsum(off[kept + 1] - off[kept])].astype(off.dtype)
    cut = {name: np.empty((new_off[-1], *arr.shape[1:]), arr.dtype) for name, arr in (("t", t), ("enu", enu), ("spd", spd))}
    for k, i in enumerate(kept):
        for name, arr in (("t", t), ("enu", enu), ("spd", spd)):
            cut[name][new_off[k]:new_off[k + 1]] = arr[off[i]:off[i + 1]]
    new_number = np.cumsum(keep) - 1
    mine = keep[seg_of]                 # the samples of the flights kept, still in bucket order
    part = out.with_suffix(".part.npz")     # so an interrupted run never leaves a half-written index in the cache
    try:
        np.savez(part, lat0=lat0, lon0=lon0, bucket_s=float(z["bucket_s"]), off=new_off, **cut, icao=z["icao"][kept],
                 trace_idx=z["trace_idx"][kept], sb=np.asarray(sb)[mine], ss=new_number[seg_of[mine]].astype(ss.dtype),
                 sj=np.asarray(sj)[mine], parent_seg=kept.astype(np.int32))
        if flights_file(src).exists():
            known, here = json.loads(flights_file(src).read_text()), set(z["icao"][kept].tolist())
            flights_file(out).write_text(json.dumps({icao: about for icao, about in known.items() if icao in here}))
        part.rename(out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return out


def flights_file(index: Path) -> Path:
    return index.with_suffix(".flights.json")


def build_day_index(day: date, bbox, root: Path, progress=None) -> Path:
    """Stream the day, keep propeller aircraft that touch bbox, write the engine's day index.

    progress(bytes_done, bytes_total, traces_seen) is called as the stream advances.
    Beside the index goes a small JSON of registration and type per aircraft, for the report."""
    from dopplertrack.adsb import AircraftTypes
    from dopplertrack.fetch import iter_tar_traces
    from dopplertrack.search import DayIndex

    # asked before the 4 GB stream, not after it: a Mac pushed 14 GB past full freezes rather than fails
    whole_usa = tuple(bbox) == CONUS
    free = free_memory_gb() if whole_usa else None
    if free is not None and free < BUILD_PEAK_GB - SQUEEZE_GB:
        raise FlybyError(f"indexing a whole-USA day of flights takes about {BUILD_PEAK_GB} GB of memory for a minute or two, "
                         f"and only {free:.0f} GB is free right now. Close some apps and run the same command again, "
                         "or name a state with --area: that takes under 1 GB.")
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"{day}_{_tag(bbox)}.npz"
    stream = RangedStream(release_assets(day))
    types = AircraftTypes()
    hook = (lambda n, _: progress(stream.total, stream.size, n)) if progress else None
    # the name says which searches the index serves; a smaller box also holds the flights just outside it
    reach = bbox if whole_usa else _padded(bbox)
    try:
        traces = [t for t in iter_tar_traces(io.BufferedReader(stream, 1 << 22), reach, hook) if types.is_prop(t.typ)]
    finally:
        stream.close()
    if progress:
        progress(stream.size, stream.size, len(traces))
    if not traces:
        raise NoFlightData(f"no propeller flights were recorded over that area on {day}")
    idx = DayIndex.build(traces, None, None, bucket_s=10.0)
    flights_file(out).write_text(json.dumps({t.icao: [t.reg, t.typ, t.desc] for t in traces}))
    # a whole-USA day is about 14 GB in memory at this point: let go of everything the file does not need
    del traces
    idx.segs.clear()
    part = out.with_suffix(".part.npz")     # so an interrupted run never leaves a half-written index in the cache
    try:
        idx.save(part)
        part.rename(out)
    except BaseException:
        # a save cut short by Ctrl-C or a full disk: 6 GB that nothing else would ever remove
        part.unlink(missing_ok=True)
        flights_file(out).unlink(missing_ok=True)
        raise
    return out
