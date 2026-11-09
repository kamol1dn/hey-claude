import QtQuick
import ".."

// image: a local file or an https URL, at most 180 px high, with a caption.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    readonly property real minWidth: Math.max(300, Math.min(532, img.paintedWidth + 28))
    implicitHeight: (img.status === Image.Ready ? Math.min(180, img.paintedHeight) : 60) + (d.caption ? 20 : 0) + 4

    Rectangle {
        width: w.width; height: img.status === Image.Ready ? Math.min(180, img.paintedHeight) : 60; radius: 8
        color: Qt.rgba(1, 1, 1, 0.05); clip: true
        Image {
            id: img
            anchors.centerIn: parent
            width: w.width; height: 180
            source: w.d.src || ""
            fillMode: Image.PreserveAspectFit
            asynchronous: true; smooth: true; mipmap: true
            sourceSize.height: 360
        }
        Text { visible: img.status !== Image.Ready; anchors.centerIn: parent
            text: img.status === Image.Error ? "couldn't load the image" : "loading…"
            color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: w.action("open", w.d.url || w.d.path || "") }
    }
    Text { visible: !!w.d.caption; y: (img.status === Image.Ready ? Math.min(180, img.paintedHeight) : 60) + 4; width: w.width
        text: w.d.caption || ""; color: "#9A9A9A"; elide: Text.ElideRight; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
}
