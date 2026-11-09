import QtQuick
import ".."

// calendar_list: an agenda, one row per event; rows slide in one by one.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 420
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var events: d.events || []
    implicitHeight: (d.title ? 20 : 0) + 28 * events.length

    Text { visible: !!w.d.title; y: 0; height: 18; text: w.d.title || ""; color: "#9A9A9A"; verticalAlignment: Text.AlignVCenter
        font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6 }
    Repeater {
        model: w.events
        Item {
            id: ev
            y: (w.d.title ? 20 : 0) + index * 28; width: w.width; height: 28
            opacity: 0
            property real dx: 8
            transform: Translate { x: ev.dx }
            Component.onCompleted: slide.start()
            ParallelAnimation {
                id: slide
                SequentialAnimation { PauseAnimation { duration: 40 * index } NumberAnimation { target: ev; property: "opacity"; to: 1; duration: 200; easing.type: Easing.OutCubic } }
                SequentialAnimation { PauseAnimation { duration: 40 * index } NumberAnimation { target: ev; property: "dx"; to: 0; duration: 200; easing.type: Easing.OutCubic } }
            }
            Rectangle { x: 0; y: 6; width: 3; height: 16; radius: 1.5; color: modelData.color || "#D97757" }
            Text { x: 10; width: 86; height: 28; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                text: modelData.start + (modelData.end ? "–" + modelData.end : ""); color: "#9A9A9A"
                font.family: "Segoe UI Variable Text"; font.pointSize: 9; font.features: { "tnum": 1 } }
            Text { x: 100; width: w.width - 100 - (loc.visible ? loc.width + 8 : 0); height: 28; verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight; text: modelData.title; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
            Text { id: loc; visible: !!modelData.location; x: w.width - width; height: 28; verticalAlignment: Text.AlignVCenter
                width: Math.min(implicitWidth, 130); elide: Text.ElideRight
                text: modelData.location || ""; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
        }
    }
}
