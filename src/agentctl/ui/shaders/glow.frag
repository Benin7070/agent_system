#version 330 core
in vec2 v_uv;
out vec4 fragColor;

uniform float u_time;
uniform int u_state;
uniform float u_intensity;
uniform vec3 u_color_a;
uniform vec3 u_color_b;
uniform vec3 u_color_c;
uniform vec2 u_resolution;
uniform float u_alpha_scale;

void main() {
    // Aspect-corrected coordinate space around center (0, 0)
    vec2 st = (gl_FragCoord.xy - 0.5 * u_resolution) / min(u_resolution.x, u_resolution.y);

    // State parameters
    float speed = 0.5;
    float aura_opacity = 0.28;
    float radius = 0.22;
    float ring_opacity = 0.0;
    float shake_offset = 0.0;

    if (u_state == 0) { // IDLE (breathing)
        speed = 0.4;
        radius = 0.22 * (1.0 + 0.06 * sin(u_time * 1.2));
        aura_opacity = 0.28;
    } else if (u_state == 1) { // LISTENING (reactive)
        speed = 1.2;
        radius = 0.22 * (1.0 + 0.25 * u_intensity);
        aura_opacity = 0.42 + 0.15 * u_intensity;
        ring_opacity = 0.18;
    } else if (u_state == 2) { // PROCESSING (spinning aurora)
        speed = 2.0;
        radius = 0.22;
        aura_opacity = 0.38;
        ring_opacity = 0.25;
    } else if (u_state == 3) { // SUCCESS
        radius = 0.24;
        aura_opacity = 0.65;
    } else if (u_state == 4) { // ERROR
        shake_offset = sin(u_time * 40.0) * 0.015;
        st.x += shake_offset;
        radius = 0.22;
        aura_opacity = 0.60;
    } else if (u_state == 5) { // CANCEL
        radius = 0.16;
        aura_opacity = 0.20;
    }

    float dist = length(st);

    // 1. Core orb SDF
    float core = 1.0 - smoothstep(radius * 0.4, radius, dist);

    // 2. 3 Lissajous metaball color blobs
    vec2 b1 = vec2(sin(u_time * speed * 0.9) * 0.07, cos(u_time * speed * 1.1) * 0.07);
    vec2 b2 = vec2(cos(u_time * speed * 1.3 + 1.5) * 0.08, sin(u_time * speed * 0.8 + 2.0) * 0.08);
    vec2 b3 = vec2(sin(u_time * speed * 1.4 + 3.0) * 0.06, cos(u_time * speed * 1.2 + 4.0) * 0.06);

    float d1 = length(st - b1);
    float d2 = length(st - b2);
    float d3 = length(st - b3);

    float w1 = 1.0 - smoothstep(0.0, radius * 1.2, d1);
    float w2 = 1.0 - smoothstep(0.0, radius * 1.2, d2);
    float w3 = 1.0 - smoothstep(0.0, radius * 1.2, d3);

    vec3 blob_color = u_color_a * w1 + u_color_b * w2 + u_color_c * w3;
    blob_color = normalize(blob_color + vec3(0.05));

    // 3. Specular highlight (upper-left glass reflection)
    vec2 spec_pos = vec2(-radius * 0.35, radius * 0.35);
    float spec_dist = length(st - spec_pos);
    float specular = (1.0 - smoothstep(0.0, radius * 0.35, spec_dist)) * 0.60;

    // 4. Outer Gaussian aura / bloom
    float aura = exp(-dist * dist / (2.0 * radius * radius * 1.2)) * aura_opacity;

    // 5. Optional delicate outer aura ring in listening/processing
    if (ring_opacity > 0.0) {
        float ring_dist = abs(dist - radius * 1.4);
        float ring = (1.0 - smoothstep(0.0, 0.02, ring_dist)) * ring_opacity;
        aura += ring;
    }

    vec3 final_color = blob_color * core + vec3(specular) + blob_color * aura;
    float final_alpha = clamp(core * 0.90 + aura + specular * 0.8, 0.0, 1.0);

    float scale = clamp(u_alpha_scale, 0.0, 1.0);
    fragColor = vec4(final_color * scale, final_alpha * scale);
}

