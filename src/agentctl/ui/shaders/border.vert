#version 330 core
layout(location = 0) in vec2 a_pos;
out vec2 v_uv;

void main() {
    // NDC quad: a_pos in [-1, 1]. Convert to [0, 1] UV for the fragment shader.
    v_uv = a_pos * 0.5 + 0.5;
    gl_Position = vec4(a_pos, 0.0, 1.0);
}

