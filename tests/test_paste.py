"""Dictate anywhere: clipboard + Ctrl+V paste, with fakes (no clipboard, keyboard or UIA).

    .venv\\Scripts\\python tests\\test_paste.py           # offline tests (CI)
    .venv\\Scripts\\python tests\\test_paste.py --live    # also really paste into Notepad

The live check opens Notepad on a temp file, pastes into that tab, reads it
back, checks the clipboard came back, then closes the tab without saving. It
uses (and afterwards restores) the real clipboard and needs an unlocked desktop.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from heyclaude import paste as P  # noqa: E402
from heyclaude.paste import (  # noqa: E402
    VK_CONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN, VK_MASK, VK_RCONTROL, VK_V,
    Clipboard, ClipboardBusy, PasteResult, paste_text, plan_paste_keys, send_paste,
)

CTRL_V = [(VK_CONTROL, False), (VK_V, False), (VK_V, True), (VK_CONTROL, True)]


class FakeClipboard:
    def __init__(self, log, content="old"):
        self.log = log
        self.content = content
        self.seq = 100

    def snapshot(self):
        self.log.append("snapshot")
        return [(13, self.content)]

    def sequence_number(self):
        return self.seq

    def set_text(self, text):
        self.log.append(("set", text))
        self.content = text
        self.seq += 1
        return self.seq

    def restore(self, snap):
        self.log.append("restore")
        self.content = snap[0][1]
        self.seq += 1

    def other_app_copies(self, text):
        self.content = text
        self.seq += 1


def _kit(log, *, password=False, send=None, content="old"):
    cb = FakeClipboard(log, content)

    def is_pw():
        log.append("is_password")
        return password

    def default_send():
        log.append("send")
        return ["lalt"]

    return dict(clipboard=cb, send=send or default_send, is_password=is_pw,
                sleep=lambda s: log.append(("sleep", s)), foreground=lambda: "notepad.exe"), cb


def test_order_of_operations_and_result_fields():
    log = []
    kit, cb = _kit(log)
    r = paste_text("hi there", **kit)
    assert log == ["is_password", "snapshot", ("set", "hi there"), ("sleep", 0.02), "send",
                   ("sleep", 0.25), "restore"], log
    assert isinstance(r, PasteResult)
    assert r.ok and r.reason == "pasted" and r.app == "notepad.exe"
    assert r.released == ["lalt"] and r.restored
    assert isinstance(r.ms, float) and r.ms >= 0
    assert cb.content == "old"


def test_restore_delay_is_configurable():
    log = []
    kit, _cb = _kit(log)
    paste_text("x", restore_delay_s=0.6, **kit)
    assert ("sleep", 0.6) in log


def test_restore_happens_when_send_fails():
    log = []

    def boom():
        log.append("send")
        raise OSError("SendInput inserted 0 of 4 key events")

    kit, cb = _kit(log, send=boom)
    r = paste_text("hello", **kit)
    assert not r.ok and "paste failed" in r.reason and "SendInput" in r.reason
    assert r.restored and cb.content == "old"
    assert log[-1] == "restore"
    assert ("sleep", 0.25) not in log          # no point waiting for a paste that never went


def test_no_restore_when_clipboard_changed_meanwhile():
    log = []
    holder = {}

    def send():
        log.append("send")
        holder["cb"].other_app_copies("the user copied this")
        return []

    kit, cb = _kit(log, send=send)
    holder["cb"] = cb
    r = paste_text("dictated", **kit)
    assert r.ok and not r.restored
    assert "changed meanwhile" in r.reason
    assert "restore" not in log
    assert cb.content == "the user copied this"


def test_password_field_skips_paste_and_leaves_clipboard():
    log = []
    kit, cb = _kit(log, password=True)
    seq = cb.seq
    r = paste_text("secret words", **kit)
    assert not r.ok and r.reason == "password field" and r.app == "notepad.exe"
    assert log == ["is_password"]
    assert cb.content == "old" and cb.seq == seq


def test_password_unknown_or_erroring_still_pastes():
    for pw in (None, False):
        log = []
        kit, _cb = _kit(log, password=pw)
        assert paste_text("x", **kit).ok
    log = []
    kit, _cb = _kit(log)

    def raising():
        raise RuntimeError("uia gone")

    kit["is_password"] = raising
    assert paste_text("x", **kit).ok


def test_clipboard_busy_means_no_paste():
    log = []
    kit, cb = _kit(log)

    def busy():
        raise ClipboardBusy("held by another app")

    cb.snapshot = busy
    r = paste_text("x", **kit)
    assert not r.ok and "clipboard unavailable" in r.reason
    assert "send" not in log and cb.content == "old"


def test_set_text_failure_restores_only_if_touched():
    # failed before touching anything: nothing to restore
    log = []
    kit, cb = _kit(log)

    def fail_clean(text):
        raise ClipboardBusy("nope")

    cb.set_text = fail_clean
    r = paste_text("x", **kit)
    assert not r.ok and "set clipboard failed" in r.reason and not r.restored
    assert "send" not in log
    # emptied the clipboard, then failed: put the old content back
    log = []
    kit, cb = _kit(log)

    def fail_dirty(text):
        cb.content, cb.seq = None, cb.seq + 1
        raise MemoryError("GlobalAlloc failed")

    cb.set_text = fail_dirty
    r = paste_text("x", **kit)
    assert not r.ok and r.restored and cb.content == "old"


def test_empty_text_does_nothing():
    log = []
    kit, cb = _kit(log)
    r = paste_text("", **kit)
    assert not r.ok and log == [] and cb.content == "old"


def test_foreground_failure_is_blank_app():
    log = []
    kit, _cb = _kit(log)

    def bad():
        raise OSError("no window")

    kit["foreground"] = bad
    r = paste_text("x", **kit)
    assert r.ok and r.app == ""


def test_pastes_never_interleave():
    log = []
    gate = threading.Event()
    entered = threading.Event()
    cb = FakeClipboard(log)

    def slow_send():
        log.append("send-1")
        entered.set()
        gate.wait(2)
        return []

    common = dict(clipboard=cb, is_password=lambda: False, sleep=lambda s: None,
                  foreground=lambda: "")
    t1 = threading.Thread(target=paste_text, args=("one",), kwargs=dict(send=slow_send, **common))
    t1.start()
    entered.wait(2)
    t2 = threading.Thread(target=paste_text, args=("two",),
                          kwargs=dict(send=lambda: log.append("send-2") or [], **common))
    t2.start()
    time.sleep(0.1)
    assert ("set", "two") not in log           # second paste is waiting on the lock
    gate.set()
    t1.join(2)
    t2.join(2)
    first_restore = log.index("restore")
    assert log.index(("set", "two")) > first_restore
    assert log[-1] == "restore" and cb.content == "old"


# ---- keystrokes / modifier release

def _gaks(*down):
    return lambda vk: -32768 if vk in down else 0


def test_plain_ctrl_v_when_nothing_held():
    events, released = plan_paste_keys(_gaks())
    assert events == CTRL_V and released == []


def test_alt_released_first_with_menu_mask():
    events, released = plan_paste_keys(_gaks(VK_LMENU))
    assert released == ["lalt"]
    assert events == [(VK_MASK, False), (VK_MASK, True), (VK_LMENU, True)] + CTRL_V


def test_win_and_shift_released_win_first():
    events, released = plan_paste_keys(_gaks(VK_LSHIFT, VK_LWIN))
    assert released == ["lwin", "lshift"]
    assert events[:2] == [(VK_MASK, False), (VK_MASK, True)]
    assert events[2:4] == [(VK_LWIN, True), (VK_LSHIFT, True)]
    assert events[4:] == CTRL_V


def test_held_ctrl_released_without_mask():
    events, released = plan_paste_keys(_gaks(VK_RCONTROL))
    assert released == ["rctrl"]
    assert events == [(VK_RCONTROL, True)] + CTRL_V


def test_low_bit_only_is_not_down():
    # GetAsyncKeyState's low bit means "pressed since last call", not "down now"
    events, released = plan_paste_keys(lambda vk: 1)
    assert released == [] and events == CTRL_V


def test_send_paste_uses_injected_and_checks_count():
    sent = []
    released = send_paste(_gaks(VK_LMENU), lambda ev: sent.append(ev) or len(ev))
    assert released == ["lalt"] and sent[0][-4:] == CTRL_V
    try:
        send_paste(_gaks(), lambda ev: 0)
    except OSError as e:
        assert "0 of 4" in str(e)
    else:
        raise AssertionError("short SendInput must raise")


# ---- Clipboard class logic with a fake win32clipboard

class FakeWC:
    def __init__(self, formats, busy=0):
        self.formats = formats
        self.calls = []
        self.busy = busy

    def OpenClipboard(self, hwnd=None):
        if self.busy:
            self.busy -= 1
            raise OSError("access denied")
        self.calls.append("open")

    def CloseClipboard(self):
        self.calls.append("close")

    def EmptyClipboard(self):
        self.calls.append("empty")

    def EnumClipboardFormats(self, fmt):
        ids = [f for f, _ in self.formats]
        i = ids.index(fmt) + 1 if fmt else 0
        return ids[i] if i < len(ids) else 0

    def RegisterClipboardFormat(self, name):
        return {P.EXCLUDE_FORMAT: 0xC001, P.HISTORY_FORMAT: 0xC002, P.CLOUD_FORMAT: 0xC003}[name]


def _fake_clipboard(wc):
    cb = Clipboard(retries=3, retry_delay_s=0)
    cb._wc = lambda: wc
    puts = []
    cb._put = lambda _wc, fmt, data: puts.append((fmt, data))
    data = dict(wc.formats)

    def read(_wc, fmt):
        if isinstance(data[fmt], Exception):
            raise data[fmt]
        return data[fmt]

    cb._read = read
    return cb, puts


def test_snapshot_skips_handle_formats_and_failures():
    wc = FakeWC([(13, b"h\0i\0\0\0"), (2, b"bmp"), (14, b"emf"), (8, b"dib"), (0x301, b"gdi"),
                 (0xC123, RuntimeError("won't render")), (16, b"\x09\x04\0\0")])
    cb, _ = _fake_clipboard(wc)
    snap = cb.snapshot()
    assert snap == [(13, b"h\0i\0\0\0"), (8, b"dib"), (16, b"\x09\x04\0\0")]
    assert wc.calls == ["open", "close"]


def test_restore_puts_data_then_our_markers():
    wc = FakeWC([])
    cb, puts = _fake_clipboard(wc)
    cb.restore([(13, b"a\0\0\0"), (0xC002, b"\x01\0\0\0"), (8, b"dib")])
    assert wc.calls == ["open", "empty", "close"]
    assert puts[:2] == [(13, b"a\0\0\0"), (8, b"dib")]       # its own history flag is dropped
    assert dict(puts[2:]) == {0xC001: b"\0\0\0\0", 0xC002: b"\0\0\0\0", 0xC003: b"\0\0\0\0"}


def test_restore_of_empty_clipboard_just_empties():
    wc = FakeWC([])
    cb, puts = _fake_clipboard(wc)
    cb.restore([])
    assert wc.calls == ["open", "empty", "close"] and puts == []


def test_set_text_marks_and_terminates():
    wc = FakeWC([])
    cb, puts = _fake_clipboard(wc)
    cb.sequence_number = lambda: 7
    assert cb.set_text("hé") == 7
    assert puts[-1] == (13, "hé\0".encode("utf-16-le"))
    assert {f for f, _ in puts[:-1]} == {0xC001, 0xC002, 0xC003}


def test_open_retries_then_gives_up():
    wc = FakeWC([], busy=2)
    cb, _ = _fake_clipboard(wc)
    cb.snapshot()                                   # two failures, third try works
    wc.busy = 5
    try:
        cb.snapshot()
    except ClipboardBusy:
        pass
    else:
        raise AssertionError("expected ClipboardBusy")


def test_handle_format_ranges():
    for f in (2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E, 0x200, 0x2FF, 0x300, 0x3FF):
        assert P._is_handle_format(f), f
    for f in (1, 7, 8, 13, 15, 16, 17, 0xC001):
        assert not P._is_handle_format(f), f


# ---- live (opt-in): really paste into Notepad

def _live():  # noqa: C901 - a linear script
    import ctypes
    import subprocess
    import tempfile

    import comtypes.client
    import win32con
    import win32gui

    def notepads():
        out = []
        win32gui.EnumWindows(lambda h, _: out.append(h) if win32gui.GetClassName(h) == "Notepad"
                             else None, None)
        return out

    if notepads():
        print("live: SKIP - Notepad is already open; close it first (we won't touch your windows)")
        return

    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    P._automation()                                   # COM + UIA on this thread
    uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)

    def editor_text(hw):
        kids = []
        win32gui.EnumChildWindows(hw, lambda h, _: kids.append(h)
                                  if win32gui.GetClassName(h) == "RichEditD2DPT" else None, None)
        u32 = ctypes.WinDLL("user32")
        u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
        u32.SendMessageW.restype = ctypes.c_ssize_t
        for k in kids:
            n = u32.SendMessageW(k, win32con.WM_GETTEXTLENGTH, 0, None)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.SendMessageW(k, win32con.WM_GETTEXT, n + 1, buf)
            return buf.value
        return None

    def keys(*vks):
        ev = [(vk, False) for vk in vks] + [(vk, True) for vk in reversed(vks)]
        P._send_input(ev)

    def ensure_foreground(hw):
        for _ in range(20):
            if win32gui.GetForegroundWindow() == hw:
                return True
            P._send_input([(VK_MASK, False), (VK_MASK, True)])   # unlocks SetForegroundWindow
            try:
                win32gui.SetForegroundWindow(hw)
            except Exception:
                pass
            time.sleep(0.1)
        return win32gui.GetForegroundWindow() == hw

    tmpdir = tempfile.mkdtemp(prefix="hc_paste_")
    name = f"hc_paste_live_{os.getpid()}.txt"
    path = os.path.join(tmpdir, name)
    open(path, "w").close()
    real = Clipboard()
    user_clip = real.snapshot()
    hw = None
    failures = []
    try:
        subprocess.Popen(["notepad.exe", path])
        t0 = time.time()
        while time.time() - t0 < 15 and hw is None:
            for h in notepads():
                if name in win32gui.GetWindowText(h):
                    hw = h
            time.sleep(0.1)
        assert hw, "Notepad window with our file didn't appear"
        assert ensure_foreground(hw), "couldn't bring our Notepad to the front"
        time.sleep(0.5)                               # let the text box take focus

        app = P.foreground_app()
        pw_times = []
        for _ in range(5):
            t = time.perf_counter()
            pw = P.focused_is_password()
            pw_times.append((time.perf_counter() - t) * 1000)
        print(f"live: foreground_app={app!r} focused_is_password={pw} "
              f"({min(pw_times):.1f}-{max(pw_times):.1f} ms)")
        if app != "notepad.exe":
            failures.append(f"foreground_app {app!r}")
        if pw is not False:
            failures.append(f"focused_is_password {pw!r}")

        real.set_text("clipboard before")
        title = win32gui.GetWindowText(hw)
        assert name in title and win32gui.GetForegroundWindow() == hw, "focus moved; not pasting"
        marks = {}

        def timed_send():
            marks["send"] = time.perf_counter()
            return send_paste()

        t = time.perf_counter()
        r = paste_text("hello from hey-claude", send=timed_send)
        print(f"live: {r}  (Ctrl+V sent {(marks.get('send', t) - t) * 1000:.1f} ms in)")
        if not (r.ok and r.restored):
            failures.append(f"paste_text {r}")

        text = None
        for _ in range(20):
            text = editor_text(hw)
            if text == "hello from hey-claude":
                break
            time.sleep(0.05)
        print(f"live: Notepad text {text!r}")
        if text != "hello from hey-claude":
            failures.append(f"Notepad text {text!r}")
        back = real.get_text()
        print(f"live: clipboard after {back!r}")
        if back != "clipboard before":
            failures.append(f"clipboard after {back!r}")
    finally:
        # close OUR tab without saving; Notepad may have restored other tabs of the user's session
        if hw and win32gui.IsWindow(hw) and name in win32gui.GetWindowText(hw):
            if ensure_foreground(hw) and name in win32gui.GetWindowText(hw):
                keys(VK_CONTROL, 0x57)                # Ctrl+W
                dont = None
                for _ in range(30):
                    time.sleep(0.1)
                    if not win32gui.IsWindow(hw) or name not in win32gui.GetWindowText(hw):
                        break
                    root = uia.ElementFromHandle(hw)
                    btns = root.FindAll(4, uia.CreatePropertyCondition(30003, 50000))  # buttons
                    for i in range(btns.Length):
                        b = btns.GetElement(i)
                        if (b.CurrentName or "").replace("’", "'").lower() == "don't save":
                            dont = b
                    if dont is not None:
                        dont.GetCurrentPattern(10000).QueryInterface(mod.IUIAutomationInvokePattern).Invoke()
                        dont = None
                        time.sleep(0.5)
        if hw and win32gui.IsWindow(hw):
            if name in win32gui.GetWindowText(hw):
                failures.append("our Notepad tab is still open; please close it (Don't save)")
            else:
                # only the user's restored session tabs are left: close the window if none is modified
                root = uia.ElementFromHandle(hw)
                tabs = root.FindAll(4, uia.CreatePropertyCondition(30003, 50019))      # tab items
                names = [tabs.GetElement(i).CurrentName or "" for i in range(tabs.Length)]
                if all(n.endswith("Unmodified.") for n in names):
                    win32gui.PostMessage(hw, win32con.WM_CLOSE, 0, 0)
                    for _ in range(30):
                        if not win32gui.IsWindow(hw):
                            break
                        time.sleep(0.1)
                else:
                    print("live: left the Notepad window open (it holds modified tabs of yours)")
        real.restore(user_clip)
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError as e:
            failures.append(f"temp file cleanup: {e}")
    print("live: Notepad closed" if not (hw and win32gui.IsWindow(hw)) else "live: Notepad still open")
    if failures:
        raise AssertionError("live: " + "; ".join(failures))
    print("live: ok")


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    if "--live" in sys.argv:
        _live()
