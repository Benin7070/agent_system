"""Tests for Edge Glow Widget, Motion Controller, Configuration, and Manager."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from agentctl.config import ORB_ASSETS_DIR, edge_glow
from agentctl.ui.edge_glow import EdgeGlowManager

# Import edge_motion & orb_motion
_UI_DIR = str(Path(__file__).resolve().parent.parent / "src" / "agentctl" / "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

_ORB_DIR = str(Path(__file__).resolve().parent.parent / "src" / "agentctl" / "ui" / "orb")
if _ORB_DIR not in sys.path:
    sys.path.insert(0, _ORB_DIR)

import agentctl.ui.orb.motion as _motion
sys.modules.setdefault("orb_motion", _motion)

from edge_motion import EdgeMotion
from orb_motion import OrbMotion

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "agentctl" / "ui"
META = UI_DIR / "orb_meta.json"
DT = 1 / 60


def _run_motion(m: EdgeMotion, seconds: float, level: float | None = None) -> None:
    for _ in range(int(seconds / DT)):
        if level is not None:
            m.set_level(level)
        m.update(DT)


# ==============================================================================
# EdgeMotion Unit Tests
# ==============================================================================

def test_idle_is_dark_and_inactive():
    m = EdgeMotion(META)
    _run_motion(m, 3.0)
    assert m.edge_uniforms()["u_intensity"] < 0.01 and not m.is_active


def test_faint_idle_rim_when_configured():
    m = EdgeMotion(META, idle_intensity=0.12)
    _run_motion(m, 3.0)
    assert 0.08 < m.edge_uniforms()["u_intensity"] < 0.16 and m.is_active


def test_louder_is_brighter_and_thicker():
    q, l = EdgeMotion(META), EdgeMotion(META)
    for m, lv in ((q, 0.0), (l, 1.0)):
        m.set_state("speaking")
        _run_motion(m, 1.5, level=lv)
    uq, ul = q.edge_uniforms(), l.edge_uniforms()
    assert ul["u_intensity"] > uq["u_intensity"] + 0.5 and ul["u_thick"] > uq["u_thick"] + 0.4


def test_thinking_shows_comets():
    m = EdgeMotion(META)
    m.set_state("thinking")
    _run_motion(m, 2.0, level=0.2)
    assert m.edge_uniforms()["u_think"] > 0.9
    m.set_state("listening")
    _run_motion(m, 2.0, level=0.2)
    assert m.edge_uniforms()["u_think"] < 0.05


def test_denied_flashes_red_then_calms():
    m = EdgeMotion(META)
    m.set_state("listening")
    _run_motion(m, 0.5, level=0.3)
    m.set_state("denied")
    peak = 0.0
    for _ in range(int(1.0 / DT)):
        peak = max(peak, m.update(DT) and m.edge_uniforms()["u_flash"])
    assert peak > 0.6 and m.edge_uniforms()["u_flash"] == 0.0 and m.state == "idle"


def test_opening_sweep_and_closing_stops():
    m = EdgeMotion(META, visible=False)
    seen = []
    m.on_opened, m.on_closed = lambda: seen.append("o"), lambda: seen.append("c")
    m.play_opening()
    u = m.update(DT)
    assert m.visible and u["u_sweep"] >= 0.0
    _run_motion(m, 2.0)
    assert seen == ["o"] and m.edge_uniforms()["u_sweep"] < 0
    m.play_closing()
    _run_motion(m, 1.0)
    assert seen == ["o", "c"] and not m.is_active and m.edge_uniforms()["u_opacity"] == 0.0


def test_same_events_same_state_as_orb():
    a, b = OrbMotion(META), EdgeMotion(META)
    for m in (a, b):
        m.set_state("speaking")
    for _ in range(120):
        for m in (a, b):
            m.set_level(0.6)
            m.update(DT)
    assert abs(a.level - b.level) < 1e-9 and abs(a.phase - b.phase) < 1e-9 and a.state == b.state


# ==============================================================================
# Edge Glow Config Tests
# ==============================================================================

def test_edge_glow_config_defaults():
    # Save original state
    orig_enabled = edge_glow._enabled
    orig_band = edge_glow._band_px
    orig_idle = edge_glow._idle_intensity
    orig_radius = edge_glow._corner_radius_px
    orig_screens = edge_glow._screens

    try:
        edge_glow._enabled = None
        edge_glow._band_px = None
        edge_glow._idle_intensity = None
        edge_glow._corner_radius_px = None
        edge_glow._screens = None

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTCTL_EDGE_GLOW_ENABLED", None)
            os.environ.pop("AGENTCTL_EDGE_GLOW_BAND_PX", None)
            os.environ.pop("AGENTCTL_EDGE_GLOW_IDLE_INTENSITY", None)
            os.environ.pop("AGENTCTL_EDGE_GLOW_CORNER_RADIUS_PX", None)
            os.environ.pop("AGENTCTL_EDGE_GLOW_SCREENS", None)

            assert edge_glow.enabled is True
            assert edge_glow.band_px == 80.0
            assert edge_glow.idle_intensity == 0.0
            assert edge_glow.corner_radius_px == 28.0
            assert edge_glow.screens == "all"
    finally:
        edge_glow._enabled = orig_enabled
        edge_glow._band_px = orig_band
        edge_glow._idle_intensity = orig_idle
        edge_glow._corner_radius_px = orig_radius
        edge_glow._screens = orig_screens


def test_edge_glow_config_env_overrides():
    orig_enabled = edge_glow._enabled
    try:
        edge_glow._enabled = None
        with patch.dict(os.environ, {"AGENTCTL_EDGE_GLOW_ENABLED": "0"}):
            assert edge_glow.enabled is False

        with patch.dict(os.environ, {"AGENTCTL_EDGE_GLOW_ENABLED": "false"}):
            assert edge_glow.enabled is False

        with patch.dict(os.environ, {"AGENTCTL_EDGE_GLOW_BAND_PX": "120"}):
            edge_glow._band_px = None
            assert edge_glow.band_px == 120.0
    finally:
        edge_glow._enabled = orig_enabled


# ==============================================================================
# EdgeGlowManager Tests
# ==============================================================================

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        from agentctl.ui.orb.widget import OrbWidget
        OrbWidget.configure_format()
        app = QApplication([])
    return app


def test_edge_glow_manager_disabled(qapp):
    orig_enabled = edge_glow.enabled
    try:
        edge_glow.enabled = False
        manager = EdgeGlowManager(ORB_ASSETS_DIR)
        assert manager.enabled is False
        assert len(manager._widgets) == 0

        # Verify forwarding methods do not crash or create anything
        manager.set_state("listening")
        manager.set_level(0.5)
        manager.play_opening()
        manager.play_closing()
        manager.close()
        assert len(manager._widgets) == 0
    finally:
        edge_glow.enabled = orig_enabled


def test_edge_glow_manager_lifecycle(qapp):
    orig_enabled = edge_glow.enabled
    try:
        edge_glow.enabled = True
        manager = EdgeGlowManager(ORB_ASSETS_DIR)
        assert manager.enabled is True
        assert len(manager._widgets) > 0

        # Forward all events
        manager.set_state("listening")
        manager.set_level(0.7)
        manager.play_opening()
        qapp.processEvents()

        manager.set_state("thinking")
        manager.set_state("speaking")
        manager.play_closing()
        qapp.processEvents()

        # Teardown
        manager.close()
        assert len(manager._widgets) == 0
    finally:
        edge_glow.enabled = orig_enabled


def test_edge_glow_manager_error_handling(qapp):
    """Verifies that if widget creation fails, manager logs a warning, disables glow, and never crashes."""
    orig_enabled = edge_glow.enabled
    try:
        edge_glow.enabled = True
        with patch("agentctl.ui.edge_glow.EdgeGlowWidget", side_effect=RuntimeError("Simulated GL failure")):
            manager = EdgeGlowManager(ORB_ASSETS_DIR)
            assert manager.enabled is False
            assert len(manager._widgets) == 0

            # Forwarding methods remain safe no-ops
            manager.set_state("listening")
            manager.play_opening()
    finally:
        edge_glow.enabled = orig_enabled
