"""«Как подготовиться»: общие рекомендации перед обследованиями и прививками.

Тексты лежат в data/preparation.json, их проверяет медик команды.
"""

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "preparation.json"


@lru_cache(maxsize=1)
def load_preparation():
    return json.loads(PATH.read_text(encoding="utf-8"))


def prep_for(test):
    """{before, bring, note} или None, если для обследования текста нет."""
    data = load_preparation()
    prep = data["tests"].get(test)
    if prep is None:
        return None
    return {"before": prep["before"], "bring": data["common_bring"] + prep["bring"], "note": data["note"]}


def bring_list(tests):
    """Что взять с собой на все перечисленные обследования, без повторов."""
    seen = {}
    for test in tests:
        prep = prep_for(test)
        for thing in prep["bring"] if prep else []:
            seen.setdefault(thing, None)
    return list(seen)
