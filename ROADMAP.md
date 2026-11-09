# hey-claude roadmap: prototype → finished product

v1 (this commit) works end to end: wake word, CPU speech-to-text, three tiers
(quick answers, a warm tool agent with your claude.ai connectors, headless
tasks), the island, and spoken task summaries. See README for measured
latency.

Each milestone below is sized for **one Claude Code session in its own
worktree/branch**. That session splits the work across subagents where the
parts are independent, and finishes by merging into `main` with the offline
tests passing and a live `--simulate` run. Each brief is self-contained, so a
fresh session can start from it plus the README.

```
M1 always-on shell ──┬─▶ M2 "hey claude" wake word ──────────────┐
                     ├─▶ M3 conversation & memory ─▶ M7 tasks v2 ─┤
                     ├─▶ M4 voice quality & speed ────────────────┤
                     └─▶ M5 island v2 (animation, widgets) ─▶ M6 character ─┤
                                                                  └─▶ M8 package & ship
```

M2, M3, M4 and M5 can run **at the same time** once M1 is merged. Their files
barely overlap (see "Touches"). M6 needs M5, M7 needs M3, and M8 comes last.

---

## M1: Always-on shell and settings ✅ done (2026-11-09)

**Status:** built and verified; see README "Tray, hotkey and settings".
19 offline tests pass (config round-trip, hotkey parsing, listening/pause,
Premiere guard, crash restart). `--simulate samples\question.wav --exit`
still answers. Scripted live checks: listening off closes the WASAPI capture
session (no mic in use) and "talk now" reopens it for one request; a second
launch exits and pokes the first; the hotkey opens the mic; settings save to
`config.yaml`; Restart now relaunches; `pythonw` runs with output in
`state\logs\app.log`. The Startup shortcut was created and removed again
during testing, and autostart is left **off** until you turn it on. Not
tested: an actual reboot.

Added to M1 on request: the speech-to-text device setting (GPU with
`large-v3-turbo` by default, CPU for a request when the GPU is busy, read
from NVML/psutil per request)
and hold-to-keep-listening (`listen.hold_key`). Kokoro on the GPU was
requested at the same time; it is proposed for M4 (it needs espeak-ng, model files and a GPU onnxruntime or torch).

**Goal:** it runs all day without a terminal and is easy to switch off.

- Tray icon with an on/off **listening toggle** (mic released while off),
  "talk now", "pause for 1 hour", open settings, open logs, quit.
- **Start with Windows** toggle (Startup-folder shortcut to `pythonw`), and
  single-instance guard.
- Global push-to-talk hotkey (configurable, e.g. Ctrl+Alt+Space).
- Settings window (Qt) that edits `config.yaml`: projects (name → folder
  picker), wake threshold, tiers on/off, models, voice, hotkey, permission
  allow-lists, with live reload where possible.
- "Premiere guard": optional auto-pause of `--gpu` features when Premiere is
  running (detect `Adobe Premiere Pro.exe`).
- Logging to `state/logs/app.log` with rotation; crash → restart once.

**Touches:** `__main__.py`, new `tray.py`, `settings_ui.py`, `config.py`;
small hooks in `assistant.py`.
**Done when:** after a reboot it's listening from the tray with no console
window; the toggle frees the mic (check Windows' mic-in-use indicator);
settings changes persist.

## M2: A real "hey claude" wake word ⏳ built, waiting for the trained model (2026-11-09)

**Status:** everything except the model itself is built; see
[docs/train-hey-claude.md](docs/train-hey-claude.md). The sample recorder
(`--record-wake-samples`, tray → Record wake samples…) saves clips to
`state/wake_samples/`, holds every 4th clip out of training, and zips the
rest for Colab. `tools/wake_eval.py` measures the hit rate, false wakes per hour
on any audio/video files (ffmpeg, parallel CPU processes, ~25–75x real time),
and recommends a threshold (`--apply` writes it). The wake word is ignored
while TTS plays, plus `wake.speaking_tail_s` (0.7 s), and the model is reset
when that ends. `wake.model` defaults to `models/hey_claude.onnx`; if that's
missing or broken it falls back to hey_jarvis at 0.5, with the reason in the log and the
island label. 25 new offline tests (`tests/test_wake.py`) pass, and so do the 39 existing ones;
`--simulate samples\question.wav --exit` still answers (on the fallback).
The official openWakeWord Colab notebooks are broken on current Colab, so the
doc uses the patched notebook from openWakeWord PR #359, plus one pasted cell.
Local training would need WSL2, which isn't installed. **Not done yet:**
training (the user runs Colab) and the ≥95% / <1 per 3 h measurement with
`wake_eval.py` on the user's lecture audio.

**Goal:** wake on "hey claude", rarely on anything else.

- Train with openWakeWord's Colab notebook (see `docs/train-hey-claude.md`;
  the user runs the notebook, and the session prepares the config and phonetic
  spellings). Add 20–50 recordings of the user's own voice and accent as
  extra positives (a small in-app "record samples" helper).
- `tools/wake_eval.py`: false triggers per hour on a long negative recording
  (podcast/YouTube/class audio) and hit rate on the user's samples; pick the
  threshold from those numbers.
- Optional: suppress triggers while the assistant itself is speaking.

**Touches:** `listen.py`, `models/`, `tools/`, docs.
**Done when:** ≥95% hit rate on the user's samples and fewer than 1 false
wake per 3 hours of background audio.

## M3: Conversation, follow-ups and memory

**Goal:** talking back and forth feels like one conversation.

- **Follow-up window:** after it answers, keep listening for ~5 s without the
  wake word (island shows it). "Thanks" or silence ends it.
- **One shared conversation across tiers:** keep a rolling transcript and
  give it to whichever tier answers next, so "what's on my calendar?" →
  "and the weather then?" keeps context.
