"""flyby: send a video, get where it was filmed."""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.markup import escape

from . import engine
from .clues import find_clues
from .ingest import ingest
from .measure import measure
from .model import FlybyError
from .paths import data_root, pages_dir, prepare_env
from .report import place_name, print_result, write_page
from .ui import make_ui

EPILOG = """\
examples:
  flyby https://youtu.be/4CxVxuMVkMc --date 2026-09-08
  flyby ~/Movies/backyard.mov
  flyby https://youtu.be/... --area "Florida"

The day and the area are worked out from the video when it says them. --date and
--area only make the search faster and surer. Each day searched needs that day's
flight tracks streamed once (about 4 GB over the network). The index that is kept
is about 6 GB per day for the whole USA, so the same day is never streamed twice.

exit codes: 0 located, 3 searched but not located, 1 could not run, 130 stopped
"""


def _start_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise FlybyError(f"'{text}' is not a time. Use UTC like 2026-09-08T20:17.") from None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _length(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="flyby", description="Find where a recording was made from the aircraft it overheard.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a video link (anything yt-dlp can read) or a local video or audio file")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="the local day it was recorded, or a range YYYY-MM-DD..YYYY-MM-DD")
    ap.add_argument("--start", metavar="UTC", help="when the recording began, in UTC, if you know it to a few minutes "
                                                   "(2026-09-08T20:17); this alone can settle a weak match")
    ap.add_argument("--area", metavar="PLACE", help='where to search: "usa" (the default), a state, a city')
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("LATMIN", "LATMAX", "LONMIN", "LONMAX"), help="search box, instead of --area")
    ap.add_argument("--days-back", type=int, default=2, metavar="N",
                    help="for an upload with no recording date: how many days before the upload to try (default 2)")
    ap.add_argument("--searches", type=int, default=engine.MAX_SEARCHES, metavar="N",
                    help=f"most searches to run before giving up (default {engine.MAX_SEARCHES})")
    ap.add_argument("--workers", type=int, metavar="N", help="processor cores for the search (default: two thirds of them)")
    ap.add_argument("--no-open", action="store_true", help="do not open the result page in the browser")
    return ap


def run(argv=None) -> int:
    a = _parser().parse_args(argv)
    console = Console()
    ui = make_ui(console)
    prepare_env()
    # a closed terminal or a plain `kill` must stop the engine too, not leave it running on every core
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(143))
    try:
        media = ingest(a.source, data_root() / "runs", log=lambda text: ui.start(text.rstrip(".")))
        ui.done(f"{media.title} ({_length(media.duration_s)})")
        if a.start:
            media.started_utc = _start_time(a.start)
        clues = find_clues(media, date_arg=a.date, area_arg=a.area, bbox_arg=tuple(a.bbox) if a.bbox else None,
                           days_back=a.days_back)
        ui.note(f"When:  {clues.day_reason}")
        ui.note(f"Where: {clues.area_reason}")

        work = data_root() / "runs" / media.slug
        flyovers, dropped = measure(media.audio_path, work, ui)
        result = engine.locate(media, clues, flyovers, dropped, work, data_root() / "days", ui,
                               workers=a.workers, max_searches=a.searches)
        place = None
        if result.lat is not None:
            ui.start("Looking up the place name")
            place = place_name(result.lat, result.lon)
            if place and result.verdict == "located":
                result.headline = f"Recorded near {place}."
        ui.close()
        page = write_page(media, clues, result, place, pages_dir())
        console.print()
        print_result(console, media, clues, result, place, page)
        if result.lat is not None and not a.no_open and sys.platform == "darwin":
            subprocess.run(["open", str(page)])
        return 0 if result.verdict == "located" else 3      # 2 is argparse's code for a mistyped command
    except FlybyError as e:
        ui.close()
        console.print(f"[bold red]flyby:[/] {escape(str(e))}", highlight=False)
        return 1
    except KeyboardInterrupt:
        ui.close()
        console.print("Stopped. Run the same command again: every finished step is kept.")
        return 130
    finally:
        ui.close()      # an unexpected error must not leave the live bar holding the terminal


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
