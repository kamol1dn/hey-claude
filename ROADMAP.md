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

## M2b: Train the wake word inside Settings, locally

**Goal:** anyone setting hey-claude up trains their own wake word from
Settings in about 15 minutes, with no Colab, terminal or WSL. The M2 Colab
route stays in the docs as the "advanced, best quality" option.

Settings → **Wake word** wizard:
1. **Phrase:** "hey claude" by default. Any short phrase works, so another user
   can pick their own; the wizard warns about phrases that are too short or
   too common.
2. **Record:** the M2 recorder becomes a Qt dialog. About 25 guided clips
   (normal, quiet, loud, fast, far away), with a live level meter, a
   too-quiet / clipped check and a redo button.
3. **Room sound (optional, 1 min):** talk normally or leave the room noise
   running, as negatives from the user's own environment.
4. **Train:** one button with a progress bar and an honest time estimate. It
   runs on the GPU when `sysload.py` says it's free (< ~70% busy), otherwise on
   the CPU.
5. **Test and apply:** say it a few times to see live hits. It picks the
   threshold from held-out clips and negatives, and you switch to the new model
   with one click (the old one is kept for undo).

Local pipeline, no WSL (lighter than the full openWakeWord recipe):
- **Positives:**
  - the user's clips;
  - synthetic clips of the phrase spoken by many local TTS voices: Kokoro
    from M4 (many voices, speeds and pitches), plus Piper voices if they install
    cleanly on Windows;
  - the M2 phonetic spellings;
  - augmentation: noise, room reverb, speed and gain.
- **Negatives:**
  - look-alike phrases synthesised the same way ("hey cloud", "hey clyde"…);
  - the room recording;
  - a one-time download of openWakeWord's precomputed negative features.
    Pick the smallest set that meets the target and measure it, since the
    network here is slow (~0.5–1 MB/s). Show the size and resume support.
- **Model:** openWakeWord's frozen melspectrogram and embedding models (already
  installed), plus a small classifier head trained on the embeddings with
  torch or plain numpy. That takes minutes, not hours. Export to ONNX in
  openWakeWord's format, so `listen.py` loads it unchanged.
- **Improve over time (optional):** after a false wake, a "that wasn't me"
  click on the island saves those ~2 s as a negative. Retraining from Settings
  folds them in.

**Touches:** new `wake_train/` (synth, augment, features, train, export),
`settings_ui.py` (wizard), `wake_samples.py` (Qt recorder), `tools/wake_eval.py`
(reused for the threshold), first-run downloads.
**Done when:**
- a fresh user goes from Settings → Wake word to a working model in ≤ 20
  minutes on this PC (downloads excluded), without a terminal;
- `wake_eval.py` shows ≥ 95% hits on held-out clips;
- ≤ 1 false wake per 2 h of lecture audio. This is allowed to be a bit worse
  than the Colab model's < 1 per 3 h; state the measured gap in the README.

## M3: Conversation, follow-ups and memory ✅ built (2026-11-09)

