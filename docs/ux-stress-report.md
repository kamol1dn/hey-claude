# UX stress test: what made the back-and-forth janky, and what changed

*2026-11-09/10. Harness: `tools/ux_stress.py` (+ `heyclaude/ux_probe.py`, `tools/ux_offline.py`, 26 scenarios in `tools/ux_scenarios/`). The live re-check of 2026-11-10 (PR #15's open items, M10 and weather-mcp) is in its own section below.*

## How it was measured

The real app ran in the harness's process (`heyclaude.__main__.main(..., harness=...)`):
- the `claude` CLI tiers, the warm tool agent with MCP (calendar, Telegram read-only, Windows settings, weather);
- Kokoro on the GPU, whisper turbo on the GPU, Silero, AEC with barge-in;
- the QML island.

Only the microphone was simulated. A `SimRoom` (an extension of `--simulate`'s `WavSource`, live mode) "hears", in real time:
- your own wake clips from `state/wake_samples`. Your model fires on 22/24 of them; the 2 it misses were left out, so a miss isn't counted as jank. It also fires on every Kokoro "hey claude" tried.
- questions in five Kokoro voices, with realistic gaps, pauses and background noise;
- the echo of the assistant's own voice (gain 0.15, 60 ms).

The hotkey, hold (Ctrl), Ctrl+Shift mute, Esc, typing and the island's buttons are driven where their OS hooks call into the app. Key and mouse events go to the island's window only, never to other apps.

Every Bridge signal, engine command and island model signal was recorded, plus:
- the island sampled about 30 times a second (state, target size, label, words revealed, card, answer row);
- audio as it plays (the player callback);
- engine tick pacing and presented frames;
- focus;
- window grabs at 12 fps, made into a contact sheet and GIF per run under the git-ignored `state/ux/runs/`.

Ten detectors score each turn: expand/collapse, idle mid-turn, text revealed twice, card removed early, text/audio sync, overlapping voices, dead air over 1.5 s, layout jumps, pixel jitter, dropped frames. They also report stage latency (end of speech → first visual, first audio, card, answer).

**Runs:**
- **Baseline:** `main` 39493f1 (PR #13), all 18 scenarios twice = 36 runs. 5 were dropped as harness artifacts (see the end), leaving 31 scored.
- **After-fix pass:** stopped after 13 scored runs by a safety stop: the agent *asked* for PowerShell and was refused. Some later fixes were made during or after that pass; they are verified offline (below) but not yet live.
- **Offline end-to-end:** `tests/test_ux_stress.py`, now in CI. The real Assistant, Speaker, player callback and QML island, with a scripted brain. Quick, weather, volume + undo and a 3-turn follow-up chain all run with **zero findings**.

Both live runs are re-scored with the same final detectors (`ux_stress.py report`).

## Before → after, per run

| finding (worst feel first) | baseline (31 runs) | after (13 runs) |
|---|---|---|
| idle mid-turn | 2 | 0 * |
| text revealed twice | 2 | 0 * |
| overlapping voices (talked over you) | 2 | 0 * |
| panel opened/closed more than once | 2 | 0 * |
| pixel jitter (pill/card shifting sideways) | **27** (up to 16 px) | **0** |
| words out of step with the voice | 4 | 1 † |
| dead air > 1.5 s | 16 (median 4.2 s) | 7 (median 3.0 s) ‡ |
| layout jump (several resizes in one stage) | 7 | 3 † |
| card removed early / took focus / dropped frames | 0 / 0 / 0 | 0 / 0 / 0 |

\* All 2 baseline cases are in `task_then_question`, which the stopped after-fix pass didn't reach. These fixes are verified by the offline end-to-end run and unit tests; a live re-run is pending your go.
† The remaining cases are the reveal lead and the hover-list pop, both fixed after that pass started (see 9 and 13).
‡ What's left is model/tool latency. The late filler added afterwards (see 6) isn't in that pass.

## Findings, ranked by how janky they feel

### 1. The island drops to idle in the middle of an answer, then starts the answer over (severity 5)

**Scenario:** `task_then_question`, 2/2 runs. Asking about a task ("what did the scratch task do?").

**Timeline (baseline):**
```
89.75 turn        "What did the scratch task do?"
90.04 audio       "Let me check."   0.96 s
90.04 go speaking "Let me check."          <- the filler shown as the answer
91.02 go ready                             <- idle for 8.5 s while the task's session answers
99.58 audio       "I don't have any record of ..."
99.59 go speaking "Let me check. I don't have ..."   <- the answer line restarts, filler glued on
```

**Root cause:**
- `_ask_task` said the filler with `_say()`, so it became answer text;
- when a sentence finishes and the next one hasn't arrived yet, the speaker goes idle, and `_tts_idle` called `rest()` while the request was still running.

The same idle happens whenever the voice catches up with a slow answer.

**Fix (`assistant.py`):**
- `_tts_idle` never rests while the request that is speaking is still running; `converse()` settles it when the answer is complete;
- the task question's filler goes through the filler path, so it's a status line, never answer text;
- the engine carries on from the words already shown if "speaking" is entered again with the same answer.

### 2. The answer is shown twice and the panel jumps at "go on" (severity 4)

**Scenario:** every spoken answer with the question panel up. Contact sheet `quick-1`, 14.5 s:
- the answer fades out of the two-line head (the thread is drawn across the words);
- the same text fades in below the question;
- the panel resizes from a 60 px head to a 40 px head plus a three-line row.

The two-line head also pushed text against the pill's top edge and in under the mic/stop buttons ("words got past border").

**Root cause:** by design (PR #13/#14), the answer lived in the head while it was spoken and moved into a row under the question afterwards.

**Fix (`engine.js` `renderRow`, `Extras.qml`), as you chose:**
- **One place for the answer.** It's revealed in step with the voice in the row under the question and stays in that row; the head keeps the wave and mic/stop.
- **Height reserved up front.** Words not said yet are laid out but transparent, so the row's height only changes when more text arrives, never as words appear.
- **Never truncated.** The row uses all the room the window has (PR #14's goal), follows the newest line as it's spoken, scrolling by a line, and can be scrolled back with the wheel.

### 3. The pill and its content shift sideways by a few pixels (severity 3, 27/31 runs)

This is what you saw at the start of an answer and in the weather card's hourly row. The detector found the width spring overshooting its target by up to **16 px** (thinking → speaking, a card arriving, barge-in), plus the centred position flipping between two rounded values.

**Root causes:**
- the width used the "inhale" spring (damping 0.7, overshoots about 5%), and every resize moved the pill's left edge (and the text anchored to it) past its target and back;
- `pillX = round((600 − w)/2)` flips by one pixel as `w` settles;
- the card was laid out at the *animating* width, so the hourly row re-spaced on every frame.

**Fix:**
- **A turn keeps one width.** 440 px, or the card's width, from the question to the collapse.
- **A no-overshoot "glide" spring** for width, and for height inside a turn. Speed carried over from an earlier bounce is capped so a retarget can't overshoot; the wake catch's bounce now lives only in the ember.
- **Snapped edges.** Targets are rounded, and the drawn size snaps onto its target in the last half pixel.
- **The card holds still.** It's laid out on the target width and held still on screen while the pill morphs around it.

Result: 0 overshoots in 13 live runs.

### 4. A pause mid-sentence splits the request: it answers over you, then starts a second task (severity 4)

**Scenario:** `task_then_question`, 2/2 runs. "Start a task in the scratch project: create a file called haiku.txt…"

**Timeline:**
- after the natural pause at the colon, the end of the utterance fired;
- the assistant answered "Okay, starting that in scratch" for 0.6 s while you were still talking;
- barge-in stopped it, and the rest became its own request;
- **two tasks** ran, and "Your scratch task is done." was announced twice.

**Fix (`assistant.py`):** talking over the answer within 3 s of the end of your request now joins it ("continuation"): the two halves are one request, and a task the first half started is withdrawn quietly.

**Still open:** if the pause falls while it is still *thinking* (no voice to barge into), the rest of the sentence isn't heard. Listening while thinking would cover that, and is worth a follow-up. Holding Ctrl for a long pause works: `hold_pause` heard the whole question in all 3 live runs.

### 5. "Your task is done" is spliced into the current answer (severity 4)

**Root cause:** `_task_finished` set `_speaking_text` and queued the announcement in the middle of whatever was being answered, so the island showed "Your scratch task is done. I explained that tides…" on one line.

**Fix:** announcements (task done, timer done) wait for the answer in progress to finish, then get a turn of their own: the task's prompt in the question row, the announcement as its answer.

### 6. Dead air: seconds of silence with nothing new on screen (severity 3, 16/31 runs)

**Baseline:**
- weather or calendar cards sat as a skeleton for 4–7 s while the agent wrote its sentence;
- short questions to the tool agent had no filler (PR #13 made it long-requests-only), giving 10–12 s of "checking with your tools…";
- settings commands ("set the volume to thirty") never had a filler (6–10 s).

**Fixes:**
- **Cards show at once.** A card shows its content as soon as its data is in: the weather card is the first real answer, up to 5 s before the sentence.
- **The tool label stays.** It stays until the answer starts, instead of flipping back to "Let me check." (that flip read as starting over).
- **A late filler.** A short request, or a settings command, still gets "Let me check." if nothing has been said after 3.5 s (`agent.filler_after_s`); quick answers come well before that.

**Still open:** the agent's own latency, 5–12 s from the end of speech to the answer for tool lookups (the stage-latency tables in the run summaries).

### 7. After the answer the island changes shape, and "go on" sweeps across it (severity 3)

You reported this live.

**Root causes:**
- the follow-up stage re-sized the pill: a different head, the answer row popping in;
- the countdown was a wash sweeping across the whole pill.

**Fix:**
- the turn's size doesn't change from speaking to waiting;
- the countdown is a small ring around the ember, draining clockwise like the paused arc;
- with a panel open there's no label; without one, "go on" stays.

### 8. The panel collapses in two steps, then pops open again (severity 3)

**Root causes:**
- **Card first, answer later.** A card's timer expired first and the answer row stayed for its reading time (PR #14), so the panel shrank, then collapsed later.
- **A stale hover.** With the pointer resting where the big panel had been, the hover state carried into rest, and the task/usage list popped open (460 × 70) for a second.

**Fix:**
- the whole panel goes at once, after the answer's reading time;
- the hover list needs a hover that starts after the panel collapsed.

### 9. Words finish over a second ahead of the voice on long sentences (severity 2, 4/31)

**Root cause:** the reveal ran 10% faster than each sentence's audio, so the last words of an 8 s sentence showed about 1.2 s early.

**Fix:** the lead is now 10% of the sentence, capped at 0.35 s.

### 10. Smaller ones

- **A bare wake word opened a panel.** A repeated "hey claude" was transcribed as the request "Hey Claude.". Now it just keeps listening, with no flash of "thinking".
- **An empty talk-or-type field blocked the wake word indefinitely.** The wake word is off while the field is open, and nothing closed it except a focus change. It now closes after 20 s with nothing said or typed.
- **The question briefly appeared twice.** It showed in the head and in the new question row at once when thinking started; the head now says "…" until the row is up.
- **"SUMMARY:" was read aloud** from a task's output; that label is now dropped.
- **Telegram chat avatars showed a broken "?".** It happened for names that start with an emoji, which took half of a UTF-16 surrogate pair; initials now come from the first real letters.
- **The agent asked for Claude Code's own PowerShell tool.** For "what time is it in Tokyo?", it was refused (not allowed) but cost a turn. The agent is now never offered it (`agent.disallowed_tools`, PowerShell always).

### Not fixed (and why)

- **A wake word said during the filler, or within 0.7 s after it, is lost** (`wake_during_thinking`, 2/2 baseline). The gate that stops the assistant's own voice from waking it mutes the wake word then, and the filler is too short for barge-in's 400 ms grace. Loosening either risks self-triggers, so this needs its own careful change.
- **Model and tool latency** (see 6).
- **Frame pacing was already fine:**
  - no late engine ticks during transitions in any run (worst single late tick: 66 ms, outside a transition);
  - presented frames p50 13.9 ms, p95 21 ms during turns; the ~125 ms p99 is the 8 fps idle tier by design.
- **The island never took focus** except while the talk-or-type field was open, which is by design.

## Live re-check, 2026-11-10 (PR #15's open items, then M10 and weather-mcp)

Two live passes on the dev laptop, with the user's hey-claude quit for each.

### Pass 1: PR #15's fixes on `main` 690c350

Ten runs: `task_then_question`, `long_answer`, `weather_short`, `volume_undo` and `quick`, twice each. Run `state/ux/runs/20261110-004116`. Two subagents were building M10 and weather-mcp on the same machine (CPU ~76%), and Kokoro logged audio underruns in most replies, so the sync and latency numbers are pessimistic.

| item (PR #15) | live result |
|---|---|
| idle mid-turn / answer restarts (`task_then_question`, #1) | **Fixed for answers:** no restart, filler not glued on (2/2 runs). **One new case:** the "your task is done" *announcement* sat as a resting pill under its open question for ~1.5 s while its voice was synthesised (detector: idle_mid_turn). Fixed in #17 (the announcement turn shows thinking until its voice starts). |
| text revealed twice / panel opened twice | 0 / 0 in 10 runs |
| reveal lead (#9) | **Fixed on long answers:** `long_answer` 0/2. The 2 `quick` runs still show words finishing 1.0–1.3 s early, with heavy underruns stretching the audio in both. |
| hover-list pop (#8) | **No stale-hover pop.** The task list still pops by design when a task starts or ends (once with 4 size changes while ready). |
| late filler (#6) | It fires, but late. "Let me check." starts 5.3–6.1 s after the end of speech (3.5 s timer from the route, plus synthesis), so dead air on agent turns is still 4–5 s. Settings commands no longer use this path (M10 below). |
| talked over at a mid-sentence pause (#4) | Joined into one request as designed, but the assistant still talked for 1.4 s before barge-in stopped it (1/2 runs). |
| **harness bug found** | The harness's tool-result hook rejected the agent's new `raw` argument. In pass 1 that meant no effects, no weather card and no result audit. Probably the same held in PR #15's after-fix pass too: `raw` arrived with PR #14, which was merged during that run. The summary also printed "audited tool calls: 0" because the list was cleared per scenario; tool starts *were* audited per scenario and none was outside the safe list. Both are fixed. |

### Pass 2: M10 + weather-mcp on `main` 14d34e5, interrupted by two crashes

Live, instant commands (`state/ux/runs/20261110-020113`; speech end = the end of the simulated voice):

| turn | speech end → effect on the island (harness) | app's own log (speech end → effect) | of which end-of-turn wait (VAD tail) |
|---|---|---|---|
| "Turn the volume down a bit." | 0.44 s | 0.63 s | 0.57 s |
| "A bit more." (context) | 0.35 s | 0.53 s | 0.48 s |
| "Mute." | 0.76 s | 0.85 s | 0.80 s |
| "Unmute." | 0.60 s | 0.68 s | 0.64 s |
| "Switch to light mode." | — | 0.57 s | 0.56 s |

- **Median: 0.52 s on the harness clock (0.63 s in the app log).** Matching and running take 5–60 ms; the rest is the end-of-turn VAD. Before this, "set the volume to 30" took 5.7–11.5 s through the agent.
- The ≤ 0.5 s target is met only on the follow-up turn. Getting the rest below it means acting on a confident partial transcript (left open in M10) or a shorter end-of-turn tail for short commands.
- Every expectation passed: route instant, the effect, effect within 1.5 s, both turns.

**Weather-mcp was not reached live:**
- Before (pass 1, agent path, `weather_short-11`): first audio 4.7 s, answer 12.9 s after the end of speech.
- After: the warm local route answered in about 35 ms at text level (card and summary together; median of 5, through weather-mcp's cache) in the subagent's measurement. weather-mcp itself was ready 8.8 s after start, and the home forecast was prefetched 2 s after that.
- Its live scenario (`weather_mcp`) is in the harness, waiting for the next live run.

**Two native crashes stopped pass 2.** In both, the harness process died with no Python error:
1. An illegal instruction in no known module, at a barge-in in `task_then_question`.
2. An access violation on a native thread right after the in-process "light mode". The harness's restore never ran; the colour mode was put back by hand straight away.

What was ruled out:
- The real island cycling states while the colour mode is switched in-process from a worker thread (6 switches) does not crash, and the user's running hey-claude survived the same switches.
- 400 read-only `get_volume` calls from short-lived threads, with the garbage collector running on other threads, did not crash.

What changed anyway, as hardening:
- Every Windows tool that hey-claude runs in its own process (M10's executor, "undo that", album art, the start-up app list) now runs on one persistent COM thread (`mcp_windows._util.com_call`), never on a turn's short-lived thread.
- **This is not shown to fix the crashes.** The next live run (pass 2 again, with `-X faulthandler`) is what tells. It didn't fix them (pass 3).

### Pass 3: the crash found and fixed, the follow-ups measured

All on the trial branch of the re-check follow-ups, with `python -X faulthandler`. Runs `state/ux/runs/20261110-06*`.

**The crash.** It reproduced every time: 4 of 4 runs died within the first three instant commands. Windows' faulthandler also prints first-chance access violations that get handled, so the dumps were mostly noise; the process died on a second fault, on a native thread with no Python frame.

The bisect:
- **Acks off** (`--set instant.ack=effect`): 0 crashes and not even a handled access violation in 6 scenarios.
- **Effects plus 12 fps frame grabs** on the real island, offline: 60 s and 85 effects without a crash.
- **In-process colour-mode switches** with the island busy: no crash.

So the spoken ack was the trigger.

**Cause:** a race in `tts.Player`.
- `Speaker.begin()` opens the output stream on its own thread (`prepare`), while the synth thread's `play()` opens it too.
- `_open()` checked for a stream under the lock, then built the `sd.OutputStream` outside it, so two threads could both build one, and the second overwrote the first.
- The overwritten stream was never closed: PortAudio kept calling its callback while Python freed the object.
- Kokoro's ≥ 100 ms synthesis used to keep the two calls apart. M10's cached acks make `play()` immediate, so the race hit almost every time.
- The `task_then_question` barge-in crash fits too: its short reply ("Okay, starting that in scratch.") goes through the same cache, and the stop and restart reopen the stream.
- Against the old code, a test with a fake 50 ms stream open shows 2 streams built and 1 left running; with the fix, exactly one.

**Fix** (`tts.Player`):
- opening, replacing and closing the stream happen under one lock;
- the idle closer re-checks under it.

Two smaller defects were also fixed:
- **The colour-mode broadcast** passed a temporary string pointer that our own busy windows could read after it was freed. It now uses a process-lifetime buffer with real 64-bit argtypes.
- **In-process Windows tools** run on one lasting COM thread (pass 2's hardening).

**After the fix:** 15 live scenarios with acks on: **0 crashes, 0 access violations**. Every Windows setting was restored (dark mode, volume 26). Brightness was never touched.

| follow-up | before | after (live) |
|---|---|---|
| instant command, speech end → effect | median 0.52 s | **median 0.48 s** over 18 turns, range 0.30–0.69 s (1.16 s for "undo that") |
| weather-mcp, speech end → card / first audio | answer 12.9 s through the agent | **0.44 s / 0.81 s**; follow-up "and tomorrow?" 0.30 s / 0.30 s |
| late filler on an agent turn ("What is the volume of a sphere?") | 5.3–6.1 s | **1.78 s** (1.8 s from the end of speech, pre-synthesised) |
| task dictated with pauses | answered over the second half for 1.4 s | still split at the second pause ("…haiku." / "A haiku about autumn."); the continuation joined it. Now every task start waits `long_silence_ms` (offline-tested, not yet live) |

**Where the instant time goes:**
- A parsed instant command now ends the turn as soon as the pause transcript is in (`listen.instant_end`), not after another 300 ms.
- The end of turn is still about 0.55 s in the app's log: the 160 ms pause probe, Whisper on the pause and Silero's hangover.
- Going further needs acting on a partial transcript taken *while* speaking.

**Two misses found:**
- **"Now the other way."** came out of Whisper as "Know the other way." twice, so it went to the agent. The agent asked for `set_theme`, which the harness's safe list refused. That's now a slip fix, with near-misses ("I know the other way home") staying quiet; not re-checked live yet.
- **The late filler only starts once a turn reaches the agent.** When the quick tier first answers "LOOKUP", its own round trip (about 5 s here) is still silent.

## Harness artifacts (excluded from scoring)

These were bugs in the harness, fixed during the run:
- **`typed`, `rapid_wakes-1`:** the typed field was left open; PySide's `QTest.keyClicks` won't take a `QQuickView`, so real key events are now sent instead.
- **`followup_chain`:** `until followup` matched the follow-up state left over from the previous turn.
- **Volume:** the harness put the volume back after every scenario, which fought you turning it down by hand. It now restores only when the agent changed the volume in that scenario.

## Safety with real connections

- **Agent tools:** cut down to read-only ones plus the volume (`state/ux/config.harness.yaml`). In test runs the agent is never offered PowerShell/Bash.
- **Audit:** every tool call is audited; one attempt (PowerShell) was refused, and no tool outside the safe list ran.
- **Nothing sent or changed:** no message sent, no mail or calendar change.
- **Tasks:** ran only in `state/ux/scratch`.
- **False wakes:** saved under `state/ux/`, never in your training negatives.
- **GPU:** checked below 70% before every scenario; the run paused itself each time it found more.

## Running it

```bash
.venv\Scripts\python tools\ux_stress.py run weather quick -n 3   # live; quit your hey-claude first
.venv\Scripts\python tools\ux_stress.py run --offline            # no GPU/models/login (what CI runs)
.venv\Scripts\python tools\ux_stress.py report state\ux\runs\<run>  # re-score with the current detectors
.venv\Scripts\python tools\ux_compare_gif.py <before-run>\weather-1 <after-run>\weather-1 out.gif
```

Frames, contact sheets and GIFs stay under `state/ux/` (git-ignored): they show your answers, calendar and chats.
