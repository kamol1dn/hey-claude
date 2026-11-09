# Island motion: "Ember & Thread"

The M5 motion spec for the hey-claude island. It covers states, timelines, interruption, the QML plan, calm mode, the perf budget and acceptance checks.
The reference is `docs/island-motion-preview.html`. Open it in a browser. Every number below is what the preview runs, unless a line says **(QML only)**. The preview's only deliberate deviation: the 1 h pause is compressed to 12 s, with the pre-warm over its last 2 s.

Geometry and colours come from `heyclaude/island.py`:
- `PILL_H` 40, idle/off 28, `INPUT_W×INPUT_H` 560×46 plus 18 px per extra line, `ROW_H` 30.
- Ready is `max(120, label+46)` wide. Off is 130 wide, or 170 with tasks or "until".
- Listening, holding and followup are 300 wide.
- Text states are `min(560, max(300, text+80))` wide, and 60 high if the text wraps.
- Thinking and speaking also carry the mic and stop buttons (`CTRL_W` 66, `BTN_R` 12): `min(560, max(300, text+90) + 66)`. The text is elided or wrapped to fit in 560 − 90 − 66 = 404 px, so the pill never outgrows the 600 px window.

---

## 1. Idea

There are two things in the pill, and every state is a combination of them.

- **The ember** is Claude's presence. It is a small round coal at the pill's left end, where today's dot is. It has a hot core and a warm glow that stays **inside** the pill.
  - Its state shows in its **temperature** (ash, then banked, live, hot, white-hot), its **breath rate** and its **glow reach**.
  - It never spins, bounces or bursts. It is a plain disc, not a glyph, star or logo.
  - Nothing in the island copies or evokes the Claude or Anthropic logo or spark mark: no rays, petals or asterisk shapes, in any state.
- **The thread** is the voice line. It is a single 1.5–2 px hairline that is born out of the ember and always returns into it. Each state is a *gesture* of the thread, so states are told apart by **shape**, not only by colour.

| State | Thread gesture |
|---|---|
| ready / off / paused | absent (inside the ember) |
| listening | travelling wave flowing **in** toward the ember |
| holding | the same wave, pinned between two brackets |
| followup | the wave, over a warm wash that drains into the ember |
| input | the field's baseline |
| thinking | cursive loops being written |
| speaking | wave flowing **out** of the ember toward the words |
| task done | a check mark |
| error | an underline under the message |

### Two laws hold everywhere
1. **Inhale and exhale.**
   - Growing is an *inhale*: springy, with about 4.5 % overshoot.
   - Shrinking is an *exhale*: critically damped, with no overshoot.
   - The preset is chosen by direction at every retarget.
2. **Whose ink.**
   - **White thread = the user** (listening, holding, input, followup).
   - **Terracotta thread = Claude** (thinking, speaking).
   - On wake, the thread comes out terracotta and turns white at 220 ms: Claude hands you the pen.

---

## 2. Palette

| Token | Value | Use |
|---|---|---|
| `bg` | `#080808` @ 0.97 | pill body |
| `rim` | `#FFFFFF` @ 0.086 | border. Off/paused @ 0.05. Listening/followup: `live` @ 0.22. Speaking: `live` @ 0.18. Holding/input: `live` @ 0.40. Error: `bad` @ 0.25 |
| `ash` | `#5C5652` | off/paused ember. Darker than every warm token, so off never looks more alive than ready |
| `banked` | `#B8664C` | ready ember, deep part of the breath |
| `live` | `#D97757` | accent: Claude's ink, rings, brackets, caret |
| `hot` | `#EC9A78` | ember core, voice peaks, the newest spoken word |
| `white-hot` | `#FFE3D6` | woke flash only, ≤ 140 ms |
| `cool` | `#B38A7A` | backend downgrade, voice mismatch |
| `user` | `#F2F2F2` @ 0.88 | user's thread |
| `ok` | `#6CCB7A` | task done: ember tint ≤ 1.1 s, and the check stroke |
| `bad` | `#E5646A` | error: ember, underline, rim. Never a fill |
| `fg` / `dim` | `#F2F2F2` / `#9A9A9A` | live text / idle text |

Rules:
- **Glow** is a Gaussian centred on the ember, clipped to the pill, with alpha 0.05–0.42. There is no outer halo or drop shadow anywhere.
- **Colours** are always followed in linear-ish RGB by a one-pole follower. The τ comes from whoever set the target: default 120 ms, woke 40 ms, error 60 ms. A colour never jumps.
- **Colour-vision safety.** Each colour cue has a shape twin:
  - done = a check stroke
  - error = an underline plus a flinch
  - off = a slash
  - paused = a resume arc
  - holding = brackets

---

## 3. Motion tokens

| Token | Physical (m = 1) | QML `SpringAnimation` start point | Feel |
|---|---|---|---|
| `inhale` | k 380, ζ 0.70 | spring 4.0, damping 0.30, epsilon 0.25 | ~4.5 % overshoot, ~300 ms |
| `exhale` | k 180, ζ 1.00 | spring 3.0, damping 0.45, epsilon 0.25 | no overshoot, ~450 ms |
| `snap` | k 520, ζ 0.60 | spring 5.5, damping 0.25, epsilon 0.25 | ~9 % overshoot, ~280 ms (woke, holding grip) |
| `soft` | k 120, ζ 0.85 | spring 2.2, damping 0.40, epsilon 0.002 | positions of content, ember x |
| `calm` | 160 ms `OutCubic` tween | `NumberAnimation` | replaces every spring in calm mode |

- **Easings:** `nib` = `BezierSpline [0.2, 0, 0, 1]` for drawing strokes. Exits use `InQuad`/`InCubic`. Entries use `OutCubic`.
- **Breath** `breath(f)` is asymmetric: the inhale takes 40 % of the period with `OutSine`, the exhale 60 % with `InOutSine`.
- **Audio follower:** `y += (x − y)·(1 − e^(−dt/τ))`.
  - Mic: τ 30 ms attack / 180 ms release.
  - TTS: τ 25 ms attack / 140 ms release.
  - Mic input is gated and curved: `L = clamp((raw − 0.02)/0.5, 0, 1)^0.7`.
  - Samples arrive at 30 Hz and the follower runs per rendered frame, so 60 fps motion comes out of 30 Hz data.

---

## 4. States

Coordinates are px from the pill's top-left.

| | Value |
|---|---|
| Ember centre | x = 18 (28 px states), 20 (40 px states), 22 (input); y = head height / 2 |
| Thread field in voice states | x 40 → 208 |
| Label | x 216 in voice states, x 30 in 28 px states |

