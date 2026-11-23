import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from flyby import ingest as ingest_mod
from flyby.ingest import ingest, parse_coords, slugify
from flyby.model import FlybyError

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIX / name).read_text()


class FakeRun:
    """Stands in for subprocess.run: answers ffprobe and yt-dlp from canned output."""

    def __init__(self, probe=None, meta=None, download_ok=True):
        self.probe, self.meta, self.download_ok = probe, meta, download_ok
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        assert isinstance(args, list) and not kw.get("shell")
        self.calls.append(args)
        if args[0] == "ffprobe":
            return subprocess.CompletedProcess(args, 0, self.probe, "")
        if "-J" in args:
            return subprocess.CompletedProcess(args, 0, self.meta, "")
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"cut")
            return subprocess.CompletedProcess(args, 0, "", "")
        template = args[args.index("-o") + 1]
        if self.download_ok:
            Path(template.replace("%(ext)s", "opus")).write_bytes(b"x")
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture(autouse=True)
def tools_present(monkeypatch):
    monkeypatch.setattr(ingest_mod.shutil, "which", lambda t: f"/usr/bin/{t}")


def install(monkeypatch, **kw) -> FakeRun:
    fake = FakeRun(**kw)
    monkeypatch.setattr(ingest_mod.subprocess, "run", fake)
    return fake


def test_slugify_is_path_safe():
    assert slugify("../../Etc/Passwd  Hello!") == "etc-passwd-hello"
    assert len(slugify("a" * 200)) == 60


def test_parse_coords():
    assert parse_coords("+40.8765-073.5249+010.000/") == (40.8765, -73.5249)
    assert parse_coords("40.64, -73.78") == (40.64, -73.78)
    assert parse_coords("New York") is None
    assert parse_coords("+95.0+010.0/") is None


def test_local_file(tmp_path, monkeypatch):
    clip = tmp_path / "My Clip (1).mp4"
    clip.write_bytes(b"x")
    install(monkeypatch, probe=fixture("ffprobe_file.json"))
    m = ingest(str(clip), tmp_path / "work", log=lambda s: None)
    assert m.kind == "file" and m.audio_path == clip
    assert re.fullmatch(r"file-my-clip-1-[0-9a-f]{6}", m.slug) and (tmp_path / "work" / m.slug).is_dir()
    assert m.duration_s == 125.5 and m.title == "My Clip (1)"
    assert m.started_utc == datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
    assert m.location_hint == (40.8765, -73.5249)
    assert any("GPS" in n for n in m.notes)
    assert not any("too short" in n for n in m.notes)


def test_local_file_without_audio(tmp_path, monkeypatch):
    clip = tmp_path / "silent.mp4"
    clip.write_bytes(b"x")
    install(monkeypatch, probe=fixture("ffprobe_silent.json"))
    with pytest.raises(FlybyError, match="no audio"):
        ingest(str(clip), tmp_path / "work")


def test_short_recording_notes_not_raises(tmp_path, monkeypatch):
    clip = tmp_path / "s.mp4"
    clip.write_bytes(b"x")
    probe = json.loads(fixture("ffprobe_file.json"))
    probe["format"]["duration"] = "20"
    install(monkeypatch, probe=json.dumps(probe))
    m = ingest(str(clip), tmp_path / "work", log=lambda s: None)
    assert any("too short" in n for n in m.notes)


def test_upload_url(tmp_path, monkeypatch):
    fake = install(monkeypatch, meta=fixture("ytdlp_upload.json"))
    logs: list[str] = []
    url = "https://www.youtube.com/watch?v=AbC_123"
    m = ingest(url, tmp_path, log=logs.append)
    assert m.kind == "upload" and m.slug == "youtube-abc-123"
    assert m.uploaded == date(2024, 6, 1) and m.started_utc is None
    assert m.location_hint is None and m.duration_s == 300
    assert m.audio_path == tmp_path / m.slug / "audio.opus"
    assert logs
    for call in fake.calls:
        assert call[-2:] == ["--", url]
    dl = fake.calls[-1]
    assert "--no-playlist" in dl and "--no-progress" in dl
    assert dl[dl.index("-f") + 1] == "bestaudio[acodec=opus]/bestaudio/best"


def test_audio_is_reused(tmp_path, monkeypatch):
    fake = install(monkeypatch, meta=fixture("ytdlp_upload.json"))
    run = tmp_path / "youtube-abc-123"
    run.mkdir()
    (run / "audio.webm").write_bytes(b"x")
    m = ingest("https://youtu.be/AbC_123", tmp_path, log=lambda s: None)
    assert m.audio_path == run / "audio.webm"
    assert len(fake.calls) == 1  # metadata only


