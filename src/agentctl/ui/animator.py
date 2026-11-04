from pathlib import Path
from typing import Optional
from OpenGL import GL
from PyQt6.QtCore import QElapsedTimer, QTimer, pyqtProperty
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from agentctl.ui.styles import GLOW_COLORS


class GlowOrbWidget(QOpenGLWidget):
    """GPU-accelerated glowing orb controller rendered via OpenGL GLSL shaders."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(480, 160)

        self.current_state = 0  # IDLE
        self.audio_intensity = 0.0
        self._alpha_scale = 1.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.elapsed = QElapsedTimer()

        self.program: Optional[int] = None
        self.vao: Optional[int] = None
        self.vbo: Optional[int] = None

        # Uniform locations
        self.u_time_loc = -1
        self.u_state_loc = -1
        self.u_intensity_loc = -1
        self.u_color_a_loc = -1
        self.u_color_b_loc = -1
        self.u_color_c_loc = -1
        self.u_resolution_loc = -1
        self.u_alpha_scale_loc = -1

    @pyqtProperty(float)
    def alphaScale(self) -> float:
        return self._alpha_scale

    @alphaScale.setter
    def alphaScale(self, val: float):
        self._alpha_scale = max(0.0, min(1.0, float(val)))
        self.update()

    def set_state(self, state: int):
        self.current_state = state
        self.update()

    def set_intensity(self, intensity: float):
        # Damped smoothing
        self.audio_intensity = 0.7 * self.audio_intensity + 0.3 * intensity
        self.update()

    def stop_timer(self):
        """Stops the 60fps render loop to release CPU/GPU cycles."""
        if self.timer.isActive():
            self.timer.stop()

    def initializeGL(self):
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        # Load shaders
        shader_dir = Path(__file__).parent / "shaders"
        vert_src = (shader_dir / "glow.vert").read_text()
        frag_src = (shader_dir / "glow.frag").read_text()

        # Compile vertex shader
        vert = GL.glCreateShader(GL.GL_VERTEX_SHADER)
        GL.glShaderSource(vert, vert_src)
        GL.glCompileShader(vert)

        # Compile fragment shader
        frag = GL.glCreateShader(GL.GL_FRAGMENT_SHADER)
        GL.glShaderSource(frag, frag_src)
        GL.glCompileShader(frag)

        # Link program
        self.program = GL.glCreateProgram()
        GL.glAttachShader(self.program, vert)
        GL.glAttachShader(self.program, frag)
        GL.glLinkProgram(self.program)

        GL.glDeleteShader(vert)
        GL.glDeleteShader(frag)

        # Uniform locations
        self.u_time_loc = GL.glGetUniformLocation(self.program, "u_time")
        self.u_state_loc = GL.glGetUniformLocation(self.program, "u_state")
        self.u_intensity_loc = GL.glGetUniformLocation(self.program, "u_intensity")
        self.u_color_a_loc = GL.glGetUniformLocation(self.program, "u_color_a")
        self.u_color_b_loc = GL.glGetUniformLocation(self.program, "u_color_b")
        self.u_color_c_loc = GL.glGetUniformLocation(self.program, "u_color_c")
        self.u_resolution_loc = GL.glGetUniformLocation(self.program, "u_resolution")
        self.u_alpha_scale_loc = GL.glGetUniformLocation(self.program, "u_alpha_scale")

        # Full-screen quad
        import numpy as np
        quad_vertices = np.array([
            -1.0, -1.0,
             1.0, -1.0,
            -1.0,  1.0,
            -1.0,  1.0,
             1.0, -1.0,
             1.0,  1.0,
        ], dtype=np.float32)

        self.vao = GL.glGenVertexArrays(1)
        self.vbo = GL.glGenBuffers(1)

        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, quad_vertices.nbytes, quad_vertices, GL.GL_STATIC_DRAW)

        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glBindVertexArray(0)

        self.elapsed.start()
        self.timer.start(16)  # ~60 fps

    def resizeGL(self, w: int, h: int):
        GL.glViewport(0, 0, w, h)

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if not self.program:
            return

        GL.glUseProgram(self.program)

        time_sec = self.elapsed.elapsed() / 1000.0
        GL.glUniform1f(self.u_time_loc, time_sec)
        GL.glUniform1i(self.u_state_loc, self.current_state)
        GL.glUniform1f(self.u_intensity_loc, self.audio_intensity)
        GL.glUniform1f(self.u_alpha_scale_loc, self._alpha_scale)
        GL.glUniform2f(self.u_resolution_loc, float(self.width()), float(self.height()))

        palette = GLOW_COLORS.get(self.current_state, GLOW_COLORS[0])
        GL.glUniform3f(self.u_color_a_loc, *palette["a"])
        GL.glUniform3f(self.u_color_b_loc, *palette["b"])
        GL.glUniform3f(self.u_color_c_loc, *palette["c"])

        GL.glBindVertexArray(self.vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)

