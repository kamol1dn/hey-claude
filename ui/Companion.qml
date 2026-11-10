import QtQuick

// The desktop companion window (heyclaude/companion.py): one CharacterHost, its box
// centre at (boxX, boxY) so the whole desktop_bounds area fits the window.
Item {
    id: root
    property real boxX: 100
    property real boxY: 80
    CharacterHost {
        objectName: "host"
        x: root.boxX; y: root.boxY
        small: false
        size: 120
    }
}