### Summary table

| State | Size | Ember | Thread | Idle motion | Render rate |
|---|---|---|---|---|---|
| ready | label+46 × 28 | `banked`↔`live`, r 3.85–4.25 | none | `breath(0.10 Hz)` (10 s) | 8 fps (0 in calm) |
| off | 130 × 28 | `ash` r 3.0, 2 px slash, no glow | none | none | 0 fps |
| paused | 170 × 28 | `ash` + `live` pilot r 1.5 + resume arc r 7.5 | none | pilot `breath(0.05 Hz)` | 2 fps |
| loading | text+80 × 40 | `banked` r 3.4, fed by kindling sparks | none | sparks every 466 ms | 30 fps |
| woke | → 300 × 40 | catch flash (§5.1) | drawn out of the ember | one-shot 420 ms | 60 fps |
| listening | 300 × 40 | `live` r 5 + 2.5·L | `user`, wave flowing in | hum 0.6 px when silent | 60 fps |
| holding | 300 × 40 | as listening | `user` wave between `live` brackets | taut ripple 0.5 Hz when silent | 60 fps |
| input | 560 × 46(+18n) | r 4.5 + 1.5·L, kicked by keys | `user` baseline @ 0.25, mic wave in its middle | caret breathes 1 Hz | 60 fps; 15 when idle 2 s |
| followup | 300 × 40 | `live`, cooling | `user` wave × 0.7 over a draining wash | dots breathe | 60 fps |
| thinking | text+90+66 × 40 | `live` r 4, slow swell | `live` cursive loops | pen writes | 60 fps (30 after 30 s) |
| speaking | text+90+66 × 40/60 | r 4 + 3·A, ripples on onsets | `live` wave flowing out | breath when A≈0 | 60 fps |
| error | text+56 × 40 | `bad`, flinch | `bad` underline under message | breath 0.25 Hz ±0.3 | 60 fps in, then 0 |
| mismatch | ready size | `cool`, head shake | none | none | 60 fps ≤ 0.7 s |
| tasks | overlay | satellites orbit r 9 | none | 1 rev / 10 s | 10 fps |
| done | overlay | `ok` absorb | `ok` check mark | none | 60 fps ≤ 1.1 s, then 0 |

### ready (wake word armed; all day)

**Look**
- 28 high. Label in `dim` 9 pt: `hey claude · gpu (turbo)`, or `2 tasks running · gpu` in `fg` when tasks are running.
- No thread.
- Rim `#FFF` @ 0.086.

**Idle** (applied at draw time from a timer phase, never through Behaviors), `breath(0.10 Hz)`:
- Ember scale 0.96 → 1.06, so r 3.85 → 4.25.
- Colour mixes `banked` → `live`.
- Glow alpha 0.05 → 0.10, glow radius 18 → 26.
- Nothing else moves: no crackle, no drift, no text motion.

**Drowse** (QML only): after 10 min with no event or hover:
- The period becomes 16 s, the glow max 0.07 and the timer 4 fps.
- This is blended in with `soft` over 4 s.

**Hover:** glow +0.04 and the label `dim` → `fg` over 180 ms.

**Entry ("exhale")**, from anything:
- Size goes `exhale`.
- The thread collapses into the ember: x1 → x0 with `exhale`, and alpha → 0 over 200 ms `InQuad`.
- Ember r → 4.0 and colour → `live`, glow → baseline over 500 ms.
- The label swaps.
- The breath phase starts at the exhale midpoint, so nothing pops.

**From off ("rekindle", 650 ms):**
- 0–160 ms: the slash retracts.
- 100 ms: colour `ash` → `hot`, r 3.0 → 4.6 (`inhale`) → 4.0 at 360 ms.
- 100–650 ms: glow 0 → 0.16 → baseline.

### off (mic closed)

**Look**
- 130×28, or 170 with tasks ("off · 2 running").
- Ember `ash` r 3.0, no glow.
- **Slash** 2.0 px `dim` over a 3.0 px `bg` underlay, (−5, +5) → (+5, −5) from the ember. That is thicker than today's 1.3 px, so it reads at 28 px.
- Rim @ 0.05. Label `dim` "listening off".

**Idle:** none, 0 fps. It is calmer than ready by being completely still.

**Entry ("banking", 520 ms)**
- 0–420 ms: r 4.0 → 3.0 (`InOutSine`), colour → `ash`.
- 0–300 ms: glow → 0 (`OutQuad`).
- 180–400 ms: slash trim 0 → 1 (`OutCubic`).
- Width goes `exhale`.

**Exit:** the slash retracts over 160 ms (`InCubic`), then the target state's entry runs.

### paused (pause for 1 h)

**Look**
- 170×28. Ember `ash` r 3.0, no slash.
- A `live` **pilot light** r 1.5 at the ember centre.
- A **resume arc**: r 7.5, 1.2 px, `live` @ 0.35. It fills clockwise from 12 o'clock as `elapsed/total`, like a dawn completing rather than a countdown draining.
- Label `dim` `paused · 42m` in tabular figures. Under 60 s it reads `paused · 0:42`.

**Idle**
- Pilot alpha 0.25 → 0.50, `breath(0.05 Hz)`, at 2 fps.
- The arc retargets every 30 s with `soft`.
- The changed digit rolls: the old one goes up 4 px and fades, the new one comes from 4 px below, 200 ms `OutCubic`.

**Pre-warm (last 60 s)**
- The label suffix reads `resuming`.
- Ember `ash` → `banked` linearly with time.
- Arc alpha 0.35 → 0.70.
- Pilot breath 0.10 Hz.
- At 0 the arc is full and **rekindle** plays into ready. The island wakes itself up.

**Entry:** banking without the slash. Then pilot alpha 0 → 0.4 (600 ms, delay 400), and the arc grows from 0 (`soft`).

**Manual resume:** the arc goes full in 250 ms, then rekindle.

### loading (models load, or lean-mode reload)

**Look**
- text+80 × 40. Ember `banked` r 3.4.
- Label `dim`: `loading models…` or `warming up…`.
- Progress, when known: a `live` @ 0.07 wash from the left edge to `progress·w` (`soft`).

**Idle ("kindling")**
- Three 2 px `live` sparks spawn at (ember+26, ±6), staggered 466 ms.
- Each one curves into the ember over 1400 ms (`InQuad`), with opacity 0 → 0.8 → 0.
- Each arrival kicks ember r by +0.35 (20 ms attack, 260 ms release).
- 30 fps.

**Exit to ready**
- Spawning stops and the last spark lands.
- The ember takes one big breath: r 3.4 → 4.8 → 4.0, `inhale`.
- Then "exhale".

