import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// now_playing: the cover drops onto the card with a small tilt and settles while
// the record slides half out of its sleeve, spinning down to rest; the progress
// line sweeps up to where the track is. Then it is still: the position steps
// (about a pixel at a time, at most 4 times a second, no easing) while playing.
// previous / play-pause / next act through M9's media_control (the "media"
// action; heyclaude/island_qml.py runs it and sends the new track back here).
// The cover is the player's own (the island reads it: image://island/art-np-<id>)
// or, without one, a made one: the track's initials on a warm gradient.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    property bool active: true
    signal action(string name, var payload)
    readonly property real minWidth: 400
    property bool holds: false

    // what the card shows: the tool's data, then any update after a button press
    property var live: data_ || ({})
    readonly property var d: live
    property string art: ""
    property string status: d.status || "unknown"
    readonly property bool playing: status === "playing"
    readonly property real dur: d.duration_s || 0
    property real pos0: d.position_s || 0
    property real t0: Date.now()
    property real pos: pos0
    property bool busy: false                   // a button was pressed; waiting for the player
    readonly property real frac: dur > 0 ? Math.max(0, Math.min(1, pos / dur)) : 0
    readonly property var host: typeof model !== "undefined" ? model : null

    implicitHeight: 112

    function fmt(s) {
        s = Math.max(0, Math.floor(s))
        var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60
        return (h ? h + ":" + ("0" + m).slice(-2) : m) + ":" + ("0" + r).slice(-2)
    }
    // a stable warm palette for the made cover, from the track's name
    function hash(s) { var h = 7; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 100003; return h }
    readonly property var palettes: [["#E08A5B", "#5A2330"], ["#EC9A78", "#6E3347"], ["#E8C35A", "#A4502E"],
                                     ["#D97757", "#2B1F3A"], ["#C8665A", "#3A2436"], ["#F0B07A", "#7A3E3A"]]
    readonly property var pal: palettes[hash((d.title || "") + "|" + (d.artist || "")) % palettes.length]
    function initials() {
        var src = (d.artist && d.artist.length ? d.artist : d.title || "").replace(/[^A-Za-z0-9À-ɏЀ-ӿ ]/g, " ")
        var words = src.split(/\s+/).filter(function (x) { return x.length && ["the", "a", "an"].indexOf(x.toLowerCase()) < 0 })
        if (!words.length) return "♪"
        return (words[0][0] + (words.length > 1 ? words[1][0] : "")).toUpperCase()
    }

    // ---- the clock: whole seconds for the text, ~1 px steps for the line ---------------
    Timer {
        id: tick
        interval: Math.max(250, Math.min(1000, w.dur > 0 ? w.dur * 1000 / Math.max(1, bar.width) : 1000))
        repeat: true
        running: w.playing && w.dur > 0 && w.pos < w.dur && !w.busy
        onTriggered: w.pos = Math.min(w.dur, w.pos0 + (Date.now() - w.t0) / 1000)
    }
    function restartClock() { pos0 = d.position_s || 0; t0 = Date.now(); pos = pos0 }

    // a new track (or state) from the island after a button press
    Connections {
        target: w.host
        ignoreUnknownSignals: true
        function onWidgetUpdate(id, nd) {
            if (id !== w.wid) return
            var same = nd.title === w.d.title && nd.artist === w.d.artist
            w.busy = false
            w.live = nd
            w.status = nd.status || "unknown"
            w.restartClock()
            if (!same) { w.art = ""; drop.restart() }
        }
        function onEffectArt(id, url) { if (id === "np-" + w.wid) w.art = url }
    }

    // ---- entry -------------------------------------------------------------------------
    property real u: reduced ? 1 : 0           // the cover's drop
    property real spin: reduced ? 1 : 0        // the record sliding out and spinning down
    property real sweep: reduced ? 1 : 0       // the progress line
    property real out_: 0                      // a skip slides the old cover out
    SequentialAnimation {
        id: drop
        running: !w.reduced
        ScriptAction { script: { w.u = 0; w.spin = 0; w.sweep = 0; w.out_ = 0 } }
        PauseAnimation { duration: 140 }
        ParallelAnimation {
            NumberAnimation { target: w; property: "u"; to: 1; duration: 620; easing.type: Easing.OutBack; easing.overshoot: 1.3 }
            SequentialAnimation {
                PauseAnimation { duration: 160 }
                NumberAnimation { target: w; property: "spin"; to: 1; duration: 1000; easing.type: Easing.OutCubic }
            }
            SequentialAnimation {
                PauseAnimation { duration: 260 }
                NumberAnimation { target: w; property: "sweep"; to: 1; duration: 780; easing.type: Easing.InOutCubic }
            }
        }
    }
    NumberAnimation { id: skipOut; target: w; property: "out_"; to: 1; duration: 240; easing.type: Easing.InCubic }
    // the player never answered (closed, or it can't skip): back as it was
    Timer {
        interval: 2600; running: w.busy
        onTriggered: { w.busy = false; w.restartClockAt(); if (w.out_ > 0) drop.restart() }
    }

    // ---- the record (behind the sleeve) --------------------------------------------------
    readonly property real coverS: 76
    readonly property real peek: (w.playing ? 30 : 21) * w.spin
    Item {
        id: disc
        x: w.peek * (1 - w.out_) + 3; y: 4
        width: 70; height: 70
        opacity: (w.reduced ? 1 : Math.min(1, w.spin * 3)) * (1 - w.out_)
        rotation: (w.reduced ? 0 : -220 * (1 - w.spin)) + (w.playing ? 0 : -18)
        Behavior on x { enabled: !w.reduced && w.spin >= 1; NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
        Behavior on rotation { enabled: !w.reduced && w.spin >= 1; NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
        Rectangle { anchors.fill: parent; radius: width / 2; color: "#17171A"; border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.16) }
        Repeater {
            model: [29, 24, 19]
            Rectangle {
                anchors.centerIn: parent; width: modelData * 2; height: width; radius: modelData
                color: "transparent"; border.width: 1; border.color: Qt.rgba(1, 1, 1, index === 1 ? 0.10 : 0.065)
            }
        }
        // a sheen on the grooves: it turns with the record, so the spin reads
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Qt.rgba(1, 0.95, 0.9, 0.26); strokeWidth: 2.2; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                PathAngleArc { centerX: 35; centerY: 35; radiusX: 26; radiusY: 26; startAngle: -70; sweepAngle: 38 }
            }
            ShapePath {
                strokeColor: Qt.rgba(1, 0.95, 0.9, 0.12); strokeWidth: 2; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                PathAngleArc { centerX: 35; centerY: 35; radiusX: 21; radiusY: 21; startAngle: 110; sweepAngle: 30 }
            }
        }
        Rectangle {                              // the label
            anchors.centerIn: parent; width: 24; height: 24; radius: 12
            gradient: Gradient { GradientStop { position: 0; color: w.pal[0] } GradientStop { position: 1; color: Qt.darker(w.pal[0], 1.35) } }
            Rectangle { x: 15; y: 5; width: 3; height: 3; radius: 1.5; color: Qt.rgba(1, 1, 1, 0.55) }
            Rectangle { anchors.centerIn: parent; width: 4; height: 4; radius: 2; color: "#121214" }
        }
    }

    // ---- the cover (the sleeve) ---------------------------------------------------------
    Item {
        id: cover
        objectName: "nowPlayingCover"
        width: w.coverS; height: w.coverS
        x: -40 * w.out_
        y: w.reduced ? 2 : 2 - 22 * (1 - w.u)
        opacity: w.reduced ? 1 : Math.min(1, w.u * 2.2) * (1 - w.out_)
        rotation: w.reduced ? 0 : -9 * (1 - w.u) - 6 * w.out_
        scale: w.reduced ? 1 : 1.07 - 0.07 * w.u
        transformOrigin: Item.Bottom
        // a soft shadow under the sleeve
        Rectangle { x: 2; y: 4; width: parent.width - 4; height: parent.height - 2; radius: 11; color: Qt.rgba(0, 0, 0, 0.38) }
        // the made cover: initials on a warm gradient
        Shape {
            anchors.fill: parent
            visible: madeArt.visible
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: LinearGradient {
                    x1: 0; y1: 0; x2: w.coverS; y2: w.coverS
                    GradientStop { position: 0; color: w.pal[0] }
                    GradientStop { position: 1; color: w.pal[1] }
                }
                PathSvg { path: "M10 0 H66 A10 10 0 0 1 76 10 V66 A10 10 0 0 1 66 76 H10 A10 10 0 0 1 0 66 V10 A10 10 0 0 1 10 0 Z" }
            }
            ShapePath {                          // a low sun on it, and a horizon of grooves
                strokeColor: "transparent"; fillColor: Qt.rgba(1, 0.95, 0.9, 0.13)
                PathAngleArc { centerX: 58; centerY: 60; radiusX: 12; radiusY: 12; startAngle: 0; sweepAngle: 360 }
            }
            ShapePath {
                strokeColor: Qt.rgba(1, 0.95, 0.9, 0.10); strokeWidth: 1; fillColor: "transparent"
                PathSvg { path: "M8 64 H68 M14 69 H62" }
            }
        }
        Text {
            id: madeArt
            visible: !(w.art !== "" && artImg.status === Image.Ready)
            x: 9; y: 6
            text: w.initials()
            color: "#FFF1E8"; opacity: 0.94
            font.family: "Segoe UI Variable Display"; font.pointSize: 19; font.weight: Font.DemiBold; font.letterSpacing: -0.5
        }
        Image {
            id: artImg
            anchors.fill: parent
            source: w.art
            visible: status === Image.Ready
            asynchronous: true; smooth: true; mipmap: true
            fillMode: Image.PreserveAspectCrop
            opacity: 0
            onStatusChanged: if (status === Image.Ready) artIn.restart()
            NumberAnimation on opacity { id: artIn; running: false; to: 1; duration: w.reduced ? 120 : 320; easing.type: Easing.OutCubic }
        }
        Rectangle { anchors.fill: parent; radius: 10; color: "transparent"; border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.08) }
    }

    // ---- title, artist, the buttons ------------------------------------------------------
    readonly property real tx: w.coverS + 46
    Rise {
        i: 1; reduced: w.reduced
        x: w.tx; y: 1
        Column {
            spacing: 1
            Text {
                objectName: "nowPlayingTitle"
                width: w.width - w.tx; elide: Text.ElideRight
                text: w.d.title || ""
                color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 11; font.weight: Font.DemiBold
            }
            Text {
                width: w.width - w.tx; elide: Text.ElideRight
                visible: text !== ""
                text: [w.d.artist || "", w.d.album || ""].filter(function (s) { return s }).join("  ·  ")
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
            }
        }
    }
    component Ctl: Item {
        id: c
        property string kind: "next"            // previous / play / pause / next
        property bool main_: false
        signal tapped()
        width: main_ ? 34 : 28; height: width
        Rectangle {
            anchors.fill: parent; radius: width / 2
            color: c.main_ ? Qt.rgba(217 / 255, 119 / 255, 87 / 255, ma.containsMouse ? 1 : 0.88)
                           : Qt.rgba(1, 1, 1, ma.containsMouse ? 0.16 : 0.07)
        }
        Shape {
            width: 24; height: 24; anchors.centerIn: parent
            scale: c.main_ ? 0.95 : 0.78
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillColor: c.main_ ? "#0A0A0A" : "#F2F2F2"
                PathSvg {
                    path: c.kind === "play" ? "M8.5 5.8 Q8.5 4.6 9.6 5.2 L18.6 11.1 Q19.5 12 18.6 12.9 L9.6 18.8 Q8.5 19.4 8.5 18.2 Z"
                        : c.kind === "pause" ? "M7 5 H10.4 V19 H7 Z M13.6 5 H17 V19 H13.6 Z"
                        : c.kind === "next" ? "M5 6.2 Q5 5 6 5.7 L14 11.1 Q15 12 14 12.9 L6 18.3 Q5 19 5 17.8 Z M16 5 H19 V19 H16 Z"
                        : "M19 6.2 Q19 5 18 5.7 L10 11.1 Q9 12 10 12.9 L18 18.3 Q19 19 19 17.8 Z M5 5 H8 V19 H5 Z"
                }
            }
        }
        scale: ma.pressed ? 0.9 : 1
        Behavior on scale { enabled: !w.reduced; NumberAnimation { duration: 110 } }
        MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: c.tapped() }
    }
    Rise {
        i: 2; reduced: w.reduced
        x: w.tx; y: 41
        Row {
            spacing: 8
            Ctl { objectName: "mediaPrevious"; kind: "previous"; y: 3
                onTapped: { w.busy = true; skipOut.restart(); w.action("media", "previous") } }
            Ctl { objectName: "mediaPlayPause"; kind: w.playing ? "pause" : "play"; main_: true
                onTapped: { w.status = w.playing ? "paused" : "playing"; w.restartClockAt(); w.action("media", "play_pause") } }
            Ctl { objectName: "mediaNext"; kind: "next"; y: 3
                onTapped: { w.busy = true; skipOut.restart(); w.action("media", "next") } }
        }
    }
    // toggling: the clock carries on from where the line is
    function restartClockAt() { pos0 = pos; t0 = Date.now() }
    // playing: three still bars; paused: the word
    Rise {
        i: 3; reduced: w.reduced
        x: w.width - 60; y: 51
        Item {
            width: 60; height: 14
            Row {
                visible: w.playing
                anchors.right: parent.right; anchors.bottom: parent.bottom
                spacing: 2
                Repeater {
                    model: [7, 12, 9]
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: 3; radius: 1.5; color: "#D97757"
                        height: modelData * (w.reduced ? 1 : Math.min(1, Math.max(0.25, w.sweep * 1.4 - index * 0.12)))
                    }
                }
            }
            Text {
                visible: !w.playing
                anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.bottomMargin: -2
                text: w.busy ? "…" : w.status === "paused" ? "paused" : w.status === "stopped" ? "stopped" : ""
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
            }
        }
    }

    // ---- the progress line ---------------------------------------------------------------
    Item {
        id: bar
        objectName: "nowPlayingProgress"
        visible: w.dur > 0
        x: 0; y: 90; width: w.width; height: 4
        Rectangle { anchors.fill: parent; radius: 2; color: Qt.rgba(1, 1, 1, 0.09) }
        Rectangle {
            id: fill
            height: parent.height; radius: 2
            width: Math.max(height, bar.width * w.frac * w.sweep)
            opacity: w.frac > 0 ? 1 : 0
            gradient: Gradient { orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "#B85C40" } GradientStop { position: 1; color: "#EC9A78" } }
        }
        Rectangle {                              // the ember at its head
            visible: w.frac > 0
            x: fill.width - 5; y: -3; width: 10; height: 10; radius: 5
            color: "#F2F2F2"; border.width: 2; border.color: "#D97757"
            opacity: w.reduced ? 1 : Math.min(1, w.sweep * 2)
        }
    }
    Text {
        visible: w.dur > 0
        x: 0; y: 98
        text: w.fmt(w.pos * w.sweep)
        color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.features: { "tnum": 1 }
    }
    Text {
        visible: w.dur > 0
        anchors.right: parent.right; y: 98
        text: w.fmt(w.dur)
        color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.features: { "tnum": 1 }
    }
}
