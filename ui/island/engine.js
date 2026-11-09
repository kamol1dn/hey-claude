// Ember & Thread: the island's motion engine (docs/island-motion.md).
//
// A port of the approved preview (docs/island-motion-preview.html) to Qt's V4
// JavaScript: springs that are only ever retargeted (direction picks inhale or
// exhale), one-pole followers, tweens, labels, the 48-sample thread with
// snapshot blending, one-shots, and the driver tiers. It draws nothing itself:
// frame() integrates and fills `out`, which Island.qml applies to a ShaderEffect
// (GPU) or a Canvas (software) plus Shapes and Text items.
//
// Imported without `.pragma library`, so every Island instance has its own state.
// Everything time-based reads env.now() (ms), so tests can drive a fixed clock.

/* ---------------- colours ---------------- */
function hex(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
var C = {
    ash: hex('#5C5652'), banked: hex('#B8664C'), live: hex('#D97757'), hot: hex('#EC9A78'),
    white: hex('#FFE3D6'), cool: hex('#B38A7A'), user: hex('#F2F2F2'), ok: hex('#6CCB7A'),
    okCore: hex('#B8F0C1'), bad: hex('#E5646A'), fg: hex('#F2F2F2'), dim: hex('#9A9A9A'), rimW: [255, 255, 255]
};
function mixc(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }

/* ---------------- easing ---------------- */
function clamp(x, a, b) { if (a === undefined) a = 0; if (b === undefined) b = 1; return x < a ? a : x > b ? b : x; }
function outCubic(p) { return 1 - Math.pow(1 - p, 3); }
function inCubic(p) { return p * p * p; }
function inQuad(p) { return p * p; }
function outQuad(p) { return 1 - (1 - p) * (1 - p); }
function outSine(p) { return Math.sin(p * Math.PI / 2); }
function inOutSine(p) { return 0.5 - 0.5 * Math.cos(Math.PI * p); }
function linear(p) { return p; }
function outBack(s) { return function (p) { var c3 = s + 1; return 1 + c3 * Math.pow(p - 1, 3) + s * Math.pow(p - 1, 2); }; }
function bezier(x1, y1, x2, y2) {
    var cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx, cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    function X(t) { return ((ax * t + bx) * t + cx) * t; }
    function Y(t) { return ((ay * t + by) * t + cy) * t; }
    function dX(t) { return (3 * ax * t + 2 * bx) * t + cx; }
    return function (p) {
        var t = p;
        for (var i = 0; i < 6; i++) { var d = dX(t); if (Math.abs(d) < 1e-6) break; t -= (X(t) - p) / d; }
        return Y(clamp(t));
    };
}
var nib = bezier(0.2, 0, 0, 1);

/* ---------------- seeded rng (golden frames) ---------------- */
var seed = 0x9e3779b9;
function setSeed(s) { seed = (s >>> 0) || 0x9e3779b9; }
function rnd() { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return ((seed >>> 0) % 100000) / 100000; }

/* ---------------- environment ---------------- */
var env = {
    now: function () { return Date.now(); },
    tw: function (s, big) { return s.length * (big ? 7 : 6.2); },      // replaced by FontMetrics
    log: function (m) { console.log(m); },
    pinList: false
};
var MOTION = 'full';            // full | calm | off
var NOW = 0;
var LIVE_CAP = 60;              // 30 on battery or the software backend
var awake = false;              // an outside event wants frames now (FrameAnimation)
var inFrame = false;            // retargets made by frame() itself don't count: busy() sees them
function wake() { if (!inFrame) awake = true; }

/* ---------------- springs, followers, tweens, timers ---------------- */
var PRE = { inhale: { k: 380, z: 0.70 }, exhale: { k: 180, z: 1 }, snap: { k: 520, z: 0.60 }, soft: { k: 120, z: 0.85 },
            glide: { k: 320, z: 1 } };   // width: critically damped, so the pill's edges (and the text on them) never swing back
var SPRINGS = [];
function Spring(x) { this.x = x; this.v = 0; this.t = x; this.k = 180; this.c = 2 * Math.sqrt(180); this.tw = null; this.born = NOW; SPRINGS.push(this); }
Spring.prototype.to = function (t, pre) {
    wake();
    if (pre === undefined) pre = 'dir';
    if (MOTION === 'off') { this.x = this.t = t; this.v = 0; this.tw = null; return this; }
    if (MOTION === 'calm') {
        if (t !== this.t || (!this.tw && Math.abs(this.x - t) > 1e-3)) this.tw = { from: this.x, to: t, t0: NOW, d: 160 };
        this.t = t; this.v = 0; return this;
    }
    if (pre === 'dir') pre = t > this.x ? 'inhale' : 'exhale';
    var p = PRE[pre]; this.k = p.k; this.c = 2 * p.z * Math.sqrt(p.k); this.t = t; this.tw = null; this.born = NOW;
    // glide never overshoots, even when retargeted mid-flight: speed carried toward the new
    // target is capped at what a critically damped spring can absorb (|v| <= w * |x - t|)
    if (pre === 'glide' && this.v * (t - this.x) > 0) {
        var vmax = Math.sqrt(p.k) * Math.abs(t - this.x);
        if (Math.abs(this.v) > vmax) this.v = this.v > 0 ? vmax : -vmax;
    }
    return this;
};
Spring.prototype.set = function (t) { this.x = this.t = t; this.v = 0; this.tw = null; wake(); return this; };
Spring.prototype.step = function (h) { if (this.tw) return; var a = -this.k * (this.x - this.t) - this.c * this.v; this.v += a * h; this.x += this.v * h; };
Spring.prototype.tick = function () {
    if (this.tw) { var p = clamp((NOW - this.tw.t0) / this.tw.d); this.x = this.tw.from + (this.tw.to - this.tw.from) * outCubic(p); if (p >= 1) this.tw = null; }
    if (!isFinite(this.x) || !isFinite(this.v)) { env.log('[island] spring NaN, snapped'); STATS.nan++; this.set(this.t); }
    if (!this.tw && NOW - this.born > 3000 && (Math.abs(this.x - this.t) > 0.25 || Math.abs(this.v) > 2)) { env.log('[island] spring stalled, snapped'); this.set(this.t); }
};
Spring.prototype.settled = function () { return !this.tw && Math.abs(this.x - this.t) < 0.05 && Math.abs(this.v) < 0.5; };

var FOLS = [];
function F(x, tau) { this.x = x; this.t = x; this.tau = tau === undefined ? 0.12 : tau; FOLS.push(this); }
F.prototype.to = function (t, tau) { if (t !== this.t) wake(); this.t = t; if (tau !== undefined && tau !== null) this.tau = tau; if (MOTION === 'off') this.x = t; return this; };
F.prototype.step = function (dt) { this.x += (this.t - this.x) * (1 - Math.exp(-dt / Math.max(this.tau, 0.001))); };
function FC(c, tau) { this.r = new F(c[0], tau); this.g = new F(c[1], tau); this.b = new F(c[2], tau); }
FC.prototype.to = function (c, tau) { this.r.to(c[0], tau); this.g.to(c[1], tau); this.b.to(c[2], tau); return this; };
FC.prototype.set = function (c) { this.r.x = this.r.t = c[0]; this.g.x = this.g.t = c[1]; this.b.x = this.b.t = c[2]; };
FC.prototype.c = function () { return [this.r.x, this.g.x, this.b.x]; };

var TWEENS = [];
function T(o, k, to, d, e, delay) {
    if (e === undefined) e = outCubic;
    if (delay === undefined) delay = 0;
    TWEENS = TWEENS.filter(function (w) { return !(w.o === o && w.k === k); });
    wake();
    if (MOTION === 'calm') d = Math.min(d, 160);
    if (MOTION === 'off' || d <= 0) { if (delay <= 0) { o[k] = to; return; } d = 0; }
    TWEENS.push({ o: o, k: k, to: to, d: d, e: e, start: NOW + delay, from: null });
}
function runTweens() {
    TWEENS = TWEENS.filter(function (w) {
        if (NOW < w.start) return true;
        if (w.from === null) w.from = w.o[w.k];
        var p = w.d <= 0 ? 1 : clamp((NOW - w.start) / w.d);
        w.o[w.k] = w.from + (w.to - w.from) * w.e(p);
        return p < 1;
    });
}
var TIMERS = [];
function at(ms, fn, tag) { TIMERS.push({ t: NOW + ms, fn: fn, tag: tag || 'state' }); wake(); }
function cancel(tag) { TIMERS = TIMERS.filter(function (x) { return x.tag !== tag; }); }
function runTimers() {
    var due = TIMERS.filter(function (x) { return x.t <= NOW; });
    if (!due.length) return;
    TIMERS = TIMERS.filter(function (x) { return x.t > NOW; });
    due.sort(function (a, b) { return a.t - b.t; }).forEach(function (x) { x.fn(); });
}

/* ---------------- fonts / sizes ---------------- */
function tw(s, big) { return env.tw(s, !!big); }
function elide(s, big, max) {
    if (tw(s, big) <= max) return s;
    var lo = 0, hi = s.length;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (tw(s.slice(0, mid) + '…', big) <= max) lo = mid; else hi = mid - 1; }
    return s.slice(0, lo).replace(/\s+$/, '') + '…';
}
function elideLeft(s, big, max) {
    if (tw(s, big) <= max) return s;
    var lo = 0, hi = s.length;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (tw('…' + s.slice(s.length - mid), big) <= max) lo = mid; else hi = mid - 1; }
    return '…' + s.slice(s.length - lo).replace(/^\s+/, '');
}
function clampW(w) { return Math.min(560, Math.max(300, Math.round(w))); }
var CTRL = 66, MAXT = 560 - 90 - CTRL;
// While a turn's panel is open (question, card, answer under the head) the pill keeps one
// width from the question to the collapse: nothing moves sideways during a turn.
var TURN_W = 440, ROW_X = 26, ROW_PAD = 16;
function turnW() { return Math.max(TURN_W, S.extraW || 0); }
function rowW() { return turnW() - ROW_X - ROW_PAD; }
var NOTME_W = 70;

/* ---------------- island model ---------------- */
var W, H, HEAD, EX, ER, TX0, TX1, SUFX, ARC, EC, GC, RIMC, TC, CORE, GA, GR, RIMA, TW, TA, TAPER, AMPK, PATIENCE, HOV, HM, HS, HN, HIDE;
var O, K, OV, S, AU, LB, SUF, TASKS;
var STATS = { nan: 0, frames: 0, maxEdge: 0, maxThread: 0 };