### woke

A one-shot of 420 ms (§5.1), always handing over to listening (or input, for talk-or-type).

### listening (mic open, L at 30 Hz)

**Look**
- 300×40. Ember `live`; the core turns `hot` when L > 0.35.
- Thread `user`, 1.75 + 0.5·L px, field x 40 → 208.
- Label `dim` "listening" at x 216. Rim `live` @ 0.22.

**Thread, "flowing in"**
- `y(u) = A(u)·w(u)·sin(2π(x/22 + 1.1·t))`. The phase travels toward the ember.
- `w(u) = sin(πu)^0.8`, so both ends are pinned.
- `A(u) = 0.6 + 10.5·L(t − d(u))`, capped at 11.
- `d(u) = 0.36 s·(1 − u)`: the far end shows the newest level, so loudness visibly flows in to the ember.

**Ember and glow**
- r = 5.0 + 2.5·L.
- Glow alpha = 0.10 + 0.22·L, radius = 26 + 34·L.
- There is **no pill scale**: the body never moves with audio.

**Silent** (L < 0.04)
- The thread amplitude floor is 0.6 px, a hum.
- The ember does `breath(0.6 Hz)` ±0.4 px.

**Exit to thinking ("swallow", 360 ms)**
- The thread snapshot-blends into the cursive generator while the field contracts from 168 to 36 px (`exhale`).
- Ink `user` → `live` over 200 ms.
- Labels swap.

### holding (Ctrl held)

**Look**
- As listening, plus two `live` **brackets** (1.5 × 12 px) at x 34 and x 214 that pin the thread.
- Taper exponent 0.8 → 0.45 (the wave fills more of the field: gripped).
- Rim `live` @ 0.40. Label "holding" in `live` at x 224 (clear of the right bracket).

**Idle**
- Audio as listening.
- When silent for 400 ms, the thread goes **taut**: a standing ripple of ±0.6 px at 0.5 Hz, not the hum.

**Entry ("grip", 260 ms)**
- The brackets draw from their centres, 0 → 12 px tall (`OutBack` 1.2, 140 ms).
- The pill **squeezes** width 300 → 294 → 300: a `snap` retarget to 294, then 300 after 90 ms. This mirrors the key press.

**Exit ("release")**
- The brackets drift 6 px outward and fade over 200 ms (`OutQuad`).
- The width does +4 → 300.
- The taper returns over 200 ms.

### input (talk-or-type; field focused; mic open)

**Look**
- 560×46, +18 per line up to 4 lines. Ember at x 22, r 4.5 + 1.5·L. Rim `live` @ 0.40.
- The thread becomes the **baseline**: `user` @ 0.25, x 44 → w−16, y = h − 8.
- The middle 120 px of the baseline carries the mic: `A = L^0.6·3 px`.
- Placeholder `dim` "type or talk…".
- Caret: 1.5 px `live`, **breathing** (opacity 1.0 ↔ 0.35, sine, 1.0 Hz). Any key resets it to 1.

**Typing feeds the ember:** each key adds r +0.6 and glow +0.04 (20 ms attack, 220 ms release).

**Entry ("unroll", 380 ms)**
- Width (`inhale`) and height (`inhale`).
- The thread draws out along the baseline with `nib`, 260 ms, starting at +40.
- Field alpha 0 → 1, delay 120 ms over 180 ms, sliding in from x +6.
- **Focus is set at 0 ms, before any visual.**

**Submit ("fed to the ember", 420 ms)**
- 0–220 ms: the typed text goes to scale 0.96, x −10 px toward the ember and alpha 0 (`InCubic`).
- 200 ms: the ember kicks r +1.2 and glow 0.30.
- 220 ms: geometry goes `exhale` to the thinking size.

**Esc:** the field fades over 120 ms, then "exhale".

### followup ("go on…"; no wake word needed for N s, default 5)

**Look**
- 300×40. Thread `user` at 0.7× listening amplitude.
- Label `fg` "go on" plus three dots breathing 0.3 → 1.0 (1.2 s period, 200 ms offsets).

**Timer, a "draining wash".** It fills the whole pill and reads across a room. With `u = remaining/total`:
- A `live` wash covers x 0 → `washX = 20 + (w − 26)·u`, alpha 0.10 + 0.08·u. It has a soft 18 px leading edge.
- The ember glow radius is 10 + 40·u, so the warmth recedes into the coal.
- The ember goes `live` → `banked` over the last 40 %.
- In the last 600 ms the remainder snaps into the ember (`InCubic`), then one ember pulse (r +0.6).

**Voice** (L > 0.15)
- `u` freezes and the wash fades over 200 ms.
- It relabels to listening. The entry does not re-run, and the thread is already there.

**Entry from speaking (280 ms)**
- The thread blends from Claude's outward wave to the user's inward wave.
- Ink `live` → `user` over 200 ms.
- The wash draws from the ember out to full width (`OutCubic`, 260 ms).

**Timeout ("sigh"):** after the last pulse, "exhale" into ready.

### thinking

**Look**
- text+90+66 × 40 (see the controls below). Ember `live` r 4.0, glow 0.10.
- The transcript in `dim` at x 80, elided at 404 px.
- Thread `live` 1.75 px in a 36 px field (x 34 → 70).

**The pen writes**
- A prolate cycloid: `x = r·θ − d·sin θ`, `y = −d·cos θ`, with r 1.9 and d 4.5. That is 3 loops across 36 px.
- The visible window is 3.2π of path, with alpha `q^0.8` (q = 0 at the tail, 1 at the pen): ink drying behind the pen.
- One loop takes 380 ms.
- The stroke runs off the field end and restarts at the left with no cut: samples outside the path have alpha 0.
- The pitch drifts ±8 % at 0.3 Hz.

**Ember:** a slow swell at 0.4 Hz, r ±0.3.

**Patience**
- After 8 s: pen speed × 0.8.
- After 30 s: × 0.65 and render at 30 fps.
- Both blend with `soft` over 2 s.
- A long wait gets calmer, never more anxious.

**Exit to speaking ("gather", 300 ms)**
- The current loop finishes into a flat line.
- The field widens 36 → 40 and blends into the outward wave.
- The first TTS onset fires the first ripple.

### speaking (TTS, A 0..1)

**Look**
- text+90+66 × 40 (60 if wrapped past 404 px), with the width measured from the **whole sentence** at entry so it never grows word by word.
- Thread `live` 2.0 px, field x 34 → 74. Answer in `fg` at x 84.