**Status:** built and verified with simulated clips; see README
"Conversations and sessions". `heyclaude/conversation.py` keeps one
transcript for all tiers. Each warm process gets the turns the other tiers
answered since it last spoke, and a task gets the whole recent conversation.
It is capped by turns and tokens and resets after 10 min idle or on "start
fresh". The follow-up window (5 s) opens once M2's speaking gate is clear,
including the tail. "Thanks" or silence ends it, and the island shows a
draining ring. "Save this conversation" (off by default) writes
`state/conversations/<date>.jsonl` and names the agent session per day. Voice
task control covers "what did the X task change?" (forked headless `--resume`,
read-only tools), "stop the task" and "resume the last task in a terminal".
`Assistant.request()` is the one entry point for spoken and typed requests.
26 new offline tests (`tests/test_conversation.py`) pass, and so do the 75
existing ones (including the typed-input tests from #3, whose `submit_text()` now goes through `request()`). Live check: `--simulate` with six clips. Turn 1 (quick)
answered "Canberra". Turn 2 ("what's the weather there?", agent) searched for
Canberra. Turn 3 ("what is that in Fahrenheit?", quick) answered from the
agent's reply. Turn 4 ("start a task in sandbox: … for a day in that city")
wrote a Canberra packing list. Turn 5 ("what did the sandbox task change?")
was answered from the task's session. Then "thanks, that's all" closed the
window. Found and fixed on the way: whisper large-v3 dropped "Start a task in
X," because the hint prompt was phrased the same way. **Not done:** a check
with a real mic and room echo. The agent's web weather lookup found no live
reading (a WebSearch result issue, not context).

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

## M4: Voice quality and speed ✅ built (2026-11-09)

**Status:** built and measured with simulated clips; see README "Voice" and
"Measured latency". **Quick question, speech end → first word: median
2.36 s** over 18 requests (p90 3.08 s) with large-v3-turbo, haiku on the warm
CLI, Kokoro on the GPU (target 2.5 s). With large-v3 it's 2.62 s, so turbo is
now the default `stt.gpu_model`. Kokoro (kokoro-onnx + onnxruntime-gpu 1.31,
CUDA 13 + cuDNN 9 from the `nvidia-cudnn-cu13` wheel; espeak-ng comes bundled,
no system install) runs on the GPU with the per-reply load rule shared with
whisper, falling back to the CPU and then SAPI, with the reason on the island.
hey-claude's own GPU work no longer counts as "GPU busy" (it pushed requests to
the CPU). Silero VAD with an end of turn that transcribes at each pause: a
finished sentence ends the turn after 300 ms, a trailing "and/the/um" waits
1.2 s, and the pause transcript is reused, so speech-to-text costs ~0 after you
stop. Partial transcripts on the island while you talk. WebRTC AEC3 (livekit)
cancels the voice from the mic (36 dB on a synthetic echo path) and barge-in
stops the voice after 200 ms of your speech. The direct API path reads its key
from `ANTHROPIC_API_KEY` or `state/anthropic-api-key.txt`; every `claude`
process gets `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` stripped. 35 new
offline tests (`tests/test_voice.py`); the 101 existing ones still pass.
openWakeWord still loads and fires (score 0.998) on onnxruntime-gpu.
Dropped: a GPU rewrite of Kokoro's STFT (~0.1 s/sentence faster) made the
voice buzz. **Not done:** the accent comparison of turbo vs large-v3 needs the
user's recordings (`tools/stt_eval.py --record`; the numbers so far are on
native-accent Kokoro clips); barge-in and AEC with a real mic and speakers.

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

## M4b (optional): Wake only on the user's voice

**Goal:** a "hey claude" from anyone else (a roommate, a video, a classmate on a
call) doesn't wake it. It's **off by default**; build it only if the user asks.

Training the wake word (M2) doesn't do this: the model detects the phrase,
not the speaker, because it's trained mostly on synthetic voices. This
milestone adds a second check after the wake word fires.

- **Speaker check:** embed the wake audio (~1 s) with a speaker model on the CPU
  (SpeechBrain ECAPA or Resemblyzer, ~50 ms) and compare it with the user's
  voiceprint (cosine similarity). Below the threshold, ignore the wake.
  - Compare against openWakeWord's built-in custom verifier (lighter, but
    less strict) and pick one with numbers.
- **Enrolment** reuses M2's recorder clips in `state/wake_samples/`
  (git-ignored). The voiceprint lives in `state/` too, never in the repo.
  "Re-enrol my voice" goes in the tray/settings.
- **Settings:** `wake.only_my_voice: false` (default), `wake.voice_match`
  threshold. The island shows "voice didn't match" briefly, so a rejected
  wake isn't a mystery.
- **Applies to the wake word only.** The hotkey and clicking the island skip
  the check. Follow-ups inside an M3 conversation window also skip it.
- **It's a convenience, not security.** A recording or a similar voice can
  pass, so task permission approvals (M7) never rely on it.
- **Eval:** extend `tools/wake_eval.py` with the false-accept rate on other
  speakers (LibriSpeech/VoxCeleb clips or lecture audio of other voices) and
  the false-reject rate on the user's held-out clips.

**Touches:** `listen.py` (post-wake hook), new `speaker.py`, `tools/wake_eval.py`, settings.
**Done when:** ≤ 5% false rejects on the user's held-out clips and ≤ 2% false
accepts on other speakers at the chosen threshold, with the added wake
latency ≤ 100 ms.

## M4c: Speech backends: local, lean, Windows, cloud

**Goal:** the user can choose how much RAM/VRAM speech uses, down to almost
none, from a tray switch. Build after M4 (it reworks `stt.py`/`tts.py`).

The Claude desktop app's own voice is **out of scope**. It runs on a private
Anthropic service with no public API, and using it would mean lifting the
user's claude.ai login token and calling undocumented endpoints. That likely
breaks the terms, puts the account at risk, and could break on any app
update.

