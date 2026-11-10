"""M5b, the "system" group: the system card (mcp__info__system_status) and the reminders
card (the timers' snapshot, "what timers do I have"). Offline: the OS sources are fakes,
no GPU, battery or network is needed.

    .venv\\Scripts\\python tests\\test_widgets_system.py
"""
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import widgets as W  # noqa: E402
from heyclaude.effects import parse_result, result_text  # noqa: E402
from heyclaude.intents import Executor, IntentEngine, Timers  # noqa: E402
from heyclaude.mcp_info import system as SYS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"


def _fx(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _adapt(fx):
    man = W.registry()[fx["expect"]["widget"]]
    return W._plugin_adapter(man)(fx["args"], parse_result(fx["content"]), result_text(fx["content"]))


# == fakes for the OS ===================================================================================
class FakePsutil:
    def __init__(self, cpu=37.5, total=16 * 2 ** 30, avail=6 * 2 ** 30, battery=None):
        self._cpu, self._total, self._avail, self._bat = cpu, total, avail, battery

    def cpu_percent(self, interval=None):
        return self._cpu

    def cpu_count(self, logical=True):
        return 16 if logical else 8

    def virtual_memory(self):
        return SimpleNamespace(total=self._total, available=self._avail)

    def sensors_battery(self):
        return self._bat


class FakeNvml:
    def __init__(self, util=(40, 50, 60), used=3 * 2 ** 30, total=8 * 2 ** 30, temp=55, fail_temp=False):
        self.util, self.used, self.total, self.temp, self.fail_temp = list(util), used, total, temp, fail_temp

    def nvmlDeviceGetHandleByIndex(self, i):
        return "h0"

    def nvmlDeviceGetUtilizationRates(self, h):
        v = self.util.pop(0) if len(self.util) > 1 else self.util[0]
        return SimpleNamespace(gpu=v)

    def nvmlDeviceGetName(self, h):
        return b"NVIDIA GeForce RTX 4070 Laptop GPU"

    def nvmlDeviceGetMemoryInfo(self, h):
        return SimpleNamespace(used=self.used, total=self.total, free=self.total - self.used)

    def nvmlDeviceGetTemperature(self, h, sensor):
        if self.fail_temp:
            raise RuntimeError("not supported")
        return self.temp


def _power(ac=0, flag=0, pct=64, saver=0, life=5400):
    return SimpleNamespace(ACLineStatus=ac, BatteryFlag=flag, BatteryLifePercent=pct, SystemStatusFlag=saver,
                           BatteryLifeTime=life, BatteryFullLifeTime=0xFFFFFFFF)


# == the tool ===========================================================================================
def test_system_status_reads_every_part_from_fakes():
    r = SYS.read(psutil_mod=FakePsutil(), nvml=FakeNvml(), power=_power(), window_s=0.03,
                 cpu_name="AMD Ryzen 7 7840HS w/ Radeon 780M Graphics")
    assert r["battery"] == {"percent": 64, "plugged": False, "charging": False, "time_left_min": 90, "saver": False}, r
    assert r["cpu"] == {"percent": 37.5, "name": "AMD Ryzen 7 7840HS w/ Radeon 780M Graphics", "cores": 8, "threads": 16}
    g = r["gpu"]
    assert g["name"] == "NVIDIA GeForce RTX 4070 Laptop GPU" and 40 <= g["percent"] <= 60
    assert g["vram_used_mb"] == 3072 and g["vram_total_mb"] == 8192 and g["temp_c"] == 55
    assert r["ram"] == {"used_gb": 10.0, "total_gb": 16.0, "percent": 62.5}
    assert r["missing"] == []
    json.dumps(r)                                                  # plain JSON for the MCP server


def test_system_status_degrades_without_gpu_battery_or_psutil():
    class NoNvml:
        def nvmlDeviceGetHandleByIndex(self, i):
            raise RuntimeError("no device")
    r = SYS.read(psutil_mod=FakePsutil(), nvml=NoNvml(), power=_power(flag=128, pct=255), window_s=0.01, cpu_name="x")
    assert r["gpu"] is None and r["battery"] is None
    assert any("GPU" in m for m in r["missing"]) and any("battery" in m for m in r["missing"])
    assert r["cpu"]["percent"] == 37.5 and r["ram"]["total_gb"] == 16.0
    # charging: only when Windows' flag says so (plugged in at a charge limit isn't charging)
    assert SYS.battery(_power(ac=1, flag=1, pct=96))["charging"] is False
    b = SYS.battery(_power(ac=1, flag=8 | 1, pct=70, life=0xFFFFFFFF))
    assert b["charging"] and b["plugged"] and b["time_left_min"] is None
    assert SYS.battery(_power(saver=1))["saver"] is True
    # psutil's battery when GetSystemPowerStatus isn't there
    if sys.platform != "win32":
        ps = FakePsutil(battery=SimpleNamespace(percent=41.6, secsleft=3600, power_plugged=False))
        assert SYS.battery(None, ps) == {"percent": 42, "plugged": False, "charging": False, "time_left_min": 60,
                                         "saver": False}


def test_system_status_is_served_by_the_info_server():
    from heyclaude import mcp_info
    assert mcp_info.tools()["system_status"] is SYS.system_status
    assert "mcp__info__system_status" in mcp_info.tool_names()
    assert "system_status" in mcp_info.prompt()
    assert SYS.system_status.__doc__ and "battery" in SYS.system_status.__doc__


# == the system card ====================================================================================
def test_system_example_validates_and_renders_as_system():
    man = W.registry()["system"]
    assert not W.validate(man["example"], man["schema"])
    spec = W.normalise({"widget": "system", "data": man["example"]})
    assert spec["widget"] == "system" and W.title_for(man, spec["data"]) == "System"
    assert man["size"]["width"] <= 460 and man["size"]["max_height"] <= 280


def test_system_adapter_maps_the_recorded_result():
    W.APP_STATE.clear()
    fx = _fx("info_system_status.json")
    raw = json.loads(fx["content"][0]["text"])
    d = _adapt(fx)
    assert d["battery"]["percent"] == raw["battery"]["percent"]
    assert d["battery"]["note"] == ("charging" if raw["battery"]["charging"] else "plugged in" if raw["battery"]["plugged"]
                                    else d["battery"]["note"])
    assert d["cpu"]["label"] == "Ryzen 9 7940HS" and d["gpu"]["label"] == "RTX 4060 Laptop"
    assert d["gpu"]["sub"].endswith("°C") and d["cpu"]["sub"] == "8 cores"
    assert d["vram"] == {"used": round(raw["gpu"]["vram_used_mb"] / 1024, 1), "total": 8.0}
    assert d["ram"]["total"] == raw["ram"]["total_gb"]
    assert "devices" not in d                                      # the app hasn't said which devices it uses
    spec = W.from_tool(fx["tool"], fx["args"], parse_result(fx["content"]), result_text(fx["content"]))
    assert spec["widget"] == "system" and spec["data"] == d


def test_system_card_shows_hey_claudes_own_devices():
    fx = _fx("info_system_status.json")
    W.APP_STATE.update({"stt": "gpu (turbo)", "voice": "kokoro cpu (fallback: no cuda)"})
    try:
        d = _adapt(fx)
    finally:
        W.APP_STATE.clear()
    assert d["devices"] == [{"name": "stt", "value": "gpu (turbo)", "gpu": True},
                            {"name": "voice", "value": "kokoro cpu (fallback: no cuda)", "gpu": False}]
    assert W.normalise({"widget": "system", "data": d})["widget"] == "system"


def test_system_names_battery_notes_and_odd_results():
    ad = W._plugin_adapter(W.registry()["system"])
    mod = SimpleNamespace(**ad.__globals__)                        # the adapter module's helpers
    assert mod.short_cpu("Intel(R) Core(TM) i7-12700H CPU @ 2.30GHz") == "Core i7-12700H"
    assert mod.short_cpu("12th Gen Intel(R) Core(TM) i5-1240P") == "Core i5-1240P"
    assert mod.short_cpu("AMD Ryzen 5 5600X 6-Core Processor") == "Ryzen 5 5600X"
    assert mod.short_gpu("NVIDIA GeForce RTX 3060") == "RTX 3060" and mod.short_gpu("NVIDIA RTX A2000") == "RTX A2000"
    assert mod._note({"percent": 40, "time_left_min": 135}) == "2 h 15 min left"
    assert mod._note({"percent": 40, "time_left_min": 40, "saver": True}) == "40 min left · saver"
    assert mod._note({"percent": 40}) == "on battery"
    assert ad({}, None, "not json at all") is None and ad({}, {"weather": 1}, "") is None
    assert ad({}, {"cpu": {"percent": None}, "gpu": None, "ram": None, "battery": None}, "") is None
    d = ad({}, {"cpu": {"percent": 12, "name": "x"}, "battery": None, "gpu": None, "ram": None}, "")
    assert set(d) <= {"cpu", "devices"} and d["cpu"]["percent"] == 12


def test_bad_system_data_falls_back_to_text():
    w = W.normalise({"widget": "system", "data": {"cpu": {"percent": "busy"}}, "text": "The CPU is busy."})
    assert w["widget"] == "text" and w["data"]["text"] == "The CPU is busy."
    assert W.normalise({"widget": "system", "data": {"battery": {"percent": 140}}}) is None
    assert W.normalise({"widget": "system", "data": {"gpu": {"label": "no percent"}}, "text": "GPU"})["widget"] == "text"


# == the reminders card =================================================================================
def _engine_and_exec(clock=time.time):
    tm = Timers(clock=clock)
    e = IntentEngine({})
    e.has_timers = lambda: bool(tm.running())
    return tm, e, Executor(e, timers=tm)


def test_reminders_example_validates():
    man = W.registry()["reminders"]
    assert not W.validate(man["example"], man["schema"])
    assert W.title_for(man, man["example"]) == "Timers · 3 running"
    assert W.normalise({"widget": "reminders", "data": man["example"]})["ttl_s"] == man["ttl_s"]


def test_reminders_adapter_maps_the_recorded_snapshot():
    fx = _fx("intents_reminders.json")
    snap = json.loads(fx["content"][0]["text"])
    d = _adapt(fx)
    assert d["title"] == fx["expect"]["title"] == f"Timers · {len(snap['timers'])} running"
    lefts = [i["left_s"] for i in d["items"]]
    assert lefts == sorted(lefts)                                  # soonest first
    assert [i["label"] for i in d["items"]] == ["", "Tea", "Pasta", "Laundry"]
    assert all(i["total_s"] >= i["left_s"] and len(i["ends"]) == 5 for i in d["items"])
    assert not any(i.get("focus") for i in d["items"])
    f = _adapt(_fx("intents_reminders_focus.json"))
    assert [i["label"] for i in f["items"] if i.get("focus")] == ["Pasta"]


def test_reminders_adapter_odd_input_and_many_rows():
    ad = W._plugin_adapter(W.registry()["reminders"])
    assert ad({}, {"timers": []}, "") is None and ad({}, None, "nope") is None and ad({}, {"x": 1}, "") is None
    many = {"timers": [{"label": f"t{i}", "seconds": 600, "left_s": 600 - i} for i in range(8)]}
    d = ad({}, many, "")
    assert len(d["items"]) == 6 and d["more"] == 2 and d["title"] == "Timers · 8 running"
    assert "ends" not in d["items"][0]                             # no wall-clock time given
    mixed = ad({}, {"timers": [{"label": "stretch", "kind": "reminder", "seconds": 60, "left_s": 30}]}, "")
    assert mixed["title"] == "Reminder · 1 running" and mixed["items"][0]["label"] == "Stretch"
    assert W.normalise({"widget": "reminders", "data": d})["widget"] == "reminders"


def test_bad_reminders_data_falls_back_to_text():
    w = W.normalise({"widget": "reminders", "data": {"title": "Timers", "items": [{"label": "Tea"}]},
                     "text": "Tea: 4 minutes left."})
    assert w["widget"] == "text" and w["data"]["text"] == "Tea: 4 minutes left."
    assert W.normalise({"widget": "reminders", "data": {"items": []}}) is None


def test_list_timers_intent_makes_the_card_and_the_spoken_line():
    tm, e, ex = _engine_and_exec()
    out = ex.run(e.match("what timers do I have"))
    assert out.say == "There's no timer running." and out.widget is None
    assert e.match("list my reminders") is None                    # no timer yet: a calendar question, Claude's
    ex.run(e.match("set a 4 minute tea timer"))
    out = ex.run(e.match("What timers do I have?"))
    assert out.say == "Just the tea timer, 4 minutes left." and out.always_say
    assert out.widget["widget"] == "reminders" and out.widget["data"]["title"] == "Timer · 1 running"
    assert out.result["timers"][0]["label"] == "tea"               # the card is made from this result
    ex.run(e.match("set a timer for 90 seconds"))
    ex.run(e.match("set a pasta timer for 10 minutes"))
    m = e.match("list my reminders")
    assert m is not None and m.intent == "timer.list"              # with timers running, reminders are these
    out = ex.run(m)
    assert out.say == "3 timers. The next one ends in 1 minute 30 seconds.", out.say
    items = out.widget["data"]["items"]
    assert [i["label"] for i in items] == ["", "Tea", "Pasta"] and items[0]["left_s"] <= 90
    assert W.normalise(out.widget)["widget"] == "reminders"        # the assistant normalises it again
    tm.cancel()


def test_how_long_left_on_a_named_timer():
    tm, e, ex = _engine_and_exec()
    ex.run(e.match("set a 10 minute pasta timer"))
    out = ex.run(e.match("how long left on the pasta timer"))
    assert out.say == "10 minutes left on the pasta timer." and out.widget is None      # one timer: its own card is up
    assert ex.run(e.match("how long left on the tea timer")).say == "There's no tea timer."
    assert ex.run(e.match("how much time is left")).say == "10 minutes left."
    ex.run(e.match("set a 4 minute tea timer"))
    out = ex.run(e.match("how long left on the tea timer"))
    assert out.say == "4 minutes left on the tea timer."
    assert [i["label"] for i in out.widget["data"]["items"] if i.get("focus")] == ["Tea"]
    tm.cancel()


def test_snapshot_is_soonest_first_and_running_only():
    now = [1_800_000_000.0]
    tm = Timers(clock=lambda: now[0])
    a = tm.start(600, "pasta")
    tm.start(60, "")
    tm.start(300, "tea")
    a.handle.cancel()
    tm.cancel("tea")
    now[0] += 10
    snap = tm.snapshot()
    assert [t["label"] for t in snap["timers"]] == ["", "pasta"]
    assert snap["timers"][0]["left_s"] == 50.0 and snap["timers"][1]["ends_at"] == 1_800_000_600.0
    tm.cancel()


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