**Thread, "flowing out"**
- `y = (0.8 + 8·A^0.7)·w(u)·(sin(2π(2u − φ)) + 0.35·sin(2π(4.6u − 1.6φ)))`, with `w = sin(πu)^1.2`.
- φ advances at 0.9 Hz·(1 + 1.5·A) toward the words.

**Ember and glow:** r = 4 + 3·A. Glow alpha = 0.08 + 0.18·A, radius = 24 + 30·A.

**Heat ripples**
- An onset is when A rises by more than 0.18 within 60 ms and A > 0.25.
- Each one emits a ring r 5 → 24, stroke 1.5 → 0.5, alpha 0.30 → 0, 520 ms `OutCubic`, clipped to the pill.
- At most **2** alive, at least 160 ms apart.

**Hot word:** only the *newest* word is warm. It appears in `hot` and cools to `fg` over 400 ms (`OutSine`), fading in over 160 ms with a 1.5 px rise.

**A ≈ 0:** ember `breath(0.5 Hz)` ±0.25.

**Exit:** to followup (above), or "exhale" to ready. The text fades over 160 ms before the width shrinks.

### controls on thinking and speaking (mic, stop)

While Claude thinks or talks, the right end of the head row carries two small round buttons, as in `island.py` today.

**Look**
- Two circles r 12 (`BTN_R`), the stop circle's centre at x = w − 26, the mic's at w − 56 (6 px apart, 14 px from the end). Vertically centred on the text block: y = min(h, 60)/2.
- Fill `#FFF` @ 0.08, @ 0.18 on hover (follower τ 50 ms). Glyphs in `fg`.
- **Mic:** a 6×10 capsule (radius 3) at (−3, −7), a 1.6 px cradle arc (ellipse 12×9 centred at y +0.5, from 20° to 160° through the bottom) and a stem from y +5 to +8.
- **Stop:** a 10×10 rounded square, radius 2.
- They are plain UI glyphs, not brand marks.

**Motion**
- Entry: alpha 0 → 1 and scale 0.85 → 1, 200 ms `OutCubic`, delayed 140 ms so they arrive once the width is most of the way there (rule 10). Thinking → speaking keeps them up: no re-entry.
- Exit (any other state): alpha → 0 over 120 ms `InQuad`, before the geometry shrinks.
- Press: the pressed button dips to scale 0.9 and fill +0.12, back over 220 ms `OutCubic`.

**Actions**
- **Mic = interrupt and talk.** TTS stops and the island goes **woke from its current pose** (barge-in, §5.1): the outward wave snapshot-blends into the drawn-out user wave, so nothing jumps.
- **Stop = hush.** TTS or the turn is cancelled and the island "exhales" to the resting state (ready, or off if the mic was off).
- **Esc** while thinking or speaking does what stop does. A click anywhere else on the speaking pill also hushes it.
- Hit areas are the circles + 3 px. Hovering them shows the pointer cursor.
- Calm and `motion: off`: the same buttons; entry and exit become plain crossfades (160 ms in, 120 ms out) without the scale, and there is no press dip in `off`.

### error

A one-shot "flinch and settle" (§5.3), then a hold.

**Hold**
- Ember `bad` r 4.0 with `breath(0.25 Hz)` ±0.3, written by a 4 fps `Timer` (no `FrameAnimation` during the hold). Glow `bad` @ 0.06.
- The `bad` underline under the message's first line.
- Text `fg`. Rim `bad` @ 0.25.

**Dismiss** after 4 s or on the next event:
- The underline trims away from the left over 200 ms.
- The colour goes → `live` over 600 ms.
- Then "exhale" to the resting state (ready, or off if the mic was off).

### voice didn't match (speaker check rejected a wake)

A "shrug", 700 ms. No red.

| t (ms) | What happens |
|---|---|
| 0 | The woke anticipation starts: `white-hot`, r → 3.2. |
| reject (~140) | Colour → `cool` (τ 120), r → 3.6. Any geometry that woke started goes `exhale` back to 28. |
| reject + 0–420 | **The ember alone** head-shakes on x: `2.0·e^(−t/160)·sin(2π·6t)`. The pill never moves. |
| reject + 60 | The label crossfades to "not your voice?" in `dim`. Width goes `soft` to fit. |
| reject + 1800 | Colour → `live`, label → the ready label, breath resumes. |

### tasks running (overlay on ready, off and paused)

**Satellites**
- One per running task, at most 3.
- 2.5 px dots with a `hot` core, orbiting the ember at r 9, **one revolution per 10 s**, evenly phased. That is about 5.7 px/s, under 1 px per frame at 10 fps.
- In off or paused they are `ash` and still orbit: work continues while listening does not.

**Born:** from the ember centre to its orbit over 400 ms (`OutBack` 1.2), with alpha 0 → 1.

**Hover list (and auto-pop on status change for 6 s)**
- Width `inhale` → ≥ 460. Height `inhale` → head + 6 + 30·n + 6. Radius 18.
- Rows stagger in 35 ms apart: alpha 0 → 1 and y +6 → 0, 200 ms `OutCubic`.
- Each running row's dot is a micro-ember: `breath(0.5 Hz)` r 2.5 → 3.0, with phases offset per row.
- Collapse: rows fade together over 120 ms, then height goes `exhale`.

### task done

A one-shot "the check" (§5.2).

### badge change ("gpu (turbo)" ↔ "cpu (gpu busy)")

**Text:** only the badge segment of the ready label moves.
- The old segment goes up 4 px and fades over 140 ms (`InQuad`).
- The new one comes from 4 px below over 220 ms (`OutCubic`), starting at +80 ms.
- Width goes `soft`.

**Downgrade:** the ember cools: `live` → `cool` over 400 ms, holds 600 ms, then back over 800 ms.

**Upgrade:** the ember flares: r +0.6 and glow +0.06 (120 ms attack, 500 ms decay).

**During any voice state:** an instant swap with no ember cue (no competing motion).

---

## 5. Signature moments

### 5.1 Woke: "the catch", 420 ms

Anticipation and release, then the pen is handed over.

