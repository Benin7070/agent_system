// Screen-edge glow. Procedural: no textures, any resolution. Fragment shader body (loader prepends #version).
// Output is PREMULTIPLIED. Colours are the orb's wave colours (orange, pink, blue, cyan) flowing around the screen border.

in vec2 v_uv;
out vec4 frag_color;

uniform vec2  u_res;          // window size in pixels
uniform float u_band;         // base glow thickness in pixels
uniform float u_radius;       // screen corner radius in pixels
uniform float u_intensity;    // overall brightness (0 = nothing)
uniform float u_thick;        // thickness factor (level / state), 1 = base
uniform float u_phase;        // colour flow, in colour cycles
uniform float u_wave;         // strength of the brightness waves travelling along the border, 0..1
uniform vec3  u_tint;         // colour multiplied in ...
uniform float u_tint_amt;     // ... by this much
uniform float u_think;        // 0..1, adds two bright comets circling the border
uniform float u_comet;        // comet position in cycles
uniform float u_flash;        // 0..1 red flash (denied)
uniform float u_sweep;        // opening: bright pulse position in cycles, < 0 = off
uniform float u_appear;       // thickness scale for opening / closing, 0..~1.15
uniform float u_opacity;      // final opacity

const vec3 PAL[4] = vec3[4](vec3(1.00, 0.72, 0.28), vec3(1.00, 0.48, 0.71), vec3(0.35, 0.55, 1.00), vec3(0.44, 0.85, 1.00));
const float K = 2.0;          // colour cycles around the whole border (keep an integer: it makes the seam invisible)
const float TAU = 6.2831853;

vec3 palette(float x) {
    float u = fract(x) * 4.0;
    int i = int(floor(u));
    float f = smoothstep(0.0, 1.0, fract(u));
    return mix(PAL[i], PAL[(i + 1) % 4], f);
}

float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

// everything that varies along the border, as a function of the position s (pixels along the perimeter)
void field(float s, float P, out vec3 col, out float thick, out float arc, out float sw) {
    float x = s / P;
    col = palette(x * K + u_phase);
    float w1 = sin(TAU * (x * 3.0 - u_phase * 1.7));
    float w2 = sin(TAU * (x * 5.0 + u_phase * 1.1) + 1.3);
    thick = 1.0 + u_wave * (0.38 * w1 + 0.22 * w2);
    float d1 = abs(fract(x - u_comet + 0.5) - 0.5);
    float d2 = abs(fract(x - u_comet) - 0.5);
    arc = exp(-pow(d1 / 0.07, 2.0)) + exp(-pow(d2 / 0.07, 2.0));
    float ds = abs(fract(x - u_sweep + 0.5) - 0.5);
    sw = u_sweep >= 0.0 ? exp(-pow(ds / 0.06, 2.0)) : 0.0;
}

// position along the border (pixels) of the point where the ray from the screen centre through p leaves the screen.
// Continuous everywhere inside, so there are no seams along the diagonals or the middle lines.
float border_pos(vec2 p, vec2 hs) {
    vec2 ap = max(abs(p), vec2(1e-3));
    float k = min(hs.x / ap.x, hs.y / ap.y);
    vec2 b = p * k;
    float W = 2.0 * hs.x, H = 2.0 * hs.y;
    float eps = 1e-3;
    if (b.y <= -hs.y + eps && abs(b.x) < hs.x - eps + 2.0 * eps) return b.x + hs.x;                        // top, left to right
    if (b.x >= hs.x - eps)  return W + (b.y + hs.y);                                                     // right, top to bottom
    if (b.y >= hs.y - eps)  return W + H + (hs.x - b.x);                                                 // bottom, right to left
    return 2.0 * W + H + (hs.y - b.y);                                                                   // left, bottom to top
}

void main() {
    vec2 hs = 0.5 * u_res;
    vec2 p = (v_uv - 0.5) * u_res;
    float W = u_res.x, H = u_res.y, P = 2.0 * (W + H);

    // distance (pixels) from the window border, inward, with rounded corners
    float r = min(u_radius, min(hs.x, hs.y));
    vec2 q = abs(p) - hs + r;
    float d = max(-(length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r), 0.0);

    float T = max(u_band * u_thick * u_appear, 1.0);
    if (u_intensity <= 0.0 || u_opacity <= 0.0 || d > T * 6.0) { frag_color = vec4(0.0); return; }

    float s = border_pos(p, hs);
    vec3 col; float thick, arc, sw;
    field(s, P, col, thick, arc, sw);
    arc *= u_think;

    float Tl = max(T * (thick + 0.7 * arc + 0.9 * sw), 1.0);
    float glow = exp(-d / Tl);
    float core = exp(-d / max(0.09 * Tl, 1.0));
    float a = (glow * 0.80 + core * 0.45) * u_intensity * (1.0 + 0.8 * arc + 0.9 * sw);
    a *= 1.0 - smoothstep(Tl * 1.6, Tl * 4.2, d);          // hard fade-out so nothing reaches deep into the screen

    col = mix(col, col * u_tint, u_tint_amt);
    col = mix(col, vec3(1.0, 0.22, 0.20), clamp(u_flash, 0.0, 1.0));
    a *= 1.0 + 1.0 * u_flash;

    a += (hash(gl_FragCoord.xy) - 0.5) / 255.0 * step(0.002, a);      // hides banding in the faint tail
    float al = clamp(a, 0.0, 1.0);
    vec3 rgb = mix(col, vec3(1.0), clamp(core * 0.35, 0.0, 1.0)) * al;
    frag_color = vec4(rgb, al) * u_opacity;
}
