"""OrbWidget: the voice orb as a PyQt6 QOpenGLWidget.

    from orb_widget import OrbWidget
    OrbWidget.configure_format()          # once, BEFORE creating QApplication
    app = QApplication(sys.argv)
    orb = OrbWidget(assets_dir)           # folder with orb.vert, orb.frag, orb_*.png, orb_meta.json
    orb.set_orb_size(240)                 # orb diameter in logical pixels
    orb.set_state("listening")            # idle | listening | thinking | speaking | confirm | denied
    orb.set_level(0.4)                    # 0..1, any time, any thread
    orb.play_opening() / orb.play_closing()

Every public method is thread-safe: calls are forwarded to the GUI thread through Qt signals.
Needs an OpenGL 4.1 core context (PyQt6 only wraps the 4.1-core function set; the shaders themselves are plain GLSL 330).
If your platform cannot give that, tell me what it reports and I will adapt the loader.
"""
from __future__ import annotations

import array
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QImage, QPalette, QSurfaceFormat, QVector2D, QVector3D, QVector4D
from PyQt6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture,
    QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile, QOpenGLVertexArrayObject,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from orb_motion import OrbMotion

GL_COLOR_BUFFER_BIT = 0x4000
GL_TRIANGLE_STRIP = 0x0005
GL_FLOAT = 0x1406
GL_BLEND = 0x0BE2
GL_TEXTURE0 = 0x84C0
GL_ONE = 0x0001
GL_ONE_MINUS_SRC_ALPHA = 0x0303

DISC_FRACTION = 780.0 / 1080.0   # the orb disc spans 780 of the 1080 stage units
DEFAULT_HEADER = "#version 330 core\n"


class OrbWidget(QOpenGLWidget):
    _state_sig = pyqtSignal(str)
    _level_sig = pyqtSignal(float)
    _open_sig = pyqtSignal()
    _close_sig = pyqtSignal()
    closed = pyqtSignal()        # emitted when the closing animation has finished
    opened = pyqtSignal()        # emitted when the opening effects have finished

    def __init__(self, assets_dir: str | Path, parent=None, orb_size: int = 240,
                 version_header: str = DEFAULT_HEADER, start_visible: bool = True):
        super().__init__(parent)
        self.assets = Path(assets_dir)
        self.header = version_header
        self.motion = OrbMotion(self.assets / "orb_meta.json", visible=start_visible)
        self.motion.on_closed = self.closed.emit
        self.motion.on_opened = self.opened.emit
        self._uniforms = self.motion.uniforms()
        self._gl = None
        self._prog = None
        self._tex = {}
        self._vao = None
        self._vbo = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAutoFillBackground(False)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self.setPalette(pal)
        self.setStyleSheet("background: transparent; border: none; outline: none;")
        self.set_orb_size(orb_size)

        self._state_sig.connect(self._on_state)
        self._level_sig.connect(self._on_level)
        self._open_sig.connect(self._on_open)
        self._close_sig.connect(self._on_close)

    # ------------------------------------------------------------------ public API (thread-safe)
    @staticmethod
    def configure_format() -> None:
        fmt = QSurfaceFormat()
        fmt.setVersion(4, 1)   # PyQt6 only wraps GL 4.1 core (and 2.x); drivers return >= this, macOS maxes out here
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setAlphaBufferSize(8)
        fmt.setSwapInterval(1)
        QSurfaceFormat.setDefaultFormat(fmt)

    def set_orb_size(self, px: int) -> None:
        side = int(round(px / DISC_FRACTION))
        self.setFixedSize(side, side)

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
        if not self.motion.visible:
            self._timer.stop()      # hidden: no more rendering work until something wakes it
        self.update()

    # ------------------------------------------------------------------ OpenGL
    def _load_texture(self, name: str) -> QOpenGLTexture:
        img = QImage(str(self.assets / name))
        if img.isNull():
            raise FileNotFoundError(self.assets / name)
        # Bytes are already premultiplied: QImage -> RGBA8888 keeps the values, QOpenGLTexture does not flip.
        tex = QOpenGLTexture(img.convertToFormat(QImage.Format.Format_RGBA8888),
                             QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
        tex.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        return tex

    def initializeGL(self) -> None:
        profile = QOpenGLVersionProfile()
        profile.setVersion(4, 1)
        profile.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        self._gl = QOpenGLVersionFunctionsFactory.get(profile, self.context())
        if self._gl is None:
            raise RuntimeError("OpenGL 4.1 core functions unavailable; the context is older than 4.1 core. "
                               "Check OrbWidget.configure_format() runs before QApplication is created.")
        self._gl.initializeOpenGLFunctions()

        prog = QOpenGLShaderProgram(self)
        for kind, fname in ((QOpenGLShader.ShaderTypeBit.Vertex, "orb.vert"), (QOpenGLShader.ShaderTypeBit.Fragment, "orb.frag")):
            src = self.header + (self.assets / fname).read_text()
            if not prog.addShaderFromSourceCode(kind, src):
                raise RuntimeError(f"{fname}: {prog.log()}")
        prog.bindAttributeLocation("a_pos", 0)
        if not prog.link():
            raise RuntimeError(prog.log())
        self._prog = prog

        self._tex = {"u_base": self._load_texture("orb_base.png"),
                     "u_top": self._load_texture("orb_top.png"),
                     "u_atlas": self._load_texture("orb_atlas.png")}

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
        if self._prog is None or self._uniforms.get("u_opacity", 1.0) <= 0.0 and not self.motion.visible:
            return
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)

        prog = self._prog
        prog.bind()
        for unit, (name, tex) in enumerate(self._tex.items()):
            gl.glActiveTexture(GL_TEXTURE0 + unit)
            tex.bind()
            prog.setUniformValue(name, unit)
        for name, v in self._uniforms.items():
            if isinstance(v, (list, tuple)):
                prog.setUniformValue(name, {2: QVector2D, 3: QVector3D, 4: QVector4D}[len(v)](*[float(x) for x in v]))
            else:
                prog.setUniformValue(name, float(v))
        self._vao.bind()
        gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        self._vao.release()
        prog.release()
        gl.glDisable(GL_BLEND)
