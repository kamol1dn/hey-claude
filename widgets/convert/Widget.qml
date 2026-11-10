import QtQuick
import "../../ui/island"

// convert: maths, units and currency with the working shown. The answer's digits roll
// into place like an odometer (each wheel spins a turn or two more than the one to its
// left, and settles last on the right), then the working writes itself on, line by
// line, behind a small ember nib, with a thread drawing down beside it. Everything
// stops by ~1.3 s; nothing animates at rest. Data: heyclaude/mcp_info/convert.py.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 400
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var steps: (d.steps || []).slice(0, 5)
    readonly property string res: String(d.result || "")
    readonly property var chars: res.split("")
    readonly property bool hasFoot: !!(d.note || d.source || d.date)
    readonly property real lineH: 19
    readonly property real yWork: big.height + 8
    readonly property real yFoot: yWork + steps.length * lineH + 6
    implicitHeight: yFoot + (hasFoot ? 22 : 0)

    readonly property string fam: "Segoe UI Variable Text"
    readonly property color ink: "#F2F2F2"
    readonly property color dim: "#9A9A9A"
    readonly property color accent: "#D97757"
    readonly property color hot: "#EC9A78"

    function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }
    // "= 0.15 × 240": the equals sign in the accent, the operators a touch dimmer than the numbers
    function styled(s, last) {
        var t = esc(s)
        t = t.replace(/^= /, "<font color='" + w.accent + "'>=</font> ")
        return t
    }

    // ---- the answer: an odometer ------------------------------------------------------
    FontMetrics { id: bigFm; font.family: w.fam; font.pointSize: 25; font.weight: Font.DemiBold }
    FontMetrics { id: unitFm; font.family: w.fam; font.pointSize: 12 }
    readonly property real digitW: Math.ceil(bigFm.advanceWidth("0"))
    readonly property real digitH: bigFm.tightBoundingRect("0").height
    readonly property real winY: Math.max(0, bigFm.ascent - digitH - 4)       // the wheels' window, in a cell
    readonly property real winH: digitH + 8
    readonly property int nDigits: res.replace(/[^0-9]/g, "").length

    Item {
        id: big
        width: odo.width + (unit.visible ? unit.implicitWidth + 7 : 0); height: Math.ceil(bigFm.height)
        Row {
            id: odo
            Repeater {
                model: w.chars
                Item {
                    id: cell
                    readonly property bool isDigit: /[0-9]/.test(modelData)
                    // which wheel this is, counted among the digits only, from the right
                    readonly property int fromRight: {
                        var n = 0
                        for (var i = index + 1; i < w.chars.length; i++) if (/[0-9]/.test(w.chars[i])) n++
                        return n
                    }
                    readonly property int leftIdx: w.nDigits - 1 - fromRight
                    // the leftmost wheel turns once, the rightmost three times, and settles last
                    readonly property real target: isDigit ? Number(modelData) + 10 * (1 + Math.round(leftIdx * 2 / Math.max(1, w.nDigits - 1))) : 0
                    property real pos: 0
                    width: isDigit ? w.digitW : stat.implicitWidth
                    height: big.height
                    // two faces of the wheel, seen through a window just taller than a digit:
                    // the one passing up and out, the next coming up from below
                    readonly property real frac: pos - Math.floor(pos)
                    Item {
                        visible: cell.isDigit
                        y: w.winY; width: cell.width; height: w.winH
                        clip: true
                        Text {
                            y: -w.winY - cell.frac * w.winH
                            width: cell.width; horizontalAlignment: Text.AlignHCenter
                            opacity: Math.max(0, 1 - 1.4 * cell.frac)
                            text: Math.floor(cell.pos) % 10
                            color: w.ink; font: bigFm.font
                        }
                        Text {
                            visible: cell.frac > 0.001
                            y: -w.winY + (1 - cell.frac) * w.winH
                            width: cell.width; horizontalAlignment: Text.AlignHCenter
                            opacity: Math.max(0, (cell.frac - 0.3) / 0.7)
                            text: (Math.floor(cell.pos) + 1) % 10
                            color: w.ink; font: bigFm.font
                        }
                    }
                    Text {                                      // separators, the minus, "× 10¹⁵": they ink in
                        id: stat
                        visible: !cell.isDigit
                        text: modelData
                        color: w.ink; font: bigFm.font
                        opacity: w.reduced ? 1 : 0
                        NumberAnimation on opacity { running: !w.reduced && !cell.isDigit; from: 0; to: 1; duration: 380
                            easing.type: Easing.OutCubic }
                    }
                    Component.onCompleted: {
                        if (!isDigit) return
                        if (w.reduced) { pos = target; return }
                        roll.duration = Math.min(1000, 640 + leftIdx * 50)
                        rolling.start()
                    }
                    SequentialAnimation {
                        id: rolling
                        PauseAnimation { duration: 150 }             // the panel opens first
                        NumberAnimation { id: roll; target: cell; property: "pos"; to: cell.target; easing.type: Easing.OutQuart }
                    }
                }
            }
        }
        Text {
            id: unit
            visible: (w.d.unit || "") !== ""
            x: odo.width + 7
            y: bigFm.ascent - unitFm.ascent
            text: w.d.unit || ""
            color: w.hot; font: unitFm.font
            opacity: w.reduced ? 1 : 0
            SequentialAnimation on opacity {
                running: !w.reduced
                PauseAnimation { duration: 560 }
                NumberAnimation { to: 1; duration: 300; easing.type: Easing.OutCubic }
            }
        }
    }

    // ---- the working: written on, line by line -----------------------------------------
    Rectangle {                                                 // the thread beside the derivation
        x: 3; y: w.yWork + 4; width: 1; radius: 0.5
        color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.45)
        property real full: Math.max(0, w.steps.length * w.lineH - 8)
        height: w.reduced ? full : 0
        NumberAnimation on height {
            running: !w.reduced; to: Math.max(0, w.steps.length * w.lineH - 8)
            duration: 460 + w.steps.length * 160; easing.type: Easing.InOutCubic
        }
    }
    Repeater {
        model: w.steps
        Item {
            id: line
            readonly property bool last: index === w.steps.length - 1
            x: 14; y: w.yWork + index * w.lineH
            width: w.width - 14; height: w.lineH
            Item {
                id: reveal
                height: parent.height; clip: true
                width: w.reduced ? txt.implicitWidth + 2 : 0
                Text {
                    id: txt
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.StyledText
                    text: w.styled(modelData, line.last)
                    color: line.last ? w.ink : "#C4C4C4"
                    font.family: w.fam; font.pointSize: line.last ? 10 : 9.5
                    font.weight: line.last ? Font.DemiBold : Font.Normal
                    font.features: { "tnum": 1 }
                }
            }
            Rectangle {                                         // the nib at the pen's edge
                id: nib
                visible: !w.reduced
                x: reveal.width - 1; anchors.verticalCenter: parent.verticalCenter
                width: 2; height: 12; radius: 1
                color: w.hot
                opacity: 0
            }
            SequentialAnimation {
                running: !w.reduced
                PauseAnimation { duration: 460 + index * 160 }
                ParallelAnimation {
                    NumberAnimation { target: reveal; property: "width"; to: txt.implicitWidth + 2
                        duration: Math.min(420, 160 + txt.implicitWidth * 1.1); easing.type: Easing.OutSine }
                    SequentialAnimation {
                        NumberAnimation { target: nib; property: "opacity"; to: 0.9; duration: 60 }
                        PauseAnimation { duration: Math.max(0, Math.min(420, 160 + txt.implicitWidth * 1.1) - 160) }
                        NumberAnimation { target: nib; property: "opacity"; to: 0; duration: 140 }
                    }
                }
            }
        }
    }

    // ---- the footing: the rate or the formula, and where it came from --------------------
    Rise {
        visible: w.hasFoot
        i: 2 + w.steps.length * 3; reduced: w.reduced
        y: w.yFoot + 4
        Item {
            width: w.width; height: 16
            Text {
                id: note
                width: Math.min(implicitWidth, w.width - src.implicitWidth - 16); elide: Text.ElideRight
                text: w.d.note || ""
                color: w.dim; font.family: w.fam; font.pointSize: 8; font.features: { "tnum": 1 }
            }
            Text {
                id: src
                anchors.right: parent.right
                width: Math.min(implicitWidth, w.width * 0.55); elide: Text.ElideLeft
                text: [w.d.source || "", w.d.date ? (w.d.stale ? "offline, rates of " : "") + w.d.date : ""]
                      .filter(function (s) { return s }).join(" · ")
                color: w.d.stale ? "#E5646A" : "#7A7A7A"; font.family: w.fam; font.pointSize: 8
            }
        }
    }
}
