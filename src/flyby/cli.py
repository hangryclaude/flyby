"""flyby: send a video, get where it was filmed."""
from __future__ import annotations

import argparse
import re
import signal
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.markup import escape

from . import engine
from .ask import LONG_VIDEO_S, Extras, ask_source, interview, parse_section
from .captions import SAID
from .clues import STREAM_TOL_S, confirm_places, find_clues, local_days
from .guess import ai_guess
from .hints import read_hints
from .ingest import fetch, parse_coords, part_of, probe
from .measure import measure
from .model import CONUS, Answers, Area, FlybyError, Hint, Media, PlaceNotFound
from .paths import data_root, pages_dir, prepare_env
from .pictures import Seen, cross_checks, merge, narrowest, see
from .practice import practice
from .report import place_name, print_result, write_page
from .ui import header, indent, make_ui

EPILOG = """\
examples:
  flyby                                   asks for the link, then asks what you know
  flyby https://youtu.be/4CxVxuMVkMc --date 2026-09-08
  flyby ~/Movies/backyard.mov
  flyby https://youtu.be/... --area "Florida" --no-ask

In a terminal flyby asks a few questions first: where, which day, what time, which part.
Every answer makes the search faster or more sure, and Enter skips any of them. The options
above answer a question ahead of time; --no-ask skips the questions.

flyby also looks at the picture. It takes a few still frames, reads captions, clocks and signs
in them, and builds a page that searches the web for each frame. --no-pictures skips all of that.

Each day searched needs that day's flight tracks streamed once (about 4 GB over the network).
The index that is kept is about 6 GB per day for the whole USA, so the same day is never
streamed twice.

exit codes: 0 located, 3 searched but not located, 1 could not run, 130 stopped
"""

SEARCHES = {"quick": 1, "normal": engine.MAX_SEARCHES, "thorough": 2 * engine.MAX_SEARCHES}
FLAG_START_TOL_S = 180.0    # --start promises "to a few minutes"
HINT_START_TOL_S = 600.0    # a time read off a title or description is rounder than that
CLOCK_SLACK_S = 30.0        # when two clocks disagree, the true start may lie this far beyond their gap


def _start_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise FlybyError(f"'{text}' is not a time. Use UTC like 2026-09-08T20:17.") from None
    if ":" not in text:     # a bare day would be read as midnight, to the minute
        raise FlybyError(f"'{text}' has no time of day. Use UTC like 2026-09-08T20:17, or --date for a day alone.")
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _seconds(text: str) -> float:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(s|m|h)?\s*", text.lower())
    if not m:
        raise FlybyError(f"'{text}' is not a length of time. Use 90s, 10m or 1h.")
    return float(m[1]) * {"s": 1, "m": 60, "h": 3600}[m[2] or "s"]


def _count(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("give a whole number, 1 or more")
    return int(text)


def _length(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="flyby", description="Find where a recording was made from the aircraft it overheard.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="a video link (anything yt-dlp can read) or a local video or audio file; "
                                              "leave it out and flyby asks for it")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="the local day it was recorded, or a range YYYY-MM-DD..YYYY-MM-DD")
    ap.add_argument("--start", metavar="UTC", help="when the video began, in UTC (2026-09-08T20:17); "
                                                   "this alone can settle a weak match")
    ap.add_argument("--start-tol", metavar="TIME", help="how far off --start may be: 90s, 10m, 1h (default 3m)")
    ap.add_argument("--area", metavar="PLACE", help='where to search: "usa" (the default), a state, a city, or "lat,lon"')
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("LATMIN", "LATMAX", "LONMIN", "LONMAX"), help="search box, instead of --area")
    ap.add_argument("--section", metavar="H:MM-H:MM", help="use only this part of a long video, in hours and minutes "
                                                           "from its start (2:00-4:00)")
    ap.add_argument("--days-back", type=int, default=2, metavar="N",
                    help="for an upload with no recording date: how many days before the upload to try (default 2)")
    ap.add_argument("--effort", choices=list(SEARCHES), help="quick: one search; normal: up to "
                    f"{SEARCHES['normal']}; thorough: up to {SEARCHES['thorough']} (default normal)")
    ap.add_argument("--searches", type=_count, metavar="N", help="most searches to run before giving up, instead of --effort")
    ap.add_argument("--workers", type=int, metavar="N", help="processor cores for the search (default: two thirds of them)")
    ap.add_argument("--known", metavar="LAT,LON", help="practice: where it really was filmed. The search still runs blind; "
                    "afterwards flyby says how far off it was and what the flight data holds at that spot")
    ap.add_argument("--no-pictures", action="store_true", help="do not look at the picture: no still frames, no reading of "
                    "captions, clocks and signs, no picture search page")
    ap.add_argument("--ai-guess", action="store_true", help="let an AI look at three still frames and guess the place. "
                    "They go to Claude through the claude command, on your own plan. The guess is only a lead")
    ap.add_argument("--no-ask", "-y", action="store_true", help="ask no questions: use the options and what the video itself says")
    ap.add_argument("--no-open", action="store_true", help="do not open the result page in the browser")
    return ap


