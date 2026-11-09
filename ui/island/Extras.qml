import QtQuick

// Under the island's head: an M9 effect row, approval cards (M7) and the
// response widget, stacked. Its height and minimum width become part of the
// pill's geometry (Island.qml hands them to the engine), so the pill grows
// with an "inhale" and the content fades in once it is mostly there.
Item {
    id: ex
    property bool resting: false
    property bool reduced: false
    property string engineState: ""
    // The open turn (one request): its question stays from "heard" until the panel
    // collapses; the answer is revealed under it in step with the voice and stays in
    // that same row. Card, question and answer go together.
    property string question: ""
    property string answer: ""                  // the answer so far, as text (info / tests)
    property real maxHeight: 100000          // what the window has under the head (Island.qml)
    // The answer row: revealed in step with the voice by the engine (engine.js renderRow:
    // StyledText lines, words not said yet laid out but clear), the same row during and after
    // speaking, so the answer is never shown twice and never moves. It may use whatever the
    // window has left under the head (rowMax lines), then it scrolls by a line.
    property var rowLines: []
    property int rowN: 0
    readonly property int rowMax: Math.max(2, Math.min(14, Math.floor((maxHeight - ((effect.ev ? effect.height : 0)
        + approvals.height + (widget.spec ? widget.height : 0) + qrow.height) - 12) / arow.lineH)))
    readonly property bool showAnswer: rowN > 0
    signal turnCleared()
    readonly property real contentHeight: (effect.ev ? effect.height : 0) + approvals.height + (widget.spec ? widget.height : 0)
        + qrow.height + arow.height
    readonly property real minWidth: Math.max(effect.ev ? effect.minWidth : 0, approvals.count ? 440 : 0, widget.spec ? widget.minWidth : 0)
    function clearTurn() { linger.stop(); question = ""; answer = ""; turnCleared() }
    // At rest: a card keeps the turn up until it goes (then all of it collapses at
    // once); without a card the turn lingers a few seconds.
    Timer { id: linger; interval: Math.max(4000, arow.readMs); onTriggered: ex.clearTurn() }
    onRestingChanged: {
        if (!resting) { linger.stop(); return }
        if (question === "" && answer === "") return
        if (!widget.spec || widget.holds) linger.restart()
    }
    readonly property bool animating: effect.animating
    readonly property real flash: effect.ev ? effect.flash : 0
    function info() {
        return { height: contentHeight, minWidth: minWidth, effect: effect.ev ? effect.ev.effect : "",
                 widget: widget.spec ? widget.spec.widget : "", widgetReady: widget.ready, widgetSource: widget.source,
                 approvals: approvals.count, question: question, answer: showAnswer ? answer : "", pending: widget.pending,
                 rowN: rowN }
    }
    height: contentHeight

    Column {
        width: ex.width
        TurnRow { id: qrow; width: ex.width; text: ex.question; lines: 1 }
        EffectRow {
            id: effect
            width: ex.width
            visible: ev !== null
            onClicked: function (what) { if (model && ev) model.effectClicked(what, ev.id); clear() }
            onDone: clear()
        }
        Column {
            id: approvals
            width: ex.width
            property int count: list.count
            Repeater {
                id: list
                model: ListModel { id: approvalModel }
                ApprovalCard {
                    width: ex.width
                    req: JSON.parse(json)
                    onDecided: function (action) { if (model) model.approvalDecided(apId, action); ex.removeApproval(apId) }
                }
            }
        }
        WidgetCard {
            id: widget
            width: ex.width
            visible: spec !== null
            resting: ex.resting
            reduced: ex.reduced
            // arrived while it's still working on the answer: a skeleton until the answer starts
            // A skeleton only while the card is still being built; once its data is in it shows,
            // even before the answer: the card is the first real answer (a weather card can be
            // up seconds before the agent has written its sentence).
            pending: ex.engineState === "thinking" && ex.question !== "" && !widget.ready
            onAction: function (wid, name, payload) {
                if (!model) return
                if (name === "open") model.openUrl(String(payload))
                else if (name === "copy") model.copyText(String(payload))
                model.widgetAction(wid, name, payload === undefined ? null : payload)
                if (name === "dismiss" || name === "cancel") clear()
            }
            // a long answer still being read stays a little longer than its (private) card
            onExpired: { clear(); if (ex.resting) { if (arow.readMs > 0) linger.restart(); else ex.clearTurn() } }
        }
        Item {
            id: arow
            objectName: "answerRow"
            width: ex.width
            readonly property real lineH: 18
            height: ex.rowN > 0 ? ex.rowN * lineH + 12 : 0
            visible: ex.rowN > 0
            // a long answer stays up long enough to read the lines it ended on
            readonly property real readMs: ex.rowN > 3 ? 2500 + ex.rowN * 900 : 0
            Rectangle { x: 16; y: 10; width: 2; height: Math.max(0, ex.rowN * arow.lineH - 4); radius: 1; color: "#D97757" }
            Repeater {
                model: ex.rowN
                Text {
                    x: 26; y: 6 + index * arow.lineH; width: ex.width - 26 - 16; height: arow.lineH
                    text: ex.rowLines[index] || ""
                    textFormat: Text.StyledText; wrapMode: Text.NoWrap
                    color: "#F2F2F2"; font.family: "Segoe UI Variable Text"; font.pointSize: 10
                }
            }
        }
    }

    function removeApproval(id) {
        for (var i = 0; i < approvalModel.count; i++) if (approvalModel.get(i).apId === id) { approvalModel.remove(i); return }
    }
    Connections {
        target: model
        function onEffectShown(e) { effect.show(e) }
        function onEffectArt(id, url) { if (effect.ev && effect.ev.id === id) effect.art = url }
        function onWidgetShown(w) { widget.show(w) }
        function onWidgetCleared() { widget.clear(); if (ex.resting) ex.clearTurn() }
        function onQuestionShown(q) { linger.stop(); ex.question = q; ex.answer = "" }
        function onAnswerShown(a) { ex.answer = a }
        function onApprovalShown(a) { ex.removeApproval(a.id); approvalModel.append({ apId: a.id, json: JSON.stringify(a) }) }
        function onApprovalCleared(id) { ex.removeApproval(id) }
    }
}
