import QtQuick
import "../../ui/island"

// code: a short snippet (at most 12 lines) in a quiet mono block: a top strip
// with the file name / language and Copy, line numbers, light syntax colour
// (keywords, strings, numbers, comments, defined names). Long lines are
// clipped at the edge; Copy hands the whole snippet to the host ("copy").
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 460
    property bool holds: false

    readonly property var d: data_ || ({})
    readonly property var lines: String(d.code || "").replace(/\r/g, "").replace(/\t/g, "    ").replace(/\n+$/, "").split("\n").slice(0, 12)
    readonly property int n: Math.max(1, lines.length)
    // fit 12 lines into the body budget (196): roomy for short snippets, tight for long ones
    readonly property int lh: Math.max(13, Math.min(18, Math.floor(156 / n)))
    readonly property real fs: lh >= 16 ? 8.5 : 8
    readonly property string lang: String(d.language || "").toLowerCase()
    property bool copied: false
    implicitHeight: 28 + 6 + n * lh + 6

    // ---- a small tokenizer (StyledText out; every token escaped) ---------------------
    readonly property var kw: ({
        "def": 1, "return": 1, "if": 1, "elif": 1, "else": 1, "for": 1, "while": 1, "in": 1, "not": 1, "and": 1, "or": 1,
        "is": 1, "import": 1, "from": 1, "as": 1, "class": 1, "with": 1, "try": 1, "except": 1, "finally": 1, "raise": 1,
        "lambda": 1, "yield": 1, "pass": 1, "break": 1, "continue": 1, "None": 1, "True": 1, "False": 1, "async": 1,
        "await": 1, "global": 1, "nonlocal": 1, "del": 1, "assert": 1, "function": 1, "const": 1, "let": 1, "var": 1,
        "do": 1, "switch": 1, "case": 1, "new": 1, "this": 1, "extends": 1, "export": 1, "default": 1, "catch": 1,
        "throw": 1, "typeof": 1, "instanceof": 1, "null": 1, "undefined": 1, "true": 1, "false": 1, "of": 1, "fn": 1,
        "pub": 1, "struct": 1, "impl": 1, "mut": 1, "use": 1, "mod": 1, "match": 1, "enum": 1, "trait": 1, "self": 1,
        "func": 1, "package": 1, "type": 1, "interface": 1, "go": 1, "defer": 1, "range": 1, "int": 1, "void": 1,
        "char": 1, "float": 1, "double": 1, "long": 1, "static": 1, "bool": 1, "auto": 1, "then": 1, "fi": 1,
        "done": 1, "esac": 1, "echo": 1, "property": 1, "signal": 1, "readonly": 1, "public": 1, "private": 1,
        "SELECT": 1, "FROM": 1, "WHERE": 1, "select": 1, "where": 1, "nil": 1, "end": 1, "local": 1, "elseif": 1 })
    readonly property var defs: ({ "def": 1, "function": 1, "fn": 1, "func": 1, "class": 1, "struct": 1, "enum": 1, "trait": 1, "interface": 1, "type": 1 })
    readonly property string cmt: /^(py|python|sh|bash|zsh|shell|ps1|powershell|rb|ruby|r|yaml|yml|toml|ini|conf|make|cmake|perl|nim)$/.test(lang) ? "#"
                                : /^(sql|lua|haskell|hs|elm)$/.test(lang) ? "--"
                                : lang === "" ? "#|//" : "//"
    function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/ /g, "&nbsp;") }
    function paint(s, c) { return "<font color=\"" + c + "\">" + esc(s) + "</font>" }
    function hl(line) {
        var c = cmt === "#" ? "#.*$" : cmt === "--" ? "--.*$" : cmt === "//" ? "\\/\\/.*$|\\/\\*.*$" : "#.*$|\\/\\/.*$"
        var re = new RegExp("(\\s+)|(" + c + ")|(\"(?:[^\"\\\\]|\\\\.)*\"?|'(?:[^'\\\\]|\\\\.)*'?|`[^`]*`?)|" +
                            "(\\b\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b|\\.\\d+\\b|\\b0x[0-9a-fA-F]+\\b)|([A-Za-z_$][\\w$]*)|([\\s\\S])", "g")
        var out = "", m, prev = ""
        while ((m = re.exec(line)) !== null) {
            if (m[1]) out += esc(m[1])
            else if (m[2]) out += paint(m[2], "#6f6a66")
            else if (m[3]) out += paint(m[3], "#b8d89a")
            else if (m[4]) out += paint(m[4], "#e8c47d")
            else if (m[5]) {
                var id = m[5]
                out += defs[prev] ? paint(id, "#9fc2e8") : kw[id] ? paint(id, "#EC9A78") : esc(id)
                prev = id
                continue
            } else out += esc(m[6])
            if (!m[1]) prev = ""
        }
        return out
    }

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
        width: w.width; height: w.implicitHeight
        still: w.reduced

        Rectangle {
            anchors.fill: parent; radius: 10
            color: Qt.rgba(1, 1, 1, 0.035)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.05)
        }
        // top strip: name / language, Copy
        Text {
            x: 10; width: parent.width - 10 - copyB.width - 16; height: 28
            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; textFormat: Text.PlainText
            text: w.d.title || w.d.language || "snippet"
            color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
        }
        Rectangle {
            id: copyB
            x: parent.width - width - 6; y: 4; height: 20; radius: 10
            width: copyRow.implicitWidth + 18
            color: w.copied ? Qt.rgba(108 / 255, 203 / 255, 122 / 255, 0.12)
                 : Qt.rgba(1, 1, 1, copyMa.containsMouse ? 0.14 : 0.08)
            Behavior on color { ColorAnimation { duration: 140 } }
            Behavior on width { enabled: !w.reduced; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            scale: copyMa.pressed ? 0.94 : 1
            Row {
                id: copyRow
                anchors.centerIn: parent; spacing: 3
                Glyph { visible: w.copied; anchors.verticalCenter: parent.verticalCenter; name: "check"; color: "#6CCB7A"; s: 0.5 }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: w.copied ? "Copied" : "Copy"
                    color: w.copied ? "#6CCB7A" : "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 8
                }
            }
            MouseArea {
                id: copyMa
                anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: { w.action("copy", w.d.code || ""); w.copied = true; back.restart() }
            }
        }
        Timer { id: back; interval: 1600; onTriggered: w.copied = false }
        Rectangle { x: 1; y: 28; width: parent.width - 2; height: 1; color: Qt.rgba(1, 1, 1, 0.05) }

        // the code
        Item {
            x: 0; y: 28 + 6; width: parent.width - 10; height: w.n * w.lh
            clip: true
            Repeater {
                model: w.lines
                Item {
                    y: index * w.lh; width: parent.width; height: w.lh
                    Text {
                        x: 10; width: 18; height: w.lh; verticalAlignment: Text.AlignVCenter
                        text: index + 1; color: "#5a5450"
                        font.family: "Cascadia Mono"; font.pointSize: w.fs
                    }
                    Text {
                        x: 32; height: w.lh; verticalAlignment: Text.AlignVCenter
                        textFormat: Text.StyledText
                        text: w.hl(modelData)
                        color: "#E8E2DC"
                        font.family: "Cascadia Mono"; font.pointSize: w.fs
                    }
                }
            }
        }
    }
}