def _given(a: argparse.Namespace, media: Media) -> Answers:
    """What the options already answer."""
    section = None
    if a.section:
        try:
            section = parse_section(a.section, media.duration_s)
        except ValueError as miss:
            raise FlybyError(f"--section {a.section}: {miss} Use hours and minutes from the start, like 2:00-4:00.") from None
        if not isinstance(section, tuple):
            raise FlybyError(f"--section {a.section} is not a part of the video. Use hours and minutes, like 2:00-4:00.")
    start = _start_time(a.start) if a.start else None
    if start and a.date:
        days = local_days(a.date)
        # the start time alone decides which days are searched; a US evening is the next day in UTC
        if not days[0] - timedelta(days=1) <= start.date() <= days[-1] + timedelta(days=1):
            raise FlybyError(f"--date {a.date} and --start {a.start} are different days. Give one of them, or make them agree.")
    tol = _seconds(a.start_tol) if a.start_tol else FLAG_START_TOL_S if start else None
    return Answers(area=a.area, bbox=tuple(a.bbox) if a.bbox else None, day=a.date, start_utc=start, start_tol_s=tol,
                   section=section, effort=a.effort or (f"{a.searches} searches" if a.searches else None))


def _take_stated(given: Answers, hints: list[Hint], media: Media, area: Area | None = None) -> tuple[Answers, str | None]:
    """With nobody to ask, take what the publisher says outright. A weak guess is left alone:
    a wrong place makes the search miss, and nobody is here to catch it.
    `area` is a part of the map the pictures point to, used when nothing names a place.
    Returns the answers and, when the place came from the video, the words that gave it."""
    a = replace(given)
    why = None
    sure = [h for h in hints if h.strength in ("stated", "likely")]     # hints arrive strongest first
    if not (a.area or a.bbox or media.location_hint):
        if place := next((h for h in sure if h.kind == "place"), None):
            a.area, why = place.value, place.because
        elif area:
            a.bbox, a.box_label, a.box_why = area.bbox, area.label, area.why
    if not a.day and a.start_utc is None and media.started_utc is None:
        if start := next((h for h in sure if h.kind == "start"), None):
            a.start_utc, a.start_tol_s = _start_time(start.value), a.start_tol_s or HINT_START_TOL_S
        elif day := next((h for h in sure if h.kind == "day"), None):
            a.day = day.value
    return a, why


def _teller(ui, cut_next: bool = False):
    """What ingest is handed as `log=`: its sentences become steps from the captions table."""
    def log(text: str) -> None:
        words = text.rstrip(".")
        key = SAID.get(words)
        if key is None:
            ui.start(words)     # a sentence the table does not know is still shown, on its own
        else:
            ui.step(key, then="Cut out the part you chose" if key == "download" and cut_next else None)
    return log


def _ask_ai(ui, seen: Seen, folder, notes: list[str]) -> list[Hint]:
    """Let an AI look at the pictures. Like every picture tool it is an extra: a failure is a note, not a stop."""
    ui.step("guess")
    try:
        # the map settles each town the AI names, so a town that does not exist cannot become the search area
        found = confirm_places(ai_guess(seen.frames, folder))
    except FlybyError as e:
        ui.done("The AI gave no guess")
        ui.note(str(e))
        notes.append(str(e))
        return []
    ui.done(f"The AI gave {len(found)} guess{'es' if len(found) != 1 else ''}" if found else "The AI could not tell where this is")
    return found