function makeModel() {
    SPRINGS = []; FOLS = []; TWEENS = []; TIMERS = [];
    W = new Spring(150); H = new Spring(28); HEAD = new Spring(28); EX = new Spring(18); ER = new Spring(4);
    TX0 = new Spring(18); TX1 = new Spring(18); SUFX = new Spring(70); ARC = new Spring(0);
    EC = new FC(C.live); GC = new FC(C.live); RIMC = new FC(C.rimW); TC = new FC(C.user); CORE = new F(0.3);
    GA = new F(0.07); GR = new F(22); RIMA = new F(0.086); TW = new F(1.75); TA = new F(0); TAPER = new F(0.8); AMPK = new F(1);
    PATIENCE = new F(1, 0.7);
    HOV = new F(0, 0.06); HM = new F(0, 0.05); HS = new F(0, 0.05); HN = new F(0, 0.05);
    HIDE = new F(1, 0.12);              // 1 = shown; hide-when-idle fades the pill to 0
    O = { slash: 0, pilotOn: 0, arcA: 0, field: 0, fieldX: 6, trim: 0, bracket: 0, bracketA: 0, bracketOff: 0,
        check: 0, checkW: 1.5, wash: 0, washReveal: 0, drain: 0, ghost: 0, ghostX: 0, words: 1, ul: 0, ulStart: 0, satVis: 1, progress: 0,
        ctl: 0, pressM: 0, pressS: 0, notme: 0, drop: 0, muted: 0 };
    K = { r: 0, rt: 0, g: 0, gt: 0 };
    OV = {};
    S = { state: 'ready', prev: null, enteredAt: 0, lastChange: -1e9, changes: [], floodUntil: 0, resting: 'ready',
        breathT0: 0, hover: false, ctlHover: null, listOpen: false, listGeom: false, badge: '', wakeLabel: 'hey claude',
        done: null, wokeGlow: false, lastWoke: -1e9, pluckT0: -1e9, trem: null, shake: null, baseTau: 0.12,
        typed: '', ghost: '', lastKey: -1e9, thinkText: '', errText: '', errW: 0, loadText: 'starting up…', loadT0: 0,
        pauseT0: 0, pauseDur: 3600000, pauseEnd: 0, arcLast: 0, fuT0: 0, fuDur: 5000, fuEnded: false,
        words: [], speakT0: 0, wordIdx: -1, speakW: 300, lines: 1, lastRipple: -1e9, speakText: '', revealAcc: 0, speakTop: 0,
        silentFor: 0, penTheta: 0, phi: 0, sparks: [], nextSpark: 0, rings: [], partial: '', notMe: false,
        flash: '', flashUntil: 0, extraH: 0, extraW: 0, hideIdle: false, peek: false, lastU: 0, pend: null,
        inputLines: 1, ctlCy: 20, enteredRestAt: 0, slowSince: 0, readyFastSince: 0, toolLabel: '', drop: false,
        lastFrameAt: 0, emaFt: 0, slowFrames: 0, calmByGpu: false, sync: false, plans: [], sentEnd: 0,
        turn: false, rowTop: 0, rowLines: 0, rowMax: 3 };
    AU = { L: 0, A: 0, micRaw: 0, ttsRaw: 0, hist: [], aHist: [], micOpen: false, ttsT: -1e9, micT: -1e9 };
    TASKS = [];
}

function topOV() { var b = null; for (var k in OV) if (!b || OV[k].p > b.p) b = OV[k]; return b; }
function ov(name, c, tau, p) { if (c) OV[name] = { c: c, tau: tau, p: p }; else delete OV[name]; wake(); }
function kickR(a) { K.rt += a; wake(); }
function kickG(a) { K.gt += a; wake(); }
function isRest(s) { return s === 'ready' || s === 'off' || s === 'paused'; }
function flood() { return NOW < S.floodUntil; }

/* ---------------- labels ---------------- */
function Labels(o) { this.L = []; this.last = -1e9; this.o = o; }
Labels.prototype.set = function (spec) {
    wake();
    var cur = null;
    for (var i = 0; i < this.L.length; i++) if (!this.L[i].out) { cur = this.L[i]; break; }
    if (spec && cur && cur.text === spec.text && cur.x === spec.x) { cur.color = spec.color; return; }
    if (!spec && !cur) return;
    if (spec && cur && NOW - this.last < 80 && cur.a < 0.35) { Object.assign(cur, spec); this.last = NOW; return; }
    for (var j = 0; j < this.L.length; j++) { var l = this.L[j]; if (!l.out) { l.out = true; l.t0 = NOW; l.a0 = l.a; l.dy0 = l.dy; } }
    if (spec) {
        var useNib = !!spec.nib && MOTION === 'full' && !flood();
        this.L.push(Object.assign({ yo: 0, big: false, icon: '' }, spec, { nib: useNib, a: 0, dy: this.o.inDy, t0: NOW, out: false,
            rev: useNib ? 0 : 1, revD: clamp(18 * spec.text.length, 120, 320) }));
    }
    while (this.L.length > 2) this.L.shift();
    this.last = NOW;
};
Labels.prototype.step = function () {
    var calm = MOTION !== 'full', off = MOTION === 'off', o = this.o;
    this.L = this.L.filter(function (l) {
        if (l.out) {
            var p = off ? 1 : clamp((NOW - l.t0) / o.outD);
            l.a = l.a0 * (1 - inQuad(p)); l.dy = calm ? 0 : l.dy0 + (o.outDy - l.dy0) * p;
            return p < 1;
        }
        var del = calm ? 0 : o.inDelay, d = calm ? 120 : o.inD;
        var q = off ? 1 : clamp((NOW - l.t0 - del) / d), e = outCubic(q);
        l.a = l.nib ? Math.min(1, e * 1.4) : e; l.dy = calm ? 0 : o.inDy * (1 - e);
        if (l.nib) l.rev = clamp((NOW - l.t0 - del) / l.revD);
        return true;
    });
};
Labels.prototype.busy = function () {
    for (var i = 0; i < this.L.length; i++) { var l = this.L[i]; if (l.out || l.a < 0.999 || Math.abs(l.dy) > 0.01 || (l.nib && l.rev < 1)) return true; }
    return false;
};

