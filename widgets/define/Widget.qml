import QtQuick
import "../../ui/island"

// define: a dictionary entry. The headword inks in letter by letter (each glyph lands
// in the ember's colour and dries to white), the pronunciation follows, then the
// senses turn in like a page swinging on its spine at the left edge, a shadow lifting
// off it as it lies flat. Done in ~1.1 s; still afterwards.
// Data: heyclaude/mcp_info/lookup.py define (dictionaryapi.dev, Wiktionary).
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 400
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var senses: (d.senses || []).slice(0, 3)
    // letters, keeping a combining accent (кни́га) with its letter
    readonly property var letters: String(d.word || "").match(/.[̀-ͯ]*/g) || []
    readonly property real stagger: Math.min(42, 380 / Math.max(1, letters.length))
    readonly property real yPage: 46
    implicitHeight: yPage + page.height + (d.source ? 24 : 6)

    readonly property string fam: "Segoe UI Variable Text"
    readonly property color ink: "#F2F2F2"
    readonly property color dim: "#9A9A9A"
    readonly property color accent: "#D97757"
    readonly property color hot: "#EC9A78"

    // ---- the headword, inked in ---------------------------------------------------------
    Row {
        id: word
        y: 2
        Repeater {
            model: w.letters
            Text {
                id: ch
                text: modelData
                font.family: "Georgia"; font.pointSize: 21; font.weight: Font.DemiBold
                color: w.reduced ? w.ink : w.accent
                opacity: w.reduced ? 1 : 0
                transform: Translate { id: drop; y: w.reduced ? 0 : -4 }
                SequentialAnimation {
                    running: !w.reduced
                    PauseAnimation { duration: 140 + index * w.stagger }
                    ParallelAnimation {
                        NumberAnimation { target: ch; property: "opacity"; to: 1; duration: 200; easing.type: Easing.OutCubic }
                        NumberAnimation { target: drop; property: "y"; to: 0; duration: 260; easing.type: Easing.OutBack }
                        SequentialAnimation {
                            PauseAnimation { duration: 140 }
                            ColorAnimation { target: ch; property: "color"; to: w.ink; duration: 420; easing.type: Easing.InOutSine }
                        }
                    }
                }
            }
        }
    }
    FontMetrics { id: wordFm; font.family: "Georgia"; font.pointSize: 21 }
    FontMetrics { id: ipaFm; font.family: w.fam; font.pointSize: 10.5 }
    Text {
        id: ipa
        visible: (w.d.ipa || "") !== ""
        x: word.width + 12; y: word.y + wordFm.ascent - ipaFm.ascent
        width: Math.min(implicitWidth, w.width - x); elide: Text.ElideRight
        text: w.d.ipa || ""
        color: w.dim; font: ipaFm.font
        opacity: w.reduced ? 1 : 0
        SequentialAnimation on opacity {
            running: !w.reduced
            PauseAnimation { duration: 240 + w.letters.length * w.stagger }
            NumberAnimation { to: 1; duration: 320; easing.type: Easing.OutCubic }
        }
    }
    Rectangle {                                         // the rule under the headword, drawn from the left
        y: w.yPage - 9; height: 1
        color: Qt.rgba(1, 1, 1, 0.07)
        property real u: w.reduced ? 1 : 0
        width: w.width * u
        NumberAnimation on u { running: !w.reduced; to: 1; duration: 520; easing.type: Easing.OutCubic }
    }

    // ---- the senses: a page turning in ----------------------------------------------------
    Item {
        id: page
        y: w.yPage
        width: w.width; height: col.implicitHeight
        opacity: w.reduced ? 1 : 0
        transform: Rotation {
            id: turn
            origin.x: 0; origin.y: page.height / 2
            axis { x: 0; y: 1; z: 0 }
            angle: w.reduced ? 0 : -78
        }
        Column {
            id: col
            width: parent.width
            spacing: 5
            Repeater {
                model: w.senses
                Item {
                    readonly property bool newPos: (modelData.pos || "") !== "" && (index === 0 || (w.senses[index - 1].pos || "") !== modelData.pos)
                    // senses are numbered within their part of speech, as in a dictionary
                    readonly property int num: {
                        var n = 1
                        for (var i = index - 1; i >= 0 && (w.senses[i].pos || "") === (modelData.pos || ""); i--) n++
                        return n
                    }
                    width: col.width
                    height: body.y + body.height
                    Text {
                        id: pos
                        visible: parent.newPos
                        height: visible ? implicitHeight + 2 : 0
                        text: modelData.pos || ""
                        color: w.hot; font.family: "Georgia"; font.italic: true; font.pointSize: 9
                    }
                    Text {
                        y: body.y
                        width: 16
                        text: parent.num + "."
                        color: w.accent; font.family: w.fam; font.pointSize: 9; font.features: { "tnum": 1 }
                    }
                    Column {
                        id: body
                        x: 16; y: pos.height
                        width: parent.width - 16
                        spacing: 1
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                            text: modelData.text
                            color: "#E8E8E8"; font.family: w.fam; font.pointSize: 9.5
                        }
                        Text {
                            visible: (modelData.example || "") !== ""
                            width: parent.width
                            elide: Text.ElideRight
                            text: "“" + (modelData.example || "") + "”"
                            color: w.dim; font.family: "Georgia"; font.italic: true; font.pointSize: 8.5
                        }
                        Text {
                            visible: (modelData.example_tr || "") !== ""
                            width: parent.width
                            elide: Text.ElideRight
                            text: modelData.example_tr || ""
                            color: "#7A7A7A"; font.family: w.fam; font.pointSize: 8
                        }
                    }
                }
            }
        }
        Rectangle {                                     // the page's shadow, lifting as it lies flat
            visible: !w.reduced
            anchors.fill: parent
            opacity: Math.min(1, Math.abs(turn.angle) / 60) * 0.9
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Qt.rgba(0.03, 0.03, 0.03, 0.2) }
                GradientStop { position: 1; color: Qt.rgba(0.03, 0.03, 0.03, 1) }
            }
        }
    }
    SequentialAnimation {
        running: !w.reduced
        PauseAnimation { duration: 400 }                // once the panel is open and the word is inked
        ParallelAnimation {
            NumberAnimation { target: turn; property: "angle"; to: 0; duration: 720; easing.type: Easing.OutQuad }
            NumberAnimation { target: page; property: "opacity"; to: 1; duration: 300; easing.type: Easing.OutCubic }
        }
    }
    NumberAnimation { running: w.reduced; target: page; property: "opacity"; from: 0; to: 1; duration: 160 }

    Rise {
        visible: (w.d.source || "") !== ""
        i: 6; reduced: w.reduced
        y: w.yPage + page.height + 8
        Text {
            width: w.width; horizontalAlignment: Text.AlignRight
            text: w.d.source || ""
            color: "#7A7A7A"; font.family: w.fam; font.pointSize: 8
        }
    }
}
