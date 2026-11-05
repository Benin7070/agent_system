"""EdgeGlowWidget: full-screen glow around the 4 screen edges, as a PyQt6 QOpenGLWidget.

    from edge_widget import EdgeGlowWidget
    OrbWidget.configure_format()                      # same call as for the orb, once, before QApplication
    edge = EdgeGlowWidget(assets_dir)                 # same folder as the orb
    edge.make_overlay(screen)                         # frameless, transparent, always on top, click-through, covers the screen
    edge.set_state("listening"); edge.set_level(0.4); edge.play_opening(); edge.play_closing()

The API is the same as OrbWidget on purpose: send every message to BOTH widgets and they stay in step.
Extra settings: set_band(px), set_idle_intensity(0..1)   (0 = nothing is drawn while idle; try 0.12 for a faint always-on rim).
All methods are thread-safe. While nothing is visible (idle with no glow, or closed) the render timer stops.
"""
from __future__ import annotations

import array
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QSurfaceFormat, QVector2D, QVector3D
from PyQt6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLVersionFunctionsFactory,
                            QOpenGLVersionProfile, QOpenGLVertexArrayObject)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from edge_motion import EdgeMotion

GL_COLOR_BUFFER_BIT = 0x4000
GL_TRIANGLE_STRIP = 0x0005
GL_FLOAT = 0x1406
GL_BLEND = 0x0BE2


class EdgeGlowWidget(QOpenGLWidget):
    _state_sig = pyqtSignal(str)
    _level_sig = pyqtSignal(float)
    _open_sig = pyqtSignal()
    _close_sig = pyqtSignal()
    closed = pyqtSignal()
    opened = pyqtSignal()

    def __init__(self, assets_dir: str | Path, parent=None, band_px: float = 80.0,
                 idle_intensity: float = 0.0, corner_radius_px: float = 28.0, start_visible: bool = True):
        super().__init__(parent)
        self.assets = Path(assets_dir)
        self.motion = EdgeMotion(self.assets / "orb_meta.json", visible=start_visible, idle_intensity=idle_intensity,
                                 band_px=band_px, corner_radius_px=corner_radius_px)
        self.motion.on_closed = self.closed.emit
        self.motion.on_opened = self.opened.emit
        self._uniforms = self.motion.edge_uniforms()
        self._gl = self._prog = self._vao = self._vbo = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._state_sig.connect(self._on_state)
        self._level_sig.connect(self._on_level)
        self._open_sig.connect(self._on_open)
        self._close_sig.connect(self._on_close)

    # ------------------------------------------------------------------ public API (thread-safe)
    def make_overlay(self, screen=None) -> None:
        """Turn this widget into a click-through, always-on-top overlay covering `screen` (default: primary)."""
        from PyQt6.QtWidgets import QApplication
        screen = screen or QApplication.primaryScreen()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(screen.geometry())

    def set_band(self, px: float) -> None:
        self.motion.band_px = float(px)

    def set_idle_intensity(self, v: float) -> None:
        self.motion.idle_intensity = max(0.0, min(1.0, float(v)))
        self._start()

    def set_state(self, name: str) -> None:
        self._state_sig.emit(name)

    def set_level(self, value: float) -> None:
        self._level_sig.emit(float(value))

    def play_opening(self) -> None:
        self._open_sig.emit()

    def play_closing(self) -> None:
        self._close_sig.emit()

    # ------------------------------------------------------------------ slots (GUI thread)
    @pyqtSlot(str)
    def _on_state(self, name: str) -> None:
        self.motion.set_state(name)
        self._start()

    @pyqtSlot(float)
    def _on_level(self, v: float) -> None:
        self.motion.set_level(v)
        self._start()

    @pyqtSlot()
    def _on_open(self) -> None:
        self.motion.play_opening()
        self._start()

    @pyqtSlot()
    def _on_close(self) -> None:
        self.motion.play_closing()
        self._start()

    def _start(self) -> None:
        if not self._timer.isActive():
            self._clock.restart()
            self._timer.start()

    def _tick(self) -> None:
        dt = self._clock.restart() / 1000.0
        self._uniforms = self.motion.update(dt)
        if not self.motion.is_active:
            self._timer.stop()          # nothing visible: no render work until the next event
        self.update()

    # ------------------------------------------------------------------ OpenGL
    def initializeGL(self) -> None:
        profile = QOpenGLVersionProfile()
        profile.setVersion(4, 1)
        profile.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        self._gl = QOpenGLVersionFunctionsFactory.get(profile, self.context())
        if self._gl is None:
            raise RuntimeError("OpenGL 4.1 core functions unavailable; call OrbWidget.configure_format() before QApplication.")
        self._gl.initializeOpenGLFunctions()

        prog = QOpenGLShaderProgram(self)
        for kind, fname in ((QOpenGLShader.ShaderTypeBit.Vertex, "edge.vert"), (QOpenGLShader.ShaderTypeBit.Fragment, "edge.frag")):
            if not prog.addShaderFromSourceCode(kind, "#version 330 core\n" + (self.assets / fname).read_text()):
                raise RuntimeError(f"{fname}: {prog.log()}")
        prog.bindAttributeLocation("a_pos", 0)
        if not prog.link():
            raise RuntimeError(prog.log())
        self._prog = prog

        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        quad = array.array("f", [-1, -1, 1, -1, -1, 1, 1, 1]).tobytes()
        self._vbo.allocate(quad, len(quad))
        prog.bind()
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, GL_FLOAT, 0, 2, 0)
        self._vao.release()
        prog.release()

    def paintGL(self) -> None:
        gl = self._gl
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT)
        if self._prog is None:
            return
        gl.glDisable(GL_BLEND)                # premultiplied output replaces the cleared pixel
        dpr = self.devicePixelRatioF()
        prog = self._prog
        prog.bind()
        prog.setUniformValue("u_res", QVector2D(self.width() * dpr, self.height() * dpr))
        for name, v in self._uniforms.items():
            if name in ("u_band", "u_radius"):
                v = float(v) * dpr            # settings are in logical pixels
            if isinstance(v, (list, tuple)):
                prog.setUniformValue(name, QVector3D(*[float(x) for x in v]))
            else:
                prog.setUniformValue(name, float(v))
        self._vao.bind()
        gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        self._vao.release()
        prog.release()
