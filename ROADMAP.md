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

## M2a: Ship one default "hey claude" model for everyone ✅ built, release staged (2026-11-09)

**Status:** trained locally on synthetic voices only (`python -m
heyclaude.wake_train default`, ~27 min on this PC): 96.5% hits on 480 clips
of 8 held-out voices, 0.28 false wakes/h on openWakeWord's 10.7 h validation
set (threshold chosen on that set). First-run download with SHA-256 and the
licence check (CC BY-NC-SA 4.0, models/LICENSE) are in. The release asset is
staged in `dist/wake-model-v1/` and **not published**: the maintainer runs
the `gh release create` command from the PR. **Not measured yet:** real
voices (no donated clips) and the user's lecture audio (`tools/wake_eval.py`).
See README "Wake word".

**v2 (2026-11-09):** Piper's multi-speaker LibriTTS-R voice (904 audiobook
readers, `piper-tts` 1.8) is now a second voice source next to Kokoro (half
of every synthetic set; 45 speakers held out). On the same held-out data:
Kokoro voices 96.5% → 95.2%, Piper speakers 77.5% → 97.5%, 24 real
recordings of one person 50% → 87.5%, false wakes unchanged at 0.28/h.
Staged in `dist/wake-model-v2/`, **not published**. Still to do: real clips
from more people, lecture audio.

**Goal:** new users never train anything. The wake word detects the phrase,
not the person, so one model trained once works for anyone who says "hey
claude".
- Train it **on synthetic voices only**, never the maintainer's clips (no
  personal voice in a public repo, and no bias toward one accent):
  - many Piper/Kokoro voices with a deliberate mix of accents (including
    Russian/Uzbek-style spellings from M2), speeds and pitches;
  - the M2 look-alike negatives.
- Measure it with `wake_eval.py` on held-out *real* clips from several people,
  if volunteers donate some. Their clips are used only for evaluation and are
  never committed.
- Ship `models/hey_claude.onnx` as a GitHub release asset, downloaded on first
  run and checked with SHA-256, and make it the default `wake.model`.
  `hey_jarvis` stays the fallback.
- **Licence check before shipping:** openWakeWord's pretrained models are CC
  BY-NC-SA 4.0 because of their training data. Work out what applies to a
  model trained with the same negative features, and state it in
  `models/LICENSE` and the README.

**Done when:** a fresh install wakes on "hey claude" with no training step,
with ≥ 95% hits on the donated real clips and < 1 false wake per 3 h of
lecture audio.

## M2b: Personal tune-up and custom phrases, inside Settings, locally ✅ built (2026-11-09)

**Status:** Settings → Wake word wizard (phrase, Qt recorder with level meter
and redo, optional room minute, Train with progress and ETA, test, apply with
undo) and the island's "not me". An end-to-end wizard run on this PC trained
in 6.7 min (GPU): 100% on held-out clips, 0.19 false wakes/h on the 10.7 h
validation set (synthetic stand-in clips; a run with the user's own voice is
still to come). Kokoro only so far: piper-tts 1.8 does install on Windows (a
34 MB wheel), but adding it means another voice download and a retrain, and
the targets were met without it. Its multi-speaker LibriTTS voice (~900
speakers) is the obvious next step for robustness to real voices.

**Goal:** optional. Most users keep the M2a default. The wizard is for people
who want it tuned to **their** accent, or a **different phrase**. It runs in
about 15 minutes with no Colab, terminal or WSL. The result stays on that PC
(`state/`), never in the repo. The M2 Colab route stays in the docs as the
"advanced, best quality" option.

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

## M5b: Widget packs

**Goal:** more answers show as a small live card instead of only speech. Each
widget is a plug-in (`widgets/<id>/`: `widget.json` with its data schema and
triggers, plus `Widget.qml`), loaded by M5's widget registry. New ones can come
by PR, with an unknown widget falling back to text. M5 ships the first packs:
weather (keyless Open-Meteo tool, animated conditions), calendar agenda,
timer, task card, link/file, code, image, and the Telegram set.

