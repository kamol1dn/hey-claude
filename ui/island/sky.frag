#version 440
// The weather card's animated sky (widgets/weather), on the GPU.
// cond: 0 sun, 1 partly, 2 cloud, 3 rain, 4 storm, 5 snow, 6 fog, 7 night, 8 wind.
// The sun has a slowly turning ring of dots, never rays (island-motion.md section 1).
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 size;          // item px
    float t;            // seconds
    float cond;
    float fade;         // entry 0 -> 1
    float flash;        // storm flash 0..1
    float still;        // 1 = reduced motion (no drift)
};

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float disc(vec2 p, vec2 c, float r) { return 1.0 - smoothstep(r - 0.8, r + 0.8, length(p - c)); }
float cloud(vec2 p, vec2 o, float s) {
    float d = 0.0;
    d = max(d, disc(p, o + vec2(0.0, 0.0) * s, 18.0 * s));
    d = max(d, disc(p, o + vec2(22.0, -12.0) * s, 22.0 * s));
    d = max(d, disc(p, o + vec2(46.0, 0.0) * s, 17.0 * s));
    vec2 q = p - (o + vec2(23.0, 6.0) * s);
    d = max(d, 1.0 - smoothstep(0.0, 1.0, max(abs(q.x) - 23.0 * s, abs(q.y) - 12.0 * s)));
    return d;
}

void main() {
    // drawn in a 150 px tall virtual space, scaled to the item
    float k = size.y / 150.0;
    vec2 vs = vec2(size.x / k, 150.0);
    vec2 p = qt_TexCoord0 * vs;
    vec2 size = vs;
    float tt = t * (1.0 - still);
    vec3 col = vec3(0.0);
    float a = 0.0;
    vec2 sc = vec2(size.x * 0.72, size.y * 0.36);
    int c = int(cond + 0.5);

    if (c == 0 || c == 1) {                                          // sun: disc, glow, ring of dots
        float g = exp(-pow(length(p - sc) / 46.0, 2.0)) * 0.30;
        col += vec3(0.925, 0.604, 0.471) * g; a = max(a, g);
        float dsk = disc(p, sc, 22.0);
        col = mix(col, vec3(0.925, 0.604, 0.471), dsk); a = max(a, dsk);
        for (int i = 0; i < 12; i++) {
            float an = float(i) / 12.0 * 6.2832 + tt * 0.15;
            vec2 dp = sc + 38.0 * vec2(cos(an), sin(an));
            float k = disc(p, dp, 2.6) * (0.45 + 0.35 * sin(tt * 1.3 + float(i)));
            col = mix(col, vec3(0.925, 0.604, 0.471), k); a = max(a, k);
        }
        if (c == 1) {
            float cl = cloud(p, vec2(size.x * 0.40 + sin(tt * 0.3) * 10.0, size.y * 0.52), 1.15);
            col = mix(col, vec3(0.95), cl * 0.96); a = max(a, cl * 0.96);
        }
    }
    if (c >= 2 && c <= 6) {                                          // drifting clouds
        for (int i = 0; i < 3; i++) {
            float fi = float(i);
            float x = mod(size.x * 0.25 + fi * 90.0 + tt * (8.0 + fi * 4.0), size.x + 120.0) - 60.0;
            float cl = cloud(p, vec2(x, size.y * 0.22 + fi * 22.0), 1.05 - fi * 0.15);
            float alpha = (c == 4 ? 0.42 : 0.22 + fi * 0.06) * cl;
            col = mix(col, vec3(0.95), alpha); a = max(a, alpha);
        }
    }
    if (c == 3 || c == 4) {                                          // rain streaks
        vec2 cell = vec2(14.0, 26.0);
        vec2 q = p + vec2(-p.y * 0.12, -tt * 430.0);
        vec2 id = floor(q / cell);
        vec2 f = fract(q / cell) * cell;
        float h = hash(id);
        float on = step(0.55, h);
        float x0 = 3.0 + h * 8.0;
        float streak = (1.0 - smoothstep(0.6, 1.4, abs(f.x - x0))) * step(4.0, f.y) * step(f.y, 13.0) * on;
        streak *= smoothstep(size.y * 0.25, size.y * 0.45, p.y);
        col = mix(col, vec3(0.624, 0.761, 0.91), streak * 0.6); a = max(a, streak * 0.6);
        if (c == 4) {
            float fl = flash * 0.35 * exp(-pow(length(p - vec2(size.x * 0.62, size.y * 0.3)) / 70.0, 2.0));
            col += vec3(1.0, 0.94, 0.86) * fl; a = max(a, fl);
        }
    }
    if (c == 5) {                                                    // snow
        vec2 cell = vec2(22.0, 22.0);
        vec2 q = p + vec2(0.0, -tt * 40.0);
        vec2 id = floor(q / cell);
        float h = hash(id);
        vec2 f = fract(q / cell) * cell;
        vec2 ctr = vec2(5.0 + h * 12.0 + sin(tt + h * 6.0) * 4.0, 6.0 + fract(h * 7.3) * 10.0);
        float fl = disc(f, ctr, 1.8) * step(0.35, h) * smoothstep(size.y * 0.2, size.y * 0.4, p.y);
        col = mix(col, vec3(0.95), fl * 0.85); a = max(a, fl * 0.85);
    }
    if (c == 6) {                                                    // fog bands
        for (int i = 0; i < 4; i++) {
            float fi = float(i);
            float y = size.y * 0.32 + fi * 18.0;
            float band = (1.0 - smoothstep(2.0, 4.5, abs(p.y - y))) * (0.08 + 0.03 * fi)
                         * (0.7 + 0.3 * sin(p.x * 0.02 + tt * 0.2 + fi));
            col += vec3(0.95) * band; a = max(a, band);
        }
    }
    if (c == 7) {                                                    // night: crescent and stars
        float moon = disc(p, sc, 20.0) * (1.0 - disc(p, sc + vec2(12.0, -8.0), 17.0));
        col = mix(col, vec3(0.95), moon); a = max(a, moon);
        for (int i = 0; i < 16; i++) {
            float fi = float(i);
            vec2 sp = vec2(size.x * (0.3 + 0.7 * hash(vec2(fi, 1.0))), size.y * 0.6 * hash(vec2(fi, 2.0)));
            float tw = 0.3 + 0.6 * (0.5 + 0.5 * sin(tt * 1.7 + fi * 2.3));
            float st = disc(p, sp, 1.1) * tw;
            col = mix(col, vec3(0.95), st); a = max(a, st);
        }
    }
    if (c == 8) {                                                    // wind: drifting streaks
        for (int i = 0; i < 4; i++) {
            float fi = float(i);
            float y = size.y * 0.25 + fi * 16.0;
            float x = mod(p.x - tt * (40.0 + fi * 12.0), 140.0);
            float s = (1.0 - smoothstep(0.6, 1.3, abs(p.y - y - sin(p.x * 0.05) * 3.0))) * step(x, 60.0);
            col = mix(col, vec3(0.95), s * 0.35); a = max(a, s * 0.35);
        }
    }
    // fade in; fade out toward every edge (more to the left, so the numbers stay readable)
    float edge = smoothstep(0.0, size.x * 0.45, p.x) * (1.0 - smoothstep(size.x * 0.88, size.x, p.x))
               * smoothstep(0.0, 18.0, p.y) * (1.0 - smoothstep(size.y * 0.7, size.y, p.y));
    a *= fade * edge;
    fragColor = vec4(col * fade * edge, a) * qt_Opacity;
}