def _zone_clash(seen: Seen, clues) -> list[str]:
    """A place somebody named, against the time zone the camera's clock is set to: a cheap second opinion."""
    out = []
    for area in seen.areas:
        if area.sure and clues.bbox != CONUS:
            lat, lon = (clues.bbox[0] + clues.bbox[1]) / 2, (clues.bbox[2] + clues.bbox[3]) / 2
            if not (area.bbox[0] <= lat <= area.bbox[1] and area.bbox[2] <= lon <= area.bbox[3]):
                out.append(f"The place searched ({clues.area_label}) is outside {area.label}, where the camera's clock "
                           "puts it. One of the two is wrong.")
    return out


def run(argv=None) -> int:
    a = _parser().parse_args(argv)
    console = Console()
    ui = make_ui(console)
    prepare_env()
    # a closed terminal or a plain `kill` must stop the engine too, not leave it running on every core
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(143))
    asking = not a.no_ask and console.is_terminal and sys.stdin.isatty()
    if console.is_terminal:
        header(console)
    try:
        if a.ai_guess and a.no_pictures:
            raise FlybyError("--ai-guess needs the pictures, so it cannot be used with --no-pictures.")
        known = parse_coords(a.known) if a.known else None
        if a.known and known is None:
            raise FlybyError(f"--known {a.known} is not a position. Use latitude,longitude like 41.3918,-82.5577.")
        source = a.source
        if source is None:
            if not asking:
                raise FlybyError("Give a video link or a file, for example: flyby https://youtu.be/4CxVxuMVkMc")
            source = ask_source(console)
        ui.section("Video")
        media = probe(source, log=_teller(ui))
        ui.done(f"{media.title} ({_length(media.duration_s)})")

        given = _given(a, media)
        hints = read_hints(media.about, media.uploaded)
        seen = Seen()
        pictures = data_root() / "runs" / media.slug / "pictures"      # under the whole video's name, whichever part is used
        if not a.no_pictures and media.video.get("has_video"):
            ui.section("Pictures")
            seen = see(media, pictures, data_root() / "bin", pages_dir(), ui)
            hints = merge(hints, seen.hints)
            media.notes.extend(seen.notes)
        if a.ai_guess:
            if seen.frames:
                hints = merge(hints, _ask_ai(ui, seen, pictures / "sent", media.notes))
            else:
                ui.note("--ai-guess needs pictures from the video, and there are none.")
        if any(h.towns for h in hints) and not (given.area or given.bbox or media.location_hint):
            ui.step("towns", then="Ask you what you know about the video" if asking else None)
            hints = confirm_places(hints)
            # the questions come next: the progress line must be gone. A town still carrying its readings was
            # not asked of the map (the map did not answer, or there were too many to ask)
            ui.done("Not every town name could be checked on the map" if any(h.kind == "place" and h.towns for h in hints)
                    else "Town names checked on the map")
        area_why = None
        if asking:
            ui.section("Questions")
            can_open = seen.page is not None and not a.no_open and sys.platform == "darwin"
            extras = Extras(area=narrowest(seen.areas),
                            open_pictures=(lambda: subprocess.run(["open", str(seen.page)], stdin=subprocess.DEVNULL))
                            if can_open else None,
                            ask_ai=(lambda: _ask_ai(ui, seen, pictures / "sent", media.notes))
                            if seen.frames and not a.ai_guess else None)
            answers = interview(console, media, hints, given, extras=extras)
        else:
            answers, area_why = _take_stated(given, hints, media, narrowest(seen.areas, sure_only=True))
            if media.duration_s > LONG_VIDEO_S and not answers.section:
                ui.note(f"All {_length(media.duration_s)} of it will be used. --section 2:00-4:00 uses a part and is much faster.")
        # the camera's own clock and the stream's clock disagree by this much, and nobody knows which one is right
        if seen.clock_gap_s is not None and answers.start_utc is None and answers.start_tol_s is None:
            answers.start_tol_s = max(STREAM_TOL_S, abs(seen.clock_gap_s) + CLOCK_SLACK_S)
            if answers.start_tol_s > STREAM_TOL_S:
                media.notes.append(f"Because the two clocks disagree, the start time is trusted only to within "
                                   f"{answers.start_tol_s:.0f} s.")
        if answers.still is False:
            media.notes.append("You said the camera moved. The method needs a microphone that stays still, "
                               "so treat any answer with care.")
        if answers.real_sound is False:
            media.notes.append("You said the sound has music or a voice over it. That can hide the aircraft.")

        # the person times the start of the video; a part of it starts that much later
        start = answers.start_utc + timedelta(seconds=answers.section[0]) if answers.start_utc and answers.section \
            else answers.start_utc
        # the day and the place are settled before the download, so a slip there costs seconds, not minutes
        said = dict(date_arg=answers.day, bbox_arg=answers.bbox, days_back=a.days_back, start_arg=start,
                    start_tol_s=answers.start_tol_s,
                    box=(answers.box_label, answers.box_why) if answers.box_label else None)
        try:
            clues = find_clues(part_of(media, answers.section), area_arg=answers.area, area_why=area_why, **said)
        except PlaceNotFound:
            if not area_why:
                raise
            # nobody chose this place: it was read off the video, and a map search does not know it
            clues = find_clues(part_of(media, answers.section), area_arg=None, **said)
            media.notes.append(f'The video names "{answers.area}", which no map search could find, so the whole USA was searched.')
        if not answers.box_label:
            media.notes.extend(_zone_clash(seen, clues))
        ui.section("Search")
        ui.note(f"When:  {clues.day_reason}")
        ui.note(f"Where: {clues.area_reason}")

        downloaded = media.audio_path is None or answers.section is not None
        media = fetch(media, data_root() / "runs", log=_teller(ui, cut_next=answers.section is not None),
                      section=answers.section)
        if downloaded:
            ui.done("Audio ready")

        work = data_root() / "runs" / media.slug
        flyovers, dropped = measure(media.audio_path, work, ui)
        result = engine.locate(media, clues, flyovers, dropped, work, data_root() / "days", ui, workers=a.workers,
                               max_searches=a.searches or SEARCHES[answers.effort or "normal"])
        if result.lat is not None and (seen.light or any(area.sure for area in seen.areas)):
            ui.step("daylight", then=None if not known else "Practice: check the true spot")
            # the pictures are timed from second 0 of the whole video; the search may have used a later part of it
            began = result.recording_start_utc or clues.start_utc
            if began and answers.section:
                began -= timedelta(seconds=answers.section[0])
            result.notes.extend(cross_checks(seen, result.lat, result.lon, began))
            ui.done("The pictures were checked against the answer")
        if known:
            ui.step("practice", then=None if result.lat is not None else "Show the result")
            result.notes.extend(practice(known, media, clues, result, flyovers, work / "rec.npz", data_root() / "days"))
        place = None
        if result.lat is not None:
            ui.step("place")
            place = place_name(result.lat, result.lon)
            if place and result.verdict == "located":
                result.headline = f"Recorded near {place}."
        ui.close()
        page = write_page(media, clues, result, place, pages_dir(), frames=seen.frames, pictures_page=seen.page)
        ui.section("Result")
        print_result(console, media, clues, result, place, page)
        if seen.page:
            console.print(indent(f"[dim]Picture search:[/] {escape(str(seen.page))}"), highlight=False)
        console.print()
        if result.lat is not None and not a.no_open and sys.platform == "darwin":
            subprocess.run(["open", str(page)], stdin=subprocess.DEVNULL)
        return 0 if result.verdict == "located" else 3      # 2 is argparse's code for a mistyped command
    except FlybyError as e:
        ui.close()
        console.print()
        console.print(indent(f"[bold red]✗[/] {escape(str(e))}"), highlight=False)
        return 1
    except (KeyboardInterrupt, EOFError):       # Ctrl-D at a question is the same wish as Ctrl-C
        ui.close()
        console.print()
        console.print(indent("[dim]Stopped. Run the same command again: every finished step is kept.[/]"))
        return 130
    finally:
        ui.close()      # an unexpected error must not leave the live bar holding the terminal


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
