# Character packs

The little character in the island (and, if you like, on the desktop) is a **pack**: a
folder anyone can add with a pull request. hey-claude ships five: Echo, Cinder, Wren,
Tycho and Hum (designed in `docs/character-options.html`). You pick one in
**Settings → Character**, or keep the island's own ember.

```
characters/<id>/
  character.json    the manifest: who it is, which states it draws, its idle antics
  Character.qml     the QML interface (usually three lines, see below)
  <id>.js           the drawing and motion (if you build on the kit)
  preview.png       the picker's thumbnail (tools/character_lab.py --previews)
```

For local experiments put the folder in `state/characters/<id>/` instead (git-ignored):
it shows up in Settings with no PR.

## The interface: registry states

The app speaks to a pack in **states** from the versioned registry `ui/states.json`
(shared with island themes). Five are **core** and every pack draws them:

| state | when |
|---|---|
| `idle` | wake word armed, nothing happening (all day: keep it calm) |
| `off` | listening switched off, the mic is closed |
| `listening` | hearing the user; `level` is the mic level |
| `thinking` | working out the answer |
| `talking` | speaking; `amplitude` is the voice loudness |

Every other state is optional and has a **fallback**, so a pack never breaks when the
registry grows: `sending` → `working` → `thinking`, `done` → `happy` → `idle`,
`approval_needed` → `confused` → `idle`, `asleep` → `drowsy` → `idle`, and so on
(`python -c "from heyclaude import states; print(states.chain('sending'))"`).
List what you draw in `implements`; anything else plays its fallback (logged once).

Registry 1.1 added the sleep progression: `drowsy` (nobody around for ~15 min),
`asleep` (~30 min; the wake word still works) and `waking` (a stretch when they come
back), and `budgets`: the frame rates a pack may use.

## character.json

```json
{
  "id": "echo", "name": "Echo", "kind": "pixel bat",
  "line": "One sentence for the picker.",
  "author": "your name", "licence": "MIT",
  "registry": "1.1",
  "implements": ["idle", "off", "listening", "thinking", "talking", "happy", "confused", "working", "sending",
                 "drowsy", "asleep", "waking"],
  "oneshot": {"happy": 2.9, "confused": 2.8, "sending": 3.6, "waking": 1.6},
  "size": {"pill": 24, "desktop": 120},
  "bounds": [-60, -60, 120, 120],
  "desktop_bounds": [-100, -80, 200, 170],
  "antics": [
    {"id": "moth", "name": "Catches a moth", "duration": 7, "weight": 3, "cooldown": 600, "min_idle": 120,
     "where": ["pill", "desktop"], "when": {"hours": [19, 6]},
     "props": [{"id": "moth", "box": [-58, -58, 116, 60]}],
     "exit": "the moth flutters off, Echo blinks"}
  ]
}
```

- `registry`: the registry version you built against; any `1.x` works with any `1.y`.
- Coordinates are **box units**: the character draws in a 100-unit box centred on (0, 0)
  (24 px in the island, 120 px on the desktop). `bounds` is all it may ever draw in
  inside the island (the pill clips there; keep it near the box, labels sit to its
  right), `desktop_bounds` for the companion.
- `oneshot`: how long each one-shot state plays before `finished(state)` fires.
- Antics, below.

## Character.qml

Most packs build on the kit (`ui/qml/HeyClaude/Character`: a Canvas, the simulation
from the design page, a frame-budget driver):

```qml
import QtQuick
import HeyClaude.Character
import "echo.js" as Pack

CanvasCharacter { pack: Pack }
```

`<id>.js` is a `.pragma library` file exporting `make(K)`, where `K` is the kit
(`kit.js`: palette `C`, `clamp`, `lerp`, `mixc`, `rgba`, easings, `heat`, `breath`,
`drawZ`, `drawQ`, `drawCheck`, `pillPath`, `ellipse`, `roundRect`, `drawParts`,
`random`/`rnd`/`pick`, which the lab seeds). It returns:

