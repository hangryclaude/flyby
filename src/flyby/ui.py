"""What the user watches while flyby works: a title, then one line per step. The step in hand
gets a short live block: NOW (what flyby is doing), WHY (why it matters), NEXT (what follows).

The look is set here and shared: one accent colour, a two-space left margin, and a short
heading over each part of a run. The words of the block come from captions.py."""
from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live as LiveArea
from rich.markup import escape
from rich.padding import Padding
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .captions import STEPS

ACCENT = "cyan"
MARGIN = "  "
_RULE = 46
TITLE_COLS = 60     # the block letters need this much room; under it the one-line header is used
BODY = 44           # the captions are written for a column this wide: short lines are easier to read
_LABEL = 6
_BAR = 24

# FLYBY drawn on a grid of square dots, two dots to a row of text (a half block is one dot)
TITLE = ("██▀▀▀▀▀  ██       ██    ██  ██▀▀▀█▄  ██    ██",
         "██▄▄▄    ██        ▀█▄▄█▀   ██▄▄▄█▀   ▀█▄▄█▀ ",
         "██       ██          ██     ██   ██     ██   ",
         "▀▀       ▀▀▀▀▀▀▀     ▀▀     ▀▀▀▀▀▀      ▀▀   ")
TAGLINE = "find where a video was filmed"


def indent(renderable, by: int = len(MARGIN)) -> Padding:
    """Left margin that survives wrapping, which a string prefix does not."""
    return Padding(renderable, (0, 0, 0, by))


def header(console: Console) -> None:
    console.print()
    if console.is_terminal and console.width >= TITLE_COLS:
        for row in TITLE:
            console.print(f"{MARGIN}[bold {ACCENT}]{row}[/]", highlight=False)
        console.print(f"{MARGIN}[dim]{TAGLINE}[/]", highlight=False)
    else:
        console.print(f"{MARGIN}[bold {ACCENT}]flyby[/]  [dim]{TAGLINE}[/]", highlight=False)
    console.print(f"{MARGIN}[dim]{'─' * _RULE}[/]")


def heading(console: Console, title: str) -> None:
    console.print()
    console.print(f"{MARGIN}[{ACCENT}]▍[/][bold]{escape(title)}[/]", highlight=False)


class Plain:
    """Step lines only. Used when the output is not a terminal (a pipe, a log file, the tests)."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def section(self, title: str) -> None:
        heading(self.console, title)

    def step(self, key: str, then: str | None = None, **words) -> None:
        """Start a step from the captions table. `words` fill its fields; `then` replaces what
        the table says comes next, for a caller that knows this run goes another way."""
        caption = STEPS[key]
        self._begin(caption.now.format(**words), caption.why.format(**words), then or caption.then.format(**words))

    def start(self, text: str) -> None:
        self._begin(text, "", "")

    def _begin(self, now: str, why: str, then: str) -> None:
        self.console.print(indent(f"[dim]·[/] {escape(now)}..."), highlight=False)

    def progress(self, done: float, total: float, detail: str = "") -> None:
        pass

    def done(self, text: str) -> None:
        self.console.print(indent(f"[green]✓[/] {escape(text)}"), highlight=False)

    def note(self, text: str) -> None:
        self.console.print(indent(f"[dim]{escape(text)}[/]", 4), highlight=False)

    def close(self) -> None:
        pass


class Live(Plain):
    """The same lines, plus the live block for the step in hand: its caption, a spinner, a bar,
    a percent and a clock. The block goes away when the step ends."""

    def __init__(self, console: Console | None = None):
        super().__init__(console)
        self.area: LiveArea | None = None
        self.now = self.why = self.then = self.detail = ""
        self.completed, self.total = 0.0, None
        self.began = 0.0
        self.spinner = Spinner("dots", style=ACCENT)

    def _stop(self) -> None:
        if self.area is not None:
            self.area.stop()
            self.area = None

    def section(self, title: str) -> None:
        self._stop()
        super().section(title)

    def _begin(self, now: str, why: str, then: str) -> None:
        self._stop()
        self.now, self.why, self.then, self.detail = now, why, then, ""
        self.completed, self.total = 0.0, None
        self.began = time.monotonic()
        self.spinner = Spinner("dots", style=ACCENT)
        self.area = LiveArea(console=self.console, get_renderable=self.block, transient=True, refresh_per_second=10)
        self.area.start()

    def block(self) -> Padding:
        """The live block as it stands this instant."""
        # one column is always left free at the right edge: Ctrl-C makes the terminal type "^C" after
        # the block, and on a full line that wraps, so the block is rubbed out one row short
        body = max(min(BODY, self.console.width - len(MARGIN) - _LABEL - 1), 24)
        known = bool(self.total)
        share = min(self.completed / self.total, 1.0) if known else 0.0
        meter = Table.grid(padding=(0, 2, 0, 0))
        meter.add_row(self.spinner,
                      ProgressBar(total=1.0, completed=share, width=min(_BAR, body - 18), pulse=not known, style="grey23",
                                  complete_style=ACCENT, finished_style=ACCENT, pulse_style=ACCENT),
                      # rounded down: 100% is only shown for a step that is truly through
                      Text(f"{int(share * 100):3d}%" if known else "    "),
                      Text(_clock(time.monotonic() - self.began), style="dim"))
        grid = Table.grid()
        grid.add_column(width=_LABEL, no_wrap=True)
        grid.add_column(width=body)
        grid.add_row(Text("NOW", style=f"bold {ACCENT}"), Text(self.now, style="bold"))
        grid.add_row("", meter)
        if self.detail:
            grid.add_row("", Text(self.detail, style="dim"))
        if self.why:
            grid.add_row(Text("WHY", style="bold"), Text(self.why))
        if self.then:
            grid.add_row(Text("NEXT", style="bold"), Text(self.then, style="dim"))
        return Padding(grid, (0, 0, 0, len(MARGIN)), expand=False)

    def progress(self, done: float, total: float, detail: str = "") -> None:
        if self.area is not None:
            self.completed, self.total, self.detail = done, total, detail

    def done(self, text: str) -> None:
        took = time.monotonic() - self.began if self.area is not None else 0.0
        self._stop()
        # only steps long enough to wonder about get their time shown
        tail = f" [dim]({_took(took)})[/]" if took >= 20 else ""
        self.console.print(indent(f"[green]✓[/] {escape(text)}{tail}"), highlight=False)

    def close(self) -> None:
        self._stop()


def _took(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def make_ui(console: Console):
    return Live(console) if console.is_terminal else Plain(console)