def test_live_replay(tmp_path, monkeypatch):
    install(monkeypatch, meta=fixture("ytdlp_live.json"))
    m = ingest("https://www.youtube.com/watch?v=XyZ789", tmp_path, log=lambda s: None)
    assert m.kind == "live"
    assert m.started_utc == datetime.fromtimestamp(1717250400, tz=timezone.utc)
    assert m.location_hint == (40.64, -73.78)
    assert any("12 hours" in n for n in m.notes)


@pytest.mark.parametrize("status", ["is_live", "is_upcoming"])
def test_unfinished_stream_refused(tmp_path, monkeypatch, status):
    meta = json.loads(fixture("ytdlp_live.json"))
    meta["live_status"] = status
    install(monkeypatch, meta=json.dumps(meta))
    with pytest.raises(FlybyError, match="finished recording"):
        ingest("https://example.com/v", tmp_path)


def test_bad_source(tmp_path):
    with pytest.raises(FlybyError):
        ingest("ftp://example.com/x", tmp_path)
    with pytest.raises(FlybyError):
        ingest(str(tmp_path / "missing.mp4"), tmp_path)


@pytest.mark.parametrize("tool,src", [("yt-dlp", "https://example.com/v"), ("ffprobe", "FILE")])
def test_missing_tool_named(tmp_path, monkeypatch, tool, src):
    monkeypatch.setattr(ingest_mod.shutil, "which", lambda t: None if t == tool else "/x")
    if src == "FILE":
        src = tmp_path / "a.mp4"
        src.write_bytes(b"x")
    with pytest.raises(FlybyError, match=tool):
        ingest(str(src), tmp_path / "work")


def _probe(fmt):
    return json.dumps({"streams": [{"codec_type": "audio"}], "format": fmt})


@pytest.mark.parametrize("fmt", [{}, {"duration": "N/A"}])
def test_missing_duration_is_flyby_error(tmp_path, monkeypatch, fmt):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    install(monkeypatch, probe=_probe(fmt))
    with pytest.raises(FlybyError, match="how long"):
        ingest(str(clip), tmp_path / "work", log=lambda s: None)


def test_bad_creation_time_ignored_with_note(tmp_path, monkeypatch):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    install(monkeypatch, probe=_probe({"duration": "120", "tags": {"creation_time": "yesterday"}}))
    m = ingest(str(clip), tmp_path / "work", log=lambda s: None)
    assert m.started_utc is None and any("recording time" in n for n in m.notes)


def test_leftover_download_fragments_are_not_taken_for_the_audio(tmp_path):
    from flyby.ingest import _existing_audio
    for name in ("audio.webm.part", "audio.webm.part-Frag3", "audio.webm.ytdl"):
        (tmp_path / name).write_bytes(b"x")
    assert _existing_audio(tmp_path) is None
    (tmp_path / "audio.webm").write_bytes(b"x")
    assert _existing_audio(tmp_path) == tmp_path / "audio.webm"


def test_a_part_of_a_link_is_cut_here_from_the_whole_audio(tmp_path, monkeypatch):
    fake = install(monkeypatch, meta=fixture("ytdlp_upload.json"))
    media = ingest_mod.probe("https://www.youtube.com/watch?v=AbC_123", log=lambda s: None)
    part = ingest_mod.fetch(media, tmp_path, log=lambda s: None, section=(60.0, 240.0))
    assert part.slug == "youtube-abc-123-60-240" and part.duration_s == 180
    assert part.audio_path == tmp_path / part.slug / "audio.mka" and part.audio_path.read_bytes() == b"cut"
    assert any("1:00" in n or "0:01" in n for n in part.notes)
    # the site is asked for all of it: asked for a part, it sends that part at playing speed
    download = next(c for c in fake.calls if c[0] == "yt-dlp" and "-J" not in c)
    assert "--download-sections" not in download and (tmp_path / "youtube-abc-123" / "audio.opus").exists()
    cut = fake.calls[-1]
    assert cut[0] == "ffmpeg" and cut[cut.index("-ss") + 1] == "60.000" and cut[cut.index("-to") + 1] == "240.000"
    assert cut[cut.index("-i") + 1] == str(tmp_path / "youtube-abc-123" / "audio.opus")
    # a second part of the same video is cut from the audio already here
    before = len(fake.calls)
    ingest_mod.fetch(media, tmp_path, log=lambda s: None, section=(120.0, 300.0))
    assert [c[0] for c in fake.calls[before:]] == ["ffmpeg"]
    # and the same part again needs nothing at all
    ingest_mod.fetch(media, tmp_path, log=lambda s: None, section=(60.0, 240.0))
    assert len(fake.calls) == before + 1


