import QtQuick
import "../../ui/island"

// weather: the temperature ticks in, the sky is animated on the GPU (Sky.qml),
// six hours, and three days whose ranges grow from the left. Data comes from
// the built-in Open-Meteo tool (heyclaude/weather.py), never from the model.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    property bool active: true                  // the island isn't resting: the sky may move
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var hours: d.hourly || []
    readonly property var days: d.days || []
    readonly property real lo: days.length ? Math.min.apply(null, days.map(function (x) { return x.low })) - 2 : 0
    readonly property real hi: days.length ? Math.max.apply(null, days.map(function (x) { return x.high })) + 2 : 1
    implicitHeight: 58 + (hours.length ? 62 : 0) + (days.length ? 8 + 22 * days.length : 0)

    function ic(name) { return name || "cloud" }
    function icColor(name) { return name === "sun" ? "#EC9A78" : "#F2F2F2" }

    Sky {
        x: w.width - 220; y: -30; width: 236; height: 88       // up into the header's empty right side
        icon: w.d.icon || "cloud"
        active: w.active
        reduced: w.reduced
    }
    Rise {
        i: 0; reduced: w.reduced
        y: 0
        Tick {
            value: w.d.temp || 0; reduced: w.reduced; suffix: "°"
            color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 30; font.weight: Font.DemiBold
        }
    }
    Rise {
        i: 1; reduced: w.reduced
        x: 104; y: 9
        Column {
            spacing: 2
            Text { text: w.d.condition || ""; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
            Text {
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                text: [w.d.high !== undefined ? "H " + Math.round(w.d.high) + "°" : "", w.d.low !== undefined ? "L " + Math.round(w.d.low) + "°" : "",
                       w.d.feels !== undefined ? "feels " + Math.round(w.d.feels) + "°" : "", w.d.wind !== undefined ? "wind " + Math.round(w.d.wind) : ""]
                      .filter(function (s) { return s }).join("   ")
            }
        }
    }
    // the next hours
    Rectangle { visible: w.hours.length > 0; y: 58; width: w.width; height: 1; color: Qt.rgba(1, 1, 1, 0.06) }
    Repeater {
        model: w.hours
        Rise {
            i: index + 2; reduced: w.reduced
            x: index * (w.width / Math.max(1, w.hours.length)); y: 64
            width: w.width / Math.max(1, w.hours.length)
            Item {
                width: w.width / Math.max(1, w.hours.length); height: 54
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.time; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                Glyph { anchors.horizontalCenter: parent.horizontalCenter; y: 15; name: w.ic(modelData.icon); s: 0.75; color: w.icColor(modelData.icon) }
                Text { anchors.horizontalCenter: parent.horizontalCenter; y: 36; text: Math.round(modelData.temp) + "°"; color: "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 9; font.features: { "tnum": 1 } }
            }
        }
    }
    // three days, with the day's range on the week's scale
    Repeater {
        model: w.days
        Rise {
            id: dayRow
            i: index + 8; reduced: w.reduced
            y: 58 + (w.hours.length ? 62 : 0) + 6 + index * 22
            Item {
                width: w.width; height: 20
                Text { width: 46; anchors.verticalCenter: parent.verticalCenter; text: modelData.day; color: "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
                Glyph { x: 46; anchors.verticalCenter: parent.verticalCenter; name: w.ic(modelData.icon); s: 0.7; color: w.icColor(modelData.icon) }
                Text { x: 70; width: 34; horizontalAlignment: Text.AlignRight; anchors.verticalCenter: parent.verticalCenter
                    text: Math.round(modelData.low) + "°"; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
                Rectangle {
                    id: track
                    x: 114; width: w.width - 114 - 44; height: 4; radius: 2; anchors.verticalCenter: parent.verticalCenter
                    color: Qt.rgba(1, 1, 1, 0.08)
                    Rectangle {
                        id: bar
                        x: track.width * (modelData.low - w.lo) / Math.max(1, w.hi - w.lo)
                        height: 4; radius: 2
                        readonly property real full: track.width * (modelData.high - modelData.low) / Math.max(1, w.hi - w.lo)
                        width: 0
                        gradient: Gradient { orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: "#8FB6D8" } GradientStop { position: 1; color: "#EC9A78" } }
                        Component.onCompleted: grow.start()
                        SequentialAnimation {
                            id: grow
                            PauseAnimation { duration: w.reduced ? 0 : (index + 8) * 45 + 260 }
                            NumberAnimation { target: bar; property: "width"; to: bar.full; duration: w.reduced ? 0 : 520; easing.type: Easing.OutCubic }
                        }
                    }
                }
                Text { x: w.width - 36; width: 36; horizontalAlignment: Text.AlignRight; anchors.verticalCenter: parent.verticalCenter
                    text: Math.round(modelData.high) + "°"; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
            }
        }
    }
}
