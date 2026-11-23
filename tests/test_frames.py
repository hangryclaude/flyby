import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from flyby import frames
from flyby.frames import grab, light, look, thumbnails
from flyby.model import FlybyError, Media


def video(tmp_path, graph, name="clip.mp4"):
    """A tiny silent video made by ffmpeg's own pattern makers, so the tests need no download."""
    out = tmp_path / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph, "-pix_fmt", "yuv420p", str(out)],
                   check=True, timeout=60)
    return out


def media_for(path, duration, **video):
    return Media(source=str(path), audio_path=Path(path), title="clip", slug="clip", duration_s=duration,
                 kind="file", video={"has_video": True, **video})


def test_look_on_solid_colours(tmp_path):
    b, c = look(Image.new("RGB", (8, 8), (255, 0, 0)))
    assert b == pytest.approx(0.299, abs=1e-3) and c == pytest.approx(1.0)
    b, c = look(Image.new("RGB", (8, 8), (128, 128, 128)))
    assert b == pytest.approx(128 / 255, abs=1e-3) and c == pytest.approx(0.0)
    path = tmp_path / "white.png"
    Image.new("RGB", (4, 4), (255, 255, 255)).save(path)
    assert look(path) == pytest.approx((1.0, 0.0), abs=1e-3)


def test_grab_spreads_pictures_through_the_video_in_time_order(tmp_path):
    clip = video(tmp_path, "testsrc2=s=320x180:r=5:d=60")
    got = grab(media_for(clip, 60.0), tmp_path / "f", count=3)
    assert len(got) == 3
    times = [f.t_s for f in got]
    assert times == sorted(times) and all(1 <= t <= 59 for t in times)
    for f in got:
        assert f.path.name == f"frame-{int(f.t_s):06d}.jpg" and f.path.is_file()
        assert f.sharpness > 0 and f.public_url is None
    assert not list((tmp_path / "f").glob("candidate-*"))
    assert json.loads((tmp_path / "f" / "frames.json").read_text())["count"] == 3


def test_black_pictures_and_copies_are_dropped(tmp_path):
    # 30 s black, then 10 s each of red, blue and green: twelve candidates, three different pictures worth keeping
    clip = video(tmp_path, "color=black:s=160x90:r=5:d=30[k];color=red:s=160x90:r=5:d=10[r];"
                           "color=blue:s=160x90:r=5:d=10[b];color=green:s=160x90:r=5:d=10[g];"
                           "[k][r][b][g]concat=n=4:v=1:a=0")
    got = grab(media_for(clip, 60.0), tmp_path / "f", count=3)
    assert [f.t_s > 30 for f in got] == [True, True, True]
    assert [f.t_s // 10 for f in got] == [3, 4, 5]


def test_a_night_video_still_gives_its_brightest_pictures(tmp_path):
    clip = video(tmp_path, "color=0x050505:s=160x90:r=5:d=30[a];color=0x0c0c0c:s=160x90:r=5:d=30[b];[a][b]concat=n=2:v=1:a=0")
    got = grab(media_for(clip, 60.0), tmp_path / "f", count=2)
    assert len(got) == 2 and all(f.t_s > 30 for f in got)


def test_the_second_call_fetches_nothing(tmp_path, monkeypatch):
    clip = video(tmp_path, "testsrc2=s=160x90:r=5:d=40")
    first = grab(media_for(clip, 40.0), tmp_path / "f", count=2)
    monkeypatch.setattr(frames.subprocess, "run", lambda *a, **k: pytest.fail("ran a program on the second call"))
    again = grab(media_for(clip, 40.0), tmp_path / "f", count=2)
    assert [(f.path, f.t_s) for f in again] == [(f.path, f.t_s) for f in first]


def test_no_picture_gives_nothing(tmp_path):
    silent = Media(source="x.m4a", audio_path=None, title="x", slug="x", duration_s=100.0, kind="file",
                   video={"has_video": False})
    assert grab(silent, tmp_path) == [] and light(silent, tmp_path) == [] and thumbnails(silent, tmp_path) == []


def test_a_link_reads_the_address_yt_dlp_gives(tmp_path, monkeypatch):
    clip = video(tmp_path, "testsrc2=s=160x90:r=5:d=40")
    asked = []

    def address(media):
        asked.append(media.source)
        return str(clip)

    monkeypatch.setattr(frames, "_address", address)
    link = Media(source="https://www.youtube.com/watch?v=abc", audio_path=None, title="t", slug="t",
                 duration_s=40.0, kind="live", video={"has_video": True, "site": "youtube", "id": "abc"})
    assert len(grab(link, tmp_path / "f", count=2)) == 2 and len(asked) == 1


def test_no_address_from_yt_dlp_is_a_plain_error(tmp_path, monkeypatch):
    monkeypatch.setattr(frames.shutil, "which", lambda tool: "/bin/" + tool)
    monkeypatch.setattr(frames.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="ERROR: no"))
    link = Media(source="https://www.youtube.com/watch?v=abc", audio_path=None, title="t", slug="t",
                 duration_s=40.0, kind="upload", video={"has_video": True})
    with pytest.raises(FlybyError, match="address"):
        grab(link, tmp_path / "f", count=2)


