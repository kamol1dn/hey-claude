"""One conversation shared by every tier, and the follow-up window.

`Transcript` keeps the rolling list of turns (what the user said, what was
answered, and which tier answered). Whichever tier answers next gets the
turns it hasn't seen, so "what's on my calendar?" (tool agent) → "and the
weather then?" (quick model, or the agent again) keeps context:

* The warm processes (quick model, tool agent) remember their own turns, so
  each is only sent the turns that *other* tiers answered since it last spoke.
  When a process was restarted (its `epoch` changed) it gets the whole window.
* A task gets the whole window appended to its prompt.

The window is capped by `conversation.max_turns` and `conversation.max_tokens`
(estimated at 4 characters a token) and starts over after
`conversation.idle_reset_s` of silence, or on "start fresh".

With `conversation.save` on, each turn is appended to
`state/conversations/<date>.jsonl` (git-ignored) and restored on the next
start if it's recent; with it off, nothing is written.

`FollowUp` decides when the assistant listens again without the wake word:
after an answer, once its own voice (and the SpeakingGate tail) has finished,
for `conversation.followup_s` seconds.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .config import STATE_DIR

CONV_DIR = STATE_DIR / "conversations"

DEFAULTS = dict(followup_s=5.0, max_turns=8, max_tokens=1500, idle_reset_s=600, save=False)


def _c(cfg: dict | None, key: str):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


@dataclass
class Turn:
    id: int
    user: str
    reply: str = ""
    tier: str = ""                   # fast | agent | task | task-ask | local
    t: float = field(default_factory=time.time)
    task_id: str | None = None       # a task this turn started or talked about


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Transcript:
    def __init__(self, cfg: dict | None = None, clock=time.time, save_dir=None):
        self.cfg = cfg if cfg is not None else {}
        self.clock = clock
        self.save_dir = save_dir if save_dir is not None else CONV_DIR
        self.turns: list[Turn] = []
        self.conv = 1                    # bumped by reset(); tiers told to drop older context
        self._next = 1
        self.cursors: dict[str, tuple[int, int, int]] = {}   # consumer -> (epoch, conv, last turn id seen)
        self.lock = threading.RLock()
        if self.saving:
            self._restore()

    @property
    def saving(self) -> bool:
        return bool(_c(self.cfg, "save"))

    # -- window -------------------------------------------------------------
    def window(self) -> list[Turn]:
        """The turns still in context: last max_turns, trimmed to max_tokens."""
        with self.lock:
            turns = self.turns[-int(_c(self.cfg, "max_turns")):]
            budget = int(_c(self.cfg, "max_tokens")) * 4
            out, used = [], 0
            for t in reversed(turns):
                n = len(t.user) + len(t.reply) + 20
                if out and used + n > budget:
                    break
                out.append(t)
                used += n
            return list(reversed(out))

    def expire_if_idle(self) -> bool:
        """Start over when the last turn is older than idle_reset_s."""
        with self.lock:
            if self.turns and self.clock() - self.turns[-1].t > float(_c(self.cfg, "idle_reset_s")):
                self.reset("idle")
                return True
            return False

    def reset(self, why: str = "fresh"):
        with self.lock:
            if self.turns:
                print(f"[conversation] new conversation ({why}); {len(self.turns)} turns dropped")
            self.turns = []
            self.conv += 1

    # -- recording ------------------------------------------------------------
    def add(self, user: str, reply: str, tier: str, task_id: str | None = None) -> Turn:
        with self.lock:
            t = Turn(id=self._next, user=user.strip(), reply=reply.strip(), tier=tier, t=self.clock(),
                     task_id=task_id)
            self._next += 1
            self.turns.append(t)
            keep = max(int(_c(self.cfg, "max_turns")) * 3, 30)
            self.turns = self.turns[-keep:]
        if self.saving:
            self._append(t)
        return t

    def next_id(self) -> int:
        return self._next

    # -- what each tier is told -------------------------------------------------
    @staticmethod
    def _lines(turns: list[Turn], you: str = "You") -> str:
        out = []
        for t in turns:
            if t.user:                   # (a task announcement has no user line)
                out.append(f"User: {_clip(t.user, 400)}")
            if t.reply:
                out.append(f"{you}: {_clip(t.reply, 600)}")
        return "\n".join(out)

    def prompt_for(self, consumer: str, epoch: int, text: str) -> str:
        """The message to send `consumer` (a warm process at restart count
        `epoch`) for the request `text`: the turns it hasn't seen, then the
        request. Marks everything so far as seen by it."""
        with self.lock:
            prev = self.cursors.get(consumer)
            if prev and prev[0] == epoch and prev[1] == self.conv:
                # Same process, same conversation: it remembers its own turns.
                missed = [t for t in self.window() if t.id > prev[2] and t.tier != consumer]
                note = ""
            else:
                missed = self.window()
                # Same process but the conversation was reset: say so.
                note = ("(New conversation: ignore everything said before this message.)\n"
                        if prev and prev[0] == epoch else "")
            self.cursors[consumer] = (epoch, self.conv, self._next - 1)
        if not missed:
            return note + text
        head = ("(Earlier in this voice conversation, answered by another part of the assistant:\n"
                if any(t.tier != consumer for t in missed) else "(Earlier in this voice conversation:\n")
        return note + head + self._lines(missed) + ")\n\n" + text

    def task_context(self) -> str:
        """Appended to a task's prompt: the conversation that led to it."""
        w = self.window()
        if not w:
            return ""
        return ("This task was asked for by voice. The recent conversation, for context "
                "(the task is the last request; earlier turns may explain what 'it', 'that' "
                "or 'there' refer to):\n" + self._lines(w, "Assistant"))

    def last_task_id(self, within: int = 2) -> str | None:
        """The task the last few turns were about, if any."""
        with self.lock:
            for t in reversed(self.turns[-within:]):
                if t.task_id:
                    return t.task_id
        return None

    # -- persistence ---------------------------------------------------------
    def _file(self, day: str | None = None):
        return self.save_dir / f"{day or datetime.fromtimestamp(self.clock()).strftime('%Y-%m-%d')}.jsonl"

    def _append(self, t: Turn):
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            with open(self._file(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(asdict(t), conv=self.conv), ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[conversation] couldn't save: {e}")

    def _restore(self):
        """Pick up today's conversation after a restart, if it's still fresh."""
        f = self._file()
        if not f.exists():
            return
        try:
            rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            return
        if not rows or self.clock() - rows[-1].get("t", 0) > float(_c(self.cfg, "idle_reset_s")):
            return
        last_conv = rows[-1].get("conv")
        rows = [r for r in rows if r.get("conv") == last_conv][-int(_c(self.cfg, "max_turns")):]
        for r in rows:
            self.turns.append(Turn(id=self._next, user=r.get("user", ""), reply=r.get("reply", ""),
                                   tier=r.get("tier", ""), t=r.get("t", 0), task_id=r.get("task_id")))
            self._next += 1
        print(f"[conversation] restored {len(rows)} turns from {f.name}")


def session_name(cfg: dict | None, clock=time.time) -> str | None:
    """The tool agent's session name when conversations are saved: one per day."""
    if not _c(cfg, "save"):
        return None
    return "hey-claude " + datetime.fromtimestamp(clock()).strftime("%Y-%m-%d")


# -- follow-up window ----------------------------------------------------------

# A follow-up made only of these words ends the window ("ok thanks, that's all").
_END_WORDS = {"thanks", "thank", "cheers", "bye", "goodbye", "nope", "no", "nothing", "done", "stop",
              "nevermind", "enough", "ok", "okay", "cool", "great", "alright", "perfect", "nice"}
_FILLER = {"you", "a", "lot", "very", "much", "so", "that", "thats", "that's", "is", "it", "all", "else",
           "i'm", "im", "i", "am", "we're", "were", "good", "never", "mind", "see", "got", "for", "now",
           "right", "all", "claude", "jarvis", "fine", "then", "the", "help", "oh", "yeah", "sure"}


_WAKE_LEAD = re.compile(r"^\s*(?:hey|hi|okay|ok)[ ,]+(?:claude|clyde|cloud|jarvis|jarvi)\b[ ,.!?]*", re.I)


def is_end_phrase(text: str) -> bool:
    """'thanks', 'that's all', 'no thanks'... end the follow-up window."""
    words = re.sub(r"[^a-z' ]+", " ", text.lower().replace("’", "'")).split()
    if not words or len(words) > 8:
        return False
    if " ".join(words) in ("that's all", "thats all", "that's it", "thats it", "never mind", "i'm good",
                           "im good", "all good", "i'm done", "we're done"):
        return True
    return all(w in _END_WORDS or w in _FILLER for w in words) and any(w in _END_WORDS for w in words)


def strip_wake(text: str) -> str:
    """'Hey Jarvis, what time is it' -> 'what time is it' (wake word said inside the window)."""
    out = _WAKE_LEAD.sub("", text, count=1).strip()
    if out == text.strip():
        return out
    return out[:1].upper() + out[1:]


class FollowUp:
    """idle → armed (answer given, waiting for its voice to finish) → open
    (listening without the wake word for followup_s) → idle.

    `gate` is listen.SpeakingGate: the window opens only once `gate.busy()`
    is false, i.e. TTS has finished and the speaking tail has passed, even
    when wake.suppress_while_speaking is off. `busy` is a callable that says
    the assistant is still working (a reply being generated, speech queued).
    """

    ARM_TIMEOUT = 120.0     # an answer whose speech never finishes: give up

    def __init__(self, cfg: dict | None, gate, busy=lambda: False, clock=time.monotonic):
        self.cfg = cfg if cfg is not None else {}
        self.gate, self.busy, self.clock = gate, busy, clock
        self.state = "idle"
        self.armed_at = 0.0
        self.opened_at = 0.0
        self.opens = 0          # how many windows have opened (the simulator plays one clip per window)

    @property
    def length(self) -> float:
        return float(_c(self.cfg, "followup_s"))

    def arm(self):
        if self.length > 0:
            self.state = "armed"
            self.armed_at = self.clock()

    def cancel(self):
        self.state = "idle"

    def poll(self) -> bool:
        """Call on every audio block. True once, when the window opens."""
        if self.state != "armed":
            return False
        if self.clock() - self.armed_at > self.ARM_TIMEOUT:
            self.state = "idle"
            return False
        if self.gate.busy() or self.busy():
            return False
        self.state = "open"
        self.opened_at = self.clock()
        self.opens += 1
        return True

    @property
    def is_open(self) -> bool:
        return self.state == "open" and self.remaining() > 0

    def remaining(self) -> float:
        if self.state != "open":
            return 0.0
        return max(0.0, self.length - (self.clock() - self.opened_at))

    def close(self):
        self.state = "idle"
