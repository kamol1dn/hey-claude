import QtQuick

// One M9 tool effect under the head: the QML port of island_effects.py, on the
// same ui/effects.json contract. An icon, the change (old -> new) and an Undo
// button, or Confirm / Cancel for sleep, restart and shutdown. The entry
// "inhales" (a small overshoot) when the value grows and "exhales" when it
// shrinks; text rolls; an undo plays backwards. The hold is still apart from a
// countdown hairline redrawn at 4 fps, and the row goes after its ttl.
Item {
    id: row
    property var ev: null
    property string art: ""
    property real u: 0
    property real remaining: 1
    property real t0: 0
    readonly property string kind: ev ? ev.effect : "generic"
    readonly property bool hasUndo: !!ev && ev.undo === true
    readonly property bool isConfirm: kind === "confirm"
    readonly property real minWidth: ({ media: 400, confirm: 380, volume: 360, brightness: 360 })[kind] || 340
    readonly property bool animating: entry.running
    signal clicked(string what)
    signal done()
    height: 44

    function num(v) { if (v === true) return 1; if (v === false) return 0; return (typeof v === "number") ? v : null }
    function lerp(a, b, t) { return a + (b - a) * t }
    function grows(e) { var a = num(e.from), b = num(e.to); return !(a !== null && b !== null && b < a) }

    function show(e) {
        row.ev = e; row.art = ""; row.remaining = 1; row.u = 0; row.t0 = Date.now()
        grow.easing.type = grows(e) ? Easing.OutBack : Easing.OutCubic
        entry.restart()
        ttl.interval = Math.max(500, (e.ttl_s || 8) * 1000); ttl.restart()
    }
    function clear() { entry.stop(); ttl.stop(); row.ev = null }

    SequentialAnimation {
        id: entry
        PauseAnimation { duration: 120 }                     // let the row open first
        NumberAnimation { id: grow; target: row; property: "u"; from: 0; to: 1; duration: 520; easing.overshoot: 0.75 }
    }
    Timer { id: ttl; onTriggered: row.done() }
    Timer {
        interval: 250; repeat: true; running: row.ev !== null && (row.hasUndo || row.isConfirm)
        onTriggered: row.remaining = Math.max(0, 1 - (Date.now() - row.t0) / ((row.ev ? row.ev.ttl_s : 8) * 1000))
    }

    readonly property real cy: height / 2 - 1
    readonly property real rightEdge: buttons.visible ? buttons.x - 10 : width - 16
    readonly property real tx: kind === "media" ? 56 : 52
    readonly property real k: Math.max(0, Math.min(1, u))
    // shutter flash for the screenshot effect (the island lays it over the pill)
    readonly property real flash: kind === "flash" && ev && !ev.reverse ? 0.55 * Math.pow(1 - Math.min(1, (Date.now() - t0) / 380), 2) * (u < 1 ? 1 : 0) : 0

    Rectangle { x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.1) }

    // ---- icon ----------------------------------------------------------------------
    Item {
        id: iconBox
        x: 30 - 12; y: row.cy - 12; width: 24; height: 24
        visible: row.kind !== "media"
        readonly property bool toDark: !!row.ev && String(row.ev.to).toLowerCase() === "dark"
        readonly property bool fromDark: !!row.ev && String(row.ev.from).toLowerCase() === "dark"
        readonly property bool showDark: row.u >= 0.5 ? toDark : fromDark
        transform: Scale { origin.x: 12; origin.y: 12; xScale: row.kind === "theme" ? Math.max(0.06, Math.abs(Math.cos(Math.PI * row.k))) : 1 }
        Glyph {
            anchors.centerIn: parent
            name: !row.ev ? "gear" : row.kind === "volume" || row.kind === "mute" ? "speaker"
                : row.kind === "brightness" ? "sun"
                : row.kind === "theme" ? (iconBox.showDark ? "moon" : "sun")
                : row.kind === "confirm" ? "power" : (row.ev.icon || "gear")
            color: row.kind === "brightness" ? "#EC9A78" : row.kind === "theme" ? (iconBox.showDark ? "#F2F2F2" : "#EC9A78") : "#D97757"
            u: row.k
            level: !row.ev ? 60 : row.kind === "mute" ? (row.ev.to ? 0 : 60)
                 : (row.kind === "volume" || row.kind === "brightness") ? row.lerp(row.num(row.ev.from) || 0, row.num(row.ev.to) || 0, row.u) : 70
            slash: row.kind === "mute" && row.ev ? (row.ev.to ? row.k : 1 - row.k) : 0
            on: row.ev && (row.ev.icon === "wifi" || row.ev.icon === "bluetooth") && typeof row.ev.to === "boolean" ? row.ev.to : undefined
        }
    }
    // a warm wash for light mode during the theme flip
    Rectangle {
        visible: row.kind === "theme" && !!row.ev
        anchors.fill: parent; anchors.margins: 1
        color: "#FFF1E8"
        opacity: visible ? 0.10 * row.lerp(iconBox.fromDark ? 0 : 1, iconBox.toDark ? 0 : 1, row.k) : 0
    }
    // media: the album art flips in
    Item {
        visible: row.kind === "media"
        x: 16; y: row.cy - 15; width: 30; height: 30
        transform: Scale { origin.x: 15; origin.y: 15; xScale: Math.max(0.05, row.k) }
        Rectangle {
            anchors.fill: parent; radius: 6; color: Qt.rgba(1, 1, 1, 0.09); clip: true
            Image { anchors.fill: parent; source: row.art; visible: row.art !== ""; fillMode: Image.PreserveAspectCrop }
            Glyph { anchors.centerIn: parent; visible: row.art === ""; name: "music"; color: "#D97757" }
        }
    }

    // ---- label + value -------------------------------------------------------------
    Text {
        visible: row.kind !== "media" && row.kind !== "confirm"
        x: row.tx; y: 5; width: Math.max(10, row.rightEdge - row.tx); height: 14
        text: row.ev ? row.ev.label : ""; elide: Text.ElideRight
        color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8; verticalAlignment: Text.AlignVCenter
    }
    // volume / brightness: a slider that glides to the new level
    Item {
        id: slider
        visible: row.kind === "volume" || row.kind === "brightness"
        readonly property real a: row.ev ? (row.num(row.ev.from) !== null ? row.num(row.ev.from) : (row.num(row.ev.to) || 0)) : 0
        readonly property real v: row.ev ? row.lerp(a, row.num(row.ev.to) || 0, row.u) : 0
        readonly property real x1: row.rightEdge - 42
        readonly property real f: Math.max(0, Math.min(1, v / 100))
        Rectangle { x: row.tx; y: 27; width: Math.max(4, slider.x1 - row.tx); height: 4; radius: 2; color: Qt.rgba(1, 1, 1, 0.12) }
        Rectangle { x: row.tx; y: 27; height: 4; radius: 2; color: row.kind === "brightness" ? "#EC9A78" : "#D97757"
            width: Math.max(4, (slider.x1 - row.tx) * slider.f) }
        Rectangle { x: row.tx + (slider.x1 - row.tx) * slider.f - 5; y: 24; width: 10; height: 10; radius: 5; color: "#F2F2F2" }
        Text { x: slider.x1 + 8; y: 18; height: 22; verticalAlignment: Text.AlignVCenter; text: Math.round(slider.v) + "%"
            color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
    }
    // generic / mute / theme / flash: old -> new, the new text rolling in from below
    Item {
        id: change
        visible: ["generic", "mute", "theme", "flash"].indexOf(row.kind) >= 0
        readonly property string oldT: !row.ev ? "" : row.kind === "theme" ? (row.ev.text_from || "")
            : (row.ev.text_from && row.ev.text_to && row.ev.text_from !== row.ev.text_to ? row.ev.text_from : "")
        readonly property string newT: !row.ev ? "" : row.kind === "mute" ? (row.ev.to ? "muted" : "sound on")
            : row.kind === "theme" ? (row.ev.text_to || "") + " mode" : (row.ev.text_to || row.ev.text_from || "")
        readonly property bool arrow: oldT !== "" && row.kind !== "theme"
        readonly property real ow: arrow ? Math.min(oldM.advanceWidth(oldT), (row.rightEdge - row.tx) * 0.42) : 0
        FontMetrics { id: oldM; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text { visible: change.arrow; x: row.tx; y: 18; width: change.ow; height: 20; verticalAlignment: Text.AlignVCenter
            text: change.oldT; elide: Text.ElideMiddle; color: "#9A9A9A"; opacity: 0.4 + 0.6 * row.k
            font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Rectangle { visible: change.arrow; x: row.tx + change.ow + 8; y: 27.3; height: 1.4; width: 12 * row.k; color: "#D97757" }
        Text { visible: change.arrow && row.u > 0.6; x: row.tx + change.ow + 15; y: 18; height: 20; verticalAlignment: Text.AlignVCenter
            text: "›"; color: "#D97757"; font.pointSize: 10 }
        Text { visible: row.kind === "theme" && change.oldT !== "" && row.u < 1; x: row.tx; y: 18 - 5 * row.k; height: 20
            verticalAlignment: Text.AlignVCenter; text: change.oldT + " mode"; color: "#F2F2F2"; opacity: 1 - row.k
            font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text { x: change.arrow ? row.tx + change.ow + 28 : row.tx; y: 18 + 5 * (1 - row.k); height: 20
            width: Math.max(10, row.rightEdge - x); verticalAlignment: Text.AlignVCenter; elide: Text.ElideMiddle
            text: change.newT; color: "#F2F2F2"; opacity: row.kind === "theme" ? row.k : Math.min(1, row.u * 1.4)
            font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
    }
    // media: the title (a skip slides it), artist and status, a progress line
    Item {
        id: media
        visible: row.kind === "media"
        readonly property var ex: row.ev && row.ev.extra ? row.ev.extra : ({})
        readonly property string title: ex.title || (row.ev ? row.ev.text_to : "") || ""
        readonly property var old: row.ev && row.ev.from && typeof row.ev.from === "object" ? row.ev.from : ({})
        readonly property string action: String(ex.action || "")
        readonly property bool skipped: (action === "next" || action === "previous") && !!old.title && old.title !== title
        readonly property int dir: ((action === "next") !== !!(row.ev && row.ev.reverse)) ? -1 : 1
        Text { visible: media.skipped; x: row.tx + media.dir * 30 * row.k; y: 5; width: Math.max(10, row.rightEdge - row.tx); height: 18
            verticalAlignment: Text.AlignVCenter; text: media.old.title || ""; elide: Text.ElideRight; color: "#F2F2F2"; opacity: 1 - row.k
            font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text { x: row.tx - (media.skipped ? media.dir * 30 * (1 - row.k) : 0); y: 5; width: Math.max(10, row.rightEdge - row.tx); height: 18
            verticalAlignment: Text.AlignVCenter; text: media.title || "nothing playing"; elide: Text.ElideRight; color: "#F2F2F2"
            opacity: media.skipped ? row.k : Math.min(1, row.u * 1.3); font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text { x: row.tx; y: 20; width: Math.max(10, row.rightEdge - row.tx); height: 14; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            text: [media.ex.artist || "", (media.ex.status && media.ex.status !== "playing") ? media.ex.status : ""].filter(function (s) { return s }).join(" · ")
            color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
        Rectangle { visible: media.ex.progress !== undefined; x: row.tx; y: row.height - 5.5; width: Math.max(1, row.rightEdge - row.tx); height: 1.5; color: Qt.rgba(1, 1, 1, 0.12) }
        Rectangle { visible: media.ex.progress !== undefined; x: row.tx; y: row.height - 5.5; height: 1.5; color: "#D97757"
            width: Math.max(0, (row.rightEdge - row.tx) * (media.ex.progress || 0) * Math.min(1, row.u)) }
    }
    // confirm: the question
    Text {
        visible: row.kind === "confirm"
        x: 52; width: Math.max(10, row.rightEdge - 52); height: row.height - 2; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        readonly property string action: String(row.ev && row.ev.extra && row.ev.extra.action ? row.ev.extra.action : (row.ev ? row.ev.label : "")).toLowerCase()
        text: ({ sleep: "Put the PC to sleep?", restart: "Restart the PC?", shutdown: "Shut down the PC?" })[action] || (action + "?")
        color: "#F2F2F2"; opacity: Math.min(1, row.u * 1.3); font.family: "Segoe UI Variable Text"; font.pointSize: 10
    }

    // ---- buttons + countdown -------------------------------------------------------------
    Row {
        id: buttons
        visible: row.isConfirm || row.hasUndo
        spacing: 8
        x: row.width - 14 - width; y: row.cy - 12
        Pill { visible: row.isConfirm; label: "Cancel"; w: 60; onTapped: row.clicked("cancel") }
        Pill { visible: row.isConfirm; label: "Confirm"; w: 76; primary: true; onTapped: row.clicked("confirm") }
        Pill { visible: row.hasUndo && !row.isConfirm; label: "Undo"; w: 58; accent: true; onTapped: row.clicked("undo") }
    }
    Rectangle {
        visible: row.remaining > 0 && (row.hasUndo || row.isConfirm)
        x: 20; y: row.height - 3.6; height: 1.2; width: (row.width - 40) * row.remaining
        color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.35)
    }
}
