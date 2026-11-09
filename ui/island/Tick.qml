import QtQuick

// A number that ticks to its new value (temperatures, counters), then is still.
Text {
    id: tk
    property real value: 0
    property real shown: value
    property string suffix: ""
    property int decimals: 0
    property bool reduced: false
    text: shown.toFixed(decimals) + suffix
    font.features: { "tnum": 1 }
    Behavior on shown { enabled: !tk.reduced; NumberAnimation { duration: 650; easing.type: Easing.OutCubic } }
    Component.onCompleted: { if (!reduced) { var v = value; shown = value - Math.max(3, Math.abs(value) * 0.25); shown = v } }
}
