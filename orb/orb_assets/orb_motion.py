"""Orb behaviour: states, live level, opening/closing. Pure Python, no Qt, no OpenGL.

OrbMotion turns events (set_state, set_level, play_opening, play_closing) into the shader uniforms for
one frame. Call update(dt) once per render tick and hand the returned dict to the shader.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Dict, Optional

STATES = ("idle", "listening", "thinking", "speaking", "confirm", "denied")
COMP = 1080.0


# ----------------------------------------------------------------------------- helpers
def _bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """CSS-style cubic-bezier easing (same curves as the Lottie keyframes)."""
    def sample(a, b, t):
        return 3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t ** 2 + t ** 3

    def ease(x: float) -> float:
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(28):
            mid = (lo + hi) / 2
            if sample(x1, x2, mid) < x:
                lo = mid
            else:
                hi = mid
        return sample(y1, y2, (lo + hi) / 2)
    return ease


EASE = {
    "out": _bezier(0.16, 1.0, 0.3, 1.0),
    "inout": _bezier(0.65, 0.0, 0.35, 1.0),
    "linear": lambda x: min(1.0, max(0.0, x)),
}


def track(keys, frame: float) -> float:
    """keys: [(frame, value, ease_name_or_None), ...]; evaluates like Lottie keyframes."""
    if frame <= keys[0][0]:
        return keys[0][1]
    for (f0, v0, e), (f1, v1, _) in zip(keys, keys[1:]):
        if frame < f1:
            x = (frame - f0) / (f1 - f0)
            return v0 + (v1 - v0) * EASE[e or "linear"](x)
    return keys[-1][1]


def _approach(cur: float, target: float, dt: float, tau: float) -> float:
    return target + (cur - target) * math.exp(-dt / tau)


# ----------------------------------------------------------------------------- one-shot curves (60 fps frames)
OPEN_SCALE = [(0, 0.0, "out"), (16, 1.15, "inout"), (28, 0.94, "inout"), (40, 1.02, "inout"), (54, 1.0, None)]
OPEN_FADE = [(0, 0.0, "linear"), (7, 1.0, None)]
OPEN_FRAMES = 100          # the shader's ring/bloom/sparks are finished well before this

CLOSE_SCALE = [(0, 1.0, "out"), (8, 1.05, "inout"), (34, 0.0, None)]
CLOSE_FADE = [(0, 1.0, "linear"), (12, 1.0, "inout"), (34, 0.0, None)]
CLOSE_FRAMES = 34

DENIED_SECONDS = 0.85


class OrbMotion:
    def __init__(self, meta: dict | str | Path, visible: bool = True):
        if not isinstance(meta, dict):
            meta = json.loads(Path(meta).read_text())
        self.meta = meta
        self.n_frames = int(meta["frames"])
        self.src_fps = float(meta["fps"])

        self.state = "idle"
        self.clock = 0.0
        self.raw_level = 0.0
        self.level = 0.0
        self._level_time = -10.0

        # smoothed outputs
        self.speed = 0.3
        self.amp = 0.2
        self.scale_extra = 0.0
        self.tint = [1.0, 1.0, 1.0]
        self.tint_amt = 0.0
        self.glow = 0.0
        self.phase = 0.0            # in source frames (30 fps)

        self.visible = visible
        self._open_t: Optional[float] = None    # frames at 60 fps since opening started
        self._close_t: Optional[float] = None
        self._denied_t: Optional[float] = None
        self.on_closed: Optional[Callable[[], None]] = None
        self.on_opened: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------ events
    def set_state(self, name: str) -> None:
        if name not in STATES:
            raise ValueError(f"unknown state '{name}', expected one of {STATES}")
        if name == "denied":
            self._denied_t = 0.0
        self.state = name

    def set_level(self, value: float) -> None:
        self.raw_level = min(1.0, max(0.0, float(value)))
        self._level_time = self.clock

    def play_opening(self) -> None:
        self.visible = True
        self._close_t = None
        self._open_t = 0.0

    def play_closing(self) -> None:
        self._open_t = None
        self._close_t = 0.0

    # ------------------------------------------------------------------ per frame
    def _targets(self):
        """Per-state target values: speed, amp, scale_extra, tint, tint_amt, glow."""
        t, lv = self.clock, self.level
        s = self.state
        white, amt, glow = [1.0, 1.0, 1.0], 0.0, 0.0
        if s == "idle":
            speed, amp, sc = 0.30, 0.20, 0.0
        elif s == "listening":
            speed, amp, sc = 0.75 + 1.1 * lv, 0.45 + 1.0 * lv, 0.045 * lv
        elif s == "thinking":
            speed = 0.55
            amp = 0.50 + 0.12 * math.sin(2 * math.pi * 0.45 * t) + 0.6 * lv
            sc = 0.02 * math.sin(2 * math.pi * 0.35 * t) + 0.03 * lv
            white, amt = [0.80, 0.90, 1.0], 0.25
        elif s == "speaking":
            speed, amp, sc = 0.90 + 1.0 * lv, 0.60 + 1.1 * lv, 0.06 * lv
            glow = 0.25 * lv
        elif s == "confirm":
            speed, amp = 0.45, 0.40 + 0.10 * math.sin(2 * math.pi * 0.9 * t)
            sc = 0.0
            white, amt = [1.0, 0.78, 0.42], 0.40
            glow = 0.35 + 0.15 * math.sin(2 * math.pi * 0.9 * t)
        else:  # denied
            speed, amp, sc = 0.40, 0.25, 0.0
            k = 0.0 if self._denied_t is None else self._denied_t / DENIED_SECONDS
            env = min(1.0, k / 0.08) * (1.0 - min(1.0, k)) ** 0.6
            white, amt = [1.0, 0.25, 0.22], 0.85 * env
        return speed, amp, sc, white, amt, glow

    def update(self, dt: float) -> Dict[str, object]:
        dt = max(0.0, min(dt, 0.1))
        self.clock += dt

        # level: fast attack, slow release; stale values decay to zero
        raw = self.raw_level if (self.clock - self._level_time) < 0.3 else 0.0
        tau = 0.045 if raw > self.level else 0.20
        self.level = _approach(self.level, raw, dt, tau)

        # one-shot timers (60 fps frames)
        if self._denied_t is not None:
            self._denied_t += dt
            if self._denied_t >= DENIED_SECONDS:
                self._denied_t = None
                if self.state == "denied":
                    self.state = "idle"
        if self._open_t is not None:
            self._open_t += dt * 60.0
            if self._open_t >= OPEN_FRAMES:
                self._open_t = None
                if self.on_opened:
                    self.on_opened()
        if self._close_t is not None:
            self._close_t += dt * 60.0
            if self._close_t >= CLOSE_FRAMES:
                self._close_t = None
                self.visible = False
                if self.on_closed:
                    self.on_closed()

        speed, amp, sc, tint, tint_amt, glow = self._targets()
        k = 0.12
        self.speed = _approach(self.speed, speed, dt, k)
        self.amp = _approach(self.amp, amp, dt, k)
        self.scale_extra = _approach(self.scale_extra, sc, dt, 0.06)
        self.tint = [_approach(c, t, dt, 0.15) for c, t in zip(self.tint, tint)]
        self.tint_amt = _approach(self.tint_amt, tint_amt, dt, 0.08 if tint_amt > self.tint_amt else 0.25)
        self.glow = _approach(self.glow, glow, dt, 0.15)

        self.phase = (self.phase + dt * self.speed * self.src_fps) % self.n_frames
        return self.uniforms()

    def uniforms(self) -> Dict[str, object]:
        pos = self.phase
        fa = int(math.floor(pos)) % self.n_frames
        fb = (fa + 1) % self.n_frames

        scale, opacity, fx = 1.0 + self.scale_extra, 1.0, -1.0
        if self._open_t is not None:
            scale *= track(OPEN_SCALE, self._open_t)
            opacity = track(OPEN_FADE, self._open_t)
            fx = self._open_t
        if self._close_t is not None:
            scale *= track(CLOSE_SCALE, self._close_t)
            opacity = track(CLOSE_FADE, self._close_t)
        if not self.visible:
            opacity = 0.0

        shake = 0.0
        if self._denied_t is not None:
            shake = 16.0 * math.sin(2 * math.pi * 7.0 * self._denied_t) * math.exp(-5.0 * self._denied_t)

        crop = self.meta["crop"]
        atlas = self.meta["atlas"]
        return {
            "u_scale": scale,
            "u_opacity": opacity,
            "u_shift": [shake, 0.0],
            "u_amp": max(0.05, self.amp),
            "u_tint": list(self.tint),
            "u_tint_amt": self.tint_amt,
            "u_glow": max(0.0, self.glow),
            "u_glow_color": [0.55, 0.65, 1.0] if self.state != "confirm" else [1.0, 0.75, 0.4],
            "u_frame_a": float(fa),
            "u_frame_b": float(fb),
            "u_frame_mix": pos - math.floor(pos),
            "u_fx": fx,
            "u_cell": [float(atlas["cell_w"]), float(atlas["cell_h"])],
            "u_cols": float(atlas["cols"]),
            "u_atlas_size": [float(atlas["width"]), float(atlas["height"])],
            "u_crop": [crop["x"], crop["y"], crop["w"], crop["h"]],
            "u_baseline": float(self.meta["baseline_y"]),
        }


def fake_speech_level(t: float) -> float:
    """Speech-like level for testing without a microphone or TTS (0..1)."""
    return max(0.0, min(1.0, 0.5 + 0.45 * math.sin(t * 7.0) * math.sin(t * 2.3)))