Next packs, each with its own small animation:
- **Now playing:** album art, progress, controls (M9's media tools).
- **System:** battery, CPU/GPU load and VRAM, with hey-claude's own devices
  shown ("stt: gpu (turbo)").
- **Timetable:** next class, room and time left; reads the user's existing
  timetable data (`D:\coding\inha_timetable`), read-only.
- **Deadlines:** a countdown list from the user's own deadline notes. It's
  personal, so it never goes into the public repo.
- **Unit / currency conversion, quick maths**, with the working shown.
- **Definition / translation** (en / ru / uz).
- **Directions / place:** a map snippet, time and distance.
- **News / sports headlines:** a short list from web search.
- **Reminders and alarms:** a list with countdowns, once timers exist (M9 §4).
- **Email digest:** unread count and the top senders (Gmail connector).
- **Clipboard / screenshot preview** (M9).
- **Design bar:** widgets are as crafted as the motion and character previews
  (`docs/widgets-preview.html` is approved first). The island may grow: the
  pill springs down into a larger rounded panel (up to ~440x280) for a widget,
  with the character and service glyph in its header, then collapses back.
- Rules for every pack:
  - data comes from a tool result, never made up by the model;
  - widgets render under the idle frame-rate budget;
  - private data (deadlines, email, Telegram) fades when the island collapses.

**Done when:** each pack renders from a recorded real tool result, has a
schema test, and passes the M5 frame-budget check.

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

**Idle antics:** when nothing has happened for a while, the character does
little things on its own, and sometimes its surroundings change. Fishing off
the island's edge, getting bored and dozing off, reading, juggling, a
butterfly landing, rain or snow, stars at night. Aim for 10–15 per character,
with no upper limit.
- Antics are **pack-defined and open-ended**, not part of the core registry.
  Each one is declared in `character.json` with:
  - `id`, `duration` and a `weight`;
  - a `cooldown`;
  - `min_idle` (e.g. only after 10 minutes idle);
  - where it can play (in-pill, desktop companion or both);
  - optional conditions: time of day, season, day of week, weather if a
    weather source is on, how long the user has been idle;
  - `props` for environment items (fishing rod, book, rain cloud).
- **Scheduler in the app:**
  - picks a weighted random antic, never the same one twice in a row, and
    respects cooldowns;
  - plays one every few minutes of idle at most (Settings: rare / normal /
    lively / off), each a few seconds to ~20 s;
  - otherwise the character rests at the idle frame rate;
  - sleep progresses naturally: drowsy after ~15 min idle, asleep after ~30,
    and it wakes with a stretch when you come back.
- **Always interruptible:** a wake word, the hotkey, a task event or the mouse
  arriving ends any antic within ~150 ms with a short "caught!" exit (drop the
  rod, blink awake). Antics never delay a real state.
- **Calm all day:** no antics while the user is typing or in a full-screen app
  or game, in calm / reduced-motion mode, on battery saver, or while the island
  is hidden. They play at their own low frame rate and return to 0 fps after.
- **Settings → Character → Idle antics:** on/off, how often, and a checkbox
  per antic with a "play now" preview.
- CI checks every antic for: a declared interrupt exit, the fps budget, a
  maximum duration, and props that stay inside the character's bounds.
- Starter ideas for the five built-ins, which the port should flesh out to
  10–15 each:
  - **Echo:** hangs upside down, catches a moth, echolocation pings, spooked by
    its own echo.
  - **Cinder:** roasts a tiny marshmallow, smoke rings, sputters in the rain.
  - **Wren:** fishing, preening, folds itself into a boat, a paper-airplane
    flight.
  - **Tycho:** a moon eclipse, counts its moons, a comet passes, a telescope
    peek.
  - **Hum:** eyes follow a passing fly, whistles, yawns, a blink sequence, the
    eyes look at the clock.

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
- **Hand serious sessions to the Claude desktop app** ("move it to the
  desktop", or automatically for tasks marked serious). The session keeps
  running there without hey-claude. Remote Control comes from the desktop
  app's "Remote Control for all sessions" preference, so the phone and
  claude.ai/code see it too. Tested on 2026-11-09 with CLI 2.1.317:
  - `claude --desktop --resume <session-id>` opens a CLI/headless session in
    the desktop app under the same id (`local_<session-id>`), and it continues
    with its full context.
  - It **refuses redirected output**, so start it in its own console window
    (`CREATE_NEW_CONSOLE`, minimized); it exits 0 once the app has it.
  - Only hand off between turns: stop or await the current `-p` turn first,
    so two processes never write the same transcript.
  - **User hooks fire inside desktop-app sessions** with the same
    `session_id`: SessionStart on handoff, then UserPromptSubmit / Stop per
    turn. So the hook-based watching above follows the session into the
    desktop app with no extra work.
  - Desktop links: each desktop session has `claude://claude.ai/epitaxy/local_<id>`
    (seen in the app's session list). Opening it from the island jumps to the
    session. Verify that the scheme opens a session from outside the app
    before relying on it.
  - Not used: Remote Control on `-p` runs (undocumented, and needs the
    process alive); `claude --cloud` (works on a GitHub copy, with no
    personal-account way to read its status back). Keep cloud as an explicit
    "run it in the cloud" voice command only.
- Optional git worktree per task, so tasks never touch your working tree;
  "merge it" by voice after review.
- Windows toast notifications, and an optional Telegram message when a task
  finishes.
- Task dashboard window: history, cost, diff preview, open in terminal, and
  try "open in the Claude desktop app" (`claude --resume <id> --desktop`).

Design notes from studying Coucou, plus our handoff test: `docs/m7-hooks-notes.md`.

**Touches:** `tasks.py`, new `hookrelay/` (relay + pipe server),
`permissions_mcp.py` (fallback only), `ui/`.
**Done when:**
- a task that needs a non-allowed tool pauses, asks on the island, and
  continues after a spoken "yes";
- a Claude Code session the user started in a terminal shows live on the
  island and can be approved from it;
- removing the hooks from Settings leaves `~/.claude/settings.json` exactly as
  it was before.

## M9: Control Windows by voice (settings MCP, UI automation, effects) ✅ built (2026-11-09)

**Status:** built; see README "Control Windows by voice". The settings server
(`heyclaude/mcp_windows/`, 37 tools) runs in the tool agent, with every group
below except the night-light and default-device writes on the allow-list
(those two are experimental). `tools/check_windows_settings.py` passed on the
dev laptop: 18 checks, every value restored. Typed "volume 30", "light mode" /
"dark mode", "next song", "brightness down" and "change my wallpaper to
<file>" each worked through the agent, with its effect and a working "undo
that". **Not met: the ≤ 2.5 s target.** Through the agent (Sonnet, with ~690
tools loaded from the claude.ai connectors and plugins), a command took
6.5–18 s to the spoken reply; the change itself lands a moment before.
"Undo that" takes 0.1–0.2 s because it runs locally. The fix is a local fast
path for the simplest commands, which parses "volume 30" with no model, still
shows the same effect and has an undo; that's the next step. Windows-MCP is
installed apart (`uv tool install windows-mcp`) and allowed only `Snapshot` /
`Screenshot`. Its acting tools are dropped from the allow-list in code until
M7's approvals exist. Effects (`ui/effects.json` → `effects.py` →
`island_effects.py`): generic old → new, the volume slider, mute, brightness
(a ring of dots, not rays: island-motion.md §1), the sun ↔ moon flip, the media
card with album art, the screenshot flash, and Confirm for power actions.
Not built yet: the wallpaper wipe, the battery pulse, the app-launch zoom.

**Goal:** "volume 30", "dark mode", "next song", "brightness down", "change my
wallpaper", "open Wi-Fi settings" just work, each with its own little
animation on the island.

**1. Our own Windows settings MCP server** (`heyclaude/mcp_windows/`, Python,
FastMCP over stdio, loaded by the tool agent like any MCP). Comfort settings
only. Each write tool returns the old and new value, which feeds "undo that"
and the animation.
- **Audio:** master volume get/set/step, mute, per-app volume (Core Audio via
  pycaw), list output devices. Switching the default device uses the
  undocumented IPolicyConfig, so mark it experimental.
- **Display:**
  - brightness: laptop panel through WMI `WmiMonitorBrightnessMethods`,
    external monitors through DDC/CI (`monitorcontrol`);
  - dark/light mode: registry plus a `WM_SETTINGCHANGE` broadcast;
  - wallpaper get/set, per monitor through `IDesktopWallpaper`;
  - night light: registry blob, experimental.
- **Radios:** Wi-Fi / Bluetooth on/off and airplane mode status (WinRT
  `Windows.Devices.Radios`).
- **Power:**
  - battery status, and power mode (best performance / balanced /
    efficiency, via `PowerSetActiveOverlayScheme`);
  - lock;
  - sleep, restart and shutdown always need a click, never voice alone.
- **Media:** what's playing (title, artist, art) and play/pause/next/previous
  through WinRT `GlobalSystemMediaTransportControlsSessionManager`.
- **Apps and windows:** launch an app by name, list/focus/minimize/close
  windows, snap left/right.
- **Clipboard:** read/write.
- **Screen:** "what's on my screen?" sends a screenshot to the agent.
- **Open any Settings page** through `ms-settings:` URIs, the fallback for
  anything without a tool.
- **Never exposed:** Defender, firewall, UAC, user accounts, raw registry
  editing, arbitrary PowerShell, drivers/services.
- Read tools go on the agent's allow-list. Write tools are allowed too, but
  each change is announced on the island with an undo button for ~8 s.

**2. Windows-MCP for general UI automation** ([CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP),
MIT, actively maintained). Accessibility-tree snapshots plus click, type,
scroll, shortcuts and app launch, so it can work any app.
- Add it as an optional MCP. Run it with `--exclude-tools "PowerShell,Registry"`
  for the always-on tool agent.
- A voice assistant can be set off by a video, a podcast or another person in
  the room. So:
  - read-only tools (Snapshot, Screenshot) are allowed;
  - acting tools (Click, Type, Shortcut, Drag, App) need an island
    confirmation the first time per app per session, using M7's approvals;
  - PowerShell / Registry only inside tasks, with approvals, never in the
    always-on agent.
- Prefer our settings tools over clicking through Settings, because they are
  faster, exact and undoable. The agent prompt says so.

**3. Per-tool effects:** custom animations in the M5/M6 registry. A new
`ui/effects.json` maps a tool to a widget plus an animation, with a generic
fallback (service icon + old → new value morph). Characters react through the
existing `working`/`done` states and can add tool-specific antics.
- **Volume:** the pill becomes a slider that glides to the new level.
- **Mute:** a speaker icon with a slash drawn in.
- **Brightness:** sun rays grow or shrink.
- **Dark mode:** sun ↔ moon flip while the pill's own background crossfades.
- **Wallpaper:** the old wallpaper's thumbnail wipes into the new one.
- **Wi-Fi / Bluetooth:** the icon's bars or waves animate on or off.
- **Battery:** fill level with a charging pulse.
- **Media:** the album art flips in with a progress line; skip slides it out.
- **App launch:** the app's icon zooms out of the island towards the screen.
- **Screenshot:** a shutter flash.
- **Undo:** the effect plays in reverse.

**4. Smaller capacity ideas** for after this milestone, if wanted:
- timers, alarms and reminders (local scheduler, island countdown widget,
  toast);
- file search through the Windows Search index;
- clipboard history ("paste what I copied before that");
- "read my notifications": check whether the WinRT listener works without
  package identity.

**Touches:** new `heyclaude/mcp_windows/`, `config.example.yaml` (agent MCPs and
allow-list), `ui/effects.json` (with M5), README.
**Done when:**
- each settings tool has offline tests with the Windows APIs mocked, plus a
  live check script;
- "volume 30", "dark mode", "next song", "brightness down" and "change my
  wallpaper" work end to end in ≤ 2.5 s with their effect and an undo;
- Windows-MCP's acting tools are confirmed on the island before they run.

## M8: Package and ship

**Goal:** a setup a non-developer can install.

- PyInstaller or Nuitka build with an installer; models downloaded on
  first run.
- First-run wizard (no wake-word training: the M2a model is the default; the M2b tune-up is optional): find or install the `claude` CLI, check `claude auth
  status`, mic test, pick projects, choose a voice.
- Auto-update check; uninstall cleanly (Startup entry, state).
- CI: offline tests plus a simulated-audio smoke test on Windows.

**Done when:** a fresh Windows user account goes from installer to "hey claude,
what time is it?" in under 10 minutes without a terminal.
