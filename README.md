# hey-claude

An always-listening voice assistant for Windows 11. Say the wake word and a
small black "island" drops in at the top of the screen. Ask a quick question
and a small model answers out loud in a second or two. Say *"start a task in
\<project\>: \<thing\>"* and a headless Claude Code run starts in that folder.
The island tracks the run, and when it finishes a one-sentence summary is read
out.

Everything runs in one Python process. Speech-to-text uses the GPU when it
can (and the CPU whenever Premiere Pro is open, so Premiere keeps the GPU);
everything else runs on the CPU. Claude is driven through the `claude` CLI you're already signed in
to. No API key is needed.

There are three tiers:

| tier | what it's for | runs on |
|---|---|---|
| **(a) quick answers** | "what's 15% of 80", "capital of Peru" | Anthropic API (haiku) if `ANTHROPIC_API_KEY` is set, else one warm, tool-less `claude` process |
| **(b) your tools** | "who messaged me on Telegram", "what's on my calendar" | **one** warm Claude Code process with tools and MCP servers, started at launch and reused |
| **(c) real work** | "start a task in inha app: fix the login screen" | a **new** headless Claude Code session in that folder, per task |

```
mic ─▶ openWakeWord ─▶ webrtcvad (record until 0.7 s silence) ─▶ faster-whisper (GPU, CPU fallback)
                                                                    │
                                     ┌──────────────────────────────┘
                                     ▼
                     local router ── "start a task in X: Y" ───────────▶ (c) claude -p "Y" (cwd = X) ─▶ island task list
                          │       ── "telegram / calendar / email…" ──┐       stream-json → log, session id,
                          ▼                                           │       SUMMARY line → spoken when done
     (a) haiku: SDK, or a warm tool-less `claude` process             │
              │  answer │ "TASK <project> :: <task>" │ "LOOKUP" ──────┤
              │                                                       ▼
              │                         (b) warm Claude Code agent: tools + MCP + claude.ai connectors
              ▼                                                       │  final answer only
        sentence splitter ─▶ Windows SAPI voice ◀─────────────────────┘
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

3. **Config:** copy `config.example.yaml` to `config.yaml` and fill in
   `projects:` (spoken name → folder). Everything else has working defaults.

4. **Run:**

   ```bash
   run.cmd
   ```

   First start downloads the wake-word models (~6 MB) and whisper
   `large-v3-turbo` for the GPU (~1.6 GB) or `base.en` for the CPU (~140 MB).
   After that, startup takes about 5 s.

   To run it all day without a terminal, tick **Start with Windows** in the
   tray's Settings… (see below), or start it silently once with
   `.venv\Scripts\pythonw.exe -m heyclaude`.

### Flags

| flag | what it does |
|---|---|
| `--gpu` / `--cpu` | whisper on the GPU / the CPU, overriding `stt.device` (the GPU still yields to Premiere). |
| `--text` | type requests instead of speaking (no mic, no whisper). |
| `--simulate x.wav` | use a 16 kHz mono wav as the microphone. Good for testing and timing. |
| `--exit` | with `--simulate`: quit after the request (and any task it started) is done. |
| `--muted` | start with listening off (mic closed); the hotkey and "Talk now" still work. |
| `--quit` | ask the running hey-claude to quit, then exit. |
| `--config f.yaml` | use another config file instead of `config.yaml`. |
| `--no-ui` / `--no-tray` | no island / no tray icon. |

`--simulate` and `--text` runs are treated as tests: they skip the
single-instance check, the global hotkey and the Startup-shortcut sync, so
they can run next to a normal copy.

## Tray, hotkey and settings

hey-claude lives in the **tray** (the orange dot; grey with a slash while
listening is off). Left-click it to talk. Right-click for:

| menu item | what it does |
|---|---|
| **Talk now** | listen for one request without the wake word (same as clicking the island or the hotkey) |
| **Listening** | wake word on/off. Off **closes the microphone stream**, so Windows' mic-in-use indicator goes away; the island turns into a muted grey pill |
| **Pause for 1 hour** | listening off, back on by itself after an hour ("Resume listening" ends it early) |
| **Settings…** | the settings window, below |
| **Open logs** | `state\logs\` (the app log and every task's log) |
| **Quit** | |

While listening is off, **Talk now**, the hotkey and clicking the pill still
work: the mic opens for that one request and closes again.

**Push-to-talk hotkey:** `Ctrl+Alt+Space` by default (`app.hotkey`), a
system-wide hotkey through `RegisterHotKey`, no admin rights needed. If
another app already owns the combination, hey-claude shows a tray balloon
and runs without one. On this machine PowerToys holds `Ctrl+Alt+Space`;
`Ctrl+Shift+Space`, `Win+Alt+Space`, `Ctrl+Alt+H` and `F9` were free.

**Settings window** (tray → Settings…) edits `config.yaml`, creating it from
`config.example.yaml` the first time. Tabs: General (listening at start,
hotkey with a Record… button, start with Windows), Voice & wake (wake model
and threshold; whisper device, GPU and CPU models, GPU precision, Premiere
yield; voice, rate, a test button), Models (quick-answer backend and models, the tool agent on/off,
model and filler line, task model, permission mode and parallel limit),
Projects (spoken name → folder, with a folder picker) and Permissions (the
agent's and the tasks' `allowed_tools`, one per line). Fields tagged
**restart** take effect after a restart, and the window offers a **Restart
now** button after saving them. Everything else applies on save:

| applies on save | needs a restart |
|---|---|
| wake threshold and cooldown, `listen.*`, `tasks.*` (permission mode, allowed tools, model, parallel limit), projects for "start a task in …", the agent's filler line, `island.*`, hotkey, start with Windows, `stt.yield_to_premiere` | wake model, the rest of `stt.*` (device, models), `tts.*` (voice, rate, backend), `fast.*`, the agent's on/off, model and allowed tools, `claude.exe` |

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

**Speech-to-text device** (`stt.device`, default `gpu`): whisper's GPU model
(`stt.gpu_model`, `large-v3-turbo` in float16) is loaded once at start-up and
stays in VRAM, so there's no per-request load. It drops to the CPU model
(`stt.cpu_model`, `base.en` int8, loaded only when first needed) when:

| when | for how long | island / `[ready]` shows |
|---|---|---|
| Premiere Pro is running (`stt.yield_to_premiere`, on by default) | until it closes; the GPU model is unloaded so Premiere gets the VRAM back, and reloaded in the background when it closes | `cpu (fallback: premiere)` |
| CUDA isn't available | this session | `cpu (fallback: no cuda)` |
| the GPU model fails to load (missing cuBLAS/cuDNN, ...) or runs out of memory | this session | `cpu (fallback: gpu load failed)` / `out of memory` |
| a transcription raises a CUDA error | this session; that utterance is retried on the CPU | `cpu (fallback: cuda error)` |

With Premiere already open at start-up it doesn't even probe CUDA. Premiere
is checked before each utterance and every 10 s (one process-list scan,
about 12 ms). The active device shows on the idle island ("hey jarvis · gpu")
and in its right-click menu, and every switch is logged as `[stt] now …`.
`--cpu` or `stt.device: cpu` keeps the GPU out of it entirely. GPU needs:
an NVIDIA driver plus CUDA 12 cuBLAS (`cublas64_12.dll`) on PATH; the
ctranslate2 wheel already ships cuDNN 9. If cuBLAS 12 is missing (a CUDA 13
toolkit alone isn't enough), the load fails and it falls back to the CPU.

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
| *hey jarvis … what's 15% of 80?* | quick answer from haiku, spoken |
| *hey jarvis … start a task in hey claude: add a license file* | headless Claude Code run in `D:\coding\hey-claude` |
| *hey jarvis … in the inha app, find why login is slow* | the model notices it's real work and turns it into a task |
| *hey jarvis … who messaged me on Telegram today?* | "Let me check." → the warm tool agent answers (needs the MCP set up, see below) |
| *hey jarvis … how are my tasks going?* | spoken status of running tasks |
| *hey jarvis* while it's talking | cuts it off and listens again |
| click the idle pill, or press the hotkey (`Ctrl+Alt+Space`) | talk without the wake word |
| click while speaking | stop speaking |
| right-click the island | menu: talk now / stop / open logs / quit (the tray menu has the rest) |

The wake word is the stock **"hey jarvis"** model until a "hey claude" model
is trained. See [docs/train-hey-claude.md](docs/train-hey-claude.md). Project
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
- `fast.backend`: `auto` uses the Anthropic SDK (`claude-haiku-5-5`) when
  `ANTHROPIC_API_KEY` is set, and the CLI otherwise. Set `sdk_model:
  claude-sonnet-5-5` for smarter, slower answers.
- `stt.device`, `stt.gpu_model`, `stt.cpu_model`: see "Speech-to-text
  device" above. On the CPU, `base.en` is the default (≈0.6 s per request).
  `small.en` is more accurate and slower; `tiny.en` is faster and sloppier.
- `tts.backend`: `sapi` (default), `piper` or `kokoro`. Piper and Kokoro
  are wired in but untested. If one fails to load, it falls back to SAPI.

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

## Conversations and sessions

- **Follow-ups work in tiers (a) and (b).** Each tier is one long-lived
  process that keeps the conversation, so "what's the capital of France?" →
  "and Germany?" works. The fast process is recycled after 20 turns and the
  agent after 30. Both start fresh each time the app starts.
- **The two tiers don't share memory yet.** A follow-up that switches tiers
  ("what's on my calendar?" → "and what's the weather then?") starts without
  the other tier's context. This is on the roadmap.
- **What gets saved:** the fast process runs with `--no-session-persistence`,
  so nothing is written to disk. The agent **is** a saved Claude Code session
  named "hey-claude assistant", one per app launch, stored under
  `~\.claude\projects\D--coding-hey-claude\`. You can `claude --resume` it,
  but like tasks it doesn't appear in the desktop app's Code tab. Each task
  is its own saved session.

## Measured latency

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

## Files

```
heyclaude/
  __main__.py     CLI flags, Qt app, wiring (tray, hotkey, settings, single instance, restart)
  assistant.py    the loop: wake → record → STT → brain → speak / spawn; listening on/off,
                  crash restart, latency log
  tray.py         tray icon and menu
  hotkey.py       global push-to-talk hotkey (RegisterHotKey) and hotkey parsing
  settings_ui.py  the settings window
  winsys.py       Startup shortcut, single-instance lock, "is Premiere running"
  applog.py       state/logs/app.log (rotating)
  config.py       config.yaml load/save, what applies live vs after a restart
  listen.py       mic (sounddevice), openWakeWord, webrtcvad recorder, wav simulator
  stt.py          faster-whisper: GPU first, CPU fallback, yields to Premiere
  brain.py        router, tier (a) chat (CLI or SDK), tier (b) warm tool agent, TASK/LOOKUP detection
  tasks.py        headless claude runs, stream-json parsing, logs, session ids
  tts.py          SAPI (direct COM), Piper, Kokoro; sentence streaming
  island.py       the pill, task list, log window
  claude_cli.py   finds claude.exe, launch flags
