"""Тесты движка плана на демо-пациентах. Дата «сегодня» зафиксирована: 24.10.2026."""

import json
import unittest
from datetime import date
from pathlib import Path

from diary.engine import add_months, age_on, birthday_at, build_plan

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo"
TODAY = "2026-10-24"


def plan_for(name, extra_history=None):
    demo = json.loads((DEMO / f"{name}.json").read_text(encoding="utf-8"))
    history = demo["history"] + (extra_history or [])
    return build_plan(demo["profile"], history, today=TODAY)


def by_id(plan):
    return {item["id"]: item for item in plan["items"]}


class DateHelpersTest(unittest.TestCase):
    def test_add_months_end_of_month(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_birthday_on_feb_29(self):
        self.assertEqual(birthday_at(date(2000, 2, 29), 25), date(2025, 2, 28))

    def test_age(self):
        self.assertEqual(age_on(date(1985, 3, 12), date(2026, 3, 11)), 40)
        self.assertEqual(age_on(date(1985, 3, 12), date(2026, 3, 12)), 41)


class AnnaTest(unittest.TestCase):
    """Главный демо-сценарий: 41 год, последняя диспансеризация 10.05.2023."""

    def setUp(self):
        self.items = by_id(plan_for("anna"))

    def test_yearly_dispanserization_after_40_is_overdue(self):
        item = self.items["dispanserization_40"]
        self.assertEqual(item["status"], "overdue")
        self.assertEqual(item["next_due"], "2024-05-10")
        self.assertNotIn("dispanserization_18_39", self.items)

    def test_profosmotr_counts_dispanserization(self):
        self.assertEqual(self.items["profosmotr"]["last_date"], "2023-05-10")

    def test_female_screenings_present_male_absent(self):
        self.assertIn("mammography", self.items)
        self.assertIn("cervical_cytology", self.items)
        self.assertNotIn("psa", self.items)

    def test_mammography_never_done_asks_for_date(self):
        item = self.items["mammography"]
        self.assertEqual(item["status"], "soon")
        self.assertTrue(item["needs_date"])

    def test_every_item_has_source(self):
        for item in self.items.values():
            self.assertIn("404н", item["source"]["title"])
            self.assertTrue(item["source"]["clause"])

    def test_marking_done_recalculates_status(self):
        items = by_id(plan_for("anna", [{"test": "fluorography", "date": TODAY}]))
        self.assertEqual(items["fluorography"]["status"], "actual")
        self.assertEqual(items["fluorography"]["next_due"], "2028-10-24")

    def test_dispanserization_marks_profosmotr_done(self):
        items = by_id(plan_for("anna", [{"test": "dispanserization", "date": "2026-10-20"}]))
        self.assertEqual(items["profosmotr"]["status"], "actual")
        self.assertEqual(items["dispanserization_40"]["next_due"], "2027-10-20")


class AgeRulesTest(unittest.TestCase):
    def test_psa_and_egd_soon_before_45(self):
        items = by_id(plan_for("ivan"))
        self.assertEqual(items["psa"]["status"], "soon")
        self.assertEqual(items["psa"]["next_due"], "2026-11-20")
        self.assertEqual(items["egd"]["next_due"], "2026-11-20")

    def test_no_psa_after_64(self):
        items = by_id(plan_for("petr"))
        self.assertNotIn("psa", items)
        self.assertIn("fobt_65_75", items)
        self.assertNotIn("fobt_40_64", items)
        self.assertEqual(items["fobt_65_75"]["next_due"], "2027-01-20")

    def test_young_patient_all_actual(self):
        plan = plan_for("mariya")
        self.assertEqual(plan["summary"]["counts"]["overdue"], 0)
        self.assertEqual(plan["summary"]["progress_percent"], 100)
        self.assertNotIn("mammography", by_id(plan))

    def test_turning_40_moves_dispanserization_to_birthday(self):
        items = by_id(plan_for("olga_39"))
        self.assertEqual(items["dispanserization_18_39"]["next_due"], "2027-01-01")
        self.assertNotIn("dispanserization_40", items)
        self.assertEqual(items["fobt_40_64"]["next_due"], "2027-01-01")

    def test_mastectomy_excludes_mammography(self):
        self.assertNotIn("mammography", by_id(plan_for("olga_39")))


class IncompleteDataTest(unittest.TestCase):
    def test_no_birth_date_no_plan(self):
        plan = plan_for("no_birth_date")
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["problems"][0]["field"], "birth_date")

    def test_no_sex_only_common_items_with_warning(self):
        plan = plan_for("no_sex")
        self.assertTrue(plan["ready"])
        ids = by_id(plan)
        for sex_specific in ("mammography", "cervical_cytology", "gyn_exam", "psa"):
            self.assertNotIn(sex_specific, ids)
        self.assertTrue(any(p["field"] == "sex" for p in plan["problems"]))

    def test_minor_gets_no_plan(self):
        plan = build_plan({"birth_date": "2010-01-01", "sex": "male"}, today=TODAY)
        self.assertFalse(plan["ready"])

    def test_future_dates_ignored(self):
        plan = build_plan({"birth_date": "1985-03-12", "sex": "female"},
                          [{"test": "fluorography", "date": "2027-01-01"}], today=TODAY)
        self.assertIsNone(by_id(plan)["fluorography"]["last_date"])
        self.assertTrue(any(p["field"] == "history" for p in plan["problems"]))

    def test_bad_date_format(self):
        plan = build_plan({"birth_date": "12.03.1985", "sex": "female"}, today=TODAY)
        self.assertFalse(plan["ready"])


if __name__ == "__main__":
    unittest.main()
