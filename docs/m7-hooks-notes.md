# M7 design notes: watching Claude Code sessions through hooks

Notes for M7 (Tasks v2). They come from studying how Coucou does it on Windows
([Louis-CFM/coucou](https://github.com/Louis-CFM/coucou) at `b1bc09de7f`, MIT
code; its names, character and sounds are not MIT), plus our own handoff test
on 2026-11-09. File references point into the Coucou repo. Copy *designs*, and
keep Coucou's MIT notice on any code that is actually copied.

## What we verified ourselves (2026-11-09, CLI 2.1.317)

- Hooks fire for headless `claude -p` runs: SessionStart, UserPromptSubmit,
  PreToolUse, Stop, all with `session_id`, `cwd` and `transcript_path`.
- `claude --desktop --resume <id>` hands a CLI session to the Claude desktop
  app under the same id (`local_<id>`), with its full context. It refuses
  redirected output, so start it in its own console window.
- The same user hooks fire inside the desktop session afterwards (SessionStart
  on handoff, then UserPromptSubmit / Stop per turn).

## Shape

- A **tiny relay** that Claude Code runs as the hook command, plus a
  **named-pipe server** inside hey-claude. Never make the GUI process itself the
  hook.
- Pipe: `\\.\pipe\hey-claude-<user SID>`, created with
  `FILE_FLAG_FIRST_PIPE_INSTANCE`, a fresh instance per connection and after
  every failed `connect()` (Coucou #853).
- **Give it an explicit DACL for the current user only, and check the client.**
  Coucou doesn't (their #1076): the default DACL let any same-user process
  inject a fake PermissionRequest. The relay also checks the server's token SID
  before sending (Coucou `hook/src/win.rs:37-82`).
- One JSON line each way. The app sends only `allow` / `deny` / `always` /
  `{"answers":…}`. Only the relay builds Claude Code's `hookSpecificOutput`
  JSON, so that format lives in one place.

## Relay rules

- **Exit 0 and print nothing on any failure**: app not running, connect
  timeout (300 ms), garbage on stdin. Never print anything that allows a tool
  without a human click (Coucou has a test for exactly this).
- Every blocking call runs on a worker thread under a deadline:
  - fire-and-forget events: ≤ 2 s in total;
  - decisions: 110 s.
- Timeout ladder **app 108 s < relay 110 s < hook 120 s**, so each layer
  answers before the one above gives up.
- Watch the parent process (handle + `WaitForSingleObject` every 100 ms). When
  the user answers in the terminal, Claude Code kills the shell above the relay.
  The relay exits, the server's pipe read sees EOF, and the island drops the
  card.
- Cap strings at 2 KB, except PostToolUse of Edit/MultiEdit/Write, where the
  edit text is kept up to 256 KB per field and 512 KB in total (for diffs). Mark
  truncation with a flag. Drop `tool_response`.
- Strip a UTF-8 BOM from stdin, and write UTF-8 bytes to stdout.
- Python relay: stdlib only, `-I -S`, no PyInstaller `--onefile` (it unpacks on
  every start). A small native exe is better if startup time matters.
- Add context from the environment: `CLAUDE_CODE_ENTRYPOINT == "claude-desktop"`
  marks desktop-app sessions; also `WT_SESSION`, `TERM_PROGRAM`, `VSCODE_PID`.

## Hook installation (`~/.claude/settings.json`, or `$CLAUDE_CONFIG_DIR`)

- Events, with timeouts in seconds:

  | event | timeout |
  |---|---|
  | SessionStart, SessionEnd, UserPromptSubmit | 10 |
  | PreToolUse, PostToolUse, PostToolUseFailure | 10 |
  | **PermissionRequest** | **120** |
  | Notification, Stop, StopFailure | 10 |
  | SubagentStart, SubagentStop | 10 |

- Command: the relay path in double quotes with forward slashes, then the event
  name. Hooks run through Git Bash in the CLI and through cmd/PowerShell in the
  desktop app, and this form works in all three.
- Merge without clobbering: remove only **our own individual hook entries**,
  matched by our exact relay path, then append ours.
  - Coucou removes whole groups that contain a marker substring, which can
    delete a user's hook that shares the group. Don't.
  - Refuse to touch a file whose `hooks` isn't the documented shape.
- Writer:
  1. strict parse (a missing file is `{}`, an unreadable file is an error),
     with the BOM stripped;
  2. show the user a diff and take a fingerprint of the bytes shown;
  3. refuse if the file changed since;
  4. dated backup created with `create_new`;
  5. temp file + rename, following symlinks.
- Uninstall restores the file exactly, and must run **before** the relay binary
  is deleted (Coucou #855: hooks pointing at a deleted exe make every event
  fail).
- Install the relay into a folder only hey-claude writes. Compare a hash, not
  size + mtime (#1076).

## Approvals

- Two-phase wait: the island acknowledges that the card is visible within
  ~800 ms (otherwise decline at once, and the terminal asks), then the long
  wait.
- Queue at most 5 pending requests (overflow → terminal), with a timer per
  request id.
- "Settled elsewhere": drop a card when the same session sends Stop,
  StopFailure, UserPromptSubmit, SessionEnd, or a PostToolUse with the same tool
  and target.
- Replies:
  ```
  allow : {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
  deny  : {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"Denied from hey-claude"}}}
  always: ... "decision":{"behavior":"allow","updatedPermissions": <payload.permission_suggestions>}
  ```
  For ExitPlanMode, include `updatedInput: tool_input` (allow is ignored
  without it).
- AskUserQuestion: keep the untruncated `tool_input`, validate answers (one per
  question, keyed by the question text, labels from the options, a list for
  multi-select), and reply with an allow plus `updatedInput: {questions, answers}`.
  Leave it to the terminal if a question was truncated or has < 2 options.
- The desktop app honours these decisions and answers (Coucou 0.5.8).
- Voice approval is a convenience only: anything flagged dangerous needs a
  click.

## Session tracking

- Key everything by `session_id`, never one entry per agent kind (Coucou #604),
  and clear only that session on SessionEnd.
- Ticker from PreToolUse: `<Runs|Reads|Writes|Edits|Searches> · <command[:40] |
  basename(file) | query[:40]>`; keep the last ~20 steps.
- Diffs from PostToolUse Edit/MultiEdit/Write: an LCS line diff, showing counts
  only for huge files. Up to 50 per session, 1 h TTL.
- Stop → finished (`last_assistant_message` is the final line), then idle after
  ~5 s. StopFailure → error. Notification with "rate limit" → rate limited.
- Session title: read the transcript tail once per event for the last
  `custom-title`, else `ai-title`. No continuous tailing.
- Ignore SubagentStop with an empty `agent_type`; that's Claude Code's own
  helper (#840).
- hey-claude's own internal `claude -p` calls (the quick tier, task questions)
  run with `--setting-sources ""`, so they don't show up as sessions.

## Jump to the session

- When an unknown `session_id` first arrives, take the relay's PID with
  `GetNamedPipeClientProcessId`. Walk up its parents (ToolHelp, depth ≤ 16,
  stopping at explorer/svchost/…) and keep the nearest one that owns a visible
  titled window. Prefer the window whose title contains the folder name.
- Raise it with `ShowWindow` + `AttachThreadInput` + `SetForegroundWindow`
  (the island doesn't take focus, so Windows' foreground lock otherwise
  refuses).
- Classic conhost windows can't be found this way. Fall back to `code <cwd>`.
- Desktop-app sessions: `claude://claude.ai/epitaxy/local_<id>` (to verify).
  Otherwise focus a `claude.exe` window, then start the desktop app, then
  `ShellExecute("claude://")`.
- New terminal session: `wt.exe -d <folder> pwsh -NoExit -Command <script>`,
  with the prompt passed through a temp file (wt tabs don't inherit the
  environment).
