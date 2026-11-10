"""M10: instant commands without Claude (a local intent engine).

    engine.py      IntentEngine.match(text) -> Match | None (grammar, context, taught, classifier)
    grammar.py     the anchored patterns, one set per command
    grammar_ru.py  the same for Russian (Cyrillic), with its numbers and durations
    grammar_uz.py  the same for Uzbek (Latin, or Cyrillic transliterated), with its numbers
    slots.py       spoken numbers, amounts, durations, on/off
    normalize.py   clean-up and Whisper slips
    classifier.py  numpy TF-IDF nearest neighbours for paraphrases (timid)
    executors.py   Executor.run(match) -> Outcome, through M9's tool registry
    timers.py      the local timer scheduler
    learn.py       missed phrasings (state/) and "teach it"

brain.Brain.instant(text) asks the engine; assistant._instant runs the match.
"""
from .engine import IntentEngine, Match  # noqa: F401
from .executors import Executor, Outcome  # noqa: F401
from .timers import Timers  # noqa: F401
