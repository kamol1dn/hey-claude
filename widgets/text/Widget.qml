import QtQuick
import "../../ui/island"

// text: a plain card of text (also what an unknown or invalid widget falls
// back to). The title, if any, is the host's header; the body is the text in
// a calm reading size: a short line reads a little larger, a longer note
// steps down so up to six lines fit. Lines rise in once, then it is still.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 400
    property bool holds: false

    readonly property var d: data_ || ({})
    readonly property string txt: String(d.text || "").trim()
    readonly property bool short_: txt.length <= 80 && txt.indexOf("\n") < 0
    implicitHeight: Math.max(26, body.implicitHeight + 8)

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
        y: 3; width: w.width; height: body.implicitHeight
        still: w.reduced
        Text {
            id: body
            width: parent.width - 8
            text: w.txt
            wrapMode: Text.Wrap; maximumLineCount: 6; elide: Text.ElideRight
            textFormat: Text.PlainText
            lineHeight: 1.12
            color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: w.short_ ? 10 : 9.5
        }
    }
}
