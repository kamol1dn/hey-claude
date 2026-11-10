"""Quick maths, unit and currency conversion, with the working shown (ROADMAP M5b).

Served by the info MCP server as mcp__info__calculate, mcp__info__convert_units and
mcp__info__convert_currency; widgets/convert draws the card from their results (the
big answer, the derivation line by line, the rate's date and source).

- calculate: a safe evaluator (Python's ast, a whitelist of nodes and functions, no
  eval), reduced one layer at a time so each step of the working is a real line.
- convert_units: length, mass, temperature, volume, speed, area, data and time, with
  the formula used.
- convert_currency: keyless daily rates from open.er-api.com (ExchangeRate-API; 160+
  currencies including UZS, which the ECB's frankfurter doesn't have), cached for
  hours in memory and in state/cache/fx_usd.json; frankfurter.dev as a fallback for
  the majors. Settings (the `info:` config section): convert.rates_ttl_h (default 6).
"""
from __future__ import annotations

import ast
import json
import math
import re
import time
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

PROMPT = ("For any arithmetic, percentage, unit conversion or currency conversion use calculate, "
          "convert_units or convert_currency (live rates, UZS included) instead of working it out yourself; "
          "the card shows the working.")

UA = "hey-claude/1.0 (+https://github.com/kamol1dn/hey-claude)"
MINUS = "−"
_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


# ----------------------------------------------------------------- numbers
def fmt(x, sig: int = 10, decimals: int | None = None, strip: bool = True) -> str:
    """A number for people: thousands commas, no float noise, U+2212 for minus,
    "1.23 × 10¹⁵" when it's huge or tiny."""
    if isinstance(x, bool):
        x = int(x)
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        raise ValueError("the result isn't a finite number")
    if x == 0:
        return "0"
    a = abs(x)
    if a >= 1e15 or a < 1e-6:
        m, e = f"{x:.{max(1, min(sig, 6)) - 1}e}".split("e")
        m = m.rstrip("0").rstrip(".") if "." in m else m
        return f"{m.replace('-', MINUS)} × 10{str(int(e)).translate(_SUP)}"
    if decimals is None:
        decimals = max(0, sig - 1 - math.floor(math.log10(a)))
    if isinstance(x, int) and decimals >= 0:
        s = f"{x:,}"
    else:
        s = f"{x:,.{decimals}f}"
        if "." in s and strip:
            s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s.replace("-", MINUS)


def _nice(x: float) -> str:
    """A conversion result: 4 significant digits, never rounding away whole digits."""
    if x == 0:
        return "0"
    a = abs(x)
    if a >= 1e15 or a < 1e-6:
        return fmt(x, 4)
    return fmt(x, decimals=max(0, min(6, 3 - math.floor(math.log10(a)))))


def _clean(x: float) -> str:
    """A conversion factor, exact as far as it goes (1.609344, 0.3048, 1,000)."""
    return fmt(float(f"{x:.10g}"), 10)


def _digits(s: str) -> int:
    return len(re.sub(r"[^0-9]", "", s).strip("0")) or 1


# ----------------------------------------------------------------- calculate
FUNCS = {
    "sqrt": (lambda x: _need(x >= 0, "the square root of a negative number") or math.sqrt(x), 1),
    "cbrt": (lambda x: math.copysign(abs(x) ** (1 / 3), x), 1),
    "abs": (abs, 1), "round": (round, (1, 2)), "floor": (math.floor, 1), "ceil": (math.ceil, 1),
    "sin": (math.sin, 1), "cos": (math.cos, 1), "tan": (math.tan, 1),
    "asin": (math.asin, 1), "acos": (math.acos, 1), "atan": (math.atan, 1),
    "log": (lambda x, b=10: _need(x > 0, "the log of a number ≤ 0") or math.log(x, b), (1, 2)),
    "log10": (lambda x: _need(x > 0, "the log of a number ≤ 0") or math.log10(x), 1),
    "log2": (lambda x: _need(x > 0, "the log of a number ≤ 0") or math.log2(x), 1),
    "ln": (lambda x: _need(x > 0, "the log of a number ≤ 0") or math.log(x), 1),
    "exp": (lambda x: _need(x < 700, "a number too large") or math.exp(x), 1),
    "min": (min, (1, 20)), "max": (max, (1, 20)),
    "fact": (lambda n: _need(float(n).is_integer() and 0 <= n <= 170, "a factorial of a whole number 0..170")
             or math.factorial(int(n)), 1),
    "pct": (lambda x: x / 100, 1),                     # 15% (written by the parser)
    "deg": (math.radians, 1),                          # 30° inside sin/cos/tan
}
CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^", ast.Mod: "mod", ast.FloorDiv: "//", ast.MatMult: "of"}
_PREC = {"+": 1, "-": 1, "*": 2, "/": 2, "//": 2, "mod": 2, "of": 2, "neg": 3, "^": 4}
_SHOW = {"+": "+", "-": MINUS, "*": "×", "/": "÷", "//": "div", "mod": "mod", "of": "of", "^": "^"}


