"""M5b words-and-numbers packs: convert (calculate, convert_units, convert_currency) and
define / translate (heyclaude/mcp_info/convert.py, lookup.py; widgets/convert, define,
translate). Offline: every HTTP call is mocked.

    .venv\\Scripts\\python tests\\test_widgets_words.py
"""
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import widgets as W  # noqa: E402
from heyclaude.effects import parse_result, result_text  # noqa: E402
from heyclaude.mcp_info import convert as C  # noqa: E402
from heyclaude.mcp_info import lookup as L  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"
PACKS = ("convert", "define", "translate")


def _fixture(name):
    fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    spec = W.from_tool(fx["tool"], fx["args"], parse_result(fx["content"]), result_text(fx["content"]))
    return fx, spec


def _ok(expected, raises):
    try:
        expected()
    except raises:
        return
    raise AssertionError(f"expected {raises}")


# ----------------------------------------------------------------- the packs
def test_packs_load_and_examples_validate():
    reg = W.registry()
    for wid in PACKS:
        man = reg[wid]
        assert man["triggers"]["data"] == "adapter" and (Path(man["dir"]) / "adapter.py").exists(), wid
        assert man["size"]["width"] <= 460 and man["size"]["max_height"] <= 280, wid
        assert W.validate(man["example"], man["schema"]) == [], wid
        spec = W.normalise({"widget": wid, "data": man["example"], "text": ""})
        assert spec and spec["widget"] == wid and spec["data"] == man["example"], wid
        assert "{" not in W.title_for(man, man["example"]), wid
    # the model never writes them: the prompt says they come from the tools
    p = W.prompt_text()
    for wid in PACKS:
        line = next(x for x in p.splitlines() if x.startswith(f"- {wid}:"))
        assert "never write it yourself" in line and "mcp__info__" in line, line
    from heyclaude import mcp_info
    names = set(mcp_info.tools())
    assert {"calculate", "convert_units", "convert_currency", "define", "translate"} <= names, names
    assert "calculate" in mcp_info.prompt() and "translate" in mcp_info.prompt()


def test_fixtures_map_to_their_cards():
    fx, s = _fixture("info_calculate.json")
    assert s["widget"] == "convert" and s["data"]["result"] == "36", s
    assert s["data"]["steps"] == ["15% of 240", "= 0.15 × 240", "= 36"] and s["data"]["heading"] == "Maths"
    fx, s = _fixture("info_calculate_steps.json")
    assert s["data"]["result"] == "52" and len(s["data"]["steps"]) == 4, s
    fx, s = _fixture("info_convert_units.json")
    assert s["data"] == {"kind": "units", "result": "22.2", "steps": ["(72 °F − 32) × 5/9", "= 40 × 5/9", "= 22.2 °C"],
                         "heading": "Temperature · °F → °C", "unit": "°C", "note": "32 °F = 0 °C"}, s
    fx, s = _fixture("info_convert_currency.json")
    d = s["data"]
    assert d["unit"] == "UZS" and d["source"] == "open.er-api.com" and d["date"] == "2026-11-10", d
    assert d["steps"][0].startswith("100 USD × ") and d["steps"][-1] == f"= {d['result']} UZS", d
    fx, s = _fixture("info_define.json")
    d = s["data"]
    assert d["word"] == "glimmer" and d["ipa"].startswith("/") and d["language"] == "English", d
    assert [x["pos"] for x in d["senses"]] == ["noun", "noun", "verb"], d       # both parts of speech
    assert all(x["text"] for x in d["senses"]) and d["senses"][0]["example"], d
    fx, s = _fixture("info_define_ru.json")
    d = s["data"]
    assert d["word"] == "книга" and d["ipa"] == "[ˈknʲiɡə]" and d["senses"][0]["text"] == "book", d
    assert "́" not in d["senses"][0]["example"] and d["senses"][0]["example_tr"], d    # stress marks dropped
    fx, s = _fixture("info_translate.json")
    d = s["data"]
    assert (d["from"], d["to"], d["detected"]) == ("en", "uz", True) and d["translation"], d
    fx, s = _fixture("info_translate_ru.json")
    assert s["data"]["from"] == "ru" and "detected" not in s["data"], s
    for f in FIXTURES.glob("info_*.json"):                 # titles as recorded, for test_island's live check
        fx = json.loads(f.read_text(encoding="utf-8"))
        if fx["tool"].split("__")[-1] not in ("calculate", "convert_units", "convert_currency", "define", "translate"):
            continue
        w = W.for_qml(_fixture(f.name)[1])
        assert w["widget"] == fx["expect"]["widget"] and w["title"] == fx["expect"]["title"], (f.name, w["title"])
        assert w["glyph"] and not w["icon_url"]


