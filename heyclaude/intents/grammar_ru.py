"""Russian phrasings for the core instant commands (Whisper writes Russian in Cyrillic).

Same rules as grammar.py: every pattern must match the WHOLE utterance, and a
slot that doesn't parse makes the rule fail ("какой объём у шара", "громкость
звука в физике", "сколько времени займёт дорога" never act). The text arrives
from normalize.basic(): lower case, ё -> е, punctuation out, "30%" -> "30 percent".

Covered: volume set / up / down / mute / unmute, brightness set / up / down,
dark / light mode, next / previous / pause / play, timers, the time and date,
open <app>, lock, "запомни это" (teach it), and follow-ups ("ещё", "меньше",
"ещё раз", "наоборот", "на 40"). The acks stay English (Kokoro has no Russian).
"""
from __future__ import annotations

import re

from . import grammar as G

RULES: list = []


def rule(intent, *patterns):
    return G.rule(intent, *patterns, into=RULES)


# -- numbers, levels, amounts, durations -----------------------------------------------------
_UNITS = {"ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одну": 1, "одно": 1, "одного": 1, "одной": 1,
          "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5,
          "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9}
_TEENS = {"десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
          "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
_TEENS.update({k[:-1] + "и": v for k, v in list(_TEENS.items())})          # десяти, пятнадцати...
_TENS = {"двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40,
         "пятьдесят": 50, "пятидесяти": 50, "шестьдесят": 60, "шестидесяти": 60, "семьдесят": 70,
         "семидесяти": 70, "восемьдесят": 80, "восьмидесяти": 80, "девяносто": 90, "девяноста": 90}
_HUNDRED = {"сто": 100, "ста": 100, "сотни": 100, "сотню": 100}
PCT = r"(?:\s+(?:процент\w*|проц|percent|пункт\w*))?"


def parse_int(s: str) -> int | None:
    """'30', 'тридцать', 'тридцать пять', 'сто', 'тридцати' (до тридцати)."""
    words = " ".join((s or "").split()).split()
    if not words:
        return None
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    total, seen, last = 0, False, None
    for w in words:
        if w in _HUNDRED and not seen:
            total, last = 100, "h"
        elif w in _TENS and last in (None, "h"):
            total, last = total + _TENS[w], "t"
        elif w in _TEENS and last in (None, "h"):
            total, last = total + _TEENS[w], "u"
        elif w in _UNITS and last in (None, "h", "t"):
            total, last = total + _UNITS[w], "u"
        elif w.isdigit() and not seen:
            total, last = int(w), "u"
        else:
            return None
        seen = True
    return total if seen else None


_LEVEL_WORDS = {"максимум": 100, "максимума": 100, "максимальную": 100, "максимальная": 100, "максимальный": 100,
                "полную": 100, "всю": 100, "полную громкость": 100, "сто процентов": 100, "упора": 100,
                "минимум": 0, "минимума": 0, "минимальную": 0, "минимальная": 0, "минимальный": 0, "ноль": 0, "нуль": 0,
                "половину": 50, "наполовину": 50, "половина": 50, "середину": 50, "треть": 33, "четверть": 25}


def parse_level(s: str) -> int | None:
    s = " ".join((s or "").split())
    s = re.sub(r"^(?:на|до|в)\s+", "", s)
    s = re.sub(r"\s+(?:процент\w*|проц|percent)$", "", s)
    s = re.sub(r"^(?:примерно|около|где-то|ровно|прямо)\s+", "", s)
    if s in _LEVEL_WORDS:
        return _LEVEL_WORDS[s]
    n = parse_int(s)
    return n if n is not None and 0 <= n <= 100 else None


_SMALL = {"немного", "немножко", "чуть", "чуть чуть", "чуточку", "слегка", "капельку", "капельку ещё", "малость",
          "самую малость", "совсем немного", "чуть-чуть", "немного ещё", "ещё немного", "ещё чуть", "чуть ещё",
          "ещё чуть чуть", "на чуть чуть", "на немного", "не много", "чу чуть"}
_SMALL = {w.replace("ё", "е") for w in _SMALL}
_LARGE = {"намного", "гораздо", "сильно", "значительно", "очень", "куда", "прилично", "сильнее", "много",
          "гораздо сильнее", "намного сильнее"}


def parse_amount(s: str, step: int = 10) -> int | None:
    """'' -> step, 'немного' -> half a step, 'намного' -> 2.5 steps, 'на 20 (процентов)' -> 20."""
    s = " ".join((s or "").split())
    s = re.sub(r"^(?:ещё|еще)\s*", "", s).strip() if s not in _SMALL else s
    if not s or s in ("ещё", "еще"):
        return step
    if s in _SMALL:
        return max(1, round(step * 0.5))
    if s in _LARGE:
        return round(step * 2.5)
    s = re.sub(r"^на\s+", "", s)
    s = re.sub(r"\s+(?:процент\w*|проц|percent|пункт\w*|делени\w*)$", "", s)
    n = parse_int(s)
    return n if n is not None and 1 <= n <= 100 else None


_DUR_UNITS = {}
for _w in ("секунда", "секунду", "секунды", "секунд", "сек", "секундочку"):
    _DUR_UNITS[_w] = 1
for _w in ("минута", "минуту", "минуты", "минут", "мин", "минутку", "минуток"):
    _DUR_UNITS[_w] = 60
for _w in ("час", "часа", "часов", "часик"):
    _DUR_UNITS[_w] = 3600
_DUR_WHOLE = {"полчаса": 1800, "пол часа": 1800, "полминуты": 30, "пол минуты": 30, "полтора часа": 5400,
              "полторы минуты": 90, "четверть часа": 900, "полторы часа": 5400}
_ONE = {"час", "минуту", "минутку", "секунду", "секундочку", "часик"}


def parse_duration(s: str) -> int | None:
    """Seconds: 'пять минут', '10 минут', 'полчаса', 'час', 'полтора часа', '1 час 30 минут', '30 секунд'."""
    s = " ".join((s or "").split())
    if s in _DUR_WHOLE:
        return _DUR_WHOLE[s]
    toks = s.split()
    total, buf, units = 0, [], 0
    for t in toks:
        if t in _DUR_UNITS:
            while buf and buf[0] == "и":
                buf = buf[1:]
            if not buf and t in _ONE:
                q = 1
            else:
                q = parse_int(" ".join(buf))
            if q is None or q <= 0:
                return None
            total += q * _DUR_UNITS[t]
            buf, units = [], units + 1
            continue
        buf.append(t)
    if buf or not units:
        return None
    return total if 0 < total <= 86400 else None


# -- filler ---------------------------------------------------------------------------------
_LEAD = re.compile(
    r"^(?:(?:эй|ей|хей|окей|ок|ну|слушай|послушай|так|а|и|ладно|пожалуйста|будь добр|будь добра|будь любезен|"
    r"давай|давайте|можешь|можете|ты можешь|вы можете|можешь ли ты|не мог бы ты|не могла бы ты|не могли бы вы|"
    r"сможешь|просто|быстро|теперь|сейчас|хм|эм|ээ|мм|клод|клоуд|клауд|клод клод|hey|claude)\s+)+")
_TAIL = re.compile(r"(?:\s+(?:пожалуйста|плиз|спасибо|быстро|теперь|ок|окей|клод|ладно|давай|прямо сейчас|"
                   r"сейчас же|срочно))+$")


def prep(t: str) -> str:
    t = t.replace("ё", "е")
    prev = None
    while prev != t:
        prev = t
        t = _LEAD.sub("", t).strip()
        t = _TAIL.sub("", t).strip()
    return t


# -- building blocks ---------------------------------------------------------------------------
NUM = r"(?P<n>[а-яa-z0-9 ]+?)"
AMT = r"(?P<a>[а-яa-z0-9 ]+?)"
AMT2 = r"(?P<a2>[а-яa-z0-9 ]+?)"
VOLN = (r"(?:(?:уровень|уровня)\s+(?:громкости|звука)|громкость звука|громкость|громкости|звук|звука|"
        r"музыку|музыка)")
SETV = r"(?:постав|сдела|установ|выстав|измен|смен|включ|переключ|верн)\w*"
UPV = r"(?:прибав|добав|увелич|подним|подня|повыс|усил|подкрут)\w*"
DOWNV = r"(?:убав|уменьш|сниз|пониз|опуст|приглуш|сбав|скрут|ослаб)\w*"
BRN = r"(?:яркость экрана|яркость монитора|яркость дисплея|яркость|яркости|подсветку|подсветка|подсветки)"
SCREEN = r"(?:экран|экрана|монитор|монитора|дисплей|дисплея)"


def _dir(v: str) -> str | None:
    if re.fullmatch(UPV + r"|выше|больше|громче|погромче|вверх|плюс|ярче|поярче|повыше|побольше", v):
        return "up"
    if re.fullmatch(DOWNV + r"|ниже|меньше|тише|потише|вниз|минус|темнее|потемнее|тусклее|поменьше|пониже", v):
        return "down"
    return None


def _step(m, ctx):
    d = G._g(m, "d") or G._g(m, "v")
    if G._g(m, "too"):
        d = "тише" if G._g(m, "too") in ("громко", "ярко", "яркий") else "громче"
    direction = _dir(d)
    if direction is None:
        return None
    amt = parse_amount(G._g(m, "a") or G._g(m, "a2"), ctx.step)
    if amt is None:
        return None
    return {"delta": amt if direction == "up" else -amt}


def _level(m, ctx):
    lv = parse_level(G._g(m, "n"))
    return None if lv is None else {"level": lv}


# == volume ===================================================================================
@rule("volume.mute",
      r"(?:выключи|выключите|выключить|отключи|отключите|отключить|убери|уберите|заглуши|заглушите|выруби|вырубите)"
      r"\s+(?:весь\s+|совсем\s+)?(?:звук|звуки)(?:\s+(?:совсем|полностью|на компьютере))?",
      r"звук\s+(?:выключи|выключить|отключи|отключить|выкл)",
      r"(?:(?:включи|поставь|сделай)\s+)?(?:беззвучный режим|режим без звука|без звука)",
      r"(?:замьють|замуть|мьют|мут)(?:\s+звук)?")
def _mute(m, ctx):
    return {}


@rule("volume.unmute",
      r"(?:включи|включите|включить|верни|верните|вернуть)\s+звук(?:\s+(?:обратно|назад|снова))?",
      r"звук\s+(?:включи|включить|верни|обратно|вкл)",
      r"(?:сними|убери|выключи)\s+(?:с\s+)?(?:беззвучного(?: режима)?|беззвучный режим|мьюта|мьют|режима без звука)",
      r"(?:размьють|анмьют|размуть)")
def _unmute(m, ctx):
    return {}


@rule("volume.set",
      r"(?:" + SETV + r"\s+)?" + VOLN + r"\s+(?:на\s+|в\s+|до\s+)?" + NUM + PCT,
      r"(?:" + UPV + "|" + DOWNV + r")\s+" + VOLN + r"\s+до\s+" + NUM + PCT,
      VOLN + r"\s+(?:" + UPV + "|" + DOWNV + r")\s+до\s+" + NUM + PCT,
      VOLN + r"\s+" + SETV + r"\s+(?:до|на)\s+" + NUM + PCT,
      r"(?:" + SETV + r"\s+)?(?:на\s+)?" + NUM + PCT + r"\s+(?:громкост\w*|звука)",
      r"(?:" + SETV + r"\s+)?(?P<n>максимальн\w*|минимальн\w*)\s+(?:громкост\w*|звук)",
      r"(?:" + UPV + r"\s+)?" + VOLN + r"\s+(?P<n>на максимум|до максимума|на полную|на всю|до упора)")
def _vol_set(m, ctx):
    return _level(m, ctx)


@rule("volume.step",
      r"(?:" + AMT + r"\s+)?(?P<v>" + UPV + "|" + DOWNV + r")\s+" + VOLN + r"(?:\s+" + AMT2 + r")?",
      VOLN + r"\s+(?:" + AMT + r"\s+)?(?P<d>выше|ниже|больше|меньше|громче|тише|погромче|потише|вверх|вниз|плюс|минус)"
      r"(?:\s+" + AMT2 + r")?",
      r"(?:(?:сделай|сделайте|сделать|можно|включи|давай)\s+)?(?:(?:музыку|звук|громкость|все|это|ее|его)\s+)?"
      r"(?:" + AMT + r"\s+)?(?P<d>громче|тише|погромче|потише)(?:\s+" + AMT2 + r")?",
      r"(?:(?:музыка|звук|тут|здесь|музыка играет)\s+)?(?:слишком|чересчур)\s+(?P<too>громко|тихо)(?:\s+(?:тут|здесь))?")
def _vol_step(m, ctx):
    return _step(m, ctx)


# "убавь" / "прибавь чуть-чуть" with nothing named: the volume, or the brightness right after changing it.
_BARE = r"(?P<v>прибавь|прибавьте|убавь|убавьте)(?:\s+" + AMT + r")?"


@rule("volume.step", _BARE)
def _vol_bare(m, ctx):
    return None if ctx.domain == "brightness" else _step(m, ctx)


@rule("brightness.step", _BARE)
def _br_bare(m, ctx):
    return _step(m, ctx) if ctx.domain == "brightness" else None


# == brightness =================================================================================
@rule("brightness.set",
      r"(?:" + SETV + r"\s+)?" + BRN + r"\s+(?:на\s+|в\s+|до\s+)?" + NUM + PCT,
      r"(?:" + UPV + "|" + DOWNV + r")\s+" + BRN + r"\s+до\s+" + NUM + PCT,
      r"(?:" + SETV + r"\s+)?(?:на\s+)?" + NUM + PCT + r"\s+яркост\w*",
      r"(?:" + SETV + r"\s+)?(?P<n>максимальн\w*|минимальн\w*)\s+яркост\w*",
      r"(?:" + UPV + r"\s+)?" + BRN + r"\s+(?P<n>на максимум|до максимума|на полную|до упора)")
def _br_set(m, ctx):
    return _level(m, ctx)


@rule("brightness.step",
      r"(?:" + AMT + r"\s+)?(?P<v>" + UPV + "|" + DOWNV + r")\s+" + BRN + r"(?:\s+" + AMT2 + r")?",
      BRN + r"\s+(?:" + AMT + r"\s+)?(?P<d>выше|ниже|больше|меньше|вверх|вниз|плюс|минус|ярче|темнее)"
      r"(?:\s+" + AMT2 + r")?",
      r"(?:(?:сделай|сделайте|сделать)\s+)?(?:" + SCREEN + r"\s+)?(?:" + AMT + r"\s+)?(?P<d>ярче|поярче|тусклее)"
      r"(?:\s+" + AMT2 + r")?",
      r"(?:сделай|сделайте|сделать)\s+" + SCREEN + r"\s+(?:" + AMT + r"\s+)?(?P<d>темнее|потемнее)(?:\s+" + AMT2 + r")?",
      r"(?P<v>приглуши|затемни)\s+" + SCREEN + r"(?:\s+" + AMT2 + r")?",
      SCREEN + r"\s+(?:слишком|чересчур)\s+(?P<too>яркий|ярко|темный|темно|тусклый)")
def _br_step(m, ctx):
    too = G._g(m, "too")
    v = G._g(m, "v")
    if too:
        direction = "down" if too in ("яркий", "ярко") else "up"
        return {"delta": ctx.step if direction == "up" else -ctx.step}
    if v in ("приглуши", "затемни"):
        amt = parse_amount(G._g(m, "a2"), ctx.step)
        return None if amt is None else {"delta": -amt}
    return _step(m, ctx)


# == dark / light mode ===========================================================================
_THEME_N = r"(?:тему|тема|темы|режим|режима|оформление)"
_MODE = r"(?P<m>темн\w*|светл\w*)"


@rule("theme.set",
      r"(?:(?:включи|включите|включить|поставь|поставьте|сделай|сделайте|переключи|переключись|переключить|"
      r"перейди|перейти|активируй|хочу)\s+(?:на\s+)?)?" + _MODE + r"\s+" + _THEME_N +
      r"(?:\s+(?:в windows|на компьютере|на ноутбуке|везде|обратно|снова))?",
      r"(?P<off>выключи|выключите|отключи|отключите|убери|уберите)\s+" + _MODE + r"\s+" + _THEME_N,
      r"(?:смени|поменяй|переключи|измени|сделай|поставь)\s+(?:тему|оформление|режим|windows)\s+на\s+" + _MODE)
def _theme(m, ctx):
    mode = "dark" if G._g(m, "m").startswith("темн") else "light"
    if G._g(m, "off"):
        mode = {"dark": "light", "light": "dark"}[mode]
    return {"mode": mode}


# == media ======================================================================================
_TRACK = r"(?:песн\w*|трек\w*|композици\w*|видео|видос)"


@rule("media.next",
      r"(?:(?:включи|включите|поставь|поставьте|давай|переключи на|перейди на|играй)\s+)?следующ\w*\s+" + _TRACK,
      r"(?:переключи|переключите|пропусти|пропустите|смени|поменяй|скипни|скипай|промотай)\s+(?:эту\s+|этот\s+)?" + _TRACK,
      r"(?:включи|поставь|давай)\s+(?:другую|другой)\s+(?:песню|трек|музыку)",
      r"(?:следующ\w*|дальше|некст|пропусти|скипни)")
def _next(m, ctx):
    if re.fullmatch(r"следующ\w*|дальше|некст|пропусти|скипни", m.group(0)) and ctx.domain != "media":
        return None
    return {"action": "next"}


@rule("media.previous",
      r"(?:(?:включи|включите|поставь|поставьте|давай|верни|верните|переключи на|вернись на|вернись к|вернуться к)\s+)?"
      r"(?:предыдущ|прошл|прежн)\w*\s+" + _TRACK,
      r"(?:предыдущ\w*|назад|верни|вернись)")
def _prev(m, ctx):
    if re.fullmatch(r"предыдущ\w*|назад|верни|вернись", m.group(0)) and ctx.domain != "media":
        return None
    return {"action": "previous"}


@rule("media.pause",
      r"(?:пауза|на паузу|паузу)",
      r"(?:поставь|поставьте|поставить|сделай)\s+(?:(?:музыку|песню|трек|видео|воспроизведение|это|ее|его)\s+)?на\s+паузу",
      r"(?:останови|остановите|приостанови|приостановите|стоп|выключи|выключите|паузни|запаузь|тормозни)\s+"
      r"(?:музыку|песню|трек|воспроизведение|видео|плеер|музыка)")
def _pause(m, ctx):
    return {"action": "pause"}


@rule("media.play",
      r"(?:продолжи|продолжай|продолжите|возобнови|возобновите)\s+(?:музыку|воспроизведение|играть|песню|трек|видео)",
      r"(?:включи|включите|запусти|запустите|поставь)\s+(?:музыку|песню|трек|воспроизведение)(?:\s+(?:снова|обратно|опять))?",
      r"(?:сними|снимите|убери)\s+(?:с\s+)?паузы?",
      r"(?:играй|плей|воспроизведи|воспроизведение)")
def _play(m, ctx):
    return {"action": "play"}


# == timers ======================================================================================
_DUR = r"(?P<d>[а-я0-9 ]+?)"


@rule("timer.set",
      r"(?:(?:поставь|поставьте|постави|установи|установите|заведи|заведите|запусти|запустите|включи|сделай|создай)\s+)?"
      r"таймер\s+(?:на\s+)?" + _DUR,
      r"(?:засеки|засекай|засеките|отсчитай|отсчитайте|отмерь)\s+(?:мне\s+)?" + _DUR,
      r"(?:поставь|поставьте|установи|заведи)\s+(?:на\s+)?" + _DUR + r"\s+таймер")
def _timer_set(m, ctx):
    secs = parse_duration(G._g(m, "d"))
    return None if secs is None else {"seconds": secs}


@rule("timer.cancel",
      r"(?:отмени|отмените|выключи|выключите|останови|остановите|сбрось|сбросьте|убери|уберите|удали|удалите|"
      r"отключи|отключите)\s+(?:все\s+)?таймер\w*",
      r"таймер\w*\s+(?:отмени|отменить|выключи|выключить|стоп|отбой)")
def _timer_cancel(m, ctx):
    return {}


@rule("timer.status",
      r"сколько\s+(?:времени\s+|минут\s+)?осталось\s+(?:на|по|до конца)\s+таймер\w*",
      r"(?:что|как)\s+(?:там\s+)?с\s+таймером")
def _timer_status(m, ctx):
    return {}


# == the clock ======================================================================================
@rule("clock.time",
      r"который\s+(?:сейчас\s+)?час(?:\s+сейчас)?",
      r"сколько\s+(?:сейчас\s+)?(?:времени|время)(?:\s+сейчас)?",
      r"(?:скажи|подскажи|скажите|подскажите|назови)\s+(?:мне\s+)?(?:который час|сколько времени|(?:текущее\s+)?время)",
      r"какое\s+(?:сейчас\s+)?время(?:\s+сейчас)?")
def _time(m, ctx):
    return {}


@rule("clock.date",
      r"какое\s+(?:сегодня\s+)?число(?:\s+сегодня)?",
      r"какой\s+(?:сегодня\s+)?день(?:\s+недели)?(?:\s+сегодня)?",
      r"какая\s+сегодня\s+дата|(?:скажи|подскажи)\s+(?:сегодняшнюю\s+)?дату")
def _date(m, ctx):
    r = m.group(0)
    if r.startswith("какой") and "сегодня" not in r and "недели" not in r:
        return None                                    # "какой день" alone is too vague
    return {}


# == lock ==========================================================================================
@rule("screen.lock",
      r"(?:заблокируй|заблокируйте|заблокировать|заблочь|залочь|блокируй)\s+(?:мой\s+|этот\s+)?"
      r"(?:компьютер|комп|пк|ноутбук|ноут|экран|систему|windows|виндовс)(?:\s+сейчас)?",
      r"блокировка\s+экрана")
def _lock(m, ctx):
    return {}


# == apps ==========================================================================================
# Russian spoken name -> the English key grammar.resolve_app knows.
APPS = {
    "спотифай": "spotify", "спотифи": "spotify", "спотифай приложение": "spotify",
    "хром": "chrome", "гугл хром": "chrome", "гугл": "chrome", "эдж": "edge", "майкрософт эдж": "edge",
    "файрфокс": "firefox", "фаерфокс": "firefox", "блокнот": "notepad", "калькулятор": "calculator",
    "проводник": "file explorer", "мои файлы": "file explorer", "настройки": "settings", "параметры": "settings",
    "пейнт": "paint", "паинт": "paint", "ворд": "word", "эксель": "excel", "ексель": "excel",
    "пауэрпоинт": "powerpoint", "поверпоинт": "powerpoint", "аутлук": "outlook", "тимс": "teams",
    "дискорд": "discord", "стим": "steam", "слак": "slack", "телеграм": "telegram", "телеграмм": "telegram",
    "ватсап": "whatsapp", "вотсап": "whatsapp", "вацап": "whatsapp", "вс код": "vs code", "вскод": "vs code",
    "визуал студио код": "vs code", "зум": "zoom", "ножницы": "snipping tool", "фото": "photos",
    "фотографии": "photos", "камеру": "camera", "камера": "camera", "календарь": "calendar app",
    "почту": "mail app", "почта": "mail app", "магазин": "store", "диспетчер задач": "task manager",
    "обсидиан": "obsidian", "ноушн": "notion", "фигму": "figma", "фигма": "figma", "фотошоп": "photoshop",
    "блендер": "blender", "обс": "obs", "влс": "vlc", "премьер": "premiere", "премьер про": "premiere pro",
}


def app_key(want: str) -> str:
    want = " ".join(want.split())
    want = re.sub(r"^(?:приложение|программу|прогу)\s+", "", want)
    return APPS.get(want, want)


@rule("app.open",
      r"(?:открой|откройте|открыть|запусти|запустите|запустить|включи|включите)\s+(?:мне\s+)?(?P<app>[а-яa-z0-9 .]+?)")
def _app(m, ctx):
    return G.open_app(app_key(G._g(m, "app")), ctx)


# == teach =========================================================================================
@rule("learn.teach", r"(?:запомни|выучи|заучи)\s+(?:это|эту фразу|эту команду|так)")
def _teach(m, ctx):
    return {}


# == follow-ups ======================================================================================
@rule("context.more",
      r"(?:ещ[её])(?:\s+" + AMT + r")?",
      r"(?:(?P<a2>[а-я0-9 ]+?)\s+)?(?P<w>больше|выше|побольше|повыше)(?:\s+(?:ещё|еще))?")
def _more(m, ctx):
    a = G._g(m, "a") or G._g(m, "a2")
    if a in ("раз",):
        return None                                     # "ещё раз" is "again"
    amt = parse_amount(a, ctx.step)
    if amt is None:
        return None
    out = {"direction": "up", "amount": amt}
    if not G._g(m, "w") or G._g(m, "w") in ("больше", "побольше"):
        out["keep"] = True                              # "тише" ... "ещё" = quieter still
    return out


@rule("context.less", r"(?:(?P<a>[а-я0-9 ]+?)\s+)?(?:меньше|ниже|поменьше|пониже)(?:\s+(?:ещё|еще))?")
def _less(m, ctx):
    amt = parse_amount(G._g(m, "a"), ctx.step)
    return None if amt is None else {"direction": "down", "amount": amt}


@rule("context.again", r"(?:давай\s+)?(?:ещё|еще)\s+раз|повтори|снова|опять")
def _again(m, ctx):
    return {}


@rule("context.reverse", r"(?:нет\s+)?(?:наоборот|в другую сторону|не туда|обратно)")
def _reverse(m, ctx):
    return {}


@rule("context.set",
      r"(?:сделай|поставь|давай)\s+(?:на\s+)?" + NUM + PCT,
      r"на\s+" + NUM + PCT,
      NUM + r"\s+(?:процент\w*|percent)")
def _ctx_set(m, ctx):
    lv = parse_level(G._g(m, "n"))
    return None if lv is None else {"level": lv}


def parse(t: str, ctx: G.Ctx):
    return G.parse(prep(t), ctx, RULES)


def parse_context(t: str, ctx: G.Ctx):
    return G.parse_context(prep(t), ctx, RULES)
