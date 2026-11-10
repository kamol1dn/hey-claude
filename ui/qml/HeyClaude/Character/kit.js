.pragma library
// The character kit (ROADMAP M6): the shared simulation and drawing helpers every
// built-in pack uses, ported from docs/character-options.html so the five designs
// keep their motion. A pack's <id>.js exports make(K) and gets this module as K.
//
// Coordinates: a character draws in a 100-unit box centred on (0, 0) between
// o.begin() and o.end(). o.mw(px) turns screen px into units, o.lw(units, minPx)
// is a line width that never gets thinner than minPx on screen.

var TAU = Math.PI * 2;
function hex(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
var C = {
    bg: hex('#080808'), accent: hex('#D97757'), fg: hex('#F2F2F2'), dim: hex('#9A9A9A'), ok: hex('#6CCB7A'), bad: hex('#E5646A'),
    ash: hex('#5C5652'), banked: hex('#B8664C'), live: hex('#D97757'), hot: hex('#EC9A78'), whitehot: hex('#FFE3D6'), cool: hex('#B38A7A')
};
function clamp(x, a, b) { if (a === undefined) a = 0; if (b === undefined) b = 1; return x < a ? a : x > b ? b : x; }
function lerp(a, b, t) { return a + (b - a) * t; }
function mixc(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
function mul(c, k) { return [c[0] * k, c[1] * k, c[2] * k]; }
function rgba(c, a) { if (a === undefined) a = 1; return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + clamp(a).toFixed(3) + ')'; }
function easeOutCubic(t) { return 1 - Math.pow(1 - clamp(t), 3); }
function easeInCubic(t) { return Math.pow(clamp(t), 3); }
function easeInOut(t) { t = clamp(t); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
function easeOutBack(t) { t = clamp(t); var c1 = 1.6, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); }
function hash(n) { var x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }

// Randomness goes through one seedable generator, so the lab and the tests are repeatable.
var _seed = 0;
function seed(s) { _seed = (s >>> 0) || 0; }
function random() {
    if (!_seed) return Math.random();
    _seed ^= _seed << 13; _seed ^= _seed >>> 17; _seed ^= _seed << 5;
    return ((_seed >>> 0) % 1000003) / 1000003;
}
function rnd(a, b) { return a + random() * (b - a); }
function pick(arr) { return arr[(random() * arr.length) | 0]; }

var HEAT = [[0, C.ash], [0.4, C.banked], [0.7, C.live], [1.0, C.hot], [1.25, C.whitehot]];
function heat(x) {
    x = clamp(x, 0, 1.25);
    for (var i = 0; i < HEAT.length - 1; i++) {
        var a = HEAT[i][0], ca = HEAT[i][1], b = HEAT[i + 1][0], cb = HEAT[i + 1][1];
        if (x <= b) return mixc(ca, cb, (x - a) / (b - a));
    }
    return C.whitehot;
}
// asymmetric breath, 0..1: inhale 40 % OutSine, exhale 60 % InOutSine
function breath(phase) {
    var f = phase - Math.floor(phase);
    if (f < 0.4) return Math.sin((f / 0.4) * Math.PI / 2);
    var u = (f - 0.4) / 0.6; return 1 - (0.5 - 0.5 * Math.cos(Math.PI * u));
}

// ---- stroke helpers, all in the current transform -------------------------------
function drawZ(ctx, x, y, s, col, a, lw) {
    ctx.save(); ctx.strokeStyle = rgba(col, a); ctx.lineWidth = lw; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(x - s / 2, y - s / 2); ctx.lineTo(x + s / 2, y - s / 2); ctx.lineTo(x - s / 2, y + s / 2); ctx.lineTo(x + s / 2, y + s / 2); ctx.stroke(); ctx.restore();
}
function drawQ(ctx, x, y, s, col, a, lw) {
    ctx.save(); ctx.strokeStyle = rgba(col, a); ctx.fillStyle = rgba(col, a); ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath(); ctx.arc(x, y - s * 0.22, s * 0.3, Math.PI * 1.08, Math.PI * 2.28); ctx.lineTo(x, y + s * 0.16); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y + s * 0.42, lw * 0.75, 0, TAU); ctx.fill(); ctx.restore();
}
function drawCheck(ctx, x, y, s, trim, col, a, lw) {
    if (trim <= 0 || a <= 0) return;
    var P = [[x - 0.45 * s, y], [x - 0.12 * s, y + 0.32 * s], [x + 0.5 * s, y - 0.38 * s]];
    var l1 = Math.hypot(P[1][0] - P[0][0], P[1][1] - P[0][1]), l2 = Math.hypot(P[2][0] - P[1][0], P[2][1] - P[1][1]);
    var d = trim * (l1 + l2);
    ctx.save(); ctx.strokeStyle = rgba(col, a); ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath(); ctx.moveTo(P[0][0], P[0][1]);
    if (d <= l1) { var u = d / l1; ctx.lineTo(lerp(P[0][0], P[1][0], u), lerp(P[0][1], P[1][1], u)); }
    else { ctx.lineTo(P[1][0], P[1][1]); var u2 = (d - l1) / l2; ctx.lineTo(lerp(P[1][0], P[2][0], u2), lerp(P[1][1], P[2][1], u2)); }
    ctx.stroke(); ctx.restore();
}
function poly(ctx, pts) { ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]); ctx.closePath(); }
function pillPath(ctx, x, y, w, h) {
    var r = h / 2; ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arc(x + w - r, y + r, r, -Math.PI / 2, Math.PI / 2);
    ctx.lineTo(x + r, y + h); ctx.arc(x + r, y + r, r, Math.PI / 2, Math.PI * 1.5); ctx.closePath();
}
// HTML canvas ellipse(x, y, rx, ry, rotation, a0, a1, ccw) for the QML Context2D (whose
// ellipse() takes a bounding box instead). Adds to the current path like the HTML one.
function ellipse(ctx, x, y, rx, ry, rot, a0, a1, ccw) {
    rot = rot || 0; if (a0 === undefined) { a0 = 0; a1 = TAU; }
    var span = a1 - a0;
    if (ccw) { span = a0 - a1; if (span < 0) span += TAU; span = -span; }
    else if (span < 0) span += TAU * Math.ceil(-span / TAU);
    if (Math.abs(span) > TAU) span = span > 0 ? TAU : -TAU;
    var n = Math.max(6, Math.ceil(Math.abs(span) / TAU * 40)), cr = Math.cos(rot), sr = Math.sin(rot);
    for (var i = 0; i <= n; i++) {
        var a = a0 + span * i / n, px = rx * Math.cos(a), py = ry * Math.sin(a);
        var X = x + px * cr - py * sr, Y = y + px * sr + py * cr;
        ctx.lineTo(X, Y);                     // the first one joins the current point, as in HTML
    }
}
// A rounded rectangle path (HTML roundRect isn't in the QML Context2D).
function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// draw options: a 100-unit box centred at (cx, cy), in canvas px (dpr = canvas px per screen px)
function mkO(ctx, cx, cy, box, small, dpr) {
    var k = box / 100;
    return {
        ctx: ctx, cx: cx, cy: cy, box: box, small: small, dpr: dpr, k: k,
        mw: function (px) { return px * dpr / k; },
        lw: function (units, minPx) { return Math.max(units, minPx * dpr / k); },
        begin: function () { ctx.save(); ctx.translate(cx, cy); ctx.scale(k, k); },
        end: function () { ctx.restore(); }
    };
}

