import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// clipboard: what's on the clipboard, or a screenshot. Private: it fades when the
// island rests (the card frame does that).
//   text / code / link   a torn paper slip slides in and settles (a small tilt that
//                        straightens); code is monospace with line numbers, its lines
//                        printed in one after another; a link shows its host and Open
//   image / files / ...  a short slip that says what the clipboard holds instead
//   screenshot           a shutter: two blades part with a flash, the picture settles
//                        from a slight zoom and the viewfinder corners close in on it
// All of it once, on entry; then nothing moves.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 400
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property string kind: d.kind || "empty"
    readonly property bool isShot: kind === "screenshot"
    readonly property bool hasText: (d.text || "") !== ""
    readonly property bool isCode: kind === "code" && hasText
    readonly property string mono: "Cascadia Mono"
    readonly property var codeLines: isCode ? d.text.split("\n").slice(0, maxCode) : []
    readonly property int maxCode: 9
    implicitHeight: isShot ? shot.height + 2 : slip.height + 4

    function plural(n, one, many) { return n + " " + (n === 1 ? one : many) }
    function num(n) { return Number(n || 0).toLocaleString(Qt.locale("en_GB"), "f", 0) }
    function sizeText() {
        var parts = [plural(d.chars || 0, "character", "characters").replace(/^\d+/, num(d.chars))]
        if ((d.lines || 0) > 1) parts.push(plural(d.lines, "line", "lines"))
        return parts.join("  ·  ")
    }
    function holdsText() {
        return ({ image: "An image, not text. Ask me what's in it.", files: "Copied files, not text.",
                  empty: "Nothing to paste: the clipboard is empty.", other: "Something that isn't text." })[kind]
               || "Nothing to show."
    }

    // ======================================================================= the slip
    property real s: reduced ? 1 : 0                  // the slide (0 -> 1)
    property real ink: reduced ? 1 : 0                // the lines being "printed"
    SequentialAnimation {
        running: !w.reduced && !w.isShot
        PauseAnimation { duration: 120 }
        ParallelAnimation {
            NumberAnimation { target: w; property: "s"; to: 1; duration: 560; easing.type: Easing.OutBack; easing.overshoot: 0.9 }
            SequentialAnimation {
                PauseAnimation { duration: 200 }
                NumberAnimation { target: w; property: "ink"; to: 1; duration: 520; easing.type: Easing.OutCubic }
            }
        }
    }
    Item {
        id: slip
        visible: !w.isShot
        width: w.width
        height: body.height + (w.hasText ? 46 : 26)
        y: 2
        x: w.reduced ? 0 : -30 * (1 - w.s)
        opacity: w.reduced ? 1 : Math.min(1, w.s * 2.5)
        rotation: w.reduced ? 0 : -3.2 * (1 - w.s)
        transformOrigin: Item.BottomLeft

        // the paper: a torn top edge, a faint fold shadow
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: Qt.rgba(1, 0.95, 0.91, 0.055)
                strokeColor: Qt.rgba(1, 1, 1, 0.075); strokeWidth: 1
                PathSvg { path: w.paper(slip.width, slip.height) }
            }
        }
        Rectangle { x: 1; y: slip.height - 3; width: slip.width - 2; height: 2; radius: 1; color: Qt.rgba(0, 0, 0, 0.25) }
        // the accent stitch on the left edge: which kind it is
        Rectangle {
            x: 0; y: 12; width: 2; height: slip.height - 24; radius: 1
            color: w.kind === "code" ? "#8FB6D8" : w.kind === "link" ? "#EC9A78" : w.hasText ? "#D97757" : "#6A6A6A"
            opacity: 0.85
        }

        Item {
            id: body
            x: 14; y: 13; width: slip.width - 28
            height: w.isCode ? codeCol.height : w.kind === "link" ? linkCol.height : w.hasText ? prose.height : holdsRow.height

            // prose
            Text {
                id: prose
                visible: w.hasText && !w.isCode && w.kind !== "link"
                width: parent.width
                text: visible ? w.d.text : ""
                wrapMode: Text.Wrap; maximumLineCount: 7; elide: Text.ElideRight
                textFormat: Text.PlainText
                lineHeight: 1.15
                color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
                opacity: w.ink
            }
            // code: numbered, one line after another
            Column {
                id: codeCol
                visible: w.isCode
                width: parent.width
                Repeater {
                    model: w.codeLines
                    Item {
                        width: codeCol.width; height: 15
                        readonly property real k: Math.max(0, Math.min(1, w.ink * (w.codeLines.length + 3) - index))
                        opacity: k
                        Text {
                            width: 18; horizontalAlignment: Text.AlignRight
                            text: index + 1
                            color: "#5E5E5E"; font.family: w.mono; font.pointSize: 7.5; font.features: { "tnum": 1 }
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            x: 28 - 6 * (1 - parent.k); width: parent.width - 28
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData
                            textFormat: Text.PlainText; elide: Text.ElideRight
                            color: modelData.trim().charAt(0) === "#" || modelData.trim().indexOf("//") === 0 ? "#8A8A8A" : "#E8E4DF"
                            font.family: w.mono; font.pointSize: 8.3
                        }
                    }
                }
            }
            // a link: the host big, the address under it, Open
            Column {
                id: linkCol
                visible: w.kind === "link" && w.hasText
                width: parent.width
                spacing: 3
                opacity: w.ink
                Text {
                    width: parent.width - 70; elide: Text.ElideRight
                    text: w.d.host || ""
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 11; font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width - 70; elide: Text.ElideMiddle
                    text: linkCol.visible ? w.d.text : ""
                    textFormat: Text.PlainText
                    color: "#9A9A9A"; font.family: w.mono; font.pointSize: 8
                }
            }
            Pill {
                visible: linkCol.visible
                anchors.right: parent.right; y: 4
                label: "Open"; w: 56; accent: true
                onTapped: w.action("open", w.d.text)
            }
            // not text
            Row {
                id: holdsRow
                visible: !w.hasText
                spacing: 10
                opacity: w.ink
                Glyph { name: w.kind === "image" ? "image" : w.kind === "files" ? "file" : "clipboard"; s: 0.8; color: "#9A9A9A"
                    anchors.verticalCenter: parent.verticalCenter }
                Text { text: w.holdsText(); color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
                    anchors.verticalCenter: parent.verticalCenter }
            }
        }
        // the size, and what's cut
        Text {
            visible: w.hasText
            x: 14; y: slip.height - 25
            text: w.sizeText()
            color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5; font.features: { "tnum": 1 }
            opacity: w.ink
        }
        Text {
            visible: w.hasText
            anchors.right: parent.right; anchors.rightMargin: 14; y: slip.height - 25
            text: (w.d.more ? "+" + w.num(w.d.more) + " more  ·  " : "") + (w.kind === "code" ? "looks like code" : w.kind === "link" ? "a link" : "plain text")
            color: w.kind === "code" ? "#8FB6D8" : "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5
            opacity: w.ink
        }
    }
    // the slip's outline: straight sides, a torn top
    function paper(wd, ht) {
        var r = 7, step = 9, p = "M0 " + (ht - r) + " V5"
        var up = true
        for (var x = 0; x < wd; x += step) {
            var nx = Math.min(wd, x + step)
            p += " L" + nx.toFixed(1) + " " + (up ? 1.2 : 4.6)
            up = !up
        }
        p += " V" + (ht - r) + " Q" + wd + " " + ht + " " + (wd - r) + " " + ht + " H" + r + " Q0 " + ht + " 0 " + (ht - r) + " Z"
        return p
    }

    // ======================================================================= the screenshot
    property real open_: reduced ? 1 : 0            // the blades part
    property real settle: reduced ? 1 : 0           // the picture's zoom and the corners
    property real flash: 0
    readonly property real aspect: (d.width && d.height) ? d.width / d.height : 16 / 9
    readonly property real frameH: Math.min(150, (w.width - 112) / aspect)
    readonly property real frameW: frameH * aspect
    readonly property string shotAt: Qt.formatTime(new Date(), "HH:mm")
    SequentialAnimation {
        running: !w.reduced && w.isShot
        PauseAnimation { duration: 140 }
        ParallelAnimation {
            NumberAnimation { target: w; property: "open_"; to: 1; duration: 420; easing.type: Easing.InOutCubic }
            SequentialAnimation {
                PauseAnimation { duration: 120 }
                NumberAnimation { target: w; property: "flash"; from: 0.85; to: 0; duration: 380; easing.type: Easing.OutQuad }
            }
            NumberAnimation { target: w; property: "settle"; to: 1; duration: 900; easing.type: Easing.OutCubic }
        }
    }
    Item {
        id: shot
        visible: w.isShot
        width: w.width; height: w.frameH + 4
        Item {
            id: frame
            objectName: "screenshotFrame"
            x: 2; y: 2; width: w.frameW; height: w.frameH
            clip: true
            Rectangle { anchors.fill: parent; color: "#0C0C0E" }
            Image {
                id: pic
                anchors.fill: parent
                source: w.d.src || ""
                visible: source !== ""
                fillMode: Image.PreserveAspectCrop
                smooth: true; mipmap: true; asynchronous: true
                scale: 1.09 - 0.09 * w.settle
            }
            Column {                                   // no preview came with it
                visible: !pic.visible
                anchors.centerIn: parent; spacing: 6
                Glyph { name: "camera"; color: "#9A9A9A"; anchors.horizontalCenter: parent.horizontalCenter }
                Text { text: "no preview"; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
            }
            // the shutter blades, an ember line on each edge
            Rectangle {
                width: parent.width; height: parent.height / 2 + 1
                y: -height * w.open_
                color: "#141416"
                visible: w.open_ < 1
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1.5; color: "#D97757"; opacity: 0.9 }
            }
            Rectangle {
                width: parent.width; height: parent.height / 2 + 1
                y: parent.height / 2 + (parent.height / 2) * w.open_
                color: "#141416"
                visible: w.open_ < 1
                Rectangle { width: parent.width; height: 1.5; color: "#D97757"; opacity: 0.9 }
            }
            Rectangle { anchors.fill: parent; color: "#FFF6EE"; opacity: w.flash; visible: opacity > 0.01 }
        }
        Rectangle { x: 2; y: 2; width: w.frameW; height: w.frameH; color: "transparent"; border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.12) }
        // the viewfinder corners close in, then rest faint
        Repeater {
            model: 4
            Item {
                readonly property bool isR: index % 2 === 1
                readonly property bool isB: index > 1
                readonly property real gap: 9 * (1 - w.settle) - 4
                x: isR ? 2 + w.frameW - 12 + gap : 2 - gap
                y: isB ? 2 + w.frameH - 12 + gap : 2 - gap
                width: 12; height: 12
                opacity: w.reduced ? 0.45 : Math.min(1, w.settle * 3) * (1 - 0.55 * Math.max(0, w.settle * 2 - 1))
                Rectangle { x: parent.isR ? 10.5 : 0; width: 1.5; height: 12; color: "#EC9A78" }
                Rectangle { y: parent.isB ? 10.5 : 0; width: 12; height: 1.5; color: "#EC9A78" }
            }
        }
        // what it is
        Rise {
            i: 2; reduced: w.reduced
            x: w.frameW + 18; y: 4
            Column {
                spacing: 3
                Text { text: w.d.width && w.d.height ? w.d.width + " × " + w.d.height : "Screenshot"
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10; font.weight: Font.DemiBold; font.features: { "tnum": 1 } }
                Text { text: (w.d.monitor || 0) === 0 ? "main display" : "display " + (w.d.monitor + 1)
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                Text { text: "taken " + w.shotAt
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.features: { "tnum": 1 } }
                Item { width: 1; height: 6 }
                Text { text: "sent to Claude"; color: "#EC9A78"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5 }
            }
        }
    }
}
