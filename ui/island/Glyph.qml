import QtQuick

// Small plain-stroke glyphs (~18 px), the QML port of island_effects.icon():
// effect icons, built-in tool glyphs and weather icons. Repainted only when a
// property changes. Brightness "rays" are a ring of dots, so nothing reads as
// an asterisk or a logo (island-motion.md section 1).
Canvas {
    id: g
    property string name: "gear"
    property color color: "#D97757"
    property real u: 1            // entry progress
    property real level: 60       // speaker / sun level (0..100)
    property real slash: 0        // speaker slash trim (mute)
    property var on: undefined    // wifi / bluetooth on-off (undefined: plain)
    property real s: 1            // scale (glyph drawn for an 18 px box)
    width: 24 * s; height: 24 * s
    renderStrategy: Canvas.Immediate
    onNameChanged: requestPaint(); onColorChanged: requestPaint(); onUChanged: requestPaint()
    onLevelChanged: requestPaint(); onSlashChanged: requestPaint(); onOnChanged: requestPaint(); onSChanged: requestPaint()

    function css(c, a) { return Qt.rgba(c.r, c.g, c.b, Math.max(0, Math.min(1, a))) }
    function rr(c, x, y, w, h, r) { c.beginPath(); c.roundedRect(x, y, w, h, r, r) }

    onPaint: {
        var c = getContext("2d")
        c.reset()
        c.translate(width / 2, height / 2)
        c.scale(s, s)
        c.lineWidth = 1.6; c.lineCap = "round"; c.lineJoin = "round"
        c.strokeStyle = color; c.fillStyle = color
        var n = name, i, r
        if (n === "speaker") {
            c.beginPath(); c.moveTo(-8, -3); c.lineTo(-4, -3); c.lineTo(1, -7); c.lineTo(1, 7); c.lineTo(-4, 3); c.lineTo(-8, 3); c.closePath(); c.fill()
            var waves = level <= 0 ? 0 : level < 34 ? 1 : level < 67 ? 2 : 3
            for (i = 0; i < 3; i++) {
                r = 4 + 3.2 * i
                c.strokeStyle = css(color, i < waves ? 1 : 0.18); c.lineWidth = 1.5
                c.beginPath(); c.arc(1, 0, r, -Math.PI / 4, Math.PI / 4); c.stroke()
            }
            if (slash > 0) {
                c.lineWidth = 3.4; c.strokeStyle = "#080808"
                c.beginPath(); c.moveTo(-9, -8); c.lineTo(-9 + 19 * slash, -8 + 16 * slash); c.stroke()
                c.lineWidth = 1.6; c.strokeStyle = "#F2F2F2"
                c.beginPath(); c.moveTo(-9, -8); c.lineTo(-9 + 19 * slash, -8 + 16 * slash); c.stroke()
            }
        } else if (n === "sun") {
            var lv = Math.max(0, Math.min(1, level / 100))
            c.beginPath(); c.arc(0, 0, 4.2, 0, Math.PI * 2); c.fill()
            var ring = 6.5 + 3.5 * lv
            for (i = 0; i < 8; i++) {
                var a = i * Math.PI / 4
                c.fillStyle = css(color, 0.35 + 0.65 * lv)
                c.beginPath(); c.arc(ring * Math.cos(a), ring * Math.sin(a), 0.8 + 0.9 * lv, 0, Math.PI * 2); c.fill()
            }
        } else if (n === "moon" || n === "night") {
            c.beginPath(); c.arc(0, 0, 7, 0, Math.PI * 2); c.fill()
            c.globalCompositeOperation = "destination-out"
            c.beginPath(); c.arc(4.2, -3.2, 6, 0, Math.PI * 2); c.fill()
            c.globalCompositeOperation = "source-over"
        } else if (n === "image") {
            rr(c, -9, -7, 18, 14, 3); c.stroke()
            c.beginPath(); c.moveTo(-6, 4); c.lineTo(-2, -1); c.lineTo(1, 2); c.lineTo(3, 0); c.lineTo(6, 4); c.stroke()
            c.beginPath(); c.arc(4, -3, 1.4, 0, Math.PI * 2); c.fill()
        } else if (n === "wifi") {
            var fill = on === undefined ? 3 : (on ? 3 * u : 3 * (1 - u))
            for (i = 0; i < 3; i++) {
                r = 3.5 + 3.5 * i
                c.strokeStyle = css(color, 0.2 + 0.8 * Math.max(0, Math.min(1, fill - i)))
                c.beginPath(); c.arc(0, 5, r, -Math.PI * 3 / 4, -Math.PI / 4); c.stroke()
            }
            c.beginPath(); c.arc(0, 5, 1.5, 0, Math.PI * 2); c.fill()
        } else if (n === "bluetooth") {
            c.strokeStyle = css(color, on === false ? 0.45 : 1)
            c.beginPath(); c.moveTo(-4, -4); c.lineTo(4, 4); c.lineTo(0, 8); c.lineTo(0, -8); c.lineTo(4, -4); c.lineTo(-4, 4); c.stroke()
        } else if (n === "bolt") {
            c.beginPath(); c.moveTo(2, -8); c.lineTo(-5, 1); c.lineTo(0, 1); c.lineTo(-2, 8); c.lineTo(5, -1); c.lineTo(0, -1); c.closePath(); c.fill()
        } else if (n === "window") {
            rr(c, -9, -7, 18, 14, 2.5); c.stroke()
            c.beginPath(); c.moveTo(-9, -3); c.lineTo(9, -3); c.stroke()
        } else if (n === "app") {
            var sc = 0.6 + 0.4 * u
            var d = [-4.5, 4.5]
            for (var p = 0; p < 2; p++) for (var q = 0; q < 2; q++) { rr(c, d[p] * sc - 3, d[q] * sc - 3, 6, 6, 1.5); c.stroke() }
        } else if (n === "clipboard") {
            rr(c, -6, -7, 12, 15, 2); c.stroke(); rr(c, -3, -9, 6, 4, 1); c.stroke()
        } else if (n === "lock") {
            rr(c, -6, -1, 12, 9, 2); c.stroke()
            c.beginPath(); c.arc(0, -3, 4, Math.PI, 0); c.stroke()
        } else if (n === "power") {
            c.beginPath(); c.arc(0, 1, 7, -Math.PI / 2 + 0.55, -Math.PI / 2 - 0.55 + Math.PI * 2); c.stroke()
            c.beginPath(); c.moveTo(0, -8); c.lineTo(0, -1); c.stroke()
        } else if (n === "camera") {
            rr(c, -9, -5, 18, 12, 2.5); c.stroke(); c.strokeRect(-3, -8, 6, 3)
            c.beginPath(); c.arc(0, 1, 3.5, 0, Math.PI * 2); c.stroke()
        } else if (n === "music") {
            c.beginPath(); c.ellipse(-5.6, 2.8, 5.2, 4.4); c.fill()
            c.beginPath(); c.moveTo(-0.6, 5); c.lineTo(-0.6, -7); c.lineTo(5, -5); c.stroke()
        } else if (n === "terminal") {
            rr(c, -9, -7, 18, 14, 2.5); c.stroke()
            c.beginPath(); c.moveTo(-5, -2); c.lineTo(-2, 1); c.lineTo(-5, 4); c.stroke()
            c.beginPath(); c.moveTo(0, 4); c.lineTo(5, 4); c.stroke()
        } else if (n === "file") {
            c.beginPath(); c.moveTo(-6, -8); c.lineTo(2, -8); c.lineTo(6, -4); c.lineTo(6, 8); c.lineTo(-6, 8); c.closePath(); c.stroke()
            c.beginPath(); c.moveTo(2, -8); c.lineTo(2, -4); c.lineTo(6, -4); c.stroke()
        } else if (n === "pencil") {
            c.beginPath(); c.moveTo(-7, 7); c.lineTo(-6, 3); c.lineTo(4, -7); c.lineTo(7, -4); c.lineTo(-3, 6); c.closePath(); c.stroke()
        } else if (n === "search") {
            c.beginPath(); c.arc(-2, -2, 5, 0, Math.PI * 2); c.stroke()
            c.beginPath(); c.moveTo(2, 2); c.lineTo(7, 7); c.stroke()
        } else if (n === "globe") {
            c.beginPath(); c.arc(0, 0, 7.5, 0, Math.PI * 2); c.stroke()
            c.beginPath(); c.ellipse(-3.2, -7.5, 6.4, 15); c.stroke()
            c.beginPath(); c.moveTo(-7.5, 0); c.lineTo(7.5, 0); c.stroke()
        } else if (n === "plug") {
            c.beginPath(); c.moveTo(-3, -8); c.lineTo(-3, -4); c.moveTo(3, -8); c.lineTo(3, -4); c.stroke()
            rr(c, -6, -4, 12, 6, 2); c.stroke()
            c.beginPath(); c.moveTo(0, 2); c.lineTo(0, 8); c.stroke()
        } else if (n === "calendar") {
            rr(c, -8, -6, 16, 14, 2.5); c.stroke()
            c.beginPath(); c.moveTo(-8, -2); c.lineTo(8, -2); c.moveTo(-4, -8); c.lineTo(-4, -4); c.moveTo(4, -8); c.lineTo(4, -4); c.stroke()
        } else if (n === "link") {
            rr(c, -8, -3, 9, 6, 3); c.stroke(); rr(c, -1, -3, 9, 6, 3); c.stroke()
        } else if (n === "code") {
            c.beginPath(); c.moveTo(-4, -5); c.lineTo(-8, 0); c.lineTo(-4, 5); c.moveTo(4, -5); c.lineTo(8, 0); c.lineTo(4, 5); c.stroke()
        } else if (n === "plane") {
            c.beginPath(); c.moveTo(-8, -1); c.lineTo(8, -7); c.lineTo(3, 8); c.lineTo(0, 2); c.closePath(); c.stroke()
            c.beginPath(); c.moveTo(0, 2); c.lineTo(8, -7); c.stroke()
        } else if (n === "check") {
            c.beginPath(); c.moveTo(-6, 0); c.lineTo(-2, 4); c.lineTo(6, -5); c.stroke()
        } else if (n === "cloud" || n === "partly" || n === "rain" || n === "storm" || n === "snow" || n === "fog") {
            if (n === "partly") {
                c.fillStyle = "#EC9A78"; c.beginPath(); c.arc(-3, -4, 4, 0, Math.PI * 2); c.fill(); c.fillStyle = color
            }
            c.beginPath(); c.arc(-3, 1, 4, Math.PI * 0.5, Math.PI * 1.5); c.arc(1, -2, 5, Math.PI, Math.PI * 2)
            c.arc(5, 2, 3, Math.PI * 1.5, Math.PI * 0.5); c.closePath()
            if (n === "partly") { c.fillStyle = "#F2F2F2"; c.fill() } else c.stroke()
            if (n === "rain") { for (i = -1; i <= 1; i++) { c.beginPath(); c.moveTo(i * 3.5, 7); c.lineTo(i * 3.5 - 1, 9.5); c.stroke() } }
            if (n === "snow") { for (i = -1; i <= 1; i++) { c.beginPath(); c.arc(i * 3.5, 8, 0.9, 0, Math.PI * 2); c.fill() } }
            if (n === "storm") { c.beginPath(); c.moveTo(1, 5); c.lineTo(-1.5, 8.5); c.lineTo(1.5, 8.5); c.lineTo(-1, 11); c.stroke() }
            if (n === "fog") { for (i = 0; i < 2; i++) { c.beginPath(); c.moveTo(-7, 7 + 2.5 * i); c.lineTo(7, 7 + 2.5 * i); c.stroke() } }
        } else if (n === "wind") {
            c.beginPath(); c.moveTo(-8, -3); c.lineTo(3, -3); c.arc(3, -5.5, 2.5, Math.PI / 2, -Math.PI / 2, true); c.stroke()
            c.beginPath(); c.moveTo(-8, 1); c.lineTo(6, 1); c.arc(6, 3.5, 2.5, -Math.PI / 2, Math.PI / 2); c.stroke()
            c.beginPath(); c.moveTo(-8, 5); c.lineTo(0, 5); c.stroke()
        } else {                                       // gear: a ring and a hub
            c.beginPath(); c.arc(0, 0, 7, 0, Math.PI * 2); c.stroke()
            c.beginPath(); c.arc(0, 0, 2.5, 0, Math.PI * 2); c.fill()
        }
    }
}
