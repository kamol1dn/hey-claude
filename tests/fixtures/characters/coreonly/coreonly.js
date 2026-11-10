.pragma library
// The smallest pack: a dot per core state (tests/test_characters.py).
function make(K) {
    var COL = { idle: K.C.dim, off: K.C.ash, listening: K.C.fg, thinking: K.C.accent, talking: K.C.hot };
    return {
        update: function (s, dt) { s.fo('r', s.state === 'listening' ? 20 + 20 * s.L : s.state === 'talking' ? 20 + 20 * s.A : 22); },
        draw: function (o, s) {
            var ctx = o.ctx; o.begin();
            ctx.fillStyle = K.rgba(COL[s.state] || K.C.bad, 1);
            ctx.beginPath(); ctx.arc(0, 0, s.p.r || 20, 0, K.TAU); ctx.fill();
            o.end();
        },
        antics: {
            spin: {
                draw: function (o, s, a) { var ctx = o.ctx; ctx.strokeStyle = K.rgba(K.C.accent, 1); ctx.lineWidth = 3;
                    ctx.beginPath(); ctx.arc(0, 0, 50, 0, K.TAU * a.u); ctx.stroke(); },
                exit: function (o, s, c, u) { var ctx = o.ctx; ctx.strokeStyle = K.rgba(K.C.accent, 1 - u); ctx.lineWidth = 3;
                    ctx.beginPath(); ctx.arc(0, 0, 50, 0, K.TAU); ctx.stroke(); }
            }
        }
    };
}
