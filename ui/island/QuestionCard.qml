import QtQuick

// Claude asks (M7, AskUserQuestion from a watched session): each question with
// its options as pills. A single-select question keeps one pick (radio-like), a
// multiSelect one toggles. Submit is enabled once every question has an answer
// and sends {question text: label | [labels]} (QmlIsland.approval_answered);
// "In terminal" leaves it to the terminal (approval_decided(id, "pass")).
Item {
    id: card
    objectName: "questionCard"
    property var req: ({})
    signal decided(string action)
    signal answered(var answers)
    readonly property var questions: req.questions || []
    // picks[i] = the labels chosen for question i
    property var picks: ({})
    readonly property bool complete: {
        if (!questions.length) return false
        for (var i = 0; i < questions.length; i++) if (!picks[i] || !picks[i].length) return false
        return true
    }
    readonly property real textX: 50
    readonly property real textW: width - textX - 16
    height: footer.y + footer.height + 10
    opacity: 0
    Component.onCompleted: fade.start()
    NumberAnimation { id: fade; target: card; property: "opacity"; from: 0; to: 1; duration: 200; easing.type: Easing.OutCubic }

    FontMetrics { id: fmP; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    function picked(qi, label) { var p = picks[qi]; return !!p && p.indexOf(label) >= 0 }
    function toggle(qi, label) {
        var q = questions[qi]; if (!q) return
        var n = Object.assign({}, picks), cur = (n[qi] || []).slice()
        var at = cur.indexOf(label)
        if (q.multiSelect) { if (at >= 0) cur.splice(at, 1); else cur.push(label) }
        else cur = at >= 0 ? [] : [label]
        n[qi] = cur
        picks = n
    }
    function payload() {
        var out = {}
        for (var i = 0; i < questions.length; i++) {
            var q = questions[i], p = picks[i] || []
            out[q.question] = q.multiSelect ? p.slice() : (p.length ? p[0] : "")
        }
        return out
    }
    function submit() { if (complete) answered(payload()) }
    function short(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s }

    Rectangle { x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.1) }
    Rectangle {
        x: 22; y: 11; width: 16; height: 16; radius: 8; color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.22)
        Text { anchors.centerIn: parent; text: "?"; color: "#EC9A78"; font.bold: true; font.pixelSize: 11; font.family: "Segoe UI Variable Text" }
    }
    Text {
        id: title
        x: card.textX; y: 9; width: card.textW - (sess.visible ? sess.width + 10 : 0)
        text: card.req.title || "Claude asks"; color: "#F2F2F2"; elide: Text.ElideRight
        font.family: "Segoe UI Variable Text"; font.pointSize: 10
    }
    Text {
        id: sess
        visible: !!card.req.session
        x: card.width - 16 - width; y: title.y + 2
        width: Math.min(implicitWidth, card.textW * 0.4)
        text: card.req.session || ""; color: "#8A847F"; elide: Text.ElideRight
        font.family: "Segoe UI Variable Text"; font.pointSize: 8
    }
    Column {
        id: qs
        x: card.textX; y: title.y + title.height + 6
        width: card.textW
        spacing: 8
        Repeater {
            model: card.questions
            Column {
                id: qcol
                readonly property int qi: index
                readonly property var q: modelData
                // room for a description line only when the options have any (no jumping on hover)
                readonly property bool hasHints: (q.options || []).some(function (o) { return !!o.description })
                width: qs.width
                spacing: 5
                Row {
                    spacing: 6
                    width: parent.width
                    Rectangle {
                        visible: !!qcol.q.header
                        width: hdr.implicitWidth + 10; height: 16; radius: 4; color: Qt.rgba(1, 1, 1, 0.08)
                        anchors.verticalCenter: parent.verticalCenter
                        Text { id: hdr; anchors.centerIn: parent; text: card.short(qcol.q.header, 16).toUpperCase(); color: "#9A9A9A"
                            font.family: "Segoe UI Variable Text"; font.pointSize: 7; font.letterSpacing: 0.4 }
                    }
                    Text {
                        width: parent.width - (qcol.q.header ? hdr.implicitWidth + 16 : 0)
                        text: (qcol.q.question || "") + (qcol.q.multiSelect ? "<font color=\"#8A847F\">  (any)</font>" : "")
                        textFormat: Text.StyledText
                        color: "#E6E1DC"; wrapMode: Text.Wrap; maximumLineCount: 4; elide: Text.ElideRight
                        font.family: "Segoe UI Variable Text"; font.pointSize: 9
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                // the hovered option's description, inside the card (a floating tip
                // ran past the island's edge and was clipped)
                property string hint: ""
                Flow {
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: qcol.q.options || []
                        Pill {
                            readonly property string lab: String(modelData.label || "")
                            readonly property bool sel: card.picked(qcol.qi, lab)
                            objectName: "option:" + qcol.qi + ":" + lab
                            label: (qcol.q.multiSelect ? (sel ? "✓ " : "") : "") + card.short(lab, 34)
                            w: Math.max(48, Math.ceil(fmP.advanceWidth(label)) + 26)
                            accent: sel
                            onHoveredChanged: {
                                if (hovered) qcol.hint = String(modelData.description || "")
                                else if (qcol.hint === String(modelData.description || "")) qcol.hint = ""
                            }
                            border.width: sel ? 1 : 0; border.color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.7)
                            onTapped: card.toggle(qcol.qi, lab)
                        }
                    }
                }
                Text {
                    objectName: "optionHint"
                    width: parent.width
                    height: qcol.hasHints ? Math.max(implicitHeight, fmP.height * 2) : 0
                    visible: qcol.hasHints
                    text: qcol.hint
                    color: "#9A9A9A"; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                    font.family: "Segoe UI Variable Text"; font.pointSize: 8
                }
            }
        }
    }
    Item {
        id: footer
        x: card.textX; y: qs.y + qs.height + 9
        width: card.textW; height: 24
        Row {
            spacing: 6
            x: parent.width - width
            Pill { objectName: "inTerminal"; label: "In terminal"; w: 76
                onTapped: card.decided("pass") }
            Pill { objectName: "submit"; label: "Submit"; w: 62; primary: card.complete; enabled_: card.complete
                onTapped: card.submit() }
        }
    }
}
