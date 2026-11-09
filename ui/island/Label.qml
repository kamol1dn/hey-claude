import QtQuick

// One label layer of the island: text that crossfades and, for "listening"
// after a wake and the done label, is revealed behind a pen edge.
Item {
    id: lab
    property alias text: t.text
    property bool big: false
    property alias color: t.color
    property real reveal: 1
    property string icon: ""
    visible: false
    width: t.implicitWidth; height: t.implicitHeight
    Image {
        visible: lab.icon !== ""
        source: lab.icon
        x: -20; y: (t.height - 14) / 2 + 1; width: 14; height: 14
        sourceSize: Qt.size(28, 28)
        smooth: true; asynchronous: true
    }
    Item {
        clip: lab.reveal < 1
        width: lab.reveal < 1 ? lab.reveal * (t.implicitWidth + 12) : t.implicitWidth + 2
        height: t.height
        Text {
            id: t
            font.family: "Segoe UI Variable Text"
            font.pointSize: lab.big ? 10 : 9
            textFormat: Text.PlainText
        }
    }
}
