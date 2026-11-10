import QtQuick
import QtQuick.Shapes
import "../../ui/island"

// directions: a route or a place on a small dark OpenStreetMap snippet.
// Entry: the map settles from a slight zoom; for a route the start dot pops, the
// line draws itself from A to B with a bright head, then the destination pin drops
// in with a little bounce and one ripple spreads from where it lands. A place: the
// pin drops and ripples. Everything is one-shot (≤ ~1.4 s), then nothing moves.
// The map is a PNG the tool rendered (state/maps/); without one the card draws a
// faint grid under the route. "© OpenStreetMap" is always on the map.
Item {
    id: w
    property var data_: ({})
    property string wid: ""
    property bool reduced: false
    signal action(string name, var payload)
    readonly property real minWidth: 440
    property bool holds: false
    readonly property bool gpu: GraphicsInfo.api !== GraphicsInfo.Software

    readonly property var d: data_ || ({})
    readonly property bool isRoute: d.kind === "route"
    readonly property real mapH: 152
    readonly property string src: {
        var m = String(d.map || "")
        if (!m) return ""
        return m.indexOf("file:") === 0 ? m : Qt.resolvedUrl(m)
    }
    readonly property bool hasMap: src !== "" && img.status === Image.Ready
    implicitHeight: mapH + 10 + (isRoute ? 46 : 44)

    // ---- where a 0..1 image point lands in the map box (the picture is cropped to fill it)
    readonly property real iw: img.implicitWidth > 0 ? img.implicitWidth : 612
    readonly property real ih: img.implicitHeight > 0 ? img.implicitHeight : 228
    readonly property real sc: Math.max(width / iw, mapH / ih)
    function mx(r) { return (width - iw * sc) / 2 + r * iw * sc }
    function my(r) { return (mapH - ih * sc) / 2 + r * ih * sc }

    readonly property var pts: {
        var r = d.route || [], out = []
        for (var i = 0; i < r.length; i++) out.push(Qt.point(mx(r[i][0]), my(r[i][1])))
        return out
    }
    readonly property var cum: {                                 // cumulative length along the route
        var c = [0]
        for (var i = 1; i < pts.length; i++) c.push(c[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y))
        return c
    }
    readonly property var pinA: (d.pins && d.pins.length > 1) ? Qt.point(mx(d.pins[0][0]), my(d.pins[0][1]))
                                                              : (pts.length ? pts[0] : Qt.point(width * 0.2, mapH * 0.6))
    readonly property var pinB: {
        var p = d.pins || []
        if (isRoute) return p.length > 1 ? Qt.point(mx(p[1][0]), my(p[1][1])) : (pts.length ? pts[pts.length - 1] : Qt.point(width * 0.8, mapH * 0.4))
        return p.length ? Qt.point(mx(p[0][0]), my(p[0][1])) : Qt.point(width / 2, mapH / 2)
    }

    // ---- the line's progress 0..1 and the points drawn so far (the tip interpolated)
    property real prog: reduced ? 1 : 0
    readonly property var tip: at(prog)
    function at(u) {
        if (pts.length < 2) return pts.length ? pts[0] : Qt.point(0, 0)
        var L = cum[cum.length - 1] * u
        for (var i = 1; i < pts.length; i++) {
            if (cum[i] >= L) {
                var seg = cum[i] - cum[i - 1], t = seg > 0 ? (L - cum[i - 1]) / seg : 0
                return Qt.point(pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t, pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t)
            }
        }
        return pts[pts.length - 1]
    }
    function drawn(u) {
        if (pts.length < 2) return []
        if (u >= 1) return pts
        var L = cum[cum.length - 1] * u, out = [pts[0]]
        for (var i = 1; i < pts.length && cum[i] < L; i++) out.push(pts[i])
        out.push(at(u))
        return out
    }

    // ---- the entry, once
    property real mapIn: reduced ? 1 : 0
    property real aIn: reduced ? 1 : 0
    property real bDrop: reduced ? 1 : 0            // 0: up in the air, 1: landed
    property real ripple: 0
    property real headA: 0
    SequentialAnimation {
        id: entry
        ParallelAnimation {
            NumberAnimation { target: w; property: "mapIn"; from: 0; to: 1; duration: 380; easing.type: Easing.OutCubic }
            SequentialAnimation {
                PauseAnimation { duration: 140 }
                NumberAnimation { target: w; property: "aIn"; from: 0; to: 1; duration: 300; easing.type: Easing.OutBack; easing.overshoot: 2.2 }
            }
            SequentialAnimation {
                PauseAnimation { duration: w.isRoute ? 260 : 0 }
                ScriptAction { script: w.headA = w.isRoute ? 1 : 0 }
                NumberAnimation { target: w; property: "prog"; from: 0; to: 1; duration: w.isRoute ? 760 : 1; easing.type: Easing.InOutCubic }
                ParallelAnimation {
                    NumberAnimation { target: w; property: "headA"; to: 0; duration: 220 }
                    NumberAnimation { target: w; property: "bDrop"; from: 0; to: 1; duration: 420; easing.type: Easing.OutBounce }
                }
                NumberAnimation { target: w; property: "ripple"; from: 0; to: 1; duration: 620; easing.type: Easing.OutCubic }
            }
        }
    }
    Component.onCompleted: if (!reduced) entry.start()

    // =================================================================== the map
    Item {
        id: mapBox
        width: w.width; height: w.mapH
        opacity: w.mapIn

        Rectangle {                                           // the frame's tone, and the grid without a map
            anchors.fill: parent; radius: 12
            color: "#141414"
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.05)
            clip: true
            Repeater {
                model: w.hasMap ? 0 : 11
                Rectangle { x: (index + 1) * parent.width / 12; width: 1; height: parent.height; color: Qt.rgba(1, 1, 1, 0.035) }
            }
            Repeater {
                model: w.hasMap ? 0 : 4
                Rectangle { y: (index + 1) * parent.height / 5; height: 1; width: parent.width; color: Qt.rgba(1, 1, 1, 0.035) }
            }
        }

        Item {                                                // the picture, poured into a rounded shape (GPU)
            id: pic
            width: w.width; height: w.mapH
            visible: !w.gpu
            clip: !w.gpu
            Image {
                id: img
                anchors.fill: parent
                source: w.src
                fillMode: Image.PreserveAspectCrop
                asynchronous: true; smooth: true; mipmap: true
                transformOrigin: Item.Center
                scale: w.reduced ? 1 : 1.045 - 0.045 * w.mapIn
            }
            Rectangle {                                       // shade the corner the credit sits in
                anchors.bottom: parent.bottom; width: parent.width; height: 34
                gradient: Gradient {
                    GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0) }
                    GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.45) }
                }
            }
        }
        ShaderEffectSource { id: picTex; sourceItem: w.gpu ? pic : null; hideSource: w.gpu; live: w.gpu; visible: false
            width: pic.width; height: pic.height }
        Shape {
            anchors.fill: parent
            visible: w.gpu && w.hasMap
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: -1; strokeColor: "transparent"
                fillItem: picTex
                PathRectangle { x: 0; y: 0; width: mapBox.width; height: mapBox.height; radius: 12 }
            }
        }

        // the route: a dark casing, the warm line, and a bright head while it draws
        Shape {
            id: line
            objectName: "routeLine"
            anchors.fill: parent
            visible: w.isRoute && w.pts.length > 1
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Qt.rgba(0, 0, 0, 0.55); strokeWidth: 7; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathPolyline { path: w.drawn(w.prog) }
            }
            ShapePath {
                strokeColor: "#EC9A78"; strokeWidth: 3.2; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathPolyline { path: w.drawn(w.prog) }
            }
        }
        Rectangle {                                           // the head
            visible: w.isRoute && w.headA > 0.01
            x: w.tip.x - 5; y: w.tip.y - 5; width: 10; height: 10; radius: 5
            color: "#FFE3D6"; opacity: w.headA
            Rectangle { anchors.centerIn: parent; width: 18; height: 18; radius: 9; color: Qt.rgba(236 / 255, 154 / 255, 120 / 255, 0.28) }
        }

        // A: where it starts
        Item {
            visible: w.isRoute
            x: w.pinA.x; y: w.pinA.y
            scale: w.aIn
            Rectangle { x: -7; y: -7; width: 14; height: 14; radius: 7; color: "#141414"; border.width: 2.5; border.color: "#F2F2F2" }
        }

        // B: the ripple where the pin lands, its shadow, and the pin
        Rectangle {
            visible: w.ripple > 0 && w.ripple < 1
            x: w.pinB.x - width / 2; y: w.pinB.y - height / 2
            width: 8 + 46 * w.ripple; height: width * 0.62; radius: height / 2
            color: "transparent"; border.width: 1.5; border.color: "#D97757"
            opacity: 0.75 * (1 - w.ripple)
        }
        Rectangle {
            x: w.pinB.x - width / 2; y: w.pinB.y - height / 2
            width: 14 * (0.35 + 0.65 * w.bDrop); height: width * 0.4; radius: height / 2
            color: Qt.rgba(0, 0, 0, 0.55 * w.bDrop)
        }
        Shape {
            id: pinB
            objectName: "destPin"
            x: w.pinB.x; y: w.pinB.y - 26 * (1 - w.bDrop)
            opacity: Math.min(1, w.bDrop * 3)
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: "#D97757"; strokeColor: "#FFD2BF"; strokeWidth: 1
                PathSvg { path: "M0 0 C-1.8 -5.5 -8 -9 -8 -15.5 A8 8 0 1 1 8 -15.5 C8 -9 1.8 -5.5 0 0 Z" }
            }
            ShapePath {
                fillColor: "#141414"; strokeColor: "transparent"
                PathSvg { path: "M-3 -15.5 A3 3 0 1 1 3 -15.5 A3 3 0 1 1 -3 -15.5 Z" }
            }
        }

        Rectangle {                                           // the credit OSM's tiles ask for
            anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 5
            height: credit.implicitHeight + 4; width: credit.implicitWidth + 12; radius: height / 2
            color: Qt.rgba(0, 0, 0, 0.62)
            Text {
                id: credit
                objectName: "osmCredit"
                anchors.centerIn: parent
                text: w.d.attribution || "© OpenStreetMap"
                color: Qt.rgba(1, 1, 1, 0.62); font.family: "Segoe UI Variable Text"; font.pointSize: 7
            }
        }
        Rectangle {                                           // a hairline on top, like the other cards
            anchors.fill: parent; radius: 12; color: "transparent"
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.07)
        }
    }

    // =================================================================== under the map
    Rise {                                                    // a route: time, distance, how, when
        visible: w.isRoute
        i: 4; reduced: w.reduced
        y: w.mapH + 10
        Item {
            width: w.width; height: 46
            Row {
                spacing: 8
                Text {
                    id: dur
                    objectName: "routeTime"
                    text: w.d.duration || ""
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Display"; font.pointSize: 16; font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                }
                Text {
                    anchors.baseline: dur.baseline
                    text: w.d.distance || ""
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 10
                }
            }
            Text {
                y: 27; width: w.width - chip.width - 12
                text: (w.d.origin || "") + "  →  " + (w.d.dest || "")
                elide: Text.ElideMiddle; textFormat: Text.PlainText
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
            }
            Column {
                id: chip
                anchors.right: parent.right; y: 2; spacing: 5
                Rectangle {
                    anchors.right: parent.right
                    height: 20; radius: 10; width: howT.implicitWidth + 18
                    color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.14)
                    Text { id: howT; anchors.centerIn: parent; text: w.d.how || ""; color: "#EC9A78"
                        font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
                }
                Text {
                    visible: !!w.d.arrive
                    anchors.right: parent.right
                    text: "arrive " + (w.d.arrive || "")
                    color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8; font.features: { "tnum": 1 }
                }
            }
        }
    }
    Rise {                                                    // a place: its name, kind and address
        visible: !w.isRoute
        i: 4; reduced: w.reduced
        y: w.mapH + 10
        Item {
            width: w.width; height: 44
            Text {
                id: nm
                width: w.width - kindChip.width - 12
                text: w.d.name || ""
                elide: Text.ElideRight; textFormat: Text.PlainText
                color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 11; font.weight: Font.DemiBold
            }
            Rectangle {
                id: kindChip
                visible: !!w.d.place_kind
                anchors.right: parent.right; y: 1
                height: 20; radius: 10; width: visible ? kindT.implicitWidth + 18 : 0
                color: Qt.rgba(217 / 255, 119 / 255, 87 / 255, 0.14)
                Text { id: kindT; anchors.centerIn: parent; text: w.d.place_kind || ""; color: "#EC9A78"
                    font.family: "Segoe UI Variable Text"; font.pointSize: 8 }
            }
            Text {
                y: 23; width: w.width
                text: w.d.address || ""
                elide: Text.ElideRight; textFormat: Text.PlainText
                color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5
            }
        }
    }
}
