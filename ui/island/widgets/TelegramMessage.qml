import QtQuick
import ".."

// telegram_message: one message or a short thread; replies are indented, long
// text is clamped to 3 lines. A click opens the chat in Telegram Desktop.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 460
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    implicitHeight: (d.chat ? 20 : 0) + col.implicitHeight

    Row {
        visible: !!w.d.chat; spacing: 6; height: 18
        ServiceIcon { y: 2; size: 13; url: Qt.resolvedUrl("../../icons/services/telegram.svg") }
        Text { height: 18; verticalAlignment: Text.AlignVCenter; text: w.d.chat || ""; color: "#9A9A9A"
            font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    }
    Column {
        id: col
        y: w.d.chat ? 20 : 0; width: w.width; spacing: 6
        Repeater {
            model: w.d.messages || []
            Item {
                id: mi
                width: col.width; height: body.implicitHeight + 18
                opacity: 0
                Component.onCompleted: fadeIn.start()
                NumberAnimation { id: fadeIn; target: mi; property: "opacity"; to: 1; duration: 200; easing.type: Easing.OutCubic; running: false }
                readonly property real ind: modelData.reply ? 24 : 0
                Rectangle { x: parent.ind; width: 2; height: parent.height; radius: 1; color: modelData.reply ? Qt.rgba(1, 1, 1, 0.15) : "#D97757" }
                Text { x: parent.ind + 10; y: 0; text: (modelData.sender || "") + (modelData.time ? "  ·  " + modelData.time : "")
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                Text { id: body; x: parent.ind + 10; y: 15; width: col.width - parent.ind - 10
                    text: modelData.text || ""; color: "#F2F2F2"; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                    textFormat: Text.PlainText; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
            }
        }
    }
    MouseArea { anchors.fill: parent; enabled: !!w.d.link; cursorShape: Qt.PointingHandCursor; onClicked: w.action("open", w.d.link) }
}
