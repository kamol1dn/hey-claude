import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// email: an inbox digest. Left, a small stack of envelopes drops in one on top of
// another, each settling at its own tilt, then the unread badge pops on the top one
// (a single ring bursts behind it and the count ticks up). Right, the top senders
// rise in: who, how many, the newest subject and when. One-shot, then still.
// Private: the card frame fades it when the island rests.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var rows: (d.senders || []).slice(0, 4)
    readonly property int unread: Math.max(0, d.unread || 0)
    readonly property int nEnv: Math.min(3, Math.max(1, unread))
    readonly property real rowH: 37
    readonly property real listX: 128
    implicitHeight: Math.max(rows.length * rowH + 2, 128)

    // ---------------------------------------------------------- the envelope stack
    component Envelope: Item {
        id: env
        property int k: 0                     // 0 = the top one
        property real tilt: 0
        property color body: "#24211F"
        property real ink: 0.5
        property real fall: w.reduced ? 1 : 0
        width: 76; height: 52
        opacity: Math.min(1, fall * 2.5)
        rotation: tilt * (1 + 1.6 * (1 - fall))
        transform: Translate { y: -34 * (1 - env.fall) }
        Rectangle {
            anchors.fill: parent; radius: 6
            color: env.body
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.09)
        }
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Qt.rgba(217 / 255, 119 / 255, 87 / 255, env.ink); strokeWidth: 1.4; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: "M5 6 L38 30 L71 6" }
            }
            ShapePath {
                strokeColor: Qt.rgba(1, 1, 1, 0.06); strokeWidth: 1; fillColor: "transparent"
                PathSvg { path: "M5 47 L29 27 M71 47 L47 27" }
            }
        }
        SequentialAnimation {
            id: drop
            PauseAnimation { duration: 70 + (w.nEnv - 1 - env.k) * 115 }
            NumberAnimation { target: env; property: "fall"; from: 0; to: 1; duration: 380; easing.type: Easing.OutBack; easing.overshoot: 1.25 }
        }
        Component.onCompleted: if (!w.reduced) drop.start()
    }

    Item {
        id: stack
        width: 112; height: 112
        y: Math.max(0, (w.implicitHeight - 128) / 2)
        Envelope { k: 2; visible: w.nEnv > 2; x: 4; y: 30; tilt: -11; body: "#1A1817"; ink: 0.22 }
        Envelope { k: 1; visible: w.nEnv > 1; x: 28; y: 26; tilt: 9; body: "#1F1C1B"; ink: 0.32 }
        Envelope { id: top; k: 0; x: 16; y: 44; tilt: -1.5; body: "#2A2624"; ink: 0.75 }

        // the unread badge: a burst ring, then the pill with the count
        property real pop: w.reduced ? 1 : 0
        property real burst: 0
        Rectangle {
            visible: stack.burst > 0 && stack.burst < 1
            x: badge.x + badge.width / 2 - width / 2; y: badge.y + badge.height / 2 - height / 2
            width: badge.height * (1 + 1.1 * stack.burst); height: width; radius: width / 2
            color: "transparent"; border.width: 2; border.color: badge.color
            opacity: 0.6 * (1 - stack.burst)
        }
        Rectangle {
            id: badge
            objectName: "unreadBadge"
            x: 74; y: 28
            height: 28; radius: 14; width: Math.max(28, cnt.implicitWidth + 16)
            color: w.unread > 0 ? "#D97757" : "#6CCB7A"
            scale: stack.pop
            border.width: 2; border.color: "#0B0B0B"
            Tick {
                id: cnt
                anchors.centerIn: parent
                value: w.unread
                reduced: w.reduced
                color: "#FFFFFF"; font.family: "Segoe UI Variable Text"; font.pointSize: 10.5; font.weight: Font.Bold
            }
        }
        SequentialAnimation {
            id: popIn
            PauseAnimation { duration: 70 + w.nEnv * 115 + 160 }
            ParallelAnimation {
                NumberAnimation { target: stack; property: "pop"; from: 0; to: 1; duration: 340; easing.type: Easing.OutBack; easing.overshoot: 3 }
                NumberAnimation { target: stack; property: "burst"; from: 0; to: 1; duration: 560; easing.type: Easing.OutCubic }
            }
        }
        Component.onCompleted: if (!w.reduced) popIn.start()

        Text {
            anchors.horizontalCenter: parent.horizontalCenter; anchors.horizontalCenterOffset: 4
            y: 104
            opacity: stack.pop
            text: w.unread === 0 ? "all read" : (w.d.approx ? "unread · est." : "unread")
            color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
        }
    }

    // ---------------------------------------------------------- the senders
    Text {
        visible: w.rows.length === 0
        x: w.listX; width: w.width - w.listX; height: w.implicitHeight
        verticalAlignment: Text.AlignVCenter
        text: "No messages match."
        color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
    }
    Repeater {
        model: w.rows
        Rise {
            i: index + 2; reduced: w.reduced
            x: w.listX; y: index * w.rowH + Math.max(0, (w.implicitHeight - w.rows.length * w.rowH) / 2)
            Item {
                objectName: "senderRow"
                width: w.width - w.listX; height: w.rowH
                Rectangle {
                    anchors.fill: parent; anchors.leftMargin: -6; anchors.rightMargin: -4; radius: 8
                    color: Qt.rgba(1, 1, 1, ma.containsMouse && modelData.link ? 0.05 : 0)
                }
                Rectangle {                                        // unread dot
                    visible: (modelData.unread || 0) > 0
                    x: 0; y: 8; width: 6; height: 6; radius: 3; color: "#EC9A78"
                }
                Row {
                    x: 12; y: 1; spacing: 6
                    width: parent.width - 12 - tm.width - 8
                    Text {
                        id: nm
                        width: Math.min(implicitWidth, parent.width - (cn.visible ? cn.implicitWidth + 6 : 0))
                        text: modelData.name || ""
                        elide: Text.ElideRight; textFormat: Text.PlainText
                        color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10
                        font.weight: (modelData.unread || 0) > 0 ? Font.DemiBold : Font.Normal
                    }
                    Text {
                        id: cn
                        visible: (modelData.count || 0) > 1
                        anchors.baseline: nm.baseline
                        text: "×" + (modelData.count || 0)
                        color: "#EC9A78"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5; font.features: { "tnum": 1 }
                    }
                }
                Text {
                    id: tm
                    anchors.right: parent.right; y: 3
                    text: modelData.time || ""
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.features: { "tnum": 1 }
                }
                Text {
                    x: 12; y: 19; width: parent.width - 12
                    text: modelData.subject || ""
                    elide: Text.ElideRight; textFormat: Text.PlainText
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
                }
                MouseArea {
                    id: ma
                    anchors.fill: parent; hoverEnabled: true
                    cursorShape: modelData.link ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: if (modelData.link) w.action("open", modelData.link)
                }
            }
        }
    }
}
