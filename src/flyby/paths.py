"""Where flyby keeps its data, and the environment the engine runs in."""
from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    """Day indexes, run folders and the engine's caches. The name ends in .noindex so Spotlight skips the churn."""
    env = os.environ.get("FLYBY_HOME")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").exists():
        return repo / "data.noindex"
    return Path.home() / "Library" / "Caches" / "flyby.noindex"


def pages_dir() -> Path:
    """Where result pages are written."""
    env = os.environ.get("FLYBY_PAGES")
    if env:
        return Path(env)
    return data_root() / "pages"


def prepare_env() -> None:
    """Point the engine's own cache (aircraft type table, terrain tiles) into flyby's data folder."""
    os.environ.setdefault("DOPPLERTRACK_CACHE", str(data_root() / "engine-cache"))


def engine_env() -> dict[str, str]:
    """Environment for an engine subprocess.

    The engine's search forks a worker pool. On macOS a forked child that asks the system for
    proxy settings or touches Objective-C can die, which is why the engine defaults to one
    worker here. Any proxy variable in the environment stops Python from asking the system,
    and the second switch lets the fork through; with both, the pool runs on every core."""
    return {
        **os.environ,
        "DOPPLERTRACK_CACHE": str(data_root() / "engine-cache"),
        "PYTHONUNBUFFERED": "1",
        "no_proxy": "*",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
    }
