"""Screen-edge glow behaviour. Same events as the orb (set_state, set_level, play_opening, play_closing).

EdgeMotion extends OrbMotion, so the state machine, level smoothing and one-shots are IDENTICAL to the orb's:
feed both widgets the same messages and they stay in step. This class only turns that into the edge shader's uniforms.
"""
from __future__ import annotations

import math
from typing import Dict

from orb_motion import (DENIED_SECONDS, OrbMotion, _approach, track)

EDGE_OPEN_APPEAR = [(0, 0.0, "out"), (18, 1.15, "inout"), (34, 1.0, None)]
EDGE_OPEN_FADE = [(0, 0.0, "linear"), (8, 1.0, None)]
EDGE_OPEN_SWEEP = [(0, 0.0, "inout"), (56, 1.0, None)]       # one lap around the border
EDGE_CLOSE_APPEAR = [(0, 1.0, "inout"), (26, 0.0, None)]
EDGE_CLOSE_FADE = [(0, 1.0, "linear"), (14, 1.0, "inout"), (26, 0.0, None)]


class EdgeMotion(OrbMotion):
    def __init__(self, meta, visible: bool = True, idle_intensity: float = 0.0,
                 band_px: float = 80.0, corner_radius_px: float = 28.0):
        super().__init__(meta, visible)
        self.idle_intensity = idle_intensity
        self.band_px = band_px
        self.corner_radius_px = corner_radius_px
        self.e_int = idle_intensity
        self.e_thick = 0.5
        self.e_wave = 0.2
        self.e_think = 0.0
        self.e_phase = 0.0
        self.e_comet = 0.0

    # ------------------------------------------------------------------ targets per state
    def _edge_targets(self):
        lv, t, s = self.level, self.clock, self.state
        if s == "idle":
            return self.idle_intensity, 0.5, 0.2, 0.0
        if s == "listening":
            return 0.40 + 0.75 * lv, 0.50 + 0.55 * lv, 0.4 + 0.4 * lv, 0.0
        if s == "thinking":
            return 0.38 + 0.4 * lv, 0.50 + 0.3 * lv, 0.35, 1.0
        if s == "speaking":
            return 0.55 + 0.75 * lv, 0.60 + 0.65 * lv, 0.6 + 0.3 * lv, 0.0
        if s == "confirm":
            return 0.50 + 0.22 * math.sin(2 * math.pi * 0.9 * t), 0.65, 0.25, 0.0
        return 0.70, 0.90, 0.2, 0.0        # denied (the flash itself comes from the denied envelope)

    @property
    def is_active(self) -> bool:
        """False when nothing is drawn (idle with no glow, or closed). The widget can then stop rendering."""
        if not self.visible:
            return False
        busy = self._open_t is not None or self._close_t is not None or self._denied_t is not None
        return busy or self.e_int > 0.01

    def update(self, dt: float) -> Dict[str, object]:
        super().update(dt)
        dt = max(0.0, min(dt, 0.1))
        inten, thick, wave, think = self._edge_targets()
        self.e_int = _approach(self.e_int, inten, dt, 0.10 if inten > self.e_int else 0.30)
        self.e_thick = _approach(self.e_thick, thick, dt, 0.08 if thick > self.e_thick else 0.25)
        self.e_wave = _approach(self.e_wave, wave, dt, 0.2)
        self.e_think = _approach(self.e_think, think, dt, 0.3)
        self.e_phase = (self.e_phase + dt * 0.10 * self.speed) % 1000.0
        self.e_comet = (self.e_comet + dt * 0.35) % 1.0
        return self.edge_uniforms()

    def edge_uniforms(self, width: float = 1920.0, height: float = 1080.0) -> Dict[str, object]:
        """Uniforms for edge.frag. The widget adds u_res itself; width/height only scale the band for tiny previews."""
        appear, opacity, sweep = 1.0, 1.0, -1.0
        if self._open_t is not None:
            appear = track(EDGE_OPEN_APPEAR, self._open_t)
            opacity = track(EDGE_OPEN_FADE, self._open_t)
            if self._open_t <= 56:
                sweep = track(EDGE_OPEN_SWEEP, self._open_t)
        if self._close_t is not None:
            appear = track(EDGE_CLOSE_APPEAR, self._close_t)
            opacity = track(EDGE_CLOSE_FADE, self._close_t)
        if not self.visible:
            opacity = 0.0

        flash = 0.0
        if self._denied_t is not None:
            k = self._denied_t / DENIED_SECONDS
            flash = min(1.0, k / 0.08) * (1.0 - min(1.0, k)) ** 0.6

        return {
            "u_band": self.band_px,
            "u_radius": self.corner_radius_px,
            "u_intensity": max(0.0, self.e_int),
            "u_thick": max(0.05, self.e_thick),
            "u_phase": self.e_phase,
            "u_wave": self.e_wave,
            "u_tint": list(self.tint),
            "u_tint_amt": self.tint_amt,
            "u_think": self.e_think,
            "u_comet": self.e_comet,
            "u_flash": flash,
            "u_sweep": sweep,
            "u_appear": max(0.0, appear),
            "u_opacity": opacity,
        }
