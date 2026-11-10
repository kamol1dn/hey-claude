.pragma library
// Cinder, a living coal. Ported from docs/character-options.html (const Cinder), plus the
// sleep progression (drowsy, asleep, waking), woke, approval_needed and the idle antics.
// make(K) gets the character kit (ui/qml/HeyClaude/Character/kit.js).
function make(K) {
const { C, TAU, clamp, lerp, mixc, rgba, easeOutCubic, easeInCubic, easeInOut, easeOutBack, heat, breath,
        drawParts, drawCheck, drawQ, rnd, pick } = K;
const ell = K.ellipse;

/* ---- little timing helpers (u is 0..1 through an antic) ---------------------------- */
const seg = (u, a, b) => clamp((u - a) / (b - a));
const bump = (u, a, b) => (u <= a || u >= b) ? 0 : Math.sin(Math.PI * (u - a) / (b - a));   // 0 -> 1 -> 0
const win = (u, a, b, f) => { f = f || 0.03; return seg(u, a, a + f) * (1 - seg(u, b - f, b)); }; // 1 inside [a, b]
const smooth = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };

/* ---- palette for the props ------------------------------------------------------------ */
const SMOKE = [150, 144, 140], STEAM = [228, 232, 236], WOOD = [128, 84, 52], WOOD_D = [82, 52, 34];
const MALLOW = [250, 245, 236], GOLD = [238, 190, 118], TOAST = [168, 102, 54], CHAR = [52, 40, 36];
const CLOUD = [126, 134, 148], CLOUD_D = [86, 92, 106], RAIN = [150, 180, 214], SNOW = [232, 242, 255];
const STEEL = [158, 168, 180], STEEL_D = [96, 104, 118], CORN = [252, 242, 214], KERNEL = [226, 168, 64];
const FIREFLY = [214, 240, 118], LEAF = [212, 146, 62], LEAF_D = [150, 92, 40], ASH_HI = [150, 144, 138], ASH_LO = [104, 98, 94];
const INK = [58, 22, 12];

/* ---- shared prop drawing ----------------------------------------------------------- */
// a teardrop of flame standing on (x, y), h tall, its tip leaning by `lean`
function lick(ctx, x, y, w, h, lean) {
  const tx = x + lean, ty = y - h;
  ctx.beginPath(); ctx.moveTo(x - w, y);
  ctx.bezierCurveTo(x - w * 1.15, y - h * 0.45, tx - w * 0.55, ty + h * 0.38, tx, ty);
  ctx.bezierCurveTo(tx + w * 0.25, ty + h * 0.42, x + w * 1.1, y - h * 0.32, x + w, y);
  ctx.quadraticCurveTo(x, y + w * 0.9, x - w, y); ctx.closePath();
}
function flame(ctx, x, y, w, h, lean, temp, a) {
  if (a <= 0.01 || h <= 0.3) return;
  ctx.fillStyle = rgba(heat(temp + 0.3), 0.92 * a); lick(ctx, x, y, w, h, lean); ctx.fill();
  ctx.fillStyle = rgba(C.whitehot, 0.85 * a); lick(ctx, x, y + 0.4, w * 0.5, h * 0.58, lean * 0.6); ctx.fill();
}
// a spark: a soft halo and a white-hot core that never gets thinner than a pixel
function spark(ctx, o, x, y, r, a, col) {
  if (a <= 0.01) return;
  col = col || C.hot;
  const R = r * 3.4, g = ctx.createRadialGradient(x, y, 0, x, y, R);
  g.addColorStop(0, rgba(col, 0.6 * a)); g.addColorStop(1, rgba(col, 0));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, R, 0, TAU); ctx.fill();
  ctx.fillStyle = rgba(C.whitehot, a); ctx.beginPath(); ctx.arc(x, y, Math.max(r, o.mw(0.9)), 0, TAU); ctx.fill();
}
function stroke(ctx, o, col, a, w, minPx) {
  ctx.strokeStyle = rgba(col, a); ctx.lineWidth = o.lw(w, minPx || 0.8); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
}
const CLOUD_LOBES = [[-13, 3, 8], [-4, -3, 10.5], [8, -1, 9], [16, 4, 6.5], [1, 5, 9]];
function drawCloud(ctx, x, y, sc, a, grow) {
  if (a <= 0.01) return;
  ctx.fillStyle = rgba(CLOUD_D, a);
  for (let i = 0; i < CLOUD_LOBES.length; i++) {
    const L = CLOUD_LOBES[i], g = smooth(clamp(grow * 1.6 - i * 0.15));
    ctx.beginPath(); ctx.arc(x + L[0] * sc, y + L[1] * sc + 1.5 * sc, L[2] * sc * g, 0, TAU); ctx.fill();
  }
  ctx.fillStyle = rgba(CLOUD, a);
  for (let i = 0; i < CLOUD_LOBES.length; i++) {
    const L = CLOUD_LOBES[i], g = smooth(clamp(grow * 1.6 - i * 0.15));
    ctx.beginPath(); ctx.arc(x + L[0] * sc, y + L[1] * sc - 0.8 * sc, L[2] * sc * g * 0.88, 0, TAU); ctx.fill();
  }
}
function drawFlake(ctx, o, x, y, r, rot, a) {
  if (a <= 0.01) return;
  stroke(ctx, o, SNOW, a, 1.1, 0.8);
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const an = rot + i * TAU / 6, c = Math.cos(an), s = Math.sin(an);
    ctx.moveTo(x, y); ctx.lineTo(x + c * r, y + s * r);
    if (!o.small) {
      const bx = x + c * r * 0.55, by = y + s * r * 0.55, b1 = an + 0.7, b2 = an - 0.7;
      ctx.moveTo(bx, by); ctx.lineTo(bx + Math.cos(b1) * r * 0.32, by + Math.sin(b1) * r * 0.32);
      ctx.moveTo(bx, by); ctx.lineTo(bx + Math.cos(b2) * r * 0.32, by + Math.sin(b2) * r * 0.32);
    }
  }
  ctx.stroke();
}
function drawMallow(ctx, o, x, y, col, sc, a) {
  if (a <= 0.01 || sc <= 0.02) return;
  ctx.save(); ctx.translate(x, y); ctx.scale(sc, sc);
  ctx.fillStyle = rgba(col, a); ctx.beginPath(); K.roundRect(ctx, -6, -5.5, 12, 11, 3.8); ctx.fill();
  ctx.fillStyle = rgba(mixc(col, [92, 52, 30], 0.32), a * 0.85); ctx.beginPath(); K.roundRect(ctx, -6, 1.5, 12, 4, 1.8); ctx.fill();
  ctx.fillStyle = rgba([255, 255, 255], a * 0.55); ctx.beginPath(); ctx.arc(-2.4, -2.4, 1.5, 0, TAU); ctx.fill();
  ctx.restore();
}
function drawCorn(ctx, o, x, y, sc, rot, a) {
  if (a <= 0.01 || sc <= 0.02) return;
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(sc, sc);
  const lobes = [[-3.6, 0.8, 4], [0, -3, 4.4], [3.8, 0.4, 3.8], [0.4, 2.8, 3.6]];
  ctx.fillStyle = rgba([226, 208, 168], a);
  for (const l of lobes) { ctx.beginPath(); ctx.arc(l[0], l[1] + 0.8, l[2], 0, TAU); ctx.fill(); }
  ctx.fillStyle = rgba(CORN, a);
  for (const l of lobes) { ctx.beginPath(); ctx.arc(l[0] - 0.3, l[1] - 0.2, l[2] * 0.85, 0, TAU); ctx.fill(); }
  ctx.fillStyle = rgba(KERNEL, a * 0.9); ctx.beginPath(); ctx.arc(0.6, 1.6, 1.4, 0, TAU); ctx.fill();
  ctx.restore();
}
function drawKernel(ctx, o, x, y, rot, warm, a) {
  if (a <= 0.01) return;
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  ctx.fillStyle = rgba(mixc(KERNEL, [246, 196, 96], warm), a);
  ctx.beginPath(); ctx.moveTo(0, -4.6); ctx.bezierCurveTo(4.2, -3.4, 4, 3.6, 0, 3.8); ctx.bezierCurveTo(-4, 3.6, -4.2, -3.4, 0, -4.6); ctx.fill();
  ctx.fillStyle = rgba([255, 236, 190], a * 0.7); ctx.beginPath(); ctx.arc(-1, -1.2, 1.1, 0, TAU); ctx.fill();
  ctx.restore();
}
function drawLeaf(ctx, o, x, y, rot, sc, a, burn) {
  if (a <= 0.01 || sc <= 0.02) return;
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(sc, sc);
  const col = mixc(LEAF, [70, 40, 30], burn || 0);
  ctx.fillStyle = rgba(col, a);
  ctx.beginPath(); ctx.moveTo(-6, 0); ctx.quadraticCurveTo(-1, -5, 6, 0); ctx.quadraticCurveTo(-1, 5, -6, 0); ctx.fill();
  stroke(ctx, o, LEAF_D, a, 0.8, 0.5); ctx.beginPath(); ctx.moveTo(-7.5, 0.6); ctx.lineTo(4.5, 0); ctx.stroke();
  if (burn > 0.02) { stroke(ctx, o, C.hot, a * clamp(burn * 2), 1.4, 0.7); ctx.beginPath(); ctx.moveTo(6, 0); ctx.quadraticCurveTo(-1, 5, -6, 0); ctx.stroke(); }
  ctx.restore();
}

