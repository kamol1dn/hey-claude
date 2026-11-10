import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// translate: the source and the translation side by side under their language tags.
// The source settles first; then an ember hops from the source's tag to the target's
// along a dotted thread that draws on behind it, the target tag pops as it lands, and
// the translation slides across into its column. Done by ~1.2 s, still afterwards.
// Data: heyclaude/mcp_info/lookup.py translate (MyMemory).
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 420
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property real gap: 24
    readonly property real colW: (width - gap) / 2
    readonly property real colX: colW + gap
    readonly property real yText: 30
    readonly property real bodyH: Math.max(srcText.height, dstCol.height)
    implicitHeight: yText + bodyH + (d.source ? 26 : 6)

    readonly property string fam: "Segoe UI Variable Text"
    readonly property color ink: "#F2F2F2"
    readonly property color dim: "#9A9A9A"
    readonly property color accent: "#D97757"
    readonly property color hot: "#EC9A78"

    // ---- the tags and the thread between them ------------------------------------------
    Rise {
        i: 0; reduced: w.reduced
        Row {
            id: srcRow
            spacing: 6
            Rectangle {
                id: srcTag
                width: srcCode.implicitWidth + 14; height: 18; radius: 9
                color: Qt.rgba(1, 1, 1, 0.08)
                InkText { id: srcCode; text: String(w.d.from || "").toUpperCase(); color: "#CFCFCF"
                    font.family: w.fam; font.pointSize: 7.5; font.weight: Font.DemiBold; font.letterSpacing: 0.8 }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: w.d.detected ? "detected" : ""
                color: "#6E6E6E"; font.family: w.fam; font.pointSize: 7.5
            }
        }
    }
    // the thread: from the source tag's edge to the target tag, an arc of dots
    readonly property real x0: srcRow.width + 8
    readonly property real x1: w.colX - 8
    readonly property real cy: 9
    readonly property real lift: 9
    property real t: w.reduced ? 1 : 0                     // the ember's way across (0..1)
    function bx(u) { return (1 - u) * (1 - u) * x0 + 2 * (1 - u) * u * ((x0 + x1) / 2) + u * u * x1 }
    function by(u) { return (1 - u) * (1 - u) * cy + 2 * (1 - u) * u * (cy - 2 * lift) + u * u * cy }
    Item {
        x: 0; y: 0; height: 22
        width: w.bx(w.t) + 1
        clip: true
        Shape {
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.55); strokeWidth: 1.2; fillColor: "transparent"
                strokeStyle: ShapePath.DashLine; dashPattern: [0.5, 3]; capStyle: ShapePath.RoundCap
                startX: w.x0; startY: w.cy
                PathQuad { x: w.x1; y: w.cy; controlX: (w.x0 + w.x1) / 2; controlY: w.cy - 2 * w.lift }
            }
        }
    }
    Rectangle {                                             // the ember
        id: ember
        visible: !w.reduced
        width: 7; height: 7; radius: 3.5
        x: w.bx(w.t) - 3.5; y: w.by(w.t) - 3.5
        color: w.hot; border.width: 1.5; border.color: w.accent
        opacity: 0
    }
    Rectangle {
        id: dstTag
        x: w.colX; y: 0
        width: dstCode.implicitWidth + 14; height: 18; radius: 9
        color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.2)
        border.width: 1; border.color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.35)
        transformOrigin: Item.Left
        scale: w.reduced ? 1 : 0.6
        opacity: w.reduced ? 1 : 0
        InkText { id: dstCode; text: String(w.d.to || "").toUpperCase(); color: w.hot
            font.family: w.fam; font.pointSize: 7.5; font.weight: Font.DemiBold; font.letterSpacing: 0.8 }
    }

    // ---- the two sides ------------------------------------------------------------------
    Rise {
        i: 1; reduced: w.reduced
        y: w.yText
        Text {
            id: srcText
            width: w.colW
            wrapMode: Text.WordWrap; maximumLineCount: 6; elide: Text.ElideRight
            text: w.d.text || ""
            color: "#B8B8B8"; font.family: w.fam; font.pointSize: 10; lineHeight: 1.05
        }
    }
    Rectangle {                                             // the fold between them
        x: w.colW + w.gap / 2; y: w.yText + 2; width: 1; height: Math.max(0, w.bodyH - 4)
        color: Qt.rgba(1, 1, 1, 0.07)
    }
    Item {
        id: dstSlide
        x: w.colX; y: w.yText
        width: w.colW; height: dstCol.height
        opacity: w.reduced ? 1 : 0
        transform: Translate { id: slide; x: w.reduced ? 0 : -w.colW * 0.45 }
        Column {
            id: dstCol
            width: parent.width
            spacing: 6
            Text {
                width: parent.width
                wrapMode: Text.WordWrap; maximumLineCount: 6; elide: Text.ElideRight
                text: w.d.translation || ""
                color: w.ink; font.family: w.fam; font.pointSize: 11.5; font.weight: Font.Medium; lineHeight: 1.05
            }
            Text {
                visible: (w.d.alt || "") !== ""
                width: parent.width; elide: Text.ElideRight
                text: "also “" + (w.d.alt || "") + "”"
                color: w.dim; font.family: w.fam; font.italic: true; font.pointSize: 8.5
            }
        }
    }
    Rise {
        visible: (w.d.source || "") !== ""
        i: 16; reduced: w.reduced
        y: w.yText + w.bodyH + 8
        Text {
            width: w.width; horizontalAlignment: Text.AlignRight
            text: w.d.source || ""
            color: "#7A7A7A"; font.family: w.fam; font.pointSize: 8
        }
    }

    SequentialAnimation {
        running: !w.reduced
        PauseAnimation { duration: 320 }
        NumberAnimation { target: ember; property: "opacity"; to: 1; duration: 80 }
        NumberAnimation { target: w; property: "t"; from: 0; to: 1; duration: 480; easing.type: Easing.InOutCubic }
        ParallelAnimation {
            NumberAnimation { target: ember; property: "opacity"; to: 0; duration: 180 }
            NumberAnimation { target: dstTag; property: "opacity"; to: 1; duration: 160 }
            NumberAnimation { target: dstTag; property: "scale"; to: 1; duration: 340; easing.type: Easing.OutBack; easing.overshoot: 2.2 }
            NumberAnimation { target: dstSlide; property: "opacity"; to: 1; duration: 340; easing.type: Easing.OutCubic }
            NumberAnimation { target: slide; property: "x"; to: 0; duration: 460; easing.type: Easing.OutCubic }
        }
    }
}
