import http.client
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from flyby import pictures
from flyby.model import Area, FlybyError, Frame, Hint, Media, TextLine
from flyby.pictures import Seen, cross_checks, merge, narrowest, see

UTC = timezone.utc
EASTERN = (24.0, 50.0, -90.5, -66.0)


def media(**kw) -> Media:
    base = dict(source="https://x/v", audio_path=None, title="t", slug="t", duration_s=3600.0, kind="live",
                video={"has_video": True})
    base.update(kw)
    return Media(**base)


class Ui:
    """Records what the person would have seen."""

    def __init__(self):
        self.said = []

    def step(self, key, **words):
        self.said.append(("step", key))

    def progress(self, done, total, detail=""):
        pass

    def done(self, text):
        self.said.append(("done", text))

    def note(self, text):
        self.said.append(("note", text))


@dataclass
class Zone:
    name: str
    utc_offset_h: float
    bbox: tuple
    also: str = ""


@dataclass
class Reading:
    lines: list = field(default_factory=list)
    hints: list = field(default_factory=list)
    clock: object = None
    zone: object = None
    clock_gap_s: float | None = None


@dataclass
class Strip:
    bbox: tuple
    share: float
    why: str


def staged(monkeypatch, tmp_path, frames=2, thumbs=1, light=3, reading=None, changes=(), strip=None, fail=None):
    """Every picture tool, faked. `fail` maps a tool's name to the error it raises."""
    fail = fail or {}

    def tool(name, result):
        def call(*a, **kw):
            if name in fail:
                raise fail[name]
            return result
        return call

    shots = [Frame(tmp_path / f"frame-{n}.jpg", 600.0 * (n + 1), 0.5, 0.2, 1.0) for n in range(frames)]
    own = [Frame(tmp_path / f"thumb-{n}.jpg", None, 0.4, 0.2, 1.0, f"https://i/{n}.jpg") for n in range(thumbs)]
    lines = [TextLine("Main Street Railfan - Huron OHIO", 1.0, 0.03, 0.92, 0.4, 0.04, 600.0)]
    monkeypatch.setattr(pictures.frames_mod, "grab", tool("grab", shots))
    monkeypatch.setattr(pictures.frames_mod, "thumbnails", tool("thumbnails", own))
    monkeypatch.setattr(pictures.frames_mod, "light", tool("light", [(10.0 * i, 0.5, 0.2) for i in range(light)]))
    monkeypatch.setattr(pictures.readtext, "read", tool("read", lines))
    monkeypatch.setattr(pictures.readtext, "understand", tool("understand", reading or Reading(lines=lines)))
    monkeypatch.setattr(pictures.sun, "changes", tool("changes", list(changes)))
    monkeypatch.setattr(pictures.sun, "strip", tool("strip", strip))
    monkeypatch.setattr(pictures.lookup, "write_page", tool("write_page", tmp_path / "flyby-t-pictures.html"))
    return shots, own


def look(tmp_path, m=None):
    ui = Ui()
    return see(m or media(), tmp_path / "pictures", tmp_path / "bin", tmp_path / "pages", ui), ui


def test_a_recording_with_no_picture_is_left_alone(monkeypatch, tmp_path):
    staged(monkeypatch, tmp_path)
    seen, ui = look(tmp_path, media(video={"has_video": False}))
    assert seen == Seen() and ui.said == []