/* ---- the character -------------------------------------------------------------------- */
const Cinder = {
  id: 'cinder', name: 'Cinder', kind: 'living coal',
  line: 'A warm little coal with a curl of flame. Brightens when you talk, banks down to ash when you leave.',
  enter(s, st) {
    s.cs.burst = 0; s.cs.shot = 0; s.cs.puff = 0;
    if (s.p.temp === undefined) Cinder.update(s, 0);          // the first paint can come before the first step
  },
  update(s, dt) {
    const st = s.state, t = s.t, cs = s.cs, calm = s.calm, L = s.L, A = s.A;
    const b = calm ? 0.5 : breath(s.it * 0.1);
    const fl = calm ? 0 : (Math.sin(s.T * 11) * 0.5 + Math.sin(s.T * 17.3 + 1) * 0.3 + Math.sin(s.T * 5.1) * 0.2);
    const P = {
      temp: 0.6 + 0.14 * b, size: 0.98 + 0.03 * b, tuft: 19 + 3 * b, glow: 0.1 + 0.06 * b,
      sway: -s.look.x * 0.35 + (calm ? 0 : 0.12 * Math.sin(s.it * 1.3)),
      x: 0, y: 0, sx: 1, sy: 1, ok: 0, bad: 0, eye: 1, mouth: 0, happy: 0,
      lid: 0, squint: 0, ash: 0, ember: 0, brow: 0, wavy: 0, lx: s.look.x, ly: s.look.y
    };
    switch (st) {
      case 'idle': P.temp += s.near * 0.12 + s.notice * 0.2; P.tuft += s.notice * 8; break;
      case 'off': P.temp = 0.02 + s.near * 0.08; P.size = 0.93; P.tuft = 0; P.glow = 0; P.eye = 0; P.sway = 0; break;
      case 'listening': P.temp = 0.75 + 0.4 * L; P.size = 1 + 0.07 * L; P.tuft = 18 + 18 * L + fl * 3 * L; P.glow = 0.16 + 0.3 * L; break;
      case 'thinking': P.temp = 0.7 + 0.06 * Math.sin(TAU * 0.4 * t); P.tuft = 16 + fl * 1.5; P.sway = 0.45 * Math.sin(t * 3.2); P.glow = 0.14; P.lx = 0.55; P.ly = -0.8; break;
      case 'talking': P.temp = 0.78 + 0.4 * A; P.mouth = A; P.tuft = 16 + 10 * A + fl * 2; P.glow = 0.14 + 0.25 * A; P.size = 1 + 0.04 * A; break;
      case 'happy': {
        P.happy = t < 2.4 ? 1 : 0;
        P.temp = t < 0.9 ? 1.15 : 0.82; P.ok = t < 1.2 ? 0.45 : 0;
        P.tuft = (t > 0.15 && t < 0.9) ? 40 + fl * 4 : 18;
        P.glow = t < 1 ? 0.36 : 0.16;
        if (!calm) {
          if (t < 0.18) { P.sx = 1.12; P.sy = 0.84; }
          else if (t < 0.55) { const u = (t - 0.18) / 0.37; P.y = -14 * Math.sin(Math.PI * u); P.sx = 0.93; P.sy = 1.1; }
          else if (t < 0.72) { P.sx = 1.08; P.sy = 0.9; }
        }
        break;
      }
      case 'confused': {
        P.bad = t < 0.9 ? 1 : 0.55; P.temp = 0.5; P.tuft = t < 1.2 ? 2 : 7; P.glow = 0.08;
        if (!calm) P.x = 2.5 * 1.6 * Math.exp(-t / 0.11) * Math.sin(TAU * 5.5 * t);
        P.size = t < 0.06 ? 0.9 : 1;
        break;
      }
      case 'working': P.temp = 0.86 + 0.04 * Math.sin(s.T * 3); P.tuft = 18 + fl * 3; P.glow = 0.2; if (!calm) P.y = Math.sin(s.T * 2.2) * 1.2; break;
      case 'sending': {
        P.temp = 0.8; P.tuft = 16; P.glow = 0.16;
        if (!calm && t < 0.3) { P.sy = 0.86; P.sx = 1.07; P.sway = -0.5; }
        else if (t < 0.7) { P.x = calm ? 0 : -5 * (1 - easeOutCubic((t - 0.3) / 0.4)); P.tuft = 6 + 10 * clamp((t - 0.3) / 0.4); }
        if (t > 1.0 && t < 3) P.happy = 1;
        if (t < 0.9) { P.lx = 0.8; P.ly = -0.6; }
        break;
      }
      /* ---- registry 1.1: the sleep progression ---- */
      case 'drowsy': {
        // banked down: dimmer, smaller, a slow lazy flicker; every so often the lids sink
        const bb = calm ? 0.5 : breath(s.it * 0.055);
        const nod = calm ? 0 : bump((s.it % 9.5) / 9.5, 0.6, 0.82);
        P.temp = 0.42 + 0.07 * bb; P.size = 0.96 + 0.025 * bb; P.sy = 0.96; P.y = 1.2 + 1.5 * nod;
        P.tuft = 9 + 3 * bb + (calm ? 0 : 1.4 * Math.sin(s.T * 3.3) + 0.8 * Math.sin(s.T * 5.2 + 1));
        P.glow = 0.04 + 0.03 * bb; P.sway = -s.look.x * 0.15 + (calm ? 0 : 0.2 * Math.sin(s.it * 0.5));
        P.lid = 0.55 + 0.45 * nod; P.ly = Math.max(P.ly, 0.15);
        break;
      }
      case 'asleep': {
        // warm under a cap of ash: the cracks breathe, a curl of smoke rises
        const bb = calm ? 0.5 : breath(s.it * 0.16);
        P.temp = 0.3 + 0.05 * bb; P.size = 0.95 + 0.025 * bb; P.sy = 0.95 + 0.02 * bb; P.y = 1.5;
        P.tuft = 0; P.glow = 0.05 + 0.06 * bb; P.sway = 0; P.lid = 1; P.ash = 1; P.ember = 0.5 + 0.5 * bb;
        P.lx = 0; P.ly = 0.3;
        break;
      }
      case 'waking': {
        // a shake, the ash puffs off, a stretch and a yawn, then a flare back to life
        if (t < 0.3) {
          const k = seg(t, 0, 0.3);
          P.ash = 1; P.ember = 0.9; P.temp = 0.32 + 0.12 * k; P.tuft = 3 * k; P.glow = 0.05; P.lid = 1; P.squint = k > 0.35 ? 1 : 0;
          if (!calm) { P.sx = 1 + 0.09 * k; P.sy = 1 - 0.11 * k; P.x = 0.8 * Math.sin(t * 70) * k; }
        } else if (t < 0.9) {
          const up = easeOutCubic(seg(t, 0.3, 0.8));
          P.temp = lerp(0.5, 1.15, up); P.tuft = lerp(6, 36, up) + fl * 3; P.glow = 0.32 * up;
          P.lid = t < 0.74 ? 1 : 0; P.eye = t >= 0.74 ? 1.22 : 1;
          P.mouth = 0.8 * bump(t, 0.32, 0.76);
          if (!calm) { const k = bump(t, 0.3, 0.86); P.sy = 1 + 0.14 * k; P.sx = 1 - 0.08 * k; P.y = -5 * k; }
        } else {
          const k = easeInOut(seg(t, 0.9, 1.6));
          P.temp = lerp(1.0, 0.66, k); P.tuft = lerp(32, 19, k) + fl * 2; P.glow = lerp(0.26, 0.12, k); P.eye = lerp(1.15, 1, k);
          if (!calm && t < 1.04) { P.sy = 0.93; P.sx = 1.05; }
          P.lid = bump(t, 1.24, 1.4) > 0.3 ? 1 : 0;
          if (!calm) P.lx = -0.5 * bump(t, 0.92, 1.3) + 0.35 * bump(t, 1.26, 1.6);
        }
        break;
      }
      case 'woke': {
        // heard its name: a hop, eyes wide, the flame stands up
        P.temp = 0.92 + 0.04 * Math.sin(TAU * 0.8 * t) + 0.12 * Math.exp(-t / 0.25);
        P.tuft = 27 + fl * 3 + 10 * Math.exp(-t / 0.22); P.glow = 0.24 + 0.1 * Math.exp(-t / 0.3); P.eye = 1.16; P.size = 1.02;
        P.lx = s.look.x * 0.6; P.ly = Math.min(s.look.y, -0.1);
        if (!calm) {
          if (t < 0.1) { P.sx = 1.08; P.sy = 0.9; }
          else if (t < 0.4) { const u = (t - 0.1) / 0.3; P.y = -6 * Math.sin(Math.PI * u); P.sy = 1.06; P.sx = 0.96; }
        }
        break;
      }
      case 'approval_needed': {
        // asks you: brows up, the flame raises like a hand and waves, a soft amber pulse
        const ph = (t % 1.8) / 1.8, wave = calm ? 0 : bump(ph, 0.05, 0.6) * Math.sin(TAU * 2.5 * ph);
        P.temp = 0.74 + 0.06 * Math.sin(TAU * t / 1.8); P.tuft = 24 + fl * 2 + 5 * bump(ph, 0.05, 0.6);
        P.glow = 0.12 + 0.1 * (0.5 + 0.5 * Math.sin(TAU * t / 1.8)); P.sway = 0.3 + 0.75 * wave;
        P.brow = 1; P.eye = 1.08; P.lx = 0.12; P.ly = -0.3;
        if (!calm) P.y = -1.2 * bump(ph, 0.05, 0.35);
        break;
      }
    }
    // an idle antic poses the body
    const an = s.antic && Cinder.antics[s.antic.id];
    if (an && an.pose) an.pose(s, s.antic, P, fl);
    // caught in the middle of one: a little start
    if (cs.startle > 0) {
      cs.startle -= dt; const k = clamp(cs.startle / 0.35);
      P.eye = Math.max(P.eye, 1 + 0.28 * k); P.tuft += 8 * k; P.y -= 3 * Math.sin(Math.PI * k); P.lid = 0; P.squint = 0;
    }
    s.fo('temp', P.temp, st === 'confused' ? 0.06 : 0.12); s.fo('size', P.size, 0.1); s.fo('tuft', P.tuft, 0.07); s.fo('glow', P.glow, 0.12);
    s.fo('sway', P.sway, 0.15); s.fo('x', P.x, cs.startle > 0 ? 0.07 : 0.02); s.fo('y', P.y, 0.03); s.fo('sx', P.sx, 0.05); s.fo('sy', P.sy, 0.05);
    s.fo('ok', P.ok, 0.08); s.fo('bad', P.bad, 0.06); s.fo('eye', P.eye, 0.12); s.fo('mouth', P.mouth, 0.03); s.fo('happy', P.happy, 0.08);
    s.fo('lid', P.lid, (st === 'waking' || cs.startle > 0) ? 0.03 : 0.1); s.fo('squint', P.squint, 0.04); s.fo('ash', P.ash, st === 'waking' ? 0.05 : 0.25); s.fo('ember', P.ember, 0.2);
    s.fo('brow', P.brow, 0.1); s.fo('wavy', P.wavy, 0.05); s.fo('lx', P.lx, 0.05); s.fo('ly', P.ly, 0.05);
    /* particles */
    if (st === 'listening' && L > 0.18) {
      cs.inT = (cs.inT || 0) - dt;
      if (cs.inT <= 0) {
        cs.inT = 0.09 - 0.05 * L;
        const a = rnd(-Math.PI * 0.95, -Math.PI * 0.05), R = rnd(44, 52);
        const sx0 = Math.cos(a) * R, sy0 = 12 + Math.sin(a) * R * 0.9;
        s.spawn({ k: 'dot', x: sx0, y: sy0, vx: -sx0 * 1.9, vy: (12 - sy0) * 1.9, max: 0.48, r: rnd(1.2, 2.2), col: pick([C.hot, C.whitehot, C.live]), fadeIn: true });
      }
    }
    if (st === 'off') {
      cs.smT = (cs.smT ?? 0.2) - dt;
      if (cs.smT <= 0) { cs.smT = 1.3; s.spawn({ k: 'smoke', x: rnd(-2, 2), y: -8, vx: rnd(1, 4), vy: -11, max: 3, r: 2.2, grow: 9, col: SMOKE }); }
    }
    if (st === 'asleep') {
      cs.zT = (cs.zT ?? 1.2) - dt;
      if (cs.zT <= 0) { cs.zT = 3.6; s.spawn({ k: 'z', x: 20, y: -6, vx: 3.2, vy: -7, max: 3.2, r: 7, col: C.dim }); }
    }
    if (st === 'talking' && s.onset) s.spawn({ k: 'ring', x: 0, y: 12, r: 26, r2: 50, max: 0.52, col: C.hot, a0: 0.3 });
    if (st === 'happy' && !cs.burst && t > 0.2) {
      cs.burst = 1;
      for (let i = 0; i < 14; i++) s.spawn({ k: 'dot', x: rnd(-6, 6), y: -12, vx: rnd(-48, 48), vy: rnd(-120, -60), g: 190, max: rnd(0.8, 1.3), r: rnd(1.3, 2.4), col: pick([C.hot, C.whitehot, C.ok, C.live]) });
    }
    if (st === 'confused' && !cs.puff && t > 0.04) {
      cs.puff = 1;
      for (let i = 0; i < 5; i++) s.spawn({ k: 'smoke', x: rnd(-5, 5), y: -8, vx: rnd(-12, 12), vy: rnd(-26, -14), max: 1.3, r: 2.5, grow: 8, col: [140, 132, 128], drag: 1.5 });
    }
    if (st === 'sending' && !cs.shot && t > 0.3) {
      cs.shot = 1;
      s.spawn({ k: 'trail', x: 4, y: -14, vx: 150, vy: -120, g: 40, max: 1.1, r: 3.2, col: C.hot, col2: C.whitehot, force: true });
    }
    if (st === 'waking') {
      if (!cs.puff && t > 0.3) {
        cs.puff = 1;                                       // the ash cap puffs off
        for (let i = 0; i < 12; i++) { const x0 = rnd(-24, 24); s.spawn({ k: 'dot', x: x0, y: rnd(-6, 2), vx: x0 * 1.6 + rnd(-10, 10), vy: rnd(-70, -30), g: 170, max: rnd(0.6, 1.0), r: rnd(1.1, 2.0), col: pick([ASH_HI, ASH_LO, SMOKE]) }); }
        for (let i = 0; i < 4; i++) s.spawn({ k: 'smoke', x: rnd(-14, 14), y: -6, vx: rnd(-16, 16), vy: rnd(-22, -10), max: 1.1, r: 3, grow: 9, col: SMOKE, drag: 1.4 });
      }
      if (!cs.burst && t > 0.74) {
        cs.burst = 1;
        for (let i = 0; i < 7; i++) s.spawn({ k: 'dot', x: rnd(-4, 4), y: -22, vx: rnd(-40, 40), vy: rnd(-90, -50), g: 150, max: rnd(0.6, 1.0), r: rnd(1.2, 2.0), col: pick([C.hot, C.whitehot, C.live]) });
      }
    }
    if (st === 'woke' && !cs.burst && t > 0.12) {
      cs.burst = 1;
      for (let i = 0; i < 6; i++) s.spawn({ k: 'dot', x: rnd(-3, 3), y: -24, vx: rnd(-36, 36), vy: rnd(-80, -45), g: 150, max: rnd(0.5, 0.85), r: rnd(1.1, 1.9), col: pick([C.hot, C.whitehot]) });
    }
  },
  draw(o, s) {
    const ctx = o.ctx, p = s.p, st = s.state, t = s.t;
    s.cs.small = o.small;
    o.begin();
    const X = p.x, Y = 12 + p.y, rx = 31 * p.size * p.sx, ry = 20 * p.size * p.sy;
    let col = heat(p.temp); col = mixc(col, C.ok, p.ok * 0.6); col = mixc(col, C.bad, p.bad * 0.75);
    /* glow */
    if (p.glow > 0.01) {
      const R = 34 + 38 * p.glow;
      const g = ctx.createRadialGradient(X, Y, 4, X, Y, R + 20); g.addColorStop(0, rgba(col, p.glow)); g.addColorStop(1, rgba(col, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(X, Y, R + 20, 0, TAU); ctx.fill();
    }
    /* working: embers in orbit (back) */
    const orbit = [];
    if (st === 'working') {
      for (let i = 0; i < 3; i++) { const a = s.T * TAU / 3 + i * TAU / 3; orbit.push({ x: X + 42 * Math.cos(a), y: Y + 4 + 11 * Math.sin(a), f: Math.sin(a) > 0 }); }
      for (const q of orbit) if (!q.f) { ctx.fillStyle = rgba(C.banked, 0.9); ctx.beginPath(); ctx.arc(q.x, q.y, Math.max(2.2, o.mw(1)), 0, TAU); ctx.fill(); }
    }
    /* tuft of flame */
    const h = p.tuft;
    if (h > 0.6) {
      const bx = X + p.sway * 3, byy = Y - ry * 0.82, w = 6.5 + h * 0.13;
      const tx = bx + p.sway * h * 0.7, ty = byy - h;
      const tongue = (bx, byy, w, h, tx, ty) => {
        ctx.beginPath(); ctx.moveTo(bx - w, byy + 4);
        ctx.bezierCurveTo(bx - w * 1.15, byy - h * 0.45, tx - w * 0.55 - p.sway * 5, ty + h * 0.38, tx, ty);
        ctx.bezierCurveTo(tx + w * 0.2 - p.sway * 2, ty + h * 0.42, bx + w * 1.1, byy - h * 0.32, bx + w, byy + 4);
        ctx.closePath();
      };
      const fcol = mixc(heat(p.temp + 0.3), C.bad, p.bad * 0.5);
      const g = ctx.createLinearGradient(0, byy, 0, ty);
      g.addColorStop(0, rgba(fcol, 0.95)); g.addColorStop(1, rgba(fcol, 0.55));
      /* a smaller second tongue beside the main one */
      const h2 = h * 0.55, s2 = Math.sin(s.T * 3.1) * 0.15 * (s.calm ? 0 : 1);
      ctx.fillStyle = rgba(fcol, 0.75); tongue(bx + w * 0.75, byy + 2, w * 0.55, h2, bx + w * 0.75 + (p.sway + 0.35 + s2) * h2 * 0.7, byy - h2); ctx.fill();
      ctx.fillStyle = g; tongue(bx, byy, w, h, tx, ty); ctx.fill();
      /* white-hot inner flame */
      const ih = h * 0.62, iw = w * 0.5, itx = bx + p.sway * ih * 0.7;
      ctx.fillStyle = rgba(mixc(C.whitehot, fcol, p.bad * 0.6 + (p.temp < 0.5 ? 0.5 : 0)), 0.9); tongue(bx, byy + 1, iw, ih, itx, byy - ih); ctx.fill();
    }
    /* body: a pebble, flatter underneath */
    ctx.beginPath(); ctx.moveTo(X - rx, Y); ell(ctx, X, Y, rx, ry, 0, Math.PI, TAU); ell(ctx, X, Y, rx, ry * 0.8, 0, 0, Math.PI); ctx.closePath();
    const bg = ctx.createLinearGradient(0, Y - ry, 0, Y + ry * 0.8);
    const colLo = mixc(heat(p.temp - 0.14), C.bad, p.bad * 0.6);
    bg.addColorStop(0, rgba(heat(p.temp + 0.08), 1)); bg.addColorStop(0.45, rgba(colLo, 1));
    bg.addColorStop(0.78, rgba(mixc(colLo, [48, 28, 22], 0.6), 1));
    bg.addColorStop(1, rgba(mixc(heat(p.temp - 0.4), [30, 20, 17], 0.8), 1));
    ctx.fillStyle = bg; ctx.fill();
    ctx.save(); ctx.clip();
    const hg = ctx.createRadialGradient(X - 7, Y - 9, 1, X - 7, Y - 9, 22); hg.addColorStop(0, rgba(heat(p.temp + 0.35), 0.55)); hg.addColorStop(1, rgba(heat(p.temp + 0.35), 0));
    ctx.fillStyle = hg; ctx.fillRect(X - rx, Y - ry, rx * 2, ry * 2);
    /* glowing cracks in the crust: the coal is lit from inside */
    const sc = p.size;
    if (p.temp > 0.08) {
      const ca = clamp((p.temp - 0.08) * 1.4);
      ctx.strokeStyle = rgba(heat(p.temp + 0.45), ca * 0.9); ctx.lineWidth = o.lw(1.3, 0.7); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(X - 23 * sc, Y + 8 * sc); ctx.lineTo(X - 16 * sc, Y + 11 * sc); ctx.lineTo(X - 10 * sc, Y + 10.5 * sc);
      ctx.moveTo(X + 11 * sc, Y + 12.5 * sc); ctx.lineTo(X + 17 * sc, Y + 9.5 * sc); ctx.lineTo(X + 23 * sc, Y + 9 * sc);
      ctx.stroke();
    }
    /* asleep: a cap of ash, embers breathing through its cracks */
    if (p.ash > 0.01) {
      const ay = Y - ry * 0.08;
      ctx.beginPath(); ctx.moveTo(X - rx - 2, Y - ry - 2);
      for (let i = 0; i <= 14; i++) {
        const q = i / 14, xx = X - rx + 2 * rx * q;
        ctx.lineTo(xx, ay + Math.sin(i * 1.9 + 0.4) * 1.5 + Math.sin(i * 3.3) * 0.8 - 3 * Math.cos((q - 0.5) * Math.PI));
      }
      ctx.lineTo(X + rx + 2, Y - ry - 2); ctx.closePath();
      const ag = ctx.createLinearGradient(0, Y - ry, 0, ay + 2);
      ag.addColorStop(0, rgba(ASH_HI, p.ash)); ag.addColorStop(1, rgba(ASH_LO, p.ash));
      ctx.fillStyle = ag; ctx.fill();
      ctx.fillStyle = rgba([84, 78, 74], 0.7 * p.ash);
      for (const d of [[-18, -9], [-6, -14], [9, -12], [20, -6], [-26, -3], [2, -6]]) { ctx.beginPath(); ctx.arc(X + d[0] * sc, Y + d[1] * sc, Math.max(0.9, o.mw(0.5)), 0, TAU); ctx.fill(); }
    }
    if (p.ember > 0.01) {
      const ec = heat(0.9 + 0.3 * p.ember);
      const crack = () => {
        ctx.beginPath();
        ctx.moveTo(X - 21 * sc, Y - 3 * sc); ctx.lineTo(X - 14 * sc, Y - 8 * sc); ctx.lineTo(X - 7 * sc, Y - 6.5 * sc); ctx.lineTo(X - 3 * sc, Y - 13 * sc);
        ctx.moveTo(X + 5 * sc, Y - 15 * sc); ctx.lineTo(X + 10 * sc, Y - 8 * sc); ctx.lineTo(X + 17 * sc, Y - 9.5 * sc); ctx.lineTo(X + 24 * sc, Y - 3 * sc);
        ctx.moveTo(X - 23 * sc, Y + 8 * sc); ctx.lineTo(X - 16 * sc, Y + 11 * sc); ctx.lineTo(X - 10 * sc, Y + 10.5 * sc);
        ctx.moveTo(X + 11 * sc, Y + 12.5 * sc); ctx.lineTo(X + 17 * sc, Y + 9.5 * sc); ctx.lineTo(X + 23 * sc, Y + 9 * sc);
      };
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.strokeStyle = rgba(ec, 0.28 * p.ember); ctx.lineWidth = o.lw(4, 1.4); crack(); ctx.stroke();
      ctx.strokeStyle = rgba(mixc(ec, C.whitehot, 0.3 * p.ember), 0.95 * p.ember); ctx.lineWidth = o.lw(1.4, 0.8); crack(); ctx.stroke();
    }
    ctx.restore();
    /* face */
    const ink = st === 'off' ? [26, 23, 22] : mixc(INK, [40, 26, 22], p.ash * 0.6);
    const lx = p.lx, ly = p.ly;
    const ex = X + lx * 4, ey = Y - 1 + ly * 3;
    const lw = o.lw(2.2, 1.1);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = rgba(ink, 1); ctx.fillStyle = rgba(ink, 1); ctx.lineWidth = lw;
    if (p.happy > 0.5) {
      for (const sx of [-1, 1]) { ctx.beginPath(); ctx.arc(ex + sx * 9.5, ey + 2, 3.4, Math.PI * 1.1, Math.PI * 1.9); ctx.stroke(); }
    } else if (p.squint > 0.5) {
      for (const sx of [-1, 1]) {           // > <  squeezed shut
        const cx = ex + sx * 9.5;
        ctx.beginPath(); ctx.moveTo(cx + sx * 2.6, ey - 3); ctx.lineTo(cx - sx * 2, ey); ctx.lineTo(cx + sx * 2.6, ey + 3); ctx.stroke();
      }
    } else if (p.eye < 0.25 || p.lid > 0.85 || (s.blink > 0.5 && st !== 'off')) {
      const peek = st === 'off' && s.near > 0.6;
      for (const sx of [-1, 1]) {
        if (peek && sx === 1) { ctx.beginPath(); ctx.moveTo(ex + sx * 9.5 + 2.6, ey); ell(ctx, ex + sx * 9.5, ey, 2.6, 2.6, 0, 0, TAU); ctx.fill(); continue; }
        ctx.beginPath(); ctx.arc(ex + sx * 9.5, ey - 1, 3.4, Math.PI * 0.15, Math.PI * 0.85); ctx.stroke();
      }
    } else {
      const conf = st === 'confused';
      for (const sx of [-1, 1]) {
        const cx = ex + sx * 9.5;
        const ryE = 4.3 * p.eye * (conf && sx === 1 ? 0.45 : 1) * (1 + (st === 'listening' ? 0.12 * s.L : 0));
        const ryD = Math.max(ryE, o.mw(1) / 1.2);
        if (p.lid > 0.08) {
          // heavy lids: the lid comes down over the eye, its outer corner drooping (sleepy, not cross)
          const yl = ey - ryD + 2 * ryD * p.lid * 0.8, dr = 1.6 * p.lid, ox = cx + sx * 4.2, ix = cx - sx * 4.2;
          ctx.save(); ctx.beginPath(); ctx.moveTo(ix, yl - dr * 0.3); ctx.lineTo(ox, yl + dr); ctx.lineTo(ox, ey + 8); ctx.lineTo(ix, ey + 8); ctx.closePath(); ctx.clip();
          ctx.beginPath(); ctx.moveTo(cx + 3.1, ey + 0.5 * p.lid); ell(ctx, cx, ey + 0.5 * p.lid, 3.1, ryD, 0, 0, TAU); ctx.fill(); ctx.restore();
          ctx.lineWidth = o.lw(1.2, 0.8); ctx.beginPath(); ctx.moveTo(cx - sx * 3.4, yl - dr * 0.2); ctx.quadraticCurveTo(cx, yl - 0.6, cx + sx * 3.9, yl + dr); ctx.stroke(); ctx.lineWidth = lw;
        } else {
          ctx.beginPath(); ctx.moveTo(cx + 3.1, ey); ell(ctx, cx, ey, 3.1, ryD, 0, 0, TAU); ctx.fill();
          if (!o.small && ryE > 2.5) { ctx.fillStyle = rgba(C.whitehot, 0.85); ctx.beginPath(); ctx.arc(cx - 1, ey - 1.6, 0.9, 0, TAU); ctx.fill(); ctx.fillStyle = rgba(ink, 1); }
        }
      }
    }
    if (p.brow > 0.05 && !o.small) {
      ctx.strokeStyle = rgba(ink, p.brow); ctx.lineWidth = o.lw(1.5, 0.9);
      for (const sx of [-1, 1]) { ctx.beginPath(); ctx.arc(ex + sx * 9.5, ey - 3.5, 4, Math.PI * 1.25, Math.PI * 1.75); ctx.stroke(); }
      ctx.strokeStyle = rgba(ink, 1); ctx.lineWidth = lw;
    }
    /* mouth */
    const mx = X + lx * 3, my = Y + 8 + ly * 2;
    if (p.mouth > 0.04) {
      ctx.fillStyle = rgba(ink, 1); ctx.beginPath(); ctx.moveTo(mx + 3 + 2 * p.mouth, my + 1); ell(ctx, mx, my + 1, 3 + 2 * p.mouth, 0.6 + 5 * p.mouth, 0, 0, TAU); ctx.fill();
      ctx.fillStyle = rgba(C.whitehot, 0.6 * p.mouth); ctx.beginPath(); ctx.moveTo(mx + 1.6 + p.mouth, my + 2 + 2 * p.mouth); ell(ctx, mx, my + 2 + 2 * p.mouth, 1.6 + p.mouth, 0.6 + 2 * p.mouth, 0, 0, TAU); ctx.fill();
    } else if (st === 'confused' || p.wavy > 0.5) {
      ctx.beginPath(); ctx.moveTo(mx - 4, my + 2); ctx.quadraticCurveTo(mx - 2, my, mx, my + 2); ctx.quadraticCurveTo(mx + 2, my + 4, mx + 4, my + 2); ctx.stroke();
    } else if (st !== 'off' && !o.small || p.happy > 0.5) {
      ctx.lineWidth = o.lw(1.6, 1); ctx.beginPath(); ctx.arc(mx, my - 1.5, p.happy > 0.5 ? 4 : 2.6, Math.PI * 0.2, Math.PI * 0.8); ctx.stroke();
    }
    for (const q of orbit) if (q.f) { ctx.fillStyle = rgba(C.hot, 1); ctx.beginPath(); ctx.arc(q.x, q.y, Math.max(2.8, o.mw(1.2)), 0, TAU); ctx.fill(); }
    /* asleep: a slow curl of smoke from under the ash */
    if (p.ash > 0.4 && (st === 'asleep' || st === 'waking')) {
      const ph = s.it * 0.9, x0 = X + 3, y0 = Y - ry + 2, N = 16, ca = clamp((p.ash - 0.4) / 0.6);
      let px = x0, py = y0;
      for (let i = 1; i <= N; i++) {
        const q = i / N, yy = y0 - q * 46, xx = x0 + 7 * q + Math.sin(q * 6 - ph) * (1.5 + 5.5 * q);
        ctx.strokeStyle = rgba(SMOKE, 0.55 * ca * (1 - q) * Math.min(1, q * 5)); ctx.lineWidth = o.lw(1.6 + 1.4 * q, 0.8);
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(xx, yy); ctx.stroke(); px = xx; py = yy;
      }
    }
    drawParts(o, s);
    if (st === 'sending' && t > 1.0) {
      const tr = clamp((t - 1.0) / 0.3), a = 1 - clamp((t - 2.5) / 0.4);
      drawCheck(ctx, 30, -24, 16, tr, C.ok, a, o.lw(2.6, 1.3));
    }
    if (st === 'approval_needed') {
      const a = clamp(t / 0.25), bob = s.calm ? 0 : Math.sin(t * 2.4) * 1.5;
      drawQ(ctx, 34, -30 + bob, 16, C.accent, a, o.lw(2.4, 1.2));
    }
    o.end();
  },
  antics: {}
};

/* =========================================================================================
   Idle antics. Each has pose(s, a, P, fl): it bends the body's targets before the followers
   run (so it moves with the same easing as everything else), plus the kit's draw / exit.
   ========================================================================================= */
const BODY = (s) => ({ X: s.p.x || 0, Y: 12 + (s.p.y || 0) });
// one hop of the body, k = 0..1 through it (squash, stretch, land)
function hop(P, k, h) {
  if (k <= 0 || k >= 1) return;
  if (k < 0.18) { const q = Math.sin(Math.PI * k / 0.18); P.sx *= 1 + 0.1 * q; P.sy *= 1 - 0.12 * q; }
  else if (k < 0.82) { const q = (k - 0.18) / 0.64; P.y -= h * Math.sin(Math.PI * q); P.sy *= 1.06; P.sx *= 0.95; }
  else { const q = Math.sin(Math.PI * (k - 0.82) / 0.18); P.sx *= 1 + 0.08 * q; P.sy *= 1 - 0.1 * q; }
}
function towards(P, s, x, y) { const B = BODY(s); P.lx = clamp((x - B.X) / 26, -1, 1); P.ly = clamp((y - B.Y) / 26, -1, 1); }
// every antic: when caught, a start, its own particles dropped, the state kept for the exit
function antic(d) {
  const userEnd = d.end;
  d.end = function (s, a, caught) {
    if (userEnd) userEnd(s, a, caught);
    if (caught) { s.cs.startle = 0.35; s.cs.ex = a; s.parts = s.parts.filter(q => !q.an); }
  };
  d.exitDur = d.exitDur || 0.28;
  return d;
}

/* ---- 1. roasts a marshmallow --------------------------------------------------------- */
function mallowRig(s, a) {
  const u = a.u, B = BODY(s);
  const inn = easeOutCubic(seg(u, 0, 0.08));
  let th = -2.06, Ln = lerp(22, 50, inn), al = inn;
  th += 0.06 * Math.sin(a.t * 2.2) * win(u, 0.1, 0.5);                // turning it over the flame
  th += 0.3 * Math.sin(a.t * 24) * bump(u, 0.57, 0.67);               // shaking the fire out
  const dn = easeInOut(seg(u, 0.73, 0.81));                            // down to the mouth
  let Hx = lerp(B.X + 25, B.X + 20, dn), Hy = lerp(B.Y - 7, B.Y + 14, dn);
  th = lerp(th, -2.62, dn); Ln = lerp(Ln, 14, dn);
  const out = easeInCubic(seg(u, 0.9, 0.98)); Ln = lerp(Ln, 4, out); al *= 1 - out;
  const E = { x: Hx + Ln * Math.cos(th), y: Hy + Ln * Math.sin(th) };
  const toast = seg(u, 0.1, 0.5), burnt = seg(u, 0.6, 0.67);
  let col = toast < 0.6 ? mixc(MALLOW, GOLD, toast / 0.6) : mixc(GOLD, TOAST, (toast - 0.6) / 0.4);
  col = mixc(col, CHAR, burnt);
  const eat = seg(u, 0.8, 0.9), bites = eat < 0.2 ? 1 : eat < 0.5 ? 0.66 : eat < 0.8 ? 0.36 : 0;
  return { H: { x: Hx, y: Hy }, E, th, al, col, fire: win(u, 0.5, 0.63, 0.015), burnt, sc: bites, hand: inn * (1 - out) };
}
Cinder.antics.marshmallow = antic({
  start(s, a) { a.sm = 0; a.sp = 0; },
  pose(s, a, P, fl) {
    const u = a.u;
    P.lx = 0.15; P.ly = -0.95;
    const roast = win(u, 0.08, 0.52);
    P.tuft += 8 * roast + fl * 2 * roast; P.sway = -0.15 + 0.12 * Math.sin(a.t * 2.2) * roast;
    if (u > 0.36 && u < 0.47) P.happy = 1;                                // golden! a pleased squint
    if (u >= 0.5 && u < 0.57) { P.eye = 1.32; P.mouth = 0.38; P.y -= 2; } // it caught fire
    if (u >= 0.57 && u < 0.67) { P.squint = 1; P.x = 1.2 * Math.sin(a.t * 24); P.tuft -= 6; }
    if (u >= 0.67 && u < 0.74) { P.lid = 0.6; }                           // a long look at the charcoal
    if (u >= 0.74 && u < 0.8) { P.lx = 0.45; P.ly = 0.5; P.mouth = 0.62 * seg(u, 0.76, 0.8); P.eye = 1.1; }
    if (u >= 0.8 && u < 0.93) {
      const e = (u - 0.8) / 0.13, ch = Math.max(0, Math.sin(e * TAU * 3));
      P.happy = 1; P.mouth = 0.3 * ch; P.sx *= 1 + 0.035 * ch; P.sy *= 1 - 0.025 * ch; P.lx = 0.1; P.ly = 0.1;
    }
    if (u >= 0.93) { P.happy = 1; P.lx = 0; P.ly = 0; }
  },
  update(s, dt, a) {
    const r = mallowRig(s, a);
    if (r.fire > 0.5) {
      a.sp -= dt;
      if (a.sp <= 0) { a.sp = 0.12; s.spawn({ k: 'dot', x: r.E.x + rnd(-2, 2), y: r.E.y - 6, vx: rnd(-8, 8), vy: rnd(-40, -24), max: 0.5, r: rnd(0.9, 1.5), col: C.hot, an: 1 }); }
    }
    if (a.u > 0.6 && a.u < 0.76) {
      a.sm -= dt;
      if (a.sm <= 0) { a.sm = 0.2; s.spawn({ k: 'smoke', x: r.E.x, y: r.E.y - 4, vx: rnd(-4, 4), vy: -16, max: 1.3, r: 1.1, grow: 4, col: SMOKE, a0: 0.75, an: 1 }); }
    }
  },
  draw(o, s, a) {
    const ctx = o.ctx, r = mallowRig(s, a);
    if (r.al <= 0.01) return;
    const dx = Math.cos(r.th), dy = Math.sin(r.th);
    stroke(ctx, o, WOOD, r.al, 2.1, 1); ctx.beginPath(); ctx.moveTo(r.H.x - dx * 5, r.H.y - dy * 5); ctx.lineTo(r.E.x - dx * 2, r.E.y - dy * 2); ctx.stroke();
    drawMallow(ctx, o, r.E.x, r.E.y, r.col, r.sc, r.al);
    if (r.burnt > 0.3 && r.sc > 0) { ctx.fillStyle = rgba(C.hot, 0.6 * r.al * (1 - seg(a.u, 0.7, 0.78))); ctx.beginPath(); ctx.arc(r.E.x + 2, r.E.y + 3, 1.3, 0, TAU); ctx.fill(); }
    if (r.fire > 0.01) flame(ctx, r.E.x, r.E.y - 4, 4, 9 + 2 * Math.sin(a.t * 19), Math.sin(a.t * 7) * 1.5, 0.9, r.fire);
    // the little flame "hand" that holds the stick
    flame(ctx, r.H.x, r.H.y + 3, 4.3 * r.hand, 10.5 * r.hand, -1.8, s.p.temp || 0.6, r.al);
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    const ctx = o.ctx, r = mallowRig(s, a), k = 1 - u;
    const th = r.th + 1.4 * u, Ln = 30, Hx = r.H.x + 6 * u, Hy = r.H.y + 26 * u * u;
    stroke(ctx, o, WOOD, k, 2.1, 1); ctx.beginPath(); ctx.moveTo(Hx, Hy); ctx.lineTo(Hx + Ln * Math.cos(th), Hy + Ln * Math.sin(th)); ctx.stroke();
    drawMallow(ctx, o, r.E.x - 10 * u, r.E.y - 14 * u + 30 * u * u, r.col, r.sc, k);
  }
});

/* ---- 2. blows smoke rings -------------------------------------------------------------- */
const RING_T = [0.2, 0.36, 0.52, 0.665];
function ringAt(s, a, i, age) {
  const small = i === 3, life = small ? 1.5 : 2.8, q = clamp(age / life);
  const B = BODY(s), M = { x: B.X + 6, y: B.Y + 6 };
  const D = small ? 1.18 : 1, go = easeOutCubic(small ? Math.pow(q, 0.8) : q);
  const x = M.x + 40 * D * go + Math.sin(age * 3 + i) * 1.4, y = M.y - 50 * D * go;
  const r = small ? lerp(1.6, 4.2, easeOutCubic(q * 1.4)) : lerp(2.2, 10.5, easeOutCubic(q * 1.3));
  const al = Math.min(1, age * 7) * (1 - Math.pow(q, 1.5));
  return { x, y, r, al };
}
Cinder.antics.smoke_rings = antic({
  pose(s, a, P) {
    const u = a.u;
    P.lx = 0.6; P.ly = -0.5;
    const inh = bump(u, 0.02, 0.19);
    P.sy += 0.08 * inh; P.sx -= 0.04 * inh; P.tuft -= 10 * inh; P.temp += 0.1 * inh; if (inh > 0.3) P.lid = 0.65;
    for (const tb of RING_T) { const k = bump(u, tb - 0.025, tb + 0.045); P.mouth = Math.max(P.mouth, 0.3 * k); P.sy -= 0.05 * k; P.sx += 0.03 * k; }
    if (u > 0.76 && u < 0.93) { P.happy = 1; P.tuft += 6 * bump(u, 0.76, 0.86); }
  },
  draw(o, s, a) {
    const ctx = o.ctx;
    for (let i = 0; i < RING_T.length; i++) {
      const age = a.t - RING_T[i] * a.dur; if (age <= 0) continue;
      const R = ringAt(s, a, i, age); if (R.al <= 0.01) continue;
      stroke(ctx, o, [208, 202, 198], 0.85 * R.al, lerp(2.4, 1.4, clamp(age / 2.8)), 0.9);
      ctx.beginPath(); ctx.moveTo(R.x + R.r * Math.cos(-0.45), R.y + R.r * Math.sin(-0.45)); ell(ctx, R.x, R.y, R.r, R.r * 0.6, -0.45, 0, TAU); ctx.stroke();
    }
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    const ctx = o.ctx;
    for (let i = 0; i < RING_T.length; i++) {
      const age = a.t - RING_T[i] * a.dur; if (age <= 0) continue;
      const R = ringAt(s, a, i, age); if (R.al <= 0.01) continue;
      stroke(ctx, o, [208, 202, 198], 0.85 * R.al * (1 - u), 1.6, 0.8);
      const rr = R.r * (1 + 0.8 * u);
      ctx.beginPath(); ctx.moveTo(R.x + rr * Math.cos(-0.45), R.y + rr * Math.sin(-0.45)); ell(ctx, R.x, R.y, rr, rr * 0.6, -0.45, 0, TAU); ctx.stroke();
    }
  }
});

/* ---- 3. sputters in the rain ------------------------------------------------------------ */
function rainCloud(s, a) {
  const u = a.u;
  return { x: -2 + 34 * easeInCubic(seg(u, 0.8, 0.96)), y: -43, grow: seg(u, 0, 0.12), a: 1 - seg(u, 0.88, 0.97), sc: 1 };
}
Cinder.antics.rain = antic({
  start(s, a) { a.drops = []; a.dT = 0; a.flinch = 0; a.n = 0; a.shook = 0; },
  pose(s, a, P, fl) {
    const u = a.u;
    if (u < 0.2) { P.lx = 0; P.ly = -1; P.eye = 1.1; }
    if (u >= 0.2 && u < 0.82) {
      P.lid = 0.5; P.wavy = 1; P.temp -= 0.14; P.glow *= 0.6; P.lx = 0; P.ly = -0.4;
      P.tuft = 9 + 5 * Math.abs(Math.sin(a.t * 13)) + 2 * fl;                 // sputtering
    }
    if (a.flinch > 0) { const k = clamp(a.flinch / 0.25); P.squint = 1; P.tuft -= 5 * k; P.y += 1.2 * k; P.temp -= 0.08 * k; }
    if (u >= 0.84 && u < 0.92) { const k = 1 - seg(u, 0.84, 0.92); P.x = 2.4 * Math.sin(a.t * 42) * k; P.squint = 1; }
    if (u >= 0.92) { const k = bump(u, 0.91, 1); P.happy = 1; P.tuft += 12 * k; P.temp += 0.3 * k; P.glow += 0.12 * k; }
  },
  update(s, dt, a) {
    const cl = rainCloud(s, a), B = BODY(s), rx = 31 * (s.p.size || 1), ry = 20 * (s.p.size || 1);
    if (a.flinch > 0) a.flinch -= dt;
    if (a.u > 0.13 && a.u < 0.8) {
      a.dT -= dt;
      if (a.dT <= 0) { a.dT = 0.075 + K.random() * 0.05; a.drops.push({ x: cl.x + rnd(-17, 17) * cl.sc, y: cl.y + 9, vy: 95 + K.random() * 25 }); }
    }
    const keep = [];
    for (const d of a.drops) {
      d.y += d.vy * dt;
      const ox = (d.x - B.X) / rx;
      const top = Math.abs(ox) < 1 ? B.Y - ry * Math.sqrt(1 - ox * ox) : 99;
      if (d.y >= top) {
        a.n++; a.flinch = 0.25;                                    // tss!
        s.spawn({ k: 'smoke', x: d.x, y: top - 2, vx: rnd(-6, 6), vy: rnd(-22, -14), max: 0.7, r: 1.2, grow: 4, col: STEAM, a0: 0.85, an: 1 });
        continue;
      }
      if (d.y > 30) { s.spawn({ k: 'dot', x: d.x, y: 29, vx: rnd(-14, 14), vy: rnd(-26, -12), g: 120, max: 0.3, r: 0.9, col: RAIN, an: 1 }); continue; }
      keep.push(d);
    }
    a.drops = keep;
    if (!a.shook && a.u > 0.85) {
      a.shook = 1;                                                 // shake it off
      for (let i = 0; i < 8; i++) s.spawn({ k: 'dot', x: B.X + rnd(-20, 20), y: B.Y - 10, vx: rnd(-50, 50), vy: rnd(-50, -20), g: 160, max: 0.6, r: rnd(0.8, 1.4), col: RAIN, an: 1 });
    }
  },
  draw(o, s, a) {
    const ctx = o.ctx, cl = rainCloud(s, a);
    stroke(ctx, o, RAIN, 0.85, 1.3, 0.8);
    ctx.beginPath();
    for (const d of a.drops) { ctx.moveTo(d.x, d.y - 5); ctx.lineTo(d.x - 0.6, d.y); }
    ctx.stroke();
    drawCloud(ctx, cl.x, cl.y, cl.sc, cl.a, cl.grow);
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    const cl = rainCloud(s, a);
    drawCloud(o.ctx, cl.x, cl.y - 6 * u, cl.sc * (1 + 0.4 * u), cl.a * (1 - u), cl.grow);
  }
});

/* ---- 4. chases a runaway spark ----------------------------------------------------------- */
function chaseSpark(s, a) {
  const F = 50, g = 25, tip = -26, u = a.u, L = -F * 0.62;
  const S = [
    [0.02, 0.12, 0, tip, 30, g, 16],
    [0.12, 0.19, 30, g, F - 12, g, 9],
    [0.19, 0.27, F - 12, g, F - 12, g, -3],
    [0.27, 0.34, F - 12, g, F, g, 7],
    [0.34, 0.43, F, g, F, g, -3],
    [0.43, 0.57, F, g, L, g, 74],
    [0.57, 0.66, L, g, L, g, -3],
    [0.66, 0.77, L, g, L * 0.45, g, 8],
    [0.77, 0.87, L * 0.45, g, 0, tip, 26]
  ];
  if (u < 0.02) return { x: 0, y: tip, a: 0 };
  if (u >= 0.87) return { x: 0, y: tip, a: 0 };
  for (const q of S) {
    if (u < q[1]) {
      const k = seg(u, q[0], q[1]);
      if (q[6] < 0) return { x: q[2], y: q[3] + q[6] * Math.abs(Math.sin(Math.PI * k * 3)), a: 1 };   // impatient little bounces
      return { x: lerp(q[2], q[4], k), y: lerp(q[3], q[5], k) - 4 * q[6] * k * (1 - k), a: 1 };
    }
  }
  return { x: 0, y: tip, a: 0 };
}
Cinder.antics.spark_chase = antic({
  start(s, a) { a.tr = []; },
  pose(s, a, P) {
    const u = a.u, R = 16, sp = chaseSpark(s, a);
    // two hops after it, a pause while it leaps over, two hops back
    const hops = [[0.27, 0.35, 0, R * 0.5], [0.35, 0.43, R * 0.5, R], [0.6, 0.68, R, R * 0.5], [0.68, 0.76, R * 0.5, 0]];
    let x = (u >= 0.43 && u < 0.6) ? R : 0;
    for (const h of hops) if (u >= h[0] && u < h[1]) { const k = seg(u, h[0], h[1]); x = lerp(h[2], h[3], easeInOut(k)); hop(P, k, 6); }
    P.x = x;
    if (sp.a > 0) towards(P, s, sp.x, sp.y);
    if (u < 0.12) { P.eye = 1.25; P.mouth = 0.28 * bump(u, 0.02, 0.12); }
    if (u >= 0.27 && u < 0.43) P.lid = 0.3;                         // determined
    if (u >= 0.45 && u < 0.56) { P.eye = 1.3; P.mouth = 0.25; }    // over my head!
    const fl = bump(u, 0.86, 0.98);
    P.temp += 0.42 * fl; P.tuft += 15 * fl; P.glow += 0.18 * fl;
    if (u >= 0.87) { P.happy = 1; P.lx = 0; P.ly = 0; }
  },
  update(s, dt, a) {
    const sp = chaseSpark(s, a);
    if (sp.a > 0) { a.tr.push([sp.x, sp.y]); if (a.tr.length > 6) a.tr.shift(); } else a.tr = [];
  },
  draw(o, s, a) {
    const ctx = o.ctx, sp = chaseSpark(s, a);
    if (sp.a <= 0) return;
    for (let j = 1; j < a.tr.length; j++) {
      const q = j / a.tr.length; stroke(ctx, o, C.hot, 0.5 * q, 2.2 * q, 0.6);
      ctx.beginPath(); ctx.moveTo(a.tr[j - 1][0], a.tr[j - 1][1]); ctx.lineTo(a.tr[j][0], a.tr[j][1]); ctx.stroke();
    }
    spark(ctx, o, sp.x, sp.y, 2.2, 1);
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    const sp = chaseSpark(s, a); if (sp.a <= 0) return;
    const B = BODY(s), k = easeInCubic(u);
    spark(o.ctx, o, lerp(sp.x, B.X, k), lerp(sp.y, B.Y - 26, k), 2.2, 1 - u * u);
  }
});

/* ---- 5. boils a tiny kettle ------------------------------------------------------------------ */
function kettleAt(s, a) {
  const u = a.u, B = BODY(s);
  const land = easeOutBack(seg(u, 0, 0.1)), away = easeInCubic(seg(u, 0.86, 0.98));
  const jig = (a.u > 0.55 && a.u < 0.72) ? Math.sin(a.t * 40) * 0.9 : 0;
  return { x: B.X + jig, y: lerp(-40, B.Y - 40, land) - 24 * away, a: seg(u, 0, 0.05) * (1 - away),
           heat: seg(u, 0.1, 0.55) * (1 - seg(u, 0.7, 0.86)), lid: (u > 0.42 && u < 0.72) ? -1.4 * Math.abs(Math.sin(a.t * 31)) : 0,
           whistle: win(u, 0.55, 0.71, 0.02) };
}
function drawKettle(ctx, o, k) {
  if (k.a <= 0.01) return;
  const x = k.x, y = k.y;
  // handle
  stroke(ctx, o, STEEL_D, k.a, 1.8, 0.9); ctx.beginPath(); ctx.moveTo(x - 8, y - 5); ctx.quadraticCurveTo(x, y - 17, x + 8, y - 5); ctx.stroke();
  // spout
  ctx.fillStyle = rgba(STEEL_D, k.a); ctx.beginPath(); ctx.moveTo(x + 9, y + 2); ctx.lineTo(x + 19, y - 7); ctx.lineTo(x + 21, y - 5.5); ctx.lineTo(x + 12, y + 6); ctx.closePath(); ctx.fill();
  // body: a squat dome
  const g = ctx.createLinearGradient(x - 13, 0, x + 13, 0);
  g.addColorStop(0, rgba(STEEL, k.a)); g.addColorStop(0.35, rgba([206, 214, 224], k.a)); g.addColorStop(1, rgba(STEEL_D, k.a));
  ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(x - 13, y + 8); ctx.bezierCurveTo(x - 14, y - 8, x + 14, y - 8, x + 13, y + 8); ctx.closePath(); ctx.fill();
  // the bottom heats up
  if (k.heat > 0.01) { ctx.fillStyle = rgba(C.hot, 0.7 * k.heat * k.a); ctx.beginPath(); K.roundRect(ctx, x - 12.5, y + 5.5, 25, 2.8, 1.2); ctx.fill(); }
  // lid and knob (rattling)
  ctx.fillStyle = rgba(STEEL_D, k.a); ctx.beginPath(); K.roundRect(ctx, x - 6, y - 5.5 + k.lid, 12, 2.4, 1.2); ctx.fill();
  ctx.beginPath(); ctx.arc(x, y - 7 + k.lid, 1.7, 0, TAU); ctx.fill();
  ctx.fillStyle = rgba([255, 255, 255], 0.5 * k.a); ctx.beginPath(); ctx.arc(x - 6, y - 0.5, 1.4, 0, TAU); ctx.fill();
}
Cinder.antics.kettle = antic({
  start(s, a) { a.st = 0; },
  pose(s, a, P) {
    const u = a.u, k = kettleAt(s, a);
    P.tuft = 15 + 2 * Math.sin(a.t * 9); P.sway = 0.05 * Math.sin(a.t * 3); P.lx = 0.1; P.ly = -1;
    if (u > 0.2 && u < 0.5) P.lid = 0.35;                         // cosy
    if (u >= 0.55 && u < 0.63) { P.eye = 1.35; P.mouth = 0.4; P.tuft += 10; }
    hop(P, seg(u, 0.55, 0.62), 5);
    if (u >= 0.63 && u < 0.78) { P.tuft = 5; P.temp -= 0.16; P.squint = u < 0.7 ? 1 : 0; P.lid = u >= 0.7 ? 0.5 : 0; }   // turns the heat down
    if (u >= 0.86) { P.happy = 1; P.ly = -0.6; }
    if (k.heat > 0) P.temp += 0.05 * k.heat;
  },
  update(s, dt, a) {
    const k = kettleAt(s, a), sx = k.x + 21, sy = k.y - 7;
    a.st -= dt;
    if (k.whistle > 0.5) {
      if (a.st <= 0) { a.st = 0.05; s.spawn({ k: 'smoke', x: sx, y: sy, vx: rnd(55, 75), vy: rnd(-70, -50), max: 0.55, r: 1.2, grow: 5, col: STEAM, a0: 1.0, drag: 2, an: 1 }); }
    } else if (k.heat > 0.4 && a.u < 0.86) {
      if (a.st <= 0) { a.st = lerp(0.6, 0.22, k.heat); s.spawn({ k: 'smoke', x: sx, y: sy, vx: rnd(4, 10), vy: rnd(-18, -12), max: 1.3, r: 1.0, grow: 3.5, col: STEAM, a0: 0.8, an: 1 }); }
    }
  },
  draw(o, s, a) {
    const ctx = o.ctx, k = kettleAt(s, a);
    drawKettle(ctx, o, k);
    if (k.whistle > 0.01) {                                   // ~ ~ the whistle
      stroke(ctx, o, C.fg, 0.8 * k.whistle * (0.6 + 0.4 * Math.sin(a.t * 30)), 1.3, 0.8);
      for (let i = 0; i < 3; i++) {
        const r = 4 + i * 3.6; ctx.beginPath(); ctx.arc(k.x + 22, k.y - 8, r, -1.25, -0.25); ctx.stroke();
      }
    }
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    const k = kettleAt(s, a); k.y -= 22 * easeOutCubic(u); k.a *= 1 - u; k.heat = 0;
    drawKettle(o.ctx, o, k);
  }
});

/* ---- 6. juggles three sparks --------------------------------------------------------------- */
const JUG_PD = 0.6;
function jugPos(s, a, i, t) {
  const B = BODY(s), tip = B.Y - 38, t0 = 0.06 * a.dur + i * JUG_PD * 2 / 3;
  if (t < t0) return null;
  const ph = (t - t0) / JUG_PD, n = Math.floor(ph), f = ph - n, dir = n % 2 === 0 ? 1 : -1, H = 20 + 5 * (i % 2);
  const x = n === 0 ? B.X + 12 * f : B.X + dir * (-12 + 24 * f);
  return { x, y: tip - 4 * H * f * (1 - f) };
}
function jugSpark(s, a, i) {
  const u = a.u, B = BODY(s), land = 0.8 + i * 0.055, tip = { x: B.X, y: B.Y - 34 };
  if (u < 0.72) { const p = jugPos(s, a, i, a.t); return p ? { x: p.x, y: p.y, a: 1 } : null; }
  const p0 = jugPos(s, a, i, 0.72 * a.dur) || tip, apex = { x: B.X + (i - 1) * 12, y: B.Y - 64 };
  if (u < 0.78) { const k = easeOutCubic(seg(u, 0.72, 0.78)); return { x: lerp(p0.x, apex.x, k), y: lerp(p0.y, apex.y, k), a: 1 }; }
  if (u < land - 0.04) return { x: apex.x, y: apex.y + 1.2 * Math.sin(a.t * 6 + i), a: 1 };
  if (u < land) { const k = easeInCubic(seg(u, land - 0.04, land)); return { x: lerp(apex.x, tip.x, k), y: lerp(apex.y, tip.y, k), a: 1 }; }
  return null;
}
Cinder.antics.juggle = antic({
  pose(s, a, P) {
    const u = a.u;
    P.tuft = 22 + 3 * Math.sin(a.t * TAU / JUG_PD); P.sway = 0.55 * Math.sin(a.t * Math.PI / JUG_PD);
    let hi = null;
    for (let i = 0; i < 3; i++) { const q = jugSpark(s, a, i); if (q && (!hi || q.y < hi.y)) hi = q; }
    if (hi) towards(P, s, hi.x, hi.y); else { P.ly = -0.6; }
    for (let i = 0; i < 3; i++) { const land = 0.8 + i * 0.055, k = bump(u, land, land + 0.05); P.temp += 0.3 * k; P.tuft += 10 * k; P.glow += 0.1 * k; }
    if (u > 0.93) { P.happy = 1; P.sy *= 1 - 0.06 * bump(u, 0.93, 1); }
  },
  draw(o, s, a) {
    for (let i = 0; i < 3; i++) { const q = jugSpark(s, a, i); if (q) spark(o.ctx, o, q.x, q.y, 2, q.a); }
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    for (let i = 0; i < 3; i++) { const q = jugSpark(s, a, i); if (q) spark(o.ctx, o, q.x + (i - 1) * 8 * u, q.y + 50 * u * u, 2, 1 - u); }
  }
});

/* ---- 7. fireflies at night (desktop) --------------------------------------------------------- */
const FF = [
  { e: [-96, -56], o: [0, -20], ax: 58, ay: 22, w: 0.9, ph: 0 },
  { e: [96, -30], o: [4, -10], ax: 50, ay: 28, w: -0.75, ph: 2.1 },
  { e: [-92, 36], o: [-4, -28], ax: 70, ay: 16, w: 0.62, ph: 4.2 }
];
function fireflyAt(s, a, i) {
  const f = FF[i], u = a.u, t = a.t, near = 1 - 0.28 * win(u, 0.72, 0.88, 0.05);
  const ox = f.o[0] + f.ax * near * Math.cos(f.w * t + f.ph), oy = f.o[1] + f.ay * near * Math.sin(1.3 * f.w * t + f.ph);
  const ki = easeOutCubic(seg(u, 0, 0.2)), ko = easeInCubic(seg(u, 0.86, 1));
  let x = lerp(f.e[0], ox, ki), y = lerp(f.e[1], oy, ki);
  x = lerp(x, -f.e[0], ko); y = lerp(y, f.e[1] - 30, ko);
  // the blink: their own rhythm, then they answer Cinder's flashes
  let b = 0.35 + 0.65 * Math.pow(Math.max(0, Math.sin(t * 2.4 + i * 2.1)), 2);
  if (u > 0.38 && u < 0.72) b = 0.1 + 1.1 * (bump(u, 0.5, 0.545) + bump(u, 0.665, 0.69) + bump(u, 0.7, 0.725));
  else if (u >= 0.72) b = 0.4 + 0.6 * Math.pow(Math.max(0, Math.sin(t * 2.4)), 2);
  return { x, y, b: b * (1 - ko * 0.7) * seg(u, 0, 0.05) };
}
function drawFirefly(ctx, o, q) {
  const R = 7 * Math.max(0.2, q.b), g = ctx.createRadialGradient(q.x, q.y, 0, q.x, q.y, R);
  g.addColorStop(0, rgba(FIREFLY, 0.75 * q.b)); g.addColorStop(1, rgba(FIREFLY, 0));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(q.x, q.y, R, 0, TAU); ctx.fill();
  ctx.fillStyle = rgba([40, 36, 30], 0.9); ctx.beginPath(); ctx.arc(q.x - 1, q.y - 0.6, 1.4, 0, TAU); ctx.fill();
  ctx.fillStyle = rgba(mixc(FIREFLY, [255, 255, 230], 0.5), clamp(q.b)); ctx.beginPath(); ctx.arc(q.x + 0.6, q.y + 0.4, Math.max(1.3, o.mw(0.8)), 0, TAU); ctx.fill();
}
Cinder.antics.fireflies = antic({
  pose(s, a, P) {
    const u = a.u, q = fireflyAt(s, a, Math.floor(a.t / 2.5) % 3);
    towards(P, s, q.x, q.y);
    if (u > 0.38 && u < 0.72) {
      // dims right down, then flashes: once, then twice
      const flash = bump(u, 0.44, 0.48) + bump(u, 0.58, 0.605) + bump(u, 0.615, 0.64);
      P.temp = lerp(0.36, 1.1, flash); P.glow = 0.36 * flash; P.tuft = lerp(5, 30, flash);
      if (u > 0.49) { P.lx *= 0.5; P.ly = -0.5; P.eye = 1.15; }
    }
    if (u >= 0.72 && u < 0.92) P.happy = 1;
  },
  draw(o, s, a) { for (let i = 0; i < 3; i++) drawFirefly(o.ctx, o, fireflyAt(s, a, i)); },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    for (let i = 0; i < 3; i++) { const q = fireflyAt(s, a, i); q.x *= 1 + 0.8 * u; q.y = q.y * (1 + 0.5 * u) - 10 * u; q.b *= 1 - u; drawFirefly(o.ctx, o, q); }
  }
});

/* ---- 8. a snowflake lands on it (winter) ------------------------------------------------------- */
function flakes(s, a) {
  const u = a.u, B = BODY(s), small = s.cs.small !== false, top = -52, r = small ? 6.5 : 5;
  const k = seg(u, 0, 0.55), main = { x: lerp(-26, B.X + 1, smooth(k)) + 5 * Math.sin(a.t * 1.7) * (1 - k), y: lerp(top, B.Y - 36, k), r, rot: a.t * 0.8, a: (1 - seg(u, 0.55, 0.57)) * seg(u, 0, 0.04) };
  const f2 = { x: 30 + 4 * Math.sin(a.t * 1.3 + 1), y: lerp(top + 4, 34, seg(u, 0.05, 0.9)), r: r * 0.7, rot: -a.t * 0.6, a: win(u, 0.05, 0.9, 0.08) * 0.8 };
  const f3 = { x: -44 + 4 * Math.sin(a.t * 1.1 + 2), y: lerp(top + 14, 34, seg(u, 0.2, 1)), r: r * 0.6, rot: a.t * 0.5, a: win(u, 0.2, 1, 0.08) * 0.7 };
  return [main, f2, f3];
}
Cinder.antics.snowflake = antic({
  start(s, a) { a.hiss = 0; },
  pose(s, a, P) {
    const u = a.u, m = flakes(s, a)[0];
    if (u < 0.55) { towards(P, s, m.x, m.y); P.eye = 1.1; if (u > 0.4) P.tuft -= 4 * seg(u, 0.4, 0.55); }
    if (u >= 0.55 && u < 0.7) { P.squint = 1; P.tuft = lerp(3, 19, seg(u, 0.58, 0.7)); P.x = 1.2 * Math.sin(a.t * 50) * (1 - seg(u, 0.55, 0.68)); P.temp -= 0.12; }
    if (u >= 0.7 && u < 0.95) { P.happy = 1; const g = Math.abs(Math.sin(a.t * 11)) * (1 - seg(u, 0.7, 0.95)); P.y -= 1.6 * g; P.sy *= 1 + 0.03 * g; P.tuft += 4 * g; }
  },
  update(s, dt, a) {
    if (!a.hiss && a.u >= 0.55) {
      a.hiss = 1; const B = BODY(s);
      for (let i = 0; i < 5; i++) s.spawn({ k: 'smoke', x: B.X + rnd(-4, 4), y: B.Y - 30, vx: rnd(-14, 14), vy: rnd(-30, -16), max: rnd(0.9, 1.3), r: 2, grow: 7, col: STEAM, a0: 1.3, drag: 1.2, an: 1 });
    }
  },
  draw(o, s, a) { for (const f of flakes(s, a)) drawFlake(o.ctx, o, f.x, f.y, f.r, f.rot, f.a); },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    for (const f of flakes(s, a)) drawFlake(o.ctx, o, f.x, f.y, f.r * (1 - 0.6 * u), f.rot, f.a * (1 - u));
  }
});

