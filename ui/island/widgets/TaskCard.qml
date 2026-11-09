import QtQuick
import ".."

// task_card: one task or to-do with its status.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 360
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    readonly property string st: d.status || "todo"
    readonly property color sc: st === "done" ? "#6CCB7A" : st === "failed" ? "#E5646A" : st === "running" ? "#D97757" : st === "blocked" ? "#B38A7A" : "#9A9A9A"
    implicitHeight: 44

    Rectangle { x: 4; y: 13; width: 14; height: 14; radius: 7; color: "transparent"; border.width: 1.5; border.color: w.sc
        Glyph { visible: w.st === "done"; anchors.centerIn: parent; name: "check"; color: w.sc; s: 0.5 } }
    Column {
        x: 28; y: 3; width: w.width - 28 - badge.width - 8
        Text { width: parent.width; text: w.d.title || ""; color: "#F2F2F2"; elide: Text.ElideRight; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text { width: parent.width; elide: Text.ElideRight; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
            text: [w.d.project || "", w.d.detail || "", w.d.due ? "due " + w.d.due : ""].filter(function (s) { return s }).join("  ·  ") }
    }
    Rectangle {
        id: badge
        x: w.width - width; y: 11; height: 18; radius: 9; width: bt.implicitWidth + 14
        color: Qt.rgba(w.sc.r, w.sc.g, w.sc.b, 0.16)
        Text { id: bt; anchors.centerIn: parent; text: w.st; color: w.sc; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    }
}
