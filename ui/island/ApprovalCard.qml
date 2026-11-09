import QtQuick

// An approval request (M7): what wants to run, from which session, and the
// buttons. Only a click answers it (QmlIsland.approval_decided); nothing on the
// island decides by itself.
Item {
    id: card
    property var req: ({})
    signal decided(string action)
    height: Math.max(56, col.implicitHeight + 16)
    opacity: 0
    Component.onCompleted: fade.start()
    NumberAnimation { id: fade; target: card; property: "opacity"; from: 0; to: 1; duration: 200; easing.type: Easing.OutCubic }

    Rectangle { x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.1) }
    ServiceIcon {
        x: 22; y: 14; size: 16
        url: card.req.icon_url || ""; glyph: card.req.icon_url ? "" : "lock"; initials: card.req.service || ""
    }
    Column {
        id: col
        x: 50; y: 8; width: card.width - 50 - buttons.width - 26
        spacing: 2
        Text { width: parent.width; text: card.req.title || "Allow this?"; color: "#F2F2F2"; elide: Text.ElideRight
            font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
        Text { visible: !!card.req.detail; width: parent.width; text: card.req.detail || ""; color: "#9A9A9A"
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
        Text { visible: !!card.req.session; width: parent.width; text: card.req.session || ""; color: "#6F6A66"; elide: Text.ElideRight
            font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    }
    Row {
        id: buttons
        spacing: 8
        x: card.width - 14 - width; y: 14
        Repeater {
            model: card.req.actions || []
            Pill { label: modelData.label; w: Math.max(56, 16 + modelData.label.length * 6.5); primary: !!modelData.primary
                onTapped: card.decided(modelData.id) }
        }
    }
}