def _need(ok: bool, what: str):
    if not ok:
        raise ValueError(f"can't take {what}")
    return None


def _prepare(expr: str) -> str:
    s = str(expr or "").strip().lower()
    if not s:
        raise ValueError("no expression given")
    if len(s) > 300:
        raise ValueError("that expression is too long (300 characters at most)")
    s = s.rstrip("=?. ")
    s = re.sub(r"^(what\s+is|what's|calculate|compute)\s+", "", s)
    for a, b in (("×", "*"), ("·", "*"), ("⋅", "*"), ("∗", "*"), ("÷", "/"), ("∕", "/"), ("−", "-"), ("–", "-"),
                 ("—", "-"), ("√", "sqrt"), ("π", "pi"), ("²", "^2"), ("³", "^3"), ("**", "^"), ("√", "sqrt")):
        s = s.replace(a, b)
    words = [(r"\bmultiplied by\b", "*"), (r"\btimes\b", "*"), (r"\bdivided by\b", "/"), (r"\bover\b", "/"),
             (r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\bto the power of\b", "^"), (r"\bsquared\b", "^2"),
             (r"\bcubed\b", "^3"), (r"\bsquare root of\b", "sqrt"), (r"\bmodulo\b", "%"), (r"\bmod\b", "%"),
             (r"\bpercent\b", "%")]
    for pat, rep in words:
        s = re.sub(pat, rep, s)
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)                      # 1,200,000
    s = re.sub(r"(?<=[\d)])\s*x\s*(?=[\d(])", "*", s)                   # 3 x 4
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(?:°|\bdeg(?:rees?)?\b)", r"deg(\1)", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*!", r"fact(\1)", s)
    # a % that isn't followed by an operand is a percentage; "of" then means times
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*(?:[\d(.]|(?!of\b)[a-z]))", r"pct(\1)", s)
    s = re.sub(r"\bof\b", "@", s)                                      # "of" (shown as written)
    s = re.sub(r"sqrt\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", s)               # √16
    s = re.sub(r"(?<=\d)\s*(?=\(|pi\b|sqrt|e\b)", "*", s)                  # 2(3+4), 2pi
    s = re.sub(r"\)\s*\(", ")*(", s)
    s = s.replace("^", "**")
    return s


