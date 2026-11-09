import QtQuick
import QtQuick.Shapes
import ".."

// timer: a ring that drains and mm:ss. It ticks once a second (nothing per
// frame), holds the card open until it ends, then shows a check and tells the
// island ("timer_done"), which says so.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 340
    readonly property var d: data_ || ({})
    readonly property real total: Math.max(1, d.seconds || 60)
    property real endsAt: 0
    property real remain: total
    property bool holds: remain > 0
    implicitHeight: 52
    Component.onCompleted: { endsAt = Date.now() + total * 1000; remain = total }
    Timer {
        interval: 1000; repeat: true; running: w.remain > 0
        onTriggered: {
            w.remain = Math.max(0, Math.round((w.endsAt - Date.now()) / 1000))
            if (w.remain <= 0) w.action("timer_done", w.d.label || "")
        }
    }
    function fmt(s) { var m = Math.floor(s / 60), r = Math.floor(s % 60); return (m >= 60 ? Math.floor(m / 60) + ":" + ("0" + (m % 60)).slice(-2) : m) + ":" + ("0" + r).slice(-2) }

    Shape {
        x: 4; y: 6; width: 40; height: 40
        preferredRendererType: Shape.CurveRenderer
        ShapePath { strokeColor: Qt.rgba(1, 1, 1, 0.12); strokeWidth: 3; fillColor: "transparent"
            PathAngleArc { centerX: 20; centerY: 20; radiusX: 17; radiusY: 17; startAngle: 0; sweepAngle: 360 } }
        ShapePath { strokeColor: w.remain > 0 ? "#D97757" : "#6CCB7A"; strokeWidth: 3; capStyle: ShapePath.RoundCap; fillColor: "transparent"
            PathAngleArc { centerX: 20; centerY: 20; radiusX: 17; radiusY: 17; startAngle: -90; sweepAngle: w.remain > 0 ? 360 * w.remain / w.total : 360 } }
    }
    Glyph { visible: w.remain <= 0; x: 12; y: 14; name: "check"; color: "#6CCB7A"; s: 1 }
    Text { x: 58; y: 4; text: w.remain > 0 ? w.fmt(w.remain) : "done"; color: "#F2F2F2"
        font.family: "Segoe UI Variable Text"; font.pointSize: 18; font.features: { "tnum": 1 } }
    Text { x: 58; y: 33; width: w.width - 58; text: w.d.label || "timer"; color: "#9A9A9A"; elide: Text.ElideRight
        font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
}
