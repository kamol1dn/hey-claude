import QtQuick

// The weather card's animated conditions (sky.frag, on the GPU). 60 fps for
// the entry, then ~8 fps while the card is up, and nothing once `active` goes
// false (the island rests) or in reduced motion (one still frame).
ShaderEffect {
    id: sky
    property string icon: "sun"
    property bool active: true
    property bool reduced: false
    readonly property var conds: ({ sun: 0, partly: 1, cloud: 2, rain: 3, storm: 4, snow: 5, fog: 6, night: 7, wind: 8 })
    property size size: Qt.size(width, height)
    property real t: 0
    property real cond: conds[icon] !== undefined ? conds[icon] : 2
    property real fade: 0
    property real flash: 0
    property real still: reduced ? 1 : 0
    fragmentShader: Qt.resolvedUrl("sky.frag.qsb")       // relative to this file, wherever Sky is used
    blending: true

    property real t0: Date.now()
    function step() {
        t = (Date.now() - t0) / 1000
        if (icon === "storm" && !reduced) { if (Math.random() < 0.02) flash = 1; else flash *= 0.8 }
        if (fade < 1) fade = Math.min(1, t / 0.5)
    }
    Component.onCompleted: { t0 = Date.now(); step() }
    Timer {
        interval: (Date.now() - sky.t0) < 1400 ? 16 : 125      // entry at 60 fps, then ~8 fps
        repeat: true
        running: sky.visible && sky.active && !sky.reduced
        onTriggered: sky.step()
    }
}
