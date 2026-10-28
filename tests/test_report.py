"""Памятка «Что сказать терапевту»."""

import json
import unittest
from pathlib import Path

from diary.engine import build_plan
from diary.report import doctor_questions, render_report

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo"
TODAY = "2026-10-24"


def demo_plan(name):
    demo = json.loads((DEMO / f"{name}.json").read_text(encoding="utf-8"))
    plan = build_plan(demo["profile"], demo["history"], today=TODAY)
    plan.update(name=demo["name"], profile=demo["profile"], history=demo["history"])
    return plan


class ReportTest(unittest.TestCase):
    def test_anna_overdue_and_dispanserization(self):
        html = render_report(demo_plan("anna"), {})
        self.assertIn("Просрочено (", html)
        self.assertIn("направить меня на диспансеризацию", html)
        self.assertIn("бесплатно\nпо полису ОМС", html)
        self.assertIn("ежегодно дать 1 оплачиваемый рабочий день", html)

    def test_smoker_and_history_titles(self):
        plan = demo_plan("ivan")
        html = render_report(plan, {"fluorography": "Флюорография"})
        self.assertIn("Я курю", html)
        self.assertIn("Флюорография", html)
        self.assertIn("01.02.2026", html)

    def test_condition_mentioned(self):
        self.assertTrue(any("мастэктомия" in q for q in doctor_questions(demo_plan("olga_39"))))

    def test_under_40_work_day(self):
        self.assertIn("раз в 3 года дать 1 оплачиваемый", render_report(demo_plan("olga_39"), {}))

    def test_incomplete_profile(self):
        html = render_report(demo_plan("no_birth_date"), {})
        self.assertIn("Пока не могу составить памятку", html)

    def test_html_is_escaped(self):
        plan = demo_plan("ivan")
        plan["name"] = "<script>x</script>"
        self.assertNotIn("<script>x", render_report(plan, {}))


if __name__ == "__main__":
    unittest.main()
