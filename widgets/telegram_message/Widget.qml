import QtQuick
import "../../ui/island"

// telegram_message: one message or a short thread as chat bubbles. Theirs sit
// on the left (soft grey, a 4 px tail corner), yours on the right in ember
// tint; replies are indented. Each bubble carries "sender · time" above its
// text. Bubbles rise in 45 ms apart. A thread taller than the body keeps the
// newest messages and fades the oldest out at the top. A click opens the chat
// in Telegram Desktop. Message text is private: shown, never logged.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false

    readonly property var d: data_ || ({})
    readonly property var msgs: (d.messages || []).slice(-5)
    readonly property int maxLines: msgs.length > 3 ? 2 : msgs.length > 1 ? 3 : 5
    readonly property real budget: 196
    implicitHeight: Math.max(1, Math.min(col.implicitHeight, budget))

    function mine(m) {
        if (m.me === true || m.out === true) return true
        var s = String(m.sender || "").trim().toLowerCase()
        return s === "you" || s === "me"
    }

    Item {
        id: clipper
        width: w.width; height: w.implicitHeight
        clip: col.implicitHeight > w.budget
        Column {
            id: col
            width: parent.width
            y: Math.min(0, clipper.height - implicitHeight)        // the newest stay in view
            spacing: 8
            Repeater {
                model: w.msgs
                Item {
                    id: mi
                    readonly property bool me: w.mine(modelData)
                    readonly property real ind: modelData.reply && !me ? 22 : 0
                    readonly property real maxW: Math.round((w.width - ind) * (me ? 0.84 : 0.84))
                    width: col.width; height: bub.height
                    opacity: 0
                    transform: Translate { id: rt; y: w.reduced ? 0 : 5 }
                    NumberAnimation { id: fadeM; target: mi; property: "opacity"; from: 0; to: 1; duration: 160 }
                    SequentialAnimation {
                        id: enterM
                        PauseAnimation { duration: index * 45 }
                        ParallelAnimation {
                            NumberAnimation { target: mi; property: "opacity"; to: 1; duration: 320; easing.type: Easing.OutCubic }
                            NumberAnimation { target: rt; property: "y"; to: 0; duration: 320; easing.type: Easing.OutCubic }
                        }
                    }
                    Component.onCompleted: w.reduced ? fadeM.start() : enterM.start()

                    Rectangle {
                        id: bub
                        x: mi.me ? mi.width - width : mi.ind
                        width: Math.min(mi.maxW, Math.max(meta.implicitWidth, body.implicitWidth) + 20)
                        height: 7 + meta.implicitHeight + 1 + body.height + 8
                        radius: 12
                        bottomLeftRadius: mi.me ? 12 : 4
                        bottomRightRadius: mi.me ? 4 : 12
                        color: mi.me ? Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.18) : Qt.rgba(1, 1, 1, 0.07)
                        Text {
                            id: meta
                            x: 10; y: 7; width: Math.min(implicitWidth, mi.maxW - 20)
                            text: [modelData.sender || "", modelData.time || ""].filter(function (s) { return s }).join(" · ")
                            visible: text !== ""
                            elide: Text.ElideRight; textFormat: Text.PlainText
                            color: mi.me ? "#C9A493" : "#9A9A9A"
                            font.family: "Segoe UI Variable Text"; font.pointSize: 8
                        }
                        Text {
                            id: body
                            x: 10; y: meta.visible ? meta.y + meta.implicitHeight + 1 : 7
                            width: Math.min(implicitWidth, mi.maxW - 20)
                            text: modelData.text || ""
                            wrapMode: Text.Wrap; maximumLineCount: w.maxLines; elide: Text.ElideRight
                            textFormat: Text.PlainText
                            color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9.5
                        }
                    }
                }
            }
        }
        Rectangle {                                                // older messages fade out at the top
            visible: col.implicitHeight > w.budget
            width: parent.width; height: 28
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.rgba(8 / 255, 8 / 255, 8 / 255, 0.97) }
                GradientStop { position: 1; color: Qt.rgba(8 / 255, 8 / 255, 8 / 255, 0) }
            }
        }
    }
    MouseArea {
        anchors.fill: parent
        enabled: !!w.d.link
        cursorShape: Qt.PointingHandCursor
        onClicked: w.action("open", w.d.link)
    }
}
