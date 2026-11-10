import QtQuick
import QtQuick.Shapes

// Small plain-stroke glyphs (drawn for a 24 px box, centred), as Qt Quick
// Shapes: scene-graph geometry on the GPU, rebuilt only when a property
// changes. Effect icons, built-in tool glyphs and weather icons. Brightness
// "rays" are a ring of dots, so nothing reads as an asterisk or a logo
// (island-motion.md section 1).
Item {
    id: g
    property string name: "gear"
    property color color: "#D97757"
    property color color2: "#F2F2F2"
    property real u: 1            // entry progress (wifi bars, app zoom)
    property real level: 60       // speaker / sun level (0..100)
    property real slash: 0        // speaker slash trim (mute)
    property var on: undefined    // wifi / bluetooth on-off (undefined: plain)
    property real s: 1            // scale (1 = a 24 px box)
    width: 24 * s; height: 24 * s

    // ---- geometry helpers (centred coordinates, -12..12) -----------------------------
    function f(v) { return Math.round(v * 100) / 100 }
    function arc(cx, cy, r, a0, a1) {           // degrees, clockwise on screen
        var x0 = cx + r * Math.cos(a0 * Math.PI / 180), y0 = cy + r * Math.sin(a0 * Math.PI / 180)
        var x1 = cx + r * Math.cos(a1 * Math.PI / 180), y1 = cy + r * Math.sin(a1 * Math.PI / 180)
        var large = Math.abs(a1 - a0) > 180 ? 1 : 0
        return "M" + f(x0) + " " + f(y0) + " A" + r + " " + r + " 0 " + large + " 1 " + f(x1) + " " + f(y1)
    }
    function circle(cx, cy, r) {
        return "M" + f(cx - r) + " " + cy + " A" + r + " " + r + " 0 1 1 " + f(cx + r) + " " + cy +
               " A" + r + " " + r + " 0 1 1 " + f(cx - r) + " " + cy + " Z"
    }
    function rrect(x, y, w, h, r) {
        return "M" + (x + r) + " " + y + " H" + (x + w - r) + " A" + r + " " + r + " 0 0 1 " + (x + w) + " " + (y + r) +
               " V" + (y + h - r) + " A" + r + " " + r + " 0 0 1 " + (x + w - r) + " " + (y + h) +
               " H" + (x + r) + " A" + r + " " + r + " 0 0 1 " + x + " " + (y + h - r) +
               " V" + (y + r) + " A" + r + " " + r + " 0 0 1 " + (x + r) + " " + y + " Z"
    }
    function cloudPath(dx, dy) {
        return "M" + (-3 + dx) + " " + (5 + dy) + " A4 4 0 0 1 " + (-3.6 + dx) + " " + (-2.9 + dy) +
               " A5 5 0 0 1 " + (5.3 + dx) + " " + (-1.6 + dy) + " A3.4 3.4 0 0 1 " + (5 + dx) + " " + (5 + dy) + " Z"
    }
    function crescent(cx, cy, r, ox, oy, r2) {
        // the part of circle (cx,cy,r) outside circle (cx+ox,cy+oy,r2)
        var d = Math.sqrt(ox * ox + oy * oy), a = (r * r - r2 * r2 + d * d) / (2 * d), h = Math.sqrt(Math.max(0, r * r - a * a))
        var px = cx + a * ox / d, py = cy + a * oy / d
        var x1 = px + h * oy / d, y1 = py - h * ox / d, x2 = px - h * oy / d, y2 = py + h * ox / d
        return "M" + f(x1) + " " + f(y1) + " A" + r + " " + r + " 0 1 0 " + f(x2) + " " + f(y2) +
               " A" + r2 + " " + r2 + " 0 0 1 " + f(x1) + " " + f(y1) + " Z"
    }
    function dots(cx, cy, ring, n, r) {
        var p = ""
        for (var i = 0; i < n; i++) p += circle(cx + ring * Math.cos(i * 2 * Math.PI / n), cy + ring * Math.sin(i * 2 * Math.PI / n), r) + " "
        return p
    }

    // ---- per-glyph paths: {fill, stroke, fill2, stroke2 (faint), slash} ---------------
    readonly property var p: {
        var n = name, o = { fill: "", stroke: "", fill2: "", stroke2: "", slash: "" }, i, r
        if (n === "speaker") {
            o.fill = "M-8 -3 L-4 -3 L1 -7 L1 7 L-4 3 L-8 3 Z"
            var waves = level <= 0 ? 0 : level < 34 ? 1 : level < 67 ? 2 : 3
            for (i = 0; i < 3; i++) { r = 4 + 3.2 * i; var a = arc(1, 0, r, -45, 45) + " "; if (i < waves) o.stroke += a; else o.stroke2 += a }
            if (slash > 0) o.slash = "M-9 -8 L" + f(-9 + 19 * slash) + " " + f(-8 + 16 * slash)
        } else if (n === "sun") {
            var lv = Math.max(0, Math.min(1, level / 100))
            o.fill = circle(0, 0, 4.2) + " " + dots(0, 0, 6.5 + 3.5 * lv, 8, 0.8 + 0.9 * lv)
        } else if (n === "moon" || n === "night") {
            o.fill = crescent(0, 0, 7, 4.2, -3.2, 6)
        } else if (n === "image") {
            o.stroke = rrect(-9, -7, 18, 14, 3) + " M-6 4 L-2 -1 L1 2 L3 0 L6 4"; o.fill = circle(4, -3, 1.4)
        } else if (n === "wifi") {
            var fl = on === undefined ? 3 : (on ? 3 * u : 3 * (1 - u))
            for (i = 0; i < 3; i++) { r = 3.5 + 3.5 * i; var w = arc(0, 5, r, -135, -45) + " "; if (fl - i >= 0.5) o.stroke += w; else o.stroke2 += w }
            o.fill = circle(0, 5, 1.5)
        } else if (n === "bluetooth") {
            var bt = "M-4 -4 L4 4 L0 8 L0 -8 L4 -4 L-4 4"; if (on === false) o.stroke2 = bt; else o.stroke = bt
        } else if (n === "bolt") {
            o.fill = "M2 -8 L-5 1 L0 1 L-2 8 L5 -1 L0 -1 Z"
        } else if (n === "window") {
            o.stroke = rrect(-9, -7, 18, 14, 2.5) + " M-9 -3 L9 -3"
        } else if (n === "app") {
            var sc = 0.6 + 0.4 * u
            for (var px = -1; px <= 1; px += 2) for (var py = -1; py <= 1; py += 2) o.stroke += rrect(f(4.5 * px * sc - 3), f(4.5 * py * sc - 3), 6, 6, 1.5) + " "
        } else if (n === "clipboard") {
            o.stroke = rrect(-6, -7, 12, 15, 2) + " " + rrect(-3, -9, 6, 4, 1)
        } else if (n === "lock") {
            o.stroke = rrect(-6, -1, 12, 9, 2) + " " + arc(0, -3, 4, 180, 360)
        } else if (n === "power") {
            o.stroke = arc(0, 1, 7, -58, 238) + " M0 -8 L0 -1"
        } else if (n === "camera") {
            o.stroke = rrect(-9, -5, 18, 12, 2.5) + " M-3 -5 L-3 -8 L3 -8 L3 -5 " + circle(0, 1, 3.5)
        } else if (n === "music") {
            o.fill = "M-3 2.8 A2.6 2.2 0 1 0 -3 7.2 A2.6 2.2 0 1 0 -3 2.8 Z"; o.stroke = "M-0.6 5 L-0.6 -7 L5 -5"
        } else if (n === "terminal") {
            o.stroke = rrect(-9, -7, 18, 14, 2.5) + " M-5 -2 L-2 1 L-5 4 M0 4 L5 4"
        } else if (n === "file") {
            o.stroke = "M-6 -8 L2 -8 L6 -4 L6 8 L-6 8 Z M2 -8 L2 -4 L6 -4"
        } else if (n === "pencil") {
            o.stroke = "M-7 7 L-6 3 L4 -7 L7 -4 L-3 6 Z"
        } else if (n === "search") {
            o.stroke = circle(-2, -2, 5) + " M2 2 L7 7"
        } else if (n === "globe") {
            o.stroke = circle(0, 0, 7.5) + " M0 -7.5 A3.2 7.5 0 0 0 0 7.5 A3.2 7.5 0 0 0 0 -7.5 M-7.5 0 L7.5 0"
        } else if (n === "plug") {
            o.stroke = "M-3 -8 L-3 -4 M3 -8 L3 -4 " + rrect(-6, -4, 12, 6, 2) + " M0 2 L0 8"
        } else if (n === "calendar") {
            o.stroke = rrect(-8, -6, 16, 14, 2.5) + " M-8 -2 L8 -2 M-4 -8 L-4 -4 M4 -8 L4 -4"
        } else if (n === "timer") {
            o.stroke = circle(0, 1.5, 6.5) + " M0 1.5 L0 -2 M-2.5 -7.5 L2.5 -7.5"
        } else if (n === "cap") {                                   // M5b timetable: a mortarboard and its tassel
            o.stroke = "M-10 -2.5 L0 -6.5 L10 -2.5 L0 1.5 Z M-5.5 -0.4 L-5.5 4 A5.5 2.6 0 0 0 5.5 4 L5.5 -0.4 M10 -2.5 L10 3.5"
        } else if (n === "hourglass") {                             // M5b deadlines: an hourglass with sand below
            o.stroke = "M-6 -8 L6 -8 M-6 8 L6 8 M-4.5 -8 C-4.5 -3 4.5 -2 4.5 8 M4.5 -8 C4.5 -3 -4.5 -2 -4.5 8"
            o.fill = "M-2.8 6.6 L2.8 6.6 L0 3.6 Z"
        } else if (n === "chip") {                                  // M5b system: a processor with its pins
            o.stroke = rrect(-5.5, -5.5, 11, 11, 2) + " M-2.5 -5.5 L-2.5 -8.5 M2.5 -5.5 L2.5 -8.5 M-2.5 5.5 L-2.5 8.5" +
                       " M2.5 5.5 L2.5 8.5 M-5.5 -2.5 L-8.5 -2.5 M-5.5 2.5 L-8.5 2.5 M5.5 -2.5 L8.5 -2.5 M5.5 2.5 L8.5 2.5"
            o.fill = rrect(-2, -2, 4, 4, 1)
        } else if (n === "bell") {                                  // M5b reminders
            o.stroke = "M-6 4 L-6 -1 A6 6 0 0 1 6 -1 L6 4 L7.5 5.5 L-7.5 5.5 Z M-2 8 A2 2 0 0 0 2 8"
            o.fill = circle(0, -7.6, 1)
        } else if (n === "pin") {                                   // M5b directions: a map pin
            o.stroke = "M0 9 C-1.5 4.5 -6.5 1.5 -6.5 -3 A6.5 6.5 0 1 1 6.5 -3 C6.5 1.5 1.5 4.5 0 9 Z"
            o.fill = circle(0, -3, 2.2)
        } else if (n === "news") {                                  // M5b news: a folded paper with lines
            o.stroke = "M-6 -7 L8 -7 L8 6 A2 2 0 0 1 6 8 L-7 8 A2 2 0 0 1 -9 6 L-9 -2 L-6 -2 M-6 -7 L-6 6 A2 2 0 0 1 -8 8" +
                       " M-2.5 -0.5 L4.5 -0.5 M-2.5 3 L4.5 3"
            o.fill = rrect(-2.5, -4.6, 7, 2.6, 0.6)
        } else if (n === "mail") {                                  // M5b email: an envelope
            o.stroke = rrect(-9, -6.5, 18, 13, 2.5) + " M-8 -5 L0 1.5 L8 -5"
        } else if (n === "link") {
            o.stroke = rrect(-8, -3, 9, 6, 3) + " " + rrect(-1, -3, 9, 6, 3)
        } else if (n === "code") {
            o.stroke = "M-4 -5 L-8 0 L-4 5 M4 -5 L8 0 L4 5"
        } else if (n === "plane") {
            o.stroke = "M-8 -1 L8 -7 L3 8 L0 2 Z M0 2 L8 -7"
        } else if (n === "check") {
            o.stroke = "M-6 0 L-2 4 L6 -5"
        } else if (n === "calc") {                              // M5b convert: a calculator, its display and keys
            o.stroke = rrect(-7, -9, 14, 18, 2.5) + " M-4 -5 L4 -5"
            o.fill = circle(-3.5, 0, 1.1) + " " + circle(0, 0, 1.1) + " " + circle(3.5, 0, 1.1) + " " +
                     circle(-3.5, 4.5, 1.1) + " " + circle(0, 4.5, 1.1) + " " + circle(3.5, 4.5, 1.1)
        } else if (n === "book") {                              // M5b define: an open book
            o.stroke = "M0 -5 Q-4 -8 -9 -6.5 L-9 6.5 Q-4 5 0 7.5 Q4 5 9 6.5 L9 -6.5 Q4 -8 0 -5 Z M0 -5 L0 7.5"
        } else if (n === "translate") {                         // M5b translate: two speech bubbles
            o.stroke = rrect(-9, -8, 11, 8, 2.5) + " M-6 0 L-7 3 L-3 0"
            o.fill2 = ""
            o.stroke += " " + rrect(-1, -2, 10, 8, 2.5) + " M5 6 L6 9 L2 6"
        } else if (n === "cloud" || n === "rain" || n === "storm" || n === "snow" || n === "fog") {
            o.stroke = cloudPath(0, -1)
            if (n === "rain") o.stroke += " M-3.5 7 L-4.5 9.5 M0 7 L-1 9.5 M3.5 7 L2.5 9.5"
            if (n === "storm") o.stroke += " M1 6 L-1.5 9 L1.5 9 L-1 11.5"
            if (n === "snow") o.fill = circle(-3.5, 8, 0.9) + " " + circle(0, 9, 0.9) + " " + circle(3.5, 8, 0.9)
            if (n === "fog") o.stroke += " M-7 7.5 L7 7.5 M-5 10 L6 10"
        } else if (n === "partly") {
            o.fill2 = circle(-3, -4, 4)                       // the sun behind (color2 is the cloud)
            o.fill = cloudPath(1, 1)
        } else if (n === "wind") {
            o.stroke = "M-8 -3 L3 -3 A2.5 2.5 0 1 0 0.5 -5.5 M-8 1 L6 1 A2.5 2.5 0 1 1 3.5 3.5 M-8 5 L0 5"
        } else {                                                // gear: a ring and a hub
            o.stroke = circle(0, 0, 7); o.fill = circle(0, 0, 2.5)
        }
        return o
    }
    readonly property bool partly: name === "partly"

    Shape {
        x: 12 * g.s; y: 12 * g.s
        scale: g.s
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath { fillColor: g.partly ? "#EC9A78" : g.color; strokeColor: "transparent"; PathSvg { path: g.p.fill2 } }
        ShapePath { fillColor: g.partly ? g.color2 : g.color; strokeColor: "transparent"; PathSvg { path: g.p.fill } }
        ShapePath { fillColor: "transparent"; strokeColor: Qt.rgba(g.color.r, g.color.g, g.color.b, 0.2); strokeWidth: 1.5
            capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin; PathSvg { path: g.p.stroke2 } }
        ShapePath { fillColor: "transparent"; strokeColor: g.color; strokeWidth: 1.6
            capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin; PathSvg { path: g.p.stroke } }
        ShapePath { fillColor: "transparent"; strokeColor: "#080808"; strokeWidth: 3.4; capStyle: ShapePath.RoundCap; PathSvg { path: g.p.slash } }
        ShapePath { fillColor: "transparent"; strokeColor: "#F2F2F2"; strokeWidth: 1.6; capStyle: ShapePath.RoundCap; PathSvg { path: g.p.slash } }
    }
}
