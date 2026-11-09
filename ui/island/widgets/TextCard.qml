import QtQuick

// text: a plain card (also what an unknown or invalid widget falls back to).
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    signal action(string name, var payload)
    readonly property real minWidth: 340
    readonly property bool holds: false
    readonly property var d: data_ || ({})
    implicitHeight: (d.title ? 18 : 0) + body.implicitHeight + 4
    Text { visible: !!w.d.title; text: w.d.title || ""; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
    Text { id: body; y: w.d.title ? 18 : 0; width: w.width - 24; text: w.d.text || ""; wrapMode: Text.Wrap; maximumLineCount: 5
        elide: Text.ElideRight; color: "#F2F2F2"; textFormat: Text.PlainText; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
}
