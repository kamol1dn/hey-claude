#version 440
// Ember & Thread: pill body, rim, inner glow, followup wash, heat rings, the
// thread and the ember in one pass (docs/island-motion.md section 8).
// Compiled by tools/build_shaders.py into island.frag.qsb (HLSL 50 for D3D11,
// GLSL for OpenGL, MSL); the item covers the pill's bounding box only.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 size;                          // item px
    vec4 pill;                          // x, y, w, h (px, in the item)
    vec4 ember;                         // x, y, r, coreMix
    vec4 emberCol;                      // rgb, alpha
    vec4 coreCol;
    vec4 glow;                          // r, alpha, washX (< 0: none), washA
    vec4 glowCol;
    vec4 rimCol;                        // rgb, alpha
    vec4 ring0; vec4 ring1; vec4 ring2; // x, y, r, alpha
    vec4 ringW;                         // stroke width of each ring
    vec4 thr;                           // strokeW, alpha, count (<= 48), aaPx
    vec4 thrCol;
    vec4 thrBox;                        // thread bounding box: x0, y0, x1, y1 (incl. stroke)
    vec4 prog;                          // loading progress wash: x (px), alpha
    vec4 p0; vec4 p1; vec4 p2; vec4 p3; vec4 p4; vec4 p5; vec4 p6; vec4 p7;
    vec4 p8; vec4 p9; vec4 p10; vec4 p11; vec4 p12; vec4 p13; vec4 p14; vec4 p15;
    vec4 p16; vec4 p17; vec4 p18; vec4 p19; vec4 p20; vec4 p21; vec4 p22; vec4 p23;
    vec4 a0; vec4 a1; vec4 a2; vec4 a3; vec4 a4; vec4 a5;
    vec4 a6; vec4 a7; vec4 a8; vec4 a9; vec4 a10; vec4 a11;
};

float sdPill(vec2 q, vec2 b, float r) {
    vec2 d = abs(q) - b + r;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - r;
}
float sdSeg(vec2 p, vec2 a, vec2 b) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-4), 0.0, 1.0);
    return length(pa - ba * h);
}
float ringA(vec2 p, vec4 r, float w) {
    if (r.w <= 0.001) return 0.0;
    float d = abs(length(p - r.xy) - r.z);
    return r.w * (1.0 - smoothstep(w * 0.5 - 0.5, w * 0.5 + 0.5, d));
}

void main() {
    vec2 p = qt_TexCoord0 * size;
    vec2 c = pill.xy + pill.zw * 0.5;
    float rad = min(pill.w * 0.5, glowCol.a);       // corner radius rides in glowCol.a
    float d = sdPill(p - c, pill.zw * 0.5, rad);
    float aa = thr.w;
    float body = 1.0 - smoothstep(-aa, aa, d);
    if (body <= 0.0) { fragColor = vec4(0.0); return; }

    vec3 col = vec3(0.031);
    float gl = length(p - ember.xy) / max(glow.x, 1.0);
    float g = exp(-gl * gl) * glow.y;                                   // inner glow
    float wash = glow.z < 0.0 ? 0.0 : glow.w * (1.0 - smoothstep(glow.z - 18.0, glow.z, p.x));
    float pw = prog.y * (1.0 - step(prog.x, p.x));
    col += glowCol.rgb * (g + wash + pw);
    col += glowCol.rgb * (ringA(p, ring0, ringW.x) + ringA(p, ring1, ringW.y) + ringA(p, ring2, ringW.z));

    // thread: distance to the polyline, per-segment alpha (min of its ends)
    if (thr.y > 0.004 && p.x >= thrBox.x && p.x <= thrBox.z && p.y >= thrBox.y && p.y <= thrBox.w) {
        vec4 P[24] = vec4[24](p0, p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11,
                              p12, p13, p14, p15, p16, p17, p18, p19, p20, p21, p22, p23);
        vec4 A[12] = vec4[12](a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11);
        float t = 0.0;
        int n = int(thr.z) - 1;
        for (int i = 0; i < 47; i++) {
            if (i >= n) break;
            vec4 q = P[i / 2];
            vec4 qn = P[(i + 1) / 2];
            vec2 pa = (i % 2 == 0) ? q.xy : q.zw;
            vec2 pb = ((i + 1) % 2 == 0) ? qn.xy : qn.zw;
            float al = min(A[i / 4][i % 4], A[(i + 1) / 4][(i + 1) % 4]);
            float sd = sdSeg(p, pa, pb);
            t = max(t, al * (1.0 - smoothstep(thr.x * 0.5 - aa, thr.x * 0.5 + aa, sd)));
        }
        col = mix(col, thrCol.rgb, t * thr.y * thrCol.a);
    }

    float e = length(p - ember.xy);                                     // ember disc + hot core
    float disc = 1.0 - smoothstep(ember.z - aa, ember.z + aa, e);
    vec3 ec = mix(emberCol.rgb, coreCol.rgb, ember.w * (1.0 - smoothstep(0.0, ember.z * 0.75, e)));
    col = mix(col, ec, disc * emberCol.a);

    float rim = (1.0 - smoothstep(0.0, 1.2, abs(d + 0.6))) * rimCol.a;   // 1 px hairline
    col += rimCol.rgb * rim;
    float a = body * 0.97;
    fragColor = vec4(col * a, a) * qt_Opacity;                           // premultiplied
}
