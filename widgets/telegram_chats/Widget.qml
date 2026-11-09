import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// telegram_chats: chats that need attention. A round avatar (the cached profile
// photo from widgets.py's avatar_src, else initials on a warm-to-dark tint),
// name, last-message snippet, time and an unread badge. Rows rise in 45 ms
// apart; unread badges pop once their row has landed. A click opens the chat
// in Telegram Desktop (tg://). Message text is private: shown, never logged.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false

    readonly property var chats: ((data_ && data_.chats) || []).slice(0, 6)
    readonly property int rowH: chats.length > 5 ? 32 : 38        // six rows still fit the body (196)
    readonly property int av: rowH - 8
    readonly property int bh: rowH > 34 ? 18 : 16                 // unread badge height
    implicitHeight: Math.max(1, chats.length) * rowH

    function initials(n) {
        var p = String(n || "?").trim().split(/\s+/).filter(function (s) { return s !== "" })
        return p.map(function (s) { return s[0] }).join("").slice(0, 2).toUpperCase() || "?"
    }
    function hue(n) { var h = 0; n = String(n || ""); for (var i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) % 360; return h / 360 }

    Text {
        visible: w.chats.length === 0
        anchors.verticalCenter: parent.verticalCenter
        text: "Nothing needs you."; color: "#9A9A9A"
        font.family: "Segoe UI Variable Text"; font.pointSize: 9
    }

    Repeater {
        model: w.chats
        Item {
            id: row
            y: index * w.rowH; width: w.width; height: w.rowH
            readonly property int unread: modelData.unread || 0
            readonly property bool hasAv: !!modelData.avatar_src
            property real pop: w.reduced ? 1 : 0
            opacity: 0
            transform: Translate { id: rt; y: w.reduced ? 0 : 5 }

            NumberAnimation { id: fadeR; target: row; property: "opacity"; from: 0; to: 1; duration: 160 }
            SequentialAnimation {
                id: enterR
                PauseAnimation { duration: index * 45 }
                ParallelAnimation {
                    NumberAnimation { target: row; property: "opacity"; to: 1; duration: 320; easing.type: Easing.OutCubic }
                    NumberAnimation { target: rt; property: "y"; to: 0; duration: 320; easing.type: Easing.OutCubic }
                }
            }
            SequentialAnimation {
                id: popR
                PauseAnimation { duration: index * 45 + 260 }
                NumberAnimation { target: row; property: "pop"; from: 0; to: 1; duration: 360; easing.type: Easing.OutBack; easing.overshoot: 2.6 }
            }
            Component.onCompleted: {
                if (w.reduced) fadeR.start()
                else { enterR.start(); if (unread > 0) popR.start() }
            }

            Rectangle {                                            // hover wash (only for rows that open)
                x: -6; y: 1; width: parent.width + 12; height: parent.height - 2; radius: 9
                color: Qt.rgba(1, 1, 1, ma.containsMouse && modelData.link ? 0.045 : 0)
                Behavior on color { ColorAnimation { duration: 140 } }
            }

            // avatar
            Item {
                id: avI
                x: 0; y: (w.rowH - w.av) / 2; width: w.av; height: w.av
                Rectangle {
                    visible: !row.hasAv || avImg.status !== Image.Ready
                    anchors.fill: parent; radius: width / 2
                    gradient: Gradient {
                        GradientStop { position: 0; color: Qt.hsla(w.hue(modelData.name), 0.5, 0.5, 1) }
                        GradientStop { position: 1; color: "#1d1b19" }
                    }
                    Text {
                        anchors.centerIn: parent
                        text: w.initials(modelData.name)
                        color: "#FFFFFF"; font.family: "Segoe UI Variable Text"; font.pointSize: w.av > 26 ? 8.5 : 8; font.weight: Font.DemiBold
                    }
                }
                Image {
                    id: avImg
                    visible: false                                  // a texture for the round shape below
                    width: w.av; height: w.av
                    source: modelData.avatar_src || ""
                    fillMode: Image.PreserveAspectCrop
                    sourceSize: Qt.size(w.av * 2, w.av * 2)
                    asynchronous: true; smooth: true
                }
                Shape {
                    id: avShape
                    visible: row.hasAv && avImg.status === Image.Ready
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeWidth: -1; strokeColor: "transparent"
                        fillItem: avImg
                        PathRectangle { x: 0; y: 0; width: avShape.width; height: avShape.height; radius: avShape.width / 2 }
                    }
                }
            }

            // name + snippet
            Column {
                x: w.av + 10; anchors.verticalCenter: parent.verticalCenter
                width: w.width - x - Math.max(timeT.implicitWidth, badge.width) - 12
                spacing: w.rowH > 34 ? 1 : 0
                Text {
                    width: parent.width
                    text: modelData.name || ""; elide: Text.ElideRight; textFormat: Text.PlainText
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
                    font.weight: row.unread > 0 ? Font.Medium : Font.Normal
                }
                Text {
                    visible: text !== ""
                    width: parent.width
                    text: modelData.last || ""; elide: Text.ElideRight; textFormat: Text.PlainText; maximumLineCount: 1
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
                }
            }

            // time + unread badge
            Text {
                id: timeT
                anchors.right: parent.right
                y: row.unread > 0 ? (w.rowH - w.bh - 16) / 2 : (w.rowH - implicitHeight) / 2
                text: modelData.time || ""; textFormat: Text.PlainText
                color: "#6f6a66"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                font.features: { "tnum": 1 }
            }
            Rectangle {
                id: badge
                visible: row.unread > 0
                anchors.right: parent.right
                y: timeT.y + 16; height: w.bh; radius: w.bh / 2
                width: Math.max(w.bh, ub.implicitWidth + 10)
                color: "#D97757"
                scale: row.pop
                transformOrigin: Item.Center
                Text {
                    id: ub
                    anchors.centerIn: parent
                    text: row.unread > 99 ? "99+" : row.unread
                    color: "#170c07"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5; font.weight: Font.Bold
                    font.features: { "tnum": 1 }
                }
            }
            MouseArea {
                id: ma
                anchors.fill: parent; hoverEnabled: true
                cursorShape: modelData.link ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: { if (modelData.link) w.action("open", modelData.link) }
            }
        }
    }
}
