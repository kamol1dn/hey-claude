"""The personal packs (ROADMAP M5b): timetable and deadlines. Their tools
(heyclaude/mcp_info/timetable.py, deadlines.py) against SYNTHETIC data written to a temp
dir in the user's real formats (made-up group, subjects, rooms and deadlines: nothing
personal is ever in this repo), the widget adapters, the recorded fixtures (they are
exactly what the tools return on this synthetic data at the recorded moment), the text
fallback, and the cards built on the QML engine. Offline.

    .venv\\Scripts\\python tests\\test_widgets_personal.py
    .venv\\Scripts\\python tests\\test_widgets_personal.py --record     # rewrite the fixtures
"""
import faulthandler
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()
sys.stdout.reconfigure(line_buffering=True)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heyclaude import widgets as W  # noqa: E402
from heyclaude.mcp_info import deadlines as D  # noqa: E402
from heyclaude.mcp_info import timetable as T  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"

# ---- synthetic data in the user's formats (all invented) ------------------------------
# inha_timetable's backend/clean_timetables.py: literal dicts. The import of a module that
# doesn't exist proves the file is read as data and never run.
TIMETABLE_PY = '''from __future__ import annotations
from typing import Dict
import module_that_does_not_exist_so_running_this_would_fail
classes: Dict[str, dict] = {
    "OPT": {"title": "Applied Optics"},
    "DSA2": {"title": "Data Structures II"},
    "LIN": {"title": "Linear Algebra"},
    "ENG3": {"title": "Academic English 3"},
    "CHM": {"title": "General Chemistry"},
    "SIG": {"title": "Signals and Systems"},
}
classrooms: Dict[str, dict] = {"B204": {"seats": 40}, "A108": {"seats": 60}}
group_timetables = {
    "QRS-25-03": [
        {"code": "LIN", "day": "Tue", "start": "09:00", "end": "10:30", "room": "A108"},
        {"code": "OPT", "day": "Tue", "start": "10:45", "end": "12:15", "room": "B204"},
        {"code": "DSA2", "day": "Tue", "start": "14:00", "end": "15:30", "room": "C312 Lab"},
        {"code": "ENG3", "day": "Tue", "start": "15:45", "end": "17:15", "room": "A215"},
        {"code": "SIG", "day": "Wed", "start": "10:45", "end": "12:15", "room": "B110"},
        {"code": "CHM", "day": "Wed", "start": "13:30", "end": "15:00", "room": "D014 Lab"},
        {"code": "LIN", "day": "Thu", "start": "09:00", "end": "10:30", "room": "A108"},
        {"code": "OPT", "day": "Fri", "start": "12:30", "end": "14:00", "room": "B204"},
    ],
    "QRS-25-04": [
        {"code": "CHM", "day": "Mon", "start": "09:00", "end": "10:30", "room": "D014 Lab"},
    ],
}
'''
DEADLINES_MD = """# Deadlines

Things due this term.

- 2026-11-11 09:00 Signals problem set 6
- [ ] 2026-11-12 18:00 Optics lab report
- [x] 2026-11-09 Linear algebra quiz prep
- ~~2026-11-10 Old reading~~
- 2026-11-14 Essay outline: renewable grids
* 18.11.2026 10:30 — Chemistry lab defence
- 2026-11-21 23:00 Data structures project milestone
- 2026-12-30 Far away thing
- 2026-11-10 08:00 Library book return
"""
TUE = "2026-11-10"                                     # a Tuesday


def _synth() -> Path:
    d = Path(tempfile.mkdtemp(prefix="hc_personal_"))
    (d / "backend").mkdir()
    (d / "backend" / "clean_timetables.py").write_text(TIMETABLE_PY, encoding="utf-8")
    (d / "deadlines.md").write_text(DEADLINES_MD, encoding="utf-8")
    return d


SYN = _synth()


def _cfg(**over):
    tt = {"path": str(SYN), "group": "QRS-25-03"}
    tt.update(over.pop("timetable", {}))
    dl = {"path": str(SYN / "deadlines.md")}
    dl.update(over.pop("deadlines", {}))
    return mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": json.dumps({"timetable": tt, "deadlines": dl})})


def _at(mod, when: str):
    t = datetime.fromisoformat(when)
    return mock.patch.object(mod, "_clock", (lambda cfg=None: t) if mod is T else (lambda: t))


def _adapt(wid, result):
    return W._plugin_adapter(W.registry()[wid])({}, result, json.dumps(result))