def test_the_steps_run_in_order_and_everything_is_kept(monkeypatch, tmp_path):
    caption = Hint("place", "Huron, Ohio", 'a caption on the picture says "Main Street Railfan - Huron OHIO"', "likely", ("Huron",))
    reading = Reading(hints=[caption], zone=Zone("Eastern", -4.0, EASTERN), clock_gap_s=-19.0)
    shots, own = staged(monkeypatch, tmp_path, reading=reading)
    seen, ui = look(tmp_path, media(started_utc=datetime(2026, 9, 9, 11, 45, 32, tzinfo=UTC)))
    assert [key for kind, key in ui.said if kind == "step"] == ["frames", "light", "read", "sun", "pictures_page"]
    assert seen.frames == shots and seen.thumbs == own and len(seen.light) == 3 and seen.hints == [caption]
    assert seen.page == tmp_path / "flyby-t-pictures.html" and seen.clock_gap_s == -19.0
    assert seen.areas == [Area(EASTERN, "the Eastern time zone",
                               "The clock in the picture runs 4 hours behind UTC. That is Eastern time.", sure=True)]
    assert seen.notes == ["The camera's own clock runs 19 s behind the stream's clock."]
    notes = [text for kind, text in ui.said if kind == "note"]
    assert notes[:3] == ['A caption on the picture says "Main Street Railfan - Huron OHIO".',
                         "The clock in the picture runs 4 hours behind UTC. That is Eastern time.",
                         "The camera's own clock runs 19 s behind the stream's clock."]
    assert ("done", "2 pictures taken from the video") in ui.said and ("done", "1 line of writing read off the pictures") in ui.said


def test_a_small_clock_gap_is_kept_but_not_talked_about(monkeypatch, tmp_path):
    staged(monkeypatch, tmp_path, reading=Reading(clock_gap_s=2.0))
    seen, _ = look(tmp_path)
    assert seen.clock_gap_s == 2.0 and seen.notes == []


def test_the_sun_step_needs_the_clock_and_adds_a_soft_area(monkeypatch, tmp_path):
    strip = Strip((24.0, 50.0, -100.0, -80.0), 0.3, "The picture goes dark at 00:41 UTC. The sun was setting along a strip of the map then.")
    staged(monkeypatch, tmp_path, changes=["dusk"], strip=strip)
    seen, ui = look(tmp_path)                       # an upload: no clock, so the light cannot be placed on the map
    assert ("step", "sun") not in ui.said and seen.areas == []
    seen, ui = look(tmp_path, media(started_utc=datetime(2026, 9, 9, 11, 45, tzinfo=UTC)))
    assert seen.areas == [Area(strip.bbox, "the strip where the sun was that low", strip.why, sure=False)]
    assert ("done", "The sun's light narrows the map") in ui.said and ("note", strip.why) in ui.said


def test_a_tool_that_lets_the_run_down_becomes_a_note_and_the_rest_goes_on(monkeypatch, tmp_path):
    staged(monkeypatch, tmp_path, fail={"grab": FlybyError("The site gave no address for the video's picture."),
                                        "light": http.client.IncompleteRead(b""),
                                        "read": FlybyError("The text reader could not be built on this Mac.")})
    seen, ui = look(tmp_path)
    assert seen.frames == [] and len(seen.thumbs) == 1 and seen.light == [] and seen.lines == []
    assert seen.notes == ["No pictures were taken from the video. The site gave no address for the video's picture.",
                          "The light through the video was not measured. The network or a tool let it down (IncompleteRead).",
                          "The writing in the pictures was not read. The text reader could not be built on this Mac."]
    assert ("done", "No pictures could be taken from the video") in ui.said
    # the site's own picture is still worth a search page
    assert [key for kind, key in ui.said if kind == "step"] == ["frames", "light", "read", "pictures_page"]
    assert seen.page is not None


def test_with_no_picture_of_any_kind_there_is_nothing_to_read_and_no_page(monkeypatch, tmp_path):
    staged(monkeypatch, tmp_path, frames=0, thumbs=0)
    seen, ui = look(tmp_path)
    assert seen.page is None and [key for kind, key in ui.said if kind == "step"] == ["frames", "light"]


def test_a_reader_that_cannot_be_built_still_leaves_the_page(monkeypatch, tmp_path):
    staged(monkeypatch, tmp_path, fail={"read": FlybyError("The text reader could not be built on this Mac.")})
    seen, ui = look(tmp_path)
    assert seen.lines == [] and seen.page is not None
    assert seen.notes == ["The writing in the pictures was not read. The text reader could not be built on this Mac."]
    assert ("done", "No writing found in the pictures") in ui.said


