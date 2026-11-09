import QtQuick

// A tiny tooltip drawn inside the island (no popup window, no focus).
Rectangle {
    property alias text: t.text
    width: t.implicitWidth + 12; height: t.implicitHeight + 6; radius: 6
    color: "#1d1b19"; border.color: Qt.rgba(1, 1, 1, 0.12); z: 100
    Text { id: t; anchors.centerIn: parent; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
}
