import QtQuick
import ".."

// code: a short snippet (at most 12 lines), monospace, with a Copy button.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 460
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var lines: String(d.code || "").replace(/\t/g, "    ").split("\n").slice(0, 12)
    property bool copied: false
    implicitHeight: 24 + code.implicitHeight + 10

    Text { y: 2; height: 18; verticalAlignment: Text.AlignVCenter; width: w.width - 80; elide: Text.ElideRight
        text: [w.d.title || "", w.d.language || ""].filter(function (s) { return s }).join("  ·  ") || "code"
        color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    Pill { x: w.width - width; y: 0; w: 58; label: w.copied ? "Copied" : "Copy"; accent: true
        onTapped: { w.action("copy", w.d.code || ""); w.copied = true; back.restart() } }
    Timer { id: back; interval: 1600; onTriggered: w.copied = false }
    Rectangle {
        y: 24; width: w.width; height: code.implicitHeight + 10; radius: 8
        color: Qt.rgba(1, 1, 1, 0.05)
        Text {
            id: code
            x: 10; y: 5; width: parent.width - 20
            text: w.lines.join("\n")
            color: "#E8E2DC"; font.family: "Cascadia Mono"; font.pointSize: 8.5
            elide: Text.ElideRight; textFormat: Text.PlainText
        }
    }
}
