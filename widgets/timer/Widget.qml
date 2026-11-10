import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// timer: a ring that drains with the ember riding its head, ticking digits,
// and +1 min / Pause / Cancel. Ticks once a second (nothing per frame); holds
// the card open until it ends, then says so ("timer_done").
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 380
    readonly property var d: data_ || ({})
    property real total: Math.max(1, d.seconds || 60)
    property real endsAt: 0
    property real remain: total
    property bool paused: false
    property bool cancelled: false
    property bool holds: remain > 0 && !cancelled
    implicitHeight: 104
    Component.onCompleted: { endsAt = Date.now() + total * 1000; remain = total }

    function fmt(s) {
        s = Math.max(0, Math.round(s))
        var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60
        return (h ? h + ":" + ("0" + m).slice(-2) : m) + ":" + ("0" + r).slice(-2)
    }
    function endsText() { var e = new Date(endsAt); return "ends at " + ("0" + e.getHours()).slice(-2) + ":" + ("0" + e.getMinutes()).slice(-2) }
    Timer {
        interval: 1000; repeat: true; running: w.remain > 0 && !w.paused && !w.cancelled
        onTriggered: {
            w.remain = Math.max(0, Math.round((w.endsAt - Date.now()) / 1000))
            if (w.remain <= 0) w.action("timer_done", w.d.label || "")
        }
    }
    readonly property real frac: cancelled ? 0 : remain / total
    property real shownFrac: frac
    // glides only for a real jump (+1 min, a short timer's tick); the regular 1 s tick of a long
    // timer moves the ring about a pixel, so it just steps (no 60 fps glide every second at rest)
    Behavior on shownFrac { enabled: !w.reduced && Math.abs(w.frac - w.shownFrac) > 0.05
        NumberAnimation { duration: 450; easing.type: Easing.OutCubic } }

    Rise {
        i: 0; reduced: w.reduced
        Item {
            width: 96; height: 96
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath { strokeColor: Qt.rgba(1, 1, 1, 0.08); strokeWidth: 5; fillColor: "transparent"
                    PathAngleArc { centerX: 48; centerY: 48; radiusX: 42; radiusY: 42; startAngle: 0; sweepAngle: 360 } }
                ShapePath {
                    strokeWidth: 5; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                    strokeColor: w.remain > 0 ? "#D97757" : "#6CCB7A"
                    PathAngleArc { centerX: 48; centerY: 48; radiusX: 42; radiusY: 42; startAngle: -90
                        sweepAngle: w.remain > 0 ? 360 * Math.max(0.002, w.shownFrac) : 360 }
                }
            }
            // the ember rides the head of the ring
            Rectangle {
                visible: w.remain > 0 && !w.cancelled
                readonly property real a: (-90 + 360 * w.shownFrac) * Math.PI / 180
                x: 48 + 42 * Math.cos(a) - 5; y: 48 + 42 * Math.sin(a) - 5; width: 10; height: 10; radius: 5
                color: "#EC9A78"; border.width: 2; border.color: "#D97757"
            }
            Text {
                anchors.centerIn: parent
                text: w.cancelled ? "–" : w.remain > 0 ? w.fmt(w.remain) : "done"
                color: w.remain > 0 ? "#F2F2F2" : "#6CCB7A"
                font.family: "Segoe UI Variable Text"; font.pointSize: 15; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
            }
        }
    }
    Rise {
        i: 1; reduced: w.reduced
        x: 116; y: 14
        Column {
            spacing: 2
            Text { text: w.d.label || "Timer"; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 11 }
            Text { text: w.cancelled ? "cancelled" : w.remain <= 0 ? "time's up" : w.paused ? "paused" : w.endsText()
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5 }
            Item { width: 1; height: 8 }
            Row {
                visible: w.remain > 0 && !w.cancelled
                spacing: 6
                Pill { label: "+1 min"; w: 58; onTapped: { w.total += 60; w.endsAt += 60000; w.remain += 60 } }
                Pill { label: w.paused ? "Resume" : "Pause"; w: 62
                    onTapped: { if (w.paused) { w.endsAt = Date.now() + w.remain * 1000; w.paused = false } else w.paused = true } }
                Pill { label: "Cancel"; w: 58; onTapped: { w.cancelled = true; w.action("timer_cancel", w.d.label || "") } }
            }
        }
    }
}
