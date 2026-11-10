import QtQuick
import "../../ui/island"

// deadlines: a countdown list, soonest first. Each row: an urgency bead, the task and
// when it's due, the time left (live), and a thin bar of time left in the window that
// drains from full to where it stands. Red under 24 h (and overdue), terracotta under
// 3 days. Entry: rows slide in from the right, staggered; the urgent ones flash and
// their bead sends out one ring. After that only a 1 s tick changes digits.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    property bool active: true
    signal action(string name, var payload)
    readonly property real minWidth: 420
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var items: (d.items || []).slice(0, 6)
    readonly property int more: d.more || 0
    readonly property real horizon: Math.max(1, d.days || 14) * 86400
    readonly property real rowH: 36
    implicitHeight: items.length ? items.length * rowH + (more ? 20 : 0) : 46

    property real elapsed: 0
    property real openedAt: 0
    Component.onCompleted: openedAt = Date.now()
    Timer {
        interval: 1000; repeat: true; running: w.visible && w.items.length > 0
        onTriggered: w.elapsed = Math.round((Date.now() - w.openedAt) / 1000)
    }

    function two(n) { return ("0" + n).slice(-2) }
    function urg(left) { return left < 0 ? "overdue" : left < 86400 ? "bad" : left < 3 * 86400 ? "soon" : "later" }
    function rel(s) {
        var a = Math.abs(Math.round(s)), out
        if (s >= 0 && a < 3600) return "in " + Math.floor(a / 60) + ":" + two(a % 60)
        var m = Math.max(1, Math.round(a / 60))
        if (m < 60) out = m + " min"
        else if (m < 600) out = Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) + " min" : "")
        else if (m < 2880) out = Math.round(m / 60) + " h"
        else out = Math.round(m / 1440) + " days"
        return s < 0 ? out + " late" : "in " + out
    }
    function tone(u) { return u === "overdue" || u === "bad" ? "#E5646A" : u === "soon" ? "#D97757" : "#8C8C8C" }

    // nothing due
    Rise {
        visible: w.items.length === 0
        i: 0; reduced: w.reduced
        Item {
            width: w.width; height: 46
            Glyph { name: "check"; color: "#6CCB7A"; anchors.verticalCenter: parent.verticalCenter; s: 0.9 }
            Text {
                x: 30; anchors.verticalCenter: parent.verticalCenter
                text: "Nothing due in the " + (w.d.window || "next two weeks")
                color: "#E4E4E4"; font.family: "Segoe UI Variable Text"; font.pointSize: 10
            }
        }
    }

    Repeater {
        model: w.items
        Item {
            id: row
            objectName: "deadlineRow"
            readonly property real remain: modelData.left_s - w.elapsed
            readonly property string u: w.urg(remain)
            readonly property color c: w.tone(u)
            readonly property bool urgent: modelData.urgency === "bad" || modelData.urgency === "overdue"
            readonly property real frac: Math.max(0, Math.min(1, remain / w.horizon))
            y: index * w.rowH; width: w.width; height: w.rowH

            // entry: slide in (staggered), then the bar drains to its value; urgent ones flash once
            property real inU: w.reduced ? 1 : 0
            property real barU: w.reduced ? 1 : 0
            property real flash: 0
            property real ring: 0
            opacity: w.reduced ? fade.o : Math.min(1, inU * 1.3)
            QtObject { id: fade; property real o: w.reduced ? 0 : 1 }
            transform: Translate { x: 26 * (1 - row.inU) }
            SequentialAnimation {
                running: true
                PauseAnimation { duration: w.reduced ? 0 : 130 + index * 70 }
                NumberAnimation { target: fade; property: "o"; to: 1; duration: w.reduced ? 160 : 0 }
                NumberAnimation { target: row; property: "inU"; to: 1; duration: w.reduced ? 0 : 440; easing.type: Easing.OutCubic }
                NumberAnimation { target: row; property: "barU"; to: 1; duration: w.reduced ? 0 : 640; easing.type: Easing.InOutCubic }
            }
            SequentialAnimation {
                running: row.urgent && !w.reduced
                PauseAnimation { duration: 130 + index * 70 + 380 }
                ParallelAnimation {
                    NumberAnimation { target: row; property: "ring"; from: 0; to: 1; duration: 760; easing.type: Easing.OutCubic }
                    SequentialAnimation {
                        NumberAnimation { target: row; property: "flash"; from: 0; to: 1; duration: 160; easing.type: Easing.OutQuad }
                        NumberAnimation { target: row; property: "flash"; to: 0; duration: 620; easing.type: Easing.InOutQuad }
                    }
                }
            }

            Rectangle {
                visible: row.flash > 0.001
                x: -8; y: 2; width: parent.width + 16; height: parent.height - 4; radius: 9
                color: Qt.rgba(229 / 255, 100 / 255, 106 / 255, 0.14 * row.flash)
            }
            // the bead (and its one ring)
            Item {
                x: 1; y: 10; width: 8; height: 8
                Rectangle {
                    visible: row.ring > 0 && row.ring < 1
                    anchors.centerIn: parent
                    width: 8 + 14 * row.ring; height: width; radius: width / 2
                    color: "transparent"; border.width: 1.5
                    border.color: Qt.rgba(row.c.r, row.c.g, row.c.b, 0.7 * (1 - row.ring))
                }
                Rectangle { anchors.fill: parent; radius: 4; color: row.c
                    scale: 1 + 0.35 * row.flash }
            }
            Text {
                x: 18; y: 3; width: parent.width - 18 - relT.implicitWidth - 12
                elide: Text.ElideRight
                text: modelData.title
                color: row.u === "overdue" ? "#F0B4B6" : "#F2F2F2"
                font.family: "Segoe UI Variable Text"; font.pointSize: 10
            }
            Text {
                x: 18; y: 19; width: parent.width - 18 - relT.implicitWidth - 12
                visible: !!modelData.when
                elide: Text.ElideRight
                text: (row.u === "overdue" ? "was due " : "") + (modelData.when || "")
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
            }
            Text {
                id: relT
                objectName: "deadlineLeft"
                anchors.right: parent.right; y: 4
                text: w.rel(row.remain)
                color: row.u === "later" ? "#CFCFCF" : row.c
                font.family: "Segoe UI Variable Text"; font.pointSize: 10; font.weight: Font.DemiBold
                font.features: { "tnum": 1 }
            }
            // time left in the window
            Rectangle {
                anchors.right: parent.right; y: 24; width: 74; height: 3; radius: 1.5
                color: row.u === "overdue" ? Qt.rgba(229 / 255, 100 / 255, 106 / 255, 0.22) : Qt.rgba(1, 1, 1, 0.07)
                Rectangle {
                    height: parent.height; radius: 1.5
                    width: parent.width * (1 - row.barU * (1 - row.frac))
                    color: row.u === "later" ? Qt.rgba(1, 1, 1, 0.42) : row.c
                }
            }
            Rectangle {
                visible: index < w.items.length - 1
                x: 18; y: parent.height - 1; width: parent.width - 18; height: 1; color: Qt.rgba(1, 1, 1, 0.05)
            }
        }
    }
    Rise {
        visible: w.more > 0
        i: w.items.length; reduced: w.reduced
        x: 18; y: w.items.length * w.rowH + 2
        Text { text: "+" + w.more + " more"; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    }
}