def test_bad_data_falls_back_to_text_or_nothing():
    bad = W.normalise({"widget": "convert", "data": {"result": 5}, "text": "That's 36."})
    assert bad["widget"] == "text" and bad["data"]["text"] == "That's 36.", bad
    bad = W.normalise({"widget": "define", "data": {"word": "x", "language": "English", "senses": "no"}, "text": "a word"})
    assert bad["widget"] == "text"
    bad = W.normalise({"widget": "translate", "data": {"from": "English", "to": "uz", "from_name": "E", "to_name": "U",
                                                       "text": "a", "translation": "b"}, "text": "b"})
    assert bad["widget"] == "text"                              # a language name where a code belongs
    # a tool's odd output never makes a card (and never breaks the turn)
    assert W.from_tool("mcp__info__calculate", {}, None, "Error: division by zero") is None
    assert W.from_tool("mcp__info__convert_units", {}, {"kind": "units", "steps": []}, "") is None
    assert W.from_tool("mcp__info__define", {}, {"word": "x", "meanings": []}, "") is None
    assert W.from_tool("mcp__info__translate", {}, {"text": "a", "translation": "", "from": "en", "to": "uz"}, "") is None
    assert W.from_tool("mcp__info__translate", {}, ["not", "a", "dict"], "") is None


# ----------------------------------------------------------------- calculate
def test_calculate_shows_the_working():
    cases = {
        "15% of 240": (36, ["15% of 240", "= 0.15 × 240", "= 36"]),
        "240 + 15%": (276, ["240 + 15%", "= 240 + 240 × 15%", "= 240 + 240 × 0.15", "= 240 + 36", "= 276"]),
        "(3+4)*2^3": (56, ["(3 + 4) × 2^3", "= 7 × 8", "= 56"]),
        "1+2+3+4+5+6": (21, ["1 + 2 + 3 + 4 + 5 + 6", "= 21"]),
        "12 times 7": (84, ["12 × 7", "= 84"]),
        "10 % 3": (1, ["10 mod 3", "= 1"]),
        "5!": (120, ["5!", "= 120"]),
        "sin(30°)": (0.5, ["sin(30°)", "= 0.5"]),
        "1,200,000 / 4": (300000, ["1,200,000 ÷ 4", "= 300,000"]),
        "√16 + 2": (6, ["√16 + 2", "= 4 + 2", "= 6"]),
        "2(3+4)": (14, ["2 × (3 + 4)", "= 2 × 7", "= 14"]),
        "-3^2": (-9, ["−3^2", "= −9"]),
        "what is 2 to the power of 10?": (1024, ["2^10", "= 1,024"]),
    }
    for expr, (value, steps) in cases.items():
        r = C.calculate(expr)
        assert math.isclose(r["result"], value, rel_tol=1e-12) and r["steps"] == steps, (expr, r)
    assert C.calculate("0.1 + 0.2")["result_text"] == "0.3"
    assert C.calculate("1/3")["result_text"] == "0.3333333333"
    assert C.calculate("2^100")["result_text"] == "1.26765 × 10³⁰"
    assert C.calculate("pi")["steps"][0] == "π"
    long = C.calculate("1*2+3*4+5*6+7*8+9*10+11*12")
    assert len(long["steps"]) <= 5 and long["result"] == 322


