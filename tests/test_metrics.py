"""Показатели здоровья: проверка ввода, оценка по критериям факторов риска, API."""

import tempfile
import unittest
from datetime import date

from app import App
from diary.metrics import clean_metric, summarize
from diary.storage import Storage

TODAY = date(2026, 10, 24)


class CleanTest(unittest.TestCase):
    def test_comma_decimal(self):
        self.assertEqual(clean_metric({"kind": "glucose", "date": "2026-10-01", "value": "5,4"}, TODAY)["value"], 5.4)

    def test_rejects_bad_input(self):
        bad = [
            {"kind": "glucose", "date": "2026-10-01", "value": "много"},
            {"kind": "glucose", "date": "2027-01-01", "value": "5"},
            {"kind": "glucose", "date": "2026-10-01", "value": "55"},
            {"kind": "pressure", "date": "2026-10-01", "value": "80", "value2": "120"},
            {"kind": "pulse", "date": "2026-10-01", "value": "70"},
        ]
        for record in bad:
            with self.assertRaises(ValueError, msg=record):
                clean_metric(record, TODAY)


class SummaryTest(unittest.TestCase):
    def by_kind(self, metrics):
        return {m["kind"]: m for m in summarize(metrics)["items"]}

    def test_thresholds(self):
        items = self.by_kind([
            {"kind": "pressure", "date": "2026-01-01", "value": 139, "value2": 90},
            {"kind": "cholesterol", "date": "2026-01-01", "value": 4.9},
            {"kind": "glucose", "date": "2026-01-01", "value": 6.1},
            {"kind": "weight", "date": "2026-01-01", "value": 70},
        ])
        self.assertTrue(items["pressure"]["last"]["high"])  # нижнее 90 — уже повышено
        self.assertFalse(items["cholesterol"]["last"]["high"])
        self.assertTrue(items["glucose"]["last"]["high"])
        self.assertIsNone(items["weight"]["last"]["high"])

    def test_last_and_change_by_date(self):
        items = self.by_kind([
            {"kind": "weight", "date": "2026-05-01", "value": 73.5},
            {"kind": "weight", "date": "2026-01-01", "value": 75},
        ])
        self.assertEqual(items["weight"]["last"]["text"], "73,5")
        self.assertEqual(items["weight"]["change"], "-1,5 к прошлому измерению")
        self.assertIsNone(items["glucose"]["last"])


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(storage=Storage(self.tmp.name), today="2026-10-24")

    def tearDown(self):
        self.tmp.cleanup()

    def test_needs_consent(self):
        code, data = self.app.handle("POST", "/api/metrics", {}, {"kind": "weight", "date": "2026-10-01", "value": 70})
        self.assertEqual(code, 400)

    def test_add_delete_and_reset(self):
        self.app.handle("POST", "/api/demo", {}, {"id": "anna"})
        code, plan = self.app.handle("POST", "/api/metrics", {}, {"kind": "glucose", "date": "2026-10-20", "value": "6,4"})
        self.assertEqual(code, 200)
        glucose = next(m for m in plan["metrics"]["items"] if m["kind"] == "glucose")
        self.assertTrue(glucose["last"]["high"])
        code, plan = self.app.handle("DELETE", "/api/metrics/" + glucose["points"][-1]["id"], {}, {})
        glucose = next(m for m in plan["metrics"]["items"] if m["kind"] == "glucose")
        self.assertEqual(glucose["last"]["text"], "5,2")
        self.assertEqual(self.app.handle("DELETE", "/api/metrics/nope", {}, {})[0], 404)
        code, plan = self.app.handle("POST", "/api/reset", {}, {})
        self.assertTrue(all(m["last"] is None for m in plan["metrics"]["items"]))

    def test_memo_mentions_high_values(self):
        self.app.handle("POST", "/api/demo", {}, {"id": "anna"})
        page = self.app.report_page().decode("utf-8")
        self.assertIn("Мои показатели", page)
        self.assertIn("Артериальное давление у меня 146/92", page)


if __name__ == "__main__":
    unittest.main()
