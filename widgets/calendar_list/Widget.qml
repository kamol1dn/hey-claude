import QtQuick
import "../../ui/island"

// calendar_list: an agenda. Rows stagger in; for today, past events dim, the
// next one gets an "in 25 min" badge and a now-line sits between them.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var events: (d.events || []).slice(0, 5)
    readonly property real rowH: 38
    implicitHeight: events.length * rowH + 2

    // minutes since midnight for "HH:MM" (or -1)
    function mins(s) { var m = String(s || "").match(/(\d{1,2}):(\d{2})/); return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : -1 }
    readonly property bool today: { var t = String(d.title || "today").toLowerCase(); return t.indexOf("today") >= 0 || t === "" }
    property int nowMin: { var n = new Date(); return n.getHours() * 60 + n.getMinutes() }
    Timer { interval: 60000; repeat: true; running: w.visible && w.today; onTriggered: { var n = new Date(); w.nowMin = n.getHours() * 60 + n.getMinutes() } }
    readonly property int nextIdx: {
        if (!today) return -1
        for (var i = 0; i < events.length; i++) if (mins(events[i].start) >= nowMin) return i
        return -1
    }
    function past(i) { return today && mins(events[i].end || events[i].start) >= 0 && mins(events[i].end || events[i].start) < nowMin }
    function until(i) {
        var m = mins(events[i].start) - nowMin
        return m <= 0 ? "now" : m < 60 ? "in " + m + " min" : "in " + Math.floor(m / 60) + " h " + (m % 60 ? m % 60 + " min" : "")
    }

    Repeater {
        model: w.events
        Rise {
            i: index; reduced: w.reduced
            y: index * w.rowH
            opacity: 0
            Item {
                width: w.width; height: w.rowH
                opacity: w.past(index) ? 0.45 : 1
                Column {
                    width: 46; anchors.verticalCenter: parent.verticalCenter
                    Text { width: parent.width; horizontalAlignment: Text.AlignRight; text: modelData.start; color: "#9A9A9A"
                        font.family: "Segoe UI Variable Text"; font.pointSize: 9; font.features: { "tnum": 1 } }
                    Text { visible: !!modelData.end; width: parent.width; horizontalAlignment: Text.AlignRight; text: modelData.end || ""
                        color: "#6f6a66"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5; font.features: { "tnum": 1 } }
                }
                Rectangle { x: 56; width: 3; height: 28; radius: 1.5; anchors.verticalCenter: parent.verticalCenter
                    color: modelData.color || "#D97757" }
                Column {
                    x: 70; width: w.width - 70 - (badge.visible ? badge.width + 10 : 0); anchors.verticalCenter: parent.verticalCenter
                    Text { width: parent.width; text: modelData.title; elide: Text.ElideRight; color: "#F2F2F2"
                        font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
                    Text { visible: !!(modelData.location || modelData.calendar); width: parent.width; elide: Text.ElideRight
                        text: [modelData.location || "", modelData.calendar || ""].filter(function (s) { return s }).join(" · ")
                        color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                }
                Rectangle {
                    id: badge
                    visible: index === w.nextIdx
                    x: w.width - width; anchors.verticalCenter: parent.verticalCenter
                    height: 18; radius: 9; width: bt.implicitWidth + 14
                    color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.14)
                    Text { id: bt; anchors.centerIn: parent; text: w.until(index); color: "#EC9A78"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                }
            }
        }
    }
    // now: a terracotta line before the next event
    Rise {
        visible: w.nextIdx > 0
        i: 1; reduced: w.reduced
        x: 52; y: w.nextIdx * w.rowH - 1
        Item {
            width: w.width - 52; height: 3
            Rectangle { x: 0; y: -2; width: 7; height: 7; radius: 3.5; color: "#D97757" }
            Rectangle { x: 4; y: 1; width: parent.width - 4; height: 1
                gradient: Gradient { orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: "#D97757" } GradientStop { position: 1; color: "transparent" } } }
        }
    }
}