/* ---- 9. nods off for a moment ------------------------------------------------------------------ */
Cinder.antics.doze = antic({
  start(s, a) { a.zT = 0.2; a.pop = 0; },
  pose(s, a, P) {
    const u = a.u, dz = easeInOut(seg(u, 0, 0.25)), sl = win(u, 0.25, 0.6, 0.04);
    if (u < 0.6) {
      P.lid = 0.3 + 0.7 * dz; P.temp -= 0.22 * dz; P.tuft = lerp(P.tuft, 5, dz); P.glow *= 1 - 0.6 * dz; P.sy -= 0.04 * dz; P.y += 1.6 * dz;
      P.sy += 0.03 * Math.sin(a.t * 2.1) * sl; P.y += 1.2 * (0.5 + 0.5 * Math.sin(a.t * 2.1)) * sl; P.ly = 0.4 * dz;
      if (sl > 0.5) P.mouth = 0.08 + 0.05 * Math.sin(a.t * 2.1);
    } else {
      const k = seg(u, 0.6, 0.76);
      P.temp = lerp(1.05, P.temp, k); P.tuft = lerp(36, P.tuft, k); P.glow = lerp(0.3, P.glow, k);
      hop(P, seg(u, 0.6, 0.67), 8);
      if (u < 0.72) { P.eye = 1.35; P.mouth = 0.3 * (1 - seg(u, 0.66, 0.72)); }
      if (u >= 0.67 && u < 0.86) { const q = seg(u, 0.67, 0.86); P.lx = 0.85 * Math.sin(TAU * q * 1.5); P.ly = -0.1; }
      if (u >= 0.87) P.happy = 1;                                  // ... nobody saw that
    }
  },
  update(s, dt, a) {
    if (a.u > 0.28 && a.u < 0.58) { a.zT -= dt; if (a.zT <= 0) { a.zT = 1.4; s.spawn({ k: 'z', x: BODY(s).X + 20, y: -4, vx: 3, vy: -8, max: 2.2, r: 7, col: C.dim, an: 1 }); } }
    if (!a.pop && a.u >= 0.6) {
      a.pop = 1; const B = BODY(s);                               // a knot in the wood pops
      s.parts = s.parts.filter(q => q.k !== 'z');
      for (let i = 0; i < 8; i++) s.spawn({ k: 'dot', x: B.X + rnd(-6, 6), y: B.Y - 24, vx: rnd(-60, 60), vy: rnd(-100, -50), g: 170, max: rnd(0.5, 0.9), r: rnd(1.1, 1.9), col: pick([C.hot, C.whitehot]), an: 1 });
      s.spawn({ k: 'ring', x: B.X, y: B.Y - 20, r: 6, r2: 26, max: 0.4, col: C.whitehot, a0: 0.6, an: 1 });
    }
  },
  exitDur: 0.22,
  exit(o, s, c, u) {
    // eyes snap open: a quick bright blink-line across the face
    const B = BODY(s), ctx = o.ctx;
    stroke(ctx, o, C.whitehot, 0.5 * (1 - u), 1.2, 0.6);
    ctx.beginPath(); ctx.arc(B.X, B.Y, 34 + 10 * u, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
  }
});

/* ---- 10. pops a kernel of corn ------------------------------------------------------------------ */
function cornAt(s, a) {
  const u = a.u, B = BODY(s), tipY = B.Y - 36;
  if (u < 0.15) { const k = seg(u, 0, 0.15); return { kind: 'kernel', x: B.X + 8 * (1 - k), y: lerp(-54, tipY, easeInCubic(k)), rot: a.t * 4, a: seg(u, 0, 0.03) }; }
  if (u < 0.5) { const j = seg(u, 0.15, 0.5); return { kind: 'kernel', x: B.X + Math.sin(a.t * 40) * (0.3 + 1.5 * j * j), y: tipY - Math.abs(Math.sin(a.t * 23)) * 1.6 * j * j, rot: 0.3 * Math.sin(a.t * 31) * j, warm: j, a: 1 }; }
  if (u < 0.78) {
    const k = seg(u, 0.5, 0.78), M = { x: B.X + 3, y: B.Y + 6 };
    return { kind: 'corn', x: lerp(B.X, M.x + 1, k) + 10 * Math.sin(Math.PI * k), y: lerp(tipY, M.y, k) - 4 * 46 * k * (1 - k), rot: k * 5, sc: easeOutBack(clamp(k * 8)) * lerp(1, 0.85, k), a: 1 };
  }
  return null;
}
Cinder.antics.popcorn = antic({
  start(s, a) { a.pop = 0; },
  pose(s, a, P) {
    const u = a.u, c = cornAt(s, a);
    if (c) towards(P, s, c.x, c.y);
    if (u < 0.5) { P.tuft = 15 + 2 * Math.sin(a.t * 9); if (u > 0.2) P.eye = 1 + 0.15 * seg(u, 0.2, 0.5); }
    if (u >= 0.5 && u < 0.58) { P.eye = 1.35; P.tuft += 10; } hop(P, seg(u, 0.5, 0.57), 4);
    if (u >= 0.6 && u < 0.78) { P.x = 4 * easeInOut(seg(u, 0.6, 0.7)); P.mouth = 0.85 * seg(u, 0.62, 0.72); P.eye = 1.15; }
    if (u >= 0.78 && u < 0.95) { const e = (u - 0.78) / 0.17, ch = Math.max(0, Math.sin(e * TAU * 3)); P.x = 4 * (1 - seg(u, 0.85, 0.95)); P.happy = 1; P.mouth = 0.22 * ch; P.sx *= 1 + 0.04 * ch; }
    if (u >= 0.95) P.happy = 1;
  },
  update(s, dt, a) {
    if (!a.pop && a.u >= 0.5) {
      a.pop = 1; const B = BODY(s);
      s.spawn({ k: 'ring', x: B.X, y: B.Y - 36, r: 3, r2: 16, max: 0.35, col: CORN, a0: 0.7, an: 1 });
      for (let i = 0; i < 5; i++) s.spawn({ k: 'dot', x: B.X, y: B.Y - 36, vx: rnd(-50, 50), vy: rnd(-60, -20), g: 140, max: 0.5, r: rnd(0.8, 1.3), col: CORN, an: 1 });
    }
  },
  draw(o, s, a) {
    const c = cornAt(s, a); if (!c) return;
    if (c.kind === 'kernel') drawKernel(o.ctx, o, c.x, c.y, c.rot, c.warm || 0, c.a);
    else drawCorn(o.ctx, o, c.x, c.y, c.sc, c.rot, c.a);
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    const q = cornAt(s, a); if (!q) return;
    if (q.kind === 'kernel') drawKernel(o.ctx, o, q.x + 16 * u, q.y - 20 * u, q.rot + u * 6, 0, 1 - u);
    else drawCorn(o.ctx, o, q.x + 16 * u, q.y - 20 * u, q.sc, q.rot + u * 6, 1 - u);
  }
});

/* ---- 11. a twig for a snack --------------------------------------------------------------------- */
function twigAt(s, a) {
  const u = a.u, B = BODY(s), top = B.Y - 24;
  const k = seg(u, 0, 0.14), b = bump(u, 0.14, 0.19);
  const burn = seg(u, 0.3, 0.64), len = 1 - easeInCubic(seg(u, 0.4, 0.64));
  return { x: B.X - 2 + 6 * (1 - k), y: lerp(-50, top, easeInCubic(k)) - 4 * b, rot: lerp(2.6, 0.12, easeOutCubic(k)), len, burn, a: seg(u, 0, 0.03) * (len > 0.02 ? 1 : 0) };
}
function drawTwig(ctx, o, w) {
  if (w.a <= 0.01 || w.len <= 0.02) return;
  ctx.save(); ctx.translate(w.x, w.y); ctx.rotate(w.rot); ctx.scale(w.len, 1);
  const col = mixc(WOOD, [70, 40, 30], w.burn);
  stroke(ctx, o, col, w.a, 2.2, 1);
  ctx.beginPath(); ctx.moveTo(-12, 0); ctx.lineTo(12, 0); ctx.moveTo(3, 0); ctx.lineTo(8, -5); ctx.stroke();
  if (w.burn < 0.6) drawLeaf(ctx, o, 10.5, -6.5, -0.7, 0.55, w.a * (1 - w.burn / 0.6), w.burn);
  if (w.burn > 0.05) { stroke(ctx, o, C.hot, w.a * clamp(w.burn * 3), 1.2, 0.6); ctx.beginPath(); ctx.moveTo(-12, 0.8); ctx.lineTo(12, 0.8); ctx.stroke(); }
  ctx.restore();
}
Cinder.antics.twig = antic({
  start(s, a) { a.burp = 0; a.sp = 0; },
  pose(s, a, P) {
    const u = a.u, w = twigAt(s, a);
    if (u < 0.14) { towards(P, s, w.x, w.y); P.eye = 1.1; }
    if (u >= 0.14 && u < 0.2) { P.squint = 1; P.sx *= 1.06; P.sy *= 0.9; P.tuft = 6; }    // bonk
    if (u >= 0.2 && u < 0.32) { P.lx = 0; P.ly = -1; P.eye = 1.15; P.tuft = 8; }          // what's up there?
    if (u >= 0.32 && u < 0.64) { P.happy = 1; P.tuft = 26 + 4 * Math.sin(a.t * 14); P.temp += 0.2; P.mouth = 0.18 * Math.max(0, Math.sin(a.t * 12)); }
    const fed = win(u, 0.62, 0.95, 0.08);
    P.size += 0.12 * fed; P.temp += 0.28 * fed; P.glow += 0.14 * fed; P.tuft += 10 * fed;
    if (u >= 0.64 && u < 0.79) P.happy = 1;
    if (u >= 0.79 && u < 0.86) { const k = bump(u, 0.79, 0.86); P.mouth = 0.55 * k; P.sy *= 1 - 0.08 * k; P.sx *= 1 + 0.06 * k; P.squint = 1; }  // burp
    if (u >= 0.86) P.happy = 1;
  },
  update(s, dt, a) {
    const w = twigAt(s, a), B = BODY(s);
    if (w.burn > 0.1 && w.len > 0.1) { a.sp -= dt; if (a.sp <= 0) { a.sp = 0.1; s.spawn({ k: 'dot', x: w.x + rnd(-10, 10) * w.len, y: w.y - 3, vx: rnd(-10, 10), vy: rnd(-55, -30), max: 0.6, r: rnd(0.9, 1.5), col: pick([C.hot, C.whitehot]), an: 1 }); } }
    if (!a.burp && a.u >= 0.8) {
      a.burp = 1;
      s.spawn({ k: 'smoke', x: B.X + 3, y: B.Y + 8, vx: 22, vy: -18, max: 1.2, r: 2.6, grow: 8, col: SMOKE, a0: 1.3, drag: 1.5, an: 1 });
      s.spawn({ k: 'ring', x: B.X + 6, y: B.Y + 6, r: 2, r2: 9, max: 0.5, col: SMOKE, a0: 0.8, an: 1 });
    }
  },
  draw(o, s, a) { drawTwig(o.ctx, o, twigAt(s, a)); },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    const w = twigAt(s, a); w.x -= 24 * u; w.y -= 14 * u - 20 * u * u; w.rot -= 3 * u; w.a *= 1 - u;
    drawTwig(o.ctx, o, w);
  }
});

