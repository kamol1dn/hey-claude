import QtQuick
import "../ui"

// The stage of tools/character_lab.py: one CharacterHost on a pill-black (or clear) ground.
Rectangle {
    id: stage
    width: 400; height: 300
    color: clear ? "transparent" : "#080808"
    property bool clear: false
    CharacterHost { id: h; objectName: "host"; x: Math.round(stage.width / 2); y: Math.round(stage.height / 2) }
}
