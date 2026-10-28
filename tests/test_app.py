"""Тесты основного сценария через API: профиль → план → отметка → пересчёт."""

import base64
import tempfile
import unittest

from app import App
from diary.storage import Storage

TODAY = {"today": ["2026-10-24"]}


class ApiScenarioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(storage=Storage(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        return self.app.handle(method, path, TODAY, body or {})

    def test_empty_profile_asks_for_data(self):
        code, plan = self.call("GET", "/api/plan")
        self.assertEqual(code, 200)
        self.assertFalse(plan["ready"])

    def test_full_scenario(self):
        code, plan = self.call("POST", "/api/profile", {"name": "Анна", "consent": True, "profile": {"birth_date": "1985-03-12", "sex": "female"}})
        self.assertTrue(plan["ready"])
        mammo = next(i for i in plan["items"] if i["test"] == "mammography")
        self.assertEqual(mammo["status"], "no_data")

        pdf = base64.b64encode(b"%PDF-1.4 test").decode()
        code, plan = self.call("POST", "/api/history", {
            "test": "mammography", "date": "2026-10-20", "result": "BI-RADS 1",
            "file": {"name": "маммография.pdf", "data": "data:application/pdf;base64," + pdf}})
        self.assertEqual(code, 200)
        mammo = next(i for i in plan["items"] if i["test"] == "mammography")
        self.assertEqual(mammo["status"], "actual")
        self.assertEqual(mammo["next_due"], "2028-10-20")
        record = plan["history"][0]
        self.assertIsNotNone(self.app.storage.file_path(record["file"]["stored"]))

        code, plan = self.call("DELETE", "/api/history/" + record["id"])
        mammo = next(i for i in plan["items"] if i["test"] == "mammography")
        self.assertEqual(mammo["status"], "no_data")

    def test_dispanserization_with_included_tests(self):
        self.call("POST", "/api/demo", {"id": "anna"})
        code, plan = self.call("POST", "/api/history", {
            "test": "dispanserization", "date": "2026-10-20",
            "also": ["fluorography", "gyn_exam", "mammography", "unknown_test"]})
        self.assertEqual(code, 200)
        items = {i["test"]: i for i in plan["items"]}
        for test in ("dispanserization", "fluorography", "gyn_exam", "mammography"):
            self.assertEqual(items[test]["last_date"], "2026-10-20", test)
        self.assertEqual(len(plan["history"]), 5 + 4)
        self.assertIn("в составе", next(r for r in plan["history"] if r["test"] == "gyn_exam" and r["date"] == "2026-10-20")["result"])

    def test_edit_record(self):
        self.call("POST", "/api/demo", {"id": "anna"})
        code, plan = self.call("GET", "/api/plan")
        record = next(r for r in plan["history"] if r["test"] == "fluorography")
        code, plan = self.call("PUT", "/api/history/" + record["id"], {"date": "2026-01-15", "result": "норма"})
        self.assertEqual(code, 200)
        fluoro = next(i for i in plan["items"] if i["test"] == "fluorography")
        self.assertEqual(fluoro["next_due"], "2028-01-15")
        edited = next(r for r in plan["history"] if r["id"] == record["id"])
        self.assertEqual(edited["result"], "норма")
        self.assertEqual(self.call("PUT", "/api/history/nope", {"date": "2026-01-15"})[0], 404)
        self.assertEqual(self.call("PUT", "/api/history/" + record["id"], {"date": "вчера"})[0], 400)
        self.assertEqual(self.call("PUT", "/api/history/" + record["id"], {"test": "x-ray-of-cat"})[0], 400)

    def test_catalog_has_ct(self):
        tests = {t["test"] for t in self.call("GET", "/api/tests")[1]}
        self.assertTrue({"ct_chest", "ct_breast", "profosmotr"} <= tests)

    def test_data_survives_restart(self):
        self.call("POST", "/api/demo", {"id": "anna"})
        restarted = App(storage=Storage(self.tmp.name))
        code, plan = restarted.handle("GET", "/api/plan", TODAY, {})
        self.assertEqual(plan["name"], "Анна, 41 год")
        self.assertEqual(len(plan["history"]), 5)

    def test_bad_record_rejected(self):
        code, data = self.call("POST", "/api/history", {"test": "ecg"})
        self.assertEqual(code, 400)
        code, data = self.call("POST", "/api/history", {"test": "ecg", "date": "вчера"})
        self.assertEqual(code, 400)

    def test_unknown_demo_and_path_traversal(self):
        self.assertEqual(self.call("POST", "/api/demo", {"id": "../secret"})[0], 404)
        self.assertIsNone(self.app.storage.file_path("../state.json"))

    def test_reset_clears_everything(self):
        self.call("POST", "/api/demo", {"id": "anna"})
        code, plan = self.call("POST", "/api/reset")
        self.assertEqual(code, 200)
        self.assertEqual(plan["profile"], {})
        self.assertEqual(plan["history"], [])
        self.assertFalse(plan["ready"])

    def test_report_and_calendar(self):
        self.call("POST", "/api/demo", {"id": "anna"})
        self.assertIn("Памятка для терапевта", self.app.report_page("2026-10-24").decode("utf-8"))
        self.assertIn("BEGIN:VCALENDAR", self.app.calendar_file("2026-10-24").decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
