import QtQuick

// The icon of a service in use: an image (declared / bundled / favicon), a
// built-in tool glyph, or a plug with the service's initials.
Item {
    id: si
    property string url: ""
    property string glyph: ""
    property string initials: ""
    property real size: 16
    width: size; height: size
    Image {
        anchors.fill: parent
        visible: si.url !== "" && status === Image.Ready
        source: si.url
        sourceSize: Qt.size(si.size * 4, si.size * 4)       // rasterised big enough for island.scale up to 2
        smooth: true; asynchronous: true; fillMode: Image.PreserveAspectFit
    }
    Glyph {
        visible: si.url === "" && si.glyph !== ""
        anchors.centerIn: parent
        name: si.glyph; color: "#F2F2F2"; s: si.size / 22
    }
    Rectangle {
        visible: si.url === "" && si.glyph === ""
        anchors.fill: parent; radius: si.size / 2
        color: Qt.rgba(1, 1, 1, 0.12)
        Text {
            anchors.centerIn: parent
            text: si.initials || "?"
            color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pixelSize: Math.max(6, Math.round(si.size * 0.46)); font.bold: true
        }
    }
}