```js
function make(K) {
  const { C, TAU, clamp, rgba } = K;
  return {
    enter(s, state) {},      // a state starts (s.t = 0)
    update(s, dt) {},        // every frame: s.state, s.t (s in this state), s.T, s.L (mic), s.A (voice),
                             //   s.look {x,y}, s.near, s.notice, s.blink, s.calm, s.cs (your scratch),
                             //   s.fo(key, target, tau) a follower into s.p[key], s.spawn({...}) particles
    draw(o, s) {},           // o.begin() ... o.end() puts you in box units; o.small = in the island,
                             //   o.mw(px) screen px -> units, o.lw(units, minPx) a line width
    antics: { moth: { /* see below */ } },
    fps(s, B) {}             // optional: the frame rate you need now, within the budgets B
  };
}
```

You can also write `Character.qml` from scratch (Shapes, ShaderEffect, Image) if you
keep the same interface: properties `stateId`, `level`, `amplitude`, `progress`,
`count`, `mood`, `size`, `small`, `reducedMotion`, `running`, `pixelScale`, `look`,
`near`, `bounds`, `budgets`, `manualClock`; signals `finished(stateId)` and
`anticFinished(id, caught)`; functions `playAntic(id, seconds)`, `interrupt()`,
`advance(dt)`, `anticIds()`, `anticExit(id)`. (QML's `Item` already has `state`, hence
`stateId`.) Pure QML: no native code, no network, no files outside the folder.

## Idle antics

When nothing has happened for a while the character does small things on its own:
Echo hangs upside down, Cinder roasts a marshmallow, Wren goes fishing. They are the
pack's own; aim for 10–15.

Each one is declared in `character.json` (`id`, `name`, `duration` 1–20 s, `weight`,
`cooldown` s, `min_idle` s of the assistant at rest, `where`: `pill` and/or `desktop`,
optional `when`: `hours` [from, to) wrapping midnight, `months`, `weekdays` (Mon = 0),
`weather` (clear, cloudy, rain, snow, storm, fog, wind; only with a weather source),
`user_idle_min` s; `props` with a `box` each; and `exit`, what the interrupt looks
like) and implemented in the JS:

```js
antics: {
  moth: {
    start(s, a) {},          // a = { id, t, dur, u } (u = t / dur)
    update(s, dt, a) {},
    under(o, s, a) {},       // optional: drawn before the body (box units)
    draw(o, s, a) {},        // drawn over the body (box units)
    end(s, a, caught) {},
    exitDur: 0.25,           // the caught exit, at most ~0.3 s
    exit(o, s, c, u) {}      // REQUIRED: the "caught!" exit, u 0..1 (drop the rod, blink awake)
  }
}
```

The app's scheduler (`heyclaude/characters.py`) picks a weighted random antic, never
the same twice in a row, honouring cooldowns, at most one per few minutes (Settings:
rare / normal / lively / off). Nothing plays while the user types, in a full-screen app
or game, in reduced motion, on battery saver, or while the island is hidden.

**Always interruptible:** a wake word, the hotkey, a task event or the pointer arriving
calls `interrupt()`: the antic stops drawing at once and its `exit` plays over the real
state. An antic never delays a real state.

## Budgets

From `ui/states.json` → `budgets`: `idle_fps` 8 (a resting character, with short
30 fps bursts for a blink), `rest_fps` 4 (off, asleep), `antic_fps` 30, `live_fps` 60.
The kit stops its timer completely (0 fps) in reduced motion at rest. Don't animate
continuously above these.

## Test it

```
.venv\Scripts\python tools\character_lab.py --pack <id>           # the lab window: every state, every antic, fps
.venv\Scripts\python tools\character_lab.py --check --pack <id>   # what CI checks
.venv\Scripts\python tools\character_lab.py --shots out --pack <id>   # a contact sheet png
.venv\Scripts\python tools\character_lab.py --previews --pack <id>    # preview.png for the picker
```

CI (`tests/test_characters.py`) checks every pack: the manifest schema; the core states;
each state, and every registry state through its fallback, renders offscreen without
QML errors; the resting frame budget; every antic has an interrupt exit, stays inside
the antic fps budget and its duration, and stops at once when interrupted; props stay
inside the bounds.

## Originality

Packs are original work. No copies or look-alikes of existing mascots, characters or
logos (Clawd, Mochi or anyone else's), no traced art, no brand marks. The PR template
asks you to confirm it, and a maintainer reviews it by eye.