// generic particles in unit space
function drawParts(o, s, filter) {
    var ctx = o.ctx;
    for (var i = 0; i < s.parts.length; i++) {
        var p = s.parts[i];
        if (filter && !filter(p)) continue;
        var u = p.life / p.max, a = (p.a0 !== undefined ? p.a0 : 1) * (p.fadeIn ? Math.min(1, u * 6) : 1) * (1 - Math.pow(u, 1.6));
        if (a <= 0.005) continue;
        switch (p.k) {
        case 'dot':
            ctx.fillStyle = rgba(p.col, a); ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(p.r * (1 - 0.4 * u), o.mw(0.8)), 0, TAU); ctx.fill(); break;
        case 'smoke':
            var rr = p.r + u * p.grow; ctx.fillStyle = rgba(p.col, a * 0.35); ctx.beginPath(); ctx.arc(p.x, p.y, rr, 0, TAU); ctx.fill(); break;
        case 'ring':
            var r2 = lerp(p.r, p.r2, easeOutCubic(u)); ctx.strokeStyle = rgba(p.col, (1 - u) * (p.a0 !== undefined ? p.a0 : 0.35));
            ctx.lineWidth = o.lw(lerp(1.6, 0.5, u), 0.6); ctx.beginPath(); ctx.arc(p.x, p.y, r2, 0, TAU); ctx.stroke(); break;
        case 'tri':
            ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.scale(1, Math.cos(p.rot * 1.7));
            ctx.fillStyle = rgba(p.col, a); poly(ctx, [[-p.r, p.r * 0.6], [p.r, p.r * 0.6], [0, -p.r]]); ctx.fill(); ctx.restore(); break;
        case 'trail':
            if (p.tr && p.tr.length > 1) {
                for (var j = 1; j < p.tr.length; j++) {
                    var q = j / p.tr.length; ctx.strokeStyle = rgba(p.col, a * q * 0.7); ctx.lineWidth = Math.max(p.r * 1.4 * q, o.mw(0.7)); ctx.lineCap = 'round';
                    ctx.beginPath(); ctx.moveTo(p.tr[j - 1][0], p.tr[j - 1][1]); ctx.lineTo(p.tr[j][0], p.tr[j][1]); ctx.stroke();
                }
            }
            ctx.fillStyle = rgba(p.col2 || p.col, a); ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(p.r, o.mw(1)), 0, TAU); ctx.fill(); break;
        case 'z': drawZ(ctx, p.x, p.y, p.r * (0.7 + 0.5 * u), p.col, a * 0.8, o.lw(1.6, 0.8)); break;
        }
    }
}

