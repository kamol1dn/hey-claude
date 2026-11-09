import QtQuick
import "../../ui/island"

// link: a web link or a local file as one tappable row: a warm tile with the
// link / file glyph, the title, a dim description and the host (or folder) in
// mono. A click opens it (QmlIsland -> widgets.safe_open, which never runs a
// program: those are shown in Explorer). The host draws the card header.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false

    readonly property var d: data_ || ({})
    readonly property bool isFile: !d.url && !!d.path
    readonly property bool missing: isFile && d.path_ok === false
    readonly property string target: d.url || d.path || ""
    readonly property string host: {
        var u = String(d.url || "")
        if (/^tg:\/\//i.test(u)) return "Telegram"
        var m = u.match(/^[a-z]+:\/\/([^\/?#]+)/i)
        return m ? m[1].replace(/^www\./i, "") : ""
    }
    readonly property var parts: String(d.path || "").split(/[\\\/]+/).filter(function (s) { return s !== "" })
    readonly property string fileName: parts.length ? parts[parts.length - 1] : ""
    readonly property string folder: parts.length > 1 ? parts.slice(Math.max(0, parts.length - 3), parts.length - 1).join(" › ") : ""
    readonly property string where: isFile ? folder : host
    implicitHeight: 50

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
        width: w.width; height: 50
        still: w.reduced

        Rectangle {
            anchors.fill: parent; radius: 12
            color: Qt.rgba(1, 1, 1, ma.containsMouse && !w.missing ? 0.07 : 0.04)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.05)
            Behavior on color { ColorAnimation { duration: 140 } }
        }
        Rectangle {
            id: tile
            x: 7; y: 7; width: 36; height: 36; radius: 9
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.26) }
                GradientStop { position: 1; color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.06) }
            }
            Glyph {
                anchors.centerIn: parent
                name: w.isFile ? "file" : "link"
                color: w.isFile ? "#EC9A78" : "#F2F2F2"
                s: w.isFile ? 0.92 : 0.8
            }
        }
        Text {
            id: aff
            anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
            text: w.missing ? "not found" : w.isFile ? "Open" : "↗"
            color: w.missing ? "#6f6a66" : ma.containsMouse ? "#EC9A78" : "#9A9A9A"
            font.family: "Segoe UI Variable Text"; font.pointSize: w.isFile || w.missing ? 8.5 : 10
        }
        Column {
            x: 55; anchors.verticalCenter: parent.verticalCenter
            width: aff.x - 12 - x
            spacing: 1
            Text {
                width: parent.width
                text: w.d.title || w.fileName || w.target
                color: w.missing ? "#9A9A9A" : "#F2F2F2"; elide: Text.ElideRight; textFormat: Text.PlainText
                font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
            }
            Row {
                spacing: 8
                visible: desc.text !== "" || loc.text !== ""
                Text {
                    id: desc
                    visible: text !== ""
                    width: Math.min(implicitWidth, parent.parent.width - (loc.text ? loc.implicitWidth + 8 : 0))
                    text: w.d.description || ""
                    color: "#9A9A9A"; elide: Text.ElideRight; textFormat: Text.PlainText
                    font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
                }
                Text {
                    id: loc
                    visible: text !== ""
                    anchors.baseline: desc.text ? desc.baseline : undefined
                    width: Math.min(implicitWidth, parent.parent.width)
                    text: w.where
                    color: "#6f6a66"; elide: Text.ElideMiddle; textFormat: Text.PlainText
                    font.family: "Cascadia Mono"; font.pointSize: 8
                }
            }
        }
        MouseArea {
            id: ma
            anchors.fill: parent; hoverEnabled: true
            cursorShape: w.target && !w.missing ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: { if (w.target && !w.missing) w.action("open", w.target) }
        }
    }
}
