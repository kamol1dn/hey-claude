import QtQuick

// The mic (interrupt and talk) and stop buttons on thinking / speaking:
// r 12 circles, plain UI glyphs (island-motion.md "controls"). The mic glyph is
// painted once into a small Canvas (a Shape here re-rendered every frame).
Item {
    id: b
    property string kind: "mic"
    property real cx: 0
    property real cy: 0
    property real hover: 0
    property real press: 0
    x: cx - 12; y: cy - 12; width: 24; height: 24
    scale: (1 - 0.1 * press)
    Rectangle { anchors.fill: parent; radius: 12; color: Qt.rgba(1, 1, 1, 0.08 + 0.10 * b.hover + 0.12 * b.press) }
    Rectangle { visible: b.kind === "stop"; x: 7; y: 7; width: 10; height: 10; radius: 2; color: "#F2F2F2" }
    Canvas {
        visible: b.kind === "mic"
        anchors.fill: parent
        renderStrategy: Canvas.Immediate
        onPaint: {
            var c = getContext("2d")
            c.reset()
            c.translate(12, 12)
            c.fillStyle = "#F2F2F2"; c.strokeStyle = "#F2F2F2"; c.lineWidth = 1.6; c.lineCap = "round"
            c.beginPath(); c.roundedRect(-3, -7, 6, 10, 3, 3); c.fill()
            c.beginPath(); c.ellipse(-6, -4, 12, 9)              // the cradle: the lower part of an ellipse
            c.save(); c.beginPath(); c.rect(-8, 2.0, 16, 8); c.clip()
            c.beginPath(); c.ellipse(-6, -4, 12, 9); c.stroke(); c.restore()
            c.beginPath(); c.moveTo(0, 5); c.lineTo(0, 8); c.stroke()
        }
    }
}
