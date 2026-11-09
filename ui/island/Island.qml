import QtQuick
import QtQuick.Shapes
import "engine.js" as E

// The hey-claude island, "Ember & Thread" (docs/island-motion.md).
// One fixed transparent window; the pill animates inside it. `model` is the
// Python IslandModel (heyclaude/island_qml.py): commands come in through
// model.cmd(fn, args), clicks and keys go back through its slots.
Item {
    id: root
    width: 600; height: 400

    readonly property color cFg: "#F2F2F2"
    readonly property color cDim: "#9A9A9A"
    readonly property color cLive: "#D97757"
    readonly property color cHot: "#EC9A78"
    readonly property color cOk: "#6CCB7A"
    readonly property color cBad: "#E5646A"
    readonly property string family: "Segoe UI Variable Text"

    property var o: ({ w: 150, h: 28, head: 28, cy: 14, ex: 18, er: 4, rad: 14, hide: 1, lb: [], suf: [], rows: [], words: [], sats: [], sparks: [], dots: [0, 0, 0] })
    property real band: model ? model.band : 0
    // island.scale: everything is laid out in island units on a 600 x 400 stage and
    // the stage is scaled (vector / scene graph all the way: text, Shapes, shaders)
    property real uiScale: model ? model.uiScale : 1
    readonly property real stageW: 600
    readonly property real stageH: 400
    onUiScaleChanged: { maskSent = { x: -1, y: -1, w: -1, h: -1 }; kick() }
    property int topMargin: model ? model.topMargin : -1
    property bool software: model ? model.software : false
    property real fps: 0
    property int frameCount: 0
    property string clockMode: model ? model.clockMode : "real"
    property real fakeNow: 0
    property bool inputOpen: false
    property string attachment: ""

    // Where the pill sits in the window: centred, and vertically centred in a
    // bar docked at the top of the screen (top_margin: auto), else 8 px.
    readonly property real pillX: Math.round((stageW - o.w) / 2)
    // centred in a bar docked at the top (band is in screen px, the pill in island units)
    readonly property real pillY: topMargin >= 0 ? topMargin / uiScale
        : (band >= 20 ? Math.max(0, (band / uiScale - o.head) / 2) : 8)

    FontMetrics { id: fm9; font.family: root.family; font.pointSize: 9 }
    FontMetrics { id: fm10; font.family: root.family; font.pointSize: 10 }

    function rgba(c, a) { return Qt.rgba(c[0] / 255, c[1] / 255, c[2] / 255, a) }
    function hexa(c, a) {
        function h2(v) { var s = Math.round(Math.max(0, Math.min(255, v))).toString(16); return s.length < 2 ? "0" + s : s }
        return "#" + h2(a * 255) + h2(c[0]) + h2(c[1]) + h2(c[2])
    }
    function now() { return clockMode === "fake" ? fakeNow : Date.now() }

    Component.onCompleted: {
        E.init({
            now: root.now,
            tw: function (s, big) { return (big ? fm10 : fm9).advanceWidth(s) },
            log: function (m) { if (model) model.log(m); else console.log(m) },
            pinList: false, suspended: false
        })
        if (model) model.ready()
        tick()
    }

    // -- the driver: exactly one of FrameAnimation / Timer / nothing ----------------
    property real lastGap: 0
    property real lastTier: -2
    function kick() {
        if (frames.running) return
        rest.stop()
        frames.start()
    }
    // Frame pacing for the UX recorder: while the engine wants every frame (live tier),
    // ticks further apart than ~2 frames are hitches. Bounded; drained by takeHitches().
    property string rowKey: ""
    property int sentRowMax: -1
    property real lastTickMs: 0
    property var hitches: []
    property int liveTicks: 0
    function tick() {
        var tnow = Date.now()
        if (root.lastGap >= 0 && root.lastGap <= 40 && root.lastTickMs > 0) {   // live tier (60 fps, or 30 on battery/software)
            root.liveTicks++
            var dms = tnow - root.lastTickMs
            if (dms > 2 * Math.max(16, root.lastGap) + 2 && root.hitches.length < 4000) root.hitches.push([tnow, dms])
        }
        root.lastTickMs = tnow
        var gap = E.frame()
        root.o = E.out
        root.oChanged()                         // same object every frame: tell the bindings
        body.apply(E.out)
        applyLabels()
        frameCount++
        updateMask()
        lastGap = gap
        if (gap === 0) { if (!frames.running) { frames.triggeredOnStart = false; frames.start(); frames.triggeredOnStart = true } rest.stop() }
        else {
            frames.running = false
            if (gap === Infinity) rest.stop()
            else { rest.interval = Math.max(1, Math.round(gap)); rest.restart() }
        }
        var tier = gap === Infinity ? -1 : gap
        if (model && tier !== root.lastTier) { root.lastTier = tier; model.frame(tier) }   // only when the tier changes
    }
    // Live tier: a ~60 Hz timer. (A running FrameAnimation makes Qt draw every vsync,
    // 144 a second on this laptop's panel, even when nothing changed.)
    Timer {
        id: frames
        interval: 13; repeat: true; running: false   // every 2nd vsync on 144 Hz, every vsync on 60 Hz
        triggeredOnStart: true                  // the first frame of a wake lands at once
        onTriggered: root.tick()
    }
    Timer { id: rest; repeat: false; onTriggered: root.tick() }

    // Commands from Python: model.cmd("go", ["ready", {}]) etc.
    Connections {
        target: model
        function onCmd(fn, args) {
            if (fn === "clock") { root.clockMode = args[0]; root.fakeNow = args[1]; return }
            if (fn === "step") { root.fakeNow = args[0]; root.tick(); return }
            if (fn === "openInput") { root.openInput(args[0] || ""); return }
            if (fn === "closeInput") { root.closeInput(args[0]); return }
            if (fn === "inputText") { field.text = args[0]; field.cursorPosition = field.length; return }
            if (fn === "pinList") { E.env.pinList = !!args[0]; root.kick(); return }
            if (fn === "suspend") { E.env.suspended = !!args[0]; root.kick(); return }
            var f = E[fn]
            if (typeof f !== "function") { model.log("[island] unknown command " + fn); return }
            f.apply(null, args)
            root.kick()
        }
    }

    // -- click-through mask: the pill (+2 px); during a morph, the union of where it
    // is and where it's going. Sent only when it changes, never per frame.
    property var maskSent: ({ x: -1, y: -1, w: -1, h: -1 })
    function updateMask() {
        if (!model) return
        var m = E.maskRect(), x, y, w, h
        if (o.hide < 0.02 && !inputOpen) {               // hidden when idle: a thin strip to peek from
            w = 220; h = 4; x = Math.round((stageW - w) / 2); y = 0
        } else {
            w = Math.ceil(m.w); h = Math.ceil(m.h) + 2
            x = Math.floor((stageW - w) / 2); y = Math.max(0, Math.floor(pillY - 2))
            var s = maskSent
            if (!m.settled && s.w >= 0) {                // grow-only while it moves
                var x2 = Math.max(x + w, s.x + s.w), y2 = Math.max(y + h, s.y + s.h)
                x = Math.min(x, s.x); y = Math.min(y, s.y); w = x2 - x; h = y2 - y
            }
        }
        var s2 = maskSent
        if (x !== s2.x || y !== s2.y || w !== s2.w || h !== s2.h) {
            maskSent = { x: x, y: y, w: w, h: h }
            var S = uiScale                          // island units -> window pixels
            model.setMask(Math.floor(x * S), Math.floor(y * S), Math.ceil(w * S) + 1, Math.ceil(h * S) + 1)
        }
    }

    // -- labels: two layers each, from the engine's label lists ---------------------
    function applyLabels() {
        var L = o.lb || [], S = o.suf || []
        setLabel(lb0, L.length > 0 ? L[0] : null, 0)
        setLabel(lb1, L.length > 1 ? L[1] : null, 0)
        setLabel(suf0, S.length > 0 ? S[0] : null, o.sufX)
        setLabel(suf1, S.length > 1 ? S[1] : null, o.sufX)
        // speaking words (StyledText per line; only the newest word is warm)
        var lines = ["", ""]
        var W = o.words || []
        for (var i = 0; i < W.length; i++) {
            var wd = W[i]
            lines[wd.line] += (lines[wd.line] ? " " : "") + "<font color=\"" + hexa(wd.col, wd.a) + "\">" + escapeHtml(wd.w) + "</font>"
        }
        if (words0.text !== lines[0]) words0.text = lines[0]
        if (words1.text !== lines[1]) words1.text = lines[1]
        // in a turn the answer is revealed in the row under the question instead
        if (extras.rowMax !== root.sentRowMax) { root.sentRowMax = extras.rowMax; E.setRowMax(extras.rowMax) }
        var R = o.row
        if (R && R.key !== root.rowKey) {
            root.rowKey = R.key
            extras.rowLines = R.lines.slice(0, R.n)
            if (extras.rowN !== R.n) extras.rowN = R.n
            if (extras.rowTop !== R.top) extras.rowTop = R.top
        }
    }
    function escapeHtml(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }
    function setLabel(item, l, xo) {
        if (!l || l.a < 0.01) { item.visible = false; return }
        item.visible = true
        item.text = l.text
        item.big = !!l.big
        var dimC = l.color[0] === 154 && l.color[1] === 154
        var c = dimC && o.hov > 0.005 ? [154 + (242 - 154) * o.hov, 154 + (242 - 154) * o.hov, 154 + (242 - 154) * o.hov] : l.color
        item.color = rgba(c, 1)
        item.opacity = l.a
        item.x = l.x + xo + (o.trem || 0)
        var fm = l.big ? fm10 : fm9
        item.y = o.cy + (l.big ? 4.6 : 4.2) + (l.yo || 0) + l.dy - fm.ascent
        item.reveal = l.nib && l.rev < 1 ? l.rev : 1
        item.icon = l.icon || ""
    }

    Item {
    id: stage
    width: root.stageW; height: root.stageH
    scale: root.uiScale
    transformOrigin: Item.TopLeft
    // ==================================================================== the pill
    Item {
        id: pill
        x: root.pillX; y: root.pillY
        width: o.w; height: o.h
        opacity: o.hide
        visible: o.hide > 0.004

        Body {
            id: body
            objectName: "body"
            x: -1; y: -1; width: parent.width + 2; height: parent.height + 2
            software: root.software
            aa: 0.6 / (Math.max(1, Screen.devicePixelRatio) * root.uiScale)   // AA in device pixels
        }

        // ---- small vector bits on top of the body --------------------------------
        // off: 2 px slash over a 3 px underlay; holding-muted reuses it
        Shape {
            id: slashShape
            readonly property real t: Math.max(o.slash || 0, o.muteSlash || 0)
            visible: t > 0.01
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath { strokeColor: "#080808"; strokeWidth: 3.2; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                startX: o.ex - 5; startY: o.cy + 5
                PathLine { x: o.ex - 5 + 10 * slashShape.t; y: o.cy + 5 - 10 * slashShape.t } }
            ShapePath { strokeColor: root.cDim; strokeWidth: 2; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                startX: o.ex - 5; startY: o.cy + 5
                PathLine { x: o.ex - 5 + 10 * slashShape.t; y: o.cy + 5 - 10 * slashShape.t } }
        }
        // paused: pilot light + resume arc (fills clockwise from 12 o'clock)
        Rectangle {
            visible: (o.pilotA || 0) > 0.01
            x: o.ex - 1.5; y: o.cy - 1.5; width: 3; height: 3; radius: 1.5
            color: root.cLive; opacity: o.pilotA || 0
        }
        Shape {
            visible: (o.arcA || 0) > 0.01
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath { strokeColor: Qt.rgba(92 / 255, 86 / 255, 82 / 255, 0.5 * (o.arcA || 0) / 0.35 * 0.5); strokeWidth: 1; fillColor: "transparent"
                PathAngleArc { centerX: o.ex; centerY: o.cy; radiusX: 7.5; radiusY: 7.5; startAngle: 0; sweepAngle: 360 } }
            ShapePath { strokeColor: Qt.rgba(217 / 255, 119 / 255, 87 / 255, o.arcA || 0); strokeWidth: 1.2; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                PathAngleArc { centerX: o.ex; centerY: o.cy; radiusX: 7.5; radiusY: 7.5; startAngle: -90; sweepAngle: 360 * Math.max(0.001, o.arcFill || 0) } }
        }
        // holding: two brackets pin the thread
        Shape {
            id: brackets
            visible: (o.bracketA || 0) > 0.01
            anchors.fill: parent
            readonly property real bh: o.bracketH || 0
            readonly property real off: o.bracketOff || 0
            readonly property color bc: (o.bracketMuted || 0) > 0.5 ? Qt.rgba(154 / 255, 154 / 255, 154 / 255, o.bracketA || 0) : Qt.rgba(217 / 255, 119 / 255, 87 / 255, o.bracketA || 0)
            ShapePath { strokeColor: brackets.bc; strokeWidth: 1.5; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin; fillColor: "transparent"
                startX: 34 - brackets.off + 3; startY: o.cy - brackets.bh
                PathLine { x: 34 - brackets.off; y: o.cy - brackets.bh }
                PathLine { x: 34 - brackets.off; y: o.cy + brackets.bh }
                PathLine { x: 34 - brackets.off + 3; y: o.cy + brackets.bh } }
            ShapePath { strokeColor: brackets.bc; strokeWidth: 1.5; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin; fillColor: "transparent"
                startX: 214 + brackets.off - 3; startY: o.cy - brackets.bh
                PathLine { x: 214 + brackets.off; y: o.cy - brackets.bh }
                PathLine { x: 214 + brackets.off; y: o.cy + brackets.bh }
                PathLine { x: 214 + brackets.off - 3; y: o.cy + brackets.bh } }
        }
        // task done: the check (4 px down-right, then 9 px up-right), trimmed on
        Shape {
            id: check
            visible: (o.check || 0) > 0.01
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            readonly property real x0: o.ex + 10
            readonly property var pts: {
                var t = o.check || 0, l1 = Math.sqrt(18), l2 = Math.sqrt(2 * 6.4 * 6.4), L = (l1 + l2) * t, cy = o.cy
                if (L <= l1) { var f = L / l1; return [Qt.point(x0, cy), Qt.point(x0 + 3 * f, cy + 3 * f)] }
                var g = (L - l1) / l2
                return [Qt.point(x0, cy), Qt.point(x0 + 3, cy + 3), Qt.point(x0 + 3 + 6.4 * g, cy + 3 - 6.4 * g)]
            }
            ShapePath { strokeColor: Qt.rgba(108 / 255, 203 / 255, 122 / 255, o.checkA || 1); strokeWidth: o.checkW || 1.5
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin; fillColor: "transparent"
                PathPolyline { path: check.pts } }
        }
        // task satellites + kindling sparks
        Repeater {
            model: o.sats ? o.sats.length : 0
            Rectangle {
                readonly property var s: o.sats[index] || ({ x: 0, y: 0, sz: 1, a: 0, off: false })
                x: s.x - s.sz * 1.4; y: s.y - s.sz * 1.4; width: s.sz * 2.8; height: width; radius: width / 2
                color: s.off ? "#5C5652" : root.cHot; opacity: s.a
                border.width: s.off ? 0 : 0.6; border.color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.8)
            }
        }
        Repeater {
            model: o.sparks ? o.sparks.length : 0
            Rectangle {
                readonly property var s: o.sparks[index] || ({ x: 0, y: 0, a: 0 })
                x: s.x - 1; y: s.y - 1; width: 2; height: 2; radius: 1; color: root.cLive; opacity: s.a
            }
        }

        // ---- text, clipped to the pill ------------------------------------------
        Item {
            id: textLayer
            anchors.fill: parent
            clip: true
            Label { id: lb0 }
            Label { id: lb1 }
            Label { id: suf0 }
            Label { id: suf1 }
            // followup: three breathing dots after "go on"
            Repeater {
                model: 3
                Rectangle {
                    visible: (o.dotsA || 0) > 0.01
                    x: 216 + fm9.advanceWidth("go on") + 2 + index * 4.5 - 1.1; y: o.cy + 4.2 - 1 - 1.1
                    width: 2.2; height: 2.2; radius: 1.1; color: root.cFg
                    opacity: (o.dotsA || 0) * (o.dots ? o.dots[index] : 0)
                }
            }
            // speaking: the answer, newest two lines
            Text {
                id: words0
                x: 84; y: (o.twoLines ? 24 : o.cy + 4.6) - fm10.ascent
                visible: (o.wordsA || 0) > 0.01
                textFormat: Text.StyledText; font.family: root.family; font.pointSize: 10
                width: Math.max(10, o.w - 84 - 14 - 66); elide: Text.ElideRight
            }
            Text {
                id: words1
                x: 84; y: 42 - fm10.ascent
                visible: (o.wordsA || 0) > 0.01 && o.twoLines
                textFormat: Text.StyledText; font.family: root.family; font.pointSize: 10
                width: Math.max(10, o.w - 84 - 14 - 66); elide: Text.ElideRight
            }
            // talk-or-type: the field (focus only while it's open)
            Text {
                visible: (o.field || 0) > 0.01 && field.length === 0
                x: 44 + (o.fieldX || 0); y: field.y + field.topPadding
                text: root.attachment ? "ask about " + root.attachment + "…" : "type or talk…"
                font.family: root.family; font.pointSize: 10; color: root.cDim; opacity: o.field || 0
                elide: Text.ElideRight; width: o.w - 44 - 20
            }
            TextEdit {
                id: field
                x: 44 + (o.fieldX || 0); y: 13
                width: Math.max(40, o.w - 44 - 20)
                visible: (o.field || 0) > 0.01
                opacity: o.field || 0
                font.family: root.family; font.pointSize: 10
                color: root.cFg
                selectionColor: root.cLive; selectedTextColor: "#170c07"
                wrapMode: TextEdit.Wrap
                activeFocusOnPress: true
                cursorDelegate: Rectangle { width: 1.5; color: root.cLive; opacity: o.caretA || 1 }
                onTextChanged: if (root.inputOpen && !root.settingText) { E.typeKey(); E.setInputLines(Math.max(1, lineCount)); root.kick(); if (model) model.inputEdited(text) }
                onLineCountChanged: if (root.inputOpen) { E.setInputLines(lineCount); root.kick() }
                Keys.onPressed: function (ev) {
                    if (!model) return
                    if (ev.key === Qt.Key_Return || ev.key === Qt.Key_Enter) {
                        if (model.inputEnter(field.text, (ev.modifiers & Qt.ShiftModifier) !== 0)) ev.accepted = true
                    } else if (ev.key === Qt.Key_Escape) {
                        model.inputEscape(); ev.accepted = true
                    }
                }
            }
            // the typed text, fed to the ember on submit
            Text {
                visible: (o.ghost || 0) > 0.01
                x: 44 + (o.ghostX || 0); y: o.head / 2 + 1.6 - fm10.ascent
                text: o.ghostText || ""; color: root.cFg; opacity: o.ghost || 0
                font.family: root.family; font.pointSize: 10
                scale: 0.96 + 0.04 * (o.ghost || 0); transformOrigin: Item.Left
                width: o.w - 60; elide: Text.ElideRight
            }
        }

        // ---- mic + stop (thinking / speaking), not-me chip (listening after a wake) --
        Item {
            id: ctl
            objectName: "ctl"
            visible: (o.ctl || 0) > 0.01
            opacity: o.ctl || 0
            anchors.fill: parent
            CtlButton { kind: "mic"; cx: o.w - 56; cy: o.ctlCy; hover: o.hm; press: o.pressM }
            CtlButton { kind: "stop"; cx: o.w - 26; cy: o.ctlCy; hover: o.hs; press: o.pressS }
        }
        Rectangle {
            id: notMe
            visible: (o.notme || 0) > 0.01
            opacity: o.notme || 0
            width: 62; height: 22; radius: 11
            x: o.w - 14 - width; y: Math.min(o.head, 40) / 2 - 11
            color: Qt.rgba(1, 1, 1, 0.08 + 0.10 * (o.hn || 0))
            InkText { text: "not me"; font.family: root.family; font.pointSize: 9
                color: (o.hn || 0) > 0.5 ? root.cFg : root.cDim }
        }

        // ---- hover task list + plan usage ------------------------------------------
        Item {
            id: listLayer
            visible: o.listGeom === true
            x: 0; y: o.head + 6; width: o.w; height: o.h - o.head - 6
            clip: true
            Repeater {
                model: o.rows ? o.rows.length : 0
                Item {
                    readonly property var r: o.rows[index] || ({ name: "", status: "", right: "", elapsed: "", a: 0, dy: 0, b: 0.5 })
                    y: index * 30; width: listLayer.width; height: 30
                    opacity: r.a
                    Rectangle { visible: index > 0; x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.05) }
                    Item {
                        y: r.dy; width: parent.width; height: 30
                        Rectangle { visible: r.status === "running"; x: 18 - width / 2; y: 15 - height / 2
                            width: 2 * (2.5 + 0.5 * r.b); height: width; radius: width / 2
                            color: Qt.rgba((184 + (217 - 184) * r.b) / 255, (102 + (119 - 102) * r.b) / 255, (76 + (87 - 76) * r.b) / 255, 1) }
                        Shape {
                            visible: r.status !== "running"
                            ShapePath { strokeColor: r.status === "done" ? root.cOk : (r.status === "failed" ? root.cBad : root.cDim)
                                strokeWidth: 1.5; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin; fillColor: "transparent"
                                PathPolyline { path: r.status === "done" ? [Qt.point(14.5, 15), Qt.point(17, 17.5), Qt.point(22, 12.5)]
                                                                         : [Qt.point(15, 12), Qt.point(21, 18)] } }
                        }
                        Text { x: 34; width: Math.max(40, parent.width - 34 - 160); y: 15 - height / 2; text: r.name; color: root.cFg
                            font.family: root.family; font.pointSize: 9; elide: Text.ElideRight }
                        Text { x: parent.width - 150; width: 100; y: 15 - height / 2; text: r.right; color: root.cDim
                            font.family: root.family; font.pointSize: 9; elide: Text.ElideRight }
                        Text { x: parent.width - 14 - width; y: 15 - height / 2; text: r.elapsed; color: root.cDim
                            font.family: root.family; font.pointSize: 9 }
                    }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: if (model) model.openTask(index) }
                }
            }
            UsageRow {
                visible: model && model.usageOn && (o.usageA || 0) > 0.01
                opacity: o.usageA || 0
                y: (o.rows ? o.rows.length : 0) * 30
                width: listLayer.width
            }
        }

        // ---- under the head: effects, approvals, response widgets ----------------
        Item {
            id: extrasClip
            x: 0; y: o.headFull || o.head
            width: o.w; height: Math.max(0, o.h - y)
            clip: true
            visible: !o.listGeom && extras.contentHeight > 0
            Extras {
                id: extras
                // Laid out on the pill's target width and held still on screen while the pill
                // morphs around it: the card and the answer never re-flow or slide per frame.
                readonly property real contentW: Math.round(o.wt || o.w)
                x: Math.round((root.stageW - contentW) / 2) - root.pillX
                y: 0
                width: contentW
                resting: o.state === "ready" || o.state === "off" || o.state === "paused"
                reduced: o.motion !== "full"
                engineState: o.state || ""
                // the panel never grows past the window (the stage's bottom, less room for the glow)
                maxHeight: root.stageH - root.pillY - (o.headFull || o.head) - 18
                clip: true
                onContentHeightChanged: root.extraChanged()
                onMinWidthChanged: root.extraChanged()
                onTurnCleared: { E.setTurn(false); root.kick() }
            }
        }
        Connections { target: model; function onQuestionShown(q) { if (q) { E.newTurn(); root.kick() } } }
        // screenshot effect: a shutter flash over the whole pill
        Rectangle {
            anchors.fill: parent; radius: o.rad
            visible: extras.flash > 0.005
            color: "white"; opacity: extras.flash
        }
        // followup: the window's countdown, a ring around the ember draining clockwise
        // from 12 o'clock (the paused arc's look); nothing sweeps across the pill
        Shape {
            id: followupDrain
            objectName: "followupDrain"
            visible: (o.drainA || 0) > 0.01
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath { strokeColor: Qt.rgba(92 / 255, 86 / 255, 82 / 255, 0.35 * (o.drainA || 0)); strokeWidth: 1; fillColor: "transparent"
                PathAngleArc { centerX: o.ex; centerY: o.cy; radiusX: 9.5; radiusY: 9.5; startAngle: 0; sweepAngle: 360 } }
            ShapePath { strokeColor: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.9 * (o.drainA || 0)); strokeWidth: 1.4
                capStyle: ShapePath.RoundCap; fillColor: "transparent"
                PathAngleArc { centerX: o.ex; centerY: o.cy; radiusX: 9.5; radiusY: 9.5; startAngle: -90
                    sweepAngle: 360 * Math.max(0.001, o.drainU || 0) } }
        }

        // ---- tools in use (thinking): service icons, stacked for parallel calls --------
        Item {
            id: ticker
            visible: o.state === "thinking" && tools.length > 0
            property var tools: []
            x: 78; y: o.cy - 8; width: 28; height: 16
            opacity: visible ? 1 : 0
            // Telegram: a quick shuffle of chat bubbles behind its icon while it reads
            Repeater {
                model: ticker.tools.some(function (t) { return t.service === "Telegram" }) ? 3 : 0
                Rectangle {
                    width: 7; height: 5; radius: 2.5; color: Qt.rgba(1, 1, 1, 0.35)
                    property real ph: index * 2.1
                    x: 4 + 7 * Math.sin(shuffle.t * 6.28 + ph); y: 5 + 5 * Math.cos(shuffle.t * 6.28 * 1.3 + ph)
                }
            }
            NumberAnimation { id: shuffle; property real t: 0; target: shuffle; property: "t"; from: 0; to: 1; duration: 900
                loops: Animation.Infinite; running: ticker.visible && ticker.tools.some(function (t) { return t.service === "Telegram" }) }
            Repeater {
                model: ticker.tools.slice(-3)
                ServiceIcon {
                    id: tic
                    x: index * 5; y: 0; size: 16
                    url: modelData.icon_url || ""; glyph: modelData.glyph || ""; initials: modelData.initials || ""
                    opacity: 0
                    Component.onCompleted: slideIn.start()
                    property real dx: 8
                    transform: Translate { x: dx }
                    ParallelAnimation { id: slideIn
                        NumberAnimation { target: tic; property: "opacity"; to: 1; duration: 180; easing.type: Easing.OutCubic }
                        NumberAnimation { target: tic; property: "dx"; to: 0; duration: 220; easing.type: Easing.OutCubic } }
                }
            }
            Connections { target: model; function onToolsChanged(list) { ticker.tools = (model && model.iconsOn === false) ? [] : list } }
        }

        // ---- pointer --------------------------------------------------------------
        MouseArea {
            id: mouse
            anchors.fill: parent
            z: -1
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: root.hitCtl(mouseX, mouseY) || (o.state === "speaking") || (o.state === "ready" && !(o.rows && o.rows.length)) || o.state === "off" ? Qt.PointingHandCursor : Qt.ArrowCursor
            onEntered: { E.setHover(true); root.kick(); if (model) model.hover(true) }
            onExited: { E.setHover(false); E.setCtlHover(null); root.kick(); if (model) model.hover(false) }
            onPositionChanged: function (m) { E.setCtlHover(root.hitCtl(m.x, m.y)); root.kick() }
            onClicked: function (m) {
                if (!model) return
                if (m.button === Qt.RightButton) { model.contextMenu(); return }
                var k = root.hitCtl(m.x, m.y)
                if (k === "mic") { E.press("mic"); root.kick(); model.interruptTalk(); return }
                if (k === "stop") { E.press("stop"); root.kick(); model.stopSpeaking(); return }
                if (k === "notme") { model.notMe(); return }
                if (m.y > o.head) return
                if (o.state === "speaking") { E.press("stop"); root.kick(); model.stopSpeaking() }
                else if (o.state === "ready" || o.state === "off" || o.state === "paused") model.activate()
            }
        }
        DropArea {
            anchors.fill: parent
            keys: ["text/uri-list"]
            onEntered: function (d) { E.setDrop(true); root.kick(); d.accept(Qt.CopyAction) }
            onExited: { E.setDrop(false); root.kick() }
            onDropped: function (d) {
                E.setDrop(false); root.kick()
                var urls = []
                for (var i = 0; i < d.urls.length; i++) urls.push(d.urls[i].toString())
                if (model && urls.length) model.filesDropped(urls)
                d.acceptProposedAction()
            }
        }
    }
    // hidden when idle: the pointer touching the top-centre strip peeks it out
    MouseArea {
        visible: o.hide < 0.02 && !root.inputOpen
        x: (root.stageW - 220) / 2; y: 0; width: 220; height: 4
        hoverEnabled: true
        onEntered: { E.setPeek(true); root.kick(); peekTimer.stop() }
    }
    Timer { id: peekTimer; interval: 900; onTriggered: { E.setPeek(false); root.kick() } }
    Connections { target: mouse; function onExited() { peekTimer.restart() } function onEntered() { peekTimer.stop() } }
    }   // stage

    function hitCtl(x, y) {
        if ((o.ctl || 0) > 0.5 && (o.state === "thinking" || o.state === "speaking")) {
            var cy = o.ctlCy
            if (Math.hypot(x - (o.w - 26), y - cy) <= 15) return "stop"
            if (Math.hypot(x - (o.w - 56), y - cy) <= 15) return "mic"
        }
        if ((o.notme || 0) > 0.5 && x >= o.w - 14 - 62 - 3 && x <= o.w - 14 + 3 && Math.abs(y - Math.min(o.head, 40) / 2) <= 14) return "notme"
        return null
    }

    function extraChanged() { E.setExtra(extras.contentHeight, extras.contentHeight > 0 ? extras.minWidth : 0, false); kick() }

    // -- talk-or-type field ------------------------------------------------------------
    property bool settingText: false
    function openInput(att) {
        root.attachment = att || ""
        root.inputOpen = true
        root.settingText = true; field.text = ""; root.settingText = false
        E.go("input", {}); E.setInputLines(1)
        field.forceActiveFocus()                     // focus at 0 ms, before any visual
        kick()
    }
    function closeInput(submitted) {
        if (submitted && field.text.trim().length) E.submitInput(field.text.trim())
        root.inputOpen = false
        root.attachment = ""
        field.focus = false
        kick()
    }

    // debug: the rest invariant (section 11 check 2), asked for by Python 1 s after a rest state
    function restCheck() {
        return { frameAnimation: frames.running, timer: rest.running, interval: rest.interval, layer: false,
                 extrasAnimating: extras.animating, state: o.state }
    }
    function engineStats() { var s = E.stats(); return { nan: s.nan, frames: s.frames, maxEdge: s.maxEdge, state: E.stateName() } }
    function snapshot() { return E.snapshotForTest() }
    function busyWhy() { return E.busyWhy() + " | frames.running " + frames.running + " rest " + rest.running + "/" + rest.interval }
    function extrasInfo() { var i = extras.info(); i.pillW = o.w; i.pillH = o.h; i.head = o.head; return i }
    function hit(x, y) { return hitCtl(x, y) }
    function probeUx() { var p = E.probeForUx(); var i = extras.info(); p.question = i.question; p.answer = i.answer;
        p.widget = i.widget; p.widgetReady = i.widgetReady; p.pending = i.pending; p.extrasH = i.height; p.tier = root.lastGap === Infinity ? -1 : root.lastGap
        p.ctl = o.ctl || 0; p.notme = o.notme || 0; p.ctlCy = o.ctlCy; p.pillX = root.pillX; p.pillY = root.pillY; p.ow = o.w; p.oh = o.h
        return p }
    function takeHitches() { var h = root.hitches; root.hitches = []; var n = root.liveTicks; root.liveTicks = 0; return { hitches: h, live: n } }
    function probe() { return { er: o.er, w: o.w, h: o.h, state: o.state, glowA: o.glowA, hide: o.hide,
                                thrA: o.thrA || 0, washA: o.washA || 0, dotsA: o.dotsA || 0, drainA: o.drainA || 0, drainU: o.drainU || 0,
                                labels: (o.lb || []).filter(function (l) { return !l.out && (l.a || 0) > 0.05 }).map(function (l) { return l.text }) } }
    function measure(s, big) { return (big ? fm10 : fm9).advanceWidth(s) }
}
