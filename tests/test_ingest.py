import json
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
    assert m.slug == "file-my-clip-1" and (tmp_path / "work" / m.slug).is_dir()
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
