import QtQuick
import "../../ui/island"

// task_card: a task with its status, a progress bar that grows, and its steps.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property string st: d.status || "todo"
    readonly property color sc: st === "done" ? "#6CCB7A" : st === "failed" ? "#E5646A" : st === "running" ? "#EC9A78" : st === "blocked" ? "#B38A7A" : "#9A9A9A"
    readonly property var steps: (d.steps || []).slice(0, 4)
    readonly property bool hasProg: d.progress !== undefined
    implicitHeight: 40 + (hasProg ? 14 : 0) + steps.length * 20

    Rise {
        i: 0; reduced: w.reduced
        Item {
            width: w.width; height: 36
            Column {
                width: w.width - badge.width - 10
                Text { width: parent.width; text: w.d.title || ""; elide: Text.ElideRight; color: "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 10.5 }
                Text { width: parent.width; elide: Text.ElideRight; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                    text: [w.d.project || "", w.d.detail || "", w.d.due ? "due " + w.d.due : ""].filter(function (s) { return s }).join("  ·  ") }
            }
            Rectangle {
                id: badge
                x: w.width - width; y: 4; height: 18; radius: 9; width: bt.implicitWidth + 16
                color: Qt.rgba(w.sc.r, w.sc.g, w.sc.b, 0.15)
                Text { id: bt; anchors.centerIn: parent; text: w.st; color: w.sc; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
            }
        }
    }
    Rise {
        visible: w.hasProg
        i: 1; reduced: w.reduced
        y: 42
        Rectangle {
            width: w.width; height: 4; radius: 2; color: Qt.rgba(1, 1, 1, 0.08)
            Rectangle {
                id: fill
                height: 4; radius: 2; width: 0
                gradient: Gradient { orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: "#B8664C" } GradientStop { position: 1; color: "#EC9A78" } }
                Component.onCompleted: grow.start()
                SequentialAnimation {
                    id: grow
                    PauseAnimation { duration: w.reduced ? 0 : 260 }
                    NumberAnimation { target: fill; property: "width"; to: w.width * Math.max(0, Math.min(1, w.d.progress || 0))
                        duration: w.reduced ? 0 : 800; easing.type: Easing.OutCubic }
                }
            }
        }
    }
    Repeater {
        model: w.steps
        Rise {
            i: index + 2; reduced: w.reduced
            y: 40 + (w.hasProg ? 14 : 0) + index * 20
            Row {
                spacing: 8
                Item {
                    width: 12; height: 16
                    Glyph { visible: modelData.done === true; anchors.centerIn: parent; name: "check"; color: "#6CCB7A"; s: 0.55 }
                    Rectangle { visible: modelData.done !== true; anchors.centerIn: parent; width: 6; height: 6; radius: 3; color: "#D97757" }
                }
                Text { width: w.width - 20; elide: Text.ElideRight; text: modelData.text; color: modelData.done ? "#9A9A9A" : "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 8.5; height: 16; verticalAlignment: Text.AlignVCenter }
            }
        }
    }
}
