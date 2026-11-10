import QtQuick

// Hosts one character pack (ROADMAP M6): loads its Character.qml and forwards the live
// inputs. The host is a point: the character's 100-unit box is centred on (0, 0), so the
// island puts it where the ember would be. Used by the island, the desktop companion,
// the Settings preview and tools/character_lab.py.
Item {
    id: host
    property url source: ""                 // the pack's Character.qml ("" = no character)
    property var manifest: ({})
    property string stateId: "idle"         // already resolved for this pack (heyclaude/characters.py)
    property real level: 0
    property real amplitude: 0
    property real progress: 0
    property int count: 0
    property string mood: "neutral"
    property real size: 24
    property bool small: true
    property bool reducedMotion: false
    property bool running: true
    property real pixelScale: 1
    property point look: Qt.point(0, 0)
    property real near: 0
    property var budgets: ({ idle_fps: 8, rest_fps: 4, antic_fps: 30, live_fps: 60 })
    property bool manualClock: false
    readonly property var character: ld.item
    readonly property bool ready: ld.status === Loader.Ready
    readonly property string antic: ld.item ? ld.item.antic : ""
    readonly property real fps: ld.item ? ld.item.fps : 0
    signal finished(string stateId)
    signal anticFinished(string id, bool caught)
    signal loadFailed(string why)

    width: 0; height: 0

    function boundsFor() {
        var m = host.manifest || {}
        var b = host.small ? m.bounds : m.desktop_bounds
        return b && b.length === 4 ? b : (host.small ? [-60, -60, 120, 120] : [-100, -80, 200, 170])
    }
    function load() {
        if (!host.source || host.source.toString() === "") { ld.source = ""; return }
        ld.setSource(host.source, {
            stateId: host.stateId, size: host.size, small: host.small, bounds: boundsFor(),
            budgets: host.budgets, manualClock: host.manualClock, reducedMotion: host.reducedMotion,
            pixelScale: host.pixelScale, running: host.running, level: host.level, amplitude: host.amplitude
        })
    }
    onSourceChanged: load()
    onManifestChanged: if (ld.item) ld.item.bounds = boundsFor()
    onSmallChanged: if (ld.item) { ld.item.small = host.small; ld.item.bounds = boundsFor() }
    Component.onCompleted: load()

    function playAntic(id, dur) { return ld.item ? ld.item.playAntic(id, dur) : false }
    function interrupt() { return ld.item ? ld.item.interrupt() : false }
    function advance(dt) { return ld.item ? ld.item.advance(dt) : 0 }
    function anticIds() { return ld.item ? ld.item.anticIds() : [] }
    function anticExit(id) { return ld.item ? ld.item.anticExit(id) : false }
    function info() {
        var c = ld.item
        if (!c) return { ready: false, status: ld.status }
        return { ready: true, state: c.stateId, antic: c.antic, fps: c.fps, frames: c.frames, w: c.width, h: c.height,
                 bounds: c.bounds, small: c.small }
    }

    Loader {
        id: ld
        x: item ? -item.boxX : 0
        y: item ? -item.boxY : 0
        asynchronous: false
        onStatusChanged: if (status === Loader.Error) host.loadFailed("could not load " + host.source)
    }
    Binding { target: ld.item; property: "stateId"; value: host.stateId; when: ld.item !== null }
    Binding { target: ld.item; property: "level"; value: host.level; when: ld.item !== null }
    Binding { target: ld.item; property: "amplitude"; value: host.amplitude; when: ld.item !== null }
    Binding { target: ld.item; property: "progress"; value: host.progress; when: ld.item !== null }
    Binding { target: ld.item; property: "count"; value: host.count; when: ld.item !== null }
    Binding { target: ld.item; property: "mood"; value: host.mood; when: ld.item !== null }
    Binding { target: ld.item; property: "size"; value: host.size; when: ld.item !== null }
    Binding { target: ld.item; property: "reducedMotion"; value: host.reducedMotion; when: ld.item !== null }
    Binding { target: ld.item; property: "running"; value: host.running; when: ld.item !== null }
    Binding { target: ld.item; property: "pixelScale"; value: host.pixelScale; when: ld.item !== null }
    Binding { target: ld.item; property: "look"; value: host.look; when: ld.item !== null }
    Binding { target: ld.item; property: "near"; value: host.near; when: ld.item !== null }
    Binding { target: ld.item; property: "budgets"; value: host.budgets; when: ld.item !== null }
    Connections {
        target: ld.item
        ignoreUnknownSignals: true
        function onFinished(s) { host.finished(s) }
        function onAnticFinished(id, caught) { host.anticFinished(id, caught) }
    }
}
