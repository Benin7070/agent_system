"""Edge glow manager helper module.

Creates and manages EdgeGlowWidget instances across connected screens,
forwards state/level/opening/closing events, and gracefully disables itself
on any initialization or runtime failure without affecting the voice assistant.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication

from agentctl.logger import logger

# Ensure ui and orb package dependencies can be imported cleanly
_UI_DIR = str(Path(__file__).resolve().parent)
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

_ORB_DIR = str(Path(__file__).resolve().parent / "orb")
if _ORB_DIR not in sys.path:
    sys.path.insert(0, _ORB_DIR)

try:
    import agentctl.ui.orb.motion as _motion
    sys.modules.setdefault("orb_motion", _motion)
except Exception:
    pass

try:
    from edge_widget import EdgeGlowWidget
except Exception as e:
    EdgeGlowWidget = None
    _import_error = e
else:
    _import_error = None


class EdgeGlowManager(QObject):
    """Lifecycle and event manager for multi-screen EdgeGlowWidget instances."""

    def __init__(self, assets_dir: str | Path | None = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        ui_dir = Path(__file__).resolve().parent
        if assets_dir and (Path(assets_dir) / "edge.vert").exists():
            self.assets_dir = Path(assets_dir)
        elif (ui_dir / "edge.vert").exists():
            self.assets_dir = ui_dir
        else:
            self.assets_dir = ui_dir / "orb" / "assets"
        self._widgets: Dict[QScreen, EdgeGlowWidget] = {}
        self.enabled: bool = False
        self._band_px: float = 80.0
        self._idle_intensity: float = 0.0
        self._corner_radius_px: float = 28.0
        self._screens_mode: str = "all"
        self._current_state: str = "idle"
        self._current_level: float = 0.0

        # Read configuration safely
        try:
            from agentctl.config import edge_glow
            self.enabled = bool(getattr(edge_glow, "enabled", True))
            self._band_px = float(getattr(edge_glow, "band_px", 80.0))
            self._idle_intensity = float(getattr(edge_glow, "idle_intensity", 0.0))
            self._corner_radius_px = float(getattr(edge_glow, "corner_radius_px", 28.0))
            self._screens_mode = str(getattr(edge_glow, "screens", "all")).lower().strip()
        except Exception:
            # Fall back to defaults if config lookup fails
            self.enabled = True

        if not self.enabled:
            return

        if EdgeGlowWidget is None:
            logger.warn("EdgeGlow", f"EdgeGlowWidget could not be imported: {_import_error}. Glow disabled.")
            self.enabled = False
            return

        # Attempt initializing overlays
        try:
            self._init_overlays()
        except Exception as e:
            logger.warn("EdgeGlow", f"Failed to initialize edge glow overlays: {e}. Glow disabled.")
            self._cleanup_all()
            self.enabled = False

    def _init_overlays(self) -> None:
        app = QApplication.instance()
        if not app:
            logger.warn("EdgeGlow", "No active QApplication found when initializing edge glow. Glow disabled.")
            self.enabled = False
            return

        # Connect screen topology changes
        try:
            app.screenAdded.connect(self._on_screen_added)
            app.screenRemoved.connect(self._on_screen_removed)
        except Exception:
            pass

        target_screens = [app.primaryScreen()] if self._screens_mode == "primary" else app.screens()
        for screen in target_screens:
            if screen:
                self._create_widget_for_screen(screen)

        if not self._widgets:
            self.enabled = False

    def _create_widget_for_screen(self, screen: QScreen) -> Optional[EdgeGlowWidget]:
        if screen in self._widgets or not self.enabled:
            return None
        try:
            widget = EdgeGlowWidget(
                self.assets_dir,
                parent=None,
                band_px=self._band_px,
                idle_intensity=self._idle_intensity,
                corner_radius_px=self._corner_radius_px,
                start_visible=True,
            )
            widget.make_overlay(screen)
            widget.show()
            self._widgets[screen] = widget
            return widget
        except Exception as e:
            logger.warn("EdgeGlow", f"Failed to create edge glow for screen '{screen.name()}': {e}")
            return None

    def _on_screen_added(self, screen: QScreen) -> None:
        if not self.enabled:
            return
        if self._screens_mode == "all":
            try:
                widget = self._create_widget_for_screen(screen)
                if widget and self._current_state != "idle":
                    widget.set_state(self._current_state)
                    widget.set_level(self._current_level)
            except Exception as e:
                logger.warn("EdgeGlow", f"Error adding screen overlay: {e}")

    def _on_screen_removed(self, screen: QScreen) -> None:
        widget = self._widgets.pop(screen, None)
        if widget:
            try:
                widget.close()
                widget.deleteLater()
            except Exception:
                pass

    def _cleanup_all(self) -> None:
        for widget in list(self._widgets.values()):
            try:
                widget.close()
                widget.deleteLater()
            except Exception:
                pass
        self._widgets.clear()

    # ------------------------------------------------------------------ Forwarding API
    def set_state(self, name: str) -> None:
        if not self.enabled:
            return
        self._current_state = name
        for widget in list(self._widgets.values()):
            try:
                widget.set_state(name)
            except Exception as e:
                logger.warn("EdgeGlow", f"Error setting edge state '{name}': {e}")

    def set_level(self, value: float) -> None:
        if not self.enabled:
            return
        self._current_level = float(value)
        for widget in list(self._widgets.values()):
            try:
                widget.set_level(value)
            except Exception as e:
                logger.warn("EdgeGlow", f"Error setting edge level {value}: {e}")

    def play_opening(self) -> None:
        if not self.enabled:
            return
        for widget in list(self._widgets.values()):
            try:
                widget.play_opening()
            except Exception as e:
                logger.warn("EdgeGlow", f"Error playing edge opening: {e}")

    def play_closing(self) -> None:
        if not self.enabled:
            return
        for widget in list(self._widgets.values()):
            try:
                widget.play_closing()
            except Exception as e:
                logger.warn("EdgeGlow", f"Error playing edge closing: {e}")

    def close(self) -> None:
        app = QApplication.instance()
        if app:
            try:
                app.screenAdded.disconnect(self._on_screen_added)
            except Exception:
                pass
            try:
                app.screenRemoved.disconnect(self._on_screen_removed)
            except Exception:
                pass
        self._cleanup_all()
