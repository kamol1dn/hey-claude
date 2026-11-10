import QtQuick
import "../../ui/island"

// timetable: the next class lifted on its own card (subject, room, a live countdown),
// a day ribbon under it (the classes as blocks on the hours, a "now" marker that
// glides in to the current time, the elapsed part warming behind it) and the rest of
// the day. Entry: the ribbon slides in and its blocks unroll, the marker glides, the
// next-class card lifts. After that only a 1 s tick changes digits (and the marker
// moves a fraction of a pixel a second): nothing loops.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    property bool active: true
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var classes: d.classes || []
    readonly property int fi: (d.focus_state === "now" || d.focus_state === "next") ? (d.focus === undefined ? -1 : d.focus) : -1
    readonly property var fc: fi >= 0 && fi < classes.length ? classes[fi] : null
    readonly property var after: d.after || null
    readonly property bool isToday: d.now_min !== undefined && d.now_min !== null
    readonly property var rest: {
        var out = []
        if (fi < 0) return out
        for (var i = fi + 1; i < classes.length; i++) out.push(classes[i])
        return out
    }
    readonly property int restShown: Math.min(3, rest.length)

    // ---- time: the tool's "now" plus the seconds since the card opened ---------------
    property real elapsed: 0
    property real openedAt: 0
    Component.onCompleted: openedAt = Date.now()
    Timer {
        interval: 1000; repeat: true; running: w.visible
        onTriggered: w.elapsed = Math.round((Date.now() - w.openedAt) / 1000)
    }
    readonly property real nowMin: (d.now_min || 0) + elapsed / 60
    readonly property real remain: Math.max(0, (fc ? d.focus_in_s || 0 : after ? after.in_s || 0 : 0) - elapsed)

    function mins(s) { var m = String(s || "").match(/(\d{1,2}):(\d{2})/); return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : 0 }
    function two(n) { return ("0" + n).slice(-2) }
    function count(s) {
        s = Math.max(0, Math.round(s))
        if (s < 3600) return Math.floor(s / 60) + ":" + two(s % 60)
        if (s < 86400) { var m = Math.floor(s / 60); return Math.floor(m / 60) + " h " + two(m % 60) + " m" }
        var h = Math.floor(s / 3600); return Math.floor(h / 24) + " d " + (h % 24) + " h"
    }
    function stateOf(i) {
        var c = classes[i]
        if (!isToday) return c.state
        var s = mins(c.start), e = mins(c.end)
        return e <= nowMin ? "done" : s <= nowMin ? "now" : i === fi ? "next" : "later"
    }

    readonly property string kicker: fc ? (d.focus_state === "now" ? "NOW" : remain <= 0 ? "STARTING" : isToday ? "NEXT" : "FIRST")
                                     : after ? (classes.length ? "DONE FOR TODAY" : "NOTHING " + (d.label || "today").toUpperCase())
                                     : "FREE"
    readonly property color tone: fc ? (d.focus_state === "now" ? "#6CCB7A" : "#D97757") : "#9A9A9A"
    readonly property var hero: fc || after
    readonly property string heroTitle: hero ? hero.title : classes.length ? "Classes are over" : "No classes"
    readonly property string heroWhen: fc ? fc.start + "–" + fc.end : after ? (after.label + " " + after.start) : (d.label || "")
    readonly property string countLabel: !hero ? "" : fc && d.focus_state === "now" ? "ends in" : fc ? (remain <= 0 ? "" : "starts in") : "next in"

    // ---- layout -----------------------------------------------------------------------
    readonly property real heroH: 66
    readonly property bool hasRibbon: classes.length > 0
    readonly property real ribbonY: heroH + 12
    readonly property real ribbonH: 40
    readonly property real listY: ribbonY + ribbonH + 4
    implicitHeight: heroH + (hasRibbon ? 12 + ribbonH : 0) + (restShown ? 4 + restShown * 22 : 0) + 2

    // the ribbon's hours: the day's classes (and now, when it's near), on whole hours
    readonly property real lo: {
        if (!classes.length) return 480
        var m = mins(classes[0].start)
        if (isToday && d.now_min < m) m = Math.max(m - 120, d.now_min - 10)
        return Math.floor(m / 60) * 60
    }
    readonly property real hi: {
        if (!classes.length) return 1080
        var m = 0
        for (var i = 0; i < classes.length; i++) m = Math.max(m, mins(classes[i].end))
        if (isToday && d.now_min > m) m = Math.min(m + 120, d.now_min + 10)
        return Math.max(lo + 240, Math.ceil(m / 60) * 60)
    }
    function xOf(m) { return (Math.max(lo, Math.min(hi, m)) - lo) / (hi - lo) * w.width }
    readonly property int step: (hi - lo) <= 420 ? 60 : (hi - lo) <= 720 ? 120 : 180

    // ---- entry: ribbon in, blocks unroll, marker glides, hero lifts ---------------------
    property real ribbonU: reduced ? 1 : 0
    property real markU: reduced ? 1 : 0
    property real liftU: reduced ? 1 : 0
    property real fadeU: reduced ? 0 : 1          // reduced: a plain fade of the whole card
    opacity: reduced ? 1 - fadeU : 1
    SequentialAnimation {
        running: true
        PauseAnimation { duration: w.reduced ? 0 : 110 }
        ParallelAnimation {
            NumberAnimation { target: w; property: "fadeU"; from: 1; to: 0; duration: w.reduced ? 160 : 0 }
            NumberAnimation { target: w; property: "ribbonU"; to: 1; duration: w.reduced ? 0 : 520; easing.type: Easing.OutCubic }
            SequentialAnimation {
                PauseAnimation { duration: w.reduced ? 0 : 180 }
                NumberAnimation { target: w; property: "markU"; to: 1; duration: w.reduced ? 0 : 860; easing.type: Easing.InOutCubic }
            }
            SequentialAnimation {
                PauseAnimation { duration: w.reduced ? 0 : 300 }
                NumberAnimation { target: w; property: "liftU"; to: 1; duration: w.reduced ? 0 : 620; easing.type: Easing.OutBack; easing.overshoot: 1.4 }
            }
        }
    }

    // ---- the next class, lifted ---------------------------------------------------------
    Item {
        id: heroBox
        width: w.width; height: w.heroH
        readonly property real u: Math.max(0, Math.min(1.08, w.liftU))
        // the shadow it casts as it lifts
        Rectangle {
            x: 6; width: parent.width - 12; height: parent.height; radius: 13
            y: 2 + 5 * heroBox.u
            color: "#000000"; opacity: 0.42 * Math.min(1, heroBox.u)
        }
        Rectangle {
            id: face
            width: parent.width; height: parent.height; radius: 13
            y: 14 * (1 - heroBox.u)
            opacity: Math.min(1, heroBox.u * 1.6)
            scale: 0.965 + 0.035 * heroBox.u
            color: Qt.rgba(1, 1, 1, w.hero ? 0.055 : 0.035)
            border.width: 1
            border.color: Qt.rgba(w.tone.r, w.tone.g, w.tone.b, w.fc ? 0.38 : 0.12)
            // a warm wash on its left edge, the class's colour
            Rectangle {
                x: 1; y: 1; width: parent.width * 0.55; height: parent.height - 2; radius: 12
                gradient: Gradient { orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Qt.rgba(w.tone.r, w.tone.g, w.tone.b, w.fc ? 0.13 : 0.05) }
                    GradientStop { position: 1; color: "transparent" } }
            }
            Column {
                x: 14; y: 9; width: parent.width - 14 - countCol.width - 26
                spacing: 2
                Text {
                    text: w.kicker
                    color: w.tone; font.family: "Segoe UI Variable Text"; font.pointSize: 7; font.weight: Font.DemiBold
                    font.letterSpacing: 1.3
                }
                Text {
                    width: parent.width; elide: Text.ElideRight
                    text: w.heroTitle
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 11.5; font.weight: Font.DemiBold
                }
                Row {
                    spacing: 7
                    Rectangle {
                        objectName: "roomChip"
                        visible: !!(w.hero && w.hero.room)
                        height: 16; radius: 5; width: roomT.implicitWidth + 12
                        color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.16)
                        anchors.verticalCenter: parent.verticalCenter
                        Text { id: roomT; anchors.centerIn: parent; text: w.hero && w.hero.room ? w.hero.room : ""
                            color: "#EC9A78"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5; font.weight: Font.DemiBold }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: w.heroWhen
                        color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5; font.features: { "tnum": 1 }
                    }
                }
            }
            Column {
                id: countCol
                visible: !!w.hero
                anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
                spacing: 0
                Text {
                    anchors.right: parent.right
                    visible: w.countLabel !== ""
                    text: w.countLabel
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5
                }
                Text {
                    objectName: "countdown"
                    anchors.right: parent.right
                    text: w.fc && w.remain <= 0 ? "now" : w.count(w.remain)
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 17; font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                }
            }
            // during a class: how far through it is (moves once a second)
            Rectangle {
                visible: !!w.fc && w.d.focus_state === "now" && w.isToday
                x: 14; y: parent.height - 7; width: parent.width - 28; height: 2; radius: 1
                color: Qt.rgba(1, 1, 1, 0.07)
                Rectangle {
                    height: 2; radius: 1; color: "#6CCB7A"
                    width: !w.fc ? 0 : parent.width * Math.max(0, Math.min(1,
                           (w.nowMin - w.mins(w.fc.start)) / Math.max(1, w.mins(w.fc.end) - w.mins(w.fc.start))))
                }
            }
        }
    }

    // ---- the day ribbon ---------------------------------------------------------------
    Item {
        id: ribbon
        visible: w.hasRibbon
        y: w.ribbonY; width: w.width; height: w.ribbonH
        opacity: Math.min(1, w.ribbonU * 1.4)
        transform: Translate { x: 28 * (1 - w.ribbonU) }
        readonly property real ty: 6
        readonly property real th: 18
        Rectangle { y: ribbon.ty; width: parent.width; height: ribbon.th; radius: 6; color: Qt.rgba(1, 1, 1, 0.04) }
        // the part of the day already gone, warming behind the marker
        Rectangle {
            visible: w.isToday
            y: ribbon.ty; height: ribbon.th; radius: 6
            width: Math.max(0, marker.x)
            gradient: Gradient { orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "transparent" }
                GradientStop { position: 1; color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.13) } }
        }
        Repeater {
            model: w.classes
            Rectangle {
                id: blk
                objectName: "classBlock"
                readonly property string st: w.stateOf(index)
                readonly property real x0: w.xOf(w.mins(modelData.start))
                readonly property real full: Math.max(4, w.xOf(w.mins(modelData.end)) - x0 - 2)
                // each block unrolls from its start, a little after the one before
                readonly property real u: Math.max(0, Math.min(1, (w.ribbonU - 0.12 - index * 0.07) / 0.5))
                x: x0 + 1; y: ribbon.ty + 2; height: ribbon.th - 4; radius: 4
                width: full * (w.reduced ? 1 : (1 - Math.pow(1 - u, 3)))
                color: st === "now" ? "#6CCB7A" : st === "next" ? "#D97757" : st === "done" ? Qt.rgba(1, 1, 1, 0.10) : Qt.rgba(1, 1, 1, 0.20)
                border.width: st === "next" ? 1 : 0
                border.color: "#EC9A78"
                clip: true
                Text {
                    visible: blk.full > 30
                    anchors.verticalCenter: parent.verticalCenter; x: 5; width: blk.full - 8
                    elide: Text.ElideRight
                    text: modelData.code || modelData.title
                    opacity: blk.u
                    color: blk.st === "now" || blk.st === "next" ? "#141414" : blk.st === "done" ? "#7a7672" : "#D8D8D8"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 6.5; font.weight: Font.DemiBold
                }
            }
        }
        // the hours
        Repeater {
            model: Math.floor((w.hi - w.lo) / w.step) + 1
            Item {
                readonly property real m: w.lo + index * w.step
                x: w.xOf(m); y: ribbon.ty + ribbon.th + 2
                Rectangle { x: -0.5; width: 1; height: 3; color: Qt.rgba(1, 1, 1, 0.16) }
                Text {
                    y: 3
                    x: index === 0 ? 0 : (parent.x + implicitWidth / 2 > w.width ? -implicitWidth : -implicitWidth / 2)
                    text: Math.floor(parent.m / 60) % 24
                    color: "#6f6a66"; font.family: "Segoe UI Variable Text"; font.pointSize: 7; font.features: { "tnum": 1 }
                }
            }
        }
        // now: a hot line with a bead, gliding in from the morning
        Item {
            id: marker
            objectName: "nowMarker"
            visible: w.isToday
            x: w.xOf(w.lo + (w.nowMin - w.lo) * w.markU)
            Rectangle { x: -0.75; y: ribbon.ty - 4; width: 1.5; height: ribbon.th + 8; radius: 0.75; color: "#EC9A78" }
            Rectangle { x: -3.5; y: ribbon.ty - 7; width: 7; height: 7; radius: 3.5; color: "#EC9A78"
                border.width: 1.5; border.color: "#1a1412" }
        }
    }

    // ---- the rest of the day --------------------------------------------------------------
    Repeater {
        model: w.restShown
        Rise {
            i: index + 3; reduced: w.reduced
            y: w.listY + index * 22
            Item {
                objectName: "restRow"
                readonly property var c: w.rest[index]
                readonly property bool more: index === 2 && w.rest.length > 3
                width: w.width; height: 22
                Text {
                    width: 40; anchors.verticalCenter: parent.verticalCenter
                    text: parent.c.start
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5; font.features: { "tnum": 1 }
                }
                Rectangle { x: 44; width: 5; height: 5; radius: 2.5; anchors.verticalCenter: parent.verticalCenter
                    color: Qt.rgba(1, 1, 1, 0.28) }
                Text {
                    x: 58; width: parent.width - 58 - roomR.implicitWidth - 12; anchors.verticalCenter: parent.verticalCenter
                    elide: Text.ElideRight
                    text: parent.c.title + (parent.more ? "  ·  +" + (w.rest.length - 3) + " more" : "")
                    color: "#E4E4E4"; font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
                }
                Text {
                    id: roomR
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    text: parent.c.room || ""
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
                }
            }
        }
    }
}