// ---- the simulation behind one character ------------------------------------------
// The host feeds it: inL / inA (mic level, voice amplitude 0..1), lookT / nearT (where
// the pointer is, -1..1 and 0..1; lastMove = when it last moved), calm (reduced motion).
// s.antic = { id, t, dur, u } while an antic plays; s.caught = { id, t } for the short
// exit after an interrupt (the pack's antics[id].exit draws it).
function Sim(def) {
    this.def = def; this.state = 'idle'; this.prev = ''; this.t = 0; this.T = 0; this.it = 0; this.dt = 1 / 60;
    this.L = 0; this.A = 0; this.inL = 0; this.inA = 0; this.hist = []; this.onset = false; this.lastOnset = -9;
    this.look = { x: 0, y: 0 }; this.lookT = { x: 0, y: 0 }; this.near = 0; this.nearT = 0; this.notice = 0; this.wasNear = false;
    this.lastMove = -99; this.lookGoal = { x: 0, y: 0 }; this.wander = { x: 0, y: 0 }; this.wanderAt = 2;
    this.blink = 0; this.blinkAt = 1.5 + random() * 3; this.blinkT = -1;
    this.p = {}; this.cs = {}; this.parts = []; this.calm = false;
    this.progress = 0; this.count = 0; this.mood = 'neutral';
    this.antic = null; this.caught = null; this.small = true;   // small: in the island (the host keeps it current)
    if (def.enter) def.enter(this, 'idle');
}
Sim.prototype.set = function (state) {
    this.prev = this.state; this.state = state; this.t = 0;
    if (this.def.enter) this.def.enter(this, state);
};
Sim.prototype.fo = function (k, target, tau) {
    if (tau === undefined) tau = 0.09;
    var p = this.p; if (p[k] === undefined || !isFinite(p[k])) { p[k] = target; return target; }
    var tt = this.calm ? Math.max(tau, 0.12) : tau;
    p[k] += (target - p[k]) * (1 - Math.exp(-this.dt / tt)); return p[k];
};
Sim.prototype.spawn = function (o) {
    if (this.calm && !o.force) return;
    if (this.parts.length > 56) this.parts.shift();
    var p = { x: 0, y: 0, vx: 0, vy: 0, g: 0, drag: 0, life: 0, max: 1, r: 2, rot: 0, vr: 0, col: C.hot, k: 'dot', tr: null };
    for (var k in o) p[k] = o[k];
    this.parts.push(p);
};
Sim.prototype.histAt = function (d, key) {
    var target = this.T - d, h = this.hist;
    for (var i = h.length - 1; i >= 0; i--) if (h[i].T <= target) return h[i][key];
    return h.length ? h[0][key] : 0;
};
Sim.prototype.step = function (dt, T) {
    this.dt = dt; this.T = T; this.t += dt; if (!this.calm) this.it += dt;
    // audio followers (the same fast-attack / slow-release as the preview)
    var Lt = Math.pow(clamp((this.inL - 0.02) / 0.5), 0.7);
    this.L += (Lt - this.L) * (1 - Math.exp(-dt / (Lt > this.L ? 0.03 : 0.18)));
    var At = clamp(this.inA * 1.05);
    this.A += (At - this.A) * (1 - Math.exp(-dt / (At > this.A ? 0.025 : 0.14)));
    this.hist.push({ T: T, L: this.L, A: this.A });
    while (this.hist.length && this.hist[0].T < T - 0.7) this.hist.shift();
    var Aold = this.histAt(0.06, 'A');
    this.onset = false;
    if (this.A - Aold > 0.18 && this.A > 0.25 && T - this.lastOnset > 0.16) { this.onset = true; this.lastOnset = T; }
    // where the pointer is; with no pointer for a while, idle eyes wander
    var lt = this.lookT;
    if (T - this.lastMove > 4.5) {
        if (this.state === 'idle' && !this.calm && !this.antic) {
            this.wanderAt -= dt;
            if (this.wanderAt <= 0) { this.wanderAt = 2.5 + random() * 3.5; this.wander = random() < 0.35 ? { x: 0, y: 0 } : { x: rnd(-0.6, 0.6), y: rnd(-0.3, 0.3) }; }
            lt = this.wander;
        } else lt = { x: 0, y: 0 };
        this.nearT *= Math.exp(-dt / 0.5);
    }
    this.lookGoal = lt;
    var kl = 1 - Math.exp(-dt / 0.12);
    this.look.x += (lt.x - this.look.x) * kl; this.look.y += (lt.y - this.look.y) * kl;
    this.near += (this.nearT - this.near) * (1 - Math.exp(-dt / 0.2));
    if (this.nearT > 0.55 && !this.wasNear) { this.wasNear = true; this.notice = 1; }
    if (this.nearT < 0.3) this.wasNear = false;
    this.notice *= Math.exp(-dt / 0.35);
    if (this.notice < 0.01) this.notice = 0;
    // blink (not while asleep)
    if (!this.calm && this.state !== 'off' && this.state !== 'asleep') {
        this.blinkAt -= dt;
        if (this.blinkAt <= 0) { this.blinkT = 0; this.blinkAt = random() < 0.18 ? 0.28 : 2.6 + random() * 4.5; }
    }
    if (this.blinkT >= 0) { this.blinkT += dt; this.blink = Math.sin(Math.PI * clamp(this.blinkT / 0.16)); if (this.blinkT > 0.16) { this.blinkT = -1; this.blink = 0; } }
    // particles
    for (var i = 0; i < this.parts.length; i++) {
        var p = this.parts[i];
        p.life += dt; p.vy += p.g * dt;
        if (p.drag) { var d = Math.exp(-p.drag * dt); p.vx *= d; p.vy *= d; }
        p.x += p.vx * dt; p.y += p.vy * dt; p.rot += p.vr * dt;
        if (p.k === 'trail') { (p.tr || (p.tr = [])).push([p.x, p.y]); if (p.tr.length > 10) p.tr.shift(); }
    }
    this.parts = this.parts.filter(function (q) { return q.life < q.max; });
    // antic clocks
    if (this.antic) { this.antic.t += dt; this.antic.u = clamp(this.antic.t / this.antic.dur); }
    if (this.caught) { this.caught.t += dt; if (this.caught.t > (this.caught.dur || 0.3)) this.caught = null; }
    this.def.update(this, dt);
    var an = this.antic && this.def.antics && this.def.antics[this.antic.id];
    if (an && an.update) an.update(this, dt, this.antic);
    var ca = this.caught && this.def.antics && this.def.antics[this.caught.id];
    if (ca && ca.exitUpdate) ca.exitUpdate(this, dt, this.caught);
};
// What the pack draws this frame: the state, then the antic (or its caught exit) on top.
Sim.prototype.draw = function (o) {
    var def = this.def;
    var an = this.antic && def.antics && def.antics[this.antic.id];
    if (an && an.under) { o.begin(); an.under(o, this, this.antic); o.end(); }
    def.draw(o, this);
    if (an && an.draw) { o.begin(); an.draw(o, this, this.antic); o.end(); }
    var ca = this.caught && def.antics && def.antics[this.caught.id];
    if (ca && ca.exit) { o.begin(); ca.exit(o, this, this.caught, clamp(this.caught.t / (this.caught.dur || 0.3))); o.end(); }
};
// Is something moving that needs a frame soon (particles, a blink, a fresh state)?
Sim.prototype.busy = function () {
    return this.parts.length > 0 || this.blinkT >= 0 || this.notice > 0.02 || this.caught !== null
        || Math.abs(this.near - this.nearT) > 0.02 || Math.abs(this.look.x - this.lookGoal.x) + Math.abs(this.look.y - this.lookGoal.y) > 0.02
        || this.t < 1.2;
};

