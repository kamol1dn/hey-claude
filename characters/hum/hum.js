.pragma library
// Hum, the island's own face: no body, two eyes and a mouth made of the voice thread.
// Ported from docs/character-options.html (const Hum). New here: eyelids, brows and
// closed eyes, the sleep states (drowsy, asleep, waking), woke, approval_needed and
// twelve small idle antics. make(K) gets the character kit (ui/qml/HeyClaude/Character/kit.js).
function make(K) {
const { C, TAU, clamp, lerp, mixc, rgba, easeOutCubic, easeInCubic, easeOutBack, breath, pillPath, drawParts, drawCheck, rnd } = K;
const N = 48;                                    // points along the thread (the design page has 32)
const COOL = [214, 230, 246], WOOL = [236, 230, 222], MUZZLE = [138, 126, 118], BUG = [206, 198, 188];

// The face's geometry. In the island (small) the face fills its box; on the desktop it sits
// inside its own pill. Antic props are laid out in desktop face units and scaled by fs.
function geo(small) {
  const fs = small ? 1.45 : 1;
  return { small: small, fs: fs, eyeCY: small ? -9 : -6, ex: 15 * fs, ew: 9 * fs, eh: 15 * fs,
           mY: small ? 18 : 13, sx: small ? 20 : 18, sy: small ? 22 : 18 };
}
const G = (s) => s.cs.g || geo(true);
const ph = (u, a, b) => clamp((u - a) / (b - a));
const sm = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
const env = (u, a, b, c, d) => Math.min(sm(ph(u, a, b)), 1 - sm(ph(u, c, d)));     // up a..b, hold, down c..d
const pulse = (t, at, len) => (t > at && t < at + len) ? Math.sin(Math.PI * (t - at) / len) : 0;
// point the eyes at (x, y) in face units
function lookAt(F, x, y) { F.lx = clamp(x / 28, -1.25, 1.25); F.ly = clamp((y + 6) / 22, -1.1, 1.1); }
// where the thread is at x (-1..1), in face units
function onMouth(s, xn) {
  const cs = s.cs, g = G(s);
  if (!cs.mx) return [xn * 18, 13];
  const i = clamp(Math.round((xn + 1) / 2 * (N - 1)), 0, N - 1);
  return [cs.mx[i] * g.sx / g.fs, (g.mY + cs.my[i] * g.sy) / g.fs];
}
function oval(ctx, x, y, rx, ry, rot) {
  rot = rot || 0;
  ctx.beginPath(); ctx.moveTo(x + rx * Math.cos(rot), y + rx * Math.sin(rot));
  K.ellipse(ctx, x, y, rx, ry, rot, 0, TAU); ctx.closePath();
}
function drop(ctx, x, y, r, col, a) {
  if (a <= 0.01) return;
  ctx.fillStyle = rgba(col, a); ctx.beginPath(); ctx.moveTo(x, y - r * 2.3);
  ctx.bezierCurveTo(x + r * 0.35, y - r * 1.4, x + r, y - r * 0.8, x + r, y);
  ctx.arc(x, y, r, 0, Math.PI);
  ctx.bezierCurveTo(x - r, y - r * 0.8, x - r * 0.35, y - r * 1.4, x, y - r * 2.3);
  ctx.closePath(); ctx.fill();
}
// a music note (two = a beamed pair)
function note(ctx, x, y, sz, col, a, lw, two) {
  if (a <= 0.01) return;
  ctx.fillStyle = rgba(col, a); ctx.strokeStyle = rgba(col, a); ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  oval(ctx, x, y, sz * 0.34, sz * 0.25, -0.45); ctx.fill();
  const st = x + sz * 0.29, top = y - sz * 1.05;
  ctx.beginPath(); ctx.moveTo(st, y - sz * 0.06); ctx.lineTo(st, top);
  if (two) {
    const x2 = st + sz * 0.7;
    ctx.lineTo(x2, top - sz * 0.14); ctx.lineTo(x2, y - sz * 0.2); ctx.stroke();
    oval(ctx, x2 - sz * 0.29, y - sz * 0.14, sz * 0.34, sz * 0.25, -0.45); ctx.fill();
  } else {
    ctx.quadraticCurveTo(st + sz * 0.55, top + sz * 0.28, st + sz * 0.42, top + sz * 0.62); ctx.stroke();
  }
}
// "caught!": three tiny pop lines over the face
function startle(o, s, u) {
  const ctx = o.ctx, g = G(s), a = 0.75 * (1 - u);
  if (a < 0.02) return;
  const cy = g.eyeCY - g.eh * 0.6 - 3 * g.fs, r0 = (2 + 3.5 * easeOutCubic(u)) * g.fs, L = 3 * g.fs;
  ctx.strokeStyle = rgba(C.fg, a); ctx.lineWidth = o.lw(1.3 * g.fs, 0.9); ctx.lineCap = 'round';
  for (const an of [-0.6, 0, 0.6]) {
    const dx = Math.sin(an), dy = -Math.cos(an);
    ctx.beginPath(); ctx.moveTo(dx * r0, cy + dy * r0); ctx.lineTo(dx * (r0 + L), cy + dy * (r0 + L)); ctx.stroke();
  }
}

// ---- the face's resting mouth for each state (one point of the thread) -------------------
function baseMouth(s, st, t, q, m) {
  const cs = s.cs, calm = s.calm, L = s.L, A = s.A, w = Math.sin(Math.PI * q);
  switch (st) {
  case 'idle': m.y = 0.12 * w; break;
  case 'off': m.y = 0; break;
  case 'woke':
    if (t < 0.28) { const an = TAU * q; m.x = 0.2 * Math.cos(an); m.y = 0.16 * Math.sin(an); break; }
    // fall through: then the voice thread, like listening
  case 'listening': {
    const lv = s.histAt(0.36 * (1 - q), 'L'), wv = Math.pow(w, 0.8);
    m.y = (0.035 + 0.42 * lv) * wv * Math.sin(TAU * (q * 2.4 + (calm ? 0 : 1.1 * s.T)));
    if (calm) { m.y = 0; m.a = q < 0.35 + 0.65 * L ? 1 : 0; }
    break;
  }
  case 'thinking': {
    if (calm) { m.y = 0; m.a = 0.5 + 0.5 * Math.sin(TAU * 0.5 * t); break; }
    const p0 = t * TAU / 0.38 * 0.5, th = p0 + q * 3.2 * Math.PI, r = 1.9, d = 4.5, span = r * 3.2 * Math.PI + 2 * d;
    m.x = -1 + 2 * ((r * (th - p0) - d * Math.sin(th) + d) / span);
    m.y = -d * Math.cos(th) / span * 1.2;
    m.a = Math.pow(q, 0.8);
    break;
  }
  case 'talking': {
    const ww = Math.pow(w, 1.2), p0 = s.T * 0.9 * (1 + 1.5 * A), k = 0.78 + 0.22 * Math.sin(TAU * (2 * q - p0));
    m.y = -(0.03 + 0.32 * Math.pow(A, 0.7)) * ww * k; m.y2 = (0.04 + 0.5 * Math.pow(A, 0.7)) * ww * k;
    if (calm) { m.y = 0; m.y2 = 0; m.a = q < 0.2 + 0.8 * A ? 1 : 0.15; }
    break;
  }
  case 'happy':
    m.x = -0.75 + 1.5 * q; m.y = q < 0.35 ? 0.42 * (q / 0.35) : 0.42 - 0.95 * ((q - 0.35) / 0.65);
    m.a = q <= cs.trim ? 1 : 0; break;
  case 'confused': m.y = 0.14 * Math.sin(TAU * 3.5 * q) + 0.02; break;
  case 'working': m.y = 0; break;
  case 'sending':
    if (t < 0.9) { const an = TAU * q; m.x = 0.2 * Math.cos(an); m.y = 0.16 * Math.sin(an); }
    else m.y = 0.12 * w;
    break;
  case 'approval_needed': {
    const k = cs.ask === undefined ? 0.5 : cs.ask;          // one corner lifts and settles: "well?"
    m.x = (-1 + 2 * q) * 0.78; m.y = 0.05 * w - 0.13 * k * Math.pow(q, 3); break;
  }
  case 'drowsy': {
    const sag = cs.sag === undefined ? 0.5 : cs.sag;        // slack, a little lopsided, slowly sagging
    m.x = (-1 + 2 * q) * 0.84; m.y = 0.1 + (0.03 + 0.06 * sag) * w + 0.07 * q * q; break;
  }
  case 'asleep': {
    const b = cs.br === undefined ? 0.4 : cs.br;            // a small breathing thread: parts on the inhale
    m.x = (-1 + 2 * q) * 0.5; m.y = 0.15 - 0.045 * b * w; m.y2 = 0.15 + 0.075 * b * w; break;
  }
  case 'waking': {
    const yw = cs.yaw || 0, ow = Math.sqrt(w);                 // an upright oval
    m.x = lerp(-1 + 2 * q, -0.36 * Math.cos(Math.PI * q), yw);
    m.y = 0.12 * w * (1 - yw) - 0.3 * yw * ow; m.y2 = 0.12 * w * (1 - yw) + 0.6 * yw * ow;
    if (cs.smack) m.y += 0.05 * Math.sin(TAU * 2 * q + t * 28) * cs.smack * w;
    break;
  }
  default: m.y = 0.12 * w;
  }
}

// ---- idle antics -------------------------------------------------------------------------
// face(s, F, a, d, dt) sets the face's targets (F, smoothed) and direct offsets (d) while the
// antic plays; mouth(s, a, q, i, m) reshapes the thread. The rest is the kit's antic API.
const MEL = [[0.1, 1], [0.17, 3], [0.24, 5], [0.33, 3], [0.4, 6], [0.5, 5], [0.57, 3], [0.64, 1], [0.74, 3], [0.81, 5, 1]];
const PHR = [[0.14, 0.33, 0.95, -0.2], [0.41, 0.58, 1.35, 0.1], [0.65, 0.83, 0.75, -0.32]];  // whistle: from, to, wave, slope
const BEATS = [[0.35, 1, 0], [0.58, 0, 1], [0.81, 1, 0], [0.97, 0, 1], [1.15, 1, 1], [1.9, 1, 1], [2.18, 1, 1]];
function bowPt(q, pull) {
  const ls = 1 - 0.28 * pull, tl = 0.4 + 0.16 * pull;
  if (q < 0.16) { const v = q / 0.16; return [lerp(-tl, 0, v), lerp(tl * 1.05, 0, v) - 0.05 * Math.sin(Math.PI * v)]; }
  if (q > 0.84) { const v = (q - 0.84) / 0.16; return [lerp(0, tl, v), lerp(0, tl * 1.05, v) - 0.05 * Math.sin(Math.PI * v)]; }
  const th = -Math.PI / 2 + (q - 0.16) / 0.68 * TAU, sn = Math.sin(th), cn = Math.cos(th), den = 1 + sn * sn;
  const X = 0.66 * ls * cn / den;
  return [X, 1.15 * ls * sn * cn / den - 0.22 * ls * Math.abs(X) / 0.66];
}
function sheepAt(v, T) {
  let x, y, air = 0, ang = 0;
  if (v < 0.2) { x = lerp(-94, -60, v / 0.2); y = 38 - 2.2 * Math.abs(Math.sin(T * 11)); }
  else if (v < 0.8) {
    const k = (v - 0.2) / 0.6; x = lerp(-60, 60, k); y = 38 - 368 * k * (1 - k); air = 1;
    ang = Math.atan(-368 * (1 - 2 * k) / 120) * 0.28;
  } else { x = lerp(60, 94, (v - 0.8) / 0.2); y = 38 - 2.2 * Math.abs(Math.sin(T * 11)); }
  return { x: x, y: y, air: air, ang: ang, T: T, a: Math.min(ph(v, 0, 0.06), 1 - ph(v, 0.94, 1)) };
}
function drawSheep(o, P) {
  const ctx = o.ctx, al = P.a; if (al <= 0.01) return;
  ctx.save(); ctx.translate(P.x, P.y); ctx.rotate(P.ang);
  ctx.strokeStyle = rgba(MUZZLE, al); ctx.lineWidth = o.lw(1.3, 0.9); ctx.lineCap = 'round';
  const sw = P.air ? 0 : Math.sin(P.T * 11) * 1.3;
  ctx.beginPath();
  for (const lx of [-3.2, -1.3, 1.5, 3.3]) {
    const fwd = lx > 0 ? 1 : -1;
    ctx.moveTo(lx, 2.8);
    if (P.air) ctx.lineTo(lx + fwd * 2.2, 4.8); else ctx.lineTo(lx + sw * (fwd > 0 ? 1 : -1), 6.2);
  }
  ctx.stroke();
  ctx.fillStyle = rgba(WOOL, al); ctx.beginPath();
  for (const c of [[-3.4, 0.5, 2.9], [-0.8, -1.5, 3.3], [2.1, -0.6, 3.0], [0.2, 1.5, 2.9], [-2.2, 2.0, 2.3], [2.6, 1.7, 2.3]]) {
    ctx.moveTo(c[0] + c[2], c[1]); ctx.arc(c[0], c[1], c[2], 0, TAU);
  }
  ctx.fill();
  ctx.fillStyle = rgba(MUZZLE, al); oval(ctx, 5.5, -1.3, 2.2, 1.65, 0.3); ctx.fill();
  oval(ctx, 4.3, -2.9, 1.25, 0.6, -0.7); ctx.fill();
  ctx.fillStyle = rgba(C.bg, al); ctx.beginPath(); ctx.arc(6.1, -1.7, 0.42, 0, TAU); ctx.fill();
  ctx.restore();
}
function flake(ctx, x, y, r, rot, a, lw) {
  if (a <= 0.01 || r <= 0.2) return;
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  ctx.strokeStyle = rgba(COOL, a); ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.beginPath();
  for (let k = 0; k < 6; k++) {
    const an = k * TAU / 6, c = Math.cos(an), sn = Math.sin(an), bx = c * r * 0.55, by = sn * r * 0.55, b = r * 0.32;
    ctx.moveTo(0, 0); ctx.lineTo(c * r, sn * r);
    ctx.moveTo(bx, by); ctx.lineTo(bx + Math.cos(an + 0.85) * b, by + Math.sin(an + 0.85) * b);
    ctx.moveTo(bx, by); ctx.lineTo(bx + Math.cos(an - 0.85) * b, by + Math.sin(an - 0.85) * b);
  }
  ctx.stroke(); ctx.restore();
}

const ANT = {
  // 1. A fly wanders past, lands on the thread, gets flicked off.
  fly: {
    start(s, a) { s.cs.an = { x: -40, y: -22, land: rnd(-0.15, 0.3), sit: false, gone: false }; },
    face(s, F, a, d) {
      const an = s.cs.an, T = a.t, u = a.u, lp = onMouth(s, an.land), lx0 = lp[0], ly0 = lp[1] - 1.6;
      let x, y;
      if (u < 0.6) {
        const k = u / 0.6;
        x = -40 + 44 * k + 15 * Math.sin(k * TAU * 1.25 + 0.4) * (1 - k);
        y = -22 + 24 * k + 9 * Math.sin(k * TAU * 2.2) * (1 - k) + 1.3 * Math.sin(T * 29) * (1 - k);
        const w = sm(ph(k, 0.7, 1)); x = lerp(x, lx0, w); y = lerp(y, ly0, w);
      } else if (u < 0.78) { x = lx0; y = ly0; }
      else { const v = easeInCubic(ph(u, 0.78, 0.93)); x = lx0 + 30 * v; y = ly0 - 34 * v + 3 * Math.sin(v * 12); }
      an.x = x; an.y = y; an.sit = u >= 0.6 && u < 0.78; an.gone = u >= 0.93;
      lookAt(F, x, y);
      if (an.sit) { F.cross = 0.6; F.ly = 1.1; F.lx = clamp(x / 40, -0.35, 0.35); F.open = 1.06; }
      if (u > 0.9) { F.lx = 0.1; F.ly = -0.15; }
      d.bL = d.bR = pulse(T, a.dur * 0.93, 0.17);
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, u = a.u; if (u < 0.6 || u > 0.84) return;
      const dx = (m.x - an.land) / 0.2, w = Math.exp(-dx * dx);
      m.y += 0.06 * w * ph(u, 0.6, 0.63) * (1 - ph(u, 0.77, 0.79));
      m.y += 0.03 * Math.sin(TAU * (q * 3 - a.t * 2.2)) * Math.sin(Math.PI * q) * env(u, 0.64, 0.66, 0.72, 0.75);
      m.y -= 0.34 * w * pulse(u, 0.76, 0.05);                   // the flick
    },
    draw(o, s, a) {
      const an = s.cs.an, g = G(s), ctx = o.ctx, fs = g.fs;
      if (an.gone) return;
      const fade = Math.min(ph(a.u, 0, 0.04), 1 - ph(a.u, 0.86, 0.93));
      const X = an.x * fs, Y = an.y * fs, r = Math.max((g.small ? 1.0 : 1.5) * fs, o.mw(0.7));
      const buzz = !an.sit, wa = buzz ? 0.25 + 0.3 * Math.abs(Math.sin(a.t * 61)) : 0.4;
      ctx.fillStyle = rgba(BUG, wa * fade);
      const wy = (buzz ? 1.7 : 0.9) * r, wr = (buzz ? 1.5 : 1.1) * r;
      oval(ctx, X - 0.9 * r, Y - wy, wr, 0.65 * r, buzz ? 0.5 : 1.1); ctx.fill();
      oval(ctx, X + 0.9 * r, Y - wy, wr, 0.65 * r, buzz ? -0.5 : -1.1); ctx.fill();
      ctx.fillStyle = rgba(BUG, fade); ctx.beginPath(); ctx.arc(X, Y, r, 0, TAU); ctx.fill();
    },
    exitDur: 0.28,
    exit(o, s, c, u) {                                         // the fly zips off
      const an = s.cs.an; startle(o, s, u);
      if (!an || an.gone || an.x === undefined) return;
      const g = G(s), fs = g.fs, ctx = o.ctx, X0 = an.x * fs, Y0 = an.y * fs;
      let dx = X0, dy = Y0 - 4 * fs; const n = Math.hypot(dx, dy) || 1; dx /= n; dy /= n;
      const v = easeInCubic(u) * 40 * fs, X = X0 + dx * v, Y = Y0 + dy * v;
      ctx.strokeStyle = rgba(BUG, 0.4 * (1 - u)); ctx.lineWidth = o.lw(1.1 * fs, 0.7); ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(X0 + dx * v * 0.4, Y0 + dy * v * 0.4); ctx.lineTo(X, Y); ctx.stroke();
      ctx.fillStyle = rgba(BUG, 1 - u); ctx.beginPath(); ctx.arc(X, Y, Math.max((g.small ? 1.0 : 1.5) * fs, o.mw(0.7)), 0, TAU); ctx.fill();
    }
  },

  // 2. Hums to itself: eyes shut, swaying, little notes drifting off the thread.
  tune: {
    start(s, a) { s.cs.an = { notes: [], next: 0, beat: 0, n: 0, on: 0 }; },
    face(s, F, a, d, dt) {
      const an = s.cs.an, T = a.t, u = a.u, on = env(u, 0.03, 0.1, 0.88, 0.96), fs = G(s).fs;
      an.on = on; F.happy = on; F.lx = 0; F.ly = -0.1 * on; F.ma = lerp(0.8, 0.95, on);
      const sw = Math.sin(TAU * T * 0.5);
      d.jx = 1.8 * sw * on * fs; d.jy = -0.8 * Math.abs(sw) * on * fs;
      while (an.next < MEL.length && u >= MEL[an.next][0]) {
        const M = MEL[an.next++], side = M[2] || (an.n++ % 3 === 1) ? -1 : 1;
        an.notes.push({ t: 0, p: M[1], two: !!M[2], side: side, ph: rnd(0, TAU), x: side * 24, y: 7 });
        an.beat = 1;
      }
      an.beat *= Math.exp(-dt / 0.2);
      for (const n of an.notes) { n.t += dt; n.x = n.side * (24 + 4.5 * n.t) + 2 * Math.sin(n.t * 4 + n.ph); n.y = 7 - 14 * n.t - n.p * 1.0; }
      an.notes = an.notes.filter((n) => n.t < 1.8);
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, on = an.on, w = Math.sin(Math.PI * q);
      if (on <= 0) return;
      const hum = 0.08 * w + (0.012 + 0.05 * an.beat) * Math.sin(TAU * (q * 2.5 - a.t * 2.6)) * w;
      m.x = lerp(m.x, m.x * 0.72, on); m.y = lerp(m.y, hum, on);
    },
    draw(o, s, a) {
      const an = s.cs.an, g = G(s), fs = g.fs, sz = g.small ? 11 : 6.5, lw = o.lw(1.1 * fs, 0.8);
      for (const n of an.notes) note(o.ctx, n.x * fs - (n.two && n.side < 0 ? sz * 0.9 : 0), n.y * fs, sz, C.accent, Math.min(1, n.t / 0.15) * (1 - ph(n.t, 1.2, 1.8)) * 0.92, lw, n.two);
    },
    exitDur: 0.28,
    exit(o, s, c, u) {                                         // the notes tumble
      const an = s.cs.an; if (!an || !an.notes) return;
      const g = G(s), fs = g.fs, sz = g.small ? 11 : 6.5, lw = o.lw(1.1 * fs, 0.8);
      for (const n of an.notes) note(o.ctx, n.x * fs - (n.two && n.side < 0 ? sz * 0.9 : 0), (n.y + 14 * u * u) * fs, sz, C.accent, (1 - ph(n.t, 1.2, 1.8)) * (1 - u) * 0.92, lw, n.two);
    }
  },

  // 3. Whistles, eyes wandering innocently about.
  whistle: {
    start(s, a) { s.cs.an = { on: 0, blow: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u, on = env(u, 0.03, 0.12, 0.86, 0.96);
      an.on = on;
      let gx = 0, gy = 0;
      for (const k of [[0.1, -0.85, -0.75], [0.36, 0.9, -0.8], [0.6, -0.7, -0.95], [0.84, 0.1, -0.2], [0.95, 0, 0]]) if (u >= k[0]) { gx = k[1]; gy = k[2]; }
      F.lx = gx; F.ly = gy; F.lid = 0.2 * on; F.slL = F.slR = 0.15 * on;
      an.blow = 0;
      for (const P of PHR) an.blow = Math.max(an.blow, env(u, P[0], P[0] + 0.03, P[1] - 0.02, P[1]));
      d.bL = d.bR = pulse(u, 0.5, 0.03);
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, on = an.on; if (on <= 0) return;
      const ang = TAU * q, tr = 1 + 0.1 * Math.sin(a.t * 18) * an.blow;
      m.x = lerp(m.x, 0.2 + 0.13 * Math.cos(ang) * tr, on); m.y = lerp(m.y, 0.02 + 0.13 * Math.sin(ang) * tr, on);
    },
    draw(o, s, a) {
      const g = G(s), fs = g.fs, ctx = o.ctx, T = a.t, D = a.dur;
      const x0 = 0.36 * g.sx, y0 = g.mY + 0.02 * g.sy, Lm = 30 * fs, sp = 36 * fs, lw = o.lw(1.3 * fs, 0.9);
      ctx.lineCap = 'round'; ctx.lineWidth = lw;
      for (const P of PHR) {
        const t0 = P[0] * D, t1 = P[1] * D; if (T < t0) continue;
        const head = Math.min(Lm, sp * (T - t0)), tail = Math.min(Lm, Math.max(0, sp * (T - t1)));
        if (tail >= Lm - 0.5) continue;
        const n = Math.max(2, Math.ceil((head - tail) / (1.4 * fs)));
        let px = 0, py = 0;
        for (let k = 0; k <= n; k++) {
          const X = tail + (head - tail) * k / n, tp = Math.sin(Math.PI * clamp(X / Lm));
          const x = x0 + X, y = y0 + P[3] * X + 1.5 * fs * tp * Math.sin(X / fs * P[2] - T * 13);
          if (k) { ctx.strokeStyle = rgba(C.fg, 0.75 * (1 - 0.75 * X / Lm)); ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y); ctx.stroke(); }
          px = x; py = y;
        }
      }
    },
    exitDur: 0.25,
    exit(o, s, c, u) { startle(o, s, u); }
  },

  // 4. A big yawn: eyes squeezed, the thread opens wide, a tiny tear.
  yawn: {
    start(s, a) { s.cs.an = { op: 0, smk: 0, tear: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u;
      const op = sm(ph(u, 0.08, 0.32)) * (1 - sm(ph(u, 0.54, 0.64)));
      an.op = op; an.smk = pulse(u, 0.6, 0.12); an.tear = env(u, 0.3, 0.4, 0.62, 0.8);
      F.squeeze = u > 0.17 && u < 0.6 ? 1 : 0; F.ly = -0.45 * op; F.lx = 0; F.eyeY = -1.2 * op; F.stretch = op;
      F.lid = 0.4 * env(u, 0.62, 0.68, 0.86, 0.98); F.slL = F.slR = 0.2;
      d.bL = d.bR = pulse(u, 0.76, 0.06);
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, w = Math.sin(Math.PI * q), ow = Math.sqrt(w), op = an.op, y0 = m.y;
      if (op > 0) { m.x = lerp(m.x, -0.34 * Math.cos(Math.PI * q), op); m.y = lerp(y0, -0.26 * ow, op); m.y2 = lerp(y0, 0.58 * ow, op); }
      if (an.smk > 0) m.y += 0.05 * Math.sin(TAU * q * 2 + a.t * 26) * an.smk * w;
    },
    draw(o, s, a) {
      const an = s.cs.an, g = G(s), fs = g.fs;
      drop(o.ctx, g.ex + g.ew * 0.55 + 1.2 * fs, g.eyeCY + (1 + 4 * ph(a.u, 0.42, 0.8)) * fs, 1.3 * fs, COOL, 0.6 * an.tear);
    },
    exitDur: 0.25,
    exit(o, s, c, u) { startle(o, s, u); }
  },

  // 5. Blinks a little rhythm (shave and a haircut... two bits), then a slow wink.
  blinks: {
    start(s, a) { s.cs.an = { tick: 0, wink: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, T = a.t * 4.5 / a.dur;
      for (const b of BEATS) { const v = pulse(T, b[0], 0.15); if (b[1]) d.bL = Math.max(d.bL, v); if (b[2]) d.bR = Math.max(d.bR, v); }
      an.tick = Math.max(pulse(T, 1.9, 0.22), pulse(T, 2.18, 0.22));
      an.wink = sm(ph(T, 2.85, 3.35)) * (1 - sm(ph(T, 3.85, 4.3)));
      F.lx = s.look.x * 0.5 + 0.15 * (d.bR - d.bL); F.ly = s.look.y * 0.5;
      F.lidR = an.wink; F.slR = 0.3 * an.wink; F.oL = 1 + 0.06 * an.wink;
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, w = Math.sin(Math.PI * q), cn = Math.exp(-Math.pow(q / 0.16, 2)) + Math.exp(-Math.pow((1 - q) / 0.16, 2));
      m.y -= 0.12 * an.tick * cn;
      m.y += an.wink * (0.04 * w - 0.15 * Math.pow(q, 2.5)); m.x += 0.04 * an.wink;
    },
    exitDur: 0.22,
    exit(o, s, c, u) { startle(o, s, u); }
  },

  // 6. (desktop) Wonders what time it is: a little clock pops up, the eyes tick with it.
  clock: {
    start(s, a) { const now = new Date(); s.cs.an = { h: now.getHours(), m: now.getMinutes(), s0: now.getSeconds(), sec: -1, flip: 1, sc: 0, oh: 0, smile: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u, sec = Math.floor(a.t);
      an.sc = easeOutBack(ph(u, 0.02, 0.14)) * (1 - sm(ph(u, 0.84, 0.95)));
      if (sec !== an.sec) { an.sec = sec; an.flip = -an.flip; }
      if (u > 0.1 && u < 0.64) { lookAt(F, 70, -50); F.lx += 0.08 * an.flip; }     // tick, tock
      an.oh = env(u, 0.48, 0.52, 0.62, 0.68); F.open = 1 + 0.28 * an.oh; F.eyeY = -1.5 * an.oh;
      an.smile = env(u, 0.68, 0.74, 0.9, 0.98);
      if (u >= 0.64) { F.lx = -0.15; F.ly = 0.15; F.lid = 0.18 * an.smile; }
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, ang = TAU * q;
      if (an.oh > 0) { m.x = lerp(m.x, 0.15 * Math.cos(ang), an.oh); m.y = lerp(m.y, 0.04 + 0.16 * Math.sin(ang), an.oh); }
      if (an.smile > 0) { const cn = Math.exp(-Math.pow(q / 0.18, 2)) + Math.exp(-Math.pow((1 - q) / 0.18, 2)); m.x *= lerp(1, 0.8, an.smile); m.y -= 0.07 * an.smile * cn; }
    },
    draw(o, s, a) {
      const an = s.cs.an, ctx = o.ctx, X = 70, Y = -50, R = 11 * an.sc, al = clamp(an.sc * 1.5);
      if (R < 0.4) return;
      // a thought trail from the pill
      ctx.fillStyle = rgba(C.fg, 0.55 * al);
      ctx.beginPath(); ctx.arc(47, -30, 1.4 * clamp(an.sc), 0, TAU); ctx.fill();
      ctx.beginPath(); ctx.arc(55, -37, 2.0 * clamp(an.sc), 0, TAU); ctx.fill();
      ctx.fillStyle = rgba(C.bg, 0.97 * al); ctx.strokeStyle = rgba(C.fg, 0.8 * al); ctx.lineWidth = o.lw(1.4, 1);
      ctx.beginPath(); ctx.arc(X, Y, R, 0, TAU); ctx.fill(); ctx.stroke();
      ctx.fillStyle = rgba(C.dim, al);
      for (let k = 0; k < 12; k++) { const an2 = k * TAU / 12; ctx.beginPath(); ctx.arc(X + Math.cos(an2) * R * 0.78, Y + Math.sin(an2) * R * 0.78, (k % 3 ? 0.45 : 0.8) * an.sc, 0, TAU); ctx.fill(); }
      const hand = (ang, len, col, lw) => { ctx.strokeStyle = rgba(col, al); ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(X, Y); ctx.lineTo(X + Math.cos(ang) * len, Y + Math.sin(ang) * len); ctx.stroke(); };
      hand(((an.h % 12) + an.m / 60) / 12 * TAU - Math.PI / 2, R * 0.48, C.fg, o.lw(1.7, 1));
      hand(an.m / 60 * TAU - Math.PI / 2, R * 0.7, C.fg, o.lw(1.2, 0.8));
      hand((an.s0 + Math.max(0, an.sec)) / 60 * TAU - Math.PI / 2, R * 0.8, C.accent, o.lw(0.7, 0.6));
      ctx.fillStyle = rgba(C.accent, al); ctx.beginPath(); ctx.arc(X, Y, 1.0 * an.sc, 0, TAU); ctx.fill();
    },
    exitDur: 0.25,
    exit(o, s, c, u) {                                         // the clock pops
      const an = s.cs.an; startle(o, s, u);
      if (!an || an.sc === undefined || an.sc < 0.05) return;
      const ctx = o.ctx, R = 11 * an.sc * (1 + 0.5 * u);
      ctx.strokeStyle = rgba(C.fg, 0.7 * (1 - u)); ctx.lineWidth = o.lw(1.4 * (1 - u) + 0.3, 0.6);
      ctx.beginPath(); ctx.arc(70, -50, R, 0, TAU); ctx.stroke();
    }
  },

  // 7. Ties its thread in a little bow, admires it, pulls it loose.
  bow: {
    start(s, a) { s.cs.an = { tie: 0, pull: 0, wob: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u;
      an.tie = sm(ph(u, 0.12, 0.42)) * (1 - sm(ph(u, 0.8, 0.9)));
      an.pull = env(u, 0.7, 0.78, 0.8, 0.86);
      an.wob = u > 0.86 ? Math.exp(-(u - 0.86) * a.dur / 0.35) : 0;
      if (u < 0.46) { F.ly = 1.0; F.lx = 0; F.cross = 0.35; F.lid = 0.14; }
      else if (u < 0.7) { F.lx = 0; F.ly = 0; F.happy = env(u, 0.49, 0.53, 0.65, 0.69); }
      else if (u < 0.92) { F.ly = 0.9; F.lx = 0; F.cross = 0.2; }
      F.mcol = mixc(C.fg, C.accent, 0.8 * an.tie); F.ma = lerp(0.8, 1, an.tie);
      d.bL = d.bR = pulse(u, 0.93, 0.035);
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an;
      if (an.tie > 0) { const b = bowPt(q, an.pull); m.x = lerp(m.x, b[0], an.tie); m.y = lerp(m.y, b[1], an.tie); }
      if (an.wob > 0.01) m.y += 0.12 * an.wob * Math.sin(TAU * (1.5 * q - a.t * 3.2)) * Math.sin(Math.PI * q);
    },
    draw(o, s, a) {
      const an = s.cs.an, g = G(s), k = ph(an.tie, 0.6, 0.9);
      if (k <= 0) return;
      o.ctx.fillStyle = rgba(s.cs.mc, k); o.ctx.beginPath(); o.ctx.arc(0, g.mY, Math.max(1.5 * g.fs, o.mw(0.9)), 0, TAU); o.ctx.fill();
    },
    exitDur: 0.25,
    exit(o, s, c, u) { startle(o, s, u); }
  },

  // 8. Raises one eyebrow at you (and wiggles it).
  brow: {
    start(s, a) { s.cs.an = { on: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u, on = env(u, 0.08, 0.22, 0.82, 0.96);
      an.on = on;
      F.lx = s.look.x * 0.4; F.ly = s.look.y * 0.4;
      F.browL = on; d.bw = 0.32 * (pulse(u, 0.48, 0.08) + pulse(u, 0.6, 0.08));
      F.lidR = 0.42 * on; F.slR = -0.35 * on; F.oL = 1 + 0.12 * on; F.eyeY = -0.5 * on;
    },
    mouth(s, a, q, i, m) {
      const on = s.cs.an.on; if (on <= 0) return;
      m.x = lerp(m.x, (2 * q - 1) * 0.72 + 0.14, on);
      m.y = lerp(m.y, 0.05 * Math.sin(Math.PI * q) - 0.17 * Math.pow(q, 3) + 0.03, on);
    },
    exitDur: 0.22,
    exit(o, s, c, u) { startle(o, s, u); }
  },

  // 9. (desktop, at night) Counts sheep leaping over the pill; nods off by the fourth.
  sheep: {
    start(s, a) { s.cs.an = { list: [], z: false, n: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u; let landed = 0, cur = null;
      an.list = [];
      for (let i = 0; i < 4; i++) {
        const v = ph(u, 0.03 + i * 0.2, 0.27 + i * 0.2);
        if (v >= 0.82) landed++;
        if (v <= 0 || v >= 1) continue;
        cur = sheepAt(v, a.t + i * 0.37); an.list.push(cur);
      }
      if (cur) { lookAt(F, cur.x, cur.y); an.cx = cur.x; an.cy = cur.y; }
      F.lid = Math.min(0.72, 0.06 + 0.16 * landed); F.slL = F.slR = 0.15;
      an.n = landed;
      if (u > 0.88) {
        F.closed = u < 0.97 ? 1 : 0; F.lx = 0; F.ly = 0.2;
        if (!an.z) { an.z = true; s.spawn({ k: 'z', x: 26, y: -20, vx: 4, vy: -7, max: 2.2, r: 6, col: C.dim }); }
      }
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an; m.y += 0.025 * an.n * Math.sin(Math.PI * q); m.x *= 1 - 0.04 * an.n;
    },
    draw(o, s, a) { for (const P of s.cs.an.list) drawSheep(o, P); },
    exitDur: 0.28,
    exit(o, s, c, u) {                                         // poof
      const an = s.cs.an; if (!an || an.cx === undefined || !an.list.length) return;
      const ctx = o.ctx, r = 3 + 9 * easeOutCubic(u);
      ctx.fillStyle = rgba(WOOL, 0.8 * (1 - u));
      for (let k = 0; k < 7; k++) { const an2 = k / 7 * TAU + 0.3; ctx.beginPath(); ctx.arc(an.cx + Math.cos(an2) * r, an.cy + Math.sin(an2) * r, 2.6 * (1 - u) + 0.3, 0, TAU); ctx.fill(); }
    }
  },

  // 10. (winter) A snowflake drifts down onto the thread; cross-eyed; it melts; brr.
  snow: {
    start(s, a) { s.cs.an = { land: rnd(-0.1, 0.25), x: 1, y: -40, rot: rnd(0, 1), melt: 0, dropT: 0, brr: 0 }; },
    face(s, F, a, d, dt) {
      const an = s.cs.an, u = a.u, T = a.t, lp = onMouth(s, an.land), lx0 = lp[0], ly0 = lp[1] - 2.6;
      let x, y;
      if (u < 0.5) { const k = u / 0.5; x = lerp(1, lx0, sm(k)) + 5.5 * Math.sin(T * 1.8) * (1 - k); y = lerp(-40, ly0, k); }
      else { x = lx0; y = ly0; }
      an.x = x; an.y = y; an.rot += dt * (u < 0.5 ? 1.1 : 0.15);
      an.melt = ph(u, 0.6, 0.74); an.dropT = ph(u, 0.74, 0.84); an.brr = env(u, 0.8, 0.82, 0.9, 0.93);
      lookAt(F, x, y);
      if (u >= 0.42 && u < 0.76) { F.cross = 0.7; F.ly = 1.1; F.lx = clamp(x / 40, -0.3, 0.3); F.open = 1.08; }
      if (an.brr > 0) { d.jx = 0.7 * Math.sin(T * 70) * an.brr * G(s).fs; F.lid = 0.32 * an.brr; F.lx = 0; F.ly = 0; }
      if (u > 0.92) { F.happy = env(u, 0.92, 0.95, 0.99, 1.01); F.lx = 0; F.ly = 0; }
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, u = a.u, w = Math.sin(Math.PI * q);
      if (u >= 0.48 && u < 0.76) { const dx = (m.x - an.land) / 0.22; m.y += 0.05 * Math.exp(-dx * dx) * (1 - an.melt) * ph(u, 0.48, 0.52); }
      if (an.brr > 0) m.y += 0.05 * Math.sin(TAU * q * 7 + a.t * 45) * an.brr * w;
    },
    draw(o, s, a) {
      const an = s.cs.an, g = G(s), fs = g.fs, ctx = o.ctx;
      flake(ctx, an.x * fs, an.y * fs, (g.small ? 5.2 : 4.6) * fs * (1 - 0.75 * an.melt), an.rot, Math.min(ph(a.u, 0, 0.05), 1) * (1 - 0.5 * an.melt) * 0.95, o.lw(1.0 * fs, 0.8));
      if (an.melt > 0.3 && an.dropT < 1) drop(ctx, an.x * fs, (an.y + 3 + 9 * easeInCubic(an.dropT)) * fs, 1.2 * fs * clamp((an.melt - 0.3) * 2), COOL, 0.85 * (1 - an.dropT));
    },
    exitDur: 0.25,
    exit(o, s, c, u) {                                         // it falls away
      const an = s.cs.an; startle(o, s, u);
      if (!an || an.x === undefined || an.melt >= 1) return;
      const g = G(s), fs = g.fs;
      flake(o.ctx, an.x * fs, (an.y + 18 * u * u) * fs, (g.small ? 5.2 : 4.6) * fs * (1 - 0.75 * an.melt), an.rot + u, (1 - u) * 0.9, o.lw(1.0 * fs, 0.8));
    }
  },

  // 11. Ah... ah... CHOO. Then a sheepish grin.
  sneeze: {
    start(s, a) { s.cs.an = { build: 0, choo: 0, fired: false, shy: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u, T = a.t, D = a.dur, g = G(s), fs = g.fs;
      const ah1 = env(u, 0.06, 0.2, 0.25, 0.31), ah2 = env(u, 0.31, 0.5, 0.57, 0.6);
      an.build = Math.max(0.55 * ah1, ah2); an.choo = u >= 0.6 && u < 0.7 ? 1 : 0;
      const jolt = u >= 0.6 ? Math.exp(-(u - 0.6) * D / 0.1) : 0;
      F.ly = -0.9 * an.build; F.lx = 0.1 * an.build; F.lid = 0.5 * an.build; F.slL = F.slR = 0.2 * an.build;
      F.eyeY = -1.5 * an.build; F.stretch = 0.6 * an.build - 1.2 * jolt; F.squeeze = an.choo;
      if (u > 0.5 && u < 0.6) d.jx = 0.45 * Math.sin(T * 75) * fs;
      d.jy = 3.2 * jolt * fs;
      if (u >= 0.6 && !an.fired) {
        an.fired = true;
        for (let k = 0; k < 6; k++) s.spawn({ k: 'dot', x: rnd(-4, 4) * fs, y: g.mY + fs, vx: rnd(-40, 40) * fs, vy: rnd(-8, 26) * fs, drag: 5, max: rnd(0.35, 0.6), r: 0.9 * fs, col: C.dim });
      }
      if (u >= 0.7) { F.open = 0.92; d.bL = d.bR = Math.max(pulse(u, 0.74, 0.04), pulse(u, 0.8, 0.04)); }
      an.shy = env(u, 0.84, 0.88, 0.96, 1.0);
      if (u >= 0.84) { F.lx = -0.55 * an.shy; F.ly = 0.35 * an.shy; }
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, w = Math.sin(Math.PI * q), b = an.build, y0 = m.y;
      if (b > 0) { m.x = lerp(m.x, (2 * q - 1) * 0.38, b); m.y = lerp(y0, -0.1 * w, b); m.y2 = lerp(y0, 0.26 * w, b); }
      if (an.choo) { m.x = (2 * q - 1) * 0.45; m.y = 0.03 * Math.sin(TAU * q * 2.5); m.y2 = null; }
      if (an.shy > 0) m.y = lerp(m.y, 0.07 * w - 0.09 * Math.pow(1 - q, 3), an.shy);
    },
    exitDur: 0.22,
    exit(o, s, c, u) { startle(o, s, u); }
  },

  // 12. (island) A long, slow side-eye at the label beside it. Caught: a bead of sweat.
  sideeye: {
    start(s, a) { s.cs.an = { wry: 0, inn: 0 }; },
    face(s, F, a, d) {
      const an = s.cs.an, u = a.u, go = sm(ph(u, 0.05, 0.38));
      if (u < 0.72) { F.lx = 1.28 * go; F.ly = 0.05; F.lid = 0.42 * go; F.slL = F.slR = -0.12 * go; an.wry = go; an.inn = 0; }
      else { an.wry = 0; an.inn = env(u, 0.72, 0.75, 0.93, 1); F.lx = 0; F.ly = -0.1; F.open = 1 + 0.14 * an.inn; d.bL = d.bR = pulse(u, 0.86, 0.045); }
    },
    mouth(s, a, q, i, m) {
      const an = s.cs.an, w = Math.sin(Math.PI * q);
      if (an.wry > 0) { m.x = lerp(m.x, (2 * q - 1) * 0.7 + 0.18, an.wry); m.y = lerp(m.y, 0.05 + 0.05 * q * q - 0.02 * w, an.wry); }
      if (an.inn > 0) { m.x = lerp(m.x, (2 * q - 1) * 0.55, an.inn); m.y = lerp(m.y, 0.15 * w, an.inn); }
    },
    exitDur: 0.3,
    exit(o, s, c, u) {
      const g = G(s), fs = g.fs;
      drop(o.ctx, g.ex + g.ew * 0.5 + 3.5 * fs, g.eyeCY - g.eh * 0.45 + 4 * fs * easeInCubic(u), 1.6 * fs, COOL, 0.9 * (1 - ph(u, 0.6, 1)));
    }
  }
};

// ---- the character ----------------------------------------------------------------------
const Hum = {
  id: 'hum', name: 'Hum', kind: 'the pill itself',
  line: 'No body at all: the island is its face. Two eyes and a mouth made of the voice thread.',
  antics: ANT,
  enter(s, st) {
    const cs = s.cs;
    if (!cs.mx) {
      cs.mx = new Float32Array(N); cs.my = new Float32Array(N); cs.my2 = new Float32Array(N); cs.mA = new Float32Array(N).fill(1);
      for (let i = 0; i < N; i++) { cs.mx[i] = -1 + 2 * i / (N - 1); cs.my[i] = cs.my2[i] = 0.12 * Math.sin(Math.PI * i / (N - 1)); }
      cs.mc = C.fg.slice(); cs.d = { bL: 0, bR: 0, wL: 0, wR: 0, jx: 0, jy: 0, bw: 0 };
    }
    cs.trim = st === 'happy' ? 0 : 1;
    cs.zT = st === 'asleep' ? 1.5 : 0.4;
    cs.nodAt = 5 + K.random() * 5;
    if (s.p.open === undefined) Hum.update(s, 0);
  },
  update(s, dt) {
    const st = s.state, t = s.t, cs = s.cs, calm = s.calm, L = s.L, A = s.A;
    const F = { open: 1, lx: s.look.x, ly: s.look.y, happy: 0, rOpen: 1, eyeY: 0, mcol: C.fg, ma: 0.8, glow: 0.04, rim: [C.fg, 0.09], bounce: 0,
                lid: 0, lidL: 0, lidR: 0, slL: 0, slR: 0, closed: 0, squeeze: 0, cross: 0, browL: 0, browR: 0, stretch: 0, oL: 1, oR: 1 };
    const d = cs.d = { bL: 0, bR: 0, wL: 0, wR: 0, jx: 0, jy: 0, bw: 0 };
    switch (st) {
    case 'idle': F.open = 1 + s.notice * 0.25; F.eyeY = -s.notice * 2; F.glow = 0.04 + 0.03 * (calm ? 0 : breath(s.it * 0.1)); break;
    case 'off': F.open = 0.12; F.eyeY = 2.5; F.ma = 0; F.rim = [C.fg, 0.05]; F.glow = 0; if (s.near > 0.6) F.rOpen = 4; break;
    case 'listening': F.open = 1.1 + 0.15 * L; F.eyeY = -1.5 * L; F.ma = 0.95; F.rim = [C.accent, 0.22]; F.glow = 0.05 + 0.1 * L; break;
    case 'woke':                                                       // the catch: eyes pop, a little "o!"
      F.open = t < 0.16 ? 1.42 : 1.12; F.eyeY = -2.4; F.ma = 0.95; F.rim = [C.accent, 0.24];
      F.glow = 0.12 * (1 - ph(t, 0, 0.6)); if (!calm && t < 0.36) F.bounce = Math.sin(Math.PI * t / 0.36); break;
    case 'thinking': F.lx = 0.7; F.ly = -0.85; F.open = 0.85; F.mcol = C.accent; F.ma = 1; F.glow = 0.06; break;
    case 'talking': F.open = 1 - 0.12 * A; F.mcol = C.accent; F.ma = 1; F.rim = [C.accent, 0.18]; F.glow = 0.06 + 0.16 * A; break;
    case 'happy': F.happy = t < 2.6 ? 1 : 0; F.mcol = C.ok; F.ma = 1; F.glow = 0.12 * Math.max(0, 1 - t / 1.2); if (!calm && t < 0.5) F.bounce = Math.sin(Math.PI * t / 0.5); break;
    case 'confused': F.rOpen = 0.42; F.open = 1.12; F.mcol = C.bad; F.ma = 1; F.rim = [C.bad, 0.25]; break;
    case 'working': F.ly = 0.8; F.lx = calm ? 0 : Math.sin(TAU * 0.5 * t) * 0.8; F.open = 0.8; F.mcol = C.dim; F.ma = 0.6; break;
    case 'sending':
      F.lx = t < 0.25 ? 0 : t < 1.1 ? clamp((t - 0.25) / 0.6) : (t < 1.6 ? 1 : 0); F.ly = t < 1.6 ? -0.1 : 0;
      if (t > 1.6 && t < 2.8) F.happy = 1;
      F.mcol = t < 1.6 ? C.accent : C.fg; F.ma = 1; break;
    case 'approval_needed': {                                          // glances at the card, back at you, brow up
      const toCard = calm ? false : (s.it % 4.4) < 2.4;
      F.lx = toCard ? 0.8 : s.look.x * 0.5; F.ly = toCard ? 0.5 : s.look.y * 0.5;
      F.browL = 0.75; F.oL = 1.08; F.lidR = 0.12; F.slR = -0.2;
      F.mcol = C.accent; F.ma = 1; F.rim = [C.accent, 0.22]; F.glow = 0.05 + 0.04 * (calm ? 0.5 : breath(s.it * 0.5));
      cs.ask = calm ? 0.5 : 0.5 + 0.5 * Math.sin(TAU * s.it * 0.45);
      break;
    }
    case 'drowsy': {                                                   // heavy lids that sink, catch, sink
      const p9 = s.it * TAU / 9;
      let nod = 0;
      if (!calm) {
        cs.nodAt -= dt;
        if (cs.nodAt < 0) {
          const k = -cs.nodAt;
          if (k < 1.9) nod = k < 1.5 ? easeInCubic(k / 1.5) : -0.6 * (1 - (k - 1.5) / 0.4);
          else cs.nodAt = 7 + K.random() * 6;
        }
      }
      F.lid = calm ? 0.45 : clamp(0.42 + 0.08 * Math.sin(p9) + 0.48 * nod - 0.3 * s.notice, 0.05, 1);
      F.slL = F.slR = 0.14; F.eyeY = 1.5 + 1.5 * Math.max(0, nod); F.ly = 0.25 + s.look.y * 0.4; F.lx = s.look.x * 0.6;
      F.ma = 0.6; F.mcol = mixc(C.fg, C.dim, 0.3); F.glow = 0.025; F.rim = [C.fg, 0.07];
      cs.sag = calm ? 0.5 : clamp(0.5 + 0.4 * Math.sin(p9 + 1) + 0.4 * Math.max(0, nod));
      break;
    }
    case 'asleep': {                                                   // closed eyes, a slow breathing thread
      const b = calm ? 0.4 : breath(s.it / 5.5);
      cs.br = b;
      F.closed = 1; F.eyeY = 2; F.lx = 0; F.ly = 0.2; F.ma = 0.55; F.mcol = mixc(C.fg, C.dim, 0.5);
      F.glow = 0.015 + 0.03 * b; F.rim = [C.fg, 0.06]; F.stretch = calm ? 0 : 0.22 * b;
      break;
    }
    case 'waking': {                                                   // a big yawn, then the eyes pop open
      const yw = calm ? 0.5 * env(t, 0.1, 0.3, 0.75, 0.95) : env(t, 0.12, 0.42, 0.78, 0.98);
      cs.yaw = yw; cs.smack = calm ? 0 : pulse(t, 0.95, 0.3);
      F.closed = t < 0.2 ? 1 : 0; F.squeeze = t >= 0.2 && t < 0.92 ? 1 : 0;
      F.ly = -0.5 * yw; F.lx = 0; F.eyeY = -1.2 * yw;
      if (t >= 0.92) { F.open = t < 1.2 ? 1.4 : 1; F.eyeY = t < 1.2 ? -2.5 : 0; }
      F.stretch = calm ? 0 : yw; F.ma = 0.9; F.glow = 0.04 + 0.06 * yw;
      d.bL = d.bR = calm ? 0 : pulse(t, 1.3, 0.14);
      break;
    }
    }
    // an antic plays on top of the resting face
    const an = s.antic && ANT[s.antic.id];
    if (an && an.face) an.face(s, F, s.antic, d, dt);
    if (s.caught) { const u = clamp(s.caught.t / (s.caught.dur || 0.25)); F.open += 0.3 * (1 - u); F.eyeY -= 1.6 * (1 - u); }

    s.fo('open', F.open, 0.07); s.fo('rOpen', F.rOpen, 0.08); s.fo('happy', F.happy, 0.06); s.fo('eyeY', F.eyeY, 0.06);
    s.fo('lx', F.lx, 0.08); s.fo('ly', F.ly, 0.08); s.fo('glow', F.glow, 0.12); s.fo('ma', F.ma, 0.1); s.fo('bounce', F.bounce, 0.03);
    s.fo('lid', F.lid, 0.09); s.fo('lidL', F.lidL, 0.08); s.fo('lidR', F.lidR, 0.08); s.fo('slL', F.slL, 0.1); s.fo('slR', F.slR, 0.1);
    s.fo('closed', F.closed, 0.09); s.fo('squeeze', F.squeeze, 0.05); s.fo('cross', F.cross, 0.1);
    s.fo('browL', F.browL, 0.07); s.fo('browR', F.browR, 0.07); s.fo('stretch', F.stretch, 0.08); s.fo('oL', F.oL, 0.08); s.fo('oR', F.oR, 0.08);
    cs.rim = F.rim;
    const kc = 1 - Math.exp(-dt / (calm ? 0.14 : 0.12)); for (let i = 0; i < 3; i++) cs.mc[i] += (F.mcol[i] - cs.mc[i]) * kc;
    // the thread
    const km = 1 - Math.exp(-dt / (calm ? 0.12 : 0.06));
    if (st === 'happy') cs.trim = calm ? 1 : Math.min(1, (cs.trim || 0) + dt / 0.32); else cs.trim = Math.min(1, (cs.trim === undefined ? 1 : cs.trim) + dt / 0.2);
    const m = { x: 0, y: 0, y2: null, a: 1 };
    for (let i = 0; i < N; i++) {
      const q = i / (N - 1);
      m.x = -1 + 2 * q; m.y = 0; m.y2 = null; m.a = 1;
      baseMouth(s, st, t, q, m);
      if (an && an.mouth) an.mouth(s, s.antic, q, i, m);
      cs.mx[i] += (m.x - cs.mx[i]) * km; cs.my[i] += (m.y - cs.my[i]) * km;
      cs.my2[i] += ((m.y2 === null ? m.y : m.y2) - cs.my2[i]) * km; cs.mA[i] += (m.a - cs.mA[i]) * Math.min(1, km * 2);
    }
    if (st === 'off') {
      cs.zT -= dt;
      if (cs.zT <= 0) { cs.zT = 1.8; s.spawn({ k: 'z', x: 30, y: -14, vx: 5, vy: -8, max: 2.2, r: 7, col: C.dim }); }
    } else if (st === 'asleep') {
      cs.zT -= dt;
      if (cs.zT <= 0) { cs.zT = 4.2; const g = G(s); s.spawn({ k: 'z', x: g.small ? 26 : 26, y: g.small ? 4 : 2, vx: 3, vy: -5, max: 3.4, r: g.small ? 6 : 5, col: C.dim, a0: 0.7 }); }
    }
  },
  draw(o, s) {
    const ctx = o.ctx, p = s.p, cs = s.cs, st = s.state, t = s.t, calm = s.calm, d = cs.d || {};
    o.begin();
    const small = o.small, g = geo(small), fs = g.fs;
    cs.g = g;
    let tx = d.jx || 0;
    const ty = d.jy || 0;
    if (st === 'confused' && !calm) tx += 2.5 * 1.6 * Math.exp(-t / 0.11) * Math.sin(TAU * 5.5 * t);
    if (!small) {
      const sc = 1 + 0.04 * p.bounce, sk = p.stretch;
      ctx.save(); ctx.scale(sc * (1 - 0.025 * sk), sc * (1 + 0.06 * sk));
      pillPath(ctx, -50, -29, 100, 58); ctx.fillStyle = rgba(C.bg, 0.97); ctx.fill();
      ctx.save(); ctx.clip();
      if (p.glow > 0.005) { const gr = ctx.createRadialGradient(0, 14, 2, 0, 14, 48); gr.addColorStop(0, rgba(cs.mc, p.glow)); gr.addColorStop(1, rgba(cs.mc, 0)); ctx.fillStyle = gr; ctx.fillRect(-50, -29, 100, 58); }
      ctx.restore();
      ctx.strokeStyle = rgba(cs.rim[0], cs.rim[1]); ctx.lineWidth = o.mw(1); pillPath(ctx, -50, -29, 100, 58); ctx.stroke();
      ctx.restore();
    }
    ctx.save(); ctx.translate(tx, -p.bounce * 3 + ty);
    /* eyes */
    const eyeCY = g.eyeCY + p.eyeY * fs, ew = g.ew, eh = g.eh;
    const eyeCol = st === 'off' ? C.dim : st === 'asleep' ? mixc(C.fg, C.dim, 0.35) : C.fg;
    const lwArc = o.lw(2.6 * fs, 1.2);
    for (const sd of [-1, 1]) {
      const cx = sd * g.ex + (p.lx - sd * p.cross) * 5 * fs, cy = eyeCY + p.ly * 4 * fs;
      let o2 = p.open * (sd === 1 ? p.rOpen : 1) * (sd < 0 ? p.oL : p.oR);
      if (st === 'off') o2 = sd === 1 && s.near > 0.6 ? 0.55 : 0.12;
      const hb = eh * Math.min(o2, 1.3);
      o2 *= 1 - Math.max(s.blink * (st === 'off' ? 0 : 0.9), (sd < 0 ? d.bL : d.bR) * 0.9 || 0);
      const h = Math.max(eh * Math.min(o2, 1.3), o.mw(1.2));
      const lid = clamp(p.lid + (sd < 0 ? p.lidL : p.lidR) + ((sd < 0 ? d.wL : d.wR) || 0));
      const shut = clamp((lid - 0.78) / 0.2);
      const ha = (1 - p.happy) * (1 - p.closed) * (1 - p.squeeze) * (1 - shut);
      if (ha > 0.02) {
        ctx.save();
        if (lid > 0.004) {                     // a flat (or slanted) upper lid
          const sl = sd < 0 ? p.slL : p.slR, cut = cy - h / 2 + lid * h, ext = ew / 2 + 2;
          ctx.beginPath(); ctx.moveTo(cx - sd * ext, cut - sl * ext); ctx.lineTo(cx + sd * ext, cut + sl * ext);
          ctx.lineTo(cx + sd * ext, cy + h); ctx.lineTo(cx - sd * ext, cy + h); ctx.closePath(); ctx.clip();
        }
        ctx.fillStyle = rgba(eyeCol, ha); ctx.beginPath(); K.roundRect(ctx, cx - ew / 2, cy - h / 2, ew, h, Math.min(ew / 2, h / 2)); ctx.fill();
        ctx.restore();
      }
      if (p.happy > 0.02) {
        ctx.strokeStyle = rgba(C.fg, p.happy); ctx.lineWidth = o.lw(3 * fs, 1.2); ctx.lineCap = 'round';
        ctx.beginPath(); ctx.arc(cx, cy + 3 * fs, 5 * fs, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
      }
      const ca = Math.max(p.closed, shut) * (1 - p.happy) * (1 - p.squeeze);
      if (ca > 0.02) {                           // closed: a soft downward curve
        ctx.strokeStyle = rgba(eyeCol, ca); ctx.lineWidth = lwArc; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.arc(cx, cy - 2.2 * fs, 5 * fs, Math.PI * 0.2, Math.PI * 0.8); ctx.stroke();
      }
      if (p.squeeze > 0.02) {                    // squeezed shut: > <
        const k = -sd;
        ctx.strokeStyle = rgba(eyeCol, p.squeeze * (1 - p.happy)); ctx.lineWidth = lwArc; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        ctx.beginPath(); ctx.moveTo(cx - k * 3.4 * fs, cy - 3.8 * fs); ctx.lineTo(cx + k * 3.0 * fs, cy); ctx.lineTo(cx - k * 3.4 * fs, cy + 3.8 * fs); ctx.stroke();
      }
      const bw = sd < 0 ? p.browL + (d.bw || 0) : p.browR;
      if (bw > 0.02) {
        const by = cy - hb / 2 - (3 + 4 * bw) * fs;
        ctx.strokeStyle = rgba(C.fg, 0.85 * clamp(bw * 2.5)); ctx.lineWidth = o.lw(2 * fs, 1); ctx.lineCap = 'round';
        ctx.beginPath(); ctx.arc(cx, by + 7 * fs, 7 * fs, Math.PI * 1.32, Math.PI * 1.68); ctx.stroke();
      }
    }
    if (st === 'confused') { ctx.strokeStyle = rgba(C.fg, 0.8); ctx.lineWidth = o.lw(2 * fs, 1); ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(15 * fs - 6 * fs, eyeCY - 12 * fs); ctx.lineTo(15 * fs + 6 * fs, eyeCY - 9 * fs); ctx.stroke(); }
    /* mouth: the voice thread */
    const mY = g.mY, sx = g.sx, sy = g.sy;
    if (p.ma > 0.01) {
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      const twoLips = st === 'talking' || Math.abs(cs.my2[N >> 1] - cs.my[N >> 1]) > 0.02;
      if (twoLips) {
        ctx.beginPath();
        for (let i = 0; i < N; i++) { const X = cs.mx[i] * sx, Y = mY + cs.my[i] * sy; if (i) ctx.lineTo(X, Y); else ctx.moveTo(X, Y); }
        for (let i = N - 1; i >= 0; i--) ctx.lineTo(cs.mx[i] * sx, mY + cs.my2[i] * sy);
        ctx.closePath(); ctx.fillStyle = rgba(cs.mc, 0.22 * p.ma); ctx.fill();
      }
      const lw = o.lw(2.2, 1.1);
      const strokeLine = (arr) => {
        for (let i = 1; i < N; i++) {
          const a = Math.min(cs.mA[i - 1], cs.mA[i]) * p.ma; if (a < 0.02) continue;
          ctx.strokeStyle = rgba(cs.mc, a); ctx.lineWidth = lw; ctx.beginPath();
          ctx.moveTo(cs.mx[i - 1] * sx, mY + arr[i - 1] * sy); ctx.lineTo(cs.mx[i] * sx, mY + arr[i] * sy); ctx.stroke();
        }
      };
      strokeLine(cs.my); if (twoLips) strokeLine(cs.my2);
    }
    if (st === 'working') {
      const u = (t * 0.7) % 1, X = -sx + 2 * sx * u;
      ctx.fillStyle = rgba(C.accent, 1); ctx.beginPath(); ctx.arc(X, mY, o.lw(2.4, 1.3), 0, TAU); ctx.fill();
    }
    ctx.restore();
    /* sending: the message leaves as a warm dot */
    if (st === 'sending' && t > 0.3 && t < 1.3) {
      const u = easeInCubic(clamp((t - 0.3) / 0.9)), X = lerp(0, small ? 58 : 95, u), Y = mY - 6 * Math.sin(Math.PI * u) * 2;
      ctx.fillStyle = rgba(C.hot, 1 - clamp((u - 0.85) / 0.15)); ctx.beginPath(); ctx.arc(X, Y, o.lw(3, 1.4), 0, TAU); ctx.fill();
    }
    if (st === 'sending' && t > 1.6 && !small) drawCheck(ctx, 38, -16, 10, clamp((t - 1.6) / 0.3), C.ok, 1 - clamp((t - 2.8) / 0.3), o.lw(2.2, 1.1));
    drawParts(o, s);
    o.end();
  }
};
return Hum;
}
