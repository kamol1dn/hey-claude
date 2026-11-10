import QtQuick

// An approval request (M7): what wants to run, from which session, and the
// buttons (Allow / Deny / Always, from the request's `actions`). Only a click
// answers it (QmlIsland.approval_decided); nothing on the island decides by
// itself. `dangerous` adds a red "needs a click" marker (voice can't approve it).
//
//   [icon]  Title ................................. [! needs a click]
//           detail (the command or file, monospace, 2 lines at most;
//                   `prose`: a message draft, in text type, 4 lines)
//           session · note · 1:42              [Deny] [Always] [Allow]
Item {
    id: card
    objectName: "approvalCard"
    property var req: ({})
    signal decided(string action)
    readonly property bool dangerous: !!req.dangerous
    readonly property real textX: 50
    readonly property real textW: width - textX - 16
    height: bottom.y + bottom.height + 10
    opacity: 0
    Component.onCompleted: fade.start()
    NumberAnimation { id: fade; target: card; property: "opacity"; from: 0; to: 1; duration: 200; easing.type: Easing.OutCubic }

    // the countdown hint: until the terminal takes the question back (expires, epoch s)
    property real nowS: Date.now() / 1000
    readonly property int leftS: req.expires ? Math.max(0, Math.round(req.expires - nowS)) : -1
    Timer { interval: 1000; repeat: true; running: card.visible && card.leftS > 0; onTriggered: card.nowS = Date.now() / 1000 }
    FontMetrics { id: fmP; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    function clock(s) { var m = Math.floor(s / 60), r = s % 60; return m + ":" + (r < 10 ? "0" : "") + r }

    Rectangle { x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.1) }
    // dangerous: a thin red edge down the card's left side
    Rectangle { visible: card.dangerous; x: 14; y: 8; width: 2; height: card.height - 16; radius: 1; color: "#E5646A"; opacity: 0.8 }
    ServiceIcon {
        x: 22; y: 11; size: 16
        url: card.req.icon_url || ""; glyph: card.req.icon_url ? "" : (card.req.tool === "Bash" ? "terminal" : "lock"); initials: card.req.service || ""
    }
    Text {
        id: title
        objectName: "approvalTitle"
        x: card.textX; y: 9
        width: card.textW - (danger.visible ? danger.width + 8 : 0)
        text: card.req.title || "Allow this?"; color: "#F2F2F2"; elide: Text.ElideRight
        font.family: "Segoe UI Variable Text"; font.pointSize: 10
    }
    Rectangle {
        id: danger
        objectName: "dangerMarker"
        visible: card.dangerous
        x: card.width - 16 - width; y: 9
        width: dRow.width + 14; height: 18; radius: 9
        color: Qt.rgba(229 / 255, 100 / 255, 106 / 255, 0.16); border.width: 1; border.color: Qt.rgba(229 / 255, 100 / 255, 106 / 255, 0.55)
        Row {
            id: dRow
            anchors.centerIn: parent
            spacing: 4
            Rectangle { width: 11; height: 11; radius: 5.5; color: "#E5646A"; anchors.verticalCenter: parent.verticalCenter
                Text { anchors.centerIn: parent; text: "!"; color: "#170c07"; font.bold: true; font.pixelSize: 9; font.family: "Segoe UI Variable Text" } }
            Text { text: "needs a click"; color: "#F09A9E"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                anchors.verticalCenter: parent.verticalCenter }
        }
    }
    Rectangle {
        id: detailBox
        visible: !!card.req.detail
        x: card.textX - 4; y: title.y + title.height + 5
        width: card.textW + 4; height: visible ? detail.height + 8 : 0
        radius: 6; color: Qt.rgba(1, 1, 1, 0.045)
        Text {
            id: detail
            objectName: "approvalDetail"
            x: 6; y: 4; width: parent.width - 12
            text: card.req.detail || ""; color: "#CFC9C3"
            wrapMode: card.req.prose ? Text.WrapAtWordBoundaryOrAnywhere : Text.WrapAnywhere
            maximumLineCount: card.req.prose ? 4 : 2; elide: Text.ElideRight
            font.family: card.req.prose ? "Segoe UI Variable Text" : "Consolas"; font.pointSize: card.req.prose ? 9 : 8
        }
    }
    Item {
        id: bottom
        x: card.textX; y: (detailBox.visible ? detailBox.y + detailBox.height : title.y + title.height) + 7
        width: card.textW; height: 24
        Text {
            objectName: "approvalSession"
            anchors.verticalCenter: parent.verticalCenter
            width: Math.max(10, parent.width - buttons.width - 10)
            text: [card.req.session || "", card.req.note || "", card.leftS > 0 ? card.clock(card.leftS) : ""]
                  .filter(function (x) { return x !== "" }).join("  ·  ")
            color: "#8A847F"; elide: Text.ElideRight
            font.family: "Segoe UI Variable Text"; font.pointSize: 8
        }
        Row {
            id: buttons
            spacing: 6
            x: parent.width - width
            Repeater {
                model: card.req.actions || []
                Pill { objectName: "action:" + modelData.id; label: modelData.label
                    w: Math.max(56, Math.ceil(fmP.advanceWidth(modelData.label)) + 28); primary: !!modelData.primary
                    onTapped: card.decided(modelData.id) }
            }
        }
    }
}