// The default frame rate a state wants (fps), within the registry's budgets. A pack may
// override with def.fps(s, B) returning a number (still capped by the budgets).
var LIVE = { listening: 1, talking: 1, thinking: 1, woke: 1, holding: 1, followup: 1, input: 1, dictating: 1,
             happy: 1, done: 1, confused: 1, error: 1, voice_mismatch: 1, sending: 1, waking: 1, approval_needed: 1, loading: 1 };
function wantFps(s, B) {
    var st = s.state, calm = s.calm;
    if (s.caught) return B.live_fps;
    if (s.antic) return B.antic_fps;          // (the scheduler never starts one in calm; "play now" may)
    if (st === 'thinking' && s.t > 30) return calm ? 15 : 30;
    if (st === 'working') return calm ? 0 : Math.min(B.live_fps, 24);
    // long waits after their entrance: an approval card, a model load
    if ((st === 'approval_needed' || st === 'loading') && s.t > 1.2) return calm ? 0 : Math.min(B.live_fps, 24);
    if (LIVE[st]) return calm && s.t > 1.2 && !(st === 'listening' || st === 'talking' || st === 'holding' || st === 'followup' || st === 'dictating') ? 0 : B.live_fps;
    if (calm) return 0;
    // resting: short 30 fps bursts for a blink, the pointer arriving, a fresh state;
    // particles (a drifting z, a curl of smoke) just ride the resting rate
    if (s.blinkT >= 0 || s.notice > 0.02 || s.caught !== null || s.t < 1.2
        || Math.abs(s.near - s.nearT) > 0.02 || Math.abs(s.look.x - s.lookGoal.x) + Math.abs(s.look.y - s.lookGoal.y) > 0.02)
        return Math.min(B.antic_fps, 30);
    if (st === 'idle') return B.idle_fps;
    if (st === 'drowsy') return Math.max(1, B.idle_fps / 2);
    return B.rest_fps;                       // off, asleep, paused: a slow drift at most
}