| t (ms) | Ember | Pill and glow | Thread | Label |
|---|---|---|---|---|
| 0 | → `white-hot` (τ 40). r → 3.2 (`snap`): the anticipation | head 28 → 26 (squash). Glow 0.08 → 0.22 | — | ready label exits (100 ms `InQuad`) |
| 80 | **Release:** r → 6.5 (`snap`) | head → 40, width → 300 on `snap`, inheriting velocity. Glow → 0.42, radius → 90 (τ 60) | — | — |
| 120 | — | **Heat ring** r 6 → 40, stroke 2 → 0.5, alpha 0.5 → 0, 320 ms `OutCubic`, clipped | — | — |
| 140 | colour → `hot` (τ 90) | — | — | — |
| 160 | r → 5.0 | glow eases to listening (`soft`) | drawn out: x1 from the ember to 208 (`inhale`), ink `live` | — |
| 200 | — | — | — | "listening" in (220 ms) |
| 220 | colour → `live` | — | ink → `user` over 130 ms: **the pen is handed over** | — |
| 260–420 | — | — | **Pluck:** `+4·e^(−t/90)·sin(2π·7t)·w(u)`, blended into the live wave | — |
| 420 | state = listening, and audio owns everything | | | |

- If the user is already talking, the level mapping blends in from 160 ms.
- A **repeat wake within 600 ms** only gets glow +0.15: no ring and no squash.
- A **talk-or-type** wake targets the input size at 80 ms, and the thread draws as the baseline.

### 5.2 Task done: "the check", 1.1 s plus a 2.5 s hold

| t (ms) | Event |
|---|---|
| 0 | The task's satellite leaves orbit and flies to the ember centre: 220 ms `InCubic`, 2.5 → 1.5 px. |
| 220 | Absorb: ember → `ok` (τ 80), r kick +1.6 (`inhale`), glow tinted `ok` 0.25 → 0 over 480 ms. |
| 240 | The thread leaves the ember as an `ok` **check**: 4 px down-right then 9 px up-right, at ember x + 10. Trim 0 → 1 over 240 ms (`nib`). |
| 300 | The label `done · {task}` in `fg` is revealed behind a 15° nib edge at 18 ms per character (clamped to 120–320 ms). Width goes `inhale` if needed. |
| 480 | Check stroke 1.5 → 2.2 → 1.75 (`snap`). |
| 700–1100 | Ember `ok` → `live` over 400 ms. r → 4.0 (`exhale`). |
| 1100–3600 | Hold, completely still (0 fps). Other satellites re-space over 600 ms (`soft`). |
| 3600 | The check un-draws back into the ember (300 ms `InCubic`). The label crossfades back. Width `exhale`. |

- **During a voice state:** only the absorb plus an `ok` ember tint at 60 % for 600 ms. No label takeover and no check. The list row shows a static check.
- **No deferral queue.**

### 5.3 Error: "flinch and settle", 900 ms

| t (ms) | Event |
|---|---|
| 0 | Flinch: r 4 → 3.2 (60 ms `OutQuad`), colour → `bad` (τ 60). Thread amplitude → 0 within 60 ms, so it goes flat but is not cut. |
| 0–360 | **Tremor of the contents only** (ember and thread): x += `2.5·e^(−t/110)·sin(2π·5.5t)`, giving peaks of 2.5, 1.1 and 0.3 px. **The pill never moves.** |
| 60–400 | Glow `bad` 0.14 → 0.06 (`OutCubic`). Rim `bad` @ 0.25. |
| 120 | The message crossfades in, `fg`. Width goes `inhale` to text + 56 (≤ 560). |
| 120–620 | The flat thread travels under the first line and becomes the **underline**: frame x 34 → 34 + text width, y = cy + 9. It snapshot-blends, then trims on with `nib` from 400 ms. `bad` @ 0.6, 1 px. It is a correction mark, not a strikethrough. |
| 400–900 | Settle: r 3.2 → 4.0 (`soft`). Breath 0.25 Hz. |

---

## 6. Transition graph

```
                  rekindle (650)               banking (520)
        off  <──────────────────>  READY  <────────────────>  paused ──(timer, pre-warm)──> READY
         │  banking                 │ ▲ ▲ exhale (from any state)
         │                  wake/hk │ │ └──── mismatch (shrug, from woke's first 140 ms)
         │                          ▼ │
       loading ──(big breath)──>  WOKE ─┬───────────────┐ talk-or-type
                                   420 │               ▼
                                       ▼            INPUT ──Esc──> READY
                                  LISTENING <─grip/release─> HOLDING
                                   │   ▲                     │
                           swallow │   │ voice (relabel)     │ release + swallow
                                   ▼   │                     ▼
                                  THINKING ──gather──> SPEAKING ──> FOLLOWUP ──sigh──> READY
                                      ▲   fed to the ember   │ barge-in: wake or mic button → WOKE from current pose
                                      └────────── INPUT       │ stop button / Esc (also from THINKING) → exhale to READY or off
  error: from any state → hold → exhale to the resting state (ready, or off)
  overlays, legal in any state: tasks running, task done, badge change, hover list
```

## 7. Interruption rules

1. **Targets, never keyframes.**
   - Every continuous property is a spring, a follower or a from-current tween. This covers width, head height, list height, ember x/r/colour, glow alpha/radius/tint, rim, thread frame (x0, x1, cy), thread ink/width/alpha and wash.
   - A state change only **retargets**. Springs keep position *and velocity*, so a cut mid-overshoot carries its momentum.
   - No transition has a `from`.
2. **Direction picks the preset** at retarget time: target > current means `inhale`, otherwise `exhale`. State-specific presets (`snap` for woke and grip) override this.
3. **Thread snapshot blending.**
   - Each state supplies a generator `g(i, t) → (x, y, alpha)` over 48 samples.
   - On a change, the *drawn* samples are frozen as a snapshot and blended to the new generator over 220 ms (`OutCubic`).
   - A cut mid-blend snapshots the blended output. The line never jumps.
   - There is **one renderer** for all states, so there are no swap pops.
4. **One-shots are overlay layers.** Rings, sparks, satellite flights, tremor, shake and the fed-text ghost are fire-and-forget, with a pool cap of 6.
   - **→ off / paused:** everything alive fast-forwards to its end pose (`finish()`, 90 ms `OutCubic`).
   - **Mismatch during woke:** cancels the woke flash.
   - **Woke during error:** cancels the tremor and keeps the colour blending.
5. **Ember colour priority** (highest wins, lower ones apply when it releases):
   1. error / mismatch
   2. woke flash
   3. done tint
   4. badge cool or flare
   5. the state's base colour
6. **Task done is a rest-state overlay.** Any state change during its hold cancels the done label and un-draws the check (150 ms `InCubic`); the list row keeps its static check.
7. **Labels:** at most 2 layers.
   - A new label makes every live label exit from its *current* alpha and offset.
   - Changes under 80 ms apart are coalesced, so "thinking" never flashes between quick tool turns.
   - Nib reveal is used only for "listening" after woke and for the done label. Every other change is a 120 ms out / 220 ms in crossfade with a 2–3 px y move.
