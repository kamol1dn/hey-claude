# Plugin API

hey-claude's features snap in like Lego: **one plugin = one folder with one manifest**
(`plugin.json`). A plugin can give the assistant new tools, draw its own cards and
animations on the island, answer some requests instantly without a model, show something
on the island *without being asked* (a "live activity"), and add its own settings. Every
feature hey-claude already has is a plugin too, so each can be switched off in Settings.

This page is the contract. Code: `heyclaude/plugins.py` (the registry),
`heyclaude/activities.py` (live activities), built-in manifests in `plugins/`, tests in
`tests/test_plugins.py`.

## 1. Where plugins live

| Folder | What | In git |
|---|---|---|
| `plugins/<id>/` | built-in plugins (shipped, changed by PR) | yes |
| `state/plugins/<id>/` | your own and installed plugins | no (`state/` is git-ignored) |

Built-ins load first; a plugin in `state/plugins/` can't take a built-in's id. The
folder name must equal the plugin's `id`.

Built-in manifests are thin: they *point at* code that already lives elsewhere
(`widgets/weather/`, `heyclaude/mcp_info/convert.py`, `characters/echo/`). A plugin of your
own carries everything in its folder.

## 2. The manifest

```json
{
  "id": "lyrics",                       // a-z, 0-9, - or _; the folder's name
  "api": "1.0",                         // the plugin API it was written for (major.minor)
  "name": "Lyrics",
  "description": "Synced lyrics on the island while music plays.",
  "version": "0.3.0",                   // the plugin's own version (free form)
  "author": "you",
  "icon": "icon.svg",                   // a file in the folder, or a bundled service icon name ("spotify")
  "glyph": "music",                     // drawn when there is no icon
  "permissions": ["network", "background"],
  "contributes": { ... }                // see below
}
```

**Versioning** works like `ui/states.json`: `api` is major.minor and a plugin loads when the
major matches this hey-claude's (`plugins.API_VERSION`, now `1.0`). A minor bump only *adds*
contribution kinds (or optional fields). A host that meets a kind it doesn't know ignores
it and logs it once, so a plugin written for 1.3 still loads on a 1.0 host with what that
host understands, and a 1.0 plugin loads unchanged forever on 1.x. A field that changes
meaning is a major bump; then old plugins show as "incompatible" in Settings instead of
half-working. The same idea is used below for presentation: custom QML falls back to a
template, a template falls back to a plain line, a card falls back to the text card.

## 3. What a plugin can contribute

