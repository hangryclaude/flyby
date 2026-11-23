"""Run one command of the engine as its own process, keeping its output and reporting progress."""
from __future__ import annotations

import fcntl
import os
import select
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from .model import FlybyError
from .paths import engine_env

# A search keeps most of the cores busy for many minutes. At this priority the system gives
# them to anything else that asks first, so the machine stays usable while flyby works.
NICE = 15
# The longest the engine has gone without printing in a real whole-USA search is 17 s. Its
# worker pool waits for ever on a worker that was killed, so a long silence means it is stuck.
STALL_S = 900


@contextmanager
def one_at_a_time(folder: Path, on_wait=None):
    """Hold the one place for work that fills the memory. A second whole-USA search started
    beside the first pushed a 48 GB Mac 15 GB past full, and both ran at a quarter speed.
    on_wait is called once if another flyby holds the place; then this waits for it."""
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "busy.lock", "w") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if on_wait:
                on_wait()
            fcntl.flock(held, fcntl.LOCK_EX)
        yield       # closing the file gives the place up, however this ends


def _stop(proc: subprocess.Popen) -> None:
    """End the engine and every pool worker it forked. They share the process group made for
    them, so one signal reaches them all, including workers the pool started later."""
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    held = {sig: signal.signal(sig, signal.SIG_IGN) for sig in signals}     # a second Ctrl-C must not cut this short
    try:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(10)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    finally:
        for sig, was in held.items():
            signal.signal(sig, was)


def run_engine(args: list[str], log_path: Path, on_text=None, stall_s: float | None = None,
               air_c: float | None = None) -> None:
    """Run `dopplertrack <args>`. Everything it prints goes to log_path; on_text sees each piece
    as it arrives (the engine redraws some progress lines with a bare carriage return).
    stall_s: give up when it prints nothing for this long (only the search reports often enough).
    air_c: the air temperature the engine should take the speed of sound at (see enginecli)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["nice", "-n", str(NICE), sys.executable, "-m", "flyby.enginecli", *args]
    env = engine_env()
    if air_c is not None:
        env["FLYBY_AIR_C"] = str(air_c)
    with open(log_path, "wb") as log:
        log.write(((f"FLYBY_AIR_C={air_c} " if air_c is not None else "") + " ".join(cmd) + "\n").encode())
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                bufsize=0, start_new_session=True)
        try:
            while True:
                if not select.select([proc.stdout], [], [], stall_s)[0]:
                    raise FlybyError(f"the engine's {args[0]} step went quiet for {stall_s / 60:.0f} minutes, so it was stopped. "
                                     f"Run the same command again. Full log: {log_path}")
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                log.write(chunk)
                log.flush()
                if on_text:
                    for piece in chunk.decode("utf-8", "replace").replace("\r", "\n").split("\n"):
                        if piece.strip():
                            on_text(piece)
            code = proc.wait()
        except BaseException:
            # Ctrl-C included: the engine must not keep the cores busy after flyby has gone
            _stop(proc)
            raise
    if code != 0:
        tail = log_path.read_text(errors="replace").strip().splitlines()[-1:]
        raise FlybyError(f"the engine's {args[0]} step stopped with an error"
                         + (f" ({tail[0].strip()})" if tail else "") + f". Full log: {log_path}")
