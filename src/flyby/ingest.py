"""Turn a URL or local file into a Media: local audio plus what the source says about when and where."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .model import FlybyError, Media, plain

# platforms keep only the last ~12 h of a replay, so a replay near this length has probably lost its start
CUT_REPLAY_HOURS = 11.9
MIN_USEFUL_S = 60
AUDIO_FORMAT = "bestaudio[acodec=opus]/bestaudio/best"
STORYBOARD_MOST = 64        # of the site's strips of small frames, the finest that is at most this many pictures
THUMBNAIL_MIN_W = 640       # a smaller thumbnail is no use to a picture search

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
    proc = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True)
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


def _probe_file(path: Path, log) -> Media:
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

    about = {k: plain(str(tags[k])) for k in ("title", "description", "comment") if tags.get(k)}
    # The run folder keeps everything worked out for a recording. Named after the file alone, another
    # IMG_0001.mov, or the same file after an edit, would be given this one's sound and results.
    size = path.stat()
    mark = hashlib.sha1(f"{size.st_size}|{size.st_mtime_ns}".encode()).hexdigest()[:6]
    return Media(source=str(path), audio_path=path.resolve(), title=plain(path.stem),
                 slug=f"file-{slugify(path.stem, 48) or 'untitled'}-{mark}",
                 duration_s=duration_s, kind="file", started_utc=started, location_hint=hint, notes=notes,
                 about={"title": plain(path.stem), **about},
                 # the cover picture of a song is a "video stream" too, and is no view of where it was recorded
                 video={"has_video": any(s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
                                         for s in info.get("streams", []))})


def _existing_audio(run: Path) -> Path | None:
    # .part, .part-Frag3 and .ytdl files are leftovers of an interrupted download
    for p in sorted(run.glob("audio.*")):
        if ".part" not in p.name and ".ytdl" not in p.name:
            return p
    return None


def _video_facts(info: dict) -> dict:
    """What the picture tools need from the site's answer: its public pictures and its strip of small frames."""
    formats = info.get("formats") or []
    facts = {"site": str(info.get("extractor") or "").lower(), "id": str(info.get("id") or ""),
             "has_video": any(f.get("vcodec") not in (None, "none") for f in formats)}
    wide = sorted((t for t in info.get("thumbnails") or [] if t.get("url") and (t.get("width") or 0) >= THUMBNAIL_MIN_W),
                  key=lambda t: -t["width"])
    facts["thumbnails"] = list(dict.fromkeys(str(t["url"]) for t in wide))
    strips = [f for f in formats if f.get("format_note") == "storyboard" and f.get("fragments") and f.get("fps")
              and f.get("rows") and f.get("columns") and len(f["fragments"]) <= STORYBOARD_MOST]
    if strips:
        best = max(strips, key=lambda f: (f["fps"], f.get("width") or 0))
        facts["storyboard"] = {"urls": [str(part["url"]) for part in best["fragments"]], "rows": int(best["rows"]),
                               "columns": int(best["columns"]), "tile_w": int(best["width"]), "tile_h": int(best["height"]),
                               "seconds_per_tile": 1.0 / best["fps"]}
    return facts


def _probe_url(url: str, log) -> Media:
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

    # the publisher's own words: the clue reader looks in these for a place or a day
    about = {key: plain(str(info[field])) for key, field in (("title", "title"), ("description", "description"),
                                                      ("channel", "channel"), ("location", "location")) if info.get(field)}
    if info.get("tags"):
        about["tags"] = plain(", ".join(str(t) for t in info["tags"]))
    slug = slugify(f"{info.get('extractor') or info.get('extractor_key') or 'web'}-{info.get('id') or 'video'}")
    return Media(source=url, audio_path=None, title=plain(info.get("title") or slug), slug=slug, duration_s=duration_s,
                 kind=kind, uploaded=uploaded, started_utc=started, location_hint=hint, notes=notes, about=about,
                 video=_video_facts(info))


def probe(source: str, log=print) -> Media:
    """What the source says about itself. A link's audio is not downloaded yet: `fetch` does that,
    once it is known which part of a long video is wanted."""
    local = Path(source).expanduser()
    if local.is_file():
        return _probe_file(local, log)
    if re.match(r"^https?://\S+$", source):
        return _probe_url(source, log)
    raise FlybyError(f"'{source}' is neither an existing file nor an http(s) link.")


def _hm(seconds: float) -> str:
    return f"{int(seconds // 3600)}:{int(seconds % 3600 // 60):02d}"


def part_of(media: Media, section: tuple[float, float] | None) -> Media:
    """media narrowed to one part of the video (seconds from its start). A part is its own run,
    with its own folder, length and start time."""
    if section is None:
        return media
    first, last = section[0], min(section[1], media.duration_s)
    if last - first < MIN_USEFUL_S:
        raise FlybyError(f"The part {_hm(section[0])}-{_hm(section[1])} is under a minute of this video. Pick a longer part.")
    return replace(media, slug=f"{media.slug}-{int(first)}-{int(last)}", duration_s=last - first,
                   started_utc=media.started_utc + timedelta(seconds=first) if media.started_utc else None,
                   notes=[*media.notes, f"Only the part from {_hm(first)} to {_hm(last)} of the video is used."])


def fetch(media: Media, work_root: Path, log=print, section: tuple[float, float] | None = None) -> Media:
    """Get the audio onto disk: all of it, or only `section` (seconds from the start of the video)."""
    part = part_of(media, section)
    run = work_root / part.slug
    run.mkdir(parents=True, exist_ok=True)
    if section is None and media.audio_path is not None:
        return part

    audio = _existing_audio(run)
    if audio is None:
        source = media.audio_path
        if source is None:
            # A link's audio comes down whole, in many pieces at once, and a part is cut out of it
            # here. Asking the site for the part alone goes through ffmpeg on one connection, which
            # YouTube holds to playing speed: two hours of a replay would take two hours.
            home = work_root / media.slug
            home.mkdir(parents=True, exist_ok=True)
            source = _existing_audio(home)
            if source is None:
                log("Downloading the audio (this can take a while for a long video)...")
                _run(["yt-dlp", "--no-playlist", "--no-progress", "-f", AUDIO_FORMAT, "-N", "4",
                      "-o", str(home / "audio.%(ext)s"), "--", media.source], "yt-dlp download")
                source = _existing_audio(home)
                if source is None:
                    raise FlybyError("yt-dlp finished but saved no audio file.")
        if section is None:
            audio = source
        else:
            _require("ffmpeg")
            log("Cutting that part out of the audio...")
            first, last = section[0], min(section[1], media.duration_s)
            half = run / "audio.mka.part"       # under another name until it is whole
            _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{first:.3f}", "-to", f"{last:.3f}", "-i", str(source),
                  "-vn", "-c:a", "copy", "-f", "matroska", str(half)], "ffmpeg")
            audio = run / "audio.mka"
            half.replace(audio)
    return replace(part, audio_path=audio)


def ingest(source: str, work_root: Path, log=print, section: tuple[float, float] | None = None) -> Media:
    return fetch(probe(source, log), work_root, log, section)
