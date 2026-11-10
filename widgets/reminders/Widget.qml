import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// reminders: the running timers (later reminders and alarms), soonest first, each with a
// ring and a live countdown. Data comes from the timers' own snapshot (heyclaude/intents,
// "what timers do I have"), never from the model.
// Entry: rows rise in one after another and each ring drains from full down to what's
// left, so you see how far along it is. After that one Timer ticks once a second for all
// rows (the digits and the rings step; nothing glides), and stops when every row is done.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 420
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var all: d.items || []
    readonly property var items: all.slice(0, 5)
    readonly property int more: Math.max(0, all.length - items.length) + (d.more || 0)
    readonly property real rowH: 44
    implicitHeight: items.length * rowH + (more > 0 ? 20 : 0)

    readonly property int hiIdx: {
        for (var i = 0; i < items.length; i++) if (items[i].focus) return i
        return 0
    }
    readonly property bool anyFocus: { for (var i = 0; i < items.length; i++) if (items[i].focus) return true; return false }

    // one clock for every row
    readonly property real t0: Date.now()
    property real nowMs: t0
    readonly property real maxLeft: { var m = 0; for (var i = 0; i < items.length; i++) m = Math.max(m, items[i].left_s || 0); return m }
    Timer {
        interval: 1000; repeat: true
        running: w.visible && (w.nowMs - w.t0) / 1000 < w.maxLeft + 1
        onTriggered: w.nowMs = Date.now()
    }
    function remainOf(it) { return Math.max(0, (it.left_s || 0) - (w.nowMs - w.t0) / 1000) }
    function fmt(s) {
        s = Math.max(0, Math.round(s))
        var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60
        return (h ? h + ":" + ("0" + m).slice(-2) : m) + ":" + ("0" + r).slice(-2)
    }
    function span(s) {                          // the timer's length: "4 min", "1 h 30 min"
        s = Math.round(s)
        var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60
        if (s < 60) return s + " s"
        if (!h) return m + " min" + (r ? " " + r + " s" : "")
        return h + " h" + (m ? " " + m + " min" : "")
    }
    function kindName(k) { return k === "alarm" ? "Alarm" : k === "reminder" ? "Reminder" : "Timer" }

    Repeater {
        model: w.items
        Rise {
            id: rowRise
            i: index; reduced: w.reduced
            y: index * w.rowH
            Item {
                id: row
                objectName: "reminderRow"
                width: w.width; height: w.rowH
                readonly property var it: modelData
                readonly property real remain: w.remainOf(it)
                readonly property bool done: remain < 0.5
                readonly property bool hi: index === w.hiIdx
                readonly property real frac: done ? 1 : Math.max(0.004, remain / Math.max(1, it.total_s))
                // entry: the ring drains from full to what's left (k 0 -> 1), then follows the clock
                property real k: w.reduced ? 1 : 0
                readonly property real shown: done ? 1 : 1 - k * (1 - frac)
                Component.onCompleted: if (!w.reduced) drain.start()
                SequentialAnimation {
                    id: drain
                    PauseAnimation { duration: 200 + index * 80 }
                    NumberAnimation { target: row; property: "k"; from: 0; to: 1; duration: 760; easing.type: Easing.InOutCubic }
                }
                readonly property color ringCol: done ? "#6CCB7A" : hi ? "#D97757" : Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.55)
                opacity: w.anyFocus && !hi ? 0.62 : 1

                Item {
                    id: ring
                    width: 34; height: 34; anchors.verticalCenter: parent.verticalCenter
                    Shape {
                        anchors.fill: parent
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath { strokeColor: Qt.rgba(1, 1, 1, 0.08); strokeWidth: 3.5; fillColor: "transparent"
                            PathAngleArc { centerX: 17; centerY: 17; radiusX: 14; radiusY: 14; startAngle: 0; sweepAngle: 360 } }
                        ShapePath {
                            strokeColor: row.ringCol; strokeWidth: 3.5; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                            PathAngleArc { centerX: 17; centerY: 17; radiusX: 14; radiusY: 14; startAngle: -90
                                sweepAngle: 360 * row.shown }
                        }
                    }
                    Rectangle {                         // the ember on the ring's head
                        visible: !row.done
                        readonly property real a: (-90 + 360 * row.shown) * Math.PI / 180
                        x: 17 + 14 * Math.cos(a) - 3.5; y: 17 + 14 * Math.sin(a) - 3.5; width: 7; height: 7; radius: 3.5
                        color: row.hi ? "#EC9A78" : "#B98472"
                    }
                    Glyph {
                        anchors.centerIn: parent; s: 0.56
                        name: row.done ? "check" : row.it.kind === "timer" || !row.it.kind ? "timer" : "bell"
                        color: row.done ? "#6CCB7A" : row.hi ? "#F2F2F2" : "#9A9A9A"
                    }
                }
                Column {
                    x: 48; anchors.verticalCenter: parent.verticalCenter
                    width: w.width - 48 - clock.width - 12
                    spacing: 1
                    Text {
                        width: parent.width; elide: Text.ElideRight
                        text: row.it.label || w.kindName(row.it.kind)
                        color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10
                        font.weight: row.hi ? Font.DemiBold : Font.Normal
                    }
                    Text {
                        width: parent.width; elide: Text.ElideRight
                        text: row.done ? "time's up" : (row.it.ends ? "ends " + row.it.ends + "  ·  " : "") + w.span(row.it.total_s) + " " + w.kindName(row.it.kind).toLowerCase()
                        color: row.done ? "#6CCB7A" : "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                        font.features: { "tnum": 1 }
                    }
                }
                Text {
                    id: clock
                    objectName: "reminderClock"
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    text: row.done ? "done" : w.fmt(row.remain)
                    color: row.done ? "#6CCB7A" : row.hi ? "#F2F2F2" : "#D8D8D8"
                    font.family: "Segoe UI Variable Text"; font.pointSize: row.hi ? 16 : 12.5
                    font.weight: row.hi ? Font.DemiBold : Font.Normal; font.features: { "tnum": 1 }
                }
                Rectangle {
                    visible: index < w.items.length - 1
                    x: 48; y: w.rowH - 1; width: w.width - 48; height: 1; color: Qt.rgba(1, 1, 1, 0.05)
                }
            }
        }
    }
    Rise {
        visible: w.more > 0
        i: w.items.length; reduced: w.reduced
        x: 48; y: w.items.length * w.rowH + 2
        Text { text: "+ " + w.more + " more"; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    }
}
