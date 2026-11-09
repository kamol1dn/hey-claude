import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// image: a local file or an https URL in a rounded 170 px frame, caption laid
// over a soft bottom shade. The picture settles from a slight zoom (1.06 -> 1)
// once it has loaded. A very tall or very wide picture is shown whole over a
// dimmed, cropped copy of itself instead of being cut. A click opens it.
// The rounded corners are a Shape filled with the picture (GPU, no effects).
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false

    readonly property var d: data_ || ({})
    readonly property string src: {
        if (d.src) return d.src                                   // resolved by widgets.py
        if (d.url) return d.url
        var p = String(d.path || "")
        return p ? (/^[a-z]+:\/\//i.test(p) ? p : "file:///" + p.replace(/\\/g, "/").replace(/^\/+/, "")) : ""
    }
    readonly property string target: d.url || d.path || ""
    readonly property bool ready: img.status === Image.Ready
    readonly property real boxH: 170
    // the picture's shape against the frame's: crop unless that would cut too much.
    // Decided once on load (a binding would loop through fillMode -> the image's size).
    property bool fit: false
    function decide() {
        var r = img.implicitHeight > 0 ? img.implicitWidth / img.implicitHeight : 1, f = (w.width || 408) / boxH
        fit = r < f * 0.6 || r > f * 1.9
    }
    implicitHeight: boxH

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
        width: w.width; height: w.boxH
        still: w.reduced

        // the placeholder (loading / error), also the frame's tone under a transparent picture
        Rectangle {
            anchors.fill: parent; radius: 12
            color: Qt.rgba(1, 1, 1, 0.05)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.05)
            Column {
                visible: !w.ready
                anchors.centerIn: parent; spacing: 6
                Glyph { anchors.horizontalCenter: parent.horizontalCenter; name: "image"; color: "#6f6a66"; s: 1 }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: img.status === Image.Error || w.src === "" ? "couldn't load the image" : "loading…"
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
                }
            }
        }

        // the picture, composed off-screen, then poured into the rounded shape
        Item {
            id: pic
            width: w.width; height: w.boxH
            visible: false
            Image {                                               // backdrop for a picture shown whole
                // always loaded (a small copy) and gated by opacity: a late load or a
                // visibility flip inside the hidden source tree never reaches the texture
                anchors.fill: parent
                source: w.src
                fillMode: Image.PreserveAspectCrop
                sourceSize.width: 220
                asynchronous: true; smooth: true
                opacity: w.fit ? 0.22 : 0
            }
            Image {
                id: img
                anchors.fill: parent
                source: w.src
                fillMode: w.fit ? Image.PreserveAspectFit : Image.PreserveAspectCrop
                sourceSize.width: 880
                asynchronous: true; smooth: true; mipmap: true
                transformOrigin: Item.Center
                scale: 1
                onStatusChanged: if (status === Image.Ready) { w.decide(); if (!w.reduced) settle.start() }
            }
            Rectangle {                                           // a soft shade under the caption
                visible: !!w.d.caption
                anchors.bottom: parent.bottom; width: parent.width; height: 56
                gradient: Gradient {
                    GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0) }
                    GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.55) }
                }
            }
            Text {
                visible: !!w.d.caption
                x: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 9; width: parent.width - 24
                text: w.d.caption || ""; elide: Text.ElideRight; textFormat: Text.PlainText
                color: "#FFFFFF"; font.family: "Segoe UI Variable Text"; font.pointSize: 9
            }
        }
        NumberAnimation { id: settle; target: img; property: "scale"; from: 1.06; to: 1; duration: 1400; easing.type: Easing.OutCubic }
        ShaderEffectSource { id: picTex; sourceItem: pic; hideSource: true; visible: false; width: pic.width; height: pic.height }
        Shape {
            id: frame
            anchors.fill: parent
            visible: w.ready
            opacity: w.ready ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: w.reduced ? 160 : 260; easing.type: Easing.OutCubic } }
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: -1; strokeColor: "transparent"
                fillItem: picTex
                PathRectangle { x: 0; y: 0; width: frame.width; height: frame.height; radius: 12 }
            }
        }
        Rectangle {                                               // a hairline on top, like the other cards
            visible: w.ready
            anchors.fill: parent; radius: 12; color: "transparent"
            border.width: 1; border.color: Qt.rgba(1, 1, 1, ma.containsMouse ? 0.14 : 0.06)
        }
        Rectangle {                                               // "Open" on hover
            visible: ma.containsMouse && w.target !== ""
            x: parent.width - width - 8; y: 8; height: 20; radius: 10; width: openT.implicitWidth + 16
            color: Qt.rgba(0, 0, 0, 0.55)
            Text { id: openT; anchors.centerIn: parent; text: "Open ↗"; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
        }
        MouseArea {
            id: ma
            anchors.fill: parent; hoverEnabled: true
            cursorShape: w.target ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: { if (w.target) w.action("open", w.target) }
        }
    }
}