def test_a_part_of_a_local_file_is_cut_from_the_file(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    fake = install(monkeypatch, probe=fixture("ffprobe_file.json"))
    media = ingest_mod.probe(str(clip), log=lambda s: None)
    part = ingest_mod.fetch(media, tmp_path / "work", log=lambda s: None, section=(30.0, 120.0))
    assert part.audio_path == tmp_path / "work" / f"{media.slug}-30-120" / "audio.mka" and part.duration_s == 90
    # the file states when it began, so the part began that much later
    assert part.started_utc == datetime(2024, 6, 1, 14, 30, 30, tzinfo=timezone.utc)
    assert [c[0] for c in fake.calls] == ["ffprobe", "ffmpeg"] and str(clip) in fake.calls[-1]


def test_a_part_under_a_minute_is_refused_before_any_download(tmp_path, monkeypatch):
    fake = install(monkeypatch, meta=fixture("ytdlp_upload.json"))
    media = ingest_mod.probe("https://youtu.be/AbC_123", log=lambda s: None)
    with pytest.raises(FlybyError, match="under a minute"):
        ingest_mod.part_of(media, (280.0, 400.0))      # the video is 300 s long, so this is its last 20 s
    assert len(fake.calls) == 1 and ingest_mod.part_of(media, None) is media


def test_two_files_with_one_name_do_not_share_saved_work(tmp_path, monkeypatch):
    # camera files are all called IMG_0001.mov; the run folder holds the sound picture and every result
    install(monkeypatch, probe=fixture("ffprobe_file.json"))
    slugs = []
    for folder, body in (("a", b"one"), ("b", b"another")):
        (tmp_path / folder).mkdir()
        clip = tmp_path / folder / "IMG_0001.mov"
        clip.write_bytes(body)
        slugs.append(ingest_mod.probe(str(clip), log=lambda s: None).slug)
    assert slugs[0] != slugs[1] and all(s.startswith("file-img-0001-") for s in slugs)
    # the same file read again is the same recording, and an edit makes it a new one
    clip = tmp_path / "a" / "IMG_0001.mov"
    assert ingest_mod.probe(str(clip), log=lambda s: None).slug == slugs[0]
    os.utime(clip, ns=(1, 1))
    assert ingest_mod.probe(str(clip), log=lambda s: None).slug != slugs[0]


def test_a_link_keeps_what_the_picture_tools_need():
    info = {"extractor": "youtube", "id": "abc", "formats": [
        {"format_id": "sb3", "format_note": "storyboard", "vcodec": "none", "fps": 0.002, "rows": 10, "columns": 10,
         "width": 48, "height": 27, "fragments": [{"url": "https://i/L0.jpg", "duration": 42900.0}]},
        {"format_id": "sb2", "format_note": "storyboard", "vcodec": "none", "fps": 0.1, "rows": 10, "columns": 10,
         "width": 80, "height": 45, "fragments": [{"url": f"https://i/M{n}.jpg", "duration": 1000.0} for n in range(43)]},
        # finer, but 477 pictures to fetch: too many
        {"format_id": "sb0", "format_note": "storyboard", "vcodec": "none", "fps": 0.1, "rows": 3, "columns": 3,
         "width": 320, "height": 180, "fragments": [{"url": f"https://i/F{n}.jpg", "duration": 90.0} for n in range(477)]},
        {"format_id": "137", "vcodec": "avc1.640028", "height": 1080},
        {"format_id": "251", "vcodec": "none", "acodec": "opus"}],
        "thumbnails": [{"url": "https://i/small.jpg", "width": 168}, {"url": "https://i/hq720.jpg?v=1", "width": 1280},
                       {"url": "https://i/maxres.jpg", "width": 1920}, {"url": "https://i/nosize.jpg"}]}
    facts = ingest_mod._video_facts(info)
    assert facts["site"] == "youtube" and facts["id"] == "abc" and facts["has_video"] is True
    assert facts["thumbnails"] == ["https://i/maxres.jpg", "https://i/hq720.jpg?v=1"]
    board = facts["storyboard"]
    assert len(board["urls"]) == 43 and board["urls"][0] == "https://i/M0.jpg"
    assert (board["rows"], board["columns"], board["tile_w"], board["tile_h"]) == (10, 10, 80, 45)
    assert board["seconds_per_tile"] == 10.0


def test_a_link_with_sound_only_has_no_picture():
    facts = ingest_mod._video_facts({"extractor": "soundcloud", "id": "1", "formats": [{"format_id": "a", "vcodec": "none"}]})
    assert facts["has_video"] is False and facts["thumbnails"] == [] and "storyboard" not in facts


def test_control_characters_in_a_title_do_not_reach_the_terminal(tmp_path, monkeypatch):
    meta = json.loads(fixture("ytdlp_upload.json"))
    meta["title"] = "Plane \u001b[2Jspotting\u0007 in \u009bOhio"
    meta["description"] = "line one\nline two \u001b]0;x\u0007"
    install(monkeypatch, meta=json.dumps(meta))
    m = ingest_mod.probe("https://www.youtube.com/watch?v=AbC_123", log=lambda text: None)
    assert m.title == "Plane [2Jspotting in Ohio" and m.about["title"] == m.title
    assert m.about["description"] == "line one\nline two ]0;x"