- **One setting, `speech.backend: local | windows | cloud`**, covering both
  speech-to-text and voice output (`speech.stt_backend` / `speech.tts_backend`
  to mix them, e.g. Windows STT + Kokoro). Switch from the tray without a
  restart. The island shows which backend is active.
- **`local` + lean mode** (today's faster-whisper/Kokoro):
  - `speech.lean: true` unloads Whisper and Kokoro after
    `speech.unload_after_min` idle (default 10), and reloads them on the next
    wake.
  - The wake word stays loaded; it's ~20 MB on the CPU.
  - Show "loading…" on the island during a reload. Measure the cold-start
    cost per model (turbo GPU, large-v3 GPU, CPU fallback) in the README.
  - A tray item "Free memory now" unloads immediately.
- **`windows`: no models, free.**
  - STT: the WinRT `Windows.Media.SpeechRecognition` API with online
    dictation, through the `winrt-*` Python packages. It's multilingual and
    runs in Microsoft's cloud. It needs Settings → Privacy → Speech →
    "Online speech recognition" on; detect that and tell the user if it's off.
  - TTS: Windows natural/OneCore voices (`Windows.Media.SpeechSynthesis`),
    with SAPI as the fallback.
  - Check that the hotkey talk-or-type and the hold-Ctrl extension still
    work: hey-claude keeps its own mic capture and VAD, and passes the audio
    to WinRT.
- **`cloud`: best accuracy for little memory.**
  - STT providers behind one interface: Groq (hosted Whisper large-v3-turbo,
    has a free tier) and OpenAI transcription. Keys come from env vars
    (`GROQ_API_KEY` / `OPENAI_API_KEY`) or the Windows credential store,
    never from `config.yaml`.
  - Optional cloud TTS (OpenAI or ElevenLabs).
  - Show the estimated monthly cost in settings from usage in
    `state/latency.csv`.
  - Audio leaves the machine in this mode only. Say so in settings.
- **Auto fallback:** if the chosen backend fails (no network, missing key,
  WinRT error), use the next one in `speech.fallback_order` and say why on
  the island.
- **Load-aware option:** `speech.backend: auto` picks local while VRAM/RAM
  are free, and windows/cloud when free memory is under thresholds
  (`sysload.py`).

**Touches:** `stt.py`, `tts.py` → a `speech/` package with one module per
backend, `tray.py`, `settings_ui.py`, `config.example.yaml`, README.
**Done when:** each backend answers a simulated question; lean mode frees the
VRAM (verified with NVML) and reloads on the next wake; switching backends
from the tray works without a restart; offline tests cover backend selection
and fallback with every backend mocked.

## M4d: Dictate anywhere

**Goal:** hold a key, speak, and let go: the words are typed wherever the cursor
is, in any app. Same idea as Coucou's "Flow" dictation (Mac only there), built
on the mic, Whisper and hold-key code hey-claude already has. Build it after
M4, which changes `stt.py`/`listen.py`.

- **Key:** `dictate.key`, a hold key separate from the hold-to-keep-listening
  Ctrl, e.g. `right alt` or `capslock`. A double tap means hands-free until the
  next tap; Esc cancels. It never swallows the key outside dictation.
- **Paste:** put the text on the clipboard, send Ctrl+V to the focused window,
  then put the user's clipboard back. A "paste last dictation again" hotkey.
  Skip password fields where Windows says so (UI Automation `IsPassword`).
- **Personal dictionary:** names, course names and words Whisper gets wrong
  (Phoenix, eClass, classmates' names), used as Whisper's initial prompt.
  Also replacements ("hey cloud" → "hey Claude") and snippets (a short
  spoken phrase → a saved text). The same dictionary helps the assistant's
  own speech-to-text. Optional `stt.languages` limit (e.g. en/ru/uz) for
  language detection.
- **Optional clean-up by Claude** on the user's subscription via the CLI:
  "as said", "natural" or "polished", chosen per app (e.g. polished in
  Outlook, as said in the terminal). Off by default: it adds latency.
- The island shows a small "dictating" state with the live level. Local
  stats only (words, time saved).

**Touches:** new `dictate.py`, `holdkey.py`, `stt.py` (initial prompt /
languages), settings, island state.
**Done when:** dictation pastes into Notepad, a browser text box and a
terminal; the clipboard is restored; it measures ≤ 1 s from releasing the key
to the text appearing for a 5 s sentence on turbo; and the dictionary fixes
the user's top misheard words in a simulated test.

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
- **Motion:** implement `docs/island-motion.md` (state-by-state motion spec
  with springs, timings, reduced motion and perf budget; approved through the
  preview page `docs/island-motion-preview.html`).
- **Telegram widgets and motion** (the Telegram MCP is connected to the tool
  agent from 2026-11-09, read-only):
  - `telegram_chats`: the chats that need attention, with avatar or initials,
    unread badge, last-message snippet and time. Rows slide in one by one;
    unread badges pop.
  - `telegram_message`: one message or a short thread, with the sender and text,
    and replies indented. Long text is clamped, and a click opens the chat in
    Telegram Desktop (`tg://` link).
  - `telegram_draft` + **sending with confirmation**: the agent drafts a reply,
    the island shows it with **Send / Edit / Cancel**, and only a click or a
    clear spoken "send it" makes the call. Then a paper-plane fly-out
    animation and a ✓. Send/forward/edit/delete stay off the agent's allowed
    tools until this approval exists (M7's approval plumbing).
  - Reading animation: while it scans chats, a quick shuffle of chat bubbles
    behind the Telegram icon.
  - Avatars come through `telegram_download_media` (profile photos), cached in
    `state/icons/telegram/` (git-ignored, never committed).
  - Privacy: message text is shown only while it's the answer, and fades
    after the island collapses. Nothing goes into logs beyond the tool name.
- **Show the app or service being used, with its icon.** While the tool agent
  (or a watched session, M7) calls a tool, the island shows e.g. "Google
  Calendar · checking today" with the Calendar icon.
  - Tool names come from the stream-json `tool_use` events
    (`mcp__<server>__<tool>`) and the M7 hook payloads. Map the server to a
    friendly name: claude.ai connectors, local MCPs such as Telegram, and
    built-in tools (Bash, Edit, WebSearch) get their own small glyphs.
  - Icon sources, in order:
    1. icons the MCP server declares itself (the MCP spec's `icons` on the
       server/tool metadata);
    2. a bundled set of common service logos as SVG (Simple Icons, CC0),
       shown only to name the service in use;
    3. the service's favicon, fetched once and cached under `state/icons/`;
    4. a neutral plug glyph with the service's initials.
  - Settings: icons on/off, and a per-server override (pick any image).
  - Several tools in a row → the icons slide through like a ticker; parallel
    calls → stacked icons.
- Ideas taken from Coucou (ideas only; no names, character, icons or sounds,
  which their `LICENSE-ASSETS.md` keeps):
  - **Drop a file on the island** to ask about it. The file goes to the tool
    agent as an attachment or path.
  - **Hide when idle**, as an option: nothing on screen in `ready`. It peeks out
    when the pointer touches the top-centre edge, and always shows for
    listening, answers and tasks.
  - **Plan-usage pill:** the 5-hour and weekly Claude plan limits. Only from a
    supported source, such as what Claude Code itself reports; skip it if it
    would need private endpoints.

**Touches:** `island.py` → `ui/` (QML), `brain.py` prompts (widget field).
**Done when:** each widget type renders from a simulated reply; 60 fps
transitions measured; no focus stealing.

## M6: Characters as plug-ins (pick one in Settings, add new ones by PR)

**Goal:** the character is a swappable pack behind one interface. All five (Echo, Cinder, Wren, Tycho, Hum)
original designs in `docs/character-options.html` ship as built-in packs, the
user picks one in Settings, and anyone can add a new pack with a PR. The
interface grows as hey-claude gains features, without breaking older packs.

**The interface: one versioned state registry** (`ui/states.json`), shared by
characters and island themes (M5's Ember & Thread is the first island theme):
- Each entry has an `id`, a description, the live inputs it gets (mic `level`,
  TTS `amplitude`, `progress`, `count`, `mood`, cursor position, theme colours),
  `oneshot` or `loop`, and a **`fallback`** state.
- Core states every pack must implement: `idle`, `off`, `listening`,
  `thinking`, `talking`.
- Everything else is optional and falls back, so adding a new state never
  breaks an old pack:
  - `woke` → `listening`
  - `holding` → `listening`
  - `holding_muted` (Ctrl+Shift) → `off`
  - `followup` → `listening`
  - `loading` → `thinking`
  - `paused` → `off`
  - `working` (task) → `thinking`
  - `done` → `happy` → `idle`
  - `error` → `confused` → `idle`
  - `voice_mismatch` → `confused`
  - `sending` (Telegram etc.) → `working`
  - `dictating` (M4d) → `listening`
  - `approval_needed` (M7) → `confused`
- New feature = add a registry entry with a sensible fallback, bump the
  registry's minor version, and optionally update the built-in packs.

**A pack** is a folder `characters/<id>/`:
- `character.json`: id, name, author, licence, `registry` version supported,
  the list of implemented states, size hints (in-pill ~24 px, desktop ~120 px),
  and optional settings (e.g. colour variants).
- `Character.qml`: implements the QML interface: properties `state`, `level`,
  `amplitude`, `progress`, `mood`, `size`, `reducedMotion`, and a
  `finished(state)` signal for one-shots. Pure QML/Shapes/ShaderEffect, no
  native code, no network.
- `preview.gif` or a generated preview for the picker.

**App side:**
- A pack loader with state resolution through fallbacks, logged once per
  missing state.
- **Settings → Character:** a gallery of the installed packs, a live preview
  with a button per state, on/off, size, and in-island or desktop companion.
- The model's `mood` field (fixed list) and app events (task, tool, Telegram
  send) map onto registry states.
- User packs can also live in `state/characters/` (git-ignored) for local
  experiments without a PR.

**Contributing** (`characters/README.md`):
- How to make a pack and test it with `tools/character_lab.py`, which cycles
  every state, fakes the inputs and measures fps.
- CI validates every pack:
  - the manifest schema;
  - the core states are present;
  - each state renders offscreen without QML errors;
  - an idle budget: no continuous animation above the registry's idle fps;
  - originality: no known mascots or logos (reviewed by hand in the PR
    template).

**Port the five designs** from the HTML gallery to QML packs, keeping their
motion and character.

**Touches:** new `characters/` (5 packs + README), `ui/states.json`,
`ui/CharacterHost.qml`, `settings_ui.py` (picker), `brain.py` (mood field),
`tools/character_lab.py`, CI.
**Done when:**
- all five packs pass the CI checks;
- the picker switches packs live;
- a test pack that implements only the five core states runs every app state
  through fallbacks;
- adding a new state to the registry needs no change to any existing pack.

## M7: Tasks v2

**Goal:** voice-started work you can supervise and trust.

- **Watch every Claude Code session on this PC through hooks**, not just the
  ones hey-claude starts. This is the approach of [Coucou](https://github.com/Louis-CFM/coucou)
  (MIT code; read `windows/hook` and `docs/AGENTS.md`, and keep their MIT
  notice on any code copied).
  - Plumbing: Claude Code hooks in the user's global `~/.claude/settings.json`
    call a tiny relay (`heyclaude-hook`), which forwards the hook JSON to
    hey-claude over a per-user named pipe (`\\.\pipe\hey-claude-<user SID>`, only
    that user can connect).
  - Events: SessionStart, UserPromptSubmit, PreToolUse/PostToolUse, Stop,
    Notification. The island ticker shows what each session is doing (tool,
    file, +N −M lines for edits; click for the diff), with a one-shot "done"
    moment when a session stops.
  - Check whether the Claude desktop app's sessions fire the same user hooks.
    If they do, they show up too.
  - Installing the hooks is a persistent change to the user's settings: ask
    first, merge into the existing hooks (never overwrite), and give an
    uninstall in Settings.
- **Approve from the island:** the `PermissionRequest` hook blocks while the
  island shows **Allow / Deny / Always** (click or voice: "yes, allow that").
  - It times out after a configurable wait and falls back to the terminal's own
    prompt, so nothing hangs while the user is away.
  - `AskUserQuestion` choices show on the island too.
  - Keep `--permission-prompt-tool` (a tiny MCP server) only as a fallback for
    headless tasks if hooks can't cover them.
  - Voice approvals are a convenience: any command flagged as dangerous needs a
    click, never voice alone.
- **Jump to the session:** open the terminal window or the desktop-app session
  the event came from.
- Optional git worktree per task, so tasks never touch your working tree;
  "merge it" by voice after review.
- Windows toast notifications, and an optional Telegram message when a task
  finishes.
- Task dashboard window: history, cost, diff preview, open in terminal, and
  try "open in the Claude desktop app" (`claude --resume <id> --desktop`).

**Touches:** `tasks.py`, new `hookrelay/` (relay + pipe server),
`permissions_mcp.py` (fallback only), `ui/`.
**Done when:**
- a task that needs a non-allowed tool pauses, asks on the island, and
  continues after a spoken "yes";
- a Claude Code session the user started in a terminal shows live on the
  island and can be approved from it;
- removing the hooks from Settings leaves `~/.claude/settings.json` exactly as
  it was before.

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
