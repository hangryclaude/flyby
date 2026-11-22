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
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .model import CONUS, FlybyError, NoFlightData

API = "https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/v{tag}-planes-readsb-prod-0"
UA = {"User-Agent": "flyby/0.1 (personal research tool)"}
CHUNK = 8 << 20
CONNS = 8
BUILD_PEAK_GB = 14      # measured: a whole-USA day while the engine assembles its index
BUILD_SPARE_GB = 2


def free_memory_gb() -> float | None:
    """What macOS itself counts as available memory, the figure Activity Monitor's pressure graph follows."""
    if sys.platform != "darwin":
        return None

    def sysctl(name: str) -> int:
        return int(subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, check=True).stdout)

    return sysctl("hw.memsize") * sysctl("kern.memorystatus_level") / 100 / 2 ** 30


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


def index_box(index: Path) -> tuple[float, ...] | None:
    """The box an index file was built for, read back from its name."""
    try:
        box = tuple(float(x) for x in index.stem.split("_")[1:])
    except ValueError:
        return None
    return box if len(box) == 4 else None


def cached_index(day: date, bbox, root: Path) -> Path | None:
    """An index on disk that serves this day and box. A wider box will do: the search narrows it."""
    for p in sorted(root.glob(f"{day}_*.npz")):
        have = index_box(p)
        if have and not p.name.endswith(".part.npz") and _covers(have, bbox):
            return p
    return None


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
    free = free_memory_gb() if tuple(bbox) == CONUS else None
    if free is not None and free < BUILD_PEAK_GB + BUILD_SPARE_GB:
        raise FlybyError(f"indexing a whole-USA day of flights takes about {BUILD_PEAK_GB} GB of memory for a minute or two, "
                         f"and only {free:.0f} GB is free right now. Close some apps and run the same command again.")
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"{day}_{_tag(bbox)}.npz"
    stream = RangedStream(release_assets(day))
    types = AircraftTypes()
    hook = (lambda n, _: progress(stream.total, stream.size, n)) if progress else None
    try:
        traces = [t for t in iter_tar_traces(io.BufferedReader(stream, 1 << 22), bbox, hook) if types.is_prop(t.typ)]
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
