import os
import subprocess
import sys
import time

import pytest

from flyby import runner
from flyby.model import FlybyError

FAMILY = """
import os, time
kids = []
for _ in range(3):
    pid = os.fork()
    if pid == 0:
        time.sleep(120)
        os._exit(0)
    kids.append(pid)
print(*kids, flush=True)
time.sleep(120)
"""


def gone(pid: int) -> bool:
    for _ in range(40):         # a killed child lingers until it is reaped
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_stop_ends_the_engine_and_every_worker_it_forked():
    proc = subprocess.Popen([sys.executable, "-c", FAMILY], stdout=subprocess.PIPE, start_new_session=True)
    kids = [int(x) for x in proc.stdout.readline().split()]
    assert len(kids) == 3
    runner._stop(proc)
    assert proc.poll() is not None
    assert all(gone(pid) for pid in kids)


def test_an_engine_that_goes_quiet_is_stopped_and_reported(tmp_path, monkeypatch):
    real, started = subprocess.Popen, []

    def stuck(cmd, **kw):
        started.append(real([sys.executable, "-c", FAMILY], **kw))
        return started[0]

    monkeypatch.setattr(runner.subprocess, "Popen", stuck)
    began = time.time()
    with pytest.raises(FlybyError, match="went quiet"):
        runner.run_engine(["search"], tmp_path / "logs" / "s.log", on_text=lambda line: None, stall_s=1.0)
    assert time.time() - began < 20 and started[0].poll() is not None


def test_engine_output_reaches_the_log_and_the_listener(tmp_path):
    heard = []
    runner.run_engine(["--help"], tmp_path / "help.log", on_text=heard.append)
    assert any("spectrogram" in line for line in heard)
    assert "spectrogram" in (tmp_path / "help.log").read_text()
    with pytest.raises(FlybyError, match="stopped with an error"):
        runner.run_engine(["no-such-step"], tmp_path / "bad.log")