tools/            make_test_wav.py, bench_fastpath.py, trace_agent.py
tests/            offline tests (python tests\test_offline.py)
docs/             training a "hey claude" wake word
```

## Notes and limits (v1)

- **SAPI via pyttsx3:** pyttsx3 2.99's `runAndWait()` hangs after a
  `save_to_file` and can return without speaking when called repeatedly. The
  `sapi` backend therefore drives `SAPI.SpVoice` directly through pywin32
  (which pyttsx3 installs). It's the same voices and engine, with reliable
  async speech and instant stop.
- The wake word stays live while it speaks (so you can interrupt). "Hey
  Jarvis" never appears in its own replies, so it doesn't trigger itself.
- Only one quick question is handled at a time. Tasks run in parallel (up to
  `tasks.max_parallel`).
- Started from inside a Claude Code session, hey-claude strips the
  parent-session variables (`CLAUDE_CODE_SESSION_ID` etc.) so each task is
  its own top-level session.
- Not built yet: answering follow-up questions *about* a finished task
  (resume it in a terminal for now). See [ROADMAP.md](ROADMAP.md).
- With the default VAD, steady background sound (music, a video playing) can
  count as speech, so a "Talk now" with nobody talking may record until
  `listen.max_seconds` and whisper may invent a phrase. Silero VAD is planned
  in M4.
