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
