"""Хук Claude Code: после правки кода, правил или тестов прогоняет python -m unittest.

Если тесты падают, возвращает код 2: Claude видит ошибку и исправляет её сразу.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WATCHED = ("diary/", "data/", "tests/", "app.py")

event = json.load(sys.stdin)
path = (event.get("tool_input") or {}).get("file_path", "")
try:
    rel = Path(path).resolve().relative_to(ROOT).as_posix()
except ValueError:
    sys.exit(0)
if not rel.startswith(WATCHED) or rel.startswith("data/user/"):
    sys.exit(0)

run = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=ROOT, capture_output=True, text=True, timeout=120)
if run.returncode != 0:
    print("Тесты упали после правки " + rel + ":\n" + run.stderr[-3000:], file=sys.stderr)
    sys.exit(2)
