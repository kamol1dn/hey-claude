import QtQuick

// Staggered entry for a card's rows: fade in and rise 5 px, 45 ms apart,
// once the panel has mostly opened. Reduced motion: a plain 160 ms fade.
Item {
    id: r
    property int i: 0
    property bool reduced: false
    implicitWidth: childrenRect.width; implicitHeight: childrenRect.height
    opacity: 0
    transform: Translate { id: tr; y: r.reduced ? 0 : 5 }
    Component.onCompleted: anim.start()
    SequentialAnimation {
        id: anim
        PauseAnimation { duration: r.reduced ? 0 : r.i * 45 + 120 }
        ParallelAnimation {
            NumberAnimation { target: r; property: "opacity"; to: 1; duration: r.reduced ? 160 : 320; easing.type: Easing.OutCubic }
            NumberAnimation { target: tr; property: "y"; to: 0; duration: 320; easing.type: Easing.OutCubic }
        }
    }
}
