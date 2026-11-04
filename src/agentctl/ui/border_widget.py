"""BorderWidget: full-screen speech-reactive border glow using GLSL 330 core.

API (all methods are thread-safe — called from the GUI thread only since they
are connected to Qt signals in overlay.py):
    bw = BorderWidget(parent_widget)
    bw.set_state(name: str)       # idle | listening | thinking | speaking | confirm | denied
    bw.set_level(value: float)    # 0..1 smoothed in _handle_ws_level before call
    bw.set_opacity(value: float)  # 0..1 overall fade (called by overlay fade-in/out)
"""
from __future__ import annotations

import array
import math
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer
from PyQt6.QtGui import QColor, QPalette, QSurfaceFormat, QVector2D, QVector3D
from PyQt6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVersionFunctionsFactory,
    QOpenGLVersionProfile,
    QOpenGLVertexArrayObject,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

# GL constants (from gl.h; identical for every OpenGL implementation)
GL_COLOR_BUFFER_BIT = 0x4000
GL_TRIANGLE_STRIP   = 0x0005
GL_FLOAT            = 0x1406
GL_BLEND            = 0x0BE2
GL_SRC_ALPHA        = 0x0302
GL_ONE              = 0x0001
GL_ONE_MINUS_SRC_ALPHA = 0x0303

# State name → (int id, (r,g,b) color_a, (r,g,b) color_b)
_STATE_TABLE = {
    "idle":      (0, (0.376, 0.647, 0.980), (0.655, 0.545, 0.980)),   # blue / purple
    "listening": (1, (0.506, 0.549, 0.973), (0.220, 0.741, 0.973)),   # indigo / cyan
    "thinking":  (2, (0.961, 0.620, 0.043), (0.937, 0.267, 0.267)),   # amber / red-orange
    "speaking":  (3, (0.204, 0.827, 0.600), (0.133, 0.773, 0.816)),   # green / teal
    "confirm":   (4, (0.976, 0.800, 0.102), (0.976, 0.600, 0.102)),   # gold
    "denied":    (5, (0.973, 0.443, 0.443), (0.820, 0.102, 0.102)),   # red
}
_DEFAULT_STATE = _STATE_TABLE["idle"]

SHADER_DIR = Path(__file__).parent / "shaders"
DEFAULT_HEADER = "#version 330 core\n"
BORDER_BAND_PX = 90.0   # default bandwidth; reduced on small screens in resizeGL


class BorderWidget(QOpenGLWidget):
    """GLSL-rendered full-screen border glow. Transparent everywhere except the edge strips."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAutoFillBackground(False)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self.setPalette(pal)
        self.setStyleSheet("background: transparent; border: none; outline: none;")

        self._state_id: int        = 0
        self._color_a: tuple       = _DEFAULT_STATE[1]
        self._color_b: tuple       = _DEFAULT_STATE[2]
        self._level: float         = 0.0
        self._smooth_level: float  = 0.0
        self._opacity: float       = 0.0     # starts invisible; overlay fades it in
        self._band_px: float       = BORDER_BAND_PX

        self._gl   = None
        self._prog: Optional[QOpenGLShaderProgram] = None
        self._vao: Optional[QOpenGLVertexArrayObject] = None
        self._vbo: Optional[QOpenGLBuffer] = None
        self._clock = QElapsedTimer()

        # 30 fps render loop — 33 ms is enough for an ambient effect
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ public API
    def set_state(self, name: str) -> None:
        entry = _STATE_TABLE.get(name.lower(), _DEFAULT_STATE)
        self._state_id = entry[0]
        self._color_a  = entry[1]
        self._color_b  = entry[2]
        self._start()

    def set_level(self, value: float) -> None:
        self._level = max(0.0, min(1.0, float(value)))

    def set_opacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))
        if value > 0.0:
            self._start()

    # ------------------------------------------------------------------ internal
    def _start(self) -> None:
        if not self._timer.isActive():
            self._clock.restart()
            self._timer.start()

    def _tick(self) -> None:
        # Smooth: level rises fast (tau≈30ms), falls slowly (tau≈200ms)
        dt = max(self._clock.restart(), 1) / 1000.0
        tau = 0.03 if self._level > self._smooth_level else 0.20
        self._smooth_level += (self._level - self._smooth_level) * (1.0 - math.exp(-dt / tau))

        if self._opacity <= 0.0:
            self._timer.stop()
            return
        self.update()

    # ------------------------------------------------------------------ OpenGL lifecycle
    def initializeGL(self) -> None:
        profile = QOpenGLVersionProfile()
        profile.setVersion(4, 1)
        profile.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        self._gl = QOpenGLVersionFunctionsFactory.get(profile, self.context())
        if self._gl is None:
            raise RuntimeError("BorderWidget: OpenGL 4.1 core functions unavailable.")
        self._gl.initializeOpenGLFunctions()
        self._gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT)

        prog = QOpenGLShaderProgram(self)
        for kind, name in (
            (QOpenGLShader.ShaderTypeBit.Vertex,   "border.vert"),
            (QOpenGLShader.ShaderTypeBit.Fragment, "border.frag"),
        ):
            raw_src = (SHADER_DIR / name).read_text()
            src = raw_src if raw_src.strip().startswith("#version") else DEFAULT_HEADER + raw_src
            if not prog.addShaderFromSourceCode(kind, src):
                raise RuntimeError(f"BorderWidget shader '{name}': {prog.log()}")

        prog.bindAttributeLocation("a_pos", 0)
        if not prog.link():
            raise RuntimeError(f"BorderWidget shader link: {prog.log()}")
        self._prog = prog

        # Full-screen quad as a triangle-strip: TL, TR, BL, BR (CCW in NDC)
        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        quad = array.array("f", [-1.0, 1.0,  1.0, 1.0,  -1.0, -1.0,  1.0, -1.0]).tobytes()
        self._vbo.allocate(quad, len(quad))
        prog.bind()
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, GL_FLOAT, 0, 2, 0)
        self._vao.release()
        prog.release()

    def resizeGL(self, w: int, h: int) -> None:
        if self._gl:
            self._gl.glViewport(0, 0, w, h)
        # Scale band with display height so it looks consistent on HiDPI
        self._band_px = max(60.0, min(h * 0.08, 140.0))

    def paintGL(self) -> None:
        gl = self._gl
        if gl is None or self._prog is None:
            return

        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT)

        if self._opacity <= 0.001:
            return

        # Additive-ish blend: src*src_alpha + dst*(1-src_alpha) for premultiplied output
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)

        prog = self._prog
        prog.bind()

        t = self._clock.elapsed() / 1000.0
        prog.setUniformValue("u_time",       float(t))
        prog.setUniformValue("u_level",      float(self._smooth_level))
        prog.setUniformValue("u_opacity",    float(self._opacity))
        prog.setUniformValue("u_state",      int(self._state_id))
        prog.setUniformValue("u_band_px",    float(self._band_px))
        prog.setUniformValue("u_resolution", QVector2D(float(self.width()), float(self.height())))
        prog.setUniformValue("u_color_a",    QVector3D(*self._color_a))
        prog.setUniformValue("u_color_b",    QVector3D(*self._color_b))

        self._vao.bind()
        gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        self._vao.release()
        prog.release()

        gl.glDisable(GL_BLEND)

