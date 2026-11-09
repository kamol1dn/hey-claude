import QtQuick

// A label centred in its parent by its ink, not its line box. A Text's box
// leaves room for descenders, so "not me" (no descenders) would sit ~1.5 px
// low in a chip; this shifts it so the glyphs themselves are in the middle.
Text {
    id: t
    anchors.centerIn: parent
    FontMetrics { id: fm; font: t.font }
    readonly property rect ink: fm.tightBoundingRect(t.text)        // relative to the baseline (y up is negative)
    // box centre - ink centre, both from the top of the box (the baseline is at ascent)
    anchors.verticalCenterOffset: text.length ? (fm.ascent + fm.descent) / 2 - (fm.ascent + ink.y + ink.height / 2) : 0
}