def test_calculate_is_safe():
    for evil in ("__import__('os').system('echo hi')", "().__class__", "open('x')", "lambda: 1", "[1, 2]",
                 "x + 1", "'a' * 3", "a.b", "1 if 1 else 2", "eval('1')", "f'{1}'"):
        _ok(lambda: C.calculate(evil), ValueError)
    for bad in ("1/0", "10^1000", "sqrt(-1)", "171!", "log(0)", "", "1 +", "9" * 400, "(-8)^0.5"):
        _ok(lambda: C.calculate(bad), ValueError)
    _ok(lambda: C.calculate("+".join(["1"] * 200)), ValueError)          # too many nodes


# ----------------------------------------------------------------- units
def test_convert_units_with_formulas():
    r = C.convert_units(72, "F", "C")
    assert r["result_text"] == "22.2" and r["steps"] == ["(72 °F − 32) × 5/9", "= 40 × 5/9", "= 22.2 °C"], r
    assert C.convert_units(22, "celsius", "fahrenheit")["steps"] == ["22 °C × 9/5 + 32", "= 39.6 + 32", "= 71.6 °F"]
    assert C.convert_units(0, "°C", "K")["result_text"] == "273.15"
    r = C.convert_units(5, "miles", "km")
    assert r["result_text"] == "8.047" and r["steps"] == ["5 mi × 1.609344 km/mi", "= 8.047 km"], r
    r = C.convert_units(10, "km", "mi")
    assert r["steps"][0] == "10 km ÷ 1.609344 km/mi" and r["note"] == "1 mi = 1.609344 km", r
    assert C.convert_units(5000, "m", "km")["steps"] == ["5,000 m ÷ 1,000 m/km", "= 5 km"]
    assert C.convert_units(90, "minutes", "hours")["result_text"] == "1.5"
    assert C.convert_units(100, "km/h", "mph")["steps"][0] == "100 km/h ÷ 1.609344"         # no "km/h/mph"
    assert C.convert_units(1, "GiB", "MB")["result_text"] == "1,074"
    assert C.convert_units(1, "acre", "m2")["result_text"] == "4,047"
    assert C.convert_units(2, "cups", "ml")["result_text"] == "473.2"
    assert C.convert_units(3, "kg", "lbs")["result_text"] == "6.614"
    assert C.convert_units(10, "sotka", "ha")["result_text"] == "0.1"
    assert C.convert_units(1, "week", "days")["result_text"] == "7"
    _ok(lambda: C.convert_units(5, "kg", "km"), ValueError)
    _ok(lambda: C.convert_units(5, "parsec", "km"), ValueError)
    _ok(lambda: C.convert_units(-500, "F", "C"), ValueError)
    _ok(lambda: C.convert_units("lots", "m", "km"), ValueError)


# ----------------------------------------------------------------- currency
ER = {"result": "success", "provider": "https://www.exchangerate-api.com", "time_last_update_unix": 1794268951,
      "time_last_update_utc": "Tue, 10 Nov 2026 00:02:31 +0000", "base_code": "USD",
      "rates": {"USD": 1, "UZS": 12000.5, "EUR": 0.9, "RUB": 81.0, "GBP": 0.75}}


