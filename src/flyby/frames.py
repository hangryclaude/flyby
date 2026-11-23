"""Still pictures from the video: a few good ones to read and search with, and how light it is through the whole of it.

The sound says which aircraft flew over; the picture can say what kind of place it is, the time
on a camera's clock, and whether it was day or night. A link's pictures are taken from the site
without downloading the video: ffmpeg asks for only the bytes around each second it wants.
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from .model import FlybyError, Frame, Media

USER_AGENT = "flyby/0.1 (personal research tool)"
VIDEO_FORMAT = "bv*[height<=1080][protocol=https][vcodec^=avc1]/bv*[height<=1080][protocol=https]"
LONG_SIDE = 1920
PICTURE_LIMIT_S = 45        # one picture from a link takes 5 to 6 s; far longer means the site has stalled
EDGE = 0.02                 # the first and last moments are often a title card, a fade or a black screen
DARK = 0.06                 # brightness below this is a picture with nothing to see
SAME = 0.03                 # two pictures this close (mean difference of small grey copies) show the same thing
WORKERS = 4                 # pictures fetched at once: the site answers each one slowly, not the Mac
LIGHT_STEP_S = 30.0
LIGHT_BUDGET_S = 60.0       # a 12 h file has 1,440 half-minutes; it gets as many as fit in this, spread evenly
LIGHT_W = 64                # a light sample is an average, so a tiny picture gives the same number


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise FlybyError(f"{tool} is not installed or not on PATH. flyby needs it to look at the video.")


def _rgb(image) -> np.ndarray:
    if not isinstance(image, Image.Image):
        image = Image.open(image)
    return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


def look(image) -> tuple[float, float]:
    """(brightness, colour) of a PIL image or a path, by the formulas on model.Frame."""
    rgb = _rgb(image)
    brightness = float((rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)).mean())
    colour = float((rgb.max(axis=2) - rgb.min(axis=2)).mean())
    return brightness, colour


def _sharpness(image: Image.Image) -> float:
    # every picture of one video is scored at the same size, so the numbers compare fairly
    grey = np.asarray(image.convert("L").resize((640, 360)), dtype=np.float32) / 255.0
    return float(np.abs(np.diff(grey, axis=0)).mean() + np.abs(np.diff(grey, axis=1)).mean())


def _small(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L").resize((32, 18)), dtype=np.float32) / 255.0


def _describe(path: Path, t_s: float | None, public_url: str | None = None) -> tuple[Frame, np.ndarray]:
    with Image.open(path) as image:
        brightness, colour = look(image)
        return Frame(path=path, t_s=t_s, brightness=brightness, colour=colour, sharpness=_sharpness(image),
                     public_url=public_url), _small(image)


def _fit(path: Path) -> None:
    with Image.open(path) as image:
        if max(image.size) <= LONG_SIDE:
            return
        image = image.convert("RGB")
    image.thumbnail((LONG_SIDE, LONG_SIDE))
    image.save(path, quality=92)


def _address(media: Media) -> str:
    """Where ffmpeg reads the picture from: the file itself, or the site's direct address for a link."""
    if media.kind == "file":
        return str(Path(media.source).expanduser().resolve())
    _require("yt-dlp")
    proc = subprocess.run(["yt-dlp", "-g", "--no-playlist", "-f", VIDEO_FORMAT, "--", media.source],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=PICTURE_LIMIT_S)
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("http")]
    if proc.returncode != 0 or not lines:
        raise FlybyError("The site would not give flyby an address for the video's pictures, so no pictures could be taken.")
    return lines[0]


def _take(address: str, t_s: float, out: Path) -> bool:
    try:
        # ffmpeg listens to the keyboard for "q" and sets the terminal raw to do it. Several at once leave
        # it raw, and then Enter no longer ends a line: the questions that follow could not be answered
        proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t_s:.3f}", "-i", address,
                               "-frames:v", "1", "-q:v", "2", str(out)],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=PICTURE_LIMIT_S)
    except subprocess.TimeoutExpired:
        # one stalled picture is not worth the rest: the others are spread across the same video
        return False
    return proc.returncode == 0 and out.is_file() and out.stat().st_size > 0