# ---- the recordings: what the tools return on the synthetic data at fixed moments ---------
RECORDINGS = [
    ("info_timetable_next_class.json", "mcp__info__next_class", {}, T, f"{TUE} 13:52:20", lambda a: T.next_class(),
     "next_class() run for real on a synthetic timetable (invented group, subjects and rooms in the inha_timetable "
     "format), a Tuesday 13:52: a class in 8 minutes, one after it"),
    ("info_timetable_today.json", "mcp__info__timetable_today", {"day": ""}, T, f"{TUE} 14:41:05",
     lambda a: T.timetable_today(**a), "timetable_today() on the same synthetic timetable, Tuesday 14:41: in a class now"),
    ("info_timetable_today_done.json", "mcp__info__timetable_today", {"day": "today"}, T, f"{TUE} 18:20:00",
     lambda a: T.timetable_today(**a),
     "timetable_today() on the synthetic timetable, Tuesday 18:20: the day is over, tomorrow's first class"),
    ("info_deadlines.json", "mcp__info__deadlines", {"days": 14}, D, f"{TUE} 13:52:20", lambda a: D.deadlines(**a),
     "deadlines() run for real on a synthetic notes file (invented items in the documented markdown format)"),
]


def record():
    for name, tool, args, mod, when, call, note in RECORDINGS:
        with _cfg(), _at(mod, when):
            result = call(args)
        text = json.dumps(result, ensure_ascii=False)
        spec = W.from_tool(tool, args, result, text)
        fx = {"note": note, "tool": tool, "args": args, "content": [{"type": "text", "text": text}],
              "expect": {"widget": spec["widget"], "title": W.title_for(W.registry()[spec["widget"]], spec["data"]),
                         "icon": True}}                # the header glyph (cap, hourglass) is the card's icon
        (FIXTURES / name).write_text(json.dumps(fx, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print("recorded", name, fx["expect"])


# ---- manifests -------------------------------------------------------------------------
def test_both_packs_are_private_plugins_with_valid_examples():
    reg = W.registry()
    for wid, tools in (("timetable", {"mcp__info__next_class", "mcp__info__timetable_today"}),
                       ("deadlines", {"mcp__info__deadlines"})):
        man = reg[wid]
        assert man["private"] is True, wid
        assert set(man["triggers"]["tools"]) == tools and man["triggers"]["data"] == "adapter", wid
        assert man["size"]["width"] <= 460 and man["size"]["max_height"] <= 280, wid
        assert W.validate(man["example"], man["schema"]) == [], wid
        spec = W.normalise({"widget": wid, "data": man["example"], "text": ""})
        assert spec["widget"] == wid and spec["data"] == man["example"], wid
    from heyclaude import mcp_info
    assert {"next_class", "timetable_today", "deadlines"} <= set(mcp_info.tools())
    assert "mcp__info__next_class" in mcp_info.prompt() and "mcp__info__deadlines" in mcp_info.prompt()


def test_bad_data_falls_back_to_text():
    for wid, bad in (("timetable", {"label": "Today", "classes": [{"start": "9am", "title": "X"}], "focus": 0,
                                    "focus_state": "next"}),
                     ("timetable", {"classes": []}),
                     ("deadlines", {"window": "next 14 days", "items": [{"title": "X", "left_s": "soon", "urgency": "bad"}]}),
                     ("deadlines", {"window": "next 14 days", "items": [{"title": "X", "left_s": 5, "urgency": "panic"}]})):
        out = W.normalise({"widget": wid, "data": bad, "text": "Next: physics at nine."})
        assert out["widget"] == "text" and out["data"]["text"] == "Next: physics at nine.", (wid, out)
    assert W.normalise({"widget": "deadlines", "data": {"items": []}, "text": ""}) is None


# ---- the timetable tool ----------------------------------------------------------------
def test_timetable_not_set_up_says_so_and_makes_no_card():
    with mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": "{}"}):
        for r in (T.next_class(), T.timetable_today()):
            assert r["setup"] is False and "info.timetable.path" in r["text"], r
            assert W.from_tool("mcp__info__next_class", {}, r, json.dumps(r)) is None


def test_timetable_reads_the_data_module_without_running_it():
    with _cfg(), _at(T, f"{TUE} 13:52:20"):
        v = T.next_class()
    assert v["label"] == "Today" and v["day"] == "Tue" and v["group"] == "QRS-25-03"
    assert [c["state"] for c in v["classes"]] == ["done", "done", "next", "later"]
    assert v["focus"] == 2 and v["focus_state"] == "next" and v["focus_in_s"] == 460
    c = v["classes"][2]
    assert (c["title"], c["room"], c["start"], c["end"], c["code"]) == ("Data Structures II", "C312 Lab", "14:00", "15:30", "DSA2")
    assert v["now"] == "13:52" and abs(v["now_min"] - 832.33) < 0.01
    assert v["text"].startswith("Next: Data Structures II in C312 Lab at 14:00 (in 8 min).")
    assert "Academic English 3 at 15:45 (A215)" in v["text"]


def test_timetable_now_done_tomorrow_and_the_weekend():
    with _cfg():
        with _at(T, f"{TUE} 14:20:00"):
            v = T.next_class()
        assert v["focus_state"] == "now" and v["focus"] == 2 and v["focus_in_s"] == 70 * 60
        assert v["text"].startswith("Now: Data Structures II in C312 Lab until 15:30 (1 h 10 min left).")
        with _at(T, f"{TUE} 18:00:00"):
            v = T.next_class()                         # the day is over: tomorrow's first class
            today = T.timetable_today()                # today, all done, with what comes next
        assert v["label"] == "Tomorrow" and v["day"] == "Wed" and v["focus"] == 0 and v["focus_state"] == "next"
        assert v["focus_in_s"] == (16 * 60 + 45) * 60 and "now" not in v
        assert today["focus"] == -1 and today["focus_state"] == "none"
        assert {c["state"] for c in today["classes"]} == {"done"}
        assert today["after"] == {"label": "Tomorrow", "start": "10:45", "title": "Signals and Systems", "room": "B110",
                                  "in_s": (16 * 60 + 45) * 60}
        with _at(T, "2026-11-14 10:00:00"):            # Saturday: free, the next class is Tuesday's first
            sat = T.timetable_today()
            v = T.next_class()
        assert sat["classes"] == [] and sat["text"].startswith("No classes today.") and sat["after"]["label"] == "Tuesday"
        assert v["label"] == "Tuesday" and v["classes"][0]["title"] == "Linear Algebra" and v["focus_in_s"] == (3 * 24 - 1) * 3600
        with _at(T, f"{TUE} 13:52:20"):
            assert T.timetable_today("thursday")["label"] == "Thursday"
            assert T.timetable_today("tomorrow")["day"] == "Wed"
            assert T.timetable_today("2026-11-13")["classes"][0]["title"] == "Applied Optics"
            assert T.timetable_today("tue")["label"] == "Today"
            try:
                T.timetable_today("someday")
                raise AssertionError("not a day")
            except ValueError:
                pass


def test_timetable_group_drops_extras_and_other_formats():
    with _cfg(timetable={"group": "qrs-25-03", "drop": ["dsa2"],
                         "extra": [{"day": "Tuesday", "start": "16:30", "end": "17:30", "title": "Study group", "room": "Library"}]}), \
            _at(T, f"{TUE} 13:52:20"):
        v = T.timetable_today()
    assert [c["title"] for c in v["classes"]] == ["Linear Algebra", "Applied Optics", "Academic English 3", "Study group"]
    assert v["focus"] == 2 and v["classes"][3]["room"] == "Library"
    for group, msg in (("", "set info.timetable.group"), ("QRS-25-3", "did you mean QRS-25-03")):
        with _cfg(timetable={"group": group}):
            try:
                T.next_class()
                raise AssertionError("no group")
            except LookupError as e:
                assert msg in str(e), e
    with _cfg(timetable={"path": str(SYN / "nowhere")}):
        try:
            T.next_class()
            raise AssertionError("no path")
        except LookupError as e:
            assert "doesn't exist" in str(e)
    # the same data as JSON (tools/regular/clean_timetables.json), and the README's "groups" form
    import ast
    lit = {n.target.id if isinstance(n, ast.AnnAssign) else n.targets[0].id: ast.literal_eval(n.value)
           for n in ast.parse(TIMETABLE_PY).body if isinstance(n, (ast.Assign, ast.AnnAssign))}
    j = SYN / "tt.json"
    j.write_text(json.dumps(lit), encoding="utf-8")
    readme = SYN / "readme_form.json"
    readme.write_text(json.dumps({"groups": {"ZZ-1": {"Tuesday": [
        {"start": "14:00", "end": "15:30", "code": "X1", "title": "Invented Studies", "room": "Z-9"}]}}}), encoding="utf-8")
    with _at(T, f"{TUE} 13:52:20"):
        with _cfg(timetable={"path": str(j)}):
            assert T.next_class()["classes"][2]["title"] == "Data Structures II"
        with _cfg(timetable={"path": str(readme), "group": ""}):          # one group: no need to name it
            v = T.next_class()
            assert v["group"] == "ZZ-1" and v["classes"][0]["title"] == "Invented Studies" and v["focus_in_s"] == 460


# ---- the deadlines tool ----------------------------------------------------------------
def test_deadlines_from_markdown():
    with _cfg(), _at(D, f"{TUE} 13:52:20"):
        v = D.deadlines()
    titles = [i["title"] for i in v["items"]]
    assert titles == ["Library book return", "Signals problem set 6", "Optics lab report", "Essay outline: renewable grids",
                      "Chemistry lab defence", "Data structures project milestone"], titles   # done, struck and far ones are out
    assert [i["urgency"] for i in v["items"]] == ["overdue", "bad", "soon", "later", "later", "later"]
    assert v["items"][0]["left"] == "5 h 52 min ago" and v["items"][1]["left"] == "in 19 h"
    assert v["items"][3]["due"] == "2026-11-14 23:59" and v["items"][4]["due"] == "2026-11-18 10:30"
    assert v["items"][2]["when"] == "Thu 18:00" and v["items"][1]["when"] == "tomorrow 09:00"
    assert v["count"] == 5 and v["overdue"] == 1 and v["window"] == "next 14 days"
    assert v["text"].startswith("5 deadlines in the next 14 days and 1 overdue.")
    with _cfg(deadlines={"overdue_h": 0, "default_time": "09:00"}), _at(D, f"{TUE} 13:52:20"):
        v = D.deadlines(days=3)
    assert [i["title"] for i in v["items"]] == ["Signals problem set 6", "Optics lab report"] and v["window"] == "next 3 days"


def test_deadlines_from_json_and_yaml_and_not_set_up():
    items = [{"due": "2026-11-11 09:00", "title": "Invented task A"}, {"date": "2026-11-12", "time": "10:00", "name": "B"},
             {"due": "2026-11-11", "title": "Done one", "done": True}]
    j = SYN / "dl.json"
    j.write_text(json.dumps({"deadlines": items}), encoding="utf-8")
    y = SYN / "dl.yaml"
    y.write_text("- due: 2026-11-11 09:00\n  title: Invented task A\n- date: 2026-11-12\n  time: '10:00'\n  name: B\n"
                 "- due: 2026-11-11\n  title: Done one\n  done: true\n", encoding="utf-8")
    for p in (j, y):
        with _cfg(deadlines={"path": str(p)}), _at(D, f"{TUE} 13:52:20"):
            v = D.deadlines()
        assert [(i["title"], i["due"]) for i in v["items"]] == [("Invented task A", "2026-11-11 09:00"), ("B", "2026-11-12 10:00")], p
    with mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": "{}"}):
        r = D.deadlines()
        assert r["setup"] is False and "info.deadlines.path" in r["text"]
        assert W.from_tool("mcp__info__deadlines", {}, r, json.dumps(r)) is None
    with _cfg(deadlines={"path": str(SYN / "missing.md")}):
        try:
            D.deadlines()
            raise AssertionError("missing file")
        except LookupError:
            pass


# ---- adapters and fixtures -------------------------------------------------------------
def test_the_adapters_map_the_tool_results():
    with _cfg(), _at(T, f"{TUE} 13:52:20"):
        v = T.next_class()
    d = _adapt("timetable", v)
    assert "group" not in d and "text" not in d                     # the group code stays off the card
    assert d["focus"] == 2 and d["focus_state"] == "next" and d["focus_in_s"] == 460 and d["now"] == "13:52"
    assert d["classes"][2] == {"start": "14:00", "end": "15:30", "title": "Data Structures II", "state": "next",
                               "code": "DSA2", "room": "C312 Lab"}
    assert W.validate(d, W.registry()["timetable"]["schema"]) == []
    assert _adapt("timetable", {"setup": False, "text": "not set up"}) is None
    assert W._plugin_adapter(W.registry()["timetable"])({}, None, "Error: no group") is None
    with _cfg(), _at(D, f"{TUE} 13:52:20"):
        r = D.deadlines()
    r["items"] = r["items"] + [dict(r["items"][-1], title=f"Extra {i}") for i in range(3)]   # 9 items
    d = _adapt("deadlines", r)
    assert len(d["items"]) == 5 and d["more"] == 4 and d["window"] == "next 14 days" and d["days"] == 14
    assert d["items"][0] == {"title": "Library book return", "left_s": -21140, "urgency": "overdue", "when": "today 08:00"}
    assert W.validate(d, W.registry()["deadlines"]["schema"]) == []
    assert _adapt("deadlines", {"setup": False, "text": "x"}) is None


def test_the_fixtures_are_the_tools_on_synthetic_data():
    """Each recorded result is exactly what the tool returns on the synthetic data above at
    the recorded moment: so nothing in it can have come from the user's own files."""
    from heyclaude.effects import parse_result, result_text
    for name, tool, args, mod, when, call, note in RECORDINGS:
        fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        with _cfg(), _at(mod, when):
            assert json.loads(fx["content"][0]["text"]) == call(args), name
        assert fx["tool"] == tool and fx["args"] == args, name
        spec = W.from_tool(tool, args, parse_result(fx["content"]), result_text(fx["content"]))
        assert spec and spec["widget"] == fx["expect"]["widget"], name
        assert W.title_for(W.registry()[spec["widget"]], spec["data"]) == fx["expect"]["title"], name
    t = json.loads((FIXTURES / "info_timetable_today_done.json").read_text(encoding="utf-8"))
    assert W.from_tool(t["tool"], t["args"], parse_result(t["content"]), "")["data"]["after"]["title"] == "Signals and Systems"


# ---- the cards on the QML engine -------------------------------------------------------
_KEEP = []


def _qml(wid, data):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    global _APP, _ENG
    if "_APP" not in globals():
        _APP = QGuiApplication.instance() or QGuiApplication([])
        _ENG = QQmlEngine()
    c = QQmlComponent(_ENG, QUrl.fromLocalFile(str(ROOT / "widgets" / wid / "Widget.qml")))
    o = c.createWithInitialProperties({"data_": data, "wid": "t", "reduced": True})
    assert o is not None, [e.toString() for e in c.errors()]
    QQmlEngine.setObjectOwnership(o, QQmlEngine.ObjectOwnership.CppOwnership)
    _KEEP.append((c, o))
    o.setProperty("width", 408)
    return o


_WALK = b"""import QtQuick
QtObject {
    property var root: null
    // the text of every visible Text under root (Repeater rows included), read in QML
    readonly property var texts: {
        var out = []
        function walk(it) {
            if (!it || it.visible === false) return
            if (typeof it.text === "string" && it.text !== "") out.push(it.text)
            for (var i = 0; i < it.children.length; i++) walk(it.children[i])
        }
        walk(root)
        return out
    }
}
"""


def _texts(o):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent
    c = QQmlComponent(_ENG)
    c.setData(_WALK, QUrl.fromLocalFile(str(ROOT / "tests" / "walk.qml")))
    h = c.createWithInitialProperties({"root": o})
    _KEEP.append((c, h))
    v = h.property("texts")
    return [str(t) for t in (v.toVariant() if hasattr(v, "toVariant") else v)]


def test_the_cards_build_with_the_live_countdowns():
    from heyclaude.effects import parse_result
    for name, want in (("info_timetable_next_class.json", ["NEXT", "Data Structures II", "C312 Lab", "starts in", "7:40",
                                                           "Academic English 3"]),
                       ("info_timetable_today.json", ["NOW", "ends in", "48:55"]),
                       ("info_timetable_today_done.json", ["DONE FOR TODAY", "Signals and Systems", "Tomorrow 10:45",
                                                          "next in", "16 h 25 m"]),
                       ("info_deadlines.json", ["Library book return", "5 h 52 min late", "was due today 08:00",
                                                "in 19 h", "in 2 days", "in 11 days"])):
        fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        spec = W.from_tool(fx["tool"], fx["args"], parse_result(fx["content"]), "")
        o = _qml(spec["widget"], spec["data"])
        texts = _texts(o)
        for t in want:
            assert t in texts, (name, t, texts)
        for t in texts:
            assert "undefined" not in t and "NaN" not in t and "{" not in t, (name, t)
        assert 40 < o.property("implicitHeight") <= 262 - 30, (name, o.property("implicitHeight"))


if __name__ == "__main__":
    if "--record" in sys.argv:
        record()
        sys.exit(0)
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), 0)
    os._exit(0)
