import QtQuick
import "../../ui/island"

// telegram_draft: a reply the agent drafted, as your own (ember) bubble with
// Cancel / Edit / Send under it. Sending needs M7's approvals: until then
// (data_.can_send !== true) Send is shown disabled with a tooltip, and nothing
// is ever sent from here. When M7 sends it (data_.sent), a paper plane flies
// out from Send, the bubble settles to half strength and a check lands.
// Message text is private: shown, never logged.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false

    readonly property var d: data_ || ({})
    readonly property bool canSend: d.can_send === true && !d.sent     // M7 turns this on with an approval
    readonly property bool hasLabel: !!d.reply_to
    property bool sent: false
    property real fly: 0                                               // the plane: 0 -> 1 across the flight
    implicitHeight: btns.y + btns.height

    function markSent(animate) {
        if (sent) return
        sent = true
        if (animate && !reduced) flyOut.start()
        else { fly = 1; bubbleDim.opacity = 0.5; check.scale = 1 }
    }
    // read data_ itself here: the handler can run before the binding of d refreshes
    onData_Changed: if (data_ && data_.sent === true) markSent(true)
    Component.onCompleted: if (data_ && data_.sent === true) markSent(false)

    component Rise: Item {
        id: r
        property int i: 0
        property bool still: false
        opacity: 0
        transform: Translate { id: rt; y: r.still ? 0 : 5 }
        NumberAnimation { id: fade; target: r; property: "opacity"; from: 0; to: 1; duration: 160 }
        SequentialAnimation {
            id: seq
            PauseAnimation { duration: r.i * 45 }
            ParallelAnimation {
                NumberAnimation { target: r; property: "opacity"; to: 1; duration: 320; easing.type: Easing.OutCubic }
                NumberAnimation { target: rt; property: "y"; to: 0; duration: 320; easing.type: Easing.OutCubic }
            }
        }
        Component.onCompleted: still ? fade.start() : seq.start()
    }

    Rise {
        id: label
        visible: w.hasLabel
        i: 0; still: w.reduced
        width: w.width; height: w.hasLabel ? lt.implicitHeight : 0
        Text {
            id: lt
            width: parent.width
            text: "Reply to “" + (w.d.reply_to || "") + "”"
            elide: Text.ElideRight; textFormat: Text.PlainText
            color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
        }
    }

    Rise {
        id: bubbleRow
        i: 1; still: w.reduced
        y: w.hasLabel ? label.height + 8 : 0
        width: w.width; height: bubble.height
        Item {
            id: bubbleDim
            anchors.fill: parent
            Rectangle {
                id: bubble
                readonly property real maxW: Math.round(w.width * 0.9)
                x: parent.width - width
                width: Math.min(maxW, msg.implicitWidth + 20)
                height: msg.height + 15
                radius: 12; bottomRightRadius: 4
                color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.18)
                Text {
                    id: msg
                    x: 10; y: 7; width: Math.min(implicitWidth, bubble.maxW - 20)
                    text: w.d.text || ""
                    wrapMode: Text.Wrap; maximumLineCount: w.hasLabel ? 4 : 5; elide: Text.ElideRight
                    textFormat: Text.PlainText
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
                }
            }
        }
        Glyph {                                                    // lands once it is sent
            id: check
            x: bubble.x - width - 2; y: bubble.height - height + 2
            name: "check"; color: "#6CCB7A"; s: 0.75
            scale: 0
        }
    }

    Rise {
        id: btns
        i: 2; still: w.reduced
        y: bubbleRow.y + bubbleRow.height + 10
        width: w.width; height: 24
        Row {
            anchors.right: parent.right
            spacing: 8
            opacity: w.sent ? 0.45 : 1
            Behavior on opacity { NumberAnimation { duration: 300 } }
            Pill { label: "Cancel"; w: 64; enabled_: !w.sent; onTapped: w.action("cancel", null) }
            Pill { label: "Edit"; w: 50; enabled_: !w.sent; onTapped: w.action("edit", w.d.text || "") }
            Pill {
                id: sendP
                label: w.sent ? "Sent" : "Send"; w: 58
                primary: w.canSend; enabled_: w.canSend
                tip: w.canSend || w.sent ? "" : "Sending needs approvals (coming in M7)"
                onTapped: w.action("send", w.d.text || "")
            }
        }
    }

    // the paper plane: from Send, up and out to the right
    Glyph {
        id: plane
        visible: w.fly > 0 && w.fly < 1
        name: "plane"; color: "#EC9A78"; s: 0.9
        x: w.width - 40 + 130 * w.fly
        y: btns.y - 6 - 70 * w.fly
        rotation: -12 * w.fly
        opacity: 1 - w.fly
    }
    SequentialAnimation {
        id: flyOut
        NumberAnimation { target: w; property: "fly"; from: 0; to: 1; duration: 600; easing.type: Easing.InCubic }
        ParallelAnimation {
            NumberAnimation { target: bubbleDim; property: "opacity"; to: 0.5; duration: 300; easing.type: Easing.OutCubic }
            NumberAnimation { target: check; property: "scale"; from: 0; to: 1; duration: 360; easing.type: Easing.OutBack; easing.overshoot: 2.2 }
        }
    }
}
