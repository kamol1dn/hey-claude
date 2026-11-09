# hey-claude

[![CI](https://github.com/kamol1dn/hey-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/kamol1dn/hey-claude/actions/workflows/ci.yml) [![Release](https://img.shields.io/github/v/release/kamol1dn/hey-claude?include_prereleases)](https://github.com/kamol1dn/hey-claude/releases)

An always-listening voice assistant for Windows 11. Say the wake word and a
small black "island" drops in at the top of the screen. Ask a quick question
and a small model answers out loud in a second or two. Say *"start a task in
\<project\>: \<thing\>"* and a headless Claude Code run starts in that folder.
The island tracks the run, and when it finishes a one-sentence summary is read
out.

Everything runs in one Python process. Speech-to-text and the Kokoro voice run
on the GPU unless the GPU is busy (a render, say), in which case that request
goes to the CPU; everything else runs on the CPU. Claude is driven through the
`claude` CLI you're already signed in to. No API key is needed; with one,
quick answers can go straight to the API (faster), while lookups and tasks
stay on your subscription.

There are three tiers:

| tier | what it's for | runs on |
|---|---|---|
| **(a) quick answers** | "what's 15% of 80", "capital of Peru" | the Anthropic API (haiku) if hey-claude has a key (`ANTHROPIC_API_KEY` or `state/anthropic-api-key.txt`), else one warm, tool-less `claude` process |
| **(b) your tools** | "who messaged me on Telegram", "what's on my calendar" | **one** warm Claude Code process with tools and MCP servers, started at launch and reused |
| **(c) real work** | "start a task in inha app: fix the login screen" | a **new** headless Claude Code session in that folder, per task |

```
mic ─▶ echo canceller ─▶ openWakeWord ─▶ Silero VAD + end of turn ─▶ faster-whisper (GPU, CPU fallback)
                                                                    │
                                     ┌──────────────────────────────┘
                                     ▼
                     local router ── "start a task in X: Y" ───────────▶ (c) claude -p "Y" (cwd = X) ─▶ island task list
                          │       ── "telegram / calendar / email…" ──┐       stream-json → log, session id,
                          ▼                                           │       SUMMARY line → spoken when done
     (a) haiku: the API, or a warm tool-less `claude` process         │
              │  answer │ "TASK <project> :: <task>" │ "LOOKUP" ──────┤
              │                                                       ▼
              │                         (b) warm Claude Code agent: tools + MCP + claude.ai connectors
              ▼                                                       │  final answer only
        sentence splitter ─▶ Kokoro (GPU → CPU → SAPI) ◀──────────────┘
                                   └─▶ what it plays feeds the echo canceller (barge-in)
```

## Setup

1. **Python 3.12** (3.13+ is fine for most parts, but openWakeWord/onnxruntime
   wheels are most reliable on 3.12):

   ```bash
   cd D:\coding\hey-claude
   py -3.12 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

2. **The `claude` CLI, signed in.** It is **not on PATH** on this machine.
   The desktop app runs its own bundled copy at
   `%LOCALAPPDATA%\Claude-Data\<id>\claude-code\<version>\<hash>\claude.exe`.
   hey-claude looks for `claude` on PATH, then `~\.local\bin\claude.exe`,
   then the newest bundled copy, so it runs either way. But the bundled path
   changes on every app update, and the bundled copy is signed in only
   *through the desktop app*. Run on its own, it reports `loggedIn: false` and
   every call fails with "OAuth session expired". So install a standalone CLI
   once and sign it in. Either use the native installer, from the bundled
   copy:

   ```bash
   "%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc\claude-code\2.1.317\a33aa5518497\claude.exe" install
   ```

   or npm:

   ```bash
   npm i -g @anthropic-ai/claude-code
   ```

   then sign in:

   ```bash
   claude auth login
   ```

   Check with `claude auth status` → `"loggedIn": true`. Until then, the
   assistant answers every question with "run claude auth login". If you'd
   rather not touch PATH, set `claude.exe` in `config.yaml`.

3. **The Kokoro voice** (optional; without it, Windows voices speak). Model
   files, ~350 MB, into `state\models\kokoro\`:

   ```bash
   mkdir state\models\kokoro
   curl -L -o state\models\kokoro\kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
   curl -L -o state\models\kokoro\voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
   ```

   That's enough for Kokoro on the CPU. For the GPU, add onnxruntime-gpu and
   cuDNN 9 (~550 MB). It replaces the CPU onnxruntime's files, so run this after
   `requirements.txt`, and again after any later `pip install -r`:

   ```bash
   .venv\Scripts\python -m pip install "onnxruntime-gpu[cudnn]>=1.31"
   ```

   ```bash
   .venv\Scripts\python -m pip install --force-reinstall --no-deps "onnxruntime-gpu>=1.31"
   ```

   `[cudnn]` assumes the CUDA 13 toolkit is installed (it is on this PC);
   otherwise use `[cuda,cudnn]`. espeak-ng comes bundled with kokoro-onnx.
   The island's tooltip and right-click menu say
   which voice is active ("kokoro gpu", "kokoro cpu (fallback: no cuda)",
   "sapi (kokoro: model missing)").

4. **Config:** copy `config.example.yaml` to `config.yaml` and fill in
   `projects:` (spoken name → folder). Everything else has working defaults.

5. **Run:**

   ```bash
   run.cmd
   ```

   First start downloads the wake-word models (~6 MB) and whisper
   `large-v3-turbo` for the GPU (~1.6 GB, already there if another app on
   this PC uses faster-whisper) and `base.en` for the CPU (~140 MB). On a
   slow line, set `HF_HUB_DISABLE_XET=1` first (Hugging Face's xet transfer
   can stall at 0 bytes) or put a downloaded copy in a folder and point
   `stt.gpu_model` at it.
   After that, startup takes about 5 s.

   To run it all day without a terminal, tick **Start with Windows** in the
   tray's Settings… (see below), or start it silently once with
   `.venv\Scripts\pythonw.exe -m heyclaude`.

### Flags

| flag | what it does |
|---|---|
| `--gpu` / `--cpu` | whisper on the GPU / the CPU, overriding `stt.device`. |
| `--text` | type requests instead of speaking (no mic, no whisper). |
| `--simulate x.wav` | use a 16 kHz mono wav as the microphone. Good for testing and timing. |
| `--simulate a.wav b.wav …` | a simulated conversation: each clip after the first is played into the follow-up window (so it needs no wake word; make one with `tools\make_test_wav.py - "and tomorrow?" f.wav`), or, if that's closed, once it's idle. It waits for running tasks to finish first. |
| `--exit` | with `--simulate`: quit after the request (and any task it started) is done. |
| `--muted` | start with listening off (mic closed); the hotkey and "Talk now" still work. |
| `--quit` | ask the running hey-claude to quit, then exit. |
| `--config f.yaml` | use another config file instead of `config.yaml`. |
| `--record-wake-samples [N]` | record N (default 40) clips of you saying "hey claude" for training and `tools\wake_eval.py`, then exit. `--auto`: no Enter between clips. See [docs/train-hey-claude.md](docs/train-hey-claude.md). |
| `--no-ui` / `--no-tray` | no island / no tray icon. |

`--simulate` and `--text` runs are treated as tests: they skip the
single-instance check, the global hotkey and the Startup-shortcut sync, so
they can run next to a normal copy.

## Tray, hotkey and settings

hey-claude lives in the **tray** (the orange dot; grey with a slash while
listening is off). Left-click it to talk. Right-click for:

| menu item | what it does |
|---|---|
| **Talk now** | listen for one request without the wake word (same as clicking the island) |
| **Listening** | wake word on/off. Off **closes the microphone stream**, so Windows' mic-in-use indicator goes away; the island turns into a muted grey pill |
| **Pause for 1 hour** | listening off, back on by itself after an hour ("Resume listening" ends it early) |
| **Settings…** | the settings window, below |
| **Record wake samples…** | opens the "hey claude" sample recorder in a console window; listening is paused until you close it |
| **Open logs** | `state\logs\` (the app log and every task's log) |
| **Quit** | |

While listening is off, **Talk now**, the hotkey and clicking the pill still
work: the mic opens for that one request and closes again.

**Hotkey: talk or type.** `Ctrl+Alt+Space` by default (`app.hotkey`), a
system-wide hotkey through `RegisterHotKey`, no admin rights needed.
Pressing it opens the island as an input field, focused, with the mic on:

- **just talk:** it works like the wake word (end-of-speech detection,
  hold Ctrl to keep going). What it heard shows in the field and is sent.
- **or type:** the first keystroke turns the mic off. **Enter** sends,
  **Shift+Enter** adds a line, **Esc** cancels.
- **press the hotkey again** (or click somewhere else) to close it.

Typed requests go through the same router and tiers as spoken ones (quick
answer, tool agent, "start a task in …"). The island takes keyboard focus
even when another app is in front (it attaches to the foreground window's
input queue for the switch), and gives focus back to that app when it
closes. The wake word is ignored while the field is open. If nothing is
said within `listen.start_timeout`, the mic turns off and the field stays
open for typing.

If another app already owns the key, a tray balloon says so; click it to
open Settings and pick another. On this machine PowerToys holds
`Ctrl+Alt+Space`; `Ctrl+Shift+Space`, `Win+Alt+Space`, `Ctrl+Alt+H` and `F9`
were free.

**Hold to keep listening** (`listen.hold_key`, default `ctrl`): if you
pause to think mid-sentence, hold Ctrl while you talk. While it's down the
island shows **holding** and silence never ends the request, however long the
pause. Let go and it goes back to normal: it stops about 0.7 s after you
stop talking (counted from the later of the release and your last word).
You can press it before you start talking too. Pressing another key during
the hold (Ctrl+C, Ctrl+V...) makes it a shortcut, not a hold. The key is
watched only while the island is listening, by a low-level keyboard hook
that passes every key through untouched. Once a hold is used, the length
cap is `listen.max_utterance_s` (180 s) instead of `max_seconds`, and
pauses longer than half a second are cut out before whisper, so a 30 s
think-pause costs nothing. Other choices: `right ctrl`, `caps lock`, `f13`,
or `""` to turn it off.

**Settings window** (tray → Settings…) edits `config.yaml`, creating it from
`config.example.yaml` the first time. Tabs: General (listening at start,
hotkey with a Record… button, hold key, start with Windows), Voice & wake (wake model
and threshold; whisper device, GPU and CPU models, GPU precision, busy
thresholds, the Premiere opt-out; voice, rate, a test button), Models (quick-answer backend and models, the tool agent on/off,
model and filler line, task model, permission mode and parallel limit),
Projects (spoken name → folder, with a folder picker) and Permissions (the
agent's and the tasks' `allowed_tools`, one per line). Fields tagged
**restart** take effect after a restart, and the window offers a **Restart
now** button after saving them. Everything else applies on save:

| applies on save | needs a restart |
|---|---|
| wake threshold, cooldown and speaking suppression, `listen.*`, `tasks.*` (permission mode, allowed tools, model, parallel limit), projects for "start a task in …", the agent's filler line, `island.*`, hotkey, start with Windows, `stt.*_busy_pct`, `stt.yield_to_premiere` | wake model, the rest of `stt.*` (device, models), `tts.*` (voice, rate, backend), `fast.*`, the agent's on/off, model and allowed tools, `claude.exe` |

New project names are routed right away; whisper's spelling hints and the
quick model's project list pick them up after a restart.

**Start with Windows** puts `hey-claude.lnk` in your Startup folder
(`shell:startup`). It runs `.venv\Scripts\pythonw.exe -m heyclaude` in the
repo folder, so there's no console window. Turning it off deletes the
shortcut. The setting follows the shortcut: if you delete it by hand, the
checkbox reads off next time. Turn it on from the checkout you actually run
(the shortcut points at whichever folder saved it).

**One copy at a time:** a second launch (say, clicking the shortcut again)
tells the running copy to show itself and exits.

**Speech-to-text device** (`stt.device`, default `gpu`): the device is
picked for each request from the actual load, read from NVML (GPU
utilisation averaged over about 0.5 s, free VRAM) and psutil (CPU).
hey-claude's own whisper and Kokoro work is left out of the GPU reading:

| situation | whisper runs on | island / log says |
|---|---|---|
| GPU under `stt.gpu_busy_pct` (70%) | GPU (`stt.gpu_model`, `large-v3-turbo` int8_float16, ~0.25 s per request on an RTX 4060) | `gpu (turbo)` |
| GPU at 70% or more, or too little free VRAM to load the model | CPU (`stt.cpu_model`, `base.en`) | `cpu (gpu busy 83%)` / `cpu (low vram 0.9 GB)` |
| GPU busy **and** CPU at `stt.cpu_busy_pct` (85%) or more | GPU anyway | `gpu (cpu busy)` |
| back on the GPU | once it's under `stt.gpu_resume_pct` (60%), so it doesn't flip back and forth | |

An idle Premiere is fine; only real load counts (e.g. a Remotion render
while Premiere sits open). Both models stay loaded once used, so switching
is instant. The GPU model loads at start-up. If it isn't downloaded yet
(1.6–3 GB the first time), start-up doesn't wait: the CPU model answers
(`cpu (downloading turbo)`) while it downloads and loads in the
background (a stalled Hugging Face download leaves it on the CPU model
meanwhile; it doesn't block anything). Hard fallbacks to the CPU: CUDA missing or the GPU
load failing (`cpu (fallback: no cuda)` / `gpu load failed`, for the
session), out of memory while loading (retried a minute later), and a CUDA
error mid-transcription (`cpu (fallback: cuda error)`; that request is
retried on the CPU). `stt.yield_to_premiere: true` is an opt-in that keeps
whisper off the GPU whenever Premiere Pro is running, idle or not.
`--cpu` or `stt.device: cpu` keeps the GPU out of it entirely. The
`[ready]` line shows the choice and the readings, e.g.
`stt: cpu (downloading turbo) [gpu 7%, vram free 6.8 GB, cpu 62%]`, and
every switch is logged as `[stt] now …`.

GPU needs an NVIDIA driver plus CUDA 12 cuBLAS (`cublas64_12.dll` and
`cublasLt64_12.dll`); the ctranslate2 wheel already ships cuDNN 9. A CUDA 13
toolkit alone isn't enough. Any of these works: `pip install
nvidia-cublas-cu12` into the venv (found automatically), a torch install in
the venv, or copying the two DLLs from another venv's `torch\lib` next to
`.venv\Lib\site-packages\ctranslate2\ctranslate2.dll` (what this machine
uses; a reinstall of ctranslate2 removes them). Without cuBLAS 12 the load
fails and it uses the CPU (`cpu (fallback: gpu load failed)`).

**Logs:** everything the app prints also goes to `state\logs\app.log`
(timestamped, rotated at 1 MB, 3 old files kept), including uncaught
exceptions. That's where to look when it runs under `pythonw` with no
console. If the assistant thread crashes, it restarts once by itself; a
second crash shows the error on the island.

Make a test clip with a Windows voice:

```bash
.venv\Scripts\python tools\make_test_wav.py "Hey Jarvis" "What is the capital of France?" samples\q.wav
```

```bash
.venv\Scripts\python -m heyclaude --simulate samples\q.wav --exit
```

## Talking to it

| say | what happens |
|---|---|
| *hey claude … what's 15% of 80?* | quick answer from haiku, spoken |
| *hey claude … start a task in hey claude: add a license file* | headless Claude Code run in `D:\coding\hey-claude` |
| *hey claude … in the inha app, find why login is slow* | the model notices it's real work and turns it into a task |
| *hey claude … who messaged me on Telegram today?* | "Let me check." → the warm tool agent answers (needs the MCP set up, see below) |
| *hey claude … how are my tasks going?* | spoken status of running tasks |
| *(after an answer, no wake word)* … *and the weather there?* | a follow-up: for 5 s after it stops talking it listens without the wake word (the island shows a draining ring). "Thanks", "that's all" or silence ends it |
| *… what did the inha app task change?* | resumes that task's session headlessly (`claude -p --resume <id> --fork-session`, read-only tools) and reads the answer out. Tasks are found by project, words of the task, or "the last task"; right after a task is announced, "what did it change?" works too |
| *… stop the task* / *stop the inha app task* / *stop all tasks* | stops it (asks which one if several are running) |
| *… resume the last task in a terminal* | opens a console running `claude --resume <id>` in the task's folder |
| *… start fresh* | forgets the conversation (and restarts the warm processes so they forget too) |
| click the idle pill | talk without the wake word |
| press the hotkey (`Ctrl+Alt+Space`) | talk, or type into the field that opens (Enter sends) |
| click while speaking, or the hotkey | stop speaking (and, with the hotkey, listen again) |
| right-click the island | menu: talk now / stop / open logs / quit (the tray menu has the rest) |

The wake word is **"hey claude"** once its model is trained and saved as
`models\hey_claude.onnx` (see [docs/train-hey-claude.md](docs/train-hey-claude.md):
record your voice, train in Colab, measure with `tools\wake_eval.py`). Until
then it falls back to openWakeWord's stock **"hey jarvis"**: the idle island
shows which one is loaded, and the log says why it fell back. While it's
talking, the wake word is ignored (plus 0.7 s), so its own voice can't wake it;
click or use the hotkey to cut it off. Project
names don't have to be heard perfectly: "in ha app" still matches `inha app`,
and the names are given to whisper as a spelling hint.

## Config

See [config.example.yaml](config.example.yaml); every key is commented. Most
of it is also in the Settings window. The ones you'll touch:

- `app.*`: listening at start, the hotkey, start with Windows.
- `projects`: spoken name → folder. Several names can point at one folder.
- `tasks.permission_mode`, `tasks.allowed_tools`: what a voice-started run
  may do without asking. Runs are started with `--permission-prompts none`,
  so anything outside the allow-list is **denied automatically** instead of
  hanging on a prompt nobody will see. Denials are listed at the end of the
  task's log. The default allows reading, editing and writing files and
  read-only git; no other shell commands.
- `agent.*`: the warm tool agent (model, allowed tools, which desktop MCP
  servers to import). Set `agent.enabled: false` to drop tier (b).
- `fast.backend`: `auto` uses the Anthropic API directly (`claude-haiku-5-5`)
  when hey-claude has a key, and the CLI otherwise. The key is
  `ANTHROPIC_API_KEY` in hey-claude's own environment, or the first line of
  `state\anthropic-api-key.txt` (`fast.api_key_file`, git-ignored). The file is
  the better place: a user-wide environment variable would also switch every
  `claude` you run yourself to API billing. Every `claude` process hey-claude
  starts (quick answers on the CLI path, the tool agent, tasks, task
  questions, "resume in a terminal") has `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` removed from its environment, so lookups and tasks
  always run on your subscription login. Set `sdk_model: claude-sonnet-5-5`
  for smarter, slower answers.
- `stt.device`, `stt.gpu_model`, `stt.cpu_model`: see "Speech-to-text
  device" above. On the CPU, `base.en` is the default (≈0.6 s per request).
  `small.en` is more accurate and slower; `tiny.en` is faster and sloppier.
- `tts.*`: `engine` (`kokoro` default, `sapi`, `piper`), `device`, `voice`,
  `speed`; see "Voice" below. A `config.yaml` from before M4 (`tts.backend`)
  is moved to Kokoro, since the settings window used to write the old `sapi`
  default into every config; set `tts.engine: sapi` to keep Windows voices.
- `listen.*`: Silero VAD, the end-of-turn windows, partial transcripts, echo
  cancellation and barge-in; see "Voice" below.

## Tasks

Each task is:

```
claude -p "<task>" --output-format stream-json --verbose
       --permission-mode <tasks.permission_mode> --permission-prompts none
       --allowedTools <tasks.allowed_tools> --name "voice: <task>"
       --append-system-prompt "...end with 'SUMMARY: <one sentence>'"
```

started with the project folder as the working directory. While it runs, the
island shows a spinner and the last tool it used (e.g. `Edit: main.py`).
State is kept in `state/`:

- `state/tasks.json`: every task with its **session id**, status, summary
  and cost.
- `state/logs/<id>.log`: readable log (text, tool calls, errors, denials).
- `state/logs/<id>.jsonl`: the raw stream.

Clicking a task on the island opens its live log, with buttons to copy the
resume command, **resume it in a terminal**, open the folder, or stop it. By
hand:

```bash
cd /d D:\coding\hey-claude && claude --resume <session id>
```

### Do these sessions show up in the Claude desktop app's Code tab?

**No.** I checked twice: once with a failed run and once with a successful
task (session `1cf6959a…`, 19 s). The desktop app's Code
tab lists only sessions the app created itself (its `local_…` sessions). A
`claude -p` run writes its transcript to
`%USERPROFILE%\.claude\projects\<folder-slug>\<session id>.jsonl`, where
`claude --resume <id>` (and the `/resume` picker, under its `voice: …` name)
finds it. The Code tab didn't list it. The CLI has a `--desktop` flag ("open in
the Claude Desktop app instead of the terminal, with `--resume <id>`"), which
may be the bridge, but I haven't tried it here because it opens the app.

## Which MCP servers the tool agent sees

Checked with `claude mcp list` and with the agent's own start-up event (it
prints `[agent] model …, N tools, MCP: …` on its first lookup):

| source | seen by the headless CLI? |
|---|---|
| `~/.claude.json` user scope: **trypost** | yes, but it fails (HTTP 403, its Authorization header is rejected) |
| **telegram** (`node D:\coding\telegram-mcp-server\dist\index.js`) | **no.** It's defined only in the desktop app's `%LOCALAPPDATA%\Claude-Data\<id>\claude_desktop_config.json`, which the CLI never reads |
| claude.ai connectors | **yes, once the CLI is signed in.** They attach a few seconds after start-up. Connected: Gmail, Google Calendar, Google Drive, Docs, Sheets, Slides, Spotify, Vercel, Higgsfield, vidIQ, Claude Docs. Needs auth: Notion, Supabase. The `productivity` plugin's own servers show needs-auth/failed. Tool names look like `mcp__claude_ai_Google_Calendar__list_events` |
| built-in tools | yes, 37 (Read, Grep, WebSearch, WebFetch…); the agent may use only those in `agent.allowed_tools` |

To give the agent Telegram, set `agent.import_desktop_mcp: [telegram]`. It
copies that one server's entry, env included, into `state/agent-mcp.json`
(git-ignored) and passes it with `--mcp-config`. Then add `mcp__telegram`
to `agent.allowed_tools`. **Caveat:** that starts a second copy of the server
on the same `TELEGRAM_SESSION` as the desktop app's copy. Telegram hands
updates to only one connection per authorization (the telegram-mcp-server
README explains this). Reading history and sending work from both, but live
updates go to one of them. I didn't start it during testing for that reason.

The default `agent.allowed_tools` includes only the **read-only** Calendar,
Gmail and Drive tools (list/search/get/read). Sending email, creating events
and editing files are denied unless you add them. To see every tool name, run
`tools\trace_agent.py "list your tools that mention X"`.

## Control Windows by voice (M9)

"Volume 30", "dark mode", "next song", "brightness down", "change my
wallpaper to D:\pics\hill.jpg", "open Bluetooth settings", "snap this window
left", "what's on my screen?". Each change shows on the island with its own
small animation and an **Undo** button for ~8 s, and **"undo that"** puts the
last change back.

**How it works.** These requests skip the quick model and go straight to the
tool agent (a local pattern in `brain.py` spots them), which now also loads
hey-claude's own **Windows settings MCP server** (`heyclaude/mcp_windows/`,
FastMCP/MCPServer over stdio, started with hey-claude's Python). The config
for it is written to `state/agent-windows-mcp.json` at every start. Every
change returns `{old, new}`. hey-claude watches the agent's tool calls in its
stream-json, turns each into an effect event (`ui/effects.json` →
`heyclaude/effects.py`) and remembers how to undo it (`heyclaude/undo.py`).
Undo runs in hey-claude's own process with no model round trip, so it takes
about 0.1 s.

| group | tools (all `mcp__windows__…`) |
|---|---|
| audio | `get_volume`, `set_volume` (level or ±delta; unmutes), `set_mute`, `list_app_volumes`, `set_app_volume`, `list_audio_devices`, `set_default_output_device` (*experimental*, undocumented IPolicyConfig) |
| display | `get_brightness` / `set_brightness` (laptop panel via WMI, external monitors via DDC/CI), `get_theme` / `set_theme` (dark/light, apps/system), `get_wallpaper` / `set_wallpaper` (per monitor, IDesktopWallpaper), `get_night_light` / `set_night_light` (*experimental*, registry blob) |
| radios | `get_radios` (Wi-Fi, Bluetooth, airplane mode), `set_wifi`, `set_bluetooth` |
| power | `battery_status`, `get_power_mode` / `set_power_mode` (efficiency / balanced / performance), `lock_pc`, `request_power_action` |
| media | `now_playing`, `media_control` (play, pause, play_pause, next, previous) |
| apps, windows | `list_apps`, `launch_app` (Start-menu apps only), `list_windows`, `focus_window`, `minimize_window`, `restore_window`, `snap_window`, `close_window` |
| other | `get_clipboard`, `set_clipboard`, `screenshot` (≤ 1280 px JPEG for the agent, never saved), `open_settings_page` (`ms-settings:` pages by name) |

**Safety.** A voice assistant can be set off by a video or by someone else in
the room, so:
- **Never exposed:** Defender, firewall, UAC, user accounts, raw registry
  editing, arbitrary PowerShell, drivers and services. `launch_app` refuses
  shells, the registry editor and security/account tools, and
  `open_settings_page` refuses security, account and sign-in pages.
- **Sleep, restart and shutdown only ask.** `request_power_action` does
  nothing by itself. The island shows **Confirm / Cancel** for 20 s, and only
  the click runs it. No voice command can confirm.
- The default allow-list (`agent.allowed_tools`) has the read tools and the
  comfort-setting writes. It leaves out `close_window` (unsaved work) and the
  two experimental tools; anything not listed is denied without a prompt.

**Windows-MCP, optional** ([CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP),
MIT): accessibility-tree snapshots and screenshots of any app. It installs
**apart from hey-claude's venv**, so it can't disturb the GPU stack:

```bash
uv tool install windows-mcp
```

That is about 86 packages. Here it took 4 minutes on a ~0.5–1 MB/s line: the
biggest downloads were pywin32 6.6 MB, cryptography 3.6 MB, pydantic-core
2.2 MB and rapidfuzz 1.7 MB, about 20 MB in all (numpy and pillow came from
uv's cache). Then set `agent.windows_mcp.enabled: true`. hey-claude starts it as
`windows-mcp serve --transport stdio --exclude-tools PowerShell,Registry`
(those two are always excluded) with its telemetry off
(`ANONYMIZED_TELEMETRY=false`). Only its **read-only** tools, `Snapshot` and
`Screenshot`, may be allowed. Its acting tools (Click, Type, Shortcut, Drag,
App, …) need an island approval per app first, which arrives with M7's
approvals. Until then hey-claude removes them from `--allowedTools` even if
you add them, and says so in the log. The agent prompt tells it to use the
settings tools for settings and Windows-MCP only to look.

**The settings server's own packages** (in `requirements.txt`, ~5 MB, pure
Python or small wheels): `mcp`, `pycaw`, `monitorcontrol` and the `winrt-*`
projections for media and radios.

**Checking it on your PC:** `tools\check_windows_settings.py` runs every
tool, checks the result, puts each value back and checks that too. It also
starts the server over stdio the way the agent does. Wi-Fi/Bluetooth
switching, the default output device and locking are opt-in (`--radios`,
`--device`, `--lock`, or `--all`), because they can cut you off.
Measured here (2026-11-09, a laptop, panel brightness via WMI): 18 checks ok,
lock skipped.

**Effects** (`heyclaude/island_effects.py` paints them, from the data in
`ui/effects.json`): a volume slider that glides to the new level, a speaker
with its slash drawn in for mute, a sun whose ring of dots grows or shrinks
for brightness, a sun ↔ moon flip with a light wash for dark/light mode, a
media card with the album art (read by hey-claude itself, never sent to the
model) and the title sliding on skip, a shutter flash for screenshots, a
Confirm row for power actions, and a generic "icon + old → new" for
everything else. An undo plays the same effect backwards. They are short
(~0.6 s) and calm. After the entry only a countdown hairline redraws, at 4 fps,
and nothing runs once the row has gone. M5 will port them to QML with the same
data contract.

## Conversations and sessions

- **One conversation across all tiers.** hey-claude keeps a rolling
  transcript (`heyclaude/conversation.py`) and gives each tier the turns it
  hasn't seen: the quick model and the tool agent remember their own turns,
  so they're only sent what the *other* tiers said since they last spoke
  (everything, after a process restart). A task gets the whole recent
  conversation appended to its prompt. So "what's on my calendar?" (agent) →
  "and the weather then?" (agent or quick model) → "start a task in X:
  write that down" keeps context. The window is the last
  `conversation.max_turns` turns, at most `conversation.max_tokens`, and a
  new conversation starts after `conversation.idle_reset_s` (10 min) of
  silence or when you say "start fresh".
- **Follow-ups without the wake word.** After an answer (and after a task is
  announced) it listens for `conversation.followup_s` seconds once its own
  voice has finished. It waits for the speaking gate (TTS plus
  `wake.speaking_tail_s`), even with `wake.suppress_while_speaking` off, so it
  can't hear itself. "Thanks", "that's all", "no thanks" or silence ends it.
  Set `followup_s: 0` to turn it off.
- **Typed requests** go through the same entry point,
  `Assistant.request(text, source="typed")`, so they join the conversation
  too (no follow-up window is opened for them).
- **What gets saved:** by default nothing. The quick model and the tool agent
  run with `--no-session-persistence`. With `conversation.save: true`
  (Settings → Models → Conversation → "Save this conversation") each turn is
  appended to `state/conversations/<date>.jsonl` (git-ignored) and picked up
  again after a restart if it's recent, and the agent's Claude Code session
  is saved and named "hey-claude <date>" (one per day; a new one starts at
  midnight or when the agent is recycled after `agent.max_turns`). Tasks are
  always their own saved sessions. Questions about a task use a forked,
  unsaved copy of its session, so the task's own session is left as it was.

## Voice: Kokoro, end of turn, barge-in

**Kokoro** (`tts.engine: kokoro`, the default) speaks through kokoro-onnx.
With `tts.device: gpu` it follows the same per-request rule as speech-to-text:
the GPU unless it's at least `stt.gpu_busy_pct` (70%) busy or short of VRAM,
decided once per reply. hey-claude's own whisper and Kokoro work doesn't count
as "busy" (it's left out of the load reading; without that, every
transcription pushed the next reply onto the CPU). The fallback chain is
Kokoro on the GPU → Kokoro on the CPU → Windows SAPI, with the reason in the
log and in the island's tooltip/menu:

| situation | voice |
|---|---|
| onnxruntime-gpu + cuDNN present, GPU not busy | `kokoro gpu` |
| GPU busy (another program) | `kokoro cpu (gpu busy 83%)` for that reply |
| no CUDA provider in onnxruntime, cuDNN missing | `kokoro cpu (fallback: no cuda)` for the session |
| a CUDA error while speaking | that sentence is redone on the CPU; GPU off for the session |
| `stt.yield_to_premiere: true` and Premiere open | `kokoro cpu (fallback: premiere)` |
| model files missing, or Kokoro failing on the CPU too | `sapi (kokoro: model missing)` |

Measured on the RTX 4060 laptop (time to synthesise one sentence, after
warm-up): **GPU 0.25–0.39 s**, CPU (8 threads) 0.5–0.8 s for a 6-word
sentence. In the latency runs, first text → first audio was 0.47 s median
(that includes waiting for the first sentence to finish arriving). Sentence N+1 is synthesised while sentence N plays, and a long first
sentence is cut at a comma once it has 9 words (`tts.first_chunk_words`), so
the first audio doesn't wait for all of it. Kokoro's STFT step has no CUDA
kernel in onnxruntime and runs on the CPU (~0.1 s of each sentence). An exact
GPU rewrite of it was tried and dropped: the model is sensitive to the FFT's
rounding, and the voice came out with a metallic buzz.

The output stream opens when a reply starts and closes 4 s after the voice
stops (`tts.idle_close_s`), so an idle hey-claude doesn't keep Windows awake.

**End of turn.** Silero VAD (the model faster-whisper ships, on the CPU)
replaces webrtcvad. When you pause for 160 ms (`listen.pause_probe_ms`), the
audio so far is transcribed in the background:

- a finished sentence ("…capital of Australia?") ends the turn after
  `short_silence_ms` (300 ms);
- one that trails off ("…in Tashkent and", "the", "um", a trailing comma)
  waits `long_silence_ms` (1200 ms);
- anything else `end_silence_ms` (700 ms).

If you don't speak again, that transcript is the one used, so speech-to-text
costs nothing after you stop (`stt=0.0` in the latency log, "[transcribed at
the pause]" in app.log). In practice the turn ends about 0.5 s after the last
word with turbo (160 ms pause + ~0.25 s transcription + the short window
overlapping it), 0.6 s with large-v3.

**Partial transcripts.** While you talk, the island's listening pill shows
what it has heard so far, refreshed every 0.8 s (`listen.partials: auto` =
only while whisper is on the GPU).

**Echo cancellation and barge-in.** Everything the Kokoro (or Piper) player
outputs is handed, at 16 kHz, to WebRTC's AEC3 (the echo canceller in the
`livekit` package's prebuilt binary; no compiler needed). Every mic block
goes through it before the wake word, the VAD and whisper see it. On a
synthetic test (Kokoro speech through a 120 ms, reverberant echo path, plus
noise) it removed 36 dB of echo, and Silero heard no speech left in the echo
alone. While it speaks, `listen.barge_in` watches the cleaned audio: 200 ms of
your speech (`barge_in_ms`, after a 400 ms grace period at the start of a
reply while the filter settles) stops the voice, and what you say is taken as
the next request without the wake word. Saying "stop" just stops it. With
SAPI there's no reference signal, so barge-in is off. The 200 ms is
deliberately short: in the synthetic double-talk tests AEC3 let the first
250–350 ms of the user's voice through and then partly suppressed it while
the voice kept playing. **Not checked with a real mic and speakers yet.**

**Speech-to-text models.** `tools\stt_eval.py` measures speed and word error
rate per model. On 45 Kokoro-spoken clips (three voices, 15 assistant-style
requests, 2–5 s each; native accents, so this is the speed check, not the
accent check). Most "errors" are number formatting ("7 30" for "seven
thirty"), the same for every model:

| model | WER | median per clip | p90 | load |
|---|---|---|---|---|
| large-v3, GPU int8_float16 | 6.4% | 0.41 s | 0.47 s | 12 s |
| large-v3-turbo, GPU int8_float16 | 6.9% | 0.25 s | 0.28 s | 3 s |
| base.en, CPU int8 (the fallback) | 7.9% | 0.36 s | 0.38 s | 1 s |
| small, CPU int8 | 6.4% | 1.13 s | 1.45 s | 2 s |
| medium, CPU int8 | 6.4% | 3.06 s | 4.18 s | 4 s |

turbo is 40% faster than large-v3 at the same accuracy here, and that speed
shortens the end of turn, so it's now the default `stt.gpu_model`. On the CPU, base.en stays the fallback: small is
no more accurate on these clips and three times slower. base.en is the one
that mangles names ("an inha app", "Tashkint"). **Your accent** (the point of
the comparison) needs your voice: record the 15 prompts once (~2 min, saved
in git-ignored `state\stt_eval\`), then compare:

```bash
.venv\Scripts\python tools\stt_eval.py --record
```

```bash
.venv\Scripts\python tools\stt_eval.py
```

## Measured latency

### M4 (2026-11-09): speech end → first word, quick questions

`--simulate` with a nine-clip conversation (a Windows voice saying "Hey
Jarvis, what is the capital of Australia?", then seven follow-up questions
and "thanks, that's all"), default config: haiku over the warm CLI process
(no API key), whisper and Kokoro on the RTX 4060 laptop GPU, Silero VAD,
echo canceller on. Every request is in `state/latency.csv`.

| setup | requests | median | p90 |
|---|---|---|---|
| **large-v3-turbo** (the new default) | 18 | **2.36 s** | 3.08 s |
| large-v3 | 8 | 2.62 s | 4.33 s |
| v1 (CPU whisper, webrtcvad 0.7 s, SAPI) | 4 | 3.7–4.8 s (range) | |

Where the 2.36 s goes (medians, turbo):

| stage | time |
|---|---|
| you stop talking → turn over (pause probe + transcription at the pause + short window) | 0.53 s |
| speech-to-text after that | 0.0 s (the pause transcript is reused) |
| haiku via the warm CLI: request → first text | 1.33 s |
| first text → Kokoro audible (GPU) | 0.47 s |

The slow tail is the first question after start-up (the CLI's first real
turn: 1.9–3.0 s to first text, even after the warm-up turn) and an
occasional 1.25 s end of turn when the pause transcript isn't back in time.
The model step is now most of it; the direct API path (`fast.api_key_file`)
skips the CLI and is the next lever. **With your current config**
(`stt.gpu_model: large-v3`, `fast.cli_model: sonnet`) expect more: large-v3
adds ~0.25 s, and sonnet's first token is typically 1–2 s slower than
haiku's. Not measured with a real mic and room yet.

Two problems found while measuring, both fixed: hey-claude's own whisper
runs pushed NVML's utilisation to 77–100%, so the per-request rule sent the
next transcription and the voice to the CPU (median 3.4 s before the fix);
and a GPU rewrite of Kokoro's STFT made the voice buzz (removed, see "Voice").

### v1 (2026-11-09)

Measured on 2026-11-09 with `--simulate` on this laptop, **on slow Wi-Fi**.
Everything ran on the CPU; the RTX 4060 laptop GPU was unused. Each test clip
is a Windows voice saying "Hey Jarvis" and then the request. The clock starts
when the wake word fires, and "speech end" is when you stop talking. Every
request is logged to `state/latency.csv`.

**Wake word → first spoken word: 6.7–7.9 s for a quick question.** That
includes the 2.6 s spent saying "What is the capital of France?". The part
that's actually waiting is **3.7–4.8 s from when you stop talking to when the
voice starts**.

| stage | time |
|---|---|
| wake word detected → listening | immediate (80 ms frames) |
| you stop talking → recording ends (VAD silence window) | 0.79 s |
| whisper `base.en`, CPU int8 | 0.56–0.67 s |
| (a) haiku via the warm CLI process: request → first text | 1.8–3.1 s (network-bound) |
| first text → Windows voice audible | 0.39–0.41 s |
| **(a) quick question: speech end → first word** | **3.7–4.8 s** |
| (b) tool question ("what's on my calendar tomorrow?"): speech end → "Let me check." | 1.5–1.6 s |
| (b) speech end → the answer itself (warm agent, haiku, 2 tool calls) | 11.1–11.4 s |
| (c) "start a task in hey claude: count the python files": speech end → "Okay, starting that" | 1.7 s |
| (c) that task's headless run, start → finish | 19 s, then the spoken summary |

`tools\bench_fastpath.py`, same Wi-Fi, time to first text from haiku:

| | Q1 | Q2 | Q3 |
|---|---|---|---|
| new `claude -p` per question | 3.4 s | 3.1 s | 4.6 s |
| one warm process (the default) | 3.3 s | 1.7 s | 2.9 s |

Its first turn is the slow one, so at start-up the app sends each warm process
one throwaway "ok" turn (`[warm] … ready`) before you speak.

Fixes found while measuring:

- **The last sentence waited about 2 s.** The CLI emits its final `result`
  event roughly 2 s after the text ends. A one-sentence reply ("Paris is the
  capital of France.") has no trailing space, so it sat in the sentence buffer
  until then. The app now flushes when the text block closes (first text →
  voice went from 2.43 s to 0.4 s).
- `--exit` test runs quit as soon as "Let me check." finished, which killed
  the agent mid-lookup. Fixed; normal runs were never affected.

Where the rest of the time goes, and the knobs:

- `listen.end_silence_ms` (700) is the biggest fixed cost. 500 feels snappier
  but cuts off slow talkers.
- The model step is mostly the network. With `ANTHROPIC_API_KEY` set, tier
  (a) goes straight to the API and skips the CLI.
- The quick-answer model runs in **one long-lived `claude` process** fed
  through `--input-format stream-json`, so the CLI's start-up is paid once
  instead of per question. It also remembers the conversation, so "and
  Germany?" works. It's recycled every `fast.max_turns` turns.
- Speech is split into sentences as text streams in, so the first sentence
  plays while the rest is still being written.
- Obvious tool questions (Telegram, email, calendar, weather…) are matched
  locally too and go straight to the warm agent, skipping the haiku hop. The
  agent says "Let me check." (`agent.filler`) immediately. With hundreds of
  connector tools loaded, it searches for the right tool first, which is why
  lookups take about 10 s.
- "start a task in …" is matched locally, without a model call. You hear
  "Okay, starting that…" as soon as whisper finishes.

## CI and releases

- **CI** (`.github/workflows/ci.yml`) runs every `tests/test_*.py` suite on
  Windows with Python 3.12 for each push to `main` and each pull request. Only
  CPU packages are installed: no mic, GPU, models or Claude login are needed,
  because the suites mock them. Qt runs offscreen. Run the same thing locally
  with `for %t in (tests\test_*.py) do .venv\Scripts\python %t`.
- **Releases** (`.github/workflows/release.yml`):
  1. Bump `__version__` in `heyclaude/__init__.py` and merge it.
  2. Tag that commit and push the tag:

     ```bash
     git tag v0.4.0
     ```

     ```bash
     git push origin v0.4.0
     ```

  The workflow re-runs the tests, checks that the tag matches `__version__`,
  and publishes a GitHub release. It attaches a source zip and
  `SHA256SUMS.txt`, with notes generated from the merged PRs. A tag with a
  hyphen (`v0.5.0-rc1`) makes a pre-release.
- **Later assets:** the default wake-word model (M2a) and the Windows
  installer (M8) are attached to the same releases when those milestones land.
- Dependabot keeps the workflow actions up to date. Python packages are
  pinned by hand because of the GPU stack.

## Files

```
heyclaude/
  __main__.py     CLI flags, Qt app, wiring (tray, hotkey, settings, single instance, restart)
  assistant.py    the loop: wake → record → STT → brain → speak / spawn; listening on/off,
                  crash restart, latency log
  tray.py         tray icon and menu
  hotkey.py       global push-to-talk hotkey (RegisterHotKey) and hotkey parsing
  holdkey.py      hold-to-keep-listening key (low-level hook, armed only while listening)
  inputbox.py     the hotkey's talk-or-type session (state machine; the island draws it)
  settings_ui.py  the settings window
  winsys.py       Startup shortcut, single-instance lock, "is a process running"
  sysload.py      GPU (NVML) and CPU (psutil) load, sampled in the background
  applog.py       state/logs/app.log (rotating)
  config.py       config.yaml load/save, what applies live vs after a restart
  listen.py       mic (sounddevice), openWakeWord (+ hey_jarvis fallback, quiet while speaking),
                  the utterance recorder, wav simulator
  vad.py          Silero VAD (webrtcvad fallback), end of turn, transcripts at pauses / while talking
  aec.py          echo cancellation (WebRTC AEC3 via livekit) and the barge-in detector
  wake_samples.py record your own "hey claude" clips (state/wake_samples), zip them for training
  stt.py          faster-whisper: GPU by default, CPU when the GPU is busy or fails
  brain.py        router, tier (a) chat (CLI or SDK), tier (b) warm tool agent, TASK/LOOKUP detection,
                  voice task control routes, questions about a task (claude -p --resume)
  conversation.py the shared transcript (what each tier is told), follow-up window, end phrases
  tasks.py        headless claude runs, stream-json parsing, logs, session ids
  tts.py          Kokoro (GPU → CPU → SAPI chain, per-reply device choice), SAPI (direct COM), Piper;
                  sentence streaming, the output stream that feeds the echo canceller
  island.py       the pill, task list, log window
  island_effects.py  M9: paints the tool effects (volume slider, sun/moon, media card...) on the island
  effects.py      M9: tool call -> effect event (the data contract; ui/effects.json maps tools to effects)
  undo.py         M9: "undo that" and the island's Undo button (inverse calls, run locally)
  mcp_windows/    M9: the Windows settings MCP server (audio, display, radios, power, media, apps,
                  clipboard, screen, Settings pages) and its tool registry (allow-list, undo)
  claude_cli.py   finds claude.exe, launch flags
tools/            make_test_wav.py, bench_fastpath.py, trace_agent.py,
                  wake_eval.py (wake word hit rate, false wakes per hour, threshold),
                  stt_eval.py (record your voice; whisper models' speed and word error rate),
                  check_windows_settings.py (M9: every settings tool, live, values restored)
tests/            offline tests (python tests\test_offline.py, tests\test_wake.py, tests\test_conversation.py,
                  tests\test_input.py, tests\test_voice.py, tests\test_windows.py)
ui/               effects.json: which island effect each tool gets (M9)
models/           the trained hey_claude.onnx goes here (git-ignored)
docs/             training a "hey claude" wake word
```

## Notes and limits (v1)

- **SAPI via pyttsx3:** pyttsx3 2.99's `runAndWait()` hangs after a
  `save_to_file` and can return without speaking when called repeatedly. The
  `sapi` backend therefore drives `SAPI.SpVoice` directly through pywin32
  (which pyttsx3 installs). It's the same voices and engine, with reliable
  async speech and instant stop.
- The wake word is ignored while it speaks and for `wake.speaking_tail_s`
  (0.7 s) after, so a reply that says "Claude" can't wake it. Interrupt with a
  click or the hotkey; `wake.suppress_while_speaking: false` brings back
  interrupting by voice.
- Only one quick question is handled at a time. Tasks run in parallel (up to
  `tasks.max_parallel`).
- Started from inside a Claude Code session, hey-claude strips the
  parent-session variables (`CLAUDE_CODE_SESSION_ID` etc.) so each task is
  its own top-level session.
- With the default VAD, steady background sound (music, a video playing) can
  count as speech, so a "Talk now" with nobody talking may record until
  `listen.max_seconds` and whisper may invent a phrase. Silero VAD is planned
  in M4.
