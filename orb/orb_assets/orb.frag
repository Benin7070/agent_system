// Fragment shader body. The loader prepends the #version line (see README).
// All textures hold PREMULTIPLIED colour. The output is premultiplied too.
// Stage coordinates are Lottie comp units (1080 x 1080); the orb disc has a radius of about 390.

in vec2 v_pos;
out vec4 frag_color;

uniform sampler2D u_base;     // dark disc + glass rim (static)
uniform sampler2D u_top;      // soft white highlights, alpha only (blended 'overlay')
uniform sampler2D u_atlas;    // all wave frames in a grid

uniform float u_scale;        // orb size, 1 = original
uniform float u_opacity;      // orb opacity 0..1
uniform vec2  u_shift;        // sideways shake in comp units
uniform float u_amp;          // wave height around the baseline, 1 = original
uniform vec3  u_tint;         // colour multiplied into the orb ...
uniform float u_tint_amt;     // ... by this much (0..1)
uniform float u_glow;         // soft halo around the orb
uniform vec3  u_glow_color;
uniform float u_frame_a;      // wave frame index (two neighbours, cross-faded)
uniform float u_frame_b;
uniform float u_frame_mix;
uniform float u_fx;           // opening effects clock in 60 fps frames, < 0 = off
uniform vec2  u_cell;         // atlas cell size in texels
uniform float u_cols;         // atlas columns
uniform vec2  u_atlas_size;   // atlas size in texels
uniform vec4  u_crop;         // wave crop box in comp units: x, y, w, h
uniform float u_baseline;     // comp y of the wave centre line

const vec2 CENTER = vec2(540.0, 540.0);

// ---- opening sparks: (angle, end radius, start frame, end frame) and (r, g, b, size)
const vec4 SPARK_A[12] = vec4[12](
    vec4(-0.0553, 468.3, 15.0, 42.0),
    vec4(0.6245, 465.2, 14.0, 42.0),
    vec4(0.9576, 464.7, 13.0, 43.0),
    vec4(1.4422, 483.3, 16.0, 51.0),
    vec4(2.2350, 494.7, 14.0, 42.0),
    vec4(2.6449, 462.7, 11.0, 42.0),
    vec4(3.2542, 475.9, 11.0, 50.0),
    vec4(3.6875, 490.8, 15.0, 44.0),
    vec4(4.2144, 495.1, 12.0, 43.0),
    vec4(4.7790, 491.0, 14.0, 45.0),
    vec4(5.2927, 483.5, 12.0, 49.0),
    vec4(5.8926, 479.9, 11.0, 44.0)
);
const vec4 SPARK_C[12] = vec4[12](
    vec4(1.00, 0.72, 0.28, 14.0),
    vec4(0.44, 0.85, 1.00, 26.0),
    vec4(1.00, 0.48, 0.71, 14.0),
    vec4(0.35, 0.55, 1.00, 14.0),
    vec4(1.00, 1.00, 1.00, 26.0),
    vec4(1.00, 0.72, 0.28, 26.0),
    vec4(0.44, 0.85, 1.00, 14.0),
    vec4(1.00, 0.48, 0.71, 14.0),
    vec4(0.35, 0.55, 1.00, 26.0),
    vec4(1.00, 1.00, 1.00, 20.0),
    vec4(1.00, 0.72, 0.28, 26.0),
    vec4(0.44, 0.85, 1.00, 26.0)
);

float ease_out(float x) { x = clamp(x, 0.0, 1.0); return 1.0 - pow(1.0 - x, 3.6); }
float ease_inout(float x) { x = clamp(x, 0.0, 1.0); return x * x * (3.0 - 2.0 * x); }
float seg(float t, float a, float b) { return clamp((t - a) / (b - a), 0.0, 1.0); }

// premultiplied 'over'
vec4 over(vec4 top, vec4 under) { return top + under * (1.0 - top.a); }

// one wave frame at stage position q (already vertically un-stretched)
vec4 wave_frame(float frame, vec2 q) {
    vec2 local = (q - u_crop.xy) / u_crop.zw;
    if (local.x < 0.0 || local.x > 1.0 || local.y < 0.0 || local.y > 1.0) return vec4(0.0);
    float col = mod(frame, u_cols);
    float row = floor(frame / u_cols);
    vec2 px = clamp(local * u_cell, vec2(0.5), u_cell - 0.5);
    return texture(u_atlas, (vec2(col, row) * u_cell + px) / u_atlas_size);
}

