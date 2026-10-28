"""Шаблоны обращений: без личных данных, только по пунктам плана."""

import tempfile
import unittest

from app import App
from diary.storage import Storage

TODAY = {"today": ["2026-10-24"]}


class RefusalLetterTest(unittest.TestCase):
    def setUp(self):
        self.app = App(storage=Storage(tempfile.mkdtemp()))
        self.app.handle("POST", "/api/demo", TODAY, {"id": "anna"})

    def letter(self, **body):
        base = {"test": "mammography", "reason": "no_ticket", "date": "2026-10-20", "addressee": "chief"}
        return self.app.handle("POST", "/api/letter", TODAY, dict(base, **body))

    def test_letter_has_placeholders_and_basis(self):
        code, data = self.letter()
        self.assertEqual(code, 200)
        text = data["text"]
        self.assertIn("[ФИО]", text)
        self.assertIn("[номер полиса]", text)
        self.assertNotIn("Анна", text)  # имя из профиля в письмо не попадает
        self.assertIn("Главному врачу", text)
        self.assertIn("20.10.2026", text)
        self.assertIn("приказа № 404н", text)
        self.assertIn("326-ФЗ", text)
        self.assertNotIn("323-ФЗ", text)

    def test_paid_reason_cites_323(self):
        text = self.letter(reason="paid", addressee="smo")[1]["text"]
        self.assertIn("323-ФЗ", text)
        self.assertIn("страховую медицинскую организацию", text)

    def test_vaccine_letter(self):
        text = self.letter(test="vac_diphtheria")[1]["text"]
        self.assertIn("национальный календарь", text)
        self.assertIn("приказа № 1122н", text)

    def test_rejects_bad_input(self):
        self.assertEqual(self.letter(test="psa")[0], 400)  # ПСА Анне не положен
        self.assertEqual(self.letter(reason="<script>")[0], 400)
        self.assertEqual(self.letter(addressee="prosecutor")[0], 400)
        self.assertEqual(self.letter(date="2026-12-01")[0], 400)
        self.assertEqual(self.letter(date="вчера")[0], 400)

    def test_letter_is_not_stored(self):
        before = self.app.storage.state_path.read_text(encoding="utf-8")
        self.letter()
        self.assertEqual(self.app.storage.state_path.read_text(encoding="utf-8"), before)


class DisputeTest(unittest.TestCase):
    """Приписки: услуга в выписке ОМС, которую пациент не получал."""

    def setUp(self):
        self.app = App(storage=Storage(tempfile.mkdtemp()))
        self.app.handle("POST", "/api/demo", TODAY, {"id": "anna"})

    def add(self, **body):
        base = {"test": "fluorography", "date": "2025-04-10", "comment": "в этот день была на работе"}
        return self.app.handle("POST", "/api/disputes", TODAY, dict(base, **body))

    def test_disputed_record_not_counted(self):
        plan = self.app.handle("GET", "/api/plan", TODAY, {})[1]
        record = next(r for r in plan["history"] if r["test"] == "fluorography")
        code, plan = self.add(date=record["date"])
        self.assertEqual(code, 200)
        fluoro = next(i for i in plan["items"] if i["test"] == "fluorography")
        self.assertNotEqual(fluoro["last_date"], record["date"])
        self.assertTrue(any("ошибочную запись" in n for n in fluoro["notes"]))
        self.assertEqual(len(plan["disputes"]), 1)

    def test_letter_to_smo(self):
        plan = self.add()[1]
        code, data = self.app.handle("POST", "/api/letter", TODAY, {"kind": "dispute", "id": plan["disputes"][0]["id"]})
        self.assertEqual(code, 200)
        self.assertIn("10.04.2025", data["text"])
        self.assertIn("Флюорография", data["text"])
        self.assertIn("[ФИО]", data["text"])
        self.assertNotIn("на работе", data["text"])  # комментарий остаётся у пациента
        self.assertEqual(self.app.handle("POST", "/api/letter", TODAY, {"kind": "dispute", "id": "nope"})[0], 404)

    def test_validation(self):
        self.assertEqual(self.add(test="<img>")[0], 400)
        self.assertEqual(self.add(date="2027-01-01")[0], 400)
        self.assertEqual(self.add(date="вчера")[0], 400)
        self.assertEqual(self.add(comment="x" * 501)[0], 400)
        self.assertEqual(self.add(comment="x" * 500)[0], 200)

    def test_needs_consent_and_reset_and_export(self):
        from diary.privacy import export_archive
        import io, json, zipfile
        plan = self.add()[1]
        archive = zipfile.ZipFile(io.BytesIO(export_archive(self.app.storage)))
        self.assertEqual(len(json.loads(archive.read("state.json"))["disputes"]), 1)
        code, plan = self.app.handle("DELETE", "/api/disputes/" + plan["disputes"][0]["id"], TODAY, {})
        self.assertEqual(plan["disputes"], [])
        self.add()
        plan = self.app.handle("POST", "/api/reset", TODAY, {})[1]
        self.assertEqual(plan["disputes"], [])
        self.assertEqual(self.add()[0], 400)  # после отзыва согласия сохранять нельзя
