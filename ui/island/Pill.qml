import QtQuick

// A small rounded button (Undo, Confirm, Cancel, Send...). Never takes focus.
Rectangle {
    id: b
    property string label: ""
    property real w: 60
    property bool primary: false
    property bool accent: false
    property bool enabled_: true
    property string tip: ""
    signal tapped()
    width: w; height: 24; radius: 12
    color: !enabled_ ? Qt.rgba(1, 1, 1, 0.05)
         : primary ? Qt.rgba(217 / 255, 119 / 255, 87 / 255, ma.containsMouse ? 1 : 0.85)
         : accent ? Qt.rgba(217 / 255, 119 / 255, 87 / 255, ma.containsMouse ? 0.30 : 0.16)
         : Qt.rgba(1, 1, 1, ma.containsMouse ? 0.18 : 0.08)
    Text {
        anchors.centerIn: parent
        text: b.label
        color: !b.enabled_ ? "#6A6A6A" : b.primary ? "#080808" : b.accent ? "#EC9A78" : "#F2F2F2"
        font.family: "Segoe UI Variable Text"; font.pointSize: 8
    }
    scale: ma.pressed && enabled_ ? 0.94 : 1
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: b.enabled_ ? Qt.PointingHandCursor : Qt.ForbiddenCursor
        onClicked: if (b.enabled_) b.tapped()
    }
    TipLite { visible: b.tip !== "" && ma.containsMouse; text: b.tip; y: -height - 4; x: Math.min(0, (b.width - width) / 2) }
}