8. **Flap guard:** state changes under 60 ms apart are coalesced into the latest. Woke is never dropped. followup ↔ listening relabels without re-running entry.
9. **Flood guard:** more than 4 state changes in 1 s skips flourishes (ring, squash, pluck, nib, grip squeeze) until 500 ms of quiet. Springs and crossfades still run.
10. **Content vs geometry:**
   - On a shrink, content exits over the first 120–160 ms before the geometry finishes.
   - On a grow, content enters once the geometry is 70 % of the way there.
   - Text is clipped to the pill, so it never pokes out.
11. **Audio survives exits:** L and A decay through their release τ after their state ends, so the ember never jumps when the mic closes.
12. **Guards:**
    - dt is clamped to 1/30 s and integrated in ≤ 1/240 s substeps.
    - NaN → snap to the target and log.
    - A spring not settled within 3 s → snap and log.

---

## 8. QML implementation plan

### Window and bridge
- **One fixed window** of 600 × 260 at the top centre:
  - flags: `FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus`, except while input is open;
  - `color: "transparent"`.
- **Never resize the native window.** The `island` Item animates inside it.
- **Click-through:** `QWindow.setMask(QRegion(pillRect ± 2))`. Call it when a morph starts (the union of start and target) and when it settles. Never per frame.
- **`IslandModel(QObject)`:**
  - Properties: `mode`, `text`, `badge`, `level` (30 Hz, **only** while the mic is open), `ttsAmp` (only while speaking), `tasks` (`QAbstractListModel`), `followupEndsAt`, `pausedUntil`, `motion` (`full|calm|off`), `suspended`.
  - Signals: `woke()`, `mismatch()`, `taskDone(name)`, `badgeChanged(old, new, dir)`, `keyTyped()`.
  - Slots called from QML: `interruptTalk()` (mic button) and `stopSpeaking()` (stop button, Esc, click on the speaking pill), the same signals `island.py` emits today.
- **Python emits nothing per frame in ready.** Today's `_tick` repaint loop is deleted.

### Drivers (exactly one active)
| Tier | Driver | Used by |
|---|---|---|
| 60 | `FrameAnimation` | woke, voice states, thinking, speaking, unsettled springs, live one-shots |
| 30 | `FrameAnimation` with a 33 ms accumulator gate | loading, thinking after 30 s, battery saver, software backend |
| 15 / 10 / 8 / 2 | `Timer` (66 / 100 / 125 / 500 ms) writing phases directly | input idle / tasks / ready / paused |
| 0 | nothing | off, calm idle, holds |

- The engine is a single JS integrator inside the `FrameAnimation`. It does semi-implicit Euler for springs (`v += (−k(x−t) − 2ζ√k·v)·h; x += v·h`), the audio followers, tweens, the one-shot pool and the thread generator.
- `running = !settled || liveState`. `settled` means every |x − t| < ε and |v| < 10ε, with no one-shot alive.
- **Rest invariant:** in ready, off and paused there is **no** running `Animation`, `Behavior` or `FrameAnimation`, and no `layer.enabled`. Idle breath values are pure functions of the timer phase, applied as shader uniforms or transforms (scale and opacity), never as Shape geometry.

### Primitives
| Element | GPU path | Software path |
|---|---|---|
| Pill, rim, glow, wash, ember disc and core, heat rings (≤ 3), thread | **one** `ShaderEffect` (`island.frag`, below) | `Rectangle` pill, `Rectangle` ember, `Shape` + `RadialGradient` glow, `Shape` thread |
| Thread data | 48 points packed into **named** `vec4 p0..p23` (xy\|xy) and alphas in `vec4 a0..a11`. Rebuilt into local arrays in the shader (ShaderEffect cannot bind array uniforms). | `Shape` + `PathPolyline` (24 pts). The cursive tail fade becomes 3 stepped-alpha polylines. |
| Slash, resume arc, brackets, satellites, caret | `Shape` (`CurveRenderer`, Qt ≥ 6.7) / `Rectangle` | same |
| Labels | two ping-pong `Text` items, `renderType: Text.QtRendering` (grayscale AA; a translucent window cannot use ClearType) | same |
| Hot word | one `Text`, `textFormat: StyledText`. A 30 Hz `Timer` rewrites the newest word's colour for 400 ms. **No per-word Items.** | same |
| Nib reveal | `ShaderEffect` over the label with `layer.enabled` only during the ≤ 320 ms reveal | opacity + 1.5 px rise |
| Rows | `ListView` with delegate enter transitions | same |
| Random (sparks, phases) | seeded xorshift, with the seed exposed for golden-frame tests | same |

### `island.frag`
Compile with `pyside6-qsb --glsl "100 es,120,150" --hlsl 50 --msl 12`. The shader avoids `fwidth` and integer bitwise ops, so the ES 1.00 target compiles.

