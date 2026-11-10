.pragma library
// Tycho, the one-eyed orrery. Ported from docs/character-options.html (const Tycho), plus
// the sleep states (drowsy, asleep, waking), woke, approval_needed and its idle antics.
// make(K) gets the character kit (ui/qml/HeyClaude/Character/kit.js).
function make(K) {
const { C, TAU, clamp, lerp, mixc, rgba, easeOutCubic, easeInCubic, easeInOut, easeOutBack, heat, breath, hash, drawCheck, drawParts, rnd } = K;
function p_(s, k) { return s.p[k]; }

const INK = [44, 20, 12], MOON = [242, 230, 222], EYE = [250, 244, 238], PUPIL = [26, 16, 12], NIGHT = [12, 10, 26];
const COPPER = [196, 86, 50], BRASS = [214, 170, 98], BRASS_D = [128, 92, 50], LENS = [160, 214, 240];
const AUR_G = [96, 230, 170], AUR_V = [156, 128, 246], STAR = [255, 242, 220], PANEL = [92, 128, 186], SHADE = [30, 26, 38];
const PARK = [[-15, 28], [0, 31], [15, 28]];
const R = 22;                                            // the planet

// timing helpers (u = 0..1 through an antic)
const W = (u, a, b) => clamp((u - a) / (b - a));                                // 0..1 across [a, b]
const bump = (u, a, b) => Math.sin(Math.PI * W(u, a, b));                      // 0 -> 1 -> 0 across [a, b]
const env = (u, i, o) => Math.min(easeInOut(u / i), easeInOut((1 - u) / o));  // ramp in, hold, ramp out
const keys = (u, k) => {                                                        // eased keyframes [[u, v], ...]
    if (u <= k[0][0]) return k[0][1];
    for (let i = 1; i < k.length; i++)
        if (u <= k[i][0]) return lerp(k[i - 1][1], k[i][1], easeInOut((u - k[i - 1][0]) / (k[i][0] - k[i - 1][0])));
    return k[k.length - 1][1];
};
const cyOf = s => 2 + (s.p.py || 0);

// ---- shared drawing bits ------------------------------------------------------------
// a four-point twinkle, never smaller than ~2 px on screen
function star(o, x, y, r, a, col, minPx) {
    if (a <= 0.01) return;
    const ctx = o.ctx; r = Math.max(r, o.mw(minPx || 2.2));
    const k = r * 0.16;
    ctx.fillStyle = rgba(col || STAR, a);
    ctx.beginPath(); ctx.moveTo(x, y - r);
    ctx.quadraticCurveTo(x + k, y - k, x + r, y); ctx.quadraticCurveTo(x + k, y + k, x, y + r);
    ctx.quadraticCurveTo(x - k, y + k, x - r, y); ctx.quadraticCurveTo(x - k, y - k, x, y - r);
    ctx.closePath(); ctx.fill();
}
function dot(o, x, y, r, col, a) {
    const ctx = o.ctx; ctx.fillStyle = rgba(col, a); ctx.beginPath(); ctx.arc(x, y, Math.max(r, o.mw(0.7)), 0, TAU); ctx.fill();
}
function glowDot(o, x, y, r0, r1, col, a) {
    const ctx = o.ctx, g = ctx.createRadialGradient(x, y, r0, x, y, r1);
    g.addColorStop(0, rgba(col, a)); g.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r1, 0, TAU); ctx.fill();
}
// the "caught!" pop: a quick ring off each moon as it snaps back into orbit
function popRings(o, s, u, idx) {
    const ctx = o.ctx, mp = s.cs.mpos; if (!mp) return;
    for (const i of idx) {
        const m = mp[i]; if (!m) continue;
        ctx.strokeStyle = rgba(C.fg, 0.75 * (1 - u)); ctx.lineWidth = o.lw(1.3, 0.8);
        ctx.beginPath(); ctx.arc(m.x, m.y, m.r + 1.5 + 7 * easeOutCubic(u), 0, TAU); ctx.stroke();
    }
}
function blinkAwake(s) { if (!s.calm) s.blinkT = 0; }

