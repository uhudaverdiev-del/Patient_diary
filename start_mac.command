#!/bin/bash
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  python3 app.py --open
else
  echo "Python не найден. Установите Python 3 с сайта https://www.python.org/downloads/"
  read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть окно"
fi
