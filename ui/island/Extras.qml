import QtQuick

// Under the island's head: an M9 effect row, approval cards (M7) and the
// response widget, stacked. Its height and minimum width become part of the
// pill's geometry (Island.qml hands them to the engine), so the pill grows
// with an "inhale" and the content fades in once it is mostly there.
Item {
    id: ex
    property bool resting: false
    readonly property real contentHeight: (effect.ev ? effect.height : 0) + approvals.height + (widget.spec ? widget.height : 0)
    readonly property real minWidth: Math.max(effect.ev ? effect.minWidth : 0, approvals.count ? 440 : 0, widget.spec ? widget.minWidth : 0)
    readonly property bool animating: effect.animating
    readonly property real flash: effect.ev ? effect.flash : 0
    function info() {
        return { height: contentHeight, minWidth: minWidth, effect: effect.ev ? effect.ev.effect : "",
                 widget: widget.spec ? widget.spec.widget : "", widgetReady: widget.ready, widgetSource: widget.source,
                 approvals: approvals.count }
    }
    height: contentHeight

    Column {
        width: ex.width
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
            onAction: function (wid, name, payload) {
                if (!model) return
                if (name === "open") model.openUrl(String(payload))
                else if (name === "copy") model.copyText(String(payload))
                model.widgetAction(wid, name, payload === undefined ? null : payload)
                if (name === "dismiss" || name === "cancel") clear()
            }
            onExpired: clear()
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
        function onWidgetCleared() { widget.clear() }
        function onApprovalShown(a) { ex.removeApproval(a.id); approvalModel.append({ apId: a.id, json: JSON.stringify(a) }) }
        function onApprovalCleared(id) { ex.removeApproval(id) }
    }
}