/* ---- 12. gets the hiccups ------------------------------------------------------------------------ */
const HIC = [0.1, 0.27, 0.43, 0.58, 0.74];
Cinder.antics.hiccups = antic({
  start(s, a) { a.n = 0; },
  pose(s, a, P) {
    const u = a.u;
    let near = 0;
    for (const h of HIC) near = Math.max(near, win(u, h - 0.035, h + 0.06, 0.01));
    if (u > 0.05 && u < 0.8 && near < 0.5) { P.lid = 0.35; P.wavy = 1; P.ly = 0.15; }
    for (let i = 0; i < HIC.length; i++) {
      const big = i === HIC.length - 1, k = bump(u, HIC[i], HIC[i] + (big ? 0.06 : 0.04));
      if (k > 0) { P.y -= (big ? 9 : 5) * k; P.sy *= 1 + 0.09 * k; P.sx *= 1 - 0.05 * k; P.eye = Math.max(P.eye, 1 + 0.4 * k); P.tuft += (big ? 20 : 12) * k; P.temp += 0.2 * k; if (k > 0.3) { P.lid = 0; P.wavy = 0; P.mouth = 0.3 * k; } }
    }
    if (u >= 0.82) { const k = bump(u, 0.82, 1); P.happy = 1; P.sy *= 1 - 0.05 * k; P.sx *= 1 + 0.03 * k; }   // ...phew
  },
  update(s, dt, a) {
    if (a.n < HIC.length && a.u >= HIC[a.n]) {
      const B = BODY(s), big = a.n === HIC.length - 1; a.n++;
      for (let i = 0; i < (big ? 4 : 1); i++) s.spawn({ k: 'dot', x: B.X + rnd(-3, 3), y: B.Y - 30, vx: rnd(-26, 26), vy: rnd(-90, -60), g: 160, max: 0.7, r: rnd(1.2, 1.8), col: pick([C.hot, C.whitehot]), an: 1 });
      s.spawn({ k: 'smoke', x: B.X + 2, y: B.Y - 26, vx: rnd(-4, 4), vy: -20, max: 0.9, r: 1.8, grow: 6, col: SMOKE, an: 1 });
    }
  },
  exit(o, s, c, u) {
    const B = BODY(s), ctx = o.ctx;               // a last little hic, swallowed
    ctx.fillStyle = rgba(C.whitehot, 0.8 * (1 - u)); ctx.beginPath(); ctx.arc(B.X + 2, B.Y - 30 - 14 * u, Math.max(1.4, o.mw(0.9)), 0, TAU); ctx.fill();
  }
});