def test_merge_keeps_the_strongest_first_and_one_of_each():
    title = Hint("place", "Huron, Ohio", "the title says so", "stated")
    caption = Hint("place", "huron, ohio", "a caption says so", "likely")
    sign = Hint("place", "Michigan", "a sign says so", "weak")
    day = Hint("day", "2026-09-09", "the clock in the picture says so", "likely")
    assert merge([sign, title], [caption, day]) == [title, day, sign]
    assert merge([], []) == []


def test_narrowest_cuts_the_areas_together():
    zone = Area(EASTERN, "the Eastern time zone", "Clock.", sure=True)
    strip = Area((24.0, 50.0, -100.0, -80.0), "the strip where the sun was that low", "Sun.", sure=False)
    both = narrowest([strip, zone])
    assert both.bbox == (24.0, 50.0, -90.5, -80.0) and both.sure is False
    assert both.label == "the Eastern time zone and the strip where the sun was that low" and both.why == "Clock. Sun."
    assert narrowest([strip, zone], sure_only=True) == zone       # with nobody to ask, the light is not trusted
    assert narrowest([strip], sure_only=True) is None and narrowest([]) is None
    far = Area((24.0, 50.0, -125.0, -114.0), "the Pacific strip", "Sun.", sure=False)
    assert narrowest([far, zone]) == zone                          # no overlap: the surer one stays


def test_cross_checks_after_an_answer(monkeypatch):
    monkeypatch.setattr(pictures.sun, "daylight", lambda lat, lon, light, start: ["the sun was 31 degrees up. That fits."])
    zone = Area(EASTERN, "the Eastern time zone", "Clock.", sure=True)
    strip = Area((24.0, 50.0, -100.0, -80.0), "a strip", "Sun.", sure=False)
    seen = Seen(areas=[zone, strip], light=[(0.0, 0.5, 0.2)])
    start = datetime(2026, 9, 9, 11, 45, tzinfo=UTC)
    assert cross_checks(seen, 41.39, -82.55, start) == [
        "Clock check: the answer lies in the Eastern time zone, as the camera's clock says.",
        "Sun check: the sun was 31 degrees up. That fits."]
    assert cross_checks(seen, 34.0, -118.2, None) == [
        "Clock check: the answer lies outside the Eastern time zone, where the camera's clock puts it. One of the two is wrong."]
    assert cross_checks(Seen(), 41.39, -82.55, start) == []


def test_a_replay_long_enough_to_have_lost_its_start_makes_the_time_zone_a_soft_clue(monkeypatch, tmp_path):
    # a 12 h replay may be the tail of a longer stream: cut by one hour, its clock would point one zone too far east
    staged(monkeypatch, tmp_path, reading=Reading(zone=Zone("Central", -5.0, (25.0, 49.5, -104.5, -84.0))))
    seen, _ = look(tmp_path, media(duration_s=12 * 3600.0, started_utc=datetime(2026, 9, 9, 11, 45, tzinfo=UTC)))
    assert [(a.label, a.sure) for a in seen.areas] == [("the Central time zone", False)]
    assert seen.areas[0].why.endswith("That holds only if the replay did not lose its first hours.")
    assert narrowest(seen.areas, sure_only=True) is None


BLACK_LIGHT = [(10.0 * i, 0.016, 0.002) for i in range(40)]


def black_frames(tmp_path, n=3):
    return [Frame(tmp_path / f"frame-{k}.jpg", 600.0 * (k + 1), 0.015, 0.003, 0.002) for k in range(n)]


