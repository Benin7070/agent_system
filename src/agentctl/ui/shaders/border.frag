#version 330 core
in vec2 v_uv;
out vec4 frag_color;

// ---- uniforms set every frame ----
uniform float u_time;         // seconds since widget was created
uniform float u_level;        // smoothed audio level 0..1
uniform float u_opacity;      // overall fade in/out 0..1
uniform int   u_state;        // 0=idle 1=listening 2=thinking 3=speaking 4=confirm 5=denied
uniform vec3  u_color_a;      // primary hue  (premultiplied)
uniform vec3  u_color_b;      // secondary hue
uniform vec2  u_resolution;   // pixel size of the widget
uniform float u_band_px;      // border bandwidth in pixels (default 90.0)

// ---- constants ----
const float TWO_PI = 6.28318;

// Distance from UV point to nearest screen edge, in pixels
float edge_dist_px(vec2 uv, vec2 res) {
    vec4 d4 = vec4(uv.x, 1.0 - uv.x, uv.y, 1.0 - uv.y) * vec4(res.x, res.x, res.y, res.y);
    return min(min(d4.x, d4.y), min(d4.z, d4.w));
}

// Smooth easing
float ease_out(float x) { return 1.0 - pow(1.0 - clamp(x, 0.0, 1.0), 3.0); }

void main() {
    float d = edge_dist_px(v_uv, u_resolution);

    // ---- state-specific animation parameters ----
    float speed     = 0.5;
    float band      = u_band_px;
    float pulse_amt = 0.0;   // how much u_level expands the band
    float flow_amt  = 0.0;   // how much color flows around the perimeter
    float glow_peak = 0.55;  // max alpha at the very edge

    if (u_state == 0) {          // idle: slow breathing glow
        speed     = 0.35;
        band      = u_band_px * (0.75 + 0.12 * sin(u_time * 0.9));
        glow_peak = 0.35;
    } else if (u_state == 1) {   // listening: reactive cyan pulse
        speed     = 1.1;
        band      = u_band_px * (1.0 + 0.55 * u_level);
        pulse_amt = 0.45;
        flow_amt  = 0.4;
        glow_peak = 0.50 + 0.25 * u_level;
    } else if (u_state == 2) {   // thinking: spinning amber wave
        speed     = 2.2;
        band      = u_band_px * (0.9 + 0.18 * sin(u_time * 2.8 + u_level * TWO_PI));
        flow_amt  = 0.75;
        glow_peak = 0.42;
    } else if (u_state == 3) {   // speaking: reactive warm glow
        speed     = 1.4;
        band      = u_band_px * (0.95 + 0.60 * u_level);
        pulse_amt = 0.35;
        flow_amt  = 0.5;
        glow_peak = 0.52 + 0.22 * u_level;
    } else if (u_state == 4) {   // confirm: steady gold ring
        speed     = 0.6;
        band      = u_band_px * 0.85;
        glow_peak = 0.48 + 0.08 * sin(u_time * 2.0);
    } else if (u_state == 5) {   // denied: red flash
        speed     = 8.0;
        band      = u_band_px * (1.0 + 0.4 * max(0.0, sin(u_time * speed)));
        glow_peak = 0.70 * max(0.0, sin(u_time * speed));
    }

    // ---- perimeter flow: shift hue around the border ----
    // Parameterise the perimeter by angle of v_uv relative to screen centre
    float angle = atan(v_uv.y - 0.5, v_uv.x - 0.5);    // -PI .. PI
    float flow  = 0.5 + 0.5 * sin(angle * 2.0 - u_time * speed);
    vec3  color = mix(u_color_a, u_color_b, flow * flow_amt + (1.0 - flow_amt) * 0.5);

    // ---- Gaussian falloff from edge ----
    if (d > band * 2.5) {
        frag_color = vec4(0.0);
        return;
    }
    float alpha = glow_peak * exp(-d * d / (band * band * 0.5));
    alpha = clamp(alpha, 0.0, 0.92) * u_opacity;

    // Premultiply
    frag_color = vec4(color * alpha, alpha);
}

