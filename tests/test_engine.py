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

    def test_profosmotr_merged_into_dispanserization(self):
        # После 40 лет диспансеризация ежегодная и включает профосмотр: одна задача, а не две.
        self.assertNotIn("profosmotr", self.items)
        self.assertIn("Включает профилактический осмотр", self.items["dispanserization_40"]["notes"][0])

    def test_never_done_is_no_data_not_overdue(self):
        for test_id in ("mammography", "fobt_40_64", "eye_pressure", "hepatitis_c"):
            self.assertEqual(self.items[test_id]["status"], "no_data", test_id)
        self.assertEqual(self.items["ecg"]["status"], "overdue")

    def test_female_screenings_present_male_absent(self):
        self.assertIn("mammography", self.items)
        self.assertIn("cervical_cytology", self.items)
        self.assertNotIn("psa", self.items)

    def test_mammography_never_done_asks_for_date(self):
        item = self.items["mammography"]
        self.assertEqual(item["status_label"], "нет данных")
        self.assertTrue(item["needs_date"])

    def test_every_item_has_source(self):
        for item in self.items.values():
            self.assertIn("404н", item["source"]["title"])
            self.assertTrue(item["source"]["clause"])

    def test_marking_done_recalculates_status(self):
        items = by_id(plan_for("anna", [{"test": "fluorography", "date": TODAY}]))
        self.assertEqual(items["fluorography"]["status"], "actual")
        self.assertEqual(items["fluorography"]["next_due"], "2028-10-24")

    def test_dispanserization_recalculates(self):
        items = by_id(plan_for("anna", [{"test": "dispanserization", "date": "2026-10-20"}]))
        self.assertNotIn("profosmotr", items)
        self.assertEqual(items["dispanserization_40"]["status"], "actual")
        self.assertEqual(items["dispanserization_40"]["next_due"], "2027-10-20")

    def test_ct_counts_instead_of_fluorography_and_mammography(self):
        items = by_id(plan_for("anna", [{"test": "ct_chest", "date": "2026-03-01"},
                                        {"test": "ct_breast", "date": "2026-03-01"}]))
        self.assertEqual(items["fluorography"]["next_due"], "2028-03-01")
        # маммографию не делают, если КТ молочных желёз была в последние 12 месяцев
        self.assertEqual(items["mammography"]["next_due"], "2027-03-01")
        self.assertIn("КТ молочных желёз", items["mammography"]["notes"][-1])

    def test_mammography_after_ct_keeps_own_interval(self):
        items = by_id(plan_for("anna", [{"test": "ct_breast", "date": "2025-01-01"},
                                        {"test": "mammography", "date": "2025-06-01"}]))
        self.assertEqual(items["mammography"]["next_due"], "2027-06-01")


class HepatitisCTest(unittest.TestCase):
    """Анализ на гепатит C добавлен приказом № 378н с 30.08.2024: раньше его не могли сделать."""

    def test_not_overdue_for_people_over_25_before_2024(self):
        item = by_id(plan_for("anna"))["hepatitis_c"]
        self.assertEqual(item["status"], "no_data")
        self.assertIn("378н", item["notes"][0])

    def test_done_resets_interval(self):
        item = by_id(plan_for("anna", [{"test": "hepatitis_c", "date": "2025-02-01"}]))["hepatitis_c"]
        self.assertEqual(item["status"], "actual")
        self.assertEqual(item["next_due"], "2035-02-01")


class AgeRulesTest(unittest.TestCase):
    def test_psa_and_egd_any_day_of_the_year_turning_45(self):
        # Иван родился 20.11.1981: 45 исполняется в 2026, сдать можно в любой день года.
        items = by_id(plan_for("ivan"))
        for test_id in ("psa", "egd"):
            self.assertEqual(items[test_id]["next_due"], "2026-12-31")
            self.assertEqual(items[test_id]["status"], "actual")  # до конца года больше 60 дней
            self.assertTrue(items[test_id]["todo_this_year"])

    def test_psa_becomes_soon_60_days_before_year_end(self):
        demo = json.loads((DEMO / "ivan.json").read_text(encoding="utf-8"))
        items = by_id(build_plan(demo["profile"], demo["history"], today="2026-11-15"))
        self.assertEqual(items["psa"]["status"], "soon")

    def test_psa_done_this_year(self):
        items = by_id(plan_for("ivan", [{"test": "psa", "date": "2026-03-01"}]))
        self.assertEqual(items["psa"]["next_due"], "2031-01-01")
        self.assertFalse(items["psa"]["todo_this_year"])

    def test_progress_counts_psa_only_when_done(self):
        self.assertEqual(plan_for("ivan")["summary"]["progress_percent"], 75)
        done = plan_for("ivan", [{"test": "psa", "date": "2026-03-01"}, {"test": "egd", "date": "2026-03-01"}])
        self.assertEqual(done["summary"]["progress_percent"], 100)

    def test_calendar_year_age_band(self):
        # 40 исполнится в декабре 2026: ежегодная диспансеризация и маммография уже с 1 января 2026.
        plan = build_plan({"birth_date": "1986-12-20", "sex": "female"}, today=TODAY)
        items = by_id(plan)
        self.assertEqual(plan["age"], 39)
        self.assertIn("dispanserization_40", items)
        self.assertIn("mammography", items)

    def test_smoking_risk_factor(self):
        self.assertEqual([r["id"] for r in plan_for("ivan")["risk_factors"]], ["smoking"])
        self.assertEqual(plan_for("anna")["risk_factors"], [])

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