vec4 orb_layer(vec2 p) {
    vec2 q = (p - CENTER - u_shift) / max(u_scale, 0.001) + CENTER;
    vec2 uv = q / 1080.0;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) return vec4(0.0);

    vec4 base = texture(u_base, uv);

    // waves: stretched vertically around the baseline by u_amp
    vec2 qw = vec2(q.x, u_baseline + (q.y - u_baseline) / max(u_amp, 0.05));
    vec4 wv = mix(wave_frame(u_frame_a, qw), wave_frame(u_frame_b, qw), u_frame_mix);
    vec4 o = wv + base * (1.0 - wv.a);

    // white highlights, 'overlay' blend
    float ta = texture(u_top, uv).a;
    vec3 c1 = o.a > 1e-4 ? o.rgb / o.a : vec3(0.0);
    vec3 ov = mix(2.0 * c1, vec3(1.0), step(0.5, c1));
    vec3 pr = ta * (1.0 - o.a) + ta * o.a * ov + (1.0 - ta) * o.rgb;
    vec4 orb = vec4(pr, ta + o.a * (1.0 - ta));

    orb.rgb = mix(orb.rgb, orb.rgb * u_tint, u_tint_amt);
    return orb * u_opacity;
}

vec4 effects(vec2 p) {
    vec4 acc = vec4(0.0);
    float d = length(p - CENTER);

    // soft halo (confirm / speaking)
    if (u_glow > 0.001) {
        float h = exp(-pow(max(d - 360.0, 0.0) / 110.0, 1.4)) * u_glow * 0.55;
        acc = over(vec4(u_glow_color * h, h), acc);
    }

    if (u_fx >= 0.0) {
        float t = u_fx;

        // bloom flash behind the orb
        float bs = t < 34.0 ? mix(0.50, 1.05, ease_out(t / 34.0)) : mix(1.05, 1.25, seg(t, 34.0, 96.0));
        float env = t < 7.0 ? ease_out(t / 7.0) : 1.0 - seg(t, 7.0, 56.0);
        float r = d / (540.0 * bs);
        vec3 bc = r < 0.5 ? mix(vec3(0.72, 0.82, 1.0), vec3(0.55, 0.45, 1.0), r / 0.5)
                          : mix(vec3(0.55, 0.45, 1.0), vec3(1.0, 0.5, 0.8), clamp((r - 0.5) / 0.5, 0.0, 1.0));
        float ba = (r < 0.55 ? mix(0.55, 0.22, r / 0.55) : mix(0.22, 0.0, clamp((r - 0.55) / 0.45, 0.0, 1.0))) * env;
        acc = over(vec4(bc * ba, ba), acc);

        // glowing ring
        if (t >= 8.0 && t <= 54.0) {
            float k = seg(t, 8.0, 54.0);
            float R = 0.5 * mix(740.0, 1040.0, ease_out(k));
            float w = mix(70.0, 8.0, ease_out(k));
            float a = 0.70 * (1.0 - k);
            float g = a * exp(-pow(abs(d - R) / (0.5 * w + 18.0), 2.0));
            acc = over(vec4(vec3(0.45, 0.75, 1.0) * g, g), acc);
        }
        // crisp ring
        if (t >= 8.0 && t <= 44.0) {
            float k = seg(t, 8.0, 44.0);
            float R = 0.5 * mix(760.0, 1060.0, ease_out(k));
            float w = mix(24.0, 3.0, ease_out(k));
            float a = 1.0 - k;
            float g = a * (1.0 - smoothstep(0.5 * w - 1.0, 0.5 * w + 1.5, abs(d - R)));
            acc = over(vec4(vec3(g), g), acc);
        }

        // sparks
        for (int i = 0; i < 12; i++) {
            vec4 sa = SPARK_A[i];
            vec4 sc = SPARK_C[i];
            if (t < sa.z || t > sa.w) continue;
            float k = seg(t, sa.z, sa.w);
            float mid = 0.45;
            float rad = mix(360.0, sa.y, ease_out(k));
            vec2 sp = CENTER + vec2(cos(sa.x), sin(sa.x)) * rad;
            float sz = (k < mid ? mix(0.6, 1.1, ease_inout(k / mid)) : mix(1.1, 0.0, ease_inout((k - mid) / (1.0 - mid)))) * sc.w * 0.5;
            float al = min(1.0, (t - sa.z) / 3.0) * (k < mid ? 1.0 : 1.0 - ease_inout((k - mid) / (1.0 - mid)));
            float dot_a = al * (1.0 - smoothstep(sz - 1.5, sz + 0.8, length(p - sp)));
            acc = over(vec4(sc.rgb * dot_a, dot_a), acc);
        }
    }
    return acc;
}

void main() {
    vec4 orb = orb_layer(v_pos);
    vec4 fx = effects(v_pos);
    frag_color = over(orb, fx);
}
