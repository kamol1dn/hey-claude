import QtQuick
import "../../ui/island"

// news: up to five headlines from a web search (title, source, date). Entry: the
// headlines run in along the top line like a news ticker, one after another, and
// each drops into its row as the next one arrives, its marker cooling from hot to
// ember as it settles. One-shot (~1.1 s), then still. A click opens the story.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var rows: (d.headlines || []).slice(0, 5)
    readonly property real rowH: 38
    implicitHeight: rows.length * rowH + 2
    clip: true

    Repeater {
        model: w.rows
        Item {
            id: row
            objectName: "headlineRow"
            property int i: index
            width: w.width; height: w.rowH
            // the entry: the rows come in as one ticker tape, each a step further along it, gliding
            // left fast and slowing into the column; the source line follows once a row has landed
            x: w.reduced ? 0 : w.width * 0.5 + index * 110
            y: index * w.rowH
            opacity: 0
            property real heat: w.reduced ? 0 : 1          // 1: just in (hot marker), 0: settled
            property real meta: w.reduced ? 1 : 0
            SequentialAnimation {
                id: tickIn
                PauseAnimation { duration: 60 + row.i * 55 }
                ParallelAnimation {
                    NumberAnimation { target: row; property: "opacity"; to: 1; duration: 160 }
                    NumberAnimation { target: row; property: "x"; to: 0; duration: 620 + row.i * 40; easing.type: Easing.OutQuint }
                    SequentialAnimation {
                        PauseAnimation { duration: 300 }
                        ParallelAnimation {
                            NumberAnimation { target: row; property: "meta"; to: 1; duration: 320; easing.type: Easing.OutCubic }
                            NumberAnimation { target: row; property: "heat"; to: 0; duration: 520; easing.type: Easing.OutCubic }
                        }
                    }
                }
            }
            NumberAnimation { id: fadeIn; target: row; property: "opacity"; to: 1; duration: 160 }
            Component.onCompleted: w.reduced ? fadeIn.start() : tickIn.start()

            Rectangle {                                       // hover
                anchors.fill: parent; anchors.leftMargin: -6; anchors.rightMargin: -6; radius: 8
                color: Qt.rgba(1, 1, 1, ma.containsMouse && modelData.link ? 0.05 : 0)
            }
            Rectangle {                                       // the row's marker
                x: 0; width: 3; height: 26; radius: 1.5; anchors.verticalCenter: parent.verticalCenter
                color: Qt.rgba(0.85 + 0.075 * row.heat, 0.467 + 0.137 * row.heat, 0.341 + 0.13 * row.heat, 0.55 + 0.45 * row.heat)
            }
            Column {
                x: 13; width: parent.width - 13 - (arrow.visible ? 18 : 0); anchors.verticalCenter: parent.verticalCenter
                Text {
                    width: parent.width; text: modelData.title || ""
                    elide: Text.ElideRight; textFormat: Text.PlainText
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10
                }
                Text {
                    width: parent.width
                    opacity: row.meta
                    text: [modelData.source || "", modelData.time || ""].filter(function (s) { return s }).join("  ·  ")
                    elide: Text.ElideRight; textFormat: Text.PlainText
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                }
            }
            Text {
                id: arrow
                visible: ma.containsMouse && !!modelData.link
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                text: "↗"; color: "#EC9A78"; font.family: "Segoe UI Variable Text"; font.pointSize: 10
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