/* ---- 13. a gust of wind --------------------------------------------------------------------------- */
const LEAVES = [[0.08, -18, 0], [0.2, 4, 1.7], [0.3, -36, 3.1], [0.4, 0, 0]];   // [born, height, phase]; the last one sticks
function leafAt(s, a, i) {
  const u = a.u, B = BODY(s), Lf = LEAVES[i], life = 0.16;
  if (i === 3) {
    // flies in and sticks over Cinder's left eye; then it catches and burns away
    const k = seg(u, 0.4, 0.5), ex = B.X - 9.5, ey = B.Y - 1;
    if (u < 0.4) return null;
    if (u < 0.5) return { x: lerp(-58, ex, easeOutCubic(k)), y: lerp(-10, ey, k) - 8 * Math.sin(Math.PI * k), rot: 6 * (1 - k) + 0.3, sc: 1.35, a: seg(u, 0.4, 0.42), burn: 0 };
    const flut = u < 0.62 ? 0.18 * Math.sin(a.t * 26) : 0, burn = seg(u, 0.62, 0.7);
    return { x: ex, y: ey, rot: 0.3 + flut, sc: 1.35 * (1 - 0.7 * seg(u, 0.66, 0.72)), a: 1 - seg(u, 0.69, 0.73), burn };
  }
  const k = (u - Lf[0]) / life; if (k < 0 || k > 1) return null;
  return { x: lerp(-52, 52, k), y: Lf[1] + 6 * Math.sin(k * 9 + Lf[2]), rot: k * 9 + Lf[2], sc: 0.9, a: win(k, 0, 1, 0.12), burn: 0 };
}
Cinder.antics.wind = antic({
  start(s, a) { a.burnt = 0; },
  pose(s, a, P, fl) {
    const u = a.u, g = seg(u, 0.04, 0.16) * (1 - seg(u, 0.76, 0.9)) * (0.8 + 0.2 * Math.sin(a.t * 6));
    P.sway = 1.5 * g; P.tuft += 3 * g + 2 * fl; P.x += 1.6 * g; P.sx *= 1 + 0.03 * g; P.lx = -0.7 * g; P.ly = 0;
    if (u > 0.1 && u < 0.4) P.lid = 0.5;
    if (u >= 0.5 && u < 0.64) { P.eye = 1.3; P.wavy = 1; P.lx = 0.2; }        // can't see!
    if (u >= 0.62 && u < 0.7) { P.tuft += 6; P.temp += 0.15; }
    if (u >= 0.74 && u < 0.95) P.happy = 1;
  },
  update(s, dt, a) {
    if (!a.burnt && a.u >= 0.7) {
      a.burnt = 1; const B = BODY(s);
      for (let i = 0; i < 6; i++) s.spawn({ k: 'dot', x: B.X - 9.5 + rnd(-3, 3), y: B.Y - 1, vx: rnd(-20, 20), vy: rnd(-45, -20), g: 60, max: 0.7, r: rnd(0.8, 1.4), col: pick([C.hot, ASH_HI]), an: 1 });
      s.spawn({ k: 'smoke', x: B.X - 9.5, y: B.Y - 4, vx: 6, vy: -16, max: 1, r: 2, grow: 6, col: SMOKE, an: 1 });
    }
  },
  draw(o, s, a) {
    const ctx = o.ctx;
    // a few streaks of wind
    const g = seg(a.u, 0.04, 0.16) * (1 - seg(a.u, 0.76, 0.9));
    if (g > 0.02) {
      stroke(ctx, o, C.dim, 0.35 * g, 1, 0.6);
      for (let i = 0; i < 3; i++) { const ph = (a.t * 1.6 + i * 0.37) % 1, y = -30 + i * 22, x = lerp(-60, 60, ph); ctx.beginPath(); ctx.moveTo(x - 12, y); ctx.quadraticCurveTo(x, y - 2, x + 10, y); ctx.stroke(); }
    }
    for (let i = 0; i < LEAVES.length; i++) { const q = leafAt(s, a, i); if (q) drawLeaf(ctx, o, q.x, q.y, q.rot, q.sc, q.a, q.burn); }
    const q = leafAt(s, a, 3);
    if (q && q.burn > 0.01 && q.a > 0.05) flame(ctx, q.x + 2, q.y + 2, 3, 7, 1, 0.8, q.a * Math.sin(Math.PI * q.burn));
  },
  exit(o, s, c, u) {
    const a = s.cs.ex; if (!a) return;
    for (let i = 0; i < LEAVES.length; i++) { const q = leafAt(s, a, i); if (q) drawLeaf(o.ctx, o, q.x + 30 * u, q.y - 10 * u, q.rot + 4 * u, q.sc, q.a * (1 - u), q.burn); }
  }
});

return Cinder;
}
