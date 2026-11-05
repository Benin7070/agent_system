// Vertex shader body for the screen-edge glow. The loader prepends the #version line.
in vec2 a_pos;          // full-screen quad corners, -1..1
out vec2 v_uv;          // 0..1, origin top-left, y down

void main() {
    v_uv = vec2(a_pos.x * 0.5 + 0.5, 0.5 - a_pos.y * 0.5);
    gl_Position = vec4(a_pos, 0.0, 1.0);
}
