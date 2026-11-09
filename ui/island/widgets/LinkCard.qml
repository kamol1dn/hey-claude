import QtQuick
import ".."

// link: a web link or a local file; a click opens it (QmlIsland -> widgets.safe_open,
// which never runs a program: those are shown in Explorer).
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 380
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    readonly property bool isFile: !d.url && !!d.path
    readonly property string target: d.url || d.path || ""
    readonly property string host: { var m = String(d.url || "").match(/^[a-z]+:\/\/([^\/?#]+)/i); return m ? m[1] : "" }
    implicitHeight: 44

    Rectangle {
        anchors.fill: parent; radius: 10
        color: Qt.rgba(1, 1, 1, ma.containsMouse ? 0.08 : 0.04)
    }
    Glyph { x: 6; y: 10; name: w.isFile ? "file" : "link"; color: "#D97757" }
    Column {
        x: 38; y: 4; width: w.width - 38 - 30
        Text { width: parent.width; text: w.d.title || w.target; color: "#F2F2F2"; elide: Text.ElideRight; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text { width: parent.width; elide: Text.ElideMiddle; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
            text: w.d.description ? w.d.description + (w.host ? "  ·  " + w.host : "") : (w.host || w.target) }
    }
    Text { x: w.width - 22; y: 12; text: "↗"; color: "#9A9A9A"; font.pointSize: 10 }
    MouseArea {
        id: ma
        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        onClicked: { if (w.target) w.action("open", w.target) }
    }
}
