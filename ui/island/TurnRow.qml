import QtQuick

// One line of the open turn under the island's head: the user's question
// (a short white tick: the user's ink) or, once it has been spoken, the answer
// (a terracotta tick: Claude's ink). Fades in; the text never re-animates.
Item {
    id: row
    property string text: ""
    property bool answer: false
    property int lines: 1
    implicitHeight: text ? Math.min(t.implicitHeight, lines * 17) + 12 : 0
    height: implicitHeight
    opacity: 0
    visible: text !== ""
    onTextChanged: if (text !== "" && opacity < 1) fade.restart()
    NumberAnimation { id: fade; target: row; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
    Rectangle { x: 16; y: 10; width: 2; height: Math.min(t.implicitHeight, row.lines * 17) - 4; radius: 1
        color: row.answer ? "#D97757" : Qt.rgba(242 / 255, 242 / 255, 242 / 255, 0.7) }
    Text {
        id: t
        x: 26; y: 6; width: row.width - 26 - 16
        text: row.text
        textFormat: Text.PlainText
        wrapMode: Text.Wrap; maximumLineCount: row.lines; elide: Text.ElideRight
        color: row.answer ? "#F2F2F2" : "#9A9A9A"
        font.family: "Segoe UI Variable Text"; font.pointSize: row.answer ? 9.5 : 9
    }
}