def strip(path, greys, rows=2, columns=2, tile=(4, 3)):
    w, h = tile
    image = Image.new("RGB", (columns * w, rows * h))
    for i, g in enumerate(greys):
        image.paste((g, g, g), ((i % columns) * w, (i // columns) * h, (i % columns + 1) * w, (i // columns + 1) * h))
    image.save(path)


def test_storyboard_tiles_are_cut_and_timed(tmp_path, monkeypatch):
    made = {"https://s/0.jpg": [0, 51, 102, 153], "https://s/1.jpg": [204, 255]}
    fetched = []

    def download(url, out):
        fetched.append(url)
        # the last strip holds one row only, as the site sends it
        strip(out, made[url], rows=1 if url.endswith("1.jpg") else 2)
        return True

    monkeypatch.setattr(frames, "_download", download)
    link = Media(source="https://www.youtube.com/watch?v=abc", audio_path=None, title="t", slug="t",
                 duration_s=55.0, kind="live",
                 video={"has_video": True, "storyboard": {"urls": list(made), "rows": 2, "columns": 2,
                                                          "tile_w": 4, "tile_h": 3, "seconds_per_tile": 10.0}})
    got = light(link, tmp_path)
    # tile 5 would show second 55, past the end of the video
    assert [t for t, _, _ in got] == [5.0, 15.0, 25.0, 35.0, 45.0]
    # JPEG may move a grey by a step
    assert [b * 255 for _, b, _ in got] == pytest.approx([0, 51, 102, 153, 204], abs=2)
    assert sorted(p.name for p in (tmp_path / "storyboard").iterdir()) == ["strip-000.jpg", "strip-001.jpg"]
    assert len(fetched) == 2
    assert light(link, tmp_path) == got and len(fetched) == 2


def test_a_strip_that_fails_to_download_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(frames, "_download", lambda url, out: url.endswith("1.jpg") and strip(out, [255] * 4) is None)
    link = Media(source="https://x", audio_path=None, title="t", slug="t", duration_s=80.0, kind="upload",
                 video={"has_video": True, "storyboard": {"urls": ["https://s/0.jpg", "https://s/1.jpg"], "rows": 2,
                                                          "columns": 2, "tile_w": 4, "tile_h": 3, "seconds_per_tile": 10.0}})
    assert [t for t, _, _ in light(link, tmp_path)] == [45.0, 55.0, 65.0, 75.0]


def test_light_of_a_file_follows_dark_then_bright(tmp_path):
    clip = video(tmp_path, "color=black:s=160x90:r=2:d=60[a];color=white:s=160x90:r=2:d=60[b];[a][b]concat=n=2:v=1:a=0")
    got = light(media_for(clip, 120.0), tmp_path / "f")
    assert [t for t, _, _ in got] == [15.0, 45.0, 75.0, 105.0]
    assert [round(b) for _, b, _ in got] == [0, 0, 1, 1]
    assert json.loads((tmp_path / "f" / "light.json").read_text()) == [list(s) for s in got]


def test_spread_covers_everything_once():
    for n in (1, 2, 5, 17, 1440):
        order = frames._spread(n)
        assert sorted(order) == list(range(n))
    assert frames._spread(8)[:2] == [4, 2]


def test_thumbnails_take_the_widest_and_the_site_picks(tmp_path, monkeypatch):
    def download(url, out):
        if url.endswith("maxres2.jpg"):
            return False
        if url.endswith("maxres3.jpg"):
            out.write_bytes(b"<html>not a picture</html>")
            return True
        Image.new("RGB", (64, 36), (10, 200, 10)).save(out, "JPEG")
        return True

    monkeypatch.setattr(frames, "_download", download)
    link = Media(source="https://www.youtube.com/watch?v=abc", audio_path=None, title="t", slug="t",
                 duration_s=40.0, kind="upload",
                 video={"has_video": True, "site": "youtube", "id": "abc",
                        "thumbnails": ["https://i.ytimg.com/vi/abc/maxresdefault.jpg", "https://small.jpg"]})
    got = thumbnails(link, tmp_path)
    assert [f.public_url for f in got] == ["https://i.ytimg.com/vi/abc/maxresdefault.jpg",
                                           "https://i.ytimg.com/vi/abc/maxres1.jpg"]
    assert all(f.t_s is None and f.path.name.startswith("thumb-") and f.colour > 0.5 for f in got)
    assert not (tmp_path / "thumb-3.jpg").exists()


def test_a_run_that_got_nothing_is_not_kept_as_the_answer(tmp_path, monkeypatch):
    clip = video(tmp_path, "testsrc2=s=320x180:r=5:d=20")
    m = media_for(clip, 20.0)
    monkeypatch.setattr(frames, "_take", lambda address, t_s, out: False)      # the site stalls
    assert grab(m, tmp_path / "f", count=2) == [] and not (tmp_path / "f" / "frames.json").exists()
    monkeypatch.undo()
    assert len(grab(m, tmp_path / "f", count=2)) == 2                          # the next run tries again


def test_a_record_cut_short_is_made_again(tmp_path):
    clip = video(tmp_path, "testsrc2=s=320x180:r=5:d=60")
    m = media_for(clip, 60.0)
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "frames.json").write_text('{"source":')
    (tmp_path / "f" / "light.json").write_text("[[1.0, 0.5")
    assert len(grab(m, tmp_path / "f", count=2)) == 2
    assert len(light(m, tmp_path / "f")) == 2
    assert len(json.loads((tmp_path / "f" / "light.json").read_text())) == 2


def test_one_broken_picture_does_not_lose_the_others(tmp_path, monkeypatch):
    clip = video(tmp_path, "testsrc2=s=320x180:r=5:d=40")
    real = frames._take

    def take(address, t_s, out):
        if t_s < 10:
            out.write_bytes(b"not a picture")       # ffmpeg cut off in the middle of its file
            return True
        return real(address, t_s, out)

    monkeypatch.setattr(frames, "_take", take)
    got = grab(media_for(clip, 40.0), tmp_path / "f", count=3)
    assert len(got) == 3 and all(f.t_s >= 10 for f in got)
    assert not list((tmp_path / "f").glob("candidate-*"))


def test_light_with_a_hole_in_it_is_measured_again_next_time(tmp_path, monkeypatch):
    calls = []

    def download(url, out):
        calls.append(url)
        if url.endswith("0.jpg") and len(calls) <= 2:
            return False                            # the site did not hand this strip over, the first time
        strip(out, [255] * 4)
        return True

    monkeypatch.setattr(frames, "_download", download)
    link = Media(source="https://x", audio_path=None, title="t", slug="t", duration_s=80.0, kind="upload",
                 video={"has_video": True, "storyboard": {"urls": ["https://s/0.jpg", "https://s/1.jpg"], "rows": 2,
                                                          "columns": 2, "tile_w": 4, "tile_h": 3, "seconds_per_tile": 10.0}})
    assert len(light(link, tmp_path)) == 4 and not (tmp_path / "light.json").exists()
    assert len(light(link, tmp_path)) == 8 and (tmp_path / "light.json").exists()
    assert len(calls) == 3                          # the strip that came the first time was not asked for again