// the spyglass: from the eye's rim along th, len units out (0..24)
function drawTube(o, s, th, len, alpha) {
    if (len <= 0.3 || alpha <= 0.01) return;
    const ctx = o.ctx, cy = cyOf(s), c = Math.cos(th), sn = Math.sin(th);
    const ax = (s.p.px || 0) + 8.5 * c, ay = cy - 3 + 8.5 * sn;
    const P = d => [ax + c * d, ay + sn * d];
    const segs = [[0, 9, 2.6], [7, 16, 3.2], [14, 24, 3.9]];
    ctx.lineCap = 'butt';
    for (const [d0, d1, r] of segs) {
        if (len <= d0) break;
        const e = Math.min(len, d1), a = P(d0), b = P(e), w = Math.max(2 * r, o.mw(2.2));
        ctx.strokeStyle = rgba(BRASS_D, alpha); ctx.lineWidth = w + o.mw(0.8);
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        ctx.strokeStyle = rgba(BRASS, alpha); ctx.lineWidth = w;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        if (!o.small) {   // a highlight along the top of each draw-tube
            ctx.strokeStyle = rgba([255, 236, 190], 0.55 * alpha); ctx.lineWidth = w * 0.25;
            const off = -w * 0.22;
            ctx.beginPath(); ctx.moveTo(a[0] - sn * off, a[1] + c * off); ctx.lineTo(b[0] - sn * off, b[1] + c * off); ctx.stroke();
        }
    }
    // a dark band at each joint and the eyepiece, a wider dew cap at the far end
    const band = (d, r) => { const a = P(d), b = P(d + 1.4); ctx.strokeStyle = rgba(BRASS_D, alpha); ctx.lineWidth = Math.max(2 * r + 0.8, o.mw(2.6));
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
    band(0, 2.6); if (len > 8) band(7, 3.2); if (len > 15) band(14, 3.9);
    if (len > 20) { const a = P(len - 3), b = P(len); ctx.strokeStyle = rgba(BRASS_D, alpha); ctx.lineWidth = Math.max(9.6, o.mw(2.8));
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
    // the objective lens at the tip
    const tip = P(len), rr = len > 20 ? 4.8 : len > 14 ? 3.9 : len > 7 ? 3.2 : 2.6;
    ctx.save(); ctx.translate(tip[0], tip[1]); ctx.rotate(th);
    ctx.fillStyle = rgba(LENS, alpha); ctx.beginPath(); K.ellipse(ctx, 0, 0, Math.max(1.1, o.mw(0.8)), Math.max(rr, o.mw(1.1)), 0, 0, TAU); ctx.fill();
    ctx.restore();
}

// the aurora: curtains of light over the planet's north pole
function drawAurora(o, s, e, T) {
    if (e <= 0.01) return;
    const ctx = o.ctx, cy = cyOf(s), px = s.p.px || 0, N = o.small ? 12 : 22;
    ctx.lineCap = 'round';
    for (let i = 0; i < N; i++) {
        const q = i / (N - 1), x = -25 + 50 * q;
        const base = cy - Math.sqrt(Math.max(0, 33 * 33 - x * x)) - 1;
        const h = 0.8 * (13 + 7 * Math.sin(x * 0.14 + T * 1.6) + 4 * Math.sin(x * 0.33 - T * 2.2)) * (0.55 + 0.45 * Math.sin(Math.PI * q)) * (0.6 + 0.4 * e);
        const sway = 3 * Math.sin(T * 1.1 + q * 4);
        const a = e * (0.22 + 0.16 * Math.sin(i * 1.7 + T * 3.1)) * (0.4 + 0.6 * Math.sin(Math.PI * q));
        const g = ctx.createLinearGradient(px + x, base, px + x + sway, base - h);
        g.addColorStop(0, rgba(AUR_G, 0));
        g.addColorStop(0.12, rgba(AUR_G, a * 1.6));
        g.addColorStop(0.6, rgba(mixc(AUR_G, AUR_V, 0.6), a));
        g.addColorStop(1, rgba(AUR_V, 0));
        ctx.strokeStyle = g; ctx.lineWidth = o.lw(o.small ? 6 : 3.6, 1.2);
        ctx.beginPath(); ctx.moveTo(px + x, base); ctx.quadraticCurveTo(px + x + sway * 0.2, base - h * 0.5, px + x + sway, base - h); ctx.stroke();
    }
}

// the satellite: a gold box, two blue panels and a blinking red light
function drawSat(o, x, y, rot, a, T) {
    if (a <= 0.01) return;
    const ctx = o.ctx;
    ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
    const ph = o.small ? 4.6 : 3.8, pw = 9;
    ctx.strokeStyle = rgba(C.dim, a); ctx.lineWidth = o.lw(0.8, 0.6);
    ctx.beginPath(); ctx.moveTo(-3 - 1.5, 0); ctx.lineTo(3 + 1.5, 0); ctx.stroke();
    for (const sd of [-1, 1]) {
        const x0 = sd < 0 ? -4.5 - pw : 4.5;
        ctx.fillStyle = rgba(PANEL, a); ctx.fillRect(x0, -ph / 2, pw, ph);
        if (!o.small) {
            ctx.strokeStyle = rgba([40, 60, 100], a); ctx.lineWidth = 0.5;
            ctx.beginPath(); for (let k = 1; k < 3; k++) { ctx.moveTo(x0 + pw * k / 3, -ph / 2); ctx.lineTo(x0 + pw * k / 3, ph / 2); } ctx.stroke();
        }
    }
    ctx.fillStyle = rgba(BRASS, a); ctx.fillRect(-3, -2.6, 6, 5.2);
    if (!o.small) { ctx.fillStyle = rgba([255, 230, 170], 0.6 * a); ctx.fillRect(-3, -2.6, 6, 1.3); }
    ctx.strokeStyle = rgba(C.fg, a * 0.8); ctx.lineWidth = o.lw(0.7, 0.6);
    ctx.beginPath(); ctx.moveTo(0, -2.6); ctx.lineTo(0, -6); ctx.stroke();
    const on = (T * 1.4) % 1 < 0.35;
    dot(o, 0, -6.3, on ? 1.3 : 0.8, on ? [255, 70, 60] : [120, 40, 40], a);
    ctx.restore();
}

// the constellation: Tycho's own portrait in seven stars
const CON = (() => {
    const cx = -64, cy = -50, r = 12, pts = [];
    for (let k = 0; k < 5; k++) { const an = -Math.PI / 2 + k * TAU / 5 + 0.2; pts.push([cx + r * Math.cos(an), cy + r * Math.sin(an)]); }
    const tl = [cx - 25, cy + 7], tr = [cx + 25, cy - 7];
    // pen moves: [from, to, draw?]
    const moves = [[0, 1, 1], [1, 2, 1], [2, 3, 1], [3, 4, 1], [4, 0, 1], [0, 5, 0], [5, 6, 1]];
    return { stars: pts.concat([tl, tr]), moves: moves };
})();
function conPen(u) {               // where the pen-moon is, and how much is drawn
    const n = CON.moves.length, q = W(u, 0.16, 0.8) * n, k = Math.min(n - 1, Math.floor(q)), f = easeInOut(q - k);
    const [a, b] = CON.moves[k], A = CON.stars[a], B = CON.stars[b];
    return { x: lerp(A[0], B[0], f), y: lerp(A[1], B[1], f), k: q >= n ? n : k, f: q >= n ? 1 : f };
}
function drawCon(o, prog, a, glow, T) {
    if (a <= 0.01) return;
    const ctx = o.ctx;
    ctx.lineCap = 'round'; ctx.strokeStyle = rgba(C.fg, (0.5 + 0.35 * glow) * a); ctx.lineWidth = o.lw(0.9 + 0.5 * glow, 0.8);
    for (let i = 0; i < CON.moves.length; i++) {
        const [m0, m1, draw] = CON.moves[i]; if (!draw || i > prog.k) continue;
        const A = CON.stars[m0], B = CON.stars[m1], f = i < prog.k ? 1 : prog.f;
        if (f <= 0) continue;
        ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(lerp(A[0], B[0], f), lerp(A[1], B[1], f)); ctx.stroke();
    }
    CON.stars.forEach((p, i) => star(o, p[0], p[1], 2.6 + 1.6 * glow + 0.5 * Math.sin(T * 3 + i * 1.9), a * (0.75 + 0.25 * Math.sin(T * 2.3 + i))));
}

// the far side: freckles all round, a crater, and a little flag someone left there
const SPOTS = [[0.9, 0.35, 2.2], [1.8, -0.5, 1.5], [2.5, 0.2, 2.8], [3.7, 0.55, 1.7], [4.4, -0.25, 2.4], [5.4, 0.4, 1.6]];
function drawSurface(o, lon, w, col) {
    const ctx = o.ctx, dark = mixc(col, [40, 20, 14], 0.55);
    const proj = (L, lat) => {
        const rel = L + lon, cl = Math.cos(lat);
        return { x: R * cl * Math.sin(rel), y: R * Math.sin(lat), k: Math.cos(rel), sh: cl };
    };
    for (const [L, lat, r] of SPOTS) {
        const q = proj(L, lat); if (q.k <= 0.02) continue;
        ctx.fillStyle = rgba(dark, 0.45 * w * Math.min(1, q.k * 3));
        ctx.beginPath(); K.ellipse(ctx, q.x, q.y, r * q.k, r * 0.9, 0, 0, TAU); ctx.fill();
    }
    // the crater
    const c = proj(Math.PI + 0.55, 0.38);
    if (c.k > 0.02) {
        const a = w * Math.min(1, c.k * 3);
        ctx.fillStyle = rgba(dark, 0.55 * a); ctx.beginPath(); K.ellipse(ctx, c.x, c.y, 4.6 * c.k, 3.6, 0, 0, TAU); ctx.fill();
        ctx.strokeStyle = rgba(heat(1.05), 0.6 * a); ctx.lineWidth = o.lw(0.9, 0.6);
        ctx.beginPath(); K.ellipse(ctx, c.x, c.y + 0.4, 4.6 * c.k, 3.6, 0, 0.1, Math.PI - 0.1); ctx.stroke();
    }
    // the flag
    const f = proj(Math.PI - 0.2, -0.3);
    if (f.k > 0.02) {
        const a = w * Math.min(1, f.k * 3);
        ctx.strokeStyle = rgba(INK, 0.9 * a); ctx.lineWidth = o.lw(1, 0.8);
        ctx.beginPath(); ctx.moveTo(f.x, f.y + 1); ctx.lineTo(f.x, f.y - 9); ctx.stroke();
        ctx.fillStyle = rgba(C.fg, a);
        ctx.beginPath(); ctx.moveTo(f.x, f.y - 9); ctx.lineTo(f.x + 6.5 * f.k, f.y - 7); ctx.lineTo(f.x, f.y - 5); ctx.closePath(); ctx.fill();
    }
}

// ---- the character --------------------------------------------------------------------
const Tycho = {
  id: 'tycho', name: 'Tycho', kind: 'one-eyed orrery',
  line: 'A small planet with one curious eye and three moons. The moons do the fidgeting, so the eye can stay calm.',
  oneshot: { happy: 2.9, confused: 2.8, sending: 3.2, waking: 1.6, woke: 0.9 },
  enter(s, st) {
    const cs = s.cs; cs.burst = 0; cs.shot = 0;
    if (!cs.mo) { cs.mo = [0, 0, 0]; cs.ma = [1, 1, 1]; cs.mr = [1, 1, 1]; }
    if (!cs.mt) { cs.mt = [null, null, null]; cs.mtc = [MOON, MOON, MOON]; }
    if (st !== 'sending') { cs.ma[0] = Math.max(cs.ma[0], 0.01); }
    if (st === 'asleep') cs.zT = 1.4;
  },
  update(s, dt) {
    const st = s.state, t = s.t, cs = s.cs, calm = s.calm, L = s.L, A = s.A;
    const g = { spd: TAU / 20, rx: 40, tilt: 0.3, rot: -0.2 + s.look.x * 0.18, temp: 0.72, open: 1, dil: 1, ok: 0, bad: 0, py: 0, park: 0,
                ringA: 0.26, happy: 0, mouth: 0, sx: 1, sy: 1, lid: 0, night: 0, esc: 1, px: 0, rox: 0, roy: 0, ringC: 0, lon: 0, surf: 0,
                glow: 0, lx: null, ly: null, mt: [null, null, null], mc: [0, 0, 0], mcc: null, msc: [1, 1, 1] };
    switch (st) {
      case 'idle': g.open = 1 + s.notice * 0.15; g.dil = 1 + s.near * 0.25 + s.notice * 0.2; if (calm) g.spd = 0; break;
      case 'off': g.spd = 0; g.temp = 0.02; g.open = 0; g.park = 1; g.ringA = 0.07; if (s.near > 0.6) g.open = 0.32; break;
      case 'listening': g.rx = 40 - 8 * L; g.dil = 1 + 0.6 * L; g.open = 1.08; g.temp = 0.78 + 0.3 * L; g.ringA = 0.3 + 0.25 * L; g.spd = TAU / 14; break;
      case 'thinking': g.spd = TAU / 2.2; g.rot = -0.2 + 0.28 * Math.sin(TAU * 0.35 * t); g.tilt = 0.3 + 0.12 * Math.sin(TAU * 0.22 * t + 1); g.dil = 0.75; break;
      case 'talking': g.rx = 40 + 6 * A; g.mouth = A; g.open = 1 - 0.15 * A; g.temp = 0.78 + 0.3 * A; g.spd = TAU / 10; break;
      case 'happy': {
        g.happy = t < 2.4 ? 1 : 0; g.ok = t < 1.2 ? 0.4 : 0;
        if (t < 0.9) { g.rx = calm ? 44 : 54; g.spd = calm ? TAU / 8 : TAU / 1.1; } else g.spd = TAU / 12;
        if (!calm && t < 0.6) { g.py = -9 * Math.sin(Math.PI * t / 0.6); }
        break;
      }
      case 'confused': {
        g.bad = t < 0.9 ? 0.9 : 0.5; g.dil = 0.6; g.temp = 0.6;
        if (!calm) { g.rot = -0.2 + 0.5 * Math.exp(-t / 0.5) * Math.sin(t * 16); g.tilt = 0.3 + 0.2 * Math.exp(-t / 0.6) * Math.sin(t * 11); }
        g.spd = TAU / 30; break;
      }
      case 'working': g.spd = TAU / 3; g.temp = 0.8; g.ringA = 0.32; break;
      case 'sending': g.spd = TAU / 9; if (t > 1.0 && t < 2.6) g.happy = 1; break;
      case 'drowsy': {
        // the lid sags... catches itself... sags again; the moons are winding down, dusk creeps in
        const ph = (t / 6.5) % 1, sag = calm ? 0.45 : (ph < 0.84 ? easeInOut(ph / 0.84) : 1 - easeOutCubic((ph - 0.84) / 0.16));
        g.lid = 0.34 + 0.32 * sag; g.dil = 0.85; g.temp = 0.55; g.spd = calm ? 0 : TAU / 38; g.ringA = 0.2; g.night = 0.28;
        g.py = calm ? 0 : 1.8 * sag; g.rx = 38; g.tilt = 0.27; g.lx = s.look.x * 0.4; g.ly = 0.35;
        break;
      }
      case 'asleep': {
        // eye shut, its night side turned to us, the moons in a slow lazy drift
        const b = calm ? 0.5 : breath(s.T / 4.6); cs.br = b;
        g.open = 0; g.spd = calm ? 0 : TAU / 90; g.rx = 35; g.tilt = 0.22; g.temp = 0.38; g.ringA = 0.11; g.night = 0.72; g.rot = -0.12;
        g.py = 1.5 - 1.3 * b; g.sy = 1 + 0.025 * b; g.sx = 1 - 0.012 * b;
        if (!calm) { cs.zT = (cs.zT ?? 1.4) - dt; if (cs.zT <= 0) { cs.zT = 2.9; s.spawn({ k: 'z', x: 15, y: -17, vx: 3, vy: -5.5, max: 3.4, r: cs.small ? 10 : 6, col: C.dim }); } }
        break;
      }
      case 'waking': {
        // dawn rolls across, a flutter, then the eye pops wide and the moons spin up like a stretch
        if (calm) { g.open = clamp(t / 0.5); g.night = 0.72 * (1 - clamp(t / 0.6)); g.temp = lerp(0.4, 0.72, clamp(t / 0.8)); g.spd = 0; break; }
        g.night = 0.72 * (1 - easeOutCubic(t / 0.55));
        g.open = t < 0.2 ? 0 : t < 0.38 ? 0.5 * easeOutCubic((t - 0.2) / 0.18) : t < 0.5 ? 0.5 * (1 - (t - 0.38) / 0.12) : t < 0.8 ? 1.25 : 1;
        const big = t >= 0.5 && t < 0.95;
        g.esc = big ? 1.2 : 1; g.dil = t >= 0.5 && t < 1.1 ? 1.3 : 1;
        g.spd = t < 0.45 ? TAU / 60 : t < 1.05 ? TAU / 0.9 : TAU / 20;
        const st2 = t > 0.45 && t < 1.0;
        g.rx = st2 ? 50 : 40; g.tilt = st2 ? 0.38 : 0.3; g.ringA = t > 0.45 && t < 1.1 ? 0.42 : 0.26;
        g.sy = t > 0.45 && t < 0.8 ? 1.1 : 1; g.sx = t > 0.45 && t < 0.8 ? 0.93 : 1; g.py = t > 0.45 && t < 0.85 ? -5 : 0;
        g.temp = t < 0.45 ? 0.45 : t < 1.1 ? 0.9 : 0.72;
        if (!cs.burst && t > 0.5) { cs.burst = 1; s.spawn({ k: 'ring', x: 0, y: 2, r: 24, r2: 52, max: 0.7, col: C.fg, a0: 0.35 }); }
        break;
      }
      case 'woke': {
        const pop = !calm && t < 0.35;
        g.open = 1.1; g.esc = pop ? 1.18 : 1.05; g.dil = 1.25; g.temp = 0.86; g.rx = pop ? 47 : 42; g.spd = TAU / 12; g.ringA = 0.34; g.lx = 0; g.ly = 0.05;
        if (!cs.burst && t > 0.02) { cs.burst = 1; s.spawn({ k: 'ring', x: 0, y: 2, r: 23, r2: 46, max: 0.55, col: C.fg, a0: 0.4 }); }
        break;
      }
      case 'approval_needed': {
        // two moons sit down, one is held up high: "may I?"; the eye glances up at it and back at you
        const bob = calm ? 0 : Math.sin(s.T * 2.4);
        g.park = 0.9; g.spd = 0; g.open = 1.05; g.dil = 1.1; g.ringA = 0.3; g.ringC = 0.55; g.temp = 0.76;
        g.lx = 0; g.ly = !calm && (s.T % 3.4) < 0.9 ? -0.85 : -0.1;
        g.mt[0] = { x: 0, y: -38 + 1.6 * bob, front: true, tau: 0.14 }; g.msc[0] = 1.2;
        break;
      }
    }
    // an idle antic poses the body on top of the state
    const an = s.antic && Tycho.antics[s.antic.id];
    if (an && an.pose) an.pose(s, g, s.antic);

    const fo = (k, v, tau) => s.fo(k, v, tau);
    fo('rx', g.rx, 0.12); fo('tilt', g.tilt, 0.12); fo('rot', g.rot, 0.12); fo('temp', g.temp, 0.14); fo('open', g.open, 0.07);
    fo('dil', g.dil, 0.08); fo('ok', g.ok, 0.08); fo('bad', g.bad, 0.06); fo('py', g.py, 0.03); fo('park', g.park, 0.35);
    fo('ringA', g.ringA, 0.2); fo('happy', g.happy, 0.06); fo('mouth', g.mouth, 0.03); fo('spd', g.spd, st === 'waking' ? 0.22 : 0.4);
    fo('sx', g.sx, 0.07); fo('sy', g.sy, 0.07); fo('lid', g.lid, 0.18); fo('night', g.night, 0.4); fo('esc', g.esc, 0.08);
    fo('px', g.px, 0.08); fo('rox', g.rox, 0.06); fo('roy', g.roy, 0.06); fo('ringC', g.ringC, 0.2); fo('lon', g.lon, 0.05);
    fo('surf', g.surf, 0.1); fo('glow', g.glow, 0.1);
    fo('low', g.lx === null ? 0 : 1, 0.12);
    if (g.lx !== null) { fo('lox', g.lx, 0.07); fo('loy', g.ly, 0.07); }
    for (let i = 0; i < 3; i++) {
        const m = g.mt[i];
        fo('mw' + i, m ? (m.w ?? 1) : 0, m ? (m.tau ?? 0.12) : 0.07);   // released moons snap back into orbit
        if (m) cs.mt[i] = { x: m.x, y: m.y, front: m.front !== false };
        if (g.mcc && g.mcc[i]) cs.mtc[i] = g.mcc[i];
        fo('mc' + i, g.mc[i], 0.2); fo('msc' + i, g.msc[i], 0.1);
    }
    cs.orb = (cs.orb || 0) + dt * p_(s, 'spd');
    /* moon offsets for sending: one leaves, the other two re-space, a new one forms */
    const tmo = [0, 0, 0], tma = [1, 1, 1];
    if (st === 'sending') {
        if (t > 0.45 && t < 1.7) { tma[0] = 0; tmo[2] = -Math.PI / 3; }
        if (t >= 1.7) { tma[0] = 1; }
        if (!cs.shot && t > 0.45) {
            cs.shot = 1;
            const a = cs.orb + cs.mo[0], Rr = p_(s, 'rx'), r = p_(s, 'rot');
            const x0 = Rr * Math.cos(a), y0 = Rr * p_(s, 'tilt') * Math.sin(a);
            s.spawn({ k: 'trail', x: x0 * Math.cos(r) - y0 * Math.sin(r), y: 2 + x0 * Math.sin(r) + y0 * Math.cos(r), vx: 150, vy: -100, max: 1.1, r: 3.6, col: C.fg, col2: C.whitehot, force: true });
            cs.ma[0] = 0; cs.mr[0] = 0;
        }
    }
    for (let i = 0; i < 3; i++) {
        cs.mo[i] += (tmo[i] - cs.mo[i]) * (1 - Math.exp(-dt / 0.35));
        cs.ma[i] += (tma[i] - cs.ma[i]) * (1 - Math.exp(-dt / 0.18));
        cs.mr[i] += ((tma[i] > 0.5 ? 1 : cs.mr[i]) - cs.mr[i]) * (1 - Math.exp(-dt / 0.3));
    }
    if (st === 'confused' && !calm) { for (let i = 0; i < 3; i++) cs.mo[i] += Math.sin(s.T * 9 + i * 2) * 0.5 * Math.exp(-t / 0.6) * dt * 6; }
    if (st === 'happy' && !cs.burst && t > 0.1) {
        cs.burst = 1;
        for (let i = 0; i < 10; i++) { const a = rnd(0, TAU); s.spawn({ k: 'dot', x: Math.cos(a) * 24, y: 2 + Math.sin(a) * 24, vx: Math.cos(a) * 70, vy: Math.sin(a) * 70, drag: 2.5, max: 0.9, r: 1.6, col: K.pick([C.fg, C.ok, C.hot]) }); }
    }
  },
  draw(o, s) {
    const ctx = o.ctx, p = s.p, cs = s.cs, st = s.state, t = s.t;
    cs.small = o.small;
    if (p.rx === undefined) Tycho.update(s, 0);          // painted before the first step: settle the followers
    o.begin();
    const cy = 2 + p.py, PX = p.px || 0;
    let col = heat(p.temp); col = mixc(col, C.ok, p.ok * 0.6); col = mixc(col, C.bad, p.bad * 0.7);
    const rot = p.rot, ry = p.rx * p.tilt, rcx = PX + (p.rox || 0), rcy = cy + (p.roy || 0), night = p.night || 0;
    const ringPt = (a, rad = p.rx) => { const x = rad * Math.cos(a), y = rad * p.tilt * Math.sin(a); return [rcx + x * Math.cos(rot) - y * Math.sin(rot), rcy + x * Math.sin(rot) + y * Math.cos(rot)]; };
    /* glow */
    const gl = Math.max(0, 0.12 + 0.1 * (st === 'listening' ? s.L : 0) + (p.glow || 0)) * (1 - 0.6 * night);
    const g = ctx.createRadialGradient(PX, cy, 6, PX, cy, 52); g.addColorStop(0, rgba(col, gl)); g.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(PX, cy, 52, 0, TAU); ctx.fill();
    /* moons */
    const moons = [0, 1, 2].map(i => {
        const a = cs.orb + i * TAU / 3 + cs.mo[i];
        let [x, y] = ringPt(a); const sinA = Math.sin(a);
        x = lerp(x, PX + PARK[i][0], p.park); y = lerp(y, PARK[i][1], p.park);
        x = lerp(PX, x, cs.mr[i]); y = lerp(cy, y, cs.mr[i]);
        let front = p.park > 0.5 || sinA > 0;
        const w = p['mw' + i] || 0, q = cs.mt[i];
        if (w > 0.002 && q) { x = lerp(x, q.x, w); y = lerp(y, q.y, w); if (w > 0.5) front = q.front; }
        let rr = 3.8 * (1 + 0.18 * sinA * (1 - p.park) * (1 - w));
        if (st === 'listening') rr *= 1 + 0.45 * s.histAt(0.12 * i, 'L');
        if (st === 'talking') rr *= 1 + 0.25 * s.A;
        rr *= p['msc' + i] ?? 1;
        return { i, x, y, a, front, r: rr, al: cs.ma[i], w, tint: p['mc' + i] || 0 };
    });
    cs.mpos = moons;
    const mbase = st === 'off' ? mixc(C.ash, C.dim, 0.4) : mixc(MOON, [150, 150, 176], night * 0.55);
    const drawMoon = m => {
        if (m.al < 0.02) return;
        const mc = m.tint > 0.005 ? mixc(mbase, cs.mtc[m.i], m.tint) : mbase;
        ctx.fillStyle = rgba(mc, m.al); ctx.beginPath(); ctx.arc(m.x, m.y, Math.max(m.r, o.mw(1.1)), 0, TAU); ctx.fill();
    };
    const ringLW = o.lw(1.1 + 0.9 * (p.ringC || 0), 0.6 + 0.5 * (p.ringC || 0)), rc = mixc(C.fg, C.accent, p.ringC || 0);
    const trail = (m, len, colr, alpha, lw) => {
        if (m.w > 0.3) return;
        const n = 8; for (let j = 0; j < n; j++) { const a0 = m.a - len * (j / n), a1 = m.a - len * ((j + 1) / n); const P0 = ringPt(a0), P1 = ringPt(a1);
            ctx.strokeStyle = rgba(colr, alpha * (1 - j / n) * m.al * (1 - m.w)); ctx.lineWidth = lw; ctx.beginPath(); ctx.moveTo(P0[0], P0[1]); ctx.lineTo(P1[0], P1[1]); ctx.stroke(); } };
    const moonPass = front => {
        for (const m of moons) if (m.front === front) {
            if (st === 'thinking') trail(m, 0.9, C.fg, 0.35, o.lw(2, 0.8));
            if (st === 'working' && m === moons[0]) trail(m, 1.6, C.accent, 0.9, o.lw(2.6, 1.1));
            drawMoon(m);
        }
    };
    /* ring back half */
    ctx.strokeStyle = rgba(rc, p.ringA); ctx.lineWidth = ringLW;
    ctx.beginPath(); K.ellipse(ctx, rcx, rcy, p.rx, Math.max(ry, 0.5), rot, Math.PI, TAU); ctx.stroke();
    ctx.lineCap = 'round';
    moonPass(false);
    /* planet (with its squash and stretch) */
    ctx.save(); ctx.translate(PX, cy); ctx.scale(p.sx, p.sy);
    const pg = ctx.createRadialGradient(-7, -8, 2, 0, 0, R);
    pg.addColorStop(0, rgba(heat(p.temp + 0.3), 1)); pg.addColorStop(0.6, rgba(col, 1)); pg.addColorStop(1, rgba(mixc(heat(p.temp - 0.3), [40, 24, 20], 0.35), 1));
    ctx.fillStyle = pg; ctx.beginPath(); ctx.arc(0, 0, R, 0, TAU); ctx.fill();
    const surf = p.surf || 0, clipped = surf > 0.01 || night > 0.01;
    if (clipped) { ctx.save(); ctx.beginPath(); ctx.arc(0, 0, R, 0, TAU); ctx.clip(); }
    if (surf > 0.01) drawSurface(o, p.lon, surf, col);
    if (night > 0.01) {   // the night side, a lit crescent left on the sunward limb
        const ng = ctx.createLinearGradient(-R * 0.95, -R * 0.25, R * 0.45, R * 0.1);
        ng.addColorStop(0, rgba(NIGHT, 0)); ng.addColorStop(0.42, rgba(NIGHT, 0.4 * night)); ng.addColorStop(1, rgba(NIGHT, 0.7 * night));
        ctx.fillStyle = ng; ctx.beginPath(); ctx.arc(0, 0, R + 1, 0, TAU); ctx.fill();
    }
    /* eye (it rides round with the planet when Tycho spins) */
    let lx = s.look.x, ly = s.look.y;
    if (st === 'thinking') { lx = -0.5; ly = -0.8; }
    if (st === 'sending' && t < 1.0) { lx = 0.9; ly = -0.5; }
    if (st === 'working') { lx = Math.cos(cs.orb) * 0.7; ly = 0.2; }
    if (st === 'confused') { lx = Math.cos(s.T * 9) * 0.6; ly = Math.sin(s.T * 9) * 0.6; }
    const lw_ = p.low || 0; lx = lerp(lx, p.lox ?? 0, lw_); ly = lerp(ly, p.loy ?? 0, lw_);
    const lonC = Math.cos(p.lon || 0), lonS = Math.sin(p.lon || 0), er = 10 * (p.esc || 1);
    const sleepy = st === 'off' || st === 'drowsy' || st === 'asleep';
    const open = clamp(p.open * (1 - s.blink * (sleepy ? 0 : 1)), 0, 1.2);
    const lidCol = mixc(mixc(col, [40, 24, 20], 0.18), NIGHT, 0.62 * night * 0.7);
    if (lonC > 0.03) {
        ctx.save(); ctx.translate(R * 0.92 * lonS, -3); ctx.scale(lonC, 1);
        ctx.lineCap = 'round';
        if (p.happy > 0.5) {
            ctx.strokeStyle = rgba([250, 242, 236], 1); ctx.lineWidth = o.lw(3, 1.2);
            ctx.beginPath(); ctx.arc(0, 4, 7, Math.PI * 1.12, Math.PI * 1.88); ctx.stroke();
        } else if (open < 0.12) {
            ctx.strokeStyle = rgba(st === 'off' ? [30, 27, 26] : mixc(INK, [8, 6, 14], night), 1); ctx.lineWidth = o.lw(2, 1);
            ctx.beginPath(); ctx.arc(0, -3, 7.5, Math.PI * 0.2, Math.PI * 0.8); ctx.stroke();
        } else {
            const ery = er * Math.min(open, 1);
            ctx.save(); ctx.beginPath(); K.ellipse(ctx, 0, 0, er, ery, 0, 0, TAU); ctx.closePath();
            ctx.fillStyle = rgba(EYE, 1); ctx.fill(); ctx.clip();
            const pr = 4.2 * p.dil * (p.esc || 1), ppx = lx * 4.2, ppy = ly * 3.6;
            ctx.fillStyle = rgba(PUPIL, 1); ctx.beginPath(); ctx.arc(ppx, ppy, Math.max(pr, o.mw(1.3)), 0, TAU); ctx.fill();
            if (!o.small) { ctx.fillStyle = rgba([255, 255, 255], 0.9); ctx.beginPath(); ctx.arc(ppx - 1.4, ppy - 1.5, 1.2, 0, TAU); ctx.fill(); }
            const lid = p.lid || 0;
            if (lid > 0.01) {   // an upper lid, the planet's own colour, with a soft curve
                const top = -ery, yl = top + 2 * ery * lid;
                ctx.fillStyle = rgba(lidCol, 1);
                ctx.beginPath(); ctx.moveTo(-er - 2, top - 2); ctx.lineTo(er + 2, top - 2); ctx.lineTo(er + 2, yl - 1.5);
                ctx.quadraticCurveTo(0, yl + 2.5, -er - 2, yl - 1.5); ctx.closePath(); ctx.fill();
                ctx.strokeStyle = rgba(INK, 0.8); ctx.lineWidth = o.lw(1.2, 0.7);
                ctx.beginPath(); ctx.moveTo(-er - 2, yl - 1.5); ctx.quadraticCurveTo(0, yl + 2.5, er + 2, yl - 1.5); ctx.stroke();
            }
            ctx.restore();
            ctx.strokeStyle = rgba(mixc(col, [40, 20, 14], 0.5), 0.8); ctx.lineWidth = o.lw(1, 0.5);
            ctx.beginPath(); K.ellipse(ctx, 0, 0, er, ery, 0, 0, TAU); ctx.closePath(); ctx.stroke();
        }
        ctx.restore();
    }
    /* mouth */
    const mC = Math.cos(p.lon || 0);
    if (mC > 0.05) {
        ctx.save(); ctx.translate(18 * Math.sin(p.lon || 0), 0); ctx.scale(mC, 1);
        if (p.mouth > 0.04) {
            ctx.fillStyle = rgba(INK, 1); ctx.beginPath(); K.ellipse(ctx, 0, 12, 2.6 + 2 * p.mouth, 0.5 + 4 * p.mouth, 0, 0, TAU); ctx.fill();
        } else if (st === 'asleep') {
            if (!o.small) { const b = cs.br ?? 0.5; ctx.fillStyle = rgba(mixc(INK, [8, 6, 14], night), 0.8); ctx.beginPath(); K.ellipse(ctx, 0, 10.5, 1.5 + 0.3 * b, 1 + 1 * b, 0, 0, TAU); ctx.fill(); }
        } else if (!o.small && st !== 'off') {
            ctx.strokeStyle = rgba(INK, 0.7); ctx.lineWidth = o.lw(1.4, 0.8);
            ctx.beginPath(); ctx.arc(0, 9.5, st === 'confused' ? 2 : 3, st === 'confused' ? Math.PI * 1.2 : Math.PI * 0.2, st === 'confused' ? Math.PI * 1.8 : Math.PI * 0.8); ctx.stroke();
        }
        ctx.restore();
    }
    if (clipped) ctx.restore();
    ctx.restore();
    /* ring front half */
    ctx.strokeStyle = rgba(rc, p.ringA * 1.15); ctx.lineWidth = ringLW;
    ctx.beginPath(); K.ellipse(ctx, rcx, rcy, p.rx, Math.max(ry, 0.5), rot, 0, Math.PI); ctx.stroke();
    moonPass(true);
    if (st === 'approval_needed') {   // a soft beacon round the raised moon
        const m = moons[0], pu = s.calm ? 0.5 : 0.5 + 0.5 * Math.sin(s.T * 3);
        ctx.strokeStyle = rgba(C.accent, 0.75 - 0.35 * pu); ctx.lineWidth = o.lw(1.3, 0.9);
        ctx.beginPath(); ctx.arc(m.x, m.y, m.r + 2.5 + 2.5 * pu, 0, TAU); ctx.stroke();
    }
    drawParts(o, s);
    if (st === 'sending' && t > 1.0) { drawCheck(ctx, 34, -26, 15, clamp((t - 1.0) / 0.3), C.ok, 1 - clamp((t - 2.6) / 0.4), o.lw(2.6, 1.3)); }
    o.end();
  },
  // a gentle resting rate for the drowsy nod
  fps(s, B) {
    const f = K.wantFps(s, B);
    if (s.state === 'drowsy' && !s.calm && !s.antic && !s.caught) return Math.max(f, B.idle_fps);
    return f;
  },

  // ---- idle antics ------------------------------------------------------------------
  antics: {
    // the planet's shadow falls on one moon: it slides into the umbra and turns copper
    eclipse: {
      start(s, a) { s.cs.an = {}; },
      pose(s, g, a) {
        const u = a.u, e = env(u, 0.14, 0.14), cy = cyOf(s);
        const sh = W(u, 0.22, 0.42) * (1 - W(u, 0.68, 0.84));
        g.mt[0] = { x: 38 + 1.5 * Math.sin(a.t * 0.9), y: cy + 1 + 1.2 * Math.sin(a.t * 1.3), front: true, w: e, tau: 0.2 };
        g.msc[0] = 1 + 0.35 * e; g.mc[0] = 0.92 * sh; g.mcc = [COPPER, null, null];
        g.lx = 0.95 * e; g.ly = 0.05; g.dil = 1 + 0.45 * sh; g.spd = TAU / 40; g.temp = 0.72 - 0.08 * sh;
        g.happy = u > 0.87 && u < 0.97 ? 1 : 0;
      },
      draw(o, s, a) {
        const m = s.cs.mpos && s.cs.mpos[0]; if (!m) return;
        const u = a.u, ctx = o.ctx, sh = W(u, 0.22, 0.42) * (1 - W(u, 0.68, 0.84));
        if (sh > 0.01) glowDot(o, m.x, m.y, m.r, m.r * 2.8, COPPER, 0.35 * sh);
        if (!o.small) {   // the umbra's edge creeping across the face
            const inA = W(u, 0.18, 0.42), outA = W(u, 0.68, 0.86), dx = inA < 1 ? -(1 - inA) * 3.2 * m.r : outA * 3.2 * m.r;
            if (inA > 0 && outA < 1) {
                ctx.save(); ctx.beginPath(); ctx.arc(m.x, m.y, m.r, 0, TAU); ctx.clip();
                ctx.fillStyle = rgba([70, 26, 18], 0.55); ctx.beginPath(); ctx.arc(m.x + dx, m.y, m.r * 2.2, 0, TAU); ctx.fill(); ctx.restore();
            }
        }
      },
      end(s, a, caught) { if (caught) blinkAwake(s); },
      exitDur: 0.25,
      exit(o, s, c, u) { popRings(o, s, u, [0]); }
    },

    // lines the moons up and counts them, 1, 2, 3... and a fourth? (a star, winking out)
    count: {
      start(s, a) { s.cs.an = {}; },
      pose(s, g, a) {
        const u = a.u, cy = cyOf(s), e = env(u, 0.1, 0.12), xs = [-17, 0, 17];
        for (let i = 0; i < 3; i++) {
            const ci = 0.2 + 0.15 * i, hop = bump(u, ci, ci + 0.08);
            g.mt[i] = { x: xs[i], y: cy - 35 - 6 * hop, front: true, w: e, tau: 0.14 };
            g.msc[i] = 1 + 0.3 * hop; g.mc[i] = (u > ci + 0.03 ? 0.6 : 0) * e;
        }
        g.mcc = [C.hot, C.hot, C.hot];
        const k = clamp(Math.floor((u - 0.17) / 0.15), 0, 2);
        if (u < 0.66) { g.lx = xs[k] / 20; g.ly = -0.9; }
        else if (u < 0.73) { g.lx = -0.9; g.ly = -0.35; }
        else if (u < 0.79) { g.lx = 0.9; g.ly = -0.35; }
        else if (u < 0.89) { g.lx = 0.85; g.ly = -0.9; g.dil = 1.35; g.esc = 1.1; g.mouth = 0.35 * bump(u, 0.8, 0.88); }
        else { g.lx = 0; g.ly = 0; }
        g.lx *= e; g.ly *= e;
        g.happy = u > 0.9 && u < 0.98 ? 1 : 0;
        g.spd = TAU / 30;
      },
      draw(o, s, a) {
        const u = a.u;
        if (u > 0.78 && u < 0.92) star(o, 37, -43, 5.5 * bump(u, 0.78, 0.92), 0.95, STAR);
      },
      end(s, a, caught) { if (caught) blinkAwake(s); },
      exitDur: 0.25,
      exit(o, s, c, u) { popRings(o, s, u, [0, 1, 2]); }
    },

    // a comet crosses the sky above, shedding sparks; the eye follows it all the way
    comet: {
      start(s, a) { s.cs.an = { sp: 0 }; },
      at(u) { const q = W(u, 0.08, 0.86); return { q, x: lerp(-56, 56, q), y: -50 + 16 * q * q, a: Math.min(1, q * 8, (1 - q) * 8) }; },
      pose(s, g, a) {
        const h = this.at(a.u), cy = cyOf(s), e = env(a.u, 0.08, 0.1);
        g.lx = clamp(h.x / 36, -1, 1) * e; g.ly = clamp((h.y - cy) / 30, -1, 1) * 0.9 * e;
        g.rot = -0.2 + 0.16 * clamp(h.x / 56, -1, 1) * e; g.dil = 1 + 0.25 * e; g.esc = 1 + 0.08 * bump(h.q, 0.35, 0.65);
        g.happy = a.u > 0.88 && a.u < 0.97 ? 1 : 0;
      },
      update(s, dt, a) {
        const an = s.cs.an, h = this.at(a.u);
        an.sp -= dt;
        if (h.a > 0.3 && an.sp <= 0) { an.sp = 0.12; s.spawn({ k: 'dot', x: h.x - 3, y: h.y + 1, vx: rnd(-8, -2), vy: rnd(3, 9), max: 0.7, r: 0.9, col: C.hot }); }
      },
      under(o, s, a) {
        const ctx = o.ctx, h = this.at(a.u); if (h.a <= 0.01) return;
        ctx.lineCap = 'round';
        const n = 14;
        for (let j = n; j >= 1; j--) {
            const p0 = this.at(a.u - (j - 1) * 0.015), p1 = this.at(a.u - j * 0.015), f = 1 - j / n;
            ctx.strokeStyle = rgba(mixc(C.hot, C.whitehot, f), h.a * 0.65 * f); ctx.lineWidth = o.lw(0.8 + 3.2 * f, 0.7);
            ctx.beginPath(); ctx.moveTo(p0.x, p0.y); ctx.lineTo(p1.x, p1.y); ctx.stroke();
        }
        glowDot(o, h.x, h.y, 1, 9, C.hot, 0.55 * h.a);
        dot(o, h.x, h.y, 2.2, C.whitehot, h.a);
      },
      end(s, a, caught) { if (caught) { const h = this.at(a.u); s.cs.ex = { x: h.x, y: h.y, a: h.a }; blinkAwake(s); } },
      exitDur: 0.25,
      exit(o, s, c, u) {
        const ex = s.cs.ex; if (!ex) return;
        const x = ex.x + 50 * easeInCubic(u), y = ex.y + 12 * easeInCubic(u);
        o.ctx.strokeStyle = rgba(C.whitehot, ex.a * (1 - u) * 0.7); o.ctx.lineWidth = o.lw(2, 0.8); o.ctx.lineCap = 'round';
        o.ctx.beginPath(); o.ctx.moveTo(x - 14, y - 3); o.ctx.lineTo(x, y); o.ctx.stroke();
        dot(o, x, y, 2.2, C.whitehot, ex.a * (1 - u));
      }
    },

    // a brass spyglass telescopes out of the eye; it sweeps the sky... and finds a star
    telescope: {
      start(s, a) { s.cs.an = {}; },
      th(u) { return keys(u, [[0.18, -0.6], [0.3, -1.0], [0.43, -0.32], [0.54, -0.72], [1, -0.72]]); },
      len(u) { return 24 * clamp(easeOutBack(W(u, 0.05, 0.2))) * (1 - easeInCubic(W(u, 0.85, 0.97))); },
      pose(s, g, a) {
        const u = a.u, th = this.th(u), e = env(u, 0.06, 0.08), found = bump(u, 0.55, 0.84);
        g.lx = 0.9 * Math.cos(th) * e; g.ly = 0.9 * Math.sin(th) * e;
        g.lid = 0.3 * e * (1 - found); g.esc = 1 + 0.1 * found; g.dil = 0.85 + 0.5 * found;
        g.mouth = 0.3 * bump(u, 0.55, 0.64);
        g.happy = u > 0.9 && u < 0.98 ? 1 : 0;
      },
      draw(o, s, a) {
        const u = a.u, th = this.th(u), L = this.len(u);
        drawTube(o, s, th, L, 1);
        const f = W(u, 0.54, 0.6) * (1 - W(u, 0.8, 0.86));
        if (f > 0) {
            const cy = cyOf(s), c = Math.cos(th), sn = Math.sin(th), d = 8.5 + L + 12;
            star(o, (s.p.px || 0) + c * d, cy - 3 + sn * d, 4.5 * f * (0.85 + 0.15 * Math.sin(a.t * 9)), f, STAR);
        }
      },
      end(s, a, caught) { if (caught) s.cs.ex = { th: this.th(a.u), len: this.len(a.u) }; },
      exitDur: 0.22,
      exit(o, s, c, u) { const ex = s.cs.ex; if (ex) drawTube(o, s, ex.th, ex.len * (1 - easeOutCubic(u)), 1 - u * 0.5); }
    },

    // a night sky blooms around Tycho and meteors streak across it
    meteors: {
      start(s, a) {
        const m = [], st = [];
        for (let i = 0; i < 7; i++) m.push({ t0: 0.9 + i * 1.15 + rnd(-0.3, 0.3), x: rnd(-54, 10), y: rnd(-58, -44), ang: rnd(0.38, 0.6), life: rnd(0.5, 0.7) });
        for (let i = 0; i < 10; i++) { let x = rnd(-56, 56), y = rnd(-58, -28); st.push({ x, y, r: rnd(1.4, 2.6), ph: rnd(0, TAU) }); }
        s.cs.an = { m, st };
      },
      head(mt, t) { const q = (t - mt.t0) / mt.life; if (q < 0 || q > 1) return null; const d = 32 * Math.pow(q, 0.8); return { q, x: mt.x + Math.cos(mt.ang) * d, y: mt.y + Math.sin(mt.ang) * d }; },
      pose(s, g, a) {
        const an = s.cs.an, e = env(a.u, 0.1, 0.1), cy = cyOf(s);
        let h = null; for (const mt of an.m) { const q = this.head(mt, a.t); if (q) h = q; }
        g.lx = (h ? clamp(h.x / 36, -1, 1) : 0) * e; g.ly = (h ? clamp((h.y - cy) / 30, -1, 0) : -0.6) * e;
        g.dil = 1 + 0.3 * e; g.esc = h ? 1.06 : 1; g.spd = TAU / 40; g.temp = 0.66; g.night = 0.14 * e; g.glow = -0.03 * e;
      },
      under(o, s, a) {
        const an = s.cs.an, ctx = o.ctx, e = env(a.u, 0.08, 0.1);
        an.st.forEach((p, i) => { if (!o.small || i % 2 === 0) star(o, p.x, p.y, p.r, e * (o.small ? 0.35 : 0.5) * (0.6 + 0.4 * Math.sin(s.T * 2.1 + p.ph)), STAR, 1.3); });
        ctx.lineCap = 'round';
        for (const mt of an.m) {
            const h = this.head(mt, a.t); if (!h) continue;
            const al = Math.pow(Math.sin(Math.PI * h.q), 0.6), tl = 20 * (0.5 + h.q), tx = h.x - Math.cos(mt.ang) * tl, ty = h.y - Math.sin(mt.ang) * tl;
            const gr = ctx.createLinearGradient(tx, ty, h.x, h.y);
            gr.addColorStop(0, rgba(C.hot, 0)); gr.addColorStop(1, rgba(C.whitehot, 0.95 * al));
            ctx.strokeStyle = gr; ctx.lineWidth = o.lw(2, 1.2);
            ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(h.x, h.y); ctx.stroke();
            glowDot(o, h.x, h.y, 0.5, 5, C.hot, 0.5 * al); dot(o, h.x, h.y, 1.5, C.whitehot, al);
        }
      },
      end(s, a, caught) { if (caught) s.cs.ex = { st: s.cs.an.st, e: env(a.u, 0.08, 0.1) }; },
      exitDur: 0.25,
      exit(o, s, c, u) { const ex = s.cs.ex; if (ex) ex.st.forEach((p, i) => { if (!o.small || i % 2 === 0) star(o, p.x, p.y, p.r * (1 - 0.5 * u), ex.e * 0.5 * (1 - u), STAR, 1.3); }); }
    },

    // a three-moon cascade off the top of its head
    juggle: {
      start(s, a) { s.cs.an = {}; },
      ball(k, tau, cy) {
        const d = 0.3, hand = [-12.5, 12.5], base = cy - 23, H = 26;
        if (tau < k * d) { const sd = k % 2; return { x: hand[sd] * (k === 2 ? 0.55 : 1), y: base, f: 1 }; }
        const v = (tau - k * d) / (3 * d), n = Math.floor(v), f = v - n, side = (n + k) % 2, from = hand[side], to = hand[1 - side];
        if (f < 0.8) { const q = f / 0.8; return { x: lerp(from * 0.5, to, q), y: base - H * 4 * q * (1 - q), f }; }
        const q = (f - 0.8) / 0.2; return { x: lerp(to, to * 0.5, q), y: base + 2.2 * Math.sin(Math.PI * q), f };
      },
      pose(s, g, a) {
        const u = a.u, cy = cyOf(s), e = env(u, 0.1, 0.13), tau = a.t - 0.12 * a.dur;
        let top = null, pulse = 0;
        for (let k = 0; k < 3; k++) {
            const b = this.ball(k, Math.max(0, tau), cy);
            g.mt[k] = { x: b.x, y: b.y, front: true, w: e, tau: 0.1 };
            if (!top || b.y < top.y) top = b;
            if (tau > 0 && b.f < 0.12) pulse = Math.max(pulse, 1 - b.f / 0.12);
        }
        g.lx = clamp(top.x / 28, -1, 1) * e; g.ly = clamp((top.y - cy + 3) / 24, -1, 0.3) * e;
        g.sy = 1 - 0.04 * pulse * e; g.sx = 1 + 0.03 * pulse * e; g.py = 0.9 * pulse * e;
        g.happy = u > 0.88 && u < 0.97 ? 1 : 0; g.spd = TAU / 30;
      },
      end(s, a, caught) { if (caught) blinkAwake(s); },
      exitDur: 0.25,
      exit(o, s, c, u) { popRings(o, s, u, [0, 1, 2]); }
    },

    // the ring drops to its waist and wobbles round like a hula hoop
    hula: {
      start(s, a) { s.cs.an = { sp: 0.3 }; },
      pose(s, g, a) {
        const u = a.u, e = env(u, 0.15, 0.15), ph = a.t * TAU * 1.15;
        g.tilt = lerp(0.3, 0.17 + 0.05 * Math.cos(ph), e); g.rot = -0.2 * (1 - e) + 0.3 * e * Math.sin(ph);
        g.px = 2.8 * e * Math.sin(ph + 0.6); g.rox = -3.2 * e * Math.sin(ph); g.roy = 6 * e + 1.6 * e * Math.cos(2 * ph);
        g.ringA = 0.26 + 0.34 * e; g.ringC = 0.8 * e; g.rx = 40 - 3 * e; g.spd = lerp(TAU / 20, TAU / 2.4, e);
        g.lx = 0.45 * Math.sin(ph + 0.6) * e; g.ly = 0;
        g.happy = u > 0.3 && u < 0.78 ? 1 : 0; g.temp = 0.72 + 0.08 * e;
      },
      update(s, dt, a) {
        const an = s.cs.an, mp = s.cs.mpos; an.sp -= dt;
        if (mp && an.sp <= 0 && a.u > 0.2 && a.u < 0.8) {
            an.sp = 0.22; const m = mp[(K.random() * 3) | 0], dx = m.x - (s.p.px || 0), dy = m.y - cyOf(s), d = Math.hypot(dx, dy) || 1;
            s.spawn({ k: 'dot', x: m.x, y: m.y, vx: dx / d * 26, vy: dy / d * 26 - 6, drag: 2, max: 0.6, r: 1, col: K.pick([C.hot, C.fg]) });
        }
      },
      end(s, a, caught) { if (caught) blinkAwake(s); },
      exitDur: 0.25,
      exit(o, s, c, u) {
        const ctx = o.ctx, cy = cyOf(s);
        ctx.strokeStyle = rgba(C.accent, 0.7 * (1 - u)); ctx.lineWidth = o.lw(1.6 * (1 - u) + 0.6, 0.7);
        ctx.beginPath(); K.ellipse(ctx, 0, cy, 40 + 6 * u, 12 + 3 * Math.sin(u * 18) * (1 - u), -0.2, 0, TAU); ctx.closePath(); ctx.stroke();
      }
    },

    // curtains of green and violet light ripple over its north pole
    aurora: {
      start(s, a) { s.cs.an = {}; },
      pose(s, g, a) {
        const u = a.u, e = env(u, 0.15, 0.2);
        g.lx = 0.15 * Math.sin(a.t * 0.7) * e; g.ly = -0.95 * e; g.dil = 1 + 0.2 * e; g.spd = TAU / 36;
        g.temp = 0.62; g.night = 0.2 * e; g.lid = 0.55 * bump(u, 0.56, 0.66);
        g.ringC = 0; g.glow = -0.03 * e;
      },
      under(o, s, a) { drawAurora(o, s, env(a.u, 0.15, 0.2), a.t); },
      end(s, a, caught) { if (caught) s.cs.ex = { e: env(a.u, 0.15, 0.2), t: a.t }; },
      exitDur: 0.22,
      exit(o, s, c, u) { const ex = s.cs.ex; if (ex) drawAurora(o, s, ex.e * (1 - easeOutCubic(u)), ex.t + c.t); }
    },

    // a little satellite passes over, beeps, and Tycho beeps back
    satellite: {
      start(s, a) { s.cs.an = { beep: 0, reply: 0 }; },
      at(u) { const q = W(u, 0.04, 0.96), x = lerp(-56, 56, q); return { x, y: -40 + 0.18 * x, a: Math.min(1, q * 10, (1 - q) * 10), rot: -0.25 }; },
      pose(s, g, a) {
        const u = a.u, h = this.at(u), cy = cyOf(s), e = env(u, 0.06, 0.06), rep = bump(u, 0.53, 0.66);
        g.lx = clamp(h.x / 40, -1, 1) * e; g.ly = clamp((h.y - cy) / 34, -1, 1) * e;
        g.glow = 0.14 * rep; g.temp = 0.72 + 0.16 * rep; g.esc = 1 + 0.08 * bump(u, 0.44, 0.52);
      },
      update(s, dt, a) {
        const an = s.cs.an, h = this.at(a.u), cy = cyOf(s);
        if (!an.beep && a.u > 0.45) { an.beep = 1; s.spawn({ k: 'ring', x: h.x, y: h.y, r: 3, r2: 15, max: 0.6, col: C.fg, a0: 0.7 }); }
        if (!an.reply && a.u > 0.55) { an.reply = 1; s.spawn({ k: 'ring', x: 0, y: cy, r: 23, r2: 40, max: 0.7, col: C.accent, a0: 0.55 }); }
      },
      draw(o, s, a) { const h = this.at(a.u); drawSat(o, h.x, h.y, h.rot + 0.12 * Math.sin(a.t * 0.9), h.a, s.T); },
      end(s, a, caught) { if (caught) { const h = this.at(a.u); s.cs.ex = h; } },
      exitDur: 0.25,
      exit(o, s, c, u) { const h = s.cs.ex; if (h) drawSat(o, h.x + 40 * easeInCubic(u), h.y + 7 * easeInCubic(u), h.rot, h.a * (1 - u), s.T); }
    },

    // the third moon drifts off on its own; the other two fly out and herd it home
    stray: {
      start(s, a) { s.cs.an = { sp: 0 }; },
      strayAt(s, a) {
        const u = a.u, cy = cyOf(s), wq = easeInOut(W(u, 0.04, 0.34)), back = easeInOut(W(u, 0.6, 0.84));
        const wx = 40 + 3 * Math.sin(a.t * 1.7) * wq, wy = lerp(cy + 2, -38, wq) + 2.5 * Math.cos(a.t * 1.3) * wq;
        return { x: lerp(wx, 36, back), y: lerp(wy, cy + 4, back) };
      },
      pose(s, g, a) {
        const u = a.u, cy = cyOf(s), p = this.strayAt(s, a), out = env(u, 0.06, 0.12);
        g.mt[2] = { x: p.x, y: p.y, front: true, w: out, tau: 0.3 };
        const herd = W(u, 0.36, 0.5) * (1 - W(u, 0.84, 0.95));
        for (let k = 0; k < 2; k++) {
            const an = a.t * 4.5 + k * Math.PI;
            g.mt[k] = { x: p.x + 9 * Math.cos(an), y: p.y + 7 * Math.sin(an), front: Math.sin(an) > -0.2, w: easeInOut(herd), tau: 0.18 };
        }
        const seen = W(u, 0.22, 0.26);
        g.lx = lerp(0, clamp(p.x / 34, -1, 1), seen); g.ly = lerp(0, clamp((p.y - cy) / 30, -1, 1), seen);
        g.esc = 1 + 0.15 * bump(u, 0.22, 0.32); g.dil = 1 + 0.25 * bump(u, 0.22, 0.4);
        g.mouth = 0.3 * bump(u, 0.23, 0.31);
        g.happy = u > 0.87 && u < 0.97 ? 1 : 0;
      },
      update(s, dt, a) {
        const an = s.cs.an; an.sp -= dt;
        if (a.u > 0.08 && a.u < 0.36 && an.sp <= 0) { an.sp = 0.2; const p = this.strayAt(s, a); s.spawn({ k: 'dot', x: p.x, y: p.y + 3, max: 1.8, r: 0.9, col: C.dim, a0: 0.7 }); }
      },
      end(s, a, caught) { if (caught) blinkAwake(s); },
      exitDur: 0.25,
      exit(o, s, c, u) { popRings(o, s, u, [0, 1, 2]); }
    },

    // a little sun rises; one moon slides across it and the corona flares round its edge
    corona: {
      start(s, a) { s.cs.an = {}; },
      SX: -66, SY: -54,
      tot(u) { return W(u, 0.38, 0.44) * (1 - W(u, 0.68, 0.74)); },
      sunA(u) { return W(u, 0.02, 0.12) * (1 - W(u, 0.88, 0.98)); },
      pose(s, g, a) {
        const u = a.u, e = env(u, 0.12, 0.12), tot = this.tot(u);
        const dx = keys(u, [[0.1, 26], [0.42, 0], [0.7, 0], [0.9, -24]]), dy = keys(u, [[0.1, 14], [0.42, 0], [0.7, 0], [0.9, -8]]);
        g.mt[0] = { x: this.SX + dx, y: this.SY + dy, front: true, w: e, tau: 0.16 };
        g.msc[0] = lerp(1, 2.45, W(u, 0.14, 0.4) * (1 - W(u, 0.72, 0.9)));
        g.mc[0] = 0.95 * W(u, 0.26, 0.42) * (1 - W(u, 0.7, 0.84)); g.mcc = [SHADE, null, null];
        g.lx = -0.9 * e; g.ly = -0.8 * e; g.dil = 1 + 0.5 * tot; g.esc = 1 + 0.1 * tot;
        g.temp = 0.72 - 0.22 * tot; g.night = 0.4 * tot; g.spd = TAU / 40; g.mouth = 0.4 * bump(u, 0.42, 0.56);
        g.happy = u > 0.9 && u < 0.98 ? 1 : 0;
      },
      under(o, s, a) {
        const u = a.u, sa = this.sunA(u), tot = this.tot(u), ctx = o.ctx, X = this.SX, Y = this.SY;
        if (sa <= 0.01) return;
        glowDot(o, X, Y, 4, 24, C.hot, 0.4 * sa * (1 - 0.5 * tot));
        if (tot > 0.01) {   // the corona
            glowDot(o, X, Y, 9, 22, STAR, 0.4 * tot);
            ctx.lineCap = 'round'; ctx.strokeStyle = rgba(STAR, 0.6 * tot); ctx.lineWidth = o.lw(1.1, 0.8);
            for (let k = 0; k < 16; k++) {
                const an = k * TAU / 16 + 0.1 * Math.sin(s.T + k), l = 5 + 6 * hash(k + 7) + 2.5 * Math.sin(s.T * 5 + k * 2.3);
                ctx.beginPath(); ctx.moveTo(X + Math.cos(an) * 10, Y + Math.sin(an) * 10); ctx.lineTo(X + Math.cos(an) * (10 + l), Y + Math.sin(an) * (10 + l)); ctx.stroke();
            }
        }
        dot(o, X, Y, 9, mixc(C.whitehot, STAR, 0.4), sa);
      },
      draw(o, s, a) {   // the diamond ring, as totality begins and ends
        const u = a.u, d = Math.max(bump(u, 0.38, 0.46), bump(u, 0.66, 0.74));
        if (d > 0.01) star(o, this.SX + (u < 0.55 ? 6.5 : -6.5), this.SY - 6.5, 9 * d, d, [255, 255, 255]);
      },
      end(s, a, caught) { if (caught) { s.cs.ex = { sa: this.sunA(a.u) }; blinkAwake(s); } },
      exitDur: 0.25,
      exit(o, s, c, u) {
        const ex = s.cs.ex; if (ex) dot(o, this.SX, this.SY + 10 * easeInCubic(u), 9 * (1 - 0.6 * u), C.whitehot, ex.sa * (1 - u));
        popRings(o, s, u, [0]);
      }
    },

    // it turns on its axis: the eye slides away round the limb, the far side goes by
    // (freckles, a crater, a small flag), and the eye comes back round the other side
    farside: {
      start(s, a) { s.cs.an = {}; },
      pose(s, g, a) {
        const u = a.u, e = env(u, 0.1, 0.1);
        g.surf = e; g.lon = keys(u, [[0.14, 0], [0.45, Math.PI], [0.6, Math.PI], [0.88, TAU]]);
        g.lx = u < 0.14 ? 0.85 * bump(u, 0.02, 0.14) : 0; g.ly = 0;
        g.happy = u > 0.9 && u < 0.98 ? 1 : 0; g.spd = TAU / 16;
      },
      end(s, a, caught) {
        const p = s.p;
        if (!caught) p.lon = 0;
        else { let l = ((p.lon % TAU) + TAU) % TAU; if (l > Math.PI) l -= TAU; p.lon = l; blinkAwake(s); }
      },
      exitDur: 0.22,
      exit(o, s, c, u) {   // whoosh lines as it whips round
        const ctx = o.ctx, cy = cyOf(s);
        ctx.strokeStyle = rgba(C.fg, 0.5 * (1 - u)); ctx.lineWidth = o.lw(1.2, 0.7); ctx.lineCap = 'round';
        for (const r of [26, 30]) { ctx.beginPath(); ctx.arc(0, cy, r, -0.6 - u * 0.8, 0.3 - u * 0.8); ctx.stroke(); }
      }
    },

    // a moon becomes a pen and joins up seven stars: a little ringed planet. Itself.
    constellation: {
      start(s, a) { s.cs.an = {}; },
      pose(s, g, a) {
        const u = a.u, cy = cyOf(s), pen = conPen(u), e = env(u, 0.1, 0.1);
        const first = CON.stars[0], x = u < 0.16 ? first[0] : pen.x, y = u < 0.16 ? first[1] : pen.y;
        g.mt[0] = { x, y, front: true, w: e, tau: 0.16 }; g.msc[0] = 0.85; g.mc[0] = 0.4 * e; g.mcc = [C.whitehot, null, null];
        g.lx = clamp(x / 40, -1, 1) * e; g.ly = clamp((y - cy) / 40, -1, 1) * e;
        g.dil = 1 + 0.2 * e; g.happy = u > 0.82 && u < 0.94 ? 1 : 0;
      },
      under(o, s, a) {
        const u = a.u, fade = W(u, 0.02, 0.14) * (1 - W(u, 0.9, 1)), glow = bump(u, 0.8, 0.92);
        drawCon(o, conPen(u), fade, glow, s.T);
      },
      end(s, a, caught) { if (caught) { s.cs.ex = { prog: conPen(a.u), a: W(a.u, 0.02, 0.14) * (1 - W(a.u, 0.9, 1)) }; blinkAwake(s); } },
      exitDur: 0.25,
      exit(o, s, c, u) { const ex = s.cs.ex; if (ex) drawCon(o, ex.prog, ex.a * (1 - u), 0, s.T); popRings(o, s, u, [0]); }
    }
  }
};
return Tycho;
}
