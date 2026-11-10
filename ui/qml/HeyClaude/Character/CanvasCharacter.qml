import QtQuick
import "kit.js" as K

// The base every built-in character pack uses (ROADMAP M6). A pack's Character.qml is
//
//     import QtQuick
//     import HeyClaude.Character
//     import "echo.js" as Pack
//     CanvasCharacter { pack: Pack }
//
// where echo.js exports make(K) -> { enter, update, draw, antics, ... } (characters/README.md).
// This item is the QML interface the app talks to; a pack may also implement it from
// scratch (Shapes, ShaderEffect) as long as it keeps these properties, signals and functions.
//
// Frame budget: nothing runs at all unless something moves. A Timer steps the simulation
// at the rate the state wants (kit.js wantFps, capped by the registry's budgets) and stops
// at 0 fps; any input change kicks it again.
Item {
    id: root

    // ---- inputs -----------------------------------------------------------------
    property var pack: null                 // the pack's JS module (exports make(K))
    property string stateId: "idle"         // a registry state the pack implements (resolved by the app)
    property real level: 0                  // mic level 0..1
    property real amplitude: 0              // voice amplitude 0..1
    property real progress: 0
    property int count: 0
    property string mood: "neutral"
    property real size: 24                  // the 100-unit box, in px
    property bool small: size < 60          // in the island (true) or a desktop companion (false)
    property bool reducedMotion: false
    property bool running: true             // false: hold still at 0 fps (hidden, suspended)
    property real pixelScale: 1             // the scale of the items above us: draw at full resolution
    property point look: Qt.point(0, 0)     // where the pointer is, -1..1 from the character's centre
    property real near: 0                   // 0..1, how close the pointer is
    property var budgets: ({ idle_fps: 8, rest_fps: 4, antic_fps: 30, live_fps: 60 })
    property bool manualClock: false        // tests and the lab: advance(dt) instead of the timer
    property var oneshots: ({})             // character.json "oneshot": state -> seconds (the JS may also say)

    // ---- outputs ----------------------------------------------------------------
    signal finished(string stateId)         // a one-shot state has played out
    signal anticFinished(string id, bool caught)
    property string antic: ""              // the antic playing now (set here, never by the app)
    property real fps: 0                    // the rate it is running at now (0 = still)
    property int frames: 0                  // paints so far (the lab measures fps with it)
    property var def: null
    property var sim: null
    // the area the character may draw in, in box units [x, y, w, h] (character.json "bounds",
    // or "desktop_bounds" for the companion); the box is 100 units centred on (0, 0)
    property var bounds: [-60, -60, 120, 120]
    readonly property real boxX: -bounds[0] / 100 * size    // the box centre, in this item
    readonly property real boxY: -bounds[1] / 100 * size

    width: size * bounds[2] / 100
    height: size * bounds[3] / 100

    property real _T: 0
    property real _last: 0
    property real _oneshot: -1

    function anticIds() { return def && def.antics ? Object.keys(def.antics) : [] }
    function hasAntic(id) { return !!(def && def.antics && def.antics[id]) }
    function anticExit(id) { var a = def && def.antics && def.antics[id]; return !!(a && typeof a.exit === "function") }

    // Play antic `id` for `dur` seconds (the manifest's duration). false if the pack lacks it.
    function playAntic(id, dur) {
        if (!sim || !hasAntic(id)) return false
        if (sim.antic) interrupt()
        sim.caught = null
        sim.antic = { id: id, t: 0, dur: Math.max(0.2, dur || 6), u: 0 }
        antic = id
        var a = def.antics[id]
        if (a.start) a.start(sim, sim.antic)
        kick()
        return true
    }
    // End an antic at once with its short "caught!" exit. Safe to call any time.
    function interrupt() {
        if (!sim || !sim.antic) return false
        var id = sim.antic.id, a = def.antics[id]
        if (a && a.end) a.end(sim, sim.antic, true)
        sim.caught = { id: id, t: 0, dur: (a && a.exitDur) || 0.25 }
        sim.antic = null
        antic = ""
        anticFinished(id, true)
        step(0)                                  // the next paint already shows the real state
        canvas.requestPaint()
        kick()
        return true
    }

    function _make() {
        if (!pack) return
        K.seed(0)
        def = pack.make(K)
        sim = new K.Sim(def)
        antic = ""
        sim.calm = reducedMotion
        sim.small = small
        sim.set(stateId)
        _oneshot = oneshotFor(stateId)
        step(0)                                  // the first paint sees a stepped simulation
        kick()
    }
    function oneshotFor(st) {
        var m = (def && def.oneshot) || oneshots || {}
        return m[st] ? m[st] : -1
    }
    onSmallChanged: if (sim) { sim.small = small; canvas.requestPaint() }
    onPackChanged: _make()
    Component.onCompleted: _make()
    onStateIdChanged: {
        if (!sim) return
        if (sim.antic) interrupt()
        sim.set(stateId)
        _oneshot = oneshotFor(stateId)
        kick()
    }
    onReducedMotionChanged: if (sim) { sim.calm = reducedMotion; if (reducedMotion) { sim.parts = []; interrupt() } kick() }
    onLevelChanged: if (sim) { sim.inL = level; kick() }
    onAmplitudeChanged: if (sim) { sim.inA = amplitude; kick() }
    onProgressChanged: if (sim) { sim.progress = progress; kick() }
    onCountChanged: if (sim) { sim.count = count; kick() }
    onMoodChanged: if (sim) { sim.mood = mood; kick() }
    onLookChanged: if (sim) { sim.lookT = { x: look.x, y: look.y }; sim.lastMove = _T; kick() }
    onNearChanged: if (sim) { sim.nearT = near; sim.lastMove = _T; kick() }
    onRunningChanged: kick()
    onSizeChanged: canvas.requestPaint()
    onPixelScaleChanged: canvas.requestPaint()

    // one simulation step of dt seconds (0 = just re-evaluate)
    function step(dt) {
        if (!sim) return
        _T += dt
        sim.inL = level; sim.inA = amplitude
        sim.step(dt, _T)
        if (sim.antic && sim.antic.t >= sim.antic.dur) {
            var id = sim.antic.id, a = def.antics[id]
            if (a && a.end) a.end(sim, sim.antic, false)
            sim.antic = null
            antic = ""
            anticFinished(id, false)
        }
        if (_oneshot > 0 && sim.t >= _oneshot) { _oneshot = -1; finished(stateId) }
    }
    function wanted() {
        if (!sim || !running) return 0
        var f = def.fps ? def.fps(sim, budgets) : K.wantFps(sim, budgets)
        if (f === undefined || f === null) f = K.wantFps(sim, budgets)
        if (f <= 0 && _oneshot > 0) f = 4         // still at rest, but a one-shot's clock must reach finished()
        return Math.max(0, Math.min(f, sim.antic ? budgets.antic_fps : budgets.live_fps))
    }
    // tests / lab: step by dt and paint now
    function advance(dt) { step(dt); canvas.requestPaint(); fps = wanted(); return fps }

    function kick() {
        if (manualClock || !sim) { canvas.requestPaint(); return }
        var f = wanted()
        fps = f
        if (f <= 0) { ticker.stop(); canvas.requestPaint(); return }
        var iv = Math.max(8, Math.round(1000 / f))
        if (ticker.interval !== iv) ticker.interval = iv
        if (!ticker.running) { _last = Date.now(); ticker.start() }
    }
    Timer {
        id: ticker
        repeat: true
        onTriggered: {
            var now = Date.now(), dt = Math.min(0.25, Math.max(0.001, (now - root._last) / 1000))
            root._last = now
            root.step(dt)
            canvas.requestPaint()
            root.kick()
        }
    }

    Canvas {
        id: canvas
        // drawn at the final on-screen resolution, then scaled back into the item
        width: Math.max(1, Math.ceil(root.width * root.pixelScale))
        height: Math.max(1, Math.ceil(root.height * root.pixelScale))
        scale: 1 / root.pixelScale
        transformOrigin: Item.TopLeft
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Immediate
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            if (!root.sim) return
            var ps = root.pixelScale
            var o = K.mkO(ctx, root.boxX * ps, root.boxY * ps, root.size * ps, root.small, ps)
            root.sim.draw(o)
            root.frames++
        }
    }
}
