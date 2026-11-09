import QtQuick

// The response widget under the head (ui/widgets.schema.json). One at a time;
// a new one replaces it. It enters once the pill has mostly grown (rule 10),
// stays while the answer is up and for ttl_s after the island rests, and a
// hover shows a small x to dismiss it.
Item {
    id: card
    property var spec: null                 // {id, widget, data, text, ttl_s}
    property bool resting: false            // the island is back at rest: the ttl runs
    signal action(string wid, string name, var payload)
    signal expired()
    readonly property var files: ({
        weather: "widgets/WeatherCard.qml", calendar_list: "widgets/CalendarCard.qml", timer: "widgets/CountdownCard.qml",
        task_card: "widgets/TaskCard.qml", link: "widgets/LinkCard.qml", code: "widgets/CodeCard.qml",
        image: "widgets/ImageCard.qml", telegram_chats: "widgets/TelegramChats.qml",
        telegram_message: "widgets/TelegramMessage.qml", telegram_draft: "widgets/TelegramDraft.qml", text: "widgets/TextCard.qml" })
    readonly property real minWidth: loader.item && loader.item.minWidth ? loader.item.minWidth : 360
    readonly property bool ready: loader.status === Loader.Ready && loader.item !== null && loader.item.implicitHeight > 0
    readonly property string source: String(loader.source)
    implicitHeight: loader.item ? loader.item.implicitHeight + 12 : 0
    height: implicitHeight
    clip: true

    // Compile every card type once at idle (a few seconds after start), so the first
    // weather or agenda card of the day never stalls a frame mid-answer.
    property var warmed: []
    Timer {
        interval: 4000; running: true
        onTriggered: {
            var names = Object.keys(card.files)
            for (var i = 0; i < names.length; i++) card.warmed.push(Qt.createComponent(card.files[names[i]], Component.Asynchronous))
        }
    }
    function show(s) {
        card.spec = s
        loader.setSource(files[s.widget] || files.text, { data_: s.data, wid: s.id })
        enter.restart()
        if (resting) ttl.restart()
    }
    function clear() { ttl.stop(); card.spec = null; loader.source = "" }
    onRestingChanged: {
        if (!spec) return
        if (resting) {
            // Telegram text is shown only while it's the answer: it fades soon after.
            ttl.interval = (String(spec.widget).indexOf("telegram") === 0 ? 4 : (spec.ttl_s || 12)) * 1000
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

    Rectangle { x: 14; width: parent.width - 28; height: 1; color: Qt.rgba(1, 1, 1, 0.1) }
    Loader {
        id: loader
        asynchronous: true                  // building a card never stalls a frame
        x: 14; y: 6; width: card.width - 28
        opacity: 0
        transform: Translate { id: rise; y: 4 }
    }
    ParallelAnimation {
        id: enter
        SequentialAnimation {
            PauseAnimation { duration: 140 }
            NumberAnimation { target: loader; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
        }
        SequentialAnimation {
            PauseAnimation { duration: 140 }
            NumberAnimation { target: rise; property: "y"; from: 4; to: 0; duration: 220; easing.type: Easing.OutCubic }
        }
    }
    MouseArea {
        id: hov
        anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton; propagateComposedEvents: true
    }
    Rectangle {
        visible: hov.containsMouse || closeMa.containsMouse
        x: card.width - 22; y: 6; width: 16; height: 16; radius: 8
        color: Qt.rgba(1, 1, 1, closeMa.containsMouse ? 0.2 : 0.1)
        Text { anchors.centerIn: parent; text: "×"; color: "#F2F2F2"; font.pointSize: 9 }
        MouseArea { id: closeMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: card.action(card.spec ? card.spec.id : "", "dismiss", null) }
    }
}