def _pick(scored: list[tuple[Frame, np.ndarray]], count: int) -> list[Frame]:
    """The best `count`: lit and different from each other first, sharp before blurred. Only when the
    video has too few of those are near-copies, then dark pictures (brightest first), let back in."""
    lit = sorted((s for s in scored if s[0].brightness >= DARK), key=lambda s: -s[0].sharpness)
    dark = sorted((s for s in scored if s[0].brightness < DARK), key=lambda s: -s[0].brightness)
    kept: list[tuple[Frame, np.ndarray]] = []
    for frame, small in lit:
        if len(kept) < count and all(np.abs(small - other).mean() >= SAME for _, other in kept):
            kept.append((frame, small))
    for pool in ([s for s in lit if all(s[0] is not k[0] for k in kept)], dark):
        for item in pool:
            if len(kept) < count:
                kept.append(item)
    return sorted((f for f, _ in kept), key=lambda f: f.t_s)


def _frame_json(frame: Frame) -> dict:
    return {"path": frame.path.name, "t_s": frame.t_s, "brightness": frame.brightness, "colour": frame.colour,
            "sharpness": frame.sharpness, "public_url": frame.public_url}


def _save(path: Path, text: str) -> None:
    """Written under another name and moved into place, so a run cut short never leaves half a record."""
    part = path.with_name(path.name + ".part")
    part.write_text(text)
    part.replace(path)


def _saved_frames(record: Path, folder: Path, media: Media, count: int) -> list[Frame]:
    """The pictures an earlier run kept. None when its record is missing, unreadable, empty or of another video:
    a run that got nothing must not stand as the answer for every run after it."""
    try:
        saved = json.loads(record.read_text())
        frames = [Frame(**{**f, "path": folder / f["path"]}) for f in saved["frames"]]
        if saved["source"] == media.source and saved["count"] == count and all(f.path.is_file() for f in frames):
            return frames
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return []


def grab(media: Media, folder: Path, count: int = 6, progress=lambda done, total: None) -> list[Frame]:
    """The best `count` pictures spread through the whole video, in time order."""
    if not media.video.get("has_video") or count <= 0:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / "frames.json"
    if frames := _saved_frames(record, folder, media, count):
        return frames

    _require("ffmpeg")
    duration = media.duration_s
    # whole seconds name the files, so two candidates in one second of a short clip would be one picture
    times = sorted({round(t) for t in np.linspace(duration * EDGE, duration * (1 - EDGE), 2 * count)})
    address = _address(media)
    done = 0
    progress(done, len(times))

    def one(t: int) -> tuple[Frame, np.ndarray] | None:
        out = folder / f"candidate-{t:06d}.jpg"
        if not _take(address, float(t), out):
            return None
        try:
            _fit(out)
            return _describe(out, float(t))
        except (OSError, ValueError):
            return None     # ffmpeg left something that is not a whole picture: the others still count

    scored = []
    frames = []
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            for result in pool.map(one, times):
                done += 1
                progress(done, len(times))
                if result is not None:
                    scored.append(result)
        for frame in _pick(scored, count):
            final = folder / f"frame-{int(frame.t_s):06d}.jpg"
            frame.path.replace(final)
            frame.path = final
            frames.append(frame)
    finally:
        for leftover in folder.glob("candidate-*.jpg"):
            leftover.unlink(missing_ok=True)
    if frames:
        _save(record, json.dumps({"source": media.source, "count": count,
                                  "frames": [_frame_json(f) for f in frames]}, indent=1))
    return frames