def test_convert_currency_offline_with_cache():
    calls = []

    def fake(url, timeout=6.0):
        calls.append(url)
        if "er-api" in url:
            return ER
        raise AssertionError(url)

    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(C, "_fetch", fake), \
            mock.patch.object(C, "CACHE_FILE", Path(tmp) / "fx.json"):
        C._mem.clear()
        r = C.convert_currency(100, "USD", "UZS")
        assert r["result_text"] == "1,200,050" and r["date"] == "2026-11-10" and r["rate"] == 12000.5, r
        assert r["steps"] == ["100 USD × 12,000.5 UZS/USD", "= 1,200,050 UZS"], r
        assert r["source"].endswith("(open.er-api.com)") and r["to_name"] == "Uzbek sum"
        r = C.convert_currency(1000000, "sum", "dollars")                      # names; a rate below 1 divides
        assert r["from"] == "UZS" and r["steps"][0] == "1,000,000 UZS ÷ 12,000.5 UZS/USD" and r["result_text"] == "83.33", r
        r = C.convert_currency(50, "euro", "rub")                              # a cross rate through USD
        assert math.isclose(r["rate"], 90.0) and r["result_text"] == "4,500.00", r
        assert len(calls) == 1, calls                                          # cached in memory
        C._mem.clear()
        C.convert_currency(1, "USD", "EUR")
        assert len(calls) == 1, "read back from the cache file"
        _ok(lambda: C.convert_currency(1, "USD", "XYZ"), ValueError)
        _ok(lambda: C.convert_currency(1, "moon cheese", "USD"), ValueError)
        # offline after the cache went stale: the last rates, marked
        C._mem.clear()
        data = json.loads((Path(tmp) / "fx.json").read_text())
        data["fetched"] = time.time() - 3 * 86400
        (Path(tmp) / "fx.json").write_text(json.dumps(data))

        def down(url, timeout=6.0):
            raise OSError("offline")
        with mock.patch.object(C, "_fetch", down):
            r = C.convert_currency(10, "USD", "GBP")
            assert r["stale"] is True and r["result_text"] == "7.50", r
            spec = W.from_tool("mcp__info__convert_currency", {}, r, json.dumps(r))
            assert spec["data"]["stale"] is True
            (Path(tmp) / "fx.json").unlink()
            C._mem.clear()
            _ok(lambda: C.convert_currency(10, "USD", "GBP"), OSError)
    C._mem.clear()


def test_convert_currency_falls_back_to_frankfurter():
    def fake(url, timeout=6.0):
        if "er-api" in url:
            raise OSError("down")
        return {"amount": 1.0, "base": "USD", "date": "2026-11-09", "rates": {"EUR": 0.9}}
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(C, "_fetch", fake), \
            mock.patch.object(C, "CACHE_FILE", Path(tmp) / "fx.json"):
        C._mem.clear()
        r = C.convert_currency(10, "USD", "EUR")
        assert r["result_text"] == "9.00" and "frankfurter" in r["source"] and r["date"] == "2026-11-09", r
    C._mem.clear()


# ----------------------------------------------------------------- define / translate
DICTAPI = [{"word": "glimmer", "phonetic": "/ˈɡlɪmə(ɹ)/", "meanings": [
    {"partOfSpeech": "noun", "definitions": [{"definition": "A faint light.", "example": "a glimmer of hope"},
                                             {"definition": "A flash of light."}]},
    {"partOfSpeech": "verb", "definitions": [{"definition": "To shine faintly."}]}],
    "sourceUrls": ["https://en.wiktionary.org/wiki/glimmer"]}]
WIKT = {"ru": [{"partOfSpeech": "Noun", "language": "Russian", "definitions": [
    {"definition": '<a rel="mw:WikiLink" href="/wiki/book">book</a>',
     "parsedExamples": [{"example": "<b>кни́га</b> на столе́", "translation": "a <b>book</b> on the table"}]}]}]}
SECTIONS = {"parse": {"sections": [{"toclevel": 1, "line": "Bulgarian", "number": "1", "index": "1"},
                                   {"toclevel": 1, "line": "Russian", "number": "2", "index": "5"},
                                   {"toclevel": 2, "line": "Pronunciation", "number": "2.1", "index": "6"}]}}
SECTION = {"parse": {"text": '<div><span class="IPA">[ˈknʲiɡə]</span></div>'}}


