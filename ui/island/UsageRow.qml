import QtQuick

// Plan usage: the 5-hour and weekly limits, as Claude Code reports them
// (stream-json rate_limit_event). Shown in the hover list only.
Item {
    id: row
    height: 30
    readonly property var u: model ? model.usage : ({})
    function pct(v) { return v === undefined || v === null ? "–" : Math.round(v * 100) + "%" }
    Rectangle { x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.05) }
    Text { x: 34; y: 15 - height / 2; text: "plan"; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
    Repeater {
        model: [["5 h", "five_hour"], ["week", "seven_day"]]
        Item {
            readonly property real v: row.u && row.u[modelData[1]] !== undefined ? row.u[modelData[1]] : -1
            x: 80 + index * 150; y: 0; width: 140; height: 30
            Text { y: 15 - height / 2; text: modelData[0]; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
            Rectangle { x: 34; y: 14; width: 64; height: 3; radius: 1.5; color: Qt.rgba(1, 1, 1, 0.12)
                Rectangle { width: Math.max(3, parent.width * Math.max(0, Math.min(1, v))); height: 3; radius: 1.5
                    visible: v >= 0; color: v > 0.85 ? "#E5646A" : (v > 0.6 ? "#EC9A78" : "#D97757") } }
            Text { x: 104; y: 15 - height / 2; text: row.pct(v >= 0 ? v : null); color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
        }
    }
}
