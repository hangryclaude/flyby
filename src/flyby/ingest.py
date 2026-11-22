"""Turn a URL or local file into a Media: local audio plus what the source says about when and where."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from .model import FlybyError, Media

# platforms keep only the last ~12 h of a replay, so a replay near this length has probably lost its start
CUT_REPLAY_HOURS = 11.9
MIN_USEFUL_S = 60
AUDIO_FORMAT = "bestaudio[acodec=opus]/bestaudio/best"

_ISO6709 = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?/?$")
_DECIMAL_PAIR = re.compile(r"^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$")


def slugify(text: str, limit: int = 60) -> str:
    """Lowercase [a-z0-9-] only: titles and ids come from the internet and end up in paths."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].strip("-")


def parse_coords(text: str) -> tuple[float, float] | None:
    """ISO 6709 ("+40.8765-073.5249+010.000/") or "lat, lon"; None when it is neither or out of range."""
    text = text.strip()
    m = _ISO6709.match(text) or _DECIMAL_PAIR.match(text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return lat, lon


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise FlybyError(f"{tool} is not installed or not on PATH. flyby needs it to read the video.")


def _run(args: list[str], what: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        lines = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
        raise FlybyError(f"{what} failed: {lines[-1] if lines else 'no details given'}")
    return proc


def _utc(moment: datetime) -> datetime:
    # a tag with no zone is taken as UTC: that is what ffmpeg writes for creation_time
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _finish_notes(notes: list[str], duration_s: float) -> None:
    if duration_s < MIN_USEFUL_S:
        notes.append("This recording is under a minute long, so it is very likely too short to locate.")


def _ingest_file(path: Path, work_root: Path, log) -> Media:
    _require("ffprobe")
    log("Reading the file's audio and tags...")
    # resolved, so a file name that starts with a dash cannot be read as an ffprobe option
    proc = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path.resolve())],
                "ffprobe")
    info = json.loads(proc.stdout)
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise FlybyError(f"{path.name} has no audio track, so there is nothing to listen to.")

    fmt = info.get("format", {})
    try:
        duration_s = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        raise FlybyError(f"{path.name} does not say how long it is, so flyby cannot use it. "
                         "Re-save it as .mp4 or .m4a and try again.") from None
    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}

    notes: list[str] = []
    started = None
    if tags.get("creation_time"):
        try:
            started = _utc(datetime.fromisoformat(tags["creation_time"]))
        except ValueError:
            notes.append("The file's recording time could not be read, so it was ignored.")
    hint = None
    for key in ("location", "com.apple.quicktime.location.iso6709"):
        if tags.get(key):
            hint = parse_coords(tags[key])
            if hint:
                break
    if hint:
        notes.append("The file itself already carries GPS coordinates.")
    _finish_notes(notes, duration_s)

    slug = "file-" + (slugify(path.stem, 55) or "untitled")
    (work_root / slug).mkdir(parents=True, exist_ok=True)
    return Media(source=str(path), audio_path=path.resolve(), title=path.stem, slug=slug, duration_s=duration_s,
                 kind="file", started_utc=started, location_hint=hint, notes=notes)


def _existing_audio(run: Path) -> Path | None:
    # .part, .part-Frag3 and .ytdl files are leftovers of an interrupted download
    for p in sorted(run.glob("audio.*")):
        if ".part" not in p.name and ".ytdl" not in p.name:
            return p
    return None


def _ingest_url(url: str, work_root: Path, log) -> Media:
    _require("yt-dlp")
    log("Asking the site about this video...")
    info = json.loads(_run(["yt-dlp", "-J", "--no-playlist", "--", url], "yt-dlp").stdout)

    status = info.get("live_status")
    if status in ("is_live", "is_upcoming"):
        raise FlybyError("This stream has not finished. flyby needs a finished recording: try again once it has ended.")

    kind = "live" if status in ("was_live", "post_live") or info.get("was_live") else "upload"
    if info.get("duration") is None:
        raise FlybyError("The site did not say how long this video is, so flyby cannot use it.")
    duration_s = float(info["duration"])

    uploaded = None
    if info.get("upload_date"):
        uploaded = datetime.strptime(info["upload_date"], "%Y%m%d").date()

    notes: list[str] = []
    started = None
    if kind == "live" and info.get("release_timestamp"):
        started = datetime.fromtimestamp(info["release_timestamp"], tz=timezone.utc)
        if duration_s / 3600 >= CUT_REPLAY_HOURS:
            notes.append("Platforms cut stream replays to the last 12 hours, so the start time may be wrong.")

    hint = parse_coords(info["location"]) if isinstance(info.get("location"), str) else None
    _finish_notes(notes, duration_s)

    slug = slugify(f"{info.get('extractor') or info.get('extractor_key') or 'web'}-{info.get('id') or 'video'}")
    run = work_root / slug
    run.mkdir(parents=True, exist_ok=True)

    audio = _existing_audio(run)
    if audio is None:
        log("Downloading the audio (this can take a while for a long video)...")
        _run(["yt-dlp", "--no-playlist", "--no-progress", "-f", AUDIO_FORMAT,
              "-o", str(run / "audio.%(ext)s"), "--", url], "yt-dlp download")
        audio = _existing_audio(run)
        if audio is None:
            raise FlybyError("yt-dlp finished but saved no audio file.")

    return Media(source=url, audio_path=audio, title=info.get("title") or slug, slug=slug, duration_s=duration_s,
                 kind=kind, uploaded=uploaded, started_utc=started, location_hint=hint, notes=notes)


def ingest(source: str, work_root: Path, log=print) -> Media:
    local = Path(source).expanduser()
    if local.is_file():
        return _ingest_file(local, work_root, log)
    if re.match(r"^https?://\S+$", source):
        return _ingest_url(source, work_root, log)
    raise FlybyError(f"'{source}' is neither an existing file nor an http(s) link.")
