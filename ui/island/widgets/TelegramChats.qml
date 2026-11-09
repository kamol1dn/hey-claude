import QtQuick
import ".."

// telegram_chats: chats that need attention. Avatar (cached profile photo) or
// initials, unread badge, last-message snippet and time. Rows slide in one by
// one; unread badges pop. A click opens the chat in Telegram Desktop (tg://).
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 440
    readonly property bool holds: false
    readonly property var chats: (data_ && data_.chats) || []
    implicitHeight: 36 * chats.length

    function initials(n) { var p = String(n || "?").trim().split(/\s+/); return (p.length > 1 ? p[0][0] + p[1][0] : String(p[0]).slice(0, 2)).toUpperCase() }
    function hue(n) { var h = 0; for (var i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) % 360; return h / 360 }

    Repeater {
        model: w.chats
        Item {
            id: rowI
            y: index * 36; width: w.width; height: 36
            opacity: 0
            property real dx: 10
            property real pop: 0
            transform: Translate { x: rowI.dx }
            Component.onCompleted: slide.start()
            SequentialAnimation {
                id: slide
                PauseAnimation { duration: 50 * index }
                ParallelAnimation {
                    NumberAnimation { target: rowI; property: "opacity"; to: 1; duration: 200; easing.type: Easing.OutCubic }
                    NumberAnimation { target: rowI; property: "dx"; to: 0; duration: 220; easing.type: Easing.OutCubic }
                }
                NumberAnimation { target: rowI; property: "pop"; from: 0; to: 1; duration: 260; easing.type: Easing.OutBack; easing.overshoot: 2.2 }
            }
            Rectangle {
                x: 0; y: 4; width: 28; height: 28; radius: 14; clip: true
                color: Qt.hsla(w.hue(modelData.name || ""), 0.35, 0.38, 1)
                Text { anchors.centerIn: parent; visible: !modelData.avatar_src; text: w.initials(modelData.name)
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.bold: true }
                Image { anchors.fill: parent; visible: !!modelData.avatar_src; source: modelData.avatar_src || ""
                    fillMode: Image.PreserveAspectCrop; asynchronous: true; sourceSize: Qt.size(56, 56) }
            }
            Text { x: 38; y: 3; width: w.width - 38 - 64; text: modelData.name || ""; elide: Text.ElideRight
                color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9; font.bold: (modelData.unread || 0) > 0 }
            Text { x: 38; y: 19; width: w.width - 38 - 40; text: modelData.last || ""; elide: Text.ElideRight
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
            Text { x: w.width - width; y: 3; text: modelData.time || ""; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
            Rectangle {
                visible: (modelData.unread || 0) > 0
                x: w.width - width; y: 18; height: 16; radius: 8; width: Math.max(16, ub.implicitWidth + 10)
                color: "#D97757"; scale: rowI.pop
                Text { id: ub; anchors.centerIn: parent; text: modelData.unread > 99 ? "99+" : modelData.unread; color: "#080808"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 7; font.bold: true }
            }
            MouseArea { anchors.fill: parent; enabled: !!modelData.link; cursorShape: Qt.PointingHandCursor
                onClicked: w.action("open", modelData.link) }
        }
    }
}