def test_a_black_slide_over_the_sound_gives_no_pictures_no_light_and_no_page(monkeypatch, tmp_path):
    # the challenge clip itself is posted this way: an hour of sound under a black picture
    staged(monkeypatch, tmp_path, thumbs=0, changes=["dusk"], strip=Strip((24.0, 50.0, -100.0, -80.0), 0.3, "Sun."))
    monkeypatch.setattr(pictures.frames_mod, "grab", lambda *a, **kw: black_frames(tmp_path))
    monkeypatch.setattr(pictures.frames_mod, "light", lambda *a, **kw: list(BLACK_LIGHT))
    monkeypatch.setattr(pictures.readtext, "read", lambda *a, **kw: [])
    seen, ui = look(tmp_path, media(started_utc=datetime(2026, 9, 8, 20, 17, tzinfo=UTC)))
    assert seen.frames == [] and seen.light == [] and seen.areas == [] and seen.page is None
    assert seen.notes == ["The video's picture is black from start to end, so there is nothing in it to use."]
    assert [key for kind, key in ui.said if kind == "step"] == ["frames", "light", "read"]
    assert ("note", seen.notes[0]) in ui.said


def test_a_black_picture_with_writing_in_it_is_kept_but_its_light_is_not_used(monkeypatch, tmp_path):
    staged(monkeypatch, tmp_path, thumbs=0)
    dark = black_frames(tmp_path)
    caption = TextLine("Main Street Railfan - Huron OHIO", 1.0, 0.03, 0.92, 0.4, 0.04, 600.0, dark[0].path)
    monkeypatch.setattr(pictures.frames_mod, "grab", lambda *a, **kw: dark)
    monkeypatch.setattr(pictures.frames_mod, "light", lambda *a, **kw: list(BLACK_LIGHT))
    monkeypatch.setattr(pictures.readtext, "read", lambda *a, **kw: [caption])
    seen, ui = look(tmp_path)
    assert seen.frames == [dark[0]] and seen.light == [] and seen.page is not None
    assert seen.notes == ["The video's picture is black apart from some writing, so its light is not used."]


def test_one_black_picture_among_lit_ones_is_left_out(monkeypatch, tmp_path):
    shots, _ = staged(monkeypatch, tmp_path)
    night = black_frames(tmp_path, 1)[0]
    cover = Frame(tmp_path / "thumb-9.jpg", None, 0.01, 0.0, 0.0, "https://i/9.jpg")
    monkeypatch.setattr(pictures.frames_mod, "grab", lambda *a, **kw: [*shots, night])
    monkeypatch.setattr(pictures.frames_mod, "thumbnails", lambda *a, **kw: [cover])
    seen, _ = look(tmp_path)
    assert seen.frames == shots and seen.thumbs == [] and len(seen.light) == 3
    assert seen.notes == ["1 picture was black, so it was left out."]


def test_a_dim_night_view_with_shapes_in_it_is_not_called_black(monkeypatch, tmp_path):
    staged(monkeypatch, tmp_path, thumbs=0)
    night = [Frame(tmp_path / f"frame-{k}.jpg", 600.0 * (k + 1), 0.04, 0.01, 0.02) for k in range(3)]     # dark, but with lights
    dim = [(10.0 * i, 0.04, 0.01) for i in range(40)]
    monkeypatch.setattr(pictures.frames_mod, "grab", lambda *a, **kw: night)
    monkeypatch.setattr(pictures.frames_mod, "light", lambda *a, **kw: list(dim))
    monkeypatch.setattr(pictures.readtext, "read", lambda *a, **kw: [])
    seen, _ = look(tmp_path)
    assert seen.frames == night and seen.light == dim and seen.notes == [] and seen.page is not None


def test_two_sure_areas_that_do_not_overlap_cancel_out():
    east = Area(EASTERN, "the Eastern time zone", "Clock.", sure=True)
    west = Area((32.0, 49.5, -125.0, -114.0), "the Pacific time zone", "Sign.", sure=True)
    far_strip = Area((24.0, 50.0, -125.0, -114.0), "the Pacific strip", "Sun.", sure=False)
    near_strip = Area((24.0, 50.0, -100.0, -80.0), "the strip", "Sun.", sure=False)
    assert narrowest([east, west]) is None and narrowest([east, west], sure_only=True) is None
    # a soft area that disagrees gives way, and what the others agree on is kept
    both = narrowest([east, far_strip, near_strip])
    assert both.bbox == (24.0, 50.0, -90.5, -80.0) and both.label == "the Eastern time zone and the strip"
