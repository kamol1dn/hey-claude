import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// system: battery, CPU/GPU load, VRAM and RAM (mcp__info__system_status, never the
// model), with hey-claude's own devices (stt, voice) as chips.
// Entry (under ~1.2 s, then nothing moves): the battery fills cell by cell, the two
// load rings sweep up from zero past their value and settle back (an overshoot, like a
// needle), the memory bars fill the same way, and every number counts up from zero.
// Reduced motion: everything is drawn at its value, with the rows' plain fade.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var bat: d.battery || null
    readonly property var cpu: d.cpu || null
    readonly property var gpu: d.gpu || null
    readonly property var vram: d.vram || null
    readonly property var ram: d.ram || null
    readonly property var devices: (d.devices || []).slice(0, 3)
    readonly property string hw: [cpu && cpu.label ? cpu.label : "", gpu && gpu.label ? gpu.label : ""]
                                 .filter(function (s) { return s }).join("  ·  ")
    readonly property bool hasTop: bat !== null || devices.length > 0 || hw !== ""
    readonly property bool hasGauges: cpu !== null || gpu !== null || vram !== null || ram !== null
    readonly property real yGauges: hasTop ? 54 : 0
    readonly property real yFoot: yGauges + (hasGauges ? 96 : 0)
    readonly property bool hasFoot: bat !== null && hw !== ""
    implicitHeight: yFoot + (hasFoot ? 18 : 0)

    readonly property color accent: "#D97757"
    readonly property color hot: "#EC9A78"
    readonly property color ok: "#6CCB7A"
    readonly property color bad: "#E5646A"
    readonly property color dim: "#9A9A9A"
    function loadColor(p) { return p >= 90 ? bad : accent }
    function gb(v) { return v >= 100 ? Math.round(v).toString() : (Math.round(v * 10) / 10).toFixed(1) }

    // a number that counts up from zero once (whole numbers only, so it never flickers)
    component CountUp: Text {
        id: cu
        property real value: 0
        property int delay: 0
        property real shown: 0
        property int decimals: 0
        property string suffix: ""
        text: shown.toFixed(decimals) + suffix
        font.family: "Segoe UI Variable Text"; font.features: { "tnum": 1 }
        Component.onCompleted: { if (w.reduced) shown = value; else run.start() }
        SequentialAnimation {
            id: run
            PauseAnimation { duration: cu.delay }
            NumberAnimation { target: cu; property: "shown"; to: cu.value; duration: 820; easing.type: Easing.OutCubic }
        }
    }

    // a 270° load ring: sweeps past its value and settles, an ember on its head
    component Ring: Item {
        id: rg
        property real value: 0                     // 0..100
        property string label: ""
        property string sub: ""
        property int delay: 0
        property real u: 0                         // the drawn value (overshoots on entry)
        readonly property real sz: 78
        readonly property real r: 31
        readonly property color col: w.loadColor(value)
        width: sz; height: sz
        Component.onCompleted: { if (w.reduced) u = value; else sweep.start() }
        SequentialAnimation {
            id: sweep
            PauseAnimation { duration: rg.delay }
            NumberAnimation { target: rg; property: "u"; from: 0; to: rg.value; duration: 900
                easing.type: Easing.OutBack; easing.overshoot: 2.2 }
        }
        readonly property real frac: Math.max(0, Math.min(1, u / 100))
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath { strokeColor: Qt.rgba(1, 1, 1, 0.08); strokeWidth: 6; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                PathAngleArc { centerX: rg.sz / 2; centerY: rg.sz / 2; radiusX: rg.r; radiusY: rg.r; startAngle: 135; sweepAngle: 270 } }
            ShapePath {
                strokeColor: rg.col; strokeWidth: 6; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                PathAngleArc { centerX: rg.sz / 2; centerY: rg.sz / 2; radiusX: rg.r; radiusY: rg.r; startAngle: 135
                    sweepAngle: Math.max(0.4, 270 * rg.frac) }
            }
        }
        // tick marks at 0, 25, 50, 75, 100
        Repeater {
            model: 5
            Rectangle {
                readonly property real a: (135 + 67.5 * index) * Math.PI / 180
                x: rg.sz / 2 + (rg.r + 6.5) * Math.cos(a) - 1; y: rg.sz / 2 + (rg.r + 6.5) * Math.sin(a) - 1
                width: 2; height: 2; radius: 1; color: Qt.rgba(1, 1, 1, 0.16)
            }
        }
        Rectangle {
            readonly property real a: (135 + 270 * rg.frac) * Math.PI / 180
            x: rg.sz / 2 + rg.r * Math.cos(a) - 4.5; y: rg.sz / 2 + rg.r * Math.sin(a) - 4.5
            width: 9; height: 9; radius: 4.5
            color: rg.value >= 90 ? "#F2A0A4" : w.hot; border.width: 2; border.color: rg.col
            opacity: rg.u > 0.5 ? 1 : 0
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            y: rg.sub ? 21 : 26
            CountUp { value: Math.round(rg.value); delay: rg.delay; color: "#F2F2F2"; font.pointSize: 15; font.weight: Font.DemiBold }
            Text { text: "%"; color: w.dim; font.family: "Segoe UI Variable Text"; font.pointSize: 8; y: 9 }
        }
        Text {
            visible: rg.sub !== ""
            anchors.horizontalCenter: parent.horizontalCenter; y: 45
            text: rg.sub; color: w.dim; font.family: "Segoe UI Variable Text"; font.pointSize: 7; font.features: { "tnum": 1 }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; y: rg.sz - 15
            text: rg.label; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5
            font.letterSpacing: 0.8; font.weight: Font.DemiBold
        }
    }

    // a memory bar: label, "3.7 / 8.0 GB", a bar that fills past its value and settles
    component Meter: Item {
        id: mt
        property string label: ""
        property real used: 0
        property real total: 1
        property int delay: 0
        property real u: 0
        readonly property real frac: total > 0 ? Math.max(0, Math.min(1, used / total)) : 0
        readonly property color col: w.loadColor(frac * 100)
        height: 34
        Component.onCompleted: { if (w.reduced) u = frac; else fill.start() }
        SequentialAnimation {
            id: fill
            PauseAnimation { duration: mt.delay }
            NumberAnimation { target: mt; property: "u"; from: 0; to: mt.frac; duration: 900
                easing.type: Easing.OutBack; easing.overshoot: 1.8 }
        }
        Text { text: mt.label; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5
            font.letterSpacing: 0.8; font.weight: Font.DemiBold; y: 2 }
        Row {
            anchors.right: parent.right
            spacing: 3
            CountUp { value: mt.used; decimals: 1; delay: mt.delay; color: "#F2F2F2"; font.pointSize: 9.5 }
            Text { text: "/ " + w.gb(mt.total) + " GB"; color: w.dim; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                font.features: { "tnum": 1 }; y: 2 }
        }
        Rectangle {
            y: 22; width: parent.width; height: 6; radius: 3; color: Qt.rgba(1, 1, 1, 0.08)
            Rectangle {
                height: parent.height; radius: 3
                width: Math.max(6, Math.min(parent.width, parent.width * mt.u))
                gradient: Gradient { orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Qt.darker(mt.col, 1.25) } GradientStop { position: 1; color: mt.col } }
            }
            // quarter marks on the track
            Repeater {
                model: 3
                Rectangle { x: parent.width * (index + 1) / 4; y: 1; width: 1; height: 4; color: Qt.rgba(0, 0, 0, 0.35) }
            }
        }
    }

    // ---- top row: the battery (cell by cell) or the hardware; hey-claude's devices -------
    Rise {
        i: 0; reduced: w.reduced
        visible: w.bat !== null
        y: 2
        Item {
            id: batRow
            width: 220; height: 44
            readonly property real pct: w.bat ? w.bat.percent : 0
            readonly property color cellCol: w.bat && w.bat.charging ? w.ok : pct <= 20 ? w.bad : pct <= 35 ? w.hot : "#F2F2F2"
            // the battery outline and its five cells
            Item {
                id: shell
                y: 7; width: 54; height: 26
                Rectangle { anchors.fill: parent; radius: 6; color: "transparent"; border.width: 1.5; border.color: Qt.rgba(1, 1, 1, 0.32) }
                Rectangle { x: parent.width + 2; y: 8; width: 3; height: 10; radius: 1.5; color: Qt.rgba(1, 1, 1, 0.32) }
                Repeater {
                    model: 5
                    Item {
                        id: cell
                        readonly property real fillTo: Math.max(0, Math.min(1, (batRow.pct - index * 20) / 20))
                        property real f: 0
                        x: 4 + index * 9.4; y: 4; width: 8; height: 18
                        Rectangle { anchors.fill: parent; radius: 2; color: Qt.rgba(1, 1, 1, 0.05) }
                        Rectangle {
                            width: parent.width * cell.f; height: parent.height; radius: 2
                            color: batRow.cellCol
                            opacity: cell.f > 0 ? 0.95 : 0
                        }
                        Component.onCompleted: { if (w.reduced) f = fillTo; else cellAnim.start() }
                        SequentialAnimation {
                            id: cellAnim
                            PauseAnimation { duration: 180 + index * 95 }
                            NumberAnimation { target: cell; property: "f"; from: 0; to: cell.fillTo
                                duration: cell.fillTo > 0 ? 160 : 0; easing.type: Easing.OutQuad }
                        }
                    }
                }
                // plugged in: a bolt over the cells, popping in once they're full
                Item {
                    id: boltBox
                    visible: w.bat !== null && w.bat.plugged
                    anchors.centerIn: parent
                    width: 24; height: 24
                    scale: w.reduced ? 1 : 0
                    Glyph { anchors.centerIn: parent; name: "bolt"; color: batRow.pct > 50 ? batRow.cellCol : "#F2F2F2"; s: 1.42 }
                    Glyph { anchors.centerIn: parent; name: "bolt"; color: "#0B0B0B"; s: 1.08 }
                    SequentialAnimation {
                        running: !w.reduced && boltBox.visible
                        PauseAnimation { duration: 180 + 5 * 95 + 40 }
                        NumberAnimation { target: boltBox; property: "scale"; from: 0; to: 1; duration: 320
                            easing.type: Easing.OutBack; easing.overshoot: 2.4 }
                    }
                }
            }
            Row {
                x: 72; y: 0
                CountUp { value: batRow.pct; delay: 180; color: "#F2F2F2"; font.pointSize: 20; font.weight: Font.DemiBold }
                Text { text: "%"; color: w.dim; font.family: "Segoe UI Variable Text"; font.pointSize: 10; y: 14 }
            }
            Text {
                x: 72; y: 31
                text: w.bat ? (w.bat.note || (w.bat.charging ? "charging" : w.bat.plugged ? "plugged in" : "on battery")) : ""
                color: w.bat && w.bat.charging ? w.ok : w.dim
                font.family: "Segoe UI Variable Text"; font.pointSize: 8
            }
        }
    }
    // no battery (a desktop): the hardware names take its place
    Rise {
        i: 0; reduced: w.reduced
        visible: w.bat === null && w.hw !== ""
        y: 6
        Column {
            spacing: 1
            Text { text: w.cpu && w.cpu.label ? w.cpu.label : ""; visible: text !== ""; color: "#F2F2F2"
                font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
            Text { text: w.gpu && w.gpu.label ? w.gpu.label : ""; visible: text !== ""; color: w.dim
                font.family: "Segoe UI Variable Text"; font.pointSize: 8.5 }
        }
    }
    // hey-claude's own devices: "stt  gpu (turbo)", "voice  kokoro gpu"
    Rise {
        i: 1; reduced: w.reduced
        visible: w.devices.length > 0
        x: w.width - width; y: w.devices.length > 2 ? 0 : 5
        Column {
            spacing: 4
            Repeater {
                model: w.devices
                Rectangle {
                    objectName: "deviceChip"
                    anchors.right: parent ? parent.right : undefined
                    height: 18; radius: 9
                    width: chipRow.implicitWidth + 16
                    color: Qt.rgba(1, 1, 1, 0.06)
                    Row {
                        id: chipRow
                        x: 8; anchors.verticalCenter: parent.verticalCenter
                        spacing: 6
                        Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                            color: modelData.gpu ? w.ok : w.dim }
                        Text { text: modelData.name; color: w.dim; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5 }
                        Text { text: modelData.value; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5 }
                    }
                }
            }
        }
    }

    // ---- the gauges: two load rings, two memory bars ----------------------------------------
    Item {
        visible: w.hasGauges
        y: w.yGauges; width: w.width; height: 92
        Rise {
            i: 2; reduced: w.reduced
            visible: w.cpu !== null
            x: 0
            Ring { value: w.cpu ? w.cpu.percent : 0; label: "CPU"; sub: w.cpu ? w.cpu.sub || "" : ""; delay: 260 }
        }
        Rise {
            i: 3; reduced: w.reduced
            visible: w.gpu !== null
            x: w.cpu !== null ? 96 : 0
            Ring { value: w.gpu ? w.gpu.percent : 0; label: "GPU"; sub: w.gpu ? w.gpu.sub || "" : ""; delay: 340 }
        }
        Column {
            readonly property real lx: (w.cpu !== null ? 96 : 0) + (w.gpu !== null ? 96 : 0) + 18
            x: lx; y: 8; width: w.width - lx
            spacing: 12
            Rise {
                i: 4; reduced: w.reduced
                visible: w.vram !== null
                Meter { width: w.width - parent.parent.lx; label: "VRAM"; used: w.vram ? w.vram.used : 0
                    total: w.vram ? w.vram.total : 1; delay: 400 }
            }
            Rise {
                i: 5; reduced: w.reduced
                visible: w.ram !== null
                Meter { width: w.width - parent.parent.lx; label: "RAM"; used: w.ram ? w.ram.used : 0
                    total: w.ram ? w.ram.total : 1; delay: 470 }
            }
        }
    }

    // ---- what it's running on ----------------------------------------------------------------
    Rise {
        i: 6; reduced: w.reduced
        visible: w.hasFoot
        y: w.yFoot
        Text {
            width: w.width; elide: Text.ElideRight
            text: w.hw; color: "#6f6a66"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5
        }
    }
}
