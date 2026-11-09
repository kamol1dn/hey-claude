"""The hotkey's "talk or type" input session, as plain logic (no Qt).

Pressing the hotkey opens the island in an input state: the mic opens for one
request AND a focused text field appears. Then:

- typing (the first edit) cancels the mic: it's a typed request;
- Enter sends the text, Shift+Enter is a new line, Esc cancels;
- speaking works as usual; when the speech is transcribed the session closes
  (the assistant already handles the spoken request) -- or, if nothing was
  heard, the field stays open for typing with the mic off;
- the hotkey again while it's open cancels it.

The island wires keys and the hotkey to these methods and implements the
callbacks; the assistant implements open_mic / cancel_mic / submit.
"""
from __future__ import annotations

from typing import Callable

CLOSED, LISTENING, TYPING, IDLE_OPEN = "closed", "listening", "typing", "open"


class InputSession:
    def __init__(self, open_mic: Callable[[], None], cancel_mic: Callable[[], None],
                 submit: Callable[[str], None], show: Callable[[], None], close: Callable[[bool], None]):
        """close(submitted) hides the field and gives focus back."""
        self._open_mic, self._cancel_mic, self._submit = open_mic, cancel_mic, submit
        self._show, self._close = show, close
        self.state = CLOSED

    @property
    def is_open(self) -> bool:
        return self.state != CLOSED

    @property
    def mic_on(self) -> bool:
        return self.state == LISTENING

    # -- events --------------------------------------------------------------------
    def hotkey(self):
        """Toggle: open, or cancel if already open."""
        if self.is_open:
            self.cancel()
        else:
            self.state = LISTENING
            self._show()
            self._open_mic()

    def edited(self, text: str):
        """The text in the field changed (by the user)."""
        if self.state == LISTENING and text.strip():
            self.state = TYPING
            self._cancel_mic()            # typing wins: the mic closes right away
        elif self.state == IDLE_OPEN and text.strip():
            self.state = TYPING

    def key_enter(self, text: str, shift: bool) -> bool:
        """Returns True if the key was handled (Enter sends), False to let the
        field insert a new line (Shift+Enter)."""
        if not self.is_open or shift:
            return False
        if not text.strip():
            return True                   # nothing to send: ignore a bare Enter
        if self.state == LISTENING:
            self._cancel_mic()
        self.state = CLOSED
        self._close(True)
        self._submit(text.strip())
        return True

    def key_escape(self):
        if self.is_open:
            self.cancel()

    def cancel(self):
        if self.state == LISTENING:
            self._cancel_mic()
        self.state = CLOSED
        self._close(False)

    def heard(self, text: str):
        """The spoken request was transcribed (the assistant sends it itself)."""
        if self.state != LISTENING:
            return
        if text.strip():
            self.state = CLOSED
            self._close(True)
        else:
            self.state = IDLE_OPEN        # nothing heard: mic is off, the field stays for typing
