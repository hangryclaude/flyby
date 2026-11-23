"""Find the flyovers in a recording and measure each one, with nobody picking windows by hand.

The engine's `detect` finds the right moments but its own fits are too loose to search with:
on the reference clip it put one aircraft's closest pass 8 s early and its speed 45% low, and
the search then rejected the true flight. A second fit in a tight window around each
detection, the way the engine's author fitted by hand, lands inside the search's gates.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .runner import run_engine

BAND = (60.0, 300.0)        # blade-pass tones of propeller aircraft, Hz
DETECT_TOP = 12
HALF_WINDOW_S = 55.0        # the hand-fit windows that located the reference clip were 110 s wide
BAND_AROUND_F0 = (0.70, 1.41)
MAX_BURST = 0.15            # the engine's own example cut-off for trains and trucks
MIN_SWEEP = 0.10            # a tone that bends less than this across the window is a hum, not a pass
MAX_DRIFT_S = 15.0          # a real pass is found at the same moment and pitch by both fits
MAX_F0_DRIFT = 0.08
MIN_WINDOW_S = 80.0
SPEED_MS = (25.0, 200.0)
RANGE_M = (150.0, 12000.0)  # the search cannot look past 12 km
# detect clamps its fits to these; a fit sitting on a clamp did not find a real curve
DETECT_V_CLAMP = (35.0, 200.0)
DETECT_D_CLAMP = (200.0, 15000.0)


@dataclass
class Flyover:
    """One measured pass. `fit` is the engine's event record, exactly as its search reads it."""
    rank: int               # the engine's detection rank, 1 = clearest
    strength: float         # detect's score: how far a Doppler-shaped ridge stands out, dB
    fit: dict
    path: Path

    @property
    def t(self) -> float:
        return self.fit["t_cpa"]


def _on_clamp(det: dict) -> bool:
    v, d = det["v"], det["d"]
    return (v <= DETECT_V_CLAMP[0] * 1.03 or v >= DETECT_V_CLAMP[1] * 0.97
            or d <= DETECT_D_CLAMP[0] * 1.03 or d >= DETECT_D_CLAMP[1] * 0.97)


def sweep(fit: dict, c: float) -> float:
    """How far the fitted tone bends across its window, as a fraction of the rest tone."""
    tau = np.array([fit["t_start"], fit["t_end"]]) - fit["t_cpa_emit"]
    r = np.sqrt(fit["d"] ** 2 + (fit["v"] * tau) ** 2)
    ratio = c / (c + fit["v"] ** 2 * tau / r)
    return float(ratio[0] - ratio[1])


def why_not(det: dict, fit: dict | None, c: float) -> str | None:
    """The reason a detection is not used as a flyover, or None when it is one."""
    if det.get("burst", 0.0) > MAX_BURST:
        return "broadband noise (a train or truck)"
    if _on_clamp(det):
        return "no clear bend (a steady hum)"
    if fit is None:
        return "too close to the start or end"
    if abs(fit["t_cpa"] - det["t_cpa"]) > MAX_DRIFT_S or abs(fit["f0"] / det["f0"] - 1) > MAX_F0_DRIFT:
        return "unstable fit"
    if not SPEED_MS[0] <= fit["v"] <= SPEED_MS[1] or not RANGE_M[0] <= fit["d"] <= RANGE_M[1]:
        return "not aircraft-like"
    if sweep(fit, c) < MIN_SWEEP:
        return "no clear bend (a steady hum)"
    return None


def measure(audio: Path, run: Path, ui) -> tuple[list[Flyover], dict[str, int]]:
    """Flyovers found in the audio, clearest first, and a count of what was set aside and why."""
    from dopplertrack.audio import Spectrogram
    from dopplertrack.doppler import speed_of_sound
    from dopplertrack.tracks import fit_event

    spec = run / "rec.npz"
    if not spec.exists():
        ui.step("spectrogram", then="Measure each pass" if (run / "detected.json").exists() else None)
        part = run / "rec.part.npz"     # so an interrupted run never leaves a half-written picture behind
        run_engine(["spectrogram", str(audio), str(part)], run / "logs" / "spectrogram.log")
        part.rename(spec)
        ui.done("Audio decoded")

    found = run / "detected.json"
    if not found.exists():
        ui.step("listen")

        def on_text(text: str) -> None:
            m = re.search(r"window (\d+)/(\d+)", text)
            if m:
                ui.progress(int(m.group(1)), int(m.group(2)))

        part = run / "detected.part.json"
        run_engine(["detect", str(spec), "--band", str(BAND[0]), str(BAND[1]), "--top", str(DETECT_TOP),
                    "--out", str(part)], run / "logs" / "detect.log", on_text)
        part.rename(found)
    detections = json.loads(found.read_text())

    ui.step("passes")
    sp = Spectrogram.load(spec)
    c = speed_of_sound()
    events = run / "events"
    events.mkdir(exist_ok=True)
    flyovers: list[Flyover] = []
    dropped: dict[str, int] = {}
    for rank, det in enumerate(detections, 1):
        ui.progress(rank, len(detections))
        a, b = max(det["t_cpa"] - HALF_WINDOW_S, float(sp.t[0])), min(det["t_cpa"] + HALF_WINDOW_S, float(sp.t[-1]))
        fit = None
        if b - a >= MIN_WINDOW_S and det.get("burst", 0.0) <= MAX_BURST and not _on_clamp(det):
            fit = fit_event(sp, a, b, BAND_AROUND_F0[0] * det["f0"], BAND_AROUND_F0[1] * det["f0"],
                            harmonics=1, objective="brightness").to_dict()
        reason = why_not(det, fit, c)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        fit["burst"] = det.get("burst", 0.0)
        path = events / f"e{rank}.json"
        path.write_text(json.dumps(fit, indent=1))
        flyovers.append(Flyover(rank, float(det["score"]), fit, path))
    flyovers.sort(key=lambda f: -f.strength)
    ui.done(f"{len(flyovers)} flyover{'s' if len(flyovers) != 1 else ''} measured"
            + (f", {sum(dropped.values())} other sounds set aside" if dropped else ""))
    return flyovers, dropped
