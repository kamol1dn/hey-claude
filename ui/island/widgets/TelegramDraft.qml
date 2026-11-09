import QtQuick
import ".."

// telegram_draft: a reply the agent drafted, with Send / Edit / Cancel.
// Sending needs M7's approvals: until then Send is shown disabled and nothing
// is ever sent from here. When M7 sends it (data_.sent), a paper plane flies
// out of the card and a check lands.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 460
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    readonly property bool canSend: d.can_send === true          // M7 turns this on with an approval
    property real fly: 0
    implicitHeight: 22 + Math.max(36, msg.implicitHeight + 16) + 34

    onData_Changed: if (d.sent && fly === 0) flyOut.start()

    Row {
        spacing: 6; height: 18
        ServiceIcon { y: 2; size: 13; url: Qt.resolvedUrl("../../icons/services/telegram.svg") }
        Text { height: 18; verticalAlignment: Text.AlignVCenter; text: "Draft to " + (w.d.chat || ""); color: "#9A9A9A"
            font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    }
    Rectangle {
        id: bubble
        y: 22; x: w.width - width; width: Math.min(w.width - 40, msg.implicitWidth + 24); height: Math.max(36, msg.implicitHeight + 16)
        radius: 12; color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.16)
        opacity: 1 - w.fly
        Text { id: msg; x: 12; y: 8; width: Math.min(implicitWidth, w.width - 64); text: w.d.text || ""; wrapMode: Text.Wrap
            maximumLineCount: 5; elide: Text.ElideRight; color: "#F2F2F2"; textFormat: Text.PlainText
            font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
    }
    Glyph {
        visible: w.fly > 0 && w.fly < 1
        name: "plane"; color: "#EC9A78"
        x: bubble.x + bubble.width - 24 + 120 * w.fly; y: bubble.y + 4 - 40 * w.fly; opacity: 1 - w.fly
    }
    Glyph { visible: w.fly >= 1; x: w.width - 30; y: bubble.y + 6; name: "check"; color: "#6CCB7A" }
    Row {
        y: bubble.y + bubble.height + 8; x: w.width - width; spacing: 8
        Pill { label: "Cancel"; w: 60; onTapped: w.action("cancel", null) }
        Pill { label: "Edit"; w: 52; onTapped: w.action("edit", w.d.text || "") }
        Pill { label: "Send"; w: 58; primary: w.canSend; enabled_: w.canSend
            tip: w.canSend ? "" : "Sending needs approvals (coming in M7)"
            onTapped: w.action("send", w.d.text || "") }
    }
    NumberAnimation { id: flyOut; target: w; property: "fly"; from: 0; to: 1; duration: 520; easing.type: Easing.InCubic }
}
