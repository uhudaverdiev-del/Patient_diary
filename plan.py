"""Показать персональный план для демо-пациента.

Пример:  python plan.py data/demo/anna.json
         python plan.py data/demo/anna.json --today 2026-10-24
"""

import argparse
import json
from datetime import date

from diary.engine import build_plan

MARKS = {"overdue": "[!] ", "soon": "[~] ", "actual": "[ok]"}


def fmt(d):
    return date.fromisoformat(d).strftime("%d.%m.%Y") if d else "—"


def main():
    parser = argparse.ArgumentParser(description="Персональный план профилактики")
    parser.add_argument("patient", help="JSON-файл с профилем и историей обследований")
    parser.add_argument("--today", help="дата расчёта ГГГГ-ММ-ДД (по умолчанию сегодня)")
    args = parser.parse_args()

    with open(args.patient, encoding="utf-8") as f:
        demo = json.load(f)
    plan = build_plan(demo["profile"], demo.get("history", []), today=args.today)

    print(demo.get("name", args.patient))
    for problem in plan["problems"]:
        print("  ВНИМАНИЕ:", problem["message"])
    if not plan["ready"]:
        return
    s = plan["summary"]
    print(f"  Возраст: {plan['age']}. Мероприятий: {s['total']}, "
          f"просрочено: {s['counts']['overdue']}, скоро: {s['counts']['soon']}, "
          f"актуально: {s['counts']['actual']} ({s['progress_percent']}%)\n")
    for item in plan["items"]:
        print(f"{MARKS[item['status']]} {item['title']}")
        print(f"      {item['status_label']}; было: {fmt(item['last_date'])}; следующее: {fmt(item['next_due'])}")
        print(f"      кому: {item['who']}; основание: {item['source']['clause']} приказа № 404н")
        for note in item["notes"]:
            print(f"      {note}")


if __name__ == "__main__":
    main()
