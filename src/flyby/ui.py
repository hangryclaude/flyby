"""What the user watches while flyby works: one line per step, with a live bar on the long ones."""
from __future__ import annotations

import time

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


class Plain:
    """Step lines only. Used when the output is not a terminal (a pipe, a log file, the tests)."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def start(self, text: str) -> None:
        self.console.print(f"  {escape(text)}...", highlight=False)

    def progress(self, done: float, total: float, detail: str = "") -> None:
        pass

    def done(self, text: str) -> None:
        self.console.print(f"[green]✓[/] {escape(text)}", highlight=False)

    def note(self, text: str) -> None:
        self.console.print(f"  {escape(text)}", highlight=False)

    def close(self) -> None:
        pass


class Live(Plain):
    """The same lines, plus a spinner, a bar and a clock for the step in hand."""

    def __init__(self, console: Console | None = None):
        super().__init__(console)
        self.bar = Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(bar_width=24),
                            TextColumn("{task.fields[detail]}"), TimeElapsedColumn(),
                            console=self.console, transient=True)
        self.task = None
        self.began = 0.0

    def _stop(self) -> None:
        if self.task is not None:
            self.bar.stop()
            self.bar.remove_task(self.task)
            self.task = None

    def start(self, text: str) -> None:
        self._stop()
        self.began = time.monotonic()
        self.task = self.bar.add_task(escape(text), total=None, detail="")
        self.bar.start()

    def progress(self, done: float, total: float, detail: str = "") -> None:
        if self.task is not None:
            self.bar.update(self.task, completed=done, total=total, detail=escape(detail))

    def done(self, text: str) -> None:
        took = time.monotonic() - self.began if self.task is not None else 0.0
        self._stop()
        # only steps long enough to wonder about get their time shown
        tail = f" [dim]({_took(took)})[/]" if took >= 20 else ""
        self.console.print(f"[green]✓[/] {escape(text)}{tail}", highlight=False)

    def note(self, text: str) -> None:
        self._stop()
        super().note(text)

    def close(self) -> None:
        self._stop()


def _took(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def make_ui(console: Console):
    return Live(console) if console.is_terminal else Plain(console)