/* ---------------- tasks ---------------- */
function runningN() { return TASKS.filter(function (t) { return t.status === 'running'; }).length; }
function satAngle() {
    var t = isRest(S.state) && MOTION !== 'off' ? Math.floor(NOW / 100) / 10 : NOW / 1000;
    return MOTION === 'full' ? t / 10 * Math.PI * 2 : 0;
}
function respace() {
    var rs = TASKS.filter(function (t) { return t.status === 'running'; });
    rs.forEach(function (tk, i) {
        var tgt = i / rs.length * Math.PI * 2;
        if (MOTION !== 'full') { tk.phase = [Math.PI * 0.5 - 0.0, Math.PI * 1.17, Math.PI * 1.83][i % 3] - Math.PI / 2; return; }
        if (tk.phase === null) tk.phase = tgt;
        else { var d = tgt - tk.phase; d = ((d + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI; T(tk, 'phase', tk.phase + d, 600, inOutSine); }
    });
}
function taskRow(r) {
    return { id: r.id, name: r.name, status: r.status, right: r.right || '', elapsed: r.elapsed || '',
        t0: NOW, orb: 0, a: 0, phase: null, fly: null, ra: 0, rdy: 6, doneAt: null };
}
// rows: [{id, name, status: running|done|failed|stopped, right, elapsed}] (newest last)
function setTasks(rows) {
    NOW = env.now();
    var seen = {};
    for (var i = 0; i < rows.length; i++) {
        var r = rows[i]; seen[r.id] = true;
        var tk = null;
        for (var j = 0; j < TASKS.length; j++) if (TASKS[j].id === r.id) { tk = TASKS[j]; break; }
        var st = (r.status === 'starting' || r.status === 'running') ? 'running' : r.status;
        if (!tk) {
            tk = taskRow(r); tk.status = st; TASKS.push(tk);
            if (st === 'running') { respace(); T(tk, 'orb', 9, 400, outBack(1.2)); T(tk, 'a', 1, 400, outCubic); }
            else { tk.orb = 9; tk.a = 1; tk.doneAt = NOW; }
            if (S.listOpen) { tk.ra = 0; T(tk, 'ra', 1, 200, outCubic); T(tk, 'rdy', 0, 200, outCubic); }
        } else {
            tk.name = r.name; tk.right = r.right || ''; tk.elapsed = r.elapsed || '';
            if (tk.status === 'running' && st !== 'running') { tk.status = st; taskDone(tk); }
            else tk.status = st;
        }
    }
    TASKS = TASKS.filter(function (t) { return seen[t.id] || t.fly; });
    if (TASKS.length > 5) TASKS = TASKS.slice(TASKS.length - 5);
    restLabel(); resize(); wake();
}
function taskDone(tk) {
    cancel('done');
    var ang = (tk.phase || 0) + satAngle(), fromX = EX.x + Math.cos(ang) * tk.orb, fromY = HEAD.x / 2 + Math.sin(ang) * tk.orb;
    tk.doneAt = NOW;
    var ok = tk.status === 'done';
    var rest = isRest(S.state);
    if (!ok) { respace(); return; }                         // failed / stopped: the row shows it, no ceremony
    if (!rest || MOTION === 'off') {                         // inside a voice state: absorb + tint only
        ov('done', mixc(C.live, C.ok, 0.6), 0.08, 3); at(600, function () { ov('done'); }, 'done'); respace(); return;
    }
    if (MOTION === 'calm') {
        ov('done', C.ok, 0.08, 3); O.check = 0; T(O, 'check', 1, 150, linear); S.done = tk.name; restLabel(); resize();
        at(800, function () { ov('done'); }, 'done');
        at(3600, function () { T(O, 'check', 0, 150, linear); S.done = null; restLabel(); resize(); respace(); }, 'done');
        return;
    }
    tk.fly = { t0: NOW, x: fromX, y: fromY };
    at(220, function () { tk.fly = null; ov('done', C.ok, 0.08, 3); kickR(1.6); GC.to(C.ok, 0.05); kickG(0.22); at(480, function () { GC.to(C.live, 0.25); }, 'done'); respace(); }, 'done');
    at(240, function () { O.check = 0; T(O, 'check', 1, 240, nib); O.checkW = 1.5; }, 'done');
    at(300, function () { S.done = tk.name; restLabel(); resize(); }, 'done');
    at(480, function () { T(O, 'checkW', 2.2, 90, outQuad); at(90, function () { T(O, 'checkW', 1.75, 220, outCubic); }, 'done'); }, 'done');
    at(700, function () { ov('done'); tauBack(0.4); }, 'done');
    at(3600, function () { T(O, 'check', 0, 300, inCubic); S.done = null; restLabel(); resize(); }, 'done');
}
function tauBack(t) { S.baseTau = t; at(t * 3000, function () { S.baseTau = 0.12; }, 'g'); }

/* ---------------- size / labels per state ---------------- */
function readyMain() {
    var n = runningN();
    if (NOW < S.flashUntil && S.flash) return S.flash;
    return n ? (n + ' task' + (n > 1 ? 's' : '') + ' running') : S.wakeLabel;
}
function badgeText() { return S.badge ? ' · ' + S.badge : ''; }
function restLabel() {
    var st = S.state, n = runningN();
    if (!isRest(st) && st !== 'mismatch') return;
    if (st === 'mismatch') { LB.set({ text: 'not your voice?', x: 30, color: C.dim }); SUF.set(null); return; }
    if (S.done) { LB.set({ text: 'done · ' + S.done, x: 46, color: C.fg, nib: true }); SUF.set(null); return; }
    if (st === 'ready') {
        var main = readyMain(), flashing = NOW < S.flashUntil && S.flash;
        LB.set({ text: main, x: 30, color: (n || flashing) ? C.fg : C.dim }); SUFX.to(tw(main), 'soft');
        if (flashing || !S.badge) SUF.set(null);
        else SUF.set({ text: badgeText(), x: 30, color: n ? C.fg : C.dim });
    } else if (st === 'off') {
        LB.set({ text: n ? ('off · ' + n + ' running') : 'listening off', x: 30, color: C.dim }); SUF.set(null);
    } else if (st === 'paused') {
        LB.set({ text: 'paused', x: 30, color: C.dim }); SUFX.to(tw('paused'), 'soft');
    }
}
function readyW() {
    if (S.done) return tw('done · ' + S.done) + 16 + 46;
    if (S.state === 'mismatch') return tw('not your voice?') + 46;
    var main = readyMain();
    return tw(main + ((NOW < S.flashUntil && S.flash) ? '' : badgeText())) + 46;
}
function rowsN() { return Math.min(5, TASKS.length); }
function listRowsH() { return 30 * rowsN() + (S.usageRow ? 30 : 0); }
function headFor(st) {
    switch (st) {
        case 'ready': return S.turn ? 40 : 28;
        case 'mismatch': case 'off': case 'paused': return 28;
        case 'input': return 46 + 18 * (S.inputLines - 1);
        default: return 40;
    }
}
function resize(pre) {
    if (pre === undefined) pre = 'dir';
    var st = S.state, w = 300, head = headFor(st);
    switch (st) {
        case 'ready': case 'mismatch': w = Math.max(120, readyW()); break;
        case 'off': w = (runningN() || S.done) ? Math.max(170, S.done ? readyW() : 0) : 130; break;
        case 'paused': w = S.done ? Math.max(170, readyW()) : 170; break;
        case 'loading': w = clampW(tw(S.loadText) + 80); break;
        case 'input': w = 560; break;
        case 'listening': case 'woke':
            w = S.partial ? clampW(216 + tw(S.partial) + 16) : 300;
            if (S.notMe && !S.partial) w = 300 + NOTME_W;
            break;
        case 'holding': w = S.partial ? clampW(224 + tw(S.partial) + 16) : 300; break;
        case 'followup': w = 300; break;
        case 'thinking': w = Math.min(560, Math.max(300, (S.toolLabel ? tw(elide(S.toolLabel, true, MAXT - 26), true) + 26 : tw(S.thinkText, true)) + 90) + CTRL); break;
        case 'speaking': w = S.speakW; break;
        case 'error': w = clampW(tw(S.errText, true) + 56); break;
    }
    if (S.turn && ['woke', 'listening', 'holding', 'followup', 'thinking', 'speaking', 'ready', 'error'].indexOf(st) >= 0) w = turnW();
    var h = head + (st === 'speaking' && S.lines > 1 && !S.turn ? 20 : 0);
    if (S.extraW) w = Math.max(w, S.extraW);
    if (S.extraH) h += S.extraH;
    if (S.listGeom) { w = Math.max(w, 460); h = head + 12 + listRowsH() + (S.extraH || 0); }
    // in a turn the panel's height glides too: an answer streaming in grows it line after line,
    // and a bouncy spring restarted every few frames reads as a wobbling bottom edge
    W.to(Math.round(w), pre === 'snap' ? 'snap' : 'glide'); H.to(Math.round(h), S.turn && pre !== 'snap' ? 'glide' : pre); HEAD.to(head, pre);
    EX.to(head >= 46 ? 22 : head >= 40 ? 20 : 18, 'soft');
}

/* ---------------- state machine ---------------- */
function go(st, o) {
    if (o === undefined) o = {};
    NOW = env.now();
    if (st === 'woke' && NOW - S.lastWoke < 600 && ['woke', 'listening', 'holding'].indexOf(S.state) >= 0) { kickG(0.15); return; }
    if (st !== 'woke' && st !== S.state && NOW - S.lastChange < 60) {   // flap guard: coalesce into the latest
        S.pend = [st, o]; cancel('pend');
        at(60 - (NOW - S.lastChange), function () { var p = S.pend; S.pend = null; if (p) enter(p[0], p[1]); }, 'pend');
        return;
    }
    cancel('pend'); S.pend = null;
    enter(st, o);
}
function finishOneShots() {
    for (var i = 0; i < S.rings.length; i++) { var r = S.rings[i]; r.d = Math.min(r.d, (NOW - r.t0) + 90); }
    S.trem = null; S.shake = null; S.sparks = [];
}
function enter(st, o) {
    if (o === undefined) o = {};
    NOW = env.now();
    S.changes.push(NOW); S.changes = S.changes.filter(function (t) { return NOW - t < 1000; });
    if (S.changes.length > 4) S.floodUntil = NOW + 500;
    var prev = S.state;
    cancel('state');
    if (prev === 'off' && st !== 'off') T(O, 'slash', 0, 160, inCubic);
    if (prev === 'paused' && st !== 'paused') { T(O, 'pilotOn', 0, 200, outCubic); T(O, 'arcA', 0, 250, outCubic); SUF.set(null); }
    if (prev === 'holding' && st !== 'holding') { T(O, 'bracketOff', 6, 200, outQuad); T(O, 'bracketA', 0, 200, outQuad); TAPER.to(0.8, 0.07); T(O, 'muted', 0, 160, outQuad); }
    if (prev === 'input' && st !== 'input') T(O, 'field', 0, 120, inQuad);
    if (prev === 'speaking' && st !== 'speaking') {
        // in a turn the row keeps the whole answer: what wasn't said yet (stopped, or the
        // reveal a beat behind the voice) fades in now, nothing is ever cut
        if (S.turn) { for (var wi = S.wordIdx + 1; wi < S.words.length; wi++) S.words[wi].t = NOW; S.wordIdx = S.words.length - 1; }
        else T(O, 'words', 0, 160, inQuad);
    }
    if (prev === 'followup' && st !== 'followup') { T(O, 'wash', 0, 200, outQuad); T(O, 'drain', 0, 200, outQuad); }
    if (prev === 'error' && st !== 'error') { ov('error'); GC.to(C.live, 0.2); }
    if (prev === 'mismatch' && st !== 'mismatch') { ov('mm'); ov('wake'); }
    if (st !== 'woke') { ov('wake'); S.wokeGlow = false; }
    if (st === 'woke') S.trem = null;
    if (st === 'off' || st === 'paused') finishOneShots();
    if (S.done && st !== prev) { S.done = null; cancel('done'); ov('done'); T(O, 'check', 0, 150, inCubic); }
    if (!isRest(st) && st !== 'mismatch') SUF.set(null);
    if (st !== 'listening' && st !== 'woke' && (S.notMe || O.notme > 0)) { S.notMe = false; T(O, 'notme', 0, 120, inQuad); }
    if (['listening', 'holding', 'followup', 'woke'].indexOf(st) < 0) S.partial = '';
    if (st === 'thinking' || st === 'speaking') T(O, 'ctl', 1, 200, outCubic, O.ctl > 0.5 ? 0 : 140);
    else if (O.ctl > 0) T(O, 'ctl', 0, 120, inQuad);
    S.prev = prev; S.state = st; S.enteredAt = NOW; S.lastChange = NOW; S.baseTau = 0.12;
    if (st === 'ready' || st === 'off') S.resting = st;
    if (st === 'paused') S.resting = 'off';
    if (isRest(st)) S.enteredRestAt = NOW;
    var fn = ENTER[st]; if (fn) fn(prev, o);
    if (st !== 'woke' && st !== 'mismatch') resize();
}
function setGen(g) { if (g === GEN) return; SX.set(DX); SY.set(DY); SA.set(DA); GEN = g; blendT0 = NOW; }
function rekindle() {
    if (MOTION !== 'full') return;
    at(100, function () { ov('kindle', C.hot, 0.06, 2); kickR(0.6); kickG(0.10); }, 'state');
    at(360, function () { ov('kindle'); }, 'state');
}

var ENTER = {
    ready: function (prev) {
        ER.to(4.0); setGen('rest'); TA.to(0, 0.1); RIMC.to(C.rimW, 0.15); RIMA.to(0.086, 0.15); GC.to(C.live, 0.2);
        CORE.to(0.3); AMPK.to(1);
        S.breathT0 = Math.floor(NOW / 125) * 125 / 1000 - 0.7 / 0.10;      // phase starts at the exhale midpoint
        if (prev === 'off' || prev === 'paused') rekindle();
        if (prev === 'loading' && MOTION === 'full') kickR(1.4);
        if (prev === 'followup' && MOTION === 'full') kickR(-0.4);
        if (O.satVis !== 1) T(O, 'satVis', 1, 300, outCubic);
        restLabel();
    },
    off: function () {
        ER.to(3.0); S.baseTau = 0.14; setGen('rest'); TA.to(0, 0.1); RIMC.to(C.rimW, 0.15); RIMA.to(0.05, 0.2);
        T(O, 'slash', 1, 220, outCubic, 180); T(O, 'satVis', 1, 300, outCubic); restLabel();
    },
    paused: function (prev, o) {
        ER.to(3.0); S.baseTau = 0.14; setGen('rest'); TA.to(0, 0.1); RIMC.to(C.rimW, 0.15); RIMA.to(0.05, 0.2);
        var end = o.until ? o.until : NOW + 3600000;
        S.pauseEnd = end; S.pauseT0 = o.since || NOW; S.pauseDur = Math.max(1000, end - S.pauseT0); S.arcLast = NOW;
        if (O.arcA < 0.01) ARC.set(0); else ARC.to(0, 'exhale');
        T(O, 'pilotOn', 1, 600, outCubic, 400); T(O, 'arcA', 0.35, 300, outCubic, 300);
        T(O, 'satVis', 1, 300, outCubic); restLabel(); SUF.set({ text: pausedSuffix(), x: 30, color: C.dim });
    },
    loading: function (prev, o) {
        S.loadText = o.text || 'loading…';
        ER.to(3.4); setGen('rest'); TA.to(0, 0.1); S.loadT0 = NOW; S.nextSpark = NOW + 200; S.sparks = []; O.progress = o.progress || 0;
        RIMC.to(C.rimW); RIMA.to(0.086); T(O, 'satVis', 0, 200, outQuad);
        LB.set({ text: S.loadText, x: 34, color: C.dim });
    },
    woke: function (prev, o) {
        var fl = flood(); S.lastWoke = NOW; T(O, 'satVis', 0, 150, outQuad);
        LB.set(null); SUF.set(null); RIMC.to(C.live, 0.1); RIMA.to(0.22, 0.1); GC.to(C.live, 0.1); AMPK.to(1);
        TAPER.to(0.8, 0.07);
        if (MOTION !== 'full') {
            ov('wake', C.hot, 0.03, 4); W.to(S.turn ? turnW() : S.notMe ? 300 + NOTME_W : 300); H.to(40 + S.extraH); HEAD.to(40); EX.to(20, 'soft'); ER.to(5);
            TX0.set(40); TX1.set(208); setGen('wave'); TC.to(C.user, 0.05); TA.to(1, 0.05); TW.to(1.75);
            LB.set({ text: 'listening', x: 216, color: C.dim });
            at(160, function () { ov('wake'); });
            at(420, function () { S.state = 'listening'; resize(); });
            return;
        }
        ov('wake', C.white, 0.04, 4); CORE.to(1, 0.05);
        ER.to(3.2, 'snap'); S.wokeGlow = true; GA.to(0.22, 0.05); GR.to(30, 0.06);
        if (!fl && HEAD.x <= 30) { HEAD.to(26, 'snap'); H.to(26 + S.extraH, 'snap'); }
        at(80, function () {
            ER.to(6.5, 'snap'); W.to(S.turn ? turnW() : S.notMe ? 300 + NOTME_W : 300, 'glide');   // the catch bounces in the ember, not the width HEAD.to(40, 'snap'); H.to(40 + S.extraH, 'snap'); EX.to(20, 'soft');
            GA.to(0.42, 0.06); GR.to(90, 0.06);
        });
        if (!fl) at(120, function () { S.rings.push({ kind: 'ring', x: 20, y: 20, t0: NOW, d: 320, r0: 6, r1: 40, w0: 2, w1: 0.5, a0: 0.5, c: C.live }); });
        at(140, function () { ov('wake', C.hot, 0.09, 4); });
        at(160, function () {
            ER.to(5.0, 'soft'); S.wokeGlow = false; TW.to(1.75);
            if (GEN === 'wave' && TA.x > 0.3) { TC.to(C.live, 0.04); TX0.to(40); TX1.to(208); }
            else { TC.set(C.live); TX0.set(EX.x); TX1.set(EX.x); TX0.to(40, 'inhale'); TX1.to(208, 'inhale'); }
            setGen('wave'); TA.to(1, 0.04);
        });
        at(200, function () { LB.set({ text: S.partial ? elideLeft(S.partial, false, Math.max(60, W.t - 216 - 14)) : 'listening', x: 216, color: S.partial ? C.fg : C.dim, nib: !S.partial }); });
        at(220, function () { ov('wake'); TC.to(C.user, 0.045); });
        if (!fl) at(260, function () { S.pluckT0 = NOW; });
        at(420, function () { S.state = 'listening'; resize(); listenLabel(); });
    },
    listening: function (prev) {
        if (prev === 'holding') { if (MOTION === 'full') { W.to(304, 'snap'); at(90, function () { resize(); }, 'state'); } }
        TX0.to(40); TX1.to(208);
        setGen('wave'); TC.to(C.user, 0.07); TA.to(1, 0.06); TW.to(1.75); TAPER.to(0.8, 0.07); AMPK.to(1, 0.15);
        RIMC.to(C.live, 0.1); RIMA.to(0.22, 0.1); GC.to(C.live, 0.2); T(O, 'satVis', 0, 150, outQuad);
        listenLabel();
    },
    holding: function (prev, o) {
        var muted = !!o.muted;
        TX0.to(40); TX1.to(208); setGen('wave'); TC.to(muted ? C.dim : C.user, 0.07); TA.to(muted ? 0.45 : 1, 0.06); TW.to(1.75); TAPER.to(0.45, 0.07); AMPK.to(muted ? 0 : 1, 0.15);
        RIMC.to(muted ? C.rimW : C.live, 0.1); RIMA.to(muted ? 0.12 : 0.40, 0.1); T(O, 'satVis', 0, 150, outQuad);
        T(O, 'muted', muted ? 1 : 0, 160, outCubic);
        if (O.bracketA < 0.01) { O.bracketOff = 0; O.bracketA = 1; O.bracket = 0; }
        else { T(O, 'bracketOff', 0, 140, outCubic); T(O, 'bracketA', 1, 140, outCubic); }
        T(O, 'bracket', 1, 140, outBack(1.2));
        if (prev !== 'holding' && MOTION === 'full' && !flood()) { W.to(294, 'snap'); at(90, function () { resize('snap'); }, 'state'); }
        LB.set({ text: muted ? 'muted' : 'holding', x: 224, color: muted ? C.dim : C.live });
    },
    input: function () {
        LB.set(null); S.typed = ''; S.lastKey = NOW; O.trim = 0; T(O, 'trim', 1, 260, nib, 40);
        if (O.field < 0.01) O.fieldX = 6;
        T(O, 'field', 1, 180, outCubic, 120); T(O, 'fieldX', 0, 180, outCubic, 120);
        setGen('base'); TC.to(C.user, 0.07); TA.to(0.25, 0.08); TW.to(1);
        RIMC.to(C.live, 0.1); RIMA.to(0.40, 0.1); GC.to(C.live, 0.2); T(O, 'satVis', 0, 150, outQuad);
    },
    followup: function (prev, o) {
        S.fuT0 = NOW; S.fuEnded = false; S.fuDur = Math.max(1000, (o.seconds || 5) * 1000); S.fuHeard = false;
        // The window's countdown is a ring around the ember that drains (Island.qml
        // followupDrain): nothing sweeps across the pill. A card or the turn's panel is
        // open under the head: a quiet cue, no thread, no "go on" ("listening" when it
        // hears you); without one, "go on" and the listening wave.
        S.fuQuiet = S.extraH > 0;
        if (S.fuQuiet) {
            setGen('rest'); TA.to(0, 0.1); LB.set(null); O.wash = 0; T(O, 'drain', 1, 220, outCubic);
            RIMC.to(C.live, 0.1); RIMA.to(0.12, 0.1); T(O, 'satVis', 0, 150, outQuad);
            return;
        }
        TX0.to(40); TX1.to(208); setGen('wave'); TC.to(C.user, 0.07); TA.to(1, 0.06); TW.to(1.75); TAPER.to(0.8); AMPK.to(0.7, 0.15);
        O.wash = 0; T(O, 'drain', 1, 220, outCubic);
        RIMC.to(C.live, 0.1); RIMA.to(0.22, 0.1); T(O, 'satVis', 0, 150, outQuad);
        LB.set({ text: 'go on', x: 216, color: C.fg });
    },
    thinking: function (prev, o) {
        S.thinkText = elide(o.text || '…', true, MAXT);
        TX0.to(34, 'exhale'); TX1.to(70, 'exhale'); setGen('cursive'); TC.to(C.live, 0.07); TA.to(1, 0.06); TW.to(1.75);
        RIMC.to(C.rimW, 0.15); RIMA.to(0.086, 0.15); GC.to(C.live, 0.2); CORE.to(0.5);
        S.penTheta = 0; PATIENCE.x = PATIENCE.t = 1; T(O, 'satVis', 0, 150, outQuad);
        thinkLabel();
    },
    speaking: function (prev, o) {
        // In a turn, speaking again with the same answer (it paused for a slow sentence) carries on
        // where it was: the words already shown are never revealed a second time.
        var same = S.turn && S.words.length && S.speakText && String(o.text || '').indexOf(S.speakText.slice(0, 24)) === 0;
        var keep = same ? S.wordIdx : -1;
        S.speakText = ''; S.wordIdx = keep; S.revealAcc = 0; S.speakTop = 0; S.plans = []; S.sentEnd = keep + 1;
        layoutWords(o.text || '');
        S.speakT0 = NOW; O.words = 1; LB.set(null);
        TX0.to(34); TX1.to(S.turn ? 208 : 74); setGen('speak'); TC.to(C.live, 0.07); TA.to(1, 0.06); TW.to(2.0);
        RIMC.to(C.live, 0.1); RIMA.to(0.18, 0.12); GC.to(C.live, 0.2); CORE.to(1); T(O, 'satVis', 0, 150, outQuad);
    },
    error: function (prev, o) {
        S.errText = elide(o.text || 'something went wrong', true, 480); S.errW = tw(S.errText, true);
        ov('error', C.bad, 0.06, 5); RIMC.to(C.bad, 0.06); RIMA.to(0.25, 0.08); GC.to(C.bad, 0.06); T(O, 'satVis', 0, 150, outQuad);
        LB.set(null);
        if (MOTION === 'full') {
            ER.to(3.2, 'snap'); S.trem = { t0: NOW, amp: 2.5, tau: 0.11, f: 5.5, dur: 360 };
            kickG(0.08); at(400, function () { ER.to(4.0, 'soft'); });
        } else ER.to(4.0);
        O.ul = 0; O.ulStart = 0; TX0.to(34); TX1.to(34); setGen('uline'); TC.to(C.bad, 0.06); TA.to(0.6, 0.06); TW.to(1);
        at(120, function () { LB.set({ text: S.errText, x: 34, color: C.fg, big: true }); });
        at(MOTION === 'full' ? 400 : 150, function () { T(O, 'ul', 1, MOTION === 'full' ? 220 : 150, MOTION === 'full' ? nib : linear); });
        at(4000, function () { T(O, 'ulStart', 1, 200, inCubic); ov('error'); S.baseTau = 0.6; GC.to(C.live, 0.2); RIMC.to(C.rimW, 0.2); });
        at(4600, function () { go(S.resting); });
    },
    mismatch: function (prev) {
        LB.set(null); SUF.set(null); S.lastWoke = NOW;
        if (MOTION === 'full') { ov('wake', C.white, 0.04, 4); ER.to(3.2, 'snap'); if (HEAD.x <= 30 && !flood()) { HEAD.to(26, 'snap'); H.to(26 + S.extraH, 'snap'); } }
        var reject = MOTION === 'full' ? 140 : 0;
        at(reject, function () {
            ov('wake'); ov('mm', C.cool, 0.12, 5); ER.to(3.6); HEAD.to(28, 'exhale'); H.to(28 + S.extraH, 'exhale'); resize('exhale');
            if (MOTION === 'full') S.shake = { t0: NOW, amp: 2, tau: 0.16, f: 6, dur: 420 };
            at(60, function () { restLabel(); });
        });
        at(reject + 1800, function () { ov('mm'); go(S.resting === 'off' ? 'off' : 'ready'); });
        setGen('rest'); TA.to(0, 0.1); RIMC.to(C.rimW); RIMA.to(0.086);
    }
};

function listenLabel() {
    if (S.state === 'holding') return;
    if (S.partial) LB.set({ text: elideLeft(S.partial, false, Math.max(60, W.t - 216 - 14)), x: 216, color: C.fg });
    else LB.set({ text: 'listening', x: 216, color: C.dim });
}
function thinkLabel() {
    var max = S.turn ? turnW() - 80 - CTRL - 14 : MAXT;
    if (S.toolLabel) LB.set({ text: elide(S.toolLabel, true, max - 26), x: 106, color: C.dim, big: true });
    else LB.set({ text: elide(S.thinkText, true, max), x: 80, color: C.dim, big: true });
}
function pausedSuffix() {
    var rem = Math.max(0, S.pauseEnd - NOW);
    if (rem <= 60000) return rem <= 0 ? ' · resuming' : ' · resuming';
    var m = Math.ceil(rem / 60000);
    return m >= 60 ? (' · ' + Math.floor(m / 60) + 'h ' + (m % 60) + 'm') : (' · ' + m + 'm');
}

/* ---------------- speaking words ---------------- */
// Words wrap onto at most 2 lines of MAXT px; when the answer is longer, the
// view keeps the newest two lines (it scrolls by line, never per word).
function layoutWords(sentence) {
    var keep = S.wordIdx;
    S.speakText = sentence;
    var ws = sentence.split(/\s+/).filter(function (w) { return w.length; });
    var sp = tw(' ', true), x = 0, line = 0, maxW = 0;
    var total = tw(sentence, true);
    var limit = total > MAXT ? Math.min(MAXT, Math.max(total / 2 + 20, 0)) : 1e9;
    if (total > 2 * MAXT) limit = MAXT;
    if (S.turn) limit = rowW();               // in a turn the words go to the answer row
    S.words = [];
    for (var i = 0; i < ws.length; i++) {
        var w = ws[i], ww = tw(w, true);
        if (x > 0 && x + ww > limit) { line++; x = 0; }
        S.words.push({ w: w, x: x, line: line, t: i <= keep ? NOW - 1000 : null, ww: ww });
        x += ww + sp; maxW = Math.max(maxW, x - sp);
    }
    S.lines = line >= 1 ? 2 : 1;
    S.rowLines = line + 1;
    S.wordIdx = Math.min(keep, S.words.length - 1);
    S.speakW = Math.min(560, Math.max(300, Math.min(maxW, MAXT) + 90) + CTRL);
}

/* ---------------- thread (48 samples, snapshot blending) ---------------- */
var N = 48;
var DX = new Float32Array(N), DY = new Float32Array(N), DA = new Float32Array(N);
var GX = new Float32Array(N), GY = new Float32Array(N), GAa = new Float32Array(N);
var SX = new Float32Array(N), SY = new Float32Array(N), SA = new Float32Array(N);
var PX = new Float32Array(N), PY = new Float32Array(N);       // last frame's samples (storm check)
var GEN = 'rest', blendT0 = -1e9;
function Lat(delay) {
    var tt = NOW - delay * 1000, h = AU.hist;
    for (var i = h.length - 1; i >= 0; i--) if (h[i][0] <= tt) return h[i][1];
    return h.length ? h[0][1] : 0;
}
function genFill() {
    var t = NOW / 1000, cy = HEAD.x / 2, L = AU.L, A = AU.A, calm = MOTION !== 'full', ex = EX.x;
    var x0 = TX0.x, x1 = TX1.x, i, u;
    switch (GEN) {
        case 'rest': for (i = 0; i < N; i++) { GX[i] = ex; GY[i] = cy; GAa[i] = 0; } break;
        case 'wave': {
            var pt = (NOW - S.pluckT0) / 1000, pluck = pt >= 0 && pt < 0.2 ? 4 * Math.exp(-pt / 0.09) * Math.sin(2 * Math.PI * 7 * pt) : 0;
            var taut = S.state === 'holding' && S.silentFor > 0.4;
            for (i = 0; i < N; i++) {
                u = i / (N - 1);
                if (calm) { GX[i] = x0 + u * (x1 - x0) * (0.35 + 0.65 * L); GY[i] = cy; GAa[i] = 0.5 + 0.5 * L; continue; }
                var x = x0 + u * (x1 - x0), w = Math.pow(Math.sin(Math.PI * u), TAPER.x);
                var Ad = Math.min(11, 0.6 + 10.5 * Lat(0.36 * (1 - u))) * AMPK.x;
                var trav = Math.sin(2 * Math.PI * (x / 22 + 1.1 * t));
                var y = taut ? (Ad - 0.6) * w * trav + 0.6 * Math.sin(Math.PI * u * 3) * Math.cos(2 * Math.PI * 0.5 * t) : Ad * w * trav;
                if (O.muted > 0.01) y *= (1 - O.muted);
                y += pluck * w;
                GX[i] = x; GY[i] = cy + y; GAa[i] = 1;
            }
            break;
        }
        case 'speak': {
            for (i = 0; i < N; i++) {
                u = i / (N - 1);
                if (calm) { GX[i] = x0 + u * (x1 - x0) * (0.3 + 0.7 * A); GY[i] = cy; GAa[i] = 0.6 + 0.4 * A; continue; }
                var ws = Math.pow(Math.sin(Math.PI * u), 1.2), amp = Math.min(8.5, 0.8 + 8 * Math.pow(A, 0.7));
                GX[i] = x0 + u * (x1 - x0);
                GY[i] = cy + amp * ws * (Math.sin(2 * Math.PI * (2 * u - S.phi)) + 0.35 * Math.sin(2 * Math.PI * (4.6 * u - 1.6 * S.phi)));
                GAa[i] = 1;
            }
            break;
        }
        case 'cursive': {
            if (calm) { for (i = 0; i < N; i++) { GX[i] = x0; GY[i] = cy; GAa[i] = 0; } break; }
            var r = 1.9 * (1 + 0.08 * Math.sin(2 * Math.PI * 0.3 * t)), d = 4.5, span = 6 * Math.PI, win = 3.2 * Math.PI;
            var cyc = span + win, th = S.penTheta % cyc;
            var scale = (x1 - x0) / (span * 1.9);
            for (i = 0; i < N; i++) {
                var q = i / (N - 1), the = th - win * (1 - q);
                if (the < 0 || the > span) { var c = clamp(the, 0, span); GX[i] = x0 + (r * c - d * Math.sin(c)) * scale; GY[i] = cy - d * Math.cos(c); GAa[i] = 0; continue; }
                GX[i] = x0 + (r * the - d * Math.sin(the)) * scale; GY[i] = cy - d * Math.cos(the); GAa[i] = Math.pow(q, 0.8);
            }
            break;
        }
        case 'base': {
            var bx0 = 44, bx1 = bx0 + (W.x - 16 - bx0) * O.trim, by = HEAD.x - 8, bcx = (44 + W.x - 16) / 2;
            for (i = 0; i < N; i++) {
                u = i / (N - 1);
                var bxx = bx0 + u * (bx1 - bx0), byy = by;
                if (!calm && Math.abs(bxx - bcx) < 60) { var hn = 0.5 + 0.5 * Math.cos(Math.PI * (bxx - bcx) / 60); byy += Math.pow(L, 0.6) * 3 * hn * Math.sin(2 * Math.PI * (bxx / 14 + 1.4 * t)); }
                GX[i] = bxx; GY[i] = byy; GAa[i] = 1;
            }
            break;
        }
        case 'uline': {
            var ux0 = 34 + S.errW * O.ulStart, ux1 = 34 + S.errW * O.ul, uy = cy + 9;
            for (i = 0; i < N; i++) { u = i / (N - 1); GX[i] = ux0 + u * (ux1 - ux0); GY[i] = uy; GAa[i] = O.ul > 0.01 ? 1 : 0; }
            break;
        }
    }
}
function thread() {
    genFill();
    var s = (NOW - blendT0) / 220; s = (s >= 1 || MOTION === 'off') ? 1 : outCubic(clamp(s));
    for (var i = 0; i < N; i++) { DX[i] = SX[i] + (GX[i] - SX[i]) * s; DY[i] = SY[i] + (GY[i] - SY[i]) * s; DA[i] = SA[i] + (GAa[i] - SA[i]) * s; }
}

/* ---------------- audio ---------------- */
// Python sends the mic level (0..1, ~12 Hz) and the TTS amplitude (0..1, 30 Hz);
// the followers run per rendered frame, so motion is 60 fps out of slow data.
function micOpen() { return ['woke', 'listening', 'holding', 'input', 'followup'].indexOf(S.state) >= 0; }
function pushLevel(v) { AU.micRaw = v; AU.micT = env.now(); if (micOpen()) wake(); }
function pushTts(v) {
    var now = env.now(); AU.ttsRaw = v; AU.ttsT = now;
    AU.aHist.push([now, v]); while (AU.aHist.length && now - AU.aHist[0][0] > 80) AU.aHist.shift();
    if (S.state === 'speaking' && MOTION === 'full') {
        var mn = 1; for (var i = 0; i < AU.aHist.length; i++) mn = Math.min(mn, AU.aHist[i][1]);
        var alive = S.rings.filter(function (r) { return r.kind === 'rip'; }).length;
        if (v - mn > 0.18 && v > 0.25 && now - S.lastRipple > 160 && alive < 2) {
            S.lastRipple = now;
            S.rings.push({ kind: 'rip', x: EX.x, y: HEAD.x / 2, t0: now, d: 520, r0: 5, r1: 24, w0: 1.5, w1: 0.5, a0: 0.30, c: C.live });
        }
    }
    if (S.state === 'speaking') wake();
}
function audio(dt) {
    var raw = micOpen() && NOW - AU.micT < 400 ? AU.micRaw : 0;
    var g = clamp((raw - 0.02) / 0.5), Lt = Math.pow(g, 0.7);
    AU.L += (Lt - AU.L) * (1 - Math.exp(-dt / (Lt > AU.L ? 0.03 : 0.18)));
    var At = S.state === 'speaking' && NOW - AU.ttsT < 300 ? AU.ttsRaw : 0;
    AU.A += (At - AU.A) * (1 - Math.exp(-dt / (At > AU.A ? 0.025 : 0.14)));
    AU.hist.push([NOW, AU.L]); while (AU.hist.length && NOW - AU.hist[0][0] > 600) AU.hist.shift();
}

/* ---------------- per-frame state logic ---------------- */
function breath(t, f) { var p = ((t * f) % 1 + 1) % 1; if (p < 0.4) return outSine(p / 0.4); return 1 - inOutSine((p - 0.4) / 0.6); }
var R = { rAdd: 0, rScale: 1, alpha: 1 };
function drowsy() { return S.state === 'ready' && NOW - Math.max(S.lastChange, S.lastActivity || 0) > 600000; }
function logic(dt) {
    var st = S.state, t = NOW / 1000, L = AU.L, A = AU.A, calm = MOTION !== 'full';
    var base = C.live, rAdd = 0, rScale = 1, ga = 0.1, gr = 26, gtau = 0.12, core = 0.35, alpha = 1;
    S.silentFor = L < 0.05 ? S.silentFor + dt : 0;
    switch (st) {
        case 'ready': case 'mismatch': {
            var dz = drowsy();
            var step = dz ? 4 : 8;
            var b = calm ? 0.5 : breath(Math.floor(t * step) / step - S.breathT0, dz ? 1 / 16 : 0.10);
            base = mixc(C.banked, C.live, b); rScale = calm ? 1 : 0.96 + 0.10 * b; ga = 0.05 + (dz ? 0.02 : 0.05) * b; gr = 18 + 8 * b; core = 0.3; gtau = 0.15;
            if (S.hover) ga += 0.04;
            if (S.drop) { ga += 0.12; base = C.hot; }
            break;
        }
        case 'off': base = C.ash; ga = 0; core = 0; if (S.drop) { ga = 0.12; } break;
        case 'paused': {
            var e = clamp((NOW - S.pauseT0) / S.pauseDur), rem = Math.max(0, S.pauseEnd - NOW), warm = clamp(1 - rem / 60000);
            base = mixc(C.ash, C.banked, warm); ga = 0; core = 0;
            if (NOW - S.arcLast > 30000 || e >= 1 || ARC.t === 0) { S.arcLast = NOW; ARC.to(e, 'soft'); }
            if (warm > 0) O.arcA = 0.35 + 0.35 * warm;
            var suf = pausedSuffix();
            var cur = null; for (var k = 0; k < SUF.L.length; k++) if (!SUF.L[k].out) cur = SUF.L[k];
            if (!cur || cur.text !== suf) SUF.set({ text: suf, x: 30, color: C.dim });
            break;
        }
        case 'loading': {
            base = C.banked; ga = 0.07; gr = 26; core = 0.3;
            if (!calm && NOW >= S.nextSpark) { S.nextSpark = NOW + 466; S.sparks.push({ t0: NOW, side: S.sparks.length % 2 ? 1 : -1 }); while (S.sparks.length > 3) S.sparks.shift(); }
            break;
        }
        case 'woke':
            base = C.live; core = 1;
            if (!S.wokeGlow) { ga = 0.10 + 0.22 * L; gr = 26 + 34 * L; gtau = 0.06; } else ga = null;
            if (NOW - S.enteredAt > 160) rAdd = 2.5 * L;
            break;
        case 'listening': case 'holding':
            base = (st === 'holding' && O.muted > 0.5) ? C.ash : C.live; core = L > 0.35 ? 1 : 0.45; gtau = 0.06;
            if (st === 'holding' && O.muted > 0.5) { ga = 0.02; core = 0; gr = 20; break; }
            if (calm) { rAdd = 1 * L; ga = 0.08 + 0.08 * L; gr = 26; }
            else { rAdd = 2.5 * L; ga = 0.10 + 0.22 * L; gr = 26 + 34 * L; if (L < 0.04 && st === 'listening') rAdd += 0.4 * Math.sin(2 * Math.PI * 0.6 * t); }
            break;
        case 'input':
            base = C.live; rAdd = (calm ? 0.6 : 1.5) * L; ga = 0.08 + 0.10 * L; gr = 30; core = 0.45; gtau = 0.08;
            if (S.drop) ga += 0.1;
            break;
        case 'followup': {
            var remF = S.fuDur - (NOW - S.fuT0);
            var uF = clamp(remF / S.fuDur);
            base = uF < 0.4 ? mixc(C.banked, C.live, uF / 0.4) : C.live;
            rAdd = calm ? 0.6 * L : 0.7 * 2.5 * L; ga = 0.06 + 0.14 * uF + (calm ? 0 : 0.1 * L); gr = 10 + 40 * uF; gtau = 0.1; core = 0.4;
            if (remF <= 0 && !S.fuEnded) { S.fuEnded = true; if (MOTION === 'full') kickR(0.6); }
            if (S.fuQuiet && !S.fuHeard && L > 0.15) { S.fuHeard = true; LB.set({ text: 'listening', x: 216, color: C.dim }); }
            break;
        }
        case 'thinking': {
            var el = (NOW - S.enteredAt) / 1000;
            PATIENCE.to(el > 30 ? 0.65 : el > 8 ? 0.8 : 1, 0.7);
            base = C.live; ga = 0.10; gr = 30; core = 0.5;
            if (calm) alpha = 0.85 + 0.15 * Math.sin(2 * Math.PI * 0.5 * t);
            else rAdd = 0.3 * Math.sin(2 * Math.PI * 0.4 * t);
            S.penTheta += dt * PATIENCE.x * (2 * Math.PI / 0.38);
            break;
        }
        case 'speaking': {
            base = C.live; core = 1; gtau = 0.05;
            S.phi = S.phi + dt * 0.9 * (1 + 1.5 * A);
            if (calm) { rAdd = 1 * A; ga = 0.08 + 0.08 * A; gr = 24; }
            else { rAdd = 3 * A; ga = 0.08 + 0.18 * A; gr = 24 + 30 * A; if (A < 0.05) rAdd += 0.25 * Math.sin(2 * Math.PI * 0.5 * t); }
            // Word reveal in step with the voice: each sentence's words appear over the
            // time its audio plays (spoken(); the TTS says when a sentence starts and how
            // long it is). Without those events (an old backend): ~3.3 words a second
            // while the voice is sounding.
            var voiced = NOW - AU.ttsT < 300 && AU.A > 0.04;
            if (S.sync && (S.plans.length || NOW - S.speakT0 < 2500)) {
                for (var pi = 0; pi < S.plans.length; pi++) {
                    var pl = S.plans[pi];
                    if (NOW < pl.t0) continue;
                    // a word shows just before it is heard: a lead of 10% of the sentence, at most 0.35 s
                    // (a flat 10% put the last words of a long sentence a second ahead of the voice)
                    var frac = clamp((NOW - pl.t0 + Math.min(350, 0.1 * pl.d)) / pl.d), want = pl.a + Math.ceil((pl.b - pl.a) * frac) - 1;
                    while (S.wordIdx < Math.min(want, S.words.length - 1)) { S.wordIdx++; S.words[S.wordIdx].t = NOW; }
                }
            } else if (S.wordIdx < S.words.length - 1) {
                S.revealAcc += dt * (voiced ? 3.3 : (NOW - S.speakT0 > 1500 && NOW - AU.ttsT > 1500 ? 6 : 0.9));
                while (S.revealAcc >= 1 && S.wordIdx < S.words.length - 1) { S.revealAcc -= 1; S.wordIdx++; S.words[S.wordIdx].t = NOW; }
            }
            break;
        }
        case 'error': {
            base = C.bad; ga = 0.06; gr = 28; core = 0.3;
            if (!calm && NOW - S.enteredAt > 400) rAdd = 0.3 * Math.sin(2 * Math.PI * 0.25 * t);
            if (NOW - S.enteredAt < 400 && !calm) ga = 0.14 - 0.08 * clamp((NOW - S.enteredAt - 60) / 340);
            break;
        }
    }
    var o = topOV();
    EC.to(o ? o.c : base, o ? o.tau : S.baseTau);
    if (ga !== null) { GA.to(ga, gtau); GR.to(gr, gtau); }
    CORE.to(core, 0.1);
    HOV.to(S.hover && (isRest(st) || st === 'mismatch') ? 1 : 0);
    HM.to(S.ctlHover === 'mic' ? 1 : 0); HS.to(S.ctlHover === 'stop' ? 1 : 0); HN.to(S.ctlHover === 'notme' ? 1 : 0);
    var hide = S.hideIdle && st === 'ready' && !runningN() && !S.done && !S.extraH && !S.peek && !S.hover && !S.drop && !(NOW < S.flashUntil);
    HIDE.to(hide ? 0 : 1, hide ? 0.25 : 0.06);
    R.rAdd = rAdd; R.rScale = rScale; R.alpha = alpha;
    K.rt *= Math.exp(-dt / 0.22); K.r += (K.rt - K.r) * (1 - Math.exp(-dt / 0.02));
    K.gt *= Math.exp(-dt / 0.30); K.g += (K.gt - K.g) * (1 - Math.exp(-dt / 0.03));
    // hover list (and auto-pop on a status change)
    // the hover list needs a hover that began after the panel collapsed (a pointer that was
    // resting over the answer would otherwise pop it open the moment the panel is gone)
    var hovered = S.hover && (S.hoverSince || 0) > (S.collapsedAt || -1);
    var want = (hovered || env.pinList || NOW < (S.listUntil || 0)) && (TASKS.length > 0 || S.usageRow) && (isRest(st)) && !S.extraH;
    if (want !== S.listOpen) {
        S.listOpen = want; cancel('list');
        if (want) { S.listGeom = true; resize(); TASKS.forEach(function (tk, i) { tk.ra = 0; tk.rdy = 6; T(tk, 'ra', 1, 200, outCubic, 35 * i); T(tk, 'rdy', 0, 200, outCubic, 35 * i); }); S.usageA = 0; T(S, 'usageA', 1, 200, outCubic, 35 * TASKS.length); }
        else { TASKS.forEach(function (tk) { T(tk, 'ra', 0, 120, inQuad); }); T(S, 'usageA', 0, 120, inQuad); at(120, function () { S.listGeom = false; resize(); }, 'list'); }
    }
}

/* ---------------- output for the renderers ---------------- */
function offsetOf(sh) { if (!sh) return 0; var tt = (NOW - sh.t0) / 1000; if (tt * 1000 > sh.dur) return 0; return sh.amp * Math.exp(-tt / sh.tau) * Math.sin(2 * Math.PI * sh.f * tt); }
var out = {};
var RINGS_OUT = [], SPARKS_OUT = [], SATS_OUT = [], DOTS_OUT = [0.7, 0.7, 0.7], NO_ROWS = [];
function render() {
    // the drawn size snaps onto its target in the last half pixel: no 1 px flip-flop of the centred pill
    var w = Math.abs(W.x - W.t) < 0.5 ? W.t : W.x, h = Math.abs(H.x - H.t) < 0.5 ? H.t : Math.max(8, H.x);
    var head = Math.min(HEAD.x, h), cy = head / 2;
    out.wt = W.t; out.turn = S.turn;
    out.w = w; out.h = h; out.head = head; out.cy = cy;
    out.rad = Math.min(h / 2, S.listGeom ? 18 : S.extraH ? 22 : 20);   // a card grows the pill into a panel
    out.hide = HIDE.x;
    var trem = offsetOf(S.trem); if (S.trem && NOW - S.trem.t0 > S.trem.dur) S.trem = null;
    var shake = offsetOf(S.shake); if (S.shake && NOW - S.shake.t0 > S.shake.dur) S.shake = null;
    var ex = EX.x + trem + shake, er = Math.max(0.5, ER.x * R.rScale + R.rAdd + K.r);
    out.ex = ex; out.er = er; out.trem = trem;
    var ec = EC.c();
    out.emberCol = ec; out.emberA = R.alpha;
    out.core = CORE.x; out.coreCol = OV.done ? C.okCore : (OV.wake ? C.white : C.hot);
    out.glowA = Math.min(0.5, GA.x + K.g); out.glowR = Math.max(4, GR.x); out.glowCol = GC.c();
    out.rimCol = RIMC.c(); out.rimA = RIMA.x;
    // followup wash (drains into the ember)
    out.washX = -1; out.washA = 0;
    if (O.wash > 0.01 && (S.state === 'followup' || S.prev === 'followup')) {
        var u = 1;
        if (S.state === 'followup') {
            var rem = S.fuDur - (NOW - S.fuT0); u = clamp(rem / S.fuDur);
            if (rem < 600 && MOTION !== 'off') u = (600 / S.fuDur) * (1 - inCubic(clamp((600 - rem) / 600)));
            S.lastU = u;
        } else u = S.lastU || 0;
        var full = 20 + (w - 26) * u;
        out.washX = ex + (full - ex) * O.washReveal; out.washA = (0.10 + 0.08 * u) * O.wash;
    }
    // quiet followup (a panel is open): the hairline along its bottom edge, draining
    out.drainA = 0; out.drainU = 0;
    if (O.drain > 0.01) {
        var uD = S.state === 'followup' ? clamp((S.fuDur - (NOW - S.fuT0)) / S.fuDur) : (S.lastU || 0);
        if (S.state === 'followup') S.lastU = uD;
        out.drainA = O.drain; out.drainU = uD;
    }
    out.progress = S.state === 'loading' ? O.progress : 0;
    // rings (heat ring on woke, ripples on speech onsets), at most 3
    out.rings = RINGS_OUT; RINGS_OUT.length = 0;     // reused arrays: less garbage per frame
    S.rings = S.rings.filter(function (r) {
        var p = clamp((NOW - r.t0) / r.d); if (p >= 1) return false;
        var e = outCubic(p);
        if (out.rings.length < 3) out.rings.push({ x: r.kind === 'rip' ? ex : r.x, y: r.kind === 'rip' ? cy : r.y, r: r.r0 + (r.r1 - r.r0) * e, a: r.a0 * (1 - p), w: r.w0 + (r.w1 - r.w0) * e });
        return true;
    });
    // loading sparks (kindling)
    out.sparks = SPARKS_OUT; SPARKS_OUT.length = 0;
    S.sparks = S.sparks.filter(function (s) {
        var p = clamp((NOW - s.t0) / 1400); if (p >= 1) { kickR(0.35); return false; }
        var e = inQuad(p), sx = ex + 26, sy = cy + 6 * s.side, cx = ex + 14, ccy = cy + 10 * s.side;
        out.sparks.push({ x: (1 - e) * (1 - e) * sx + 2 * (1 - e) * e * cx + e * e * ex, y: (1 - e) * (1 - e) * sy + 2 * (1 - e) * e * ccy + e * e * cy, a: 0.8 * Math.sin(Math.PI * p) });
        return true;
    });
    // thread
    var ta = TA.x, mx = 0;
    for (var i = 0; i < N; i++) if (DA[i] > mx) mx = DA[i];
    out.thrA = (ta < 0.01 || mx < 0.01) ? 0 : ta;
    out.thrCol = TC.c(); out.thrW = TW.x + (GEN === 'wave' && MOTION === 'full' ? 0.5 * AU.L : 0);
    out.tx = DX; out.ty = DY; out.ta = DA; out.tdx = trem;
    // brackets, check, slash, pilot + arc
    out.bracketA = O.bracketA; out.bracketH = 6 * O.bracket; out.bracketOff = O.bracketOff; out.bracketMuted = O.muted;
    out.check = O.check; out.checkW = O.checkW; out.checkA = MOTION === 'calm' ? O.check : 1;
    out.slash = O.slash; out.muteSlash = O.muted;
    out.pilotA = 0; out.arcA = O.arcA; out.arcFill = clamp(ARC.x);
    if (O.pilotOn > 0.01 || O.arcA > 0.01) {
        var rem2 = Math.max(0, S.pauseEnd - NOW), warm = clamp(1 - rem2 / 60000);
        var f = warm > 0 ? 0.10 : 0.05, q = MOTION === 'full' ? Math.floor(NOW / 500) / 2 : 0;
        var b = MOTION === 'full' ? breath(q, f) : 0.5;
        out.pilotA = O.pilotOn * (0.25 + 0.25 * b) + warm * 0.3 * O.pilotOn;
    }
    // satellites
    out.sats = SATS_OUT; SATS_OUT.length = 0;
    if (O.satVis > 0.01) {
        var ang0 = satAngle(), offish = S.state === 'off' || S.state === 'paused';
        for (var j = 0; j < TASKS.length; j++) {
            var tk = TASKS[j], sx, sy, sz = 1.25, a = tk.a * O.satVis;
            if (tk.status !== 'running' && !tk.fly) continue;
            if (tk.fly) { var fp = clamp((NOW - tk.fly.t0) / 220), fe = inCubic(fp); sx = tk.fly.x + (ex - tk.fly.x) * fe; sy = tk.fly.y + (cy - tk.fly.y) * fe; sz = 1.25 - 0.5 * fe; }
            else { var an = (tk.phase || 0) + ang0; sx = ex + Math.cos(an) * tk.orb; sy = cy + Math.sin(an) * tk.orb; }
            out.sats.push({ x: sx, y: sy, sz: sz, a: a, off: offish });
        }
    }
    // labels
    out.lb = LB.L; out.suf = SUF.L; out.sufX = SUFX.x; out.hov = HOV.x;
    out.dotsA = 0;
    if (S.state === 'followup') {
        for (var m = 0; m < LB.L.length; m++) if (LB.L[m].text === 'go on' && !LB.L[m].out) out.dotsA = LB.L[m].a;
    }
    out.dots = DOTS_OUT;
    for (var dk = 0; dk < 3; dk++) DOTS_OUT[dk] = out.dotsA > 0 && MOTION === 'full' ? 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(2 * Math.PI * (NOW / 1200 - dk * 0.2 / 1.2))) : 0.7;
    // speaking words: the newest two lines; only the newest word is warm
    out.words = []; out.wordsA = 0;
    renderRow();
    if (!S.turn && S.words.length && O.words > 0.01 && (S.state === 'speaking' || S.prev === 'speaking')) {
        out.wordsA = O.words;
        var lastLine = 0;
        for (var n = 0; n <= S.wordIdx && n < S.words.length; n++) lastLine = S.words[n].line;
        var top = Math.max(0, lastLine - 1);
        for (var z = 0; z < S.words.length; z++) {
            var wd = S.words[z];
            if (wd.t === null || wd.line < top || wd.line > top + 1) continue;
            var age = NOW - wd.t, cal = MOTION !== 'full';
            out.words.push({ w: wd.w, x: wd.x, line: wd.line - top, a: clamp(age / 160) * O.words, rise: cal ? 0 : 1.5 * (1 - clamp(age / 160)),
                col: cal ? C.fg : mixc(C.hot, C.fg, outSine(clamp(age / 400))) });
        }
        out.twoLines = S.lines > 1;
    }
    // input field + caret
    out.field = O.field; out.fieldX = O.fieldX;
    var since = (NOW - S.lastKey) / 1000;
    out.caretA = S.state === 'input' ? (MOTION === 'full' ? 0.675 + 0.325 * Math.cos(2 * Math.PI * since) : 1) : 0;
    out.ghost = O.ghost; out.ghostX = O.ghostX; out.ghostText = S.ghost;
    // mic + stop buttons, not-me chip
    // the head as drawn: 40, or 60 while a two-line answer is up; cards sit below it
    out.headFull = Math.min(h, HEAD.x + (S.state === 'speaking' && S.lines > 1 && !S.turn ? 20 : 0));
    S.ctlCy = Math.min(out.headFull, 60) / 2;
    out.ctl = O.ctl; out.ctlCy = S.ctlCy; out.ctlScale = MOTION === 'full' ? 0.85 + 0.15 * O.ctl : 1;
    out.hm = HM.x; out.hs = HS.x; out.pressM = O.pressM; out.pressS = O.pressS;
    out.notme = O.notme; out.hn = HN.x;
    // hover task list + usage row
    var anyRow = S.listGeom; for (var rr = 0; rr < TASKS.length && !anyRow; rr++) anyRow = TASKS[rr].ra > 0.001;
    out.rows = !anyRow ? NO_ROWS : TASKS.slice(0, rowsN()).map(function (tk, k) {
        var bb = MOTION === 'full' ? breath(NOW / 1000 + k * 0.7, 0.5) : 0.5;
        return { name: tk.name, status: tk.status, right: tk.right, elapsed: tk.elapsed, a: tk.ra, dy: tk.rdy, b: bb };
    });
    out.usageA = S.usageA || 0;
    out.listGeom = S.listGeom;
    out.state = S.state;
    out.motion = MOTION;
    out.drop = S.drop ? 1 : 0;
    return out;
}

/* ---------------- frame loop ---------------- */
var lastT = 0, lastGap = 0;
function busy() {
    if (TWEENS.length || S.rings.length || S.sparks.length || S.trem || S.shake || S.pend) return true;
    if (NOW - blendT0 < 240) return true;
    for (var i = 0; i < TASKS.length; i++) if (TASKS[i].fly) return true;
    if (Math.abs(K.r) > 0.01 || Math.abs(K.rt) > 0.01 || Math.abs(K.g) > 0.002 || Math.abs(K.gt) > 0.002) return true;
    for (var j = 0; j < SPRINGS.length; j++) if (SPRINGS[j] !== ARC && !SPRINGS[j].settled()) return true;
    if (LB.busy() || SUF.busy()) return true;
    if (Math.abs(HIDE.x - HIDE.t) > 0.004) return true;
    return false;
}
// why frames are still wanted (debugging the rest invariant)
function busyWhy() {
    var w = [];
    if (TWEENS.length) w.push('tweens:' + TWEENS.map(function (t) { return t.k; }).join(','));
    if (S.rings.length) w.push('rings'); if (S.sparks.length) w.push('sparks'); if (S.trem) w.push('trem'); if (S.shake) w.push('shake'); if (S.pend) w.push('pend');
    if (NOW - blendT0 < 240) w.push('blend');
    if (Math.abs(K.r) > 0.01 || Math.abs(K.rt) > 0.01 || Math.abs(K.g) > 0.002 || Math.abs(K.gt) > 0.002) w.push('kick');
    var names = ['W', 'H', 'HEAD', 'EX', 'ER', 'TX0', 'TX1', 'SUFX', 'ARC'], sp = [W, H, HEAD, EX, ER, TX0, TX1, SUFX, ARC];
    for (var i = 0; i < sp.length; i++) if (sp[i] !== ARC && !sp[i].settled()) w.push('spring ' + names[i] + ' ' + sp[i].x.toFixed(2) + '->' + sp[i].t);
    if (LB.busy()) w.push('labels'); if (SUF.busy()) w.push('suffix');
    if (Math.abs(HIDE.x - HIDE.t) > 0.004) w.push('hide');
    if (S.extraAnimating) w.push('extra');
    w.push('state ' + S.state + ' gap ' + frameGap() + ' awake ' + awake + ' timers ' + TIMERS.length);
    return w.join(' | ');
}
function still() {
    for (var i = 0; i < FOLS.length; i++) { var f = FOLS[i]; if (Math.abs(f.x - f.t) >= (Math.abs(f.t) > 2 ? 0.5 : 0.002)) return 100; }
    return AU.L < 0.003 && AU.A < 0.003 ? Infinity : 100;
}
function liveGap() { return LIVE_CAP >= 60 ? 0 : 33; }
// ms to the next frame: 0 = every vsync, Infinity = stopped
function frameGap() {
    var st = S.state, el = NOW - S.enteredAt, full = MOTION === 'full';
    if (busy()) return liveGap();
    if (S.extraAnimating) return liveGap();
    // suspended (a full-screen app in front, screen locked): the idle breath stops; any real
    // state change still draws at once
    if (env.suspended && (isRest(S.state) || S.state === 'mismatch')) return Infinity;
    switch (st) {
        case 'ready':
            if (HIDE.x < 0.01) return Infinity;                 // hidden when idle: nothing to draw
            if (NOW < S.flashUntil) return Math.max(16, S.flashUntil - NOW + 5);
            return full ? (runningN() ? 100 : (drowsy() ? 250 : 125)) : still();
        case 'off': return full && runningN() ? 100 : still();
        case 'paused': {
            if (full) return 500;
            // calm: still between changes; wake for the next minute of the label,
            // the 30 s arc step, or once a second in the last minute (pre-warm)
            var rem = Math.max(0, S.pauseEnd - NOW);
            if (rem <= 60000) return 1000;
            return Math.min(rem % 60000 + 5, 30000 - (NOW - S.arcLast) + 5);
        }
        case 'loading': return 33;
        case 'thinking': return el > 30000 ? 33 : liveGap();
        case 'error': return el < 900 ? liveGap() : (full ? 250 : still());
        case 'input': return NOW - S.lastKey > 2000 && AU.L < 0.03 && !O.ghost ? 66 : liveGap();
        case 'followup': return S.fuEnded ? still() : liveGap();
        default: return liveGap();
    }
}
// One step: integrate from the last call to now; returns the gap to the next frame (ms).
function frame() {
    NOW = env.now();
    var raw = Math.max(0, (NOW - (lastT || NOW)) / 1000); lastT = NOW;
    var dt = Math.min(raw, lastGap ? Math.min(0.6, lastGap / 1000 + 0.05) : 1 / 30);
    awake = false;
    inFrame = true;
    STATS.frames++;
    runTimers();
    audio(dt);
    logic(dt);
    var n = Math.max(1, Math.ceil(dt * 240)), hh = dt / n;
    for (var k = 0; k < n; k++) for (var i = 0; i < SPRINGS.length; i++) SPRINGS[i].step(hh);
    for (var j = 0; j < SPRINGS.length; j++) SPRINGS[j].tick();
    for (var f = 0; f < FOLS.length; f++) FOLS[f].step(dt);
    runTweens(); LB.step(); SUF.step(); thread();
    var prevW = out.w, prevH = out.h;
    var o = render();
    if (prevW !== undefined && raw < 0.05 && raw > 0) {
        var per60 = (1 / 60) / Math.max(raw, 1 / 60);       // px per 60 fps frame (the software path runs at 30)
        STATS.maxEdge = Math.max(STATS.maxEdge, Math.abs(o.w - prevW) / 2 * per60, Math.abs(o.h - prevH) * per60);
    }
    if (!isFinite(o.w) || !isFinite(o.h) || !isFinite(o.er)) { STATS.nan++; env.log('[island] NaN in the frame, snapped'); W.set(W.t); H.set(H.t); ER.set(ER.t); }
    inFrame = false;
    var gap = frameGap();
    if (gap !== Infinity && TIMERS.length) {
        var nt = Infinity; for (var q = 0; q < TIMERS.length; q++) nt = Math.min(nt, TIMERS[q].t);
        gap = Math.min(gap, Math.max(0, nt - NOW));
    } else if (gap === Infinity && TIMERS.length) {
        var nt2 = Infinity; for (var q2 = 0; q2 < TIMERS.length; q2++) nt2 = Math.min(nt2, TIMERS[q2].t);
        gap = Math.max(0, nt2 - NOW);
    }
    if (awake && gap > 0) gap = liveGap();
    lastGap = gap === Infinity ? 0 : gap;
    if (gap === Infinity) lastT = 0;              // next start: no catch-up dt
    return gap;
}

/* ---------------- public API (called from Island.qml) ---------------- */
function init(e) {
    for (var k in e) env[k] = e[k];
    makeModel();
    LB = new Labels({ outD: 120, outDy: -2, inD: 220, inDy: 3, inDelay: 60 });
    SUF = new Labels({ outD: 140, outDy: -4, inD: 220, inDy: 4, inDelay: 80 });
    NOW = env.now(); lastT = 0;
    GEN = 'rest'; blendT0 = -1e9;
    for (var i = 0; i < N; i++) { DX[i] = 18; DY[i] = 14; DA[i] = 0; SX[i] = 18; SY[i] = 14; SA[i] = 0; }
    S.state = 'loading'; S.enteredAt = NOW;
    ENTER.loading(null, { text: 'starting up…' });
    resize(); W.set(W.t); H.set(H.t); HEAD.set(HEAD.t); EX.set(EX.t);
    for (var a = 0; a < LB.L.length; a++) { LB.L[a].a = 1; LB.L[a].dy = 0; LB.L[a].t0 = NOW - 1000; }
    wake();
}
function setMotion(m) { MOTION = m === 'calm' || m === 'off' ? m : 'full'; respace(); resize(); wake(); }
function setLiveCap(fps) { LIVE_CAP = fps; }
function setBadge(b, dir) {
    NOW = env.now();
    if (b === S.badge) return;
    var first = !S.badge;
    S.badge = b;
    if (S.state === 'ready') {
        SUF.set(b ? { text: badgeText(), x: 30, color: runningN() ? C.fg : C.dim } : null); resize('soft');
        if (MOTION === 'full' && !first) {
            if (dir < 0) { ov('badge', C.cool, 0.13, 2); at(1000, function () { ov('badge'); tauBack(0.27); }, 'g'); }
            else if (dir > 0) { kickR(0.6); kickG(0.06); }
        }
    }
}
function setWakeLabel(l) { NOW = env.now(); S.wakeLabel = l || 'hey claude'; if (S.state === 'ready') { restLabel(); resize('soft'); } }
function setPartial(p) {
    NOW = env.now();
    p = p || '';
    if (['listening', 'holding', 'followup', 'woke'].indexOf(S.state) < 0) p = '';
    if (p === S.partial) return;
    S.partial = p;
    if (S.state === 'listening') { resize('soft'); listenLabel(); }
    else if (S.state === 'holding' && p) { resize('soft'); }
    wake();
}
function setNotMe(on) { NOW = env.now(); on = !!on; if (on === S.notMe) return; S.notMe = on; T(O, 'notme', on ? 1 : 0, on ? 200 : 120, on ? outCubic : inQuad, on ? 140 : 0); if (S.state === 'listening' || S.state === 'woke') resize('soft'); }
function setThinkText(text) {
    NOW = env.now();
    if (S.state !== 'thinking') return;
    var t = elide(text || '…', true, MAXT);
    if (t === S.thinkText) return;
    S.thinkText = t; thinkLabel(); resize('soft');
}
function setTool(label, icon) {
    NOW = env.now();
    S.toolLabel = label || ''; S.toolIcon = icon || '';
    if (S.state === 'thinking') { thinkLabel(); resize('soft'); }
}
// A sentence of the answer starts playing (text, ms): its words are revealed over its audio.
function spoken(text, ms) {
    NOW = env.now();
    S.sync = true;
    var n = String(text || "").split(/\s+/).filter(function (w) { return w.length; }).length;
    if (!n) return;
    var a = Math.max(S.sentEnd, S.wordIdx + 1);
    S.plans.push({ a: a, b: a + n, t0: NOW, d: Math.max(200, ms) });
    S.sentEnd = a + n;
    while (S.plans.length > 8) S.plans.shift();
    wake();
}
function setSpeakText(text) {
    NOW = env.now();
    if (S.state !== 'speaking' || text === S.speakText) return;
    var oldW = S.speakW, oldLines = S.lines;
    layoutWords(text);
    if (S.speakW !== oldW || S.lines !== oldLines) resize('soft');
    wake();
}
// The turn's panel (Island.qml): a question row went up / the panel collapsed.
function newTurn() {
    NOW = env.now();
    S.turn = true; S.words = []; S.wordIdx = -1; S.rowTop = 0; S.rowLines = 0; S.speakText = ''; S.plans = []; S.sentEnd = 0;
    if (S.state === 'thinking') thinkLabel();
    resize('soft'); wake();
}
function setTurn(on) {
    NOW = env.now(); on = !!on;
    if (on === S.turn) return;
    S.turn = on;
    if (!on) { S.words = []; S.wordIdx = -1; S.rowTop = 0; S.rowLines = 0; }
    resize(); wake();
}
// The answer row under the question: words revealed in step with the voice (the ones not said
// yet are laid out but clear, so the row's height only changes when more text arrives), the
// newest word warm. All its lines are handed over; the view shows as many as the window has
// room for (setRowMax) and follows the newest line (top), so it scrolls by a line, never per
// word, and the whole answer stays there to scroll back through.
var ROW_OUT = { lines: [], n: 0, top: 0, key: '' };
function setRowMax(n) { n = Math.max(2, Math.round(n || 3)); if (n !== S.rowMax) { S.rowMax = n; wake(); } }
function renderRow() {
    var R0 = ROW_OUT, M = S.rowMax;
    if (!S.turn || !S.words.length) { if (R0.n) { R0.n = 0; R0.top = 0; R0.lines = []; R0.key = ''; } out.row = R0; return; }
    var last = 0;
    for (var i = 0; i <= S.wordIdx && i < S.words.length; i++) last = S.words[i].line;
    S.rowTop = Math.max(S.rowTop, last - (M - 1));
    var total = S.words[S.words.length - 1].line + 1;
    S.rowTop = Math.min(S.rowTop, Math.max(0, total - M));
    R0.n = total; R0.top = S.rowTop;
    var L = [], cal = MOTION !== 'full';
    for (var q = 0; q < total; q++) L.push('');
    for (var z = 0; z < S.words.length; z++) {
        var wd = S.words[z], k = wd.line;
        var a = 0, col = C.fg;
        if (wd.t !== null && z <= S.wordIdx) {
            var age = NOW - wd.t;
            a = cal ? 1 : clamp(age / 160);
            col = cal ? C.fg : mixc(C.hot, C.fg, outSine(clamp(age / 400)));
        }
        L[k] += (L[k] ? ' ' : '') + '<font color="' + hexa(col, a) + '">' + escHtml(wd.w) + '</font>';
    }
    R0.lines = L; R0.key = R0.n + '|' + R0.top + '|' + L.join('\n');
    out.row = R0;
}
function escHtml(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function hexa(c, a) {
    function h2(v) { var x = Math.round(Math.max(0, Math.min(255, v))).toString(16); return x.length < 2 ? '0' + x : x; }
    return '#' + h2(a * 255) + h2(c[0]) + h2(c[1]) + h2(c[2]);
}
function setExtra(h, w, animating) {
    NOW = env.now();
    h = Math.max(0, Math.round(h)); w = Math.max(0, Math.round(w || 0));
    S.extraAnimating = !!animating;
    if (h === S.extraH && w === S.extraW) { if (animating) wake(); return; }
    if (h === 0 && S.extraH > 0) S.collapsedAt = NOW;      // a pointer left over the panel isn't a hover on the pill
    S.extraH = h; S.extraW = w; resize(); wake();
}
function setPaused(untilMs) {
    NOW = env.now();
    if (S.state === 'paused') { S.pauseEnd = untilMs; S.pauseDur = Math.max(1000, untilMs - S.pauseT0); wake(); }
}
function setHideIdle(on) { S.hideIdle = !!on; wake(); }
function setPeek(on) { S.peek = !!on; wake(); }
function setHover(on) { NOW = env.now(); if (S.hover === !!on) return; S.hover = !!on; if (on) S.hoverSince = NOW; S.lastActivity = NOW; wake(); }
function setCtlHover(k) { if (S.ctlHover === k) return; S.ctlHover = k; wake(); }
function setDrop(on) { NOW = env.now(); S.drop = !!on; T(O, 'drop', on ? 1 : 0, 160, outCubic); if (on) kickG(0.08); wake(); }
function setUsageRow(on) { NOW = env.now(); on = !!on; if (on === !!S.usageRow) return; S.usageRow = on; if (S.listGeom) resize(); wake(); }
function popList(ms) { NOW = env.now(); S.listUntil = NOW + ms; at(ms + 5, function () { wake(); }, 'g'); wake(); }
function press(k) {
    NOW = env.now();
    var key = k === 'mic' ? 'pressM' : 'pressS';
    if ((S.state === 'thinking' || S.state === 'speaking') && MOTION !== 'off') { O[key] = 1; T(O, key, 0, 220, outCubic); }
}
function flashLabel(text, ms) {
    NOW = env.now();
    S.flash = text; S.flashUntil = NOW + ms;
    if (S.state === 'ready') { restLabel(); resize('soft'); }
    at(ms + 10, function () { if (S.state === 'ready') { restLabel(); resize('soft'); } }, 'g');
}
function typeKey() { NOW = env.now(); S.lastKey = NOW; if (MOTION === 'full') { kickR(0.6); kickG(0.04); } }
function setInputLines(n) { NOW = env.now(); n = Math.max(1, Math.min(4, n)); if (n === S.inputLines) return; S.inputLines = n; if (S.state === 'input') resize(); }
function submitInput(text) {
    NOW = env.now();
    S.ghost = text; O.ghost = 1; O.ghostX = 0;
    T(O, 'ghost', 0, 220, inCubic); T(O, 'ghostX', -10, 220, inCubic);
    at(200, function () { if (MOTION === 'full') { kickR(1.2); kickG(0.22); } }, 'g');
}
function poke() { NOW = env.now(); S.lastActivity = NOW; wake(); }
function isAwake() { return awake; }
function stats() { return STATS; }
function stateName() { return S.state; }
// Geometry the window mask must cover: the union of where the pill is and where it's going.
function maskRect() {
    var w = Math.max(W.x, W.t), h = Math.max(H.x, H.t);
    var settled = W.settled() && H.settled();
    var m = settled ? 2 : Math.max(8, 0.06 * w);
    return { w: w + 2 * m, h: h + (settled ? 2 : Math.max(8, 0.06 * h)), settled: settled };
}
function snapshotForTest() {
    return { state: S.state, w: W.x, h: H.x, wt: W.t, ht: H.t, head: HEAD.x, labels: LB.L.map(function (l) { return { text: l.text, a: l.a, out: l.out }; }),
        tweens: TWEENS.length, timers: TIMERS.length, rings: S.rings.length, gen: GEN };
}
// What the UX recorder (heyclaude/ux_probe.py) samples ~30 times a second: cheap, no arrays copied
// except the label texts. `said` = the words of the answer revealed so far in the head.
function probeForUx() {
    var lb = '', i;
    for (i = LB.L.length - 1; i >= 0; i--) if (!LB.L[i].out) { lb = LB.L[i].text; break; }
    var said = '';
    if (S.state === 'speaking' && S.words) {
        var parts = [];
        for (i = 0; i <= S.wordIdx && i < S.words.length; i++) parts.push(S.words[i].w);
        said = parts.join(' ');
    }
    return { state: S.state, w: W.x, h: H.x, wt: W.t, ht: H.t, head: HEAD.t, label: lb, tool: S.toolLabel || '',
             said: said, words: S.words ? S.words.length : 0, wordIdx: S.wordIdx, speakText: S.state === 'speaking' ? S.speakText : '',
             extraH: S.extraH || 0, hide: HIDE.x, partial: S.partial || '' };
}
