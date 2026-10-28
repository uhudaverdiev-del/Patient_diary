"""Напоминания и файл для календаря телефона."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from diary.engine import build_plan
from diary.reminders import reminders, to_ics

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo"
TODAY = "2026-10-24"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def demo_plan(name, today=TODAY):
    demo = json.loads((DEMO / f"{name}.json").read_text(encoding="utf-8"))
    return build_plan(demo["profile"], demo["history"], today=today)


class RemindersTest(unittest.TestCase):
    def test_overdue_first_then_soon(self):
        items = reminders(demo_plan("anna"))
        self.assertTrue(items)
        flags = [r["overdue"] for r in items]
        self.assertEqual(flags, sorted(flags, reverse=True))
        self.assertIn("просрочено с", items[0]["text"])

    def test_far_future_is_not_reminded(self):
        plan = demo_plan("anna")
        due = {r["id"] for r in reminders(plan)}
        for item in plan["items"]:
            if item["status"] == "actual" and item["next_due"] > "2026-12-31":
                self.assertNotIn(item["id"], due)

    def test_no_data_reminded_after_overdue(self):
        items = reminders(demo_plan("anna"))
        no_data = [r for r in items if r["no_data"]]
        self.assertEqual(len(no_data), 4)
        self.assertEqual(no_data[0]["text"], "нет отметки о прохождении, пройдите при ближайшем визите.")
        self.assertTrue(items[0]["overdue"])

    def test_days_word(self):
        plan = {"ready": True, "today": "2026-10-01", "items": [
            {"id": "x", "test": "x", "title": "X", "next_due": "2026-10-22", "status": "soon"}]}
        self.assertEqual(reminders(plan)[0]["text"], "через 21 день, до 22.10.2026.")

    def test_not_ready_plan(self):
        self.assertEqual(reminders(demo_plan("no_birth_date")), [])
        self.assertIn("END:VCALENDAR", to_ics(demo_plan("no_birth_date")))


class IcsTest(unittest.TestCase):
    def setUp(self):
        self.plan = demo_plan("anna")
        self.ics = to_ics(self.plan, now=NOW)
        self.text = self.ics.replace("\r\n ", "")

    def test_events_and_alarms(self):
        dated = [i for i in self.plan["items"] + self.plan["vaccines"] if i["next_due"] and not i["conditional"]]
        grouped = [i for i in dated if i["status"] in ("overdue", "no_data")]
        n = len(dated) - len(grouped) + 1
        self.assertEqual(self.ics.count("BEGIN:VEVENT"), n)
        self.assertEqual(self.ics.count("BEGIN:VALARM"), 2 * n)

    def test_overdue_grouped_a_week_ahead(self):
        self.assertIn("SUMMARY:Записаться в поликлинику: 10 обследований", self.text)
        self.assertIn("• ЭКГ в покое — срок 10.05.2024", self.text)
        self.assertIn("Нет отметки о прохождении:\\n• Измерение внутриглазного давления", self.text)
        self.assertNotIn("DTSTART;VALUE=DATE:2024", self.ics)
        self.assertIn("DTSTART;VALUE=DATE:20261031", self.ics)

    def test_nothing_overdue_no_group_event(self):
        plan = demo_plan("anna")
        plan["items"] = [i for i in plan["items"] if i["status"] not in ("overdue", "no_data")]
        plan["vaccines"] = []
        self.assertNotIn("Записаться в поликлинику", to_ics(plan, now=NOW))

    def test_format(self):
        self.assertTrue(self.ics.startswith("BEGIN:VCALENDAR\r\n"))
        for line in self.ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        self.assertIn("Бесплатно по полису ОМС", self.text)
        demo = json.loads((DEMO / "anna.json").read_text(encoding="utf-8"))
        plan = build_plan(demo["profile"], demo["history"] + [{"test": "mammography", "date": "2026-01-15"}], today=TODAY)
        self.assertIn("прил. 2\\, п. 1б приказа № 404н", to_ics(plan, now=NOW).replace("\r\n ", ""))


if __name__ == "__main__":
    unittest.main()
