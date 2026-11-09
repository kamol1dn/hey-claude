import QtQuick

// The pill body: rim, inner glow, followup wash, heat rings, the thread and the
// ember. One ShaderEffect on the GPU (island.frag); a Canvas with the same
// drawing on the software backend or when the shader can't load.
Item {
    id: body
    property bool software: false
    readonly property bool gpuOk: !software && shader.status !== ShaderEffect.Error
    property real aa: 0.6

    function rgba(c, a) { return Qt.rgba(c[0] / 255, c[1] / 255, c[2] / 255, a) }
    function v4c(c, a) { return Qt.vector4d(c[0] / 255, c[1] / 255, c[2] / 255, a) }

    // Called once per engine frame with the engine's `out`.
    function apply(o) {
        if (!gpuOk) { canvas.o = o; canvas.requestPaint(); return }
        var s = shader
        s.size = Qt.size(width, height)
        s.pill = Qt.vector4d(1, 1, o.w, o.h)
        s.ember = Qt.vector4d(o.ex + 1, o.cy + 1, o.er, o.core)
        s.emberCol = v4c(o.emberCol, o.emberA)
        s.coreCol = v4c(o.coreCol, 1)
        s.glow = Qt.vector4d(o.glowR, o.glowA, o.washX < 0 ? -1 : o.washX + 1, o.washA)
        s.glowCol = v4c(o.glowCol, o.rad)
        s.rimCol = v4c(o.rimCol, o.rimA)
        var r = o.rings
        s.ring0 = r.length > 0 ? Qt.vector4d(r[0].x + 1, r[0].y + 1, r[0].r, r[0].a) : Qt.vector4d(0, 0, 0, 0)
        s.ring1 = r.length > 1 ? Qt.vector4d(r[1].x + 1, r[1].y + 1, r[1].r, r[1].a) : Qt.vector4d(0, 0, 0, 0)
        s.ring2 = r.length > 2 ? Qt.vector4d(r[2].x + 1, r[2].y + 1, r[2].r, r[2].a) : Qt.vector4d(0, 0, 0, 0)
        s.ringW = Qt.vector4d(r.length > 0 ? r[0].w : 1, r.length > 1 ? r[1].w : 1, r.length > 2 ? r[2].w : 1, 0)
        s.prog = Qt.vector4d(o.progress > 0 ? o.w * o.progress + 1 : 0, o.progress > 0 ? 0.07 : 0, 0, 0)
        if (o.thrA > 0) {
            var X = o.tx, Y = o.ty, A = o.ta, dx = o.tdx + 1, n = X.length
            var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9
            for (var i = 0; i < n; i++) {
                var px = X[i] + dx, py = Y[i] + 1
                if (px < x0) x0 = px; if (px > x1) x1 = px; if (py < y0) y0 = py; if (py > y1) y1 = py
            }
            var pad = o.thrW * 0.5 + 2
            s.thrBox = Qt.vector4d(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
            s.thr = Qt.vector4d(o.thrW, o.thrA, n, aa)
            s.thrCol = v4c(o.thrCol, 1)
            for (var k = 0; k < 24; k++)
                s["p" + k] = Qt.vector4d(X[2 * k] + dx, Y[2 * k] + 1, X[2 * k + 1] + dx, Y[2 * k + 1] + 1)
            for (var m = 0; m < 12; m++)
                s["a" + m] = Qt.vector4d(A[4 * m], A[4 * m + 1], A[4 * m + 2], A[4 * m + 3])
        } else {
            s.thr = Qt.vector4d(1, 0, 0, aa)
        }
    }

    ShaderEffect {
        id: shader
        anchors.fill: parent
        visible: body.gpuOk
        blending: true
        fragmentShader: Qt.resolvedUrl("island.frag.qsb")
        property size size: Qt.size(10, 10)
        property vector4d pill; property vector4d ember; property vector4d emberCol; property vector4d coreCol
        property vector4d glow; property vector4d glowCol; property vector4d rimCol
        property vector4d ring0; property vector4d ring1; property vector4d ring2; property vector4d ringW
        property vector4d thr: Qt.vector4d(1, 0, 0, 0.6); property vector4d thrCol; property vector4d thrBox; property vector4d prog
        property vector4d p0; property vector4d p1; property vector4d p2; property vector4d p3; property vector4d p4; property vector4d p5
        property vector4d p6; property vector4d p7; property vector4d p8; property vector4d p9; property vector4d p10; property vector4d p11
        property vector4d p12; property vector4d p13; property vector4d p14; property vector4d p15; property vector4d p16; property vector4d p17
        property vector4d p18; property vector4d p19; property vector4d p20; property vector4d p21; property vector4d p22; property vector4d p23
        property vector4d a0; property vector4d a1; property vector4d a2; property vector4d a3; property vector4d a4; property vector4d a5
        property vector4d a6; property vector4d a7; property vector4d a8; property vector4d a9; property vector4d a10; property vector4d a11
    }

    Canvas {
        id: canvas
        // fixed size (the widest pill + 2): no re-allocation while the pill morphs
        x: 0; y: 0; width: 564; height: 400
        visible: !body.gpuOk
        property var o: null
        renderStrategy: Canvas.Immediate
        function rr(c, x, y, w, h, r) {
            r = Math.max(0, Math.min(r, h / 2, w / 2))
            c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r)
            c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath()
        }
        function css(c, a) { return "rgba(" + (c[0] | 0) + "," + (c[1] | 0) + "," + (c[2] | 0) + "," + Math.max(0, Math.min(1, a)) + ")" }
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var o = canvas.o
            if (!o) return
            ctx.translate(1, 1)
            var w = o.w, h = o.h, cy = o.cy, ex = o.ex
            rr(ctx, 0, 0, w, h, o.rad); ctx.fillStyle = "rgba(8,8,8,0.97)"; ctx.fill()
            ctx.save(); rr(ctx, 0, 0, w, h, o.rad); ctx.clip()
            if (o.washX >= 0) {
                ctx.fillStyle = css(o.glowCol, o.washA); ctx.fillRect(0, 0, Math.max(0, o.washX - 18), h)
                var lg = ctx.createLinearGradient(Math.max(0, o.washX - 18), 0, o.washX, 0)
                lg.addColorStop(0, css(o.glowCol, o.washA)); lg.addColorStop(1, css(o.glowCol, 0))
                ctx.fillStyle = lg; ctx.fillRect(Math.max(0, o.washX - 18), 0, Math.min(18, o.washX), h)
            }
            if (o.progress > 0) { ctx.fillStyle = css(o.glowCol, 0.07); ctx.fillRect(0, 0, w * o.progress, h) }
            if (o.glowA > 0.004) {
                var g = ctx.createRadialGradient(ex, cy, 0, ex, cy, o.glowR * 2)
                for (var k = 0; k <= 8; k++) { var d = k / 8 * 2; g.addColorStop(k / 8, css(o.glowCol, o.glowA * Math.exp(-d * d))) }
                ctx.fillStyle = g; ctx.fillRect(0, 0, w, h)
            }
            if (o.rings.length) {   // software: one ring at most
                var r = o.rings[0]
                ctx.beginPath(); ctx.arc(r.x, r.y, r.r, 0, Math.PI * 2); ctx.strokeStyle = css(o.glowCol, r.a); ctx.lineWidth = r.w; ctx.stroke()
            }
            if (o.thrA > 0) {
                var X = o.tx, Y = o.ty, A = o.ta, dx = o.tdx
                ctx.lineWidth = o.thrW; ctx.lineJoin = "round"; ctx.lineCap = "round"
                // stepped alpha: three polylines (the cursive tail fades)
                for (var band = 0; band < 3; band++) {
                    var lo = band / 3, hi = (band + 1) / 3, open = false
                    ctx.beginPath()
                    for (var i = 0; i < X.length - 1; i++) {
                        var al = Math.min(A[i], A[i + 1])
                        if (al > lo + 0.01 && al <= hi + 0.01) {
                            if (!open) { ctx.moveTo(X[i] + dx, Y[i]); open = true }
                            ctx.lineTo(X[i + 1] + dx, Y[i + 1])
                        } else open = false
                    }
                    ctx.strokeStyle = css(o.thrCol, o.thrA * hi); ctx.stroke()
                }
            }
            ctx.globalAlpha = o.emberA
            ctx.beginPath(); ctx.arc(ex, cy, o.er, 0, Math.PI * 2); ctx.fillStyle = css(o.emberCol, 1); ctx.fill()
            if (o.core > 0.02) {
                var cg = ctx.createRadialGradient(ex, cy, 0, ex, cy, o.er * 0.75)
                cg.addColorStop(0, css(o.coreCol, o.core)); cg.addColorStop(1, css(o.coreCol, 0))
                ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(ex, cy, o.er, 0, Math.PI * 2); ctx.fill()
            }
            ctx.globalAlpha = 1
            ctx.restore()
            rr(ctx, 0.5, 0.5, w - 1, h - 1, o.rad - 0.5); ctx.strokeStyle = css(o.rimCol, o.rimA); ctx.lineWidth = 1; ctx.stroke()
        }
    }
}
