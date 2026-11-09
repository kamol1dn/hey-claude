import QtQuick
import ".."

// weather: big temperature, condition, high / low, and up to 6 hours.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 380
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    readonly property string unit: "°" + (d.unit || "C")
    implicitHeight: (d.hourly && d.hourly.length ? 92 : 50)

    Glyph { x: -2; y: 6; name: w.d.icon || "partly"; color: w.d.icon === "sun" ? "#EC9A78" : "#F2F2F2"; s: 1.5 }
    Text { x: 40; y: 2; text: Math.round(w.d.temp) + w.unit; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 20 }
    Column {
        x: 120; y: 8; width: w.width - 120
        Text { width: parent.width; text: w.d.location || ""; color: "#F2F2F2"; elide: Text.ElideRight; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text {
            width: parent.width; elide: Text.ElideRight; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
            text: [w.d.condition || "", (w.d.high !== undefined ? "H " + Math.round(w.d.high) + "°" : ""), (w.d.low !== undefined ? "L " + Math.round(w.d.low) + "°" : "")]
                  .filter(function (s) { return s }).join("  ·  ")
        }
    }
    Row {
        visible: !!(w.d.hourly && w.d.hourly.length)
        x: 0; y: 50; spacing: 0
        Repeater {
            model: w.d.hourly || []
            Item {
                id: hr
                width: Math.floor(w.width / Math.max(1, (w.d.hourly || []).length)); height: 40
                opacity: 0
                Component.onCompleted: inA.start()
                NumberAnimation { id: inA; target: hr; property: "opacity"; from: 0; to: 1; duration: 200; easing.type: Easing.OutCubic
                    running: false }
                Text { anchors.horizontalCenter: parent.horizontalCenter; y: 0; text: modelData.time; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                Glyph { anchors.horizontalCenter: parent.horizontalCenter; y: 12; name: modelData.icon || "cloud"; s: 0.6; color: modelData.icon === "sun" ? "#EC9A78" : "#F2F2F2" }
                Text { anchors.horizontalCenter: parent.horizontalCenter; y: 26; text: Math.round(modelData.temp) + "°"; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
            }
        }
    }
}