```glsl
#version 440
layout(location=0) in vec2 qt_TexCoord0;
layout(location=0) out vec4 fragColor;
layout(std140, binding=0) uniform buf {
    mat4 qt_Matrix; float qt_Opacity;
    vec2 size;      vec4 pill;          // item px; pill x,y,w,h
    vec4 ember;                         // x, y, r, coreMix
    vec4 emberCol;  vec4 coreCol;       // rgb, a
    vec4 glow;                          // r, alpha, washX (<0 none), washA
    vec4 glowCol;   vec4 rimCol;        // rim: rgb, alpha
    vec4 ring0; vec4 ring1; vec4 ring2; // x, y, r, alpha (stroke = 0.5 + 1.5*alpha/0.5)
    vec4 thr;                           // strokeW, alpha, count(<=48), aaPx (0.6/dpr)
    vec4 thrCol;
    vec4 p0,p1,p2,p3,p4,p5,p6,p7,p8,p9,p10,p11,p12,p13,p14,p15,p16,p17,p18,p19,p20,p21,p22,p23;
    vec4 a0,a1,a2,a3,a4,a5,a6,a7,a8,a9,a10,a11;
};
float sdPill(vec2 q, vec2 b){ float r=b.y; vec2 d=abs(q)-b+r; return length(max(d,0.))+min(max(d.x,d.y),0.)-r; }
float sdSeg(vec2 p, vec2 a, vec2 b){ vec2 pa=p-a, ba=b-a; float h=clamp(dot(pa,ba)/max(dot(ba,ba),1e-4),0.,1.); return length(pa-ba*h); }
float ringA(vec2 p, vec4 r){ float d=abs(length(p-r.xy)-r.z); return r.w*(1.-smoothstep(0.,1.+r.w*2.,d)); }
void main(){
    vec2 p = qt_TexCoord0*size;
    vec2 c = pill.xy + pill.zw*.5;
    float d = sdPill(p-c, pill.zw*.5);
    float aa = thr.w, body = 1.-smoothstep(-aa, aa, d);
    vec3 col = vec3(.031);
    float g = exp(-pow(length(p-ember.xy)/max(glow.x,1.), 2.))*glow.y;      // inner glow
    float wash = glow.z < 0. ? 0. : glow.w*(1.-smoothstep(glow.z-18., glow.z, p.x));
    col += glowCol.rgb*(g + wash);
    col += glowCol.rgb*(ringA(p,ring0)+ringA(p,ring1)+ringA(p,ring2));
    // thread: distance to polyline, per-segment alpha
    vec4 P[24] = vec4[24](p0,p1,p2,p3,p4,p5,p6,p7,p8,p9,p10,p11,p12,p13,p14,p15,p16,p17,p18,p19,p20,p21,p22,p23);
    vec4 A[12] = vec4[12](a0,a1,a2,a3,a4,a5,a6,a7,a8,a9,a10,a11);
    float t = 0.;
    for (int i = 0; i < 47; i++) {
        if (float(i) >= thr.z - 1.) break;
        vec2 pa = (mod(float(i),2.)<.5) ? P[i/2].xy : P[i/2].zw;
        int j = i+1;
        vec2 pb = (mod(float(j),2.)<.5) ? P[j/2].xy : P[j/2].zw;
        float al = min(A[i/4][int(mod(float(i),4.))], A[j/4][int(mod(float(j),4.))]);
        float sd = sdSeg(p, pa, pb);
        t = max(t, al*(1.-smoothstep(thr.x*.5-aa, thr.x*.5+aa, sd)));
    }
    col = mix(col, thrCol.rgb, t*thr.y*thrCol.a);
    float e = length(p-ember.xy);                                          // ember disc + hot core
    float disc = 1.-smoothstep(ember.z-aa, ember.z+aa, e);
    vec3 ec = mix(emberCol.rgb, coreCol.rgb, ember.w*(1.-smoothstep(0., ember.z*.6, e)));
    col = mix(col, ec, disc*emberCol.a);
    float rim = (1.-smoothstep(0., 1.2, abs(d+.6)))*rimCol.a;               // 1 px hairline
    col += rimCol.rgb*rim;
    float a = body*.97;
    fragColor = vec4(col*a, a)*qt_Opacity;                                  // premultiplied
}
```

- **Cost:** about 600 × 60 px × 47 segments ≈ 1.7 M segment tests per frame in voice states, roughly 0.05 ms on an RTX 4060.
- **Thread bounding box (QML only):** set `thr.z = 0` when the thread alpha is below 0.01, so idle skips the loop.
- **Ready at 8 fps:** about 150 × 28 px, which is negligible.

### Degradation
- **Software backend.** When `GraphicsInfo.api === GraphicsInfo.Software`, or the shader fails to load:
  - use the right-hand column of the primitives table;
  - **no `MultiEffect` anywhere** (it needs RHI);
  - cap live states at 30 fps and cut rings to 1.
- **Slow GPU:** if the frame-time EMA stays above 12 ms for 2 s, switch to `calm` visuals and log once.
- **Battery** (`SYSTEM_POWER_STATUS` from Python): live states run at 30 fps.
- **Suspended** (locked, display off, a full-screen app per `SHQueryUserNotificationState`, or hidden): stop all timers and the `FrameAnimation`. On resume, jump straight to targets.

---

## 9. Calm and reduced motion

**Setting:** `config.motion: full | calm | off`.
- At startup and on `WM_SETTINGCHANGE`, read `SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION)`. If Windows animations are off, the default becomes `calm`.
- The preview starts in calm when `prefers-reduced-motion: reduce` is set.

**Calm keeps information-bearing motion and drops decoration**

| Element | Calm behaviour |
|---|---|
| Geometry | every spring → 160 ms `OutCubic`. No squash, grip squeeze or overshoot. |
| Idle | ready, off and paused fully static at 0 fps. The paused arc steps every 30 s. Satellites are static dots at 3, 7 and 11 o'clock. |
| Woke | ember → `hot` (80 ms), geometry 160 ms, label 120 ms. No anticipation, ring or pluck. |
| Listening, holding, followup | thread is a straight line whose length (35 → 100 % of the field) and alpha (0.5 → 1) follow L. Holding keeps static brackets. The followup wash still drains (it is information). Ember r 4.5 → 5.5, glow 0.08 → 0.16. |
| Thinking | no cursive. Ember alpha 0.7 ↔ 1.0 at 0.5 Hz. |
| Speaking | straight line, length ∝ A. No ripples, no hot word. |
| Error | no flinch or tremor. The underline fades in over 150 ms (the shape cue stays). |
| Mismatch | colour plus label only. |
| Done | ember `ok` for 800 ms. The check fades in over 150 ms. |
| Badge | crossfade only. |
| Text | 120 ms opacity crossfades, no translate, no nib. |

**`motion: off`:** everything is instant. Only the audio-driven thread length remains, because it proves the mic is live.

---

## 10. Perf budget (RTX 4060 laptop, D3D11, threaded loop, 60 Hz)

| State | Render rate | GPU (Task Manager 3D, process) | UI-thread CPU |
|---|---|---|---|
| off | 0 fps | 0 % | 0 % |
| ready | 8 fps (drowse 4, calm 0) | < 0.1 % | < 0.2 % |
| ready + tasks | 10 fps | < 0.2 % | < 0.3 % |
| paused | 2 fps | ≈ 0 % | < 0.1 % |
| done hold | 0 fps | 0 % | 0 % |
| error hold | 4 fps (`Timer` 250 ms, breath 0.25 Hz) | ≈ 0 % | < 0.1 % |
| loading | 30 fps | < 0.5 % | < 1 % |
| woke / done / error / mismatch / badge | 60 fps for ≤ 1.1 s | < 2 % burst | < 3 % burst |
| listening, holding, followup, input | 60 fps (input 15 when idle 2 s) | < 1 % | < 2 % |
| thinking | 60 → 30 fps after 30 s | < 0.8 % | < 1 % |
| speaking | 60 fps | < 1 % | < 2 % |
| software backend, live | ≤ 30 fps | n/a | < 5 % |

Most of the real cost is DWM composing a transparent window. That is why rest states must render 0 frames, or at most 8.

