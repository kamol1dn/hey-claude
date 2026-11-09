import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// weather: the temperature ticks in, the sky is animated on the GPU (Sky.qml),
// six hours, and three days whose ranges grow from the left. Data comes from
// weather-mcp or the built-in Open-Meteo tool (heyclaude/weather_mcp,
// heyclaude/weather.py), never from the model.
// M5b states, each shown only when its data is there (else the card is as before):
//   alert   a banner in the severity's colour, the issuing service credited
//   nowcast "rain in N minutes" with the next 2 hours as 15-minute bars
//   air     AQI (a coloured dot), UV, pollen; with sunrise/sunset and the moon's phase
// The days fill what's left of the card's height (at least one).
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    property bool active: true                  // the island isn't resting: the sky may move
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property var d: data_ || ({})
    readonly property var hours: d.hourly || []
    readonly property var alert: d.alert || null
    readonly property var nowc: d.nowcast && d.nowcast.steps && d.nowcast.steps.length ? d.nowcast : null
    readonly property var air: d.air || null
    readonly property var sun: d.sun || null
    readonly property var moon: d.moon || null
    readonly property bool hasStrip: (air !== null && (air.aqi !== undefined || air.uv !== undefined || !!air.pollen))
                                     || sun !== null || moon !== null
    // the rows, top to bottom
    readonly property real yAlert: 58
    readonly property real yHours: yAlert + (alert ? 32 : 0)
    readonly property real yNow: yHours + (hours.length ? 62 : 0)
    readonly property real yStrip: yNow + (nowc ? 30 : 0)
    readonly property real yDays: yStrip + (hasStrip ? 26 : 0)
    readonly property real bodyMax: 250             // widget.json max_height 280 - the card's 30 px header
    readonly property int dayFit: Math.max(1, Math.floor((bodyMax - yDays - 8) / 22))
    readonly property var days: (d.days || []).slice(0, dayFit)
    readonly property real lo: days.length ? Math.min.apply(null, days.map(function (x) { return x.low })) - 2 : 0
    readonly property real hi: days.length ? Math.max.apply(null, days.map(function (x) { return x.high })) + 2 : 1
    implicitHeight: yDays + (days.length ? 8 + 22 * days.length : 0)

    function ic(name) { return name || "cloud" }
    function icColor(name) { return name === "sun" ? "#EC9A78" : "#F2F2F2" }
    function sevColor(a) {
        var c = { green: "#6CCB7A", yellow: "#E8C35A", orange: "#E8954A", red: "#E5646A" }
        if (a && a.level && c[a.level]) return c[a.level]
        var s = a ? a.severity : ""
        return s === "extreme" ? "#E5646A" : s === "severe" ? "#E8954A" : s === "moderate" ? "#E8C35A"
             : s === "minor" ? "#8FB6D8" : "#9A9A9A"
    }
    function aqiColor(c) {
        return ({ green: "#6CCB7A", yellow: "#E8C35A", orange: "#E8954A", red: "#E5646A", purple: "#B48EDB",
                  maroon: "#A0525A" })[c] || "#9A9A9A"
    }

    Sky {
        x: w.width - 220; y: -30; width: 236; height: 88       // up into the header's empty right side
        icon: w.d.icon || "cloud"
        active: w.active
        reduced: w.reduced
    }
    Rise {
        i: 0; reduced: w.reduced
        y: 0
        Tick {
            value: w.d.temp || 0; reduced: w.reduced; suffix: "°"
            color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 30; font.weight: Font.DemiBold
        }
    }
    Rise {
        i: 1; reduced: w.reduced
        x: 104; y: 9
        Column {
            spacing: 2
            Text { text: w.d.condition || ""; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10 }
            Text {
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                text: [w.d.high !== undefined ? "H " + Math.round(w.d.high) + "°" : "", w.d.low !== undefined ? "L " + Math.round(w.d.low) + "°" : "",
                       w.d.feels !== undefined ? "feels " + Math.round(w.d.feels) + "°" : "", w.d.wind !== undefined ? "wind " + Math.round(w.d.wind) : ""]
                      .filter(function (s) { return s }).join("   ")
            }
        }
    }
    // an official warning: the severity's colour, what it is, who issued it
    Rise {
        visible: w.alert !== null
        i: 2; reduced: w.reduced
        y: w.yAlert + 2
        Rectangle {
            objectName: "weatherAlert"
            width: w.width; height: 26; radius: 8
            color: Qt.rgba(1, 1, 1, 0.04)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.05)
            Rectangle {                                         // the severity wash, from the left
                anchors.fill: parent; radius: 8; opacity: 0.16
                gradient: Gradient { orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: w.sevColor(w.alert) } GradientStop { position: 0.7; color: "transparent" } }
            }
            Rectangle {
                id: sevBar
                x: 8; width: 3; height: 14; radius: 1.5; anchors.verticalCenter: parent.verticalCenter
                color: w.sevColor(w.alert)
                SequentialAnimation on opacity {                // severe and extreme: one slow breath, then still
                    running: !w.reduced && w.alert !== null && (w.alert.severity === "severe" || w.alert.severity === "extreme")
                    loops: 2
                    NumberAnimation { to: 0.45; duration: 700; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
                }
            }
            Text {
                x: 18; width: parent.width - 18 - credit.width - 14; anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                text: w.alert ? w.alert.title + (w.alert.more ? "  +" + w.alert.more : "") : ""
                color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9
            }
            Text {
                id: credit
                anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                width: Math.min(implicitWidth, parent.width * 0.48); elide: Text.ElideRight
                text: w.alert ? [w.alert.source || "", w.alert.until ? "until " + w.alert.until : ""].filter(function (s) { return s }).join(" · ") : ""
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
            }
        }
    }
    // the next hours
    Rectangle { visible: w.hours.length > 0; y: w.yHours; width: w.width; height: 1; color: Qt.rgba(1, 1, 1, 0.06) }
    Repeater {
        model: w.hours
        Rise {
            i: index + 3; reduced: w.reduced
            x: index * (w.width / Math.max(1, w.hours.length)); y: w.yHours + 6
            width: w.width / Math.max(1, w.hours.length)
            Item {
                width: w.width / Math.max(1, w.hours.length); height: 54
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.time; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                Glyph { anchors.horizontalCenter: parent.horizontalCenter; y: 15; name: w.ic(modelData.icon); s: 0.75; color: w.icColor(modelData.icon) }
                Text { anchors.horizontalCenter: parent.horizontalCenter; y: 36; text: Math.round(modelData.temp) + "°"; color: "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 9; font.features: { "tnum": 1 } }
            }
        }
    }
    // rain in the next 2 hours, 15 minutes a bar (none · trace · light · moderate · heavy)
    Rise {
        visible: w.nowc !== null
        i: 9; reduced: w.reduced
        y: w.yNow + 3
        Item {
            objectName: "weatherNowcast"
            width: w.width; height: 24
            Glyph { x: -3; anchors.verticalCenter: parent.verticalCenter; name: "rain"; s: 0.6; color: "#8FB6D8" }
            Text {
                x: 18; anchors.verticalCenter: parent.verticalCenter
                text: w.nowc ? w.nowc.text : ""
                color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9
            }
            Row {
                id: bars
                anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.bottomMargin: 4
                spacing: 3
                readonly property real bw: Math.max(8, Math.min(18, (w.width * 0.45 - 7 * 3) / 8))
                Repeater {
                    model: w.nowc ? w.nowc.steps : []
                    Rectangle {
                        id: nb
                        width: bars.bw; radius: 1.5
                        readonly property real full: 3 + modelData * 3.5
                        height: w.reduced ? full : 0
                        color: modelData > 0 ? Qt.rgba(0.56, 0.71, 0.85, 0.35 + modelData * 0.16) : Qt.rgba(1, 1, 1, 0.08)
                        anchors.bottom: parent.bottom
                        Component.onCompleted: if (!w.reduced) rise.start()
                        SequentialAnimation {
                            id: rise
                            PauseAnimation { duration: 9 * 45 + 200 + index * 40 }
                            NumberAnimation { target: nb; property: "height"; to: nb.full; duration: 380; easing.type: Easing.OutCubic }
                        }
                    }
                }
            }
            Text {
                anchors.right: bars.left; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                visible: !!(w.nowc && w.nowc.start)
                text: w.nowc && w.nowc.start ? "from " + w.nowc.start : ""
                color: "#6f6a66"; font.family: "Segoe UI Variable Text"; font.pointSize: 7.5
            }
        }
    }
    // air quality, UV and pollen on the left; sunrise, sunset and the moon on the right
    Rise {
        visible: w.hasStrip
        i: 10; reduced: w.reduced
        y: w.yStrip + 2
        Item {
            objectName: "weatherStrip"
            width: w.width; height: 20
            Row {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 12
                Row {
                    visible: !!(w.air && w.air.aqi !== undefined)
                    spacing: 5
                    Rectangle { width: 7; height: 7; radius: 3.5; anchors.verticalCenter: parent.verticalCenter
                        color: w.aqiColor(w.air ? w.air.color : "") }
                    Text { text: w.air && w.air.aqi !== undefined ? "AQI " + Math.round(w.air.aqi) : ""; color: "#F2F2F2"
                        font.family: "Segoe UI Variable Text"; font.pointSize: 8.5; font.features: { "tnum": 1 } }
                    Text { text: w.air && w.air.label ? w.air.label.toLowerCase() : ""; color: "#9A9A9A"
                        font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                }
                Text {
                    visible: !!(w.air && w.air.uv !== undefined)
                    text: w.air && w.air.uv !== undefined ? "UV " + Math.round(w.air.uv) : ""
                    color: w.air && w.air.uv >= 6 ? "#EC9A78" : "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 8.5; font.features: { "tnum": 1 }
                }
                Text {
                    visible: !!(w.air && w.air.pollen)
                    text: w.air && w.air.pollen ? "pollen " + w.air.pollen : ""
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8
                }
            }
            Row {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                spacing: 10
                Row {
                    visible: w.sun !== null
                    spacing: 3
                    Glyph { anchors.verticalCenter: parent.verticalCenter; name: "sun"; s: 0.5; color: "#EC9A78"; level: 30 }
                    Text { anchors.verticalCenter: parent.verticalCenter
                        text: w.sun ? w.sun.rise + "  ·  " + w.sun.set : ""; color: "#9A9A9A"
                        font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.features: { "tnum": 1 } }
                }
                Row {
                    visible: w.moon !== null
                    spacing: 5
                    // the moon as it is: the lit part of a faint disk (right side waxing, left waning)
                    Shape {
                        id: moonShape
                        objectName: "weatherMoon"
                        width: 12; height: 12; anchors.verticalCenter: parent.verticalCenter
                        preferredRendererType: Shape.CurveRenderer
                        readonly property real k: w.moon ? Math.max(0, Math.min(1, (w.moon.illum || 0) / 100)) : 0
                        readonly property bool wax: w.moon ? w.moon.waxing !== false : true
                        readonly property real r: 5.5
                        readonly property real rx: Math.max(0.01, r * Math.abs(1 - 2 * k))
                        opacity: 0
                        Component.onCompleted: fadeMoon.start()
                        NumberAnimation { id: fadeMoon; target: moonShape; property: "opacity"; to: 1
                            duration: w.reduced ? 160 : 600; easing.type: Easing.OutCubic }
                        ShapePath {
                            strokeWidth: 0; strokeColor: "transparent"; fillColor: Qt.rgba(1, 1, 1, 0.12)
                            PathSvg { path: "M 6 0.5 A 5.5 5.5 0 1 1 6 11.5 A 5.5 5.5 0 1 1 6 0.5 Z" }
                        }
                        ShapePath {
                            strokeWidth: 0; strokeColor: "transparent"; fillColor: "#F2F2F2"
                            PathSvg {
                                path: moonShape.k <= 0.01 ? "M 6 6 Z" :
                                      "M 6 0.5 A 5.5 5.5 0 0 " + (moonShape.wax ? 1 : 0) + " 6 11.5 A " + moonShape.rx.toFixed(2)
                                      + " 5.5 0 0 " + (((moonShape.k < 0.5) === moonShape.wax) ? 0 : 1) + " 6 0.5 Z"
                            }
                        }
                    }
                    Text { anchors.verticalCenter: parent.verticalCenter
                        text: w.moon ? Math.round(w.moon.illum || 0) + "%" : ""; color: "#9A9A9A"
                        font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.features: { "tnum": 1 } }
                }
            }
        }
    }
    // the days, with each day's range on the days' scale
    Repeater {
        model: w.days
        Rise {
            id: dayRow
            i: index + 11; reduced: w.reduced
            y: w.yDays + 6 + index * 22
            Item {
                width: w.width; height: 20
                Text { width: 46; anchors.verticalCenter: parent.verticalCenter; text: modelData.day; color: "#F2F2F2"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
                Glyph { x: 46; anchors.verticalCenter: parent.verticalCenter; name: w.ic(modelData.icon); s: 0.7; color: w.icColor(modelData.icon) }
                Text { x: 70; width: 34; horizontalAlignment: Text.AlignRight; anchors.verticalCenter: parent.verticalCenter
                    text: Math.round(modelData.low) + "°"; color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
                Rectangle {
                    id: track
                    x: 114; width: w.width - 114 - 44; height: 4; radius: 2; anchors.verticalCenter: parent.verticalCenter
                    color: Qt.rgba(1, 1, 1, 0.08)
                    Rectangle {
                        id: bar
                        x: track.width * (modelData.low - w.lo) / Math.max(1, w.hi - w.lo)
                        height: 4; radius: 2
                        readonly property real full: track.width * (modelData.high - modelData.low) / Math.max(1, w.hi - w.lo)
                        width: 0
                        gradient: Gradient { orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: "#8FB6D8" } GradientStop { position: 1; color: "#EC9A78" } }
                        Component.onCompleted: grow.start()
                        SequentialAnimation {
                            id: grow
                            PauseAnimation { duration: w.reduced ? 0 : (index + 11) * 45 + 260 }
                            NumberAnimation { target: bar; property: "width"; to: bar.full; duration: w.reduced ? 0 : 520; easing.type: Easing.OutCubic }
                        }
                    }
                }
                Text { x: w.width - 36; width: 36; horizontalAlignment: Text.AlignRight; anchors.verticalCenter: parent.verticalCenter
                    text: Math.round(modelData.high) + "°"; color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 9 }
            }
        }
    }
}
