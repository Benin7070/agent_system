"""Tests for BorderWidget state transitions, level smoothing and opacity gating."""
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer


def _app():
    return QApplication.instance() or QApplication([])


def test_border_state_color_table():
    """set_state() must correctly update internal color and state-id for all 6 states."""
    from agentctl.ui.border_widget import BorderWidget, _STATE_TABLE
    app = _app()
    bw = BorderWidget()
    for name, (sid, ca, cb) in _STATE_TABLE.items():
        bw.set_state(name)
        assert bw._state_id == sid, f"State id mismatch for {name}"
        assert bw._color_a == ca
        assert bw._color_b == cb


def test_border_level_clamp():
    """set_level() clamps input to [0, 1]."""
    from agentctl.ui.border_widget import BorderWidget
    app = _app()
    bw = BorderWidget()
    bw.set_level(-0.5)
    assert bw._level == 0.0
    bw.set_level(1.5)
    assert bw._level == 1.0
    bw.set_level(0.42)
    assert abs(bw._level - 0.42) < 1e-6


def test_border_opacity_stops_timer():
    """Timer must stop when opacity is set to 0 and restart when set > 0."""
    from agentctl.ui.border_widget import BorderWidget
    app = _app()
    bw = BorderWidget()
    bw.set_opacity(1.0)
    bw.set_state("listening")
    assert bw._timer.isActive()
    bw.set_opacity(0.0)
    # Give Qt event loop one tick and trigger tick
    QTimer.singleShot(50, lambda: None)
    bw._tick()
    assert not bw._timer.isActive(), "Timer should stop when opacity is 0"