def _download(url: str, out: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
    except (OSError, http.client.HTTPException):
        # a missing picture (YouTube answers 404 for a maxres it never made) or no network: skip it
        return False
    out.write_bytes(body)
    return bool(body)


def thumbnails(media: Media, folder: Path) -> list[Frame]:
    """The site's own public pictures: anyone can open them, so a picture search can be pointed straight at them."""
    urls = list(media.video.get("thumbnails") or [])[:1]
    if media.video.get("site") == "youtube" and media.video.get("id"):
        urls += [f"https://i.ytimg.com/vi/{media.video['id']}/maxres{n}.jpg" for n in (1, 2, 3)]
    if not urls:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for n, url in enumerate(dict.fromkeys(urls)):
        out = folder / f"thumb-{n}.jpg"
        if not _download(url, out):
            continue
        try:
            frame, _ = _describe(out, None, public_url=url)
        except OSError:
            # the site answered with something that is not a picture
            out.unlink(missing_ok=True)
            continue
        frames.append(frame)
    return frames


def _storyboard_light(media: Media, folder: Path, progress) -> tuple[list[tuple[float, float, float]], bool]:
    """The samples, and whether every strip came: a run with a hole in it is used once, not kept."""
    board = media.video["storyboard"]
    rows, columns, tile_w, tile_h = board["rows"], board["columns"], board["tile_w"], board["tile_h"]
    step = float(board["seconds_per_tile"])
    strips = folder / "storyboard"
    strips.mkdir(parents=True, exist_ok=True)
    urls = board["urls"]
    done = 0
    progress(done, len(urls))

    def fetch(i: int) -> Path | None:
        out = strips / f"strip-{i:03d}.jpg"
        if out.is_file() and out.stat().st_size > 0:
            return out
        return out if _download(urls[i], out) else None

    samples = []
    whole = True
    with ThreadPoolExecutor(WORKERS) as pool:
        for i, path in enumerate(pool.map(fetch, range(len(urls)))):
            done += 1
            progress(done, len(urls))
            if path is None:
                whole = False
                continue
            with Image.open(path) as strip:
                strip = strip.convert("RGB")
            for r in range(rows):
                for c in range(columns):
                    t = (i * rows * columns + r * columns + c + 0.5) * step
                    # the last strip is only partly filled: past the end it is blank, or not there at all
                    if t >= media.duration_s or (r + 1) * tile_h > strip.height or (c + 1) * tile_w > strip.width:
                        continue
                    samples.append((t, *look(strip.crop((c * tile_w, r * tile_h, (c + 1) * tile_w, (r + 1) * tile_h)))))
    return sorted(samples), whole


def _spread(n: int) -> list[int]:
    """0..n-1 in an order where every prefix is spread evenly over the whole: stopping early still covers it all."""
    order, seen, parts = [], set(), 1
    while len(order) < n:
        for k in range(parts):
            i = min(n - 1, int((k + 0.5) * n / parts))
            if i not in seen:
                seen.add(i)
                order.append(i)
        parts *= 2
    return order


def _file_light(media: Media, progress) -> list[tuple[float, float, float]]:
    _require("ffmpeg")
    path = str(Path(media.source).expanduser().resolve())
    n = max(1, int(media.duration_s // LIGHT_STEP_S))
    times = [(i + 0.5) * media.duration_s / n for i in range(n)]
    deadline = time.monotonic() + LIGHT_BUDGET_S

    def one(t: float) -> tuple[float, float, float] | None:
        if time.monotonic() > deadline:
            return None
        try:
            proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                                   "-vf", f"scale={LIGHT_W}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=PICTURE_LIMIT_S)
        except subprocess.TimeoutExpired:
            return None     # one stalled sample is not worth the rest
        if proc.returncode != 0 or not proc.stdout or len(proc.stdout) % (LIGHT_W * 3):
            return None
        pixels = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(-1, LIGHT_W, 3)
        return (t, *look(Image.fromarray(pixels)))

    samples = []
    order = _spread(n)
    with ThreadPoolExecutor(WORKERS) as pool:
        for done, sample in enumerate(pool.map(one, (times[i] for i in order)), 1):
            progress(done, n)
            if sample is not None:
                samples.append(sample)
    return sorted(samples)


def light(media: Media, folder: Path, progress=lambda done, total: None) -> list[tuple[float, float, float]]:
    """(second into the video, brightness, colour) through the whole video, in time order:
    day and night, dawn and dusk, and when the camera went grey for the night."""
    cache = folder / "light.json"
    try:
        if saved := [(float(t), float(b), float(c)) for t, b, c in json.loads(cache.read_text())]:
            return saved
    except (OSError, ValueError, TypeError):
        pass        # no record, or one cut short: measure again
    whole = True
    if media.video.get("storyboard") and media.kind != "file":
        samples, whole = _storyboard_light(media, folder, progress)
    elif media.kind == "file" and media.video.get("has_video"):
        samples = _file_light(media, progress)
    else:
        return []
    if samples and whole:
        folder.mkdir(parents=True, exist_ok=True)
        _save(cache, json.dumps([list(s) for s in samples]))
    return samples