| Key | Value | What happens |
|---|---|---|
| `widgets` | card ids | Cards in `<plugin>/widgets/<id>/` (`widget.json` + `Widget.qml` + optional `adapter.py`, exactly the M5 widget format) join the island's cards. Built-ins list the existing `widgets/<id>` they own. |
| `tools` | tool names or patterns | The tools the plugin owns: `mcp__info__calculate`, a whole server `mcp__telegram`, or a glob `mcp__lyrics__get_*`. Switched off, they are not started, not offered to the agent and not allowed. |
| `mcp_servers` | `{name: {command, args, env}}` | MCP servers the plugin brings, started with the tool agent. `${PLUGIN_DIR}` and `${PYTHON}` (hey-claude's interpreter) are substituted. Their tools must be listed under `tools`. |
| `read_only_tools` | tool names | Tools that only look things up: pre-allowed. Every other tool of the plugin **asks on the island** (the approval cards). |
| `prompt` | one sentence | Added to the tool agent's system prompt ("For song lyrics use mcp__lyrics__get_lyrics."). |
| `intents` | intent patterns | Built-ins: which instant commands (`media.*`, `timer.set`) belong to it. |
| `instant` | `instant.py` | Your own instant commands: `match(text) -> {"say": str, "widget": {...}} or None`, asked after the built-in ones, in the request's path (budget 30 ms). |
| `routes` | `["weather"]` | Built-ins: hey-claude's own local routes it owns. |
| `characters` | pack ids | Character packs in `<plugin>/characters/<id>/` (the M6 format). |
| `icons` | `{server: {name, icon}}` | The name and icon the island shows while the agent uses that server. |
| `live_activities` | list | What it may show unasked (section 4). |
| `service` | `service.py` | Its background part: `start(ctx)` and optional `stop()`; it pushes live activities. Needs the `background` permission. |
| `settings` | list | Its settings, for the Settings page (section 6). |

Island themes are a likely next kind (`themes`, API 1.1); they would follow
`ui/themes/<id>/theme.json` and the same state fallbacks.

## 4. Live activities

A live activity is something a plugin keeps on the island while it is true: the song and
its current lyric line, a ringing phone, a running timer, "Physics in 20 min". The plugin
declares each kind in its manifest and its service drives it.

```json
"live_activities": [
  {"id": "line", "template": "media", "priority": "active",
   "qml": "Lyrics.qml",                                  // optional: its own look
   "actions": [{"id": "hide", "label": "Hide", "voice": ["hide the lyrics"]}]}
]
```

```python
def start(ctx):                        # service.py, in its own thread
    key = ctx.activities.start("line", {"title": t, "artist": a, "line": first},
                               on_action=lambda action: "Hidden." if action == "hide" else None)
    while ctx.enabled:                 # returns once the plugin is switched off
        ctx.activities.update("line", {"line": next_line()})    # merge; replace=True swaps
        ...
    ctx.activities.end("line")
```

`start / update / end` are the whole API (`update` also takes `priority=`, `alert=True|False`
and `ttl_s=`). An activity can only be one the manifest declared, and never above the
manifest's priority or frame rate.

**Templates** the island draws by itself: `glance` (icon + line), `progress`, `countdown`
(from an end time, so the island ticks the seconds itself), `media` (title, artist, cover,
progress, a line), `call` (caller, answer / decline). A plugin's own `qml` falls back to its
template if it fails to load, and every template falls back to `glance`.

**Presentation.** The island shows at most two: the *primary* in the pill, **compact** (one
line next to the ember) or **expanded** (the plugin's component, with its buttons); and a
*secondary* as a **minimal** dot or glyph on the side. The rest wait their turn.

**Priority and arbitration** (`ActivityHub.layout()`):

| priority | value | e.g. |
|---|---|---|
| ambient | 10 | next calendar event |
| info | 30 | a delivery on its way |
| active | 50 | music, a running timer |
| *a conversation* | *70* | *hey-claude listening, thinking, talking, an approval card* |
| alert | 80 | an incoming call, a timer that just ended |
| critical | 95 | reserved for hey-claude itself |

1. The highest priority is primary, the next is secondary.
2. Equal priority: the one that started first keeps its place (nothing flaps back and forth).
3. While hey-claude is in a conversation it owns the island: activities at or below 70 are
   hidden (not ended) and come back after. Only `alert` and above break in.
4. An activity that alerts (`alert=True`) is shown expanded at once; after 30 s it settles to
   compact, except the `call` template, which rings until answered or ended.
5. The user can open any activity (hover or click); while one is expanded there is no
   secondary.
6. A plugin may run at most 4 activities; a broken one is ended, never retried in a loop.

**Voice.** Each action may list phrases. While an activity is primary, saying one ("answer",
"pick up", "decline") runs that action locally before the normal routing (only an approval
card's spoken yes / no comes first); its handler's return value is spoken.

**Frame-rate budget** (numbers from `ui/states.json` budgets):

- At rest the island runs at **0 fps**: an activity that isn't changing costs nothing.
- A **discrete update** (a new lyric line, a new minute) animates for 0.4 s at the live rate,
  then returns to 0 fps. Updates closer than 0.25 s are coalesced into one.
- **Continuous animation** (a waveform, a pulsing ring) must be declared: `"fps": 8,
  "fps_reason": "audio waveform"`. It is capped at the idle budget (8 fps) below `active` and
  the antic budget (30 fps) from `active` up, and it stops with reduced motion or a hidden
  island. The hub publishes the frame rate the island may run at (`layout()["fps"]`).
- Prefer data the island can animate itself (an end time, a start and duration) over
  pushing many updates.

## 5. Lifecycle and failure isolation

1. **Discover**: at start, every `*/plugin.json` in `plugins/` then `state/plugins/` is read
   and validated. A bad one (unreadable JSON, wrong id, incompatible API, missing file) is
   skipped, listed in Settings as *broken* or *incompatible* with the reasons, and nothing
   else is affected.
2. **Enable**: built-in plugins are on by default; `plugins.disabled` in `config.yaml` lists
   the ones switched off. **Your own plugins (`state/plugins/`) start off**, because they run
   arbitrary code: Settings > Plugins shows them with their permissions, and switching one on
   adds it to `plugins.enabled`. Core plugins (the text card, the clock, "teach it") can't be
   switched off.
3. **Switch off / on without a restart**: the card stops (an answer that asks for it shows its
   text instead), its instant commands and routes stop claiming requests (they go on to
   Claude), its character pack goes back to the ember, its service gets `stop()` and its
   activities end. Tools: the tool agent's warm process is closed and gets the new tool list
   (like the daily restart); the next request starts it, so that one request is a few
   seconds slower.
4. **Failure isolation**: everything a plugin runs goes through a `try`. If its `instant.py`
   or service raises, the plugin is switched off **for this session** (shown as *failed*,
   not saved) and the request carries on. Its widget adapter failing shows the text card. An
   MCP server is a separate process: if it crashes, only its tools are gone.

## 6. Settings

```json
"settings": [
  {"key": "provider", "type": "enum", "options": ["lrclib", "musixmatch"], "label": "Lyrics from", "default": "lrclib"},
  {"key": "token", "type": "secret", "label": "API token", "help": "Only needed for Musixmatch."}
]
```

Types: `string number integer boolean enum secret`. Values live at
`plugins.settings.<id>.<key>` in `config.yaml`; a service reads them with
`ctx.setting("provider")`. Built-in plugins point at their existing keys instead
(`"config_key": "weather.location"`), so nothing moved.

## 7. Permissions: what is and isn't protected

A plugin is **arbitrary Python and QML running inside hey-claude**. It can do anything your
user account can. hey-claude does not sandbox it, and pretending otherwise would be worse
than saying so. What the API does:

- **Declared permissions** (`network`, `personal_data`, `acts`, `background`, `screen`) are
  shown in Settings before you switch a plugin on. They are a statement, not enforcement:
  read a plugin's code (or trust its author) before installing it.
- **Tools that act go through approvals.** Only tools a plugin declares `read_only_tools`
  are pre-allowed for the agent; every other tool of a plugin's MCP server shows an approval
  card on the island (Allow / Deny / Always, by click or voice), the same cards as
  `heyclaude/hookrelay/`. PowerShell and the registry are never pre-allowed, for plugins
  either.
- **MCP servers run as separate processes**: a crash or hang there can't take the island down.
- **What can be enforced** inside the process: a plugin only drives its own declared
  activities, never above its declared priority or frame rate, at most four at once; a
  plugin can't take a built-in id or one of hey-claude's MCP server names; card data is
  checked against the card's schema and only declared fields reach the QML.
- **What can't**: Python in a service or `instant.py` (it could read files, open sockets,
  block a thread), and QML (it runs in the island's scene). A future step can move services
  out of process (each a small process speaking JSON over stdio, like MCP), which would make
  the `background` part crash-proof too; the API above doesn't change for that.

## 8. A marketplace, later

Minimal version, not built: a curated `index.json` in a git repo listing
`{id, name, description, repo, commit, permissions}`. "Install" = `git clone` that repo at that
exact commit into `state/plugins/<id>/`, show its permissions, and leave it **off** until the
user switches it on. Updating = moving to a newer reviewed commit. Curation (someone reads the
code before a commit is listed) is the security model, as section 7 explains why it has to be.

## 9. Worked examples

### Now playing with synced lyrics

```json
{
  "id": "lyrics", "api": "1.0", "name": "Lyrics", "version": "0.1.0", "author": "you",
  "description": "The current song's lyrics, line by line, on the island while music plays.",
  "icon": "icon.svg", "permissions": ["network", "background"],
  "contributes": {
    "service": "service.py",
    "live_activities": [
      {"id": "line", "template": "media", "priority": "active", "qml": "LyricLine.qml",
       "actions": [{"id": "hide", "label": "Hide", "voice": ["hide the lyrics"]},
                   {"id": "pause", "label": "Pause", "voice": ["pause"]}]}
    ],
    "tools": ["mcp__lyrics"], "read_only_tools": ["mcp__lyrics__get_lyrics"],
    "mcp_servers": {"lyrics": {"command": "${PYTHON}", "args": ["${PLUGIN_DIR}/server.py"]}},
    "prompt": "For the words of a song use mcp__lyrics__get_lyrics.",
    "widgets": ["lyrics"],
    "settings": [{"key": "source", "type": "enum", "options": ["lrclib"], "label": "Lyrics from", "default": "lrclib"}]
  }
}
```

The service watches what's playing (the same Windows media session the `now_playing` card
reads), fetches timed lyrics once per song, and calls `update("line", {"line": ...})` at each
timestamp: one discrete update every few seconds, 0 fps in between. `fps` stays 0. Asked
"what are the lyrics?", the agent uses the read-only tool and shows the `lyrics` card.

### Phone

```json
{
  "id": "phone", "api": "1.0", "name": "Phone", "version": "0.1.0", "author": "you",
  "description": "Calls from your phone on the island: answer or decline by click or voice, and \"call Mum\".",
  "glyph": "phone", "permissions": ["personal_data", "acts", "background"],
  "contributes": {
    "service": "service.py",
    "live_activities": [
      {"id": "incoming", "template": "call", "priority": "alert", "qml": "Ringing.qml",
       "fps": 8, "fps_reason": "the ringing pulse while it rings",
       "actions": [{"id": "answer", "label": "Answer", "voice": ["answer", "pick up"]},
                   {"id": "decline", "label": "Decline", "voice": ["decline", "not now"]}]},
      {"id": "in_call", "template": "countdown", "priority": "active",
       "actions": [{"id": "hang_up", "label": "Hang up", "voice": ["hang up"]}]}
    ],
    "tools": ["mcp__phone"], "read_only_tools": ["mcp__phone__find_contact"],
    "mcp_servers": {"phone": {"command": "${PYTHON}", "args": ["${PLUGIN_DIR}/server.py"]}},
    "prompt": "To call someone use mcp__phone__find_contact, then mcp__phone__call.",
    "instant": "instant.py",
    "settings": [{"key": "device", "type": "string", "label": "Paired phone"}]
  }
}
```

A ring starts `incoming` with `alert=True`: it breaks into a conversation (80 > 70), shows
expanded with Answer / Decline, and "answer" said aloud runs the action. Answering ends it
and starts `in_call` (a counter the island ticks itself). "Call Mum" goes to the agent:
`find_contact` is read-only, `call` acts, so it shows an approval card first. `instant.py`
can claim "decline" or "hang up" even when no activity is showing.

### Next calendar event (glance)

`{"id": "next", "template": "glance", "priority": "ambient"}` and a service that checks the
calendar every few minutes and calls `start("next", {"text": "Physics in 20 min"}, ttl_s=1200)`.
Ambient: shown only when nothing else is, hidden during a conversation, gone when it's over.

## 10. Built-in plugins

| id | switching it off removes |
|---|---|
| `core` *(always on)* | — (text, code, link, image and task cards; clock; "teach it") |
| `weather` | weather card, weather tools (built-in and weather-mcp), the local weather route |
| `media` | now-playing card, media instant commands, media tools, Spotify play/search |
| `pc_control` | volume / brightness / theme / Wi-Fi / app / Settings / lock instant commands and the matching `mcp__windows__*` tools |
| `clipboard` | clipboard card and tools |
| `screen_control` | Windows-MCP (`mcp__windows-mcp`) |
| `system` | system-status card and tool |
| `timers` | timer and reminders cards, timer instant commands |
| `convert`, `words`, `directions`, `timetable`, `deadlines` | that card and its `mcp__info__*` tools |
| `news` | headlines card and its prompt (web search itself stays) |
| `email` | email card and the Gmail connector's tools |
| `calendar` | the agenda card |
| `telegram` | Telegram cards and tools (the desktop server isn't imported) |
| `character-<id>` | that character pack (the ember shows instead) |

## 11. For the Settings page

```python
from heyclaude import plugins
reg = plugins.get()
for p in reg.list():          # PluginInfo: id, name, description, icon_url, glyph, version, author,
    ...                       # builtin, core, can_disable, enabled, status, problems, kinds,
                              # contributes, permissions [{id, text}], settings [schema], dir; .as_dict()
reg.set_enabled("telegram", False)          # saves plugins.disabled (plugins.enabled for yours), notifies, applies live
reg.feature_on("media")                     # hey-claude's own code: on, or no such plugin
reg.subscribe(lambda pid, on: ...)          # any thread
reg.settings_schema("weather")              # [{key, type, label, help, default, options, config_key}]
reg.setting("weather", "location"); reg.set_setting("weather", "unit", "F")
```

If Settings saves the whole config instead, the app calls `reg.sync()` (it does in
`Assistant.apply_config` for any `plugins.*` key) and the same notifications go out. That is
what Settings > Plugins does: its switches are staged like every other setting and applied
by Save (writing `plugins.disabled` and `plugins.enabled`); Close without saving drops them.

With `pc_control` or `media` switched off, the no-model "control" shortcut stops claiming
their phrases ("volume 30", "next song"), the quick model no longer offers to control those
things, and the tool agent's prompt leaves out their lines (music and Spotify with `media` off).
Switching off the character pack that is in use puts the ember back.

## 12. Island seam (status)

Built: the hub, arbitration, budgets, voice actions, and its Python side in
`heyclaude/island_qml.py`: `model.liveActivity` (the hub's layout, updated on the UI thread),
`model.activityAction(key, action)` (a button), `model.activityExpand(key)` (hover / click;
`""` closes), the busy modes (a conversation hides activities below alert) and reduced motion.

Still to do in `ui/island/` (QML only, no Python changes needed): draw
`liveActivity.primary` in the resting pill (compact: glyph + one line; expanded: the plugin's
`qml` in a `Loader`, falling back to the built-in template named by `template`, then
`fallback`, then `glance`), `liveActivity.secondary` as a minimal glyph, the action buttons
calling `model.activityAction`, hover calling `model.activityExpand`, and the frame-rate rule:
animate a changed `seq` for the transition, otherwise run at `liveActivity.fps` (0 at rest).