- Optional persistence: a "save this conversation" setting (agent session
  named per day), and a voice command "start fresh".
- Ask about a finished task: "what did the inha app task change?" →
  `claude -p --resume <task session id>` headlessly, answer spoken.
- Voice task control: "stop the task", "resume the last task in a terminal".

**Touches:** `brain.py`, `assistant.py`, `tasks.py`.
**Done when:** a 5-turn mixed conversation (quick → lookup → quick → task →
question about the task) keeps context, verified with simulated clips.

## M4: Voice quality and speed

**Goal:** sounds good and answers in about 2.5 s after you stop talking.

- **Kokoro on the GPU as the default voice** (spec approved by the user,
  2026-11-09; M1 deferred it here because of the installs involved):
  - Config: `tts.engine: kokoro | sapi | piper` (default `kokoro`),
    `tts.device: gpu | cpu` (default `gpu`), `tts.voice` (e.g. `af_heart`),
    `tts.speed`.
  - Load once at startup and keep it resident on the GPU next to Whisper.
  - Fallback chain: Kokoro on GPU → Kokoro on CPU → Windows SAPI. Log the
    reason, and show it on the island.
  - Respect `stt.yield_to_premiere`: while Premiere Pro is running, Kokoro
    uses the CPU.
  - Speak sentence by sentence, so the first sentence plays while the rest
    is still being generated. Keep time-to-first-audio under 300 ms per
    sentence.
  - Pick torch versus `kokoro-onnx` + `onnxruntime-gpu`. Installs to plan
    for: espeak-ng, ~330 MB of model files, and torch-CUDA or
    onnxruntime-gpu. Swapping `onnxruntime` for `onnxruntime-gpu` also
    affects openWakeWord (M2's path), so **check that the wake word still
    runs after the swap**.
  - Tests for the fallback chain with CUDA mocked, and README setup steps.
- Silero VAD with a smarter end-of-turn (shorter silence when the sentence
  is clearly finished).
- Barge-in while speaking: echo cancellation (WebRTC AEC via `webrtc-audio-
  processing` or speexdsp) so it can hear "stop" over its own voice.
- Whisper: evaluate `small.en` int8 vs `base.en` on the user's accent; stream
  partial transcripts to the island.
- Direct Anthropic API path for tier (a) as the recommended low-latency
  option (needs a key), with the CLI path kept as the no-key default. Strip
  `ANTHROPIC_API_KEY` from the environment of every `claude` process
  hey-claude starts. Otherwise the CLI would likely use the key, and the
  lookups and tasks would be billed to the API instead of the subscription.

**Touches:** `tts.py`, `listen.py`, `stt.py`, `requirements.txt`, `config.example.yaml`, small bits of `assistant.py` and `claude_cli.py`.
**Done when:** quick-question speech-end → first word ≤ 2.5 s median on normal
Wi-Fi, logged in `state/latency.csv`.

## M5: Island v2: animation, transitions, response widgets

**Goal:** the island looks and moves like a product, and can show more than text.

- Rebuild the island in **Qt Quick/QML**: spring-animated morphing pill,
  smooth state transitions, a live mic waveform, a "thinking" shimmer, and a
  hover-expand task list.
- **Response widgets:** the answering model can return a widget spec next
  to the speech (`{"widget": "calendar_list", "data": {...}}`). Widget
  registry: weather card, calendar agenda, timer/countdown, task card,
  link/file card, short code snippet, image. Unknown widgets fall back to text.
- The widget contract (JSON schema) lives in one file, and both tiers are
  prompted with it.

**Touches:** `island.py` → `ui/` (QML), `brain.py` prompts (widget field).
**Done when:** each widget type renders from a simulated reply; 60 fps
transitions measured; no focus stealing.

## M6: Claude character

**Goal:** an animated character that reacts to the conversation.

- An **original** character design. Don't copy existing mascots or logos.
  States: idle, listening, thinking, talking (lip-flap synced to TTS
  amplitude), happy, confused, working (task running), done, error.
- Assets as Lottie (QML Lottie) or sprite sheets; a small state machine
  blends between them.
- The model picks a `mood` in its structured reply (from a fixed list); the
  app maps tool/task events to `working` / `done` automatically.
- Settings: character on/off, size, which corner/in-island.

**Touches:** `ui/character/`, a few lines in the reply schema.
**Done when:** every state is reachable from a scripted demo, and moods
switch from model output.

## M7: Tasks v2

**Goal:** voice-started work you can supervise and trust.

- Permission requests surfaced on the island: route prompts via
  `--permission-prompt-tool` (a tiny MCP server inside hey-claude), approve
  or deny by click or voice ("yes, allow that").
- Optional git worktree per task, so tasks never touch your working tree;
  "merge it" by voice after review.
- Windows toast notifications, and an optional Telegram message when a task
  finishes.
- Task dashboard window: history, cost, diff preview, open in terminal, and
  try "open in the Claude desktop app" (`claude --resume <id> --desktop`).

**Touches:** `tasks.py`, new `permissions_mcp.py`, `ui/`.
**Done when:** a task that needs a non-allowed tool pauses, asks on the
island, and continues after a spoken "yes".

## M8: Package and ship

**Goal:** a setup a non-developer can install.

- PyInstaller or Nuitka build with an installer; models downloaded on
  first run.
- First-run wizard: find or install the `claude` CLI, check `claude auth
  status`, mic test, pick projects, choose a voice.
- Auto-update check; uninstall cleanly (Startup entry, state).
- CI: offline tests plus a simulated-audio smoke test on Windows.

**Done when:** a fresh Windows user account goes from installer to "hey claude,
what time is it?" in under 10 minutes without a terminal.