def test_define_offline():
    def fake(url, timeout=6.0):
        if "dictionaryapi.dev" in url:
            return DICTAPI if url.endswith("/glimmer") else None
        if "rest_v1/page/definition" in url:
            return WIKT if "%D0%BA" in url else None
        if "prop=sections" in url:
            return SECTIONS
        if "prop=text" in url:
            assert "section=6" in url, url
            return SECTION
        raise AssertionError(url)
    with mock.patch.object(L, "_fetch", fake):
        L._cache.clear()
        r = L.define("Glimmer.")
        assert r["word"] == "glimmer" and r["phonetic"] == "/ˈɡlɪmə(ɹ)/" and [m["pos"] for m in r["meanings"]] == ["noun", "verb"], r
        r = L.define("книга")                                              # Cyrillic: Russian, even with lang="en"
        assert r["lang"] == "ru" and r["language"] == "Russian" and r["phonetic"] == "[ˈknʲiɡə]", r
        s = r["meanings"][0]["senses"][0]
        assert s == {"definition": "book", "example": "кни́га на столе́", "example_translation": "a book on the table"}, s
        assert r["meanings"][0]["pos"] == "noun"
        _ok(lambda: L.define("zzzzqx"), LookupError)
        _ok(lambda: L.define(""), ValueError)
        _ok(lambda: L.define("x", "klingon"), ValueError)
        d = W.from_tool("mcp__info__define", {}, L.define("книга", "ru"), "")["data"]
        assert d["senses"][0]["example"] == "книга на столе" and d["word"] == "книга"
    L._cache.clear()


def test_translate_offline():
    seen = []

    def fake(url, timeout=6.0):
        seen.append(url)
        q = __import__("urllib.parse").parse.parse_qs(__import__("urllib.parse").parse.urlsplit(url).query)
        if q["q"][0] == "quota":
            return {"responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS"},
                    "quotaFinished": True, "responseStatus": 429}
        return {"responseData": {"translatedText": "Xayrli tong", "match": 0.85}, "responseStatus": 200,
                "matches": [{"segment": q["q"][0], "translation": "Assalomu alaykum", "match": 0.7},
                            {"segment": "something else", "translation": "Boshqa", "match": 0.9}]}
    with mock.patch.object(L, "_fetch", fake), mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": "{}"}):
        L._cache.clear()
        r = L.translate("Good morning", "Uzbek")
        assert "langpair=en%7Cuz" in seen[-1] and r["from"] == "en" and r["to"] == "uz" and r["detected"], r
        assert r["translation"] == "Xayrli tong" and r["alternatives"] == ["Assalomu alaykum"], r   # same segment only
        r = L.translate("Привет, как дела?", "uz")
        assert "langpair=ru%7Cuz" in seen[-1] and r["from_name"] == "Russian", r
        r = L.translate("Kitob o'qish juda foydali", "en")
        assert "langpair=uz%7Cen" in seen[-1], seen[-1]
        r = L.translate("Good morning", "en")                               # a guess equal to the target: not that one
        assert "langpair=uz%7Cen" in seen[-1], seen[-1]
        r = L.translate("hello", "ru", from_="english")
        assert "langpair=en%7Cru" in seen[-1] and not r["detected"]
        _ok(lambda: L.translate("hello", "en", from_="en"), ValueError)
        _ok(lambda: L.translate("Привет", "ru"), ValueError)
        _ok(lambda: L.translate("quota", "uz"), OSError)
        _ok(lambda: L.translate("", "uz"), ValueError)
        _ok(lambda: L.translate("x" * 2000, "uz"), ValueError)
        n = len(seen)
        L.translate("Good morning", "uz")
        assert len(seen) == n, "cached"
        long = ". ".join(["This is a fairly long sentence about nothing at all"] * 12) + "."
        L.translate(long, "uz")
        assert len(seen) - n > 1, "split into chunks of at most 500 bytes"
    with mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": json.dumps({"translate": {"email": "dev@example.com"}})}), \
            mock.patch.object(L, "_fetch", fake):
        L._cache.clear()
        L.translate("Good evening", "uz")
        assert "de=dev%40example.com" in seen[-1]
    L._cache.clear()
    assert L.detect("Salom, qalaysiz?") == "uz" and L.detect("How are you?") == "en" and L.detect("ўқитувчи") == "uz"


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    sys.stdout.flush()
    os._exit(0)
