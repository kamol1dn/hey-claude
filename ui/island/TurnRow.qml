import QtQuick

// One line of the open turn under the island's head: the user's question
// (a short white tick: the user's ink) or, once it has been spoken, the answer
// (a terracotta tick: Claude's ink). Fades in; the text never re-animates.
//
// The answer is never cut with an ellipsis: it grows to fit, up to maxH (what
// is left of the window under the island). A longer one becomes a viewport
// with a soft fade at the edge that has more; it drifts down at a reading pace
// after a moment (and the wheel scrolls it), so all of it can be read.
Item {
    id: row
    property string text: ""
    property bool answer: false
    property int lines: 0                   // 0: as many as it takes (the answer); 1: one line, elided (the question)
    property real maxH: 100000
    readonly property real lineH: 17
    readonly property real fullH: t.implicitHeight
    readonly property real viewH: lines > 0 ? Math.min(fullH, lines * lineH) : Math.min(fullH, Math.max(lineH, maxH - 12))
    readonly property bool overflows: lines === 0 && fullH > viewH + 1
    readonly property real overflowPx: overflows ? fullH - viewH : 0
    implicitHeight: text ? viewH + 12 : 0
    height: implicitHeight
    opacity: 0
    visible: text !== ""
    onTextChanged: { if (text !== "" && opacity < 1) fade.restart(); flick.contentY = 0; drift.stop(); if (overflows) driftStart.restart() }
    onOverflowsChanged: if (overflows && !drift.running) driftStart.restart()
    NumberAnimation { id: fade; target: row; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
    Rectangle { x: 16; y: 10; width: 2; height: Math.max(0, row.viewH - 4); radius: 1
        color: row.answer ? "#D97757" : Qt.rgba(242 / 255, 242 / 255, 242 / 255, 0.7) }
    Flickable {
        id: flick
        objectName: row.answer ? "answerView" : "questionView"
        x: 26; y: 6; width: row.width - 26 - 16; height: row.viewH
        contentWidth: width; contentHeight: t.implicitHeight
        clip: row.overflows
        interactive: row.overflows
        boundsBehavior: Flickable.StopAtBounds
        onMovementStarted: { drift.stop(); driftStart.stop() }
        Text {
            id: t
            objectName: row.answer ? "answerText" : "questionText"
            width: flick.width
            text: row.text
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: row.lines > 0 ? row.lines : 100000
            elide: row.lines > 0 ? Text.ElideRight : Text.ElideNone
            color: row.answer ? "#F2F2F2" : "#9A9A9A"
            font.family: "Segoe UI Variable Text"; font.pointSize: row.answer ? 9.5 : 9
        }
    }
    // the edges that have more: fade into the panel
    Rectangle {
        visible: row.overflows && flick.contentY > 1
        x: flick.x; y: flick.y; width: flick.width; height: 14
        gradient: Gradient { GradientStop { position: 0; color: Qt.rgba(8 / 255, 8 / 255, 8 / 255, 0.97) } GradientStop { position: 1; color: "transparent" } }
    }
    Rectangle {
        visible: row.overflows && flick.contentY < row.overflowPx - 1
        x: flick.x; y: flick.y + flick.height - 16; width: flick.width; height: 16
        gradient: Gradient { GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: Qt.rgba(8 / 255, 8 / 255, 8 / 255, 0.97) } }
    }
    // reading pace: ~3 lines in 4 s, after a 2.5 s look at the first lines
    Timer { id: driftStart; interval: 2500; onTriggered: if (row.overflows) { drift.to = row.overflowPx; drift.duration = row.overflowPx / 13 * 1000; drift.restart() } }
    NumberAnimation { id: drift; target: flick; property: "contentY"; easing.type: Easing.Linear }
    // how long it takes to read all of it (Extras keeps the turn up at least this long)
    readonly property real readMs: overflows ? 2500 + overflowPx / 13 * 1000 + 2500 : 0
}