def _tree(node, budget: list):
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("that expression is too long")
    if isinstance(node, ast.Expression):
        return _tree(node.body, budget)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return ("num", node.value)
    if isinstance(node, ast.Name):
        if node.id in CONSTS:
            return ("const", node.id)
        raise ValueError(f"unknown name {node.id!r}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        x = _tree(node.operand, budget)
        if isinstance(node.op, ast.UAdd):
            return x
        if x[0] == "num":
            return ("num", -x[1])
        return ("neg", x)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return ("bin", _OPS[type(node.op)], _tree(node.left, budget), _tree(node.right, budget))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS and not node.keywords:
        n = FUNCS[node.func.id][1]
        lo, hi = (n, n) if isinstance(n, int) else n
        if not lo <= len(node.args) <= hi:
            raise ValueError(f"{node.func.id} takes {lo if lo == hi else f'{lo} to {hi}'} argument(s)")
        return ("call", node.func.id, [_tree(a, budget) for a in node.args])
    raise ValueError("only numbers, + − × ÷ ^ %, parentheses and common functions (sqrt, sin, log...) are allowed")


def _percent_of_base(t):
    """240 + 15% means 240 + 240 × 15%, as on a calculator."""
    if t[0] == "bin":
        op, l, r = t[1], _percent_of_base(t[2]), _percent_of_base(t[3])
        if op in "+-" and r[0] == "call" and r[1] == "pct":
            r = ("bin", "*", l, r)
        return ("bin", op, l, r)
    if t[0] == "neg":
        return ("neg", _percent_of_base(t[1]))
    if t[0] == "call":
        return ("call", t[1], [_percent_of_base(a) for a in t[2]])
    return t


def _apply(op: str, a, b):
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op in ("*", "of"):
        return a * b
    if op in ("/", "//", "mod"):
        if b == 0:
            raise ValueError("division by zero")
        if op == "//":
            return a // b
        if op == "mod":
            return a % b
        q = a / b
        return int(q) if isinstance(a, int) and isinstance(b, int) and a % b == 0 else q
    if op == "^":
        if a == 0 and b < 0:
            raise ValueError("division by zero")
        if abs(a) > 1 and b * math.log10(abs(a)) > 300:
            raise ValueError("that number is too large")
        if a < 0 and not float(b).is_integer():
            raise ValueError("can't raise a negative number to a fractional power")
        if isinstance(a, int) and isinstance(b, int) and b >= 0:
            return a ** b
        return float(a) ** float(b)
    raise ValueError(op)


def _value(t):
    k = t[0]
    if k == "num":
        return t[1]
    if k == "const":
        return CONSTS[t[1]]
    if k == "neg":
        return -_value(t[1])
    if k == "bin":
        return _apply(t[1], _value(t[2]), _value(t[3]))
    fn = FUNCS[t[1]][0]
    try:
        v = fn(*[_value(a) for a in t[2]])
    except (OverflowError, ZeroDivisionError):
        raise ValueError("that number is too large") from None
    except (TypeError,) as e:
        raise ValueError(str(e)) from None
    return v


def _check(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise ValueError("the result isn't a finite number")
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    return v


def _reduce_once(t):
    """One layer of the working: every operation whose operands are plain numbers is done."""
    k = t[0]
    if k == "num":
        return t
    kids = [t[1]] if k == "neg" else [t[2], t[3]] if k == "bin" else t[2] if k == "call" else []
    if all(c[0] == "num" or _lit(c) for c in kids) or (k == "bin" and _chain(t, t[1])):
        return ("num", _check(_value(t)))
    if k == "neg":
        return ("neg", _reduce_once(t[1]))
    if k == "bin":
        return ("bin", t[1], _reduce_once(t[2]), _reduce_once(t[3]))
    return ("call", t[1], [_reduce_once(a) for a in t[2]])


def _lit(t) -> bool:
    """30° is read as one number (sin(30°) -> 0.5, not sin(0.5236))."""
    return t[0] == "call" and t[1] == "deg" and t[2][0][0] == "num"


def _chain(t, op: str) -> bool:
    """1 + 2 + 3 + 4 (or 2 × 3 × 4) over plain numbers is one step, not three."""
    group = ("+", "-") if op in ("+", "-") else ("*", "/") if op in ("*", "/") else ()
    if t[0] == "num":
        return True
    return t[0] == "bin" and t[1] in group and _chain(t[2], op) and _chain(t[3], op)


def _show(t, sig: int = 8) -> str:
    def go(t, parent: int = 0, right: bool = False, pow_base: bool = False) -> str:
        k = t[0]
        if k == "num":
            s = fmt(t[1], sig)
            return f"({s})" if t[1] < 0 and (parent or pow_base) else s
        if k == "const":
            return {"pi": "π", "e": "e", "tau": "τ"}[t[1]]
        if k == "neg":
            s = MINUS + go(t[1], _PREC["neg"])
            return f"({s})" if parent >= _PREC["neg"] else s
        if k == "call":
            name, args = t[1], t[2]
            if name == "pct":
                return go(args[0], 5) + "%"
            if name == "deg":
                return go(args[0], 5) + "°"
            if name == "fact":
                return go(args[0], 5) + "!"
            if name == "sqrt":
                inner = go(args[0], 5)
                return "√" + inner
            return name + "(" + ", ".join(go(a) for a in args) + ")"
        op, l, r = t[1], t[2], t[3]
        p = _PREC[op]
        if op == "^":
            s = go(l, p + 1, pow_base=True) + "^" + go(r, p)
        else:
            shown = "×" if op == "of" and not (l[0] == "call" and l[1] == "pct") else _SHOW[op]
            s = go(l, p) + f" {shown} " + go(r, p + (1 if op in "-/mod//" else 0), right=True)
        return f"({s})" if p < parent else s
    return go(t)


def calculate(expression: str) -> dict:
    """Evaluate a maths expression safely and show the working, step by step.

    Supports + - * / ^ (power), % (a percentage: "15% of 240", "240 + 15%"; or modulo
    between two numbers: "10 % 3"), parentheses, ! (factorial), sqrt, cbrt, abs, round,
    floor, ceil, sin/cos/tan (radians, or degrees written 30°), asin/acos/atan, log
    (base 10, or log(x, base)), ln, log2, exp, min, max, and the constants pi and e.
    Words work too ("12 times 7", "2 to the power of 10").
    Returns the result, a formatted result_text, and steps (the working, one line each).
    """
    src = _prepare(expression)
    try:
        node = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ValueError(f"can't read {expression!r} as maths") from None
    original = _tree(node, [120])
    t = _percent_of_base(original)
    value = _check(_value(t))
    if isinstance(value, complex):
        raise ValueError("the result isn't a real number")
    lines = [_show(original)]
    if _show(t) != lines[0]:
        lines.append("= " + _show(t))
    cur = t
    for _ in range(12):
        if cur[0] == "num":
            break
        cur = _reduce_once(cur)
        if cur[0] != "num":
            lines.append("= " + _show(cur))
    result_text = fmt(value, 10)
    if lines[-1] != "= " + result_text and lines[0] != result_text:
        lines.append("= " + result_text)
    if len(lines) > 5:                                  # the question, the first steps, the answer
        lines = lines[:3] + ["= …"] + lines[-1:]
    return {"kind": "calc", "expression": lines[0], "result": value, "result_text": result_text, "steps": lines}


# ----------------------------------------------------------------- units
# category -> symbol -> (size in the category's base unit, names it's asked by)
UNITS: dict[str, dict[str, tuple[float, tuple[str, ...]]]] = {
    "length": {
        "nm": (1e-9, ("nanometre", "nanometer")), "µm": (1e-6, ("um", "micrometre", "micrometer", "micron")),
        "mm": (1e-3, ("millimetre", "millimeter", "мм")), "cm": (1e-2, ("centimetre", "centimeter", "см")),
        "m": (1.0, ("metre", "meter", "м")), "km": (1e3, ("kilometre", "kilometer", "км")),
        "in": (0.0254, ("inch", "inches", '"', "″")), "ft": (0.3048, ("foot", "feet", "'", "′")),
        "yd": (0.9144, ("yard", "yards")), "mi": (1609.344, ("mile", "miles")),
        "nmi": (1852.0, ("nautical mile", "nautical miles", "nm_sea")),
    },
    "mass": {
        "mg": (1e-6, ("milligram", "milligramme", "мг")), "g": (1e-3, ("gram", "gramme", "gr", "г")),
        "kg": (1.0, ("kilogram", "kilogramme", "kilo", "kilos", "кг")), "t": (1e3, ("tonne", "tonnes", "metric ton", "т")),
        "oz": (0.028349523125, ("ounce", "ounces")), "lb": (0.45359237, ("lbs", "pound", "pounds")),
        "st": (6.35029318, ("stone", "stones")),
    },
    "volume": {
        "ml": (1e-3, ("millilitre", "milliliter", "мл", "cc")), "cl": (1e-2, ("centilitre", "centiliter")),
        "dl": (0.1, ("decilitre", "deciliter")), "l": (1.0, ("litre", "liter", "л")),
        "m³": (1e3, ("m3", "cubic metre", "cubic meter")), "cm³": (1e-3, ("cm3", "cubic centimetre", "cubic centimeter")),
        "tsp": (0.00492892159375, ("teaspoon", "teaspoons")), "tbsp": (0.01478676478125, ("tablespoon", "tablespoons")),
        "fl oz": (0.0295735295625, ("floz", "fluid ounce", "fluid ounces")), "cup": (0.2365882365, ("cups",)),
        "pt": (0.473176473, ("pint", "pints")), "qt": (0.946352946, ("quart", "quarts")),
        "gal": (3.785411784, ("gallon", "gallons", "us gallon", "us gal")),
        "imp gal": (4.54609, ("imperial gallon", "imperial gallons", "uk gallon")),
        "ft³": (28.316846592, ("ft3", "cubic foot", "cubic feet")), "in³": (0.016387064, ("in3", "cubic inch", "cubic inches")),
    },
    "speed": {
        "m/s": (1.0, ("mps", "metres per second", "meters per second", "м/с")),
        "km/h": (1 / 3.6, ("kph", "kmh", "km/hr", "kilometres per hour", "kilometers per hour", "км/ч")),
        "mph": (0.44704, ("mi/h", "miles per hour")), "kn": (1852 / 3600, ("knot", "knots", "kt")),
        "ft/s": (0.3048, ("fps", "feet per second")),
    },
    "area": {
        "mm²": (1e-6, ("mm2", "square millimetre", "square millimeter")), "cm²": (1e-4, ("cm2", "square centimetre", "square centimeter")),
        "m²": (1.0, ("m2", "sq m", "square metre", "square meter", "sqm")), "km²": (1e6, ("km2", "sq km", "square kilometre", "square kilometer")),
        "ha": (1e4, ("hectare", "hectares")), "acre": (4046.8564224, ("acres", "ac")),
        "ft²": (0.09290304, ("ft2", "sq ft", "square foot", "square feet", "sqft")), "in²": (0.00064516, ("in2", "sq in", "square inch", "square inches")),
        "yd²": (0.83612736, ("yd2", "sq yd", "square yard", "square yards")), "mi²": (2589988.110336, ("mi2", "sq mi", "square mile", "square miles")),
        "sotka": (100.0, ("sotkas", "сотка", "соток")),
    },
    "data": {
        "bit": (0.125, ("bits", "b_bit")), "kbit": (125.0, ("kb_bit", "kilobit", "kilobits", "kbps")),
        "Mbit": (125e3, ("megabit", "megabits", "mbps")), "Gbit": (125e6, ("gigabit", "gigabits", "gbps")),
        "B": (1.0, ("byte", "bytes")), "KB": (1e3, ("kilobyte", "kilobytes", "kb")), "MB": (1e6, ("megabyte", "megabytes", "mb")),
        "GB": (1e9, ("gigabyte", "gigabytes", "gb")), "TB": (1e12, ("terabyte", "terabytes", "tb")),
        "PB": (1e15, ("petabyte", "petabytes", "pb")),
        "KiB": (1024.0, ("kib", "kibibyte", "kibibytes")), "MiB": (1024.0 ** 2, ("mib", "mebibyte", "mebibytes")),
        "GiB": (1024.0 ** 3, ("gib", "gibibyte", "gibibytes")), "TiB": (1024.0 ** 4, ("tib", "tebibyte", "tebibytes")),
    },
    "time": {
        "ns": (1e-9, ("nanosecond", "nanoseconds")), "µs": (1e-6, ("us", "microsecond", "microseconds")),
        "ms": (1e-3, ("millisecond", "milliseconds")), "s": (1.0, ("sec", "secs", "second", "seconds")),
        "min": (60.0, ("mins", "minute", "minutes")), "h": (3600.0, ("hr", "hrs", "hour", "hours")),
        "day": (86400.0, ("d", "days")), "week": (604800.0, ("wk", "weeks")),
        "month": (2629746.0, ("months", "mo")), "year": (31556952.0, ("yr", "yrs", "years")),
        "decade": (315569520.0, ("decades",)), "century": (3155695200.0, ("centuries",)),
    },
}
TEMPS = {"°C": ("c", "celsius", "centigrade", "degc", "deg c", "degrees celsius", "degree celsius", "℃"),
         "°F": ("f", "fahrenheit", "degf", "deg f", "degrees fahrenheit", "degree fahrenheit", "℉"),
         "K": ("k", "kelvin", "kelvins")}
_ALIAS: dict[str, tuple[str, str]] = {}
for _cat, _units in UNITS.items():
    for _sym, (_f, _names) in _units.items():
        for _n in (_sym, *_names):
            _ALIAS.setdefault(_n, (_cat, _sym))
            _ALIAS.setdefault(_n.lower(), (_cat, _sym))
for _sym, _names in TEMPS.items():
    for _n in (_sym, _sym.lower(), *_names):
        _ALIAS.setdefault(_n, ("temperature", _sym))


def _unit(u: str) -> tuple[str, str]:
    raw = re.sub(r"\s+", " ", str(u or "").strip().replace("^2", "²").replace("^3", "³"))
    for key in (raw, raw.lower(), raw.lower().rstrip("."), re.sub(r"^(degrees?|deg|°)\s*", "", raw.lower()),
                raw.lower().rstrip("s"), raw.lower().replace("square ", "sq ")):
        if key in _ALIAS:
            cat, sym = _ALIAS[key]
            # "Kb"/"kb" means bytes here unless spelled "kbit"; "B" and "b" both bytes
            return cat, sym
    raise ValueError(f"unknown unit {u!r}")


def _temp(v: float, a: str, b: str) -> tuple[float, list[str]]:
    n, show = _nice_t, lambda x: fmt(round(x, 2), decimals=2)
    src = f"{show(v)} {a}"
    if a == b:
        return v, [src]
    if a == "°C" and b == "°F":
        r = v * 9 / 5 + 32
        return r, [f"{src} × 9/5 + 32", f"= {show(v * 9 / 5)} + 32", f"= {n(r)} °F"]
    if a == "°F" and b == "°C":
        r = (v - 32) * 5 / 9
        return r, [f"({src} {MINUS} 32) × 5/9", f"= {show(v - 32)} × 5/9", f"= {n(r)} °C"]
    if a == "°C" and b == "K":
        r = v + 273.15
        return r, [f"{src} + 273.15", f"= {show(r)} K"]
    if a == "K" and b == "°C":
        r = v - 273.15
        return r, [f"{src} {MINUS} 273.15", f"= {n(r)} °C"]
    if a == "°F" and b == "K":
        c = (v - 32) * 5 / 9
        return c + 273.15, [f"({src} {MINUS} 32) × 5/9 + 273.15", f"= {show(c)} + 273.15", f"= {show(c + 273.15)} K"]
    c = v - 273.15                                   # K -> °F
    r = c * 9 / 5 + 32
    return r, [f"({src} {MINUS} 273.15) × 9/5 + 32", f"= {show(c)} × 9/5 + 32", f"= {n(r)} °F"]


def _nice_t(x: float) -> str:
    return fmt(round(x, 1), decimals=1)


def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    """Convert a value between units and show the formula used.

    Categories: length (mm cm m km in ft yd mi nmi), mass (mg g kg t oz lb st),
    temperature (C F K), volume (ml l m3 tsp tbsp cup fl oz pt qt gal, imp gal),
    speed (m/s km/h mph kn ft/s), area (cm2 m2 km2 ha acre ft2 sotka...), data (bit
    kbit Mbit B KB MB GB TB, and KiB MiB GiB TiB; KB/MB/GB are decimal) and time (ms s
    min h day week month year). Units can be symbols or names ("miles", "°F").
    Returns the result, result_text, the formula as steps, and the factor used.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} isn't a number") from None
    if math.isnan(v) or math.isinf(v):
        raise ValueError("the value isn't a finite number")
    ca, a = _unit(from_unit)
    cb, b = _unit(to_unit)
    if ca != cb:
        raise ValueError(f"can't convert {ca} ({a}) to {cb} ({b})")
    vi = int(v) if v.is_integer() and abs(v) < 1e15 else v
    out = {"kind": "units", "category": ca, "value": vi, "from": a, "to": b}
    if ca == "temperature":
        if a == "K" and v < 0 or a == "°C" and v < -273.15 or a == "°F" and v < -459.67:
            raise ValueError("that's below absolute zero")
        r, steps = _temp(v, a, b)
        txt = fmt(round(r, 2), decimals=2) if b == "K" else _nice_t(r)
        out.update(result=round(r, 6), result_text=txt, steps=steps,
                   note={"°C": "0 °C = 32 °F = 273.15 K", "°F": "32 °F = 0 °C", "K": "0 K = −273.15 °C"}[a])
        return out
    fa, fb = UNITS[ca][a][0], UNITS[ca][b][0]
    k = fa / fb
    r = v * k
    mul, div = _clean(k), _clean(1 / k)
    use_div = k < 1 and _digits(div) <= _digits(mul)
    src = f"{fmt(vi)} {a}"
    per = "/" not in a + b                              # "km/mi"; never "km/h/mph"
    if a == b:
        steps = [src]
    elif use_div:
        steps = [f"{src} ÷ {div}" + (f" {a}/{b}" if per else ""), f"= {_nice(r)} {b}"]
    else:
        steps = [f"{src} × {mul}" + (f" {b}/{a}" if per else ""), f"= {_nice(r)} {b}"]
    out.update(result=float(f"{r:.12g}"), result_text=_nice(r), steps=steps, factor=float(f"{k:.12g}"),
               note=f"1 {b} = {div} {a}" if use_div else f"1 {a} = {mul} {b}")
    return out


# ----------------------------------------------------------------- currency
CURRENCY_ALIASES = {
    "$": "USD", "us$": "USD", "dollar": "USD", "dollars": "USD", "us dollar": "USD", "us dollars": "USD", "bucks": "USD",
    "доллар": "USD", "долларов": "USD", "доллара": "USD", "dollr": "USD",
    "€": "EUR", "euro": "EUR", "euros": "EUR", "евро": "EUR",
    "£": "GBP", "pound": "GBP", "pounds": "GBP", "sterling": "GBP", "pound sterling": "GBP", "фунт": "GBP",
    "¥": "JPY", "yen": "JPY", "иена": "JPY", "йена": "JPY",
    "yuan": "CNY", "renminbi": "CNY", "rmb": "CNY", "юань": "CNY", "юаней": "CNY",
    "₽": "RUB", "ruble": "RUB", "rubles": "RUB", "rouble": "RUB", "roubles": "RUB", "рубль": "RUB", "рублей": "RUB",
    "рубля": "RUB", "rubl": "RUB",
    "sum": "UZS", "sums": "UZS", "som": "UZS", "soum": "UZS", "so'm": "UZS", "so‘m": "UZS", "сум": "UZS", "сумов": "UZS",
    "uzbek sum": "UZS", "uzbek som": "UZS",
    "₸": "KZT", "tenge": "KZT", "тенге": "KZT", "₩": "KRW", "won": "KRW", "вон": "KRW",
    "₺": "TRY", "lira": "TRY", "turkish lira": "TRY", "лира": "TRY", "₹": "INR", "rupee": "INR", "rupees": "INR",
    "dirham": "AED", "dirhams": "AED", "дирхам": "AED", "swiss franc": "CHF", "franc": "CHF", "francs": "CHF",
    "hryvnia": "UAH", "гривна": "UAH", "lari": "GEL", "лари": "GEL", "dram": "AMD", "somoni": "TJS",
    "kyrgyz som": "KGS", "manat": "AZN", "zloty": "PLN", "złoty": "PLN", "baht": "THB", "riyal": "SAR",
    "shekel": "ILS", "real": "BRL", "reais": "BRL", "peso": "MXN", "pesos": "MXN", "rand": "ZAR",
}
CURRENCY_NAMES = {
    "USD": "US dollar", "EUR": "euro", "GBP": "pound sterling", "JPY": "Japanese yen", "CNY": "Chinese yuan",
    "RUB": "Russian ruble", "UZS": "Uzbek sum", "KZT": "Kazakh tenge", "KRW": "South Korean won",
    "TRY": "Turkish lira", "INR": "Indian rupee", "AED": "UAE dirham", "CHF": "Swiss franc", "UAH": "Ukrainian hryvnia",
    "GEL": "Georgian lari", "AMD": "Armenian dram", "TJS": "Tajik somoni", "KGS": "Kyrgyz som", "AZN": "Azerbaijani manat",
    "PLN": "Polish zloty", "THB": "Thai baht", "SAR": "Saudi riyal", "ILS": "Israeli shekel", "BRL": "Brazilian real",
    "MXN": "Mexican peso", "ZAR": "South African rand", "CAD": "Canadian dollar", "AUD": "Australian dollar",
}
CACHE_FILE = Path(__file__).resolve().parents[2] / "state" / "cache" / "fx_usd.json"
_mem: dict = {}


def _fetch(url: str, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _code(c: str) -> str:
    s = re.sub(r"\s+", " ", str(c or "").strip())
    k = s.lower()
    if k in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[k]
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return s.upper()
    if k.rstrip("s") in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[k.rstrip("s")]
    raise ValueError(f"unknown currency {c!r} (use a 3-letter code such as USD, EUR, UZS)")


def _ttl_s() -> float:
    try:
        from heyclaude import mcp_info
        h = float(((mcp_info.config().get("convert") or {}).get("rates_ttl_h")) or 6)
    except (TypeError, ValueError, ImportError):
        h = 6.0
    return max(0.25, min(48.0, h)) * 3600


def _from_er_api() -> dict:
    v = _fetch("https://open.er-api.com/v6/latest/USD")
    if v.get("result") != "success" or not isinstance(v.get("rates"), dict):
        raise OSError(f"open.er-api.com: {v.get('error-type') or 'no rates'}")
    try:
        day = parsedate_to_datetime(v["time_last_update_utc"]).date().isoformat()
    except (KeyError, TypeError, ValueError):
        day = time.strftime("%Y-%m-%d", time.gmtime(v.get("time_last_update_unix") or time.time()))
    return {"rates": {k: float(x) for k, x in v["rates"].items()}, "date": day,
            "source": "ExchangeRate-API (open.er-api.com)", "fetched": time.time()}


def _from_frankfurter() -> dict:
    v = _fetch("https://api.frankfurter.dev/v1/latest?base=USD")
    rates = {k: float(x) for k, x in (v.get("rates") or {}).items()}
    if not rates:
        raise OSError("frankfurter.dev: no rates")
    rates["USD"] = 1.0
    return {"rates": rates, "date": str(v.get("date") or ""), "source": "ECB via frankfurter.dev", "fetched": time.time()}


def rates(now: float | None = None) -> dict:
    """USD-based rates {rates, date, source, fetched}: memory, then the cache file, then the network."""
    now = time.time() if now is None else now
    ttl = _ttl_s()
    if _mem and now - _mem.get("fetched", 0) < ttl:
        return _mem
    disk = None
    try:
        disk = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if now - float(disk.get("fetched", 0)) < ttl and disk.get("rates"):
            _mem.clear()
            _mem.update(disk)
            return _mem
    except (OSError, ValueError, TypeError):
        disk = None
    errors = []
    for get in (_from_er_api, _from_frankfurter):
        try:
            got = get()
        except (OSError, ValueError, KeyError) as e:
            errors.append(str(e))
            continue
        _mem.clear()
        _mem.update(got)
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(got), encoding="utf-8")
        except OSError:
            pass
        return _mem
    if disk and disk.get("rates") and now - float(disk.get("fetched", 0)) < 7 * 86400:
        return dict(disk, stale=True)                  # offline: the last rates we had, marked
    raise OSError("exchange rates are unavailable right now (" + "; ".join(errors)[:160] + ")")


def _rate_text(r: float) -> str:
    if r >= 1:
        return fmt(r, decimals=2 if r < 1e6 else 0)
    return fmt(r, 4)


def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert money between currencies at today's rate, with the working shown.

    Currencies are ISO codes (USD, EUR, RUB, UZS, KZT, GBP, JPY, CNY, KRW, TRY... 160+)
    or common names ("dollars", "euro", "rubles", "sum"). Rates are daily mid-market
    rates from open.er-api.com (keyless), cached for hours.
    Returns the result, result_text, the rate used, its date and source, and steps.
    """
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        raise ValueError(f"{amount!r} isn't an amount") from None
    if math.isnan(amt) or math.isinf(amt):
        raise ValueError("the amount isn't a finite number")
    a, b = _code(from_currency), _code(to_currency)
    fx = rates()
    table = fx["rates"]
    for c in (a, b):
        if c not in table:
            raise ValueError(f"no rate for {c}")
    rate = table[b] / table[a]
    res = amt * rate
    ai = int(amt) if amt.is_integer() and abs(amt) < 1e15 else amt
    dec = 0 if abs(res) >= 10000 else 2
    res_txt = fmt(round(res, dec), decimals=dec, strip=False)     # money keeps its cents
    rt = _rate_text(rate)
    out = {"kind": "currency", "amount": ai, "from": a, "to": b, "rate": float(f"{rate:.8g}"),
           "result": round(res, 2), "result_text": res_txt,
           "steps": [f"{fmt(ai)} {a} × {rt} {b}/{a}" if rate >= 1 else f"{fmt(ai)} {a} ÷ {_rate_text(1 / rate)} {a}/{b}",
                     f"= {res_txt} {b}"],
           "note": f"1 {a} = {rt} {b}" if rate >= 1 else f"1 {b} = {_rate_text(1 / rate)} {a}", "date": fx.get("date", ""), "source": fx.get("source", "")}
    if a in CURRENCY_NAMES:
        out["from_name"] = CURRENCY_NAMES[a]
    if b in CURRENCY_NAMES:
        out["to_name"] = CURRENCY_NAMES[b]
    if fx.get("stale"):
        out["stale"] = True
    return out


TOOLS = [calculate, convert_units, convert_currency]
