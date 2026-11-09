import QtQuick

// The response widget: a plug-in from widgets/<id>/ (Widget.qml + widget.json,
// heyclaude/widgets.py). The island grows into a panel for it (up to ~460 x 280,
// a spring morph); this draws the panel's header (the widget's glyph or the
// service icon, its title, a dismiss x on hover) and loads the body. One at a
// time; a new one replaces it. It stays while the answer is up and for ttl_s
// after the island rests (personal ones, like Telegram, fade after 4 s).
Item {
    id: card
    property var spec: null                 // {id, widget, data, title, glyph, icon_url, qml, width, max_height, ttl_s, private}
    property bool resting: false
    property bool reduced: false
    signal action(string wid, string name, var payload)
    signal expired()
    readonly property real headH: 30
    readonly property real minWidth: spec ? Math.max(spec.width || 400, loader.item && loader.item.minWidth ? loader.item.minWidth : 0) : 0
    readonly property bool ready: loader.status === Loader.Ready && loader.item !== null && loader.item.implicitHeight > 0
    readonly property string source: String(loader.source)
    readonly property real bodyMax: spec ? (spec.max_height || 236) - headH : 200
    implicitHeight: spec && loader.item ? headH + Math.min(bodyMax, loader.item.implicitHeight) + 14 : 0
    height: implicitHeight
    clip: true

    // Compile every widget once at idle (a few seconds after start), so the first
    // card of the day never stalls a frame mid-answer.
    property var warmed: []
    Timer {
        interval: 4000; running: true
        onTriggered: { var urls = model ? model.widgetUrls : []; for (var i = 0; i < urls.length; i++) card.warmed.push(Qt.createComponent(urls[i], Component.Asynchronous)) }
    }

    function show(s) {
        card.spec = s
        loader.setSource(s.qml, { data_: s.data, wid: s.id, reduced: card.reduced })
        head.opacity = 0; enter.restart()
        if (resting) ttl.restart()
    }
    function clear() { ttl.stop(); card.spec = null; loader.source = "" }
    onRestingChanged: {
        if (!spec) return
        if (loader.item && loader.item.active !== undefined) loader.item.active = !resting
        if (resting) {
            ttl.interval = (spec.private ? 4 : (spec.ttl_s || 12)) * 1000
            if (!loader.item || !loader.item.holds) ttl.restart()
        } else ttl.stop()
    }
    Timer { id: ttl; onTriggered: card.expired() }
    Connections {
        target: loader.item
        ignoreUnknownSignals: true
        function onAction(name, payload) { card.action(card.spec ? card.spec.id : "", name, payload) }
        function onHoldsChanged() { if (card.resting && loader.item && !loader.item.holds) ttl.restart() }
    }

    Rectangle { x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.07) }
    // ---- header: glyph or service icon, title ------------------------------------------
    Item {
        id: head
        x: 16; y: 0; width: card.width - 32; height: card.headH
        opacity: 0
        ServiceIcon {
            id: hicon
            y: (parent.height - 14) / 2; size: 14
            url: card.spec ? card.spec.icon_url || "" : ""
            glyph: card.spec ? card.spec.glyph || "" : ""
            initials: ""
            visible: url !== "" || glyph !== ""
        }
        Text {
            x: hicon.visible ? 22 : 0; width: parent.width - x - 24; height: parent.height; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: card.spec ? card.spec.title || "" : ""
            color: "#9A9A9A"; font.family: "Segoe UI Variable Text"; font.pointSize: 8.5; font.letterSpacing: 0.2
        }
    }
    Loader {
        id: loader
        asynchronous: true                  // building a card never stalls a frame
        x: 16; y: card.headH; width: card.width - 32
        height: Math.min(card.bodyMax, item ? item.implicitHeight : 0)
        clip: false                         // a card may paint into the header's right side (the weather sky); the card clips
        onLoaded: if (item.active !== undefined) item.active = !card.resting
    }
    NumberAnimation { id: enter; target: head; property: "opacity"; from: 0; to: 1; duration: card.reduced ? 160 : 220
        easing.type: Easing.OutCubic }

    MouseArea {
        id: hov
        anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton
    }
    Rectangle {
        visible: hov.containsMouse || closeMa.containsMouse
        x: card.width - 34; y: (card.headH - 18) / 2; width: 18; height: 18; radius: 9
        color: Qt.rgba(1, 1, 1, closeMa.containsMouse ? 0.2 : 0.08)
        Text { anchors.centerIn: parent; text: "×"; color: "#F2F2F2"; font.pointSize: 9 }
        MouseArea { id: closeMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: card.action(card.spec ? card.spec.id : "", "dismiss", null) }
    }
}