The preview follows the same tiers, so its **Preview** fps readout can be checked against this table: `requestAnimationFrame` only while a spring, tween, label, kick or one-shot is alive; a `setTimeout` tick at rest (ready 8, tasks 10, paused 2, error hold 4, loading and late thinking 30, idle input 15); and in off or calm idle the loop stops completely until an event retargets something. On timer ticks the integrator takes the whole gap in ≤ 1/240 s substeps; the 1/30 s dt clamp applies to live frames. Because its pause is compressed, the preview's paused label rolls about once a second, which shows up as short bursts above 2 fps.

---

## 11. Acceptance checks (M5 must pass)

1. **Idle cost.**
   - 8 h at ready: Task Manager GPU 3D for the process averages < 1 % (target < 0.1 %).
   - `QSG_RENDER_TIMING=1` shows ≤ 8 frames/s in ready, 0 in off, ≤ 2 in paused and 0 in calm idle.
2. **Rest invariant.**
   - A debug assert: within 1 s of entering ready, off or paused, no `Animation`, `Behavior` or `FrameAnimation` is running and no `layer.enabled` is true.
   - It also logs if ready renders above 12 fps for more than 5 s.
3. **Fluid transitions.** Every transition in §6 holds 60 fps with p99 frame time < 16.7 ms (PresentMon). The software backend holds 30 fps.
4. **Wake latency.**
   - The first visible change lands ≤ 16 ms after `woke()`.
   - Mic level to ember radius takes ≤ 50 ms (click test).
5. **State storm.**
   - Random states every 50–300 ms for 120 s, including one-shots and overlays.
   - Expectations: no NaN, no stuck overlay, no label left at partial alpha.
   - Per-frame displacement of the pill edges < 40 px.
   - Thread samples move < 6 px per frame outside audio and one-shots.
6. **Timelines.** Each §5 moment matches its table within ±1 frame in a 60 fps capture. Interrupting at 50, 150 and 300 ms stays continuous.
7. **No focus stealing.**
   - In every state except input, the foreground window and caret are unchanged (`GetForegroundWindow` before and after).
   - Input takes focus at 0 ms and returns it on submit or Esc.
8. **Click-through.**
   - Outside the pill rect (+2 px) clicks reach the window below, in every state and mid-morph.
   - No native window resize happens during morphs.
9. **Calm.**
   - With Windows animations off, the app starts in calm.
   - In calm, ready, off and paused render 0 frames between events.
   - No property overshoots its target.
10. **Distinguishability.** In a greyscale screenshot at 50 % scale, these pairs are told apart by shape alone:
    - ready vs off (slash)
    - off vs paused (arc)
    - listening vs holding (brackets)
    - thinking vs speaking (loops vs wave)
    - error (underline)
    - done (check)
11. **Determinism.** With a fixed seed and clock, golden frames for all states are byte-stable.
12. **Controls.**
    - In thinking and speaking the mic and stop buttons are visible, hit-testable (+3 px) and never overlap the text.
    - Mic goes to woke within one frame and the mic opens; stop and Esc stop TTS within one frame and exhale to the resting state.
    - Neither button steals focus (check 7).

---

## 12. Milestones (M5 split)

The user asked to add this in milestones. Each step is shippable on its own.

**M5a: QML shell and motion lab**
- Scope:
  - Fixed transparent window, mask, the `IslandModel` bridge.
  - The JS integrator (springs with direction presets, followers, tweens, guards).
  - The label crossfader.
  - Today's visuals ported.
  - A **motion lab** window that mirrors `island-motion-preview.html`: state buttons, a chaos button, fake mic and TTS, a calm toggle and an fps readout.
- *Done when:* every current state renders, ready ≤ 8 fps and off 0 fps, and the state storm passes on plain shapes.

**M5b: Ember at rest**
- Scope:
  - `island.frag` (pill, rim, glow, ember) plus the software fallback.
  - ready breath and drowse; off banking and rekindle with the 2 px slash; paused pilot, arc, digit roll and pre-warm; loading kindling.
  - Driver tiers, the suspend logic and the rest invariant assert.
  - The `motion` setting, including the Windows toggle.
- *Done when:* checks 1, 2 and 9 pass.

**M5c: The thread**
- Scope:
  - Thread generators and snapshot blending, with the packed uniforms.
  - Listening (flowing in), holding (brackets, grip, taut), input (baseline, caret, fed to the ember), followup (draining wash), thinking (cursive, patience), speaking (flowing out, ripples, hot word).
  - The mic and stop buttons on thinking and speaking, with barge-in and Esc.
- *Done when:* checks 3 and 4 pass with live mic and TTS.

**M5d: Signature moments and overlays**
- Scope:
  - Woke catch, task done check (satellites, hover list stagger), error flinch and underline, mismatch shrug, badge roll with cool or flare.
  - The interruption rules 1–12, including the flood guard and `finish()`.
- *Done when:* checks 5, 6, 7 and 10 pass.

**M5e: Polish gate**
- Scope: battery and slow-GPU downgrade, golden frames (check 11), an 8 h soak, and before/after screenshots for the README.
- *Done when:* the §10 table is measured and met.

---

## 13. As built (M5, 2026-11-09)

All of M5a–M5e shipped in one change. Where the build differs from the plan above, and why:

- **Live tier driver.** A running `FrameAnimation` makes Qt render every vsync, which is 144 frames a second on a 144 Hz laptop panel even when nothing changed. The live tier is therefore a 16 ms `Timer` (about 60 fps on any panel). The rest tiers are `Timer`s as planned, and calm idle stops completely.
- **Engine.** `ui/island/engine.js` is a port of the preview's integrator. It covers springs with direction presets, followers, tweens, labels, the 48-sample thread with snapshot blending, one-shots and driver tiers. Retargets made inside a frame don't wake the loop (the preview's `inFrame` rule), so ready stays at 8 fps.
- **Shapes.** The mic glyph is a once-painted Canvas: a `CurveRenderer` Shape there cost about 60 % of a core per frame. The slash, arc, brackets, check and timer ring stay `Shape`s, because they change only on entry.
- **Nib reveal.** A clip edge instead of the slanted `ShaderEffect`, so no `layer.enabled` appears anywhere.
- **Hot word.** One `StyledText` per line (newest two lines) with the newest word's colour cooling. There are no TTS word timings, so words appear about 3.3 per second while the voice sounds, and all at once when the text is complete.
- **Holding-muted (Ctrl+Shift).** The brackets turn dim, the ember turns ash with the slash, and the thread lies flat.
- **Window.** 600 × 400, so cards fit under the head. The click-through mask is the pill + 2 px, grown to the union while it morphs. When hidden-when-idle, the mask is a 220 × 4 px strip at the top centre.
