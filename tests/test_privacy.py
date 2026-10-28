"""Защита персональных данных: согласие, выгрузка, отзыв, обезличивание, проверка файлов."""

import base64
import json
import tempfile
import unittest
import zipfile
from io import BytesIO

from app import App
from diary.assistant import Assistant
from diary.privacy import detect_file_type, export_archive, policy_html, redact
from diary.storage import Storage
from diary.wiki import load_wiki

TODAY = {"today": ["2026-10-24"]}
PROFILE = {"birth_date": "1985-03-12", "sex": "female"}


def upload(raw, name):
    return {"name": name, "data": "data:;base64," + base64.b64encode(raw).decode()}


class ConsentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(storage=Storage(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        return self.app.handle(method, path, TODAY, body or {})

    def test_profile_needs_consent(self):
        code, data = self.call("POST", "/api/profile", {"profile": PROFILE})
        self.assertEqual(code, 400)
        self.assertIn("согласие", data["error"])
        self.assertFalse(self.app.storage.state_path.exists())

    def test_consent_recorded_with_version_and_date(self):
        code, plan = self.call("POST", "/api/profile", {"profile": PROFILE, "consent": True})
        self.assertEqual(code, 200)
        self.assertEqual(plan["consent"]["version"], "1.0")
        self.assertIn("date", plan["consent"])
        # повторное сохранение профиля согласие не сбрасывает
        code, plan = self.call("POST", "/api/profile", {"profile": dict(PROFILE, smoking="Курит")})
        self.assertEqual(code, 200)

    def test_record_needs_consent(self):
        code, _ = self.call("POST", "/api/history", {"test": "ecg", "date": "2026-01-10"})
        self.assertEqual(code, 400)

    def test_demo_is_marked(self):
        code, plan = self.call("POST", "/api/demo", {"id": "anna"})
        self.assertEqual(plan["consent"], {"demo": True})

    def test_withdraw_deletes_everything(self):
        self.call("POST", "/api/profile", {"profile": PROFILE, "consent": True})
        self.call("POST", "/api/history", {"test": "ecg", "date": "2026-01-10",
                                           "file": upload(b"%PDF-1.4 x", "экг.pdf")})
        (self.app.storage.user_dir / "llm_log.jsonl").write_text("{}\n", encoding="utf-8")
        code, plan = self.call("POST", "/api/reset")
        self.assertIsNone(plan["consent"])
        self.assertEqual(plan["history"], [])
        self.assertFalse(self.app.storage.files_dir.exists())
        self.assertFalse((self.app.storage.user_dir / "llm_log.jsonl").exists())

    def test_export_contains_all_data(self):
        self.call("POST", "/api/profile", {"profile": PROFILE, "consent": True, "name": "Анна"})
        self.call("POST", "/api/history", {"test": "ecg", "date": "2026-01-10",
                                           "file": upload(b"\x89PNG\r\n\x1a\nxx", "снимок.png")})
        zf = zipfile.ZipFile(BytesIO(export_archive(self.app.storage)))
        names = zf.namelist()
        self.assertIn("state.json", names)
        self.assertTrue(any(n.startswith("files/") and n.endswith(".png") for n in names))
        state = json.loads(zf.read("state.json"))
        self.assertEqual(state["name"], "Анна")
        self.assertEqual(state["consent"]["version"], "1.0")


class FileCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.tmp.name)
        self.storage.save_profile(PROFILE, consent=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_types_by_content(self):
        self.assertEqual(detect_file_type(b"%PDF-1.7"), "pdf")
        self.assertEqual(detect_file_type(b"\xff\xd8\xff\xe0"), "jpg")
        self.assertEqual(detect_file_type(b"\x89PNG\r\n\x1a\n"), "png")
        self.assertIsNone(detect_file_type(b"<html><script>"))
        self.assertIsNone(detect_file_type(b"MZ\x90\x00"))

    def test_html_rejected_even_if_named_pdf(self):
        with self.assertRaises(ValueError):
            self.storage.add_record({"test": "ecg", "date": "2026-01-10",
                                     "file": upload(b"<html><script>alert(1)</script>", "анализ.pdf")})

    def test_extension_follows_content(self):
        entry = self.storage.add_record({"test": "ecg", "date": "2026-01-10",
                                         "file": upload(b"%PDF-1.4 x", "отчёт.html")})
        self.assertTrue(entry["file"]["stored"].endswith(".pdf"))
        self.assertEqual(entry["file"]["name"], "отчёт.html")


class RedactTest(unittest.TestCase):
    def test_contacts_and_documents_removed(self):
        text = ("Мой телефон +7 (916) 123-45-67 или 89161234567, почта anna.ivanova@mail.ru, "
                "СНИЛС 112-233-445 95, полис 1234567890123456, паспорт 4510 123456. Когда ЭКГ в 2026 году?")
        out = redact(text)
        for secret in ("916", "anna.ivanova", "112-233", "1234567890123456", "4510 123456"):
            self.assertNotIn(secret, out)
        self.assertIn("Когда ЭКГ в 2026 году?", out)

    def test_ordinary_question_untouched(self):
        q = "Что мне пройти в 2026 году после 40 лет?"
        self.assertEqual(redact(q), q)

    def test_llm_gets_redacted_question(self):
        sent = {}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(storage=Storage(tmp.name))
        app.storage.load_demo("anna")
        plan = app.plan("2026-10-24")

        def llm(system, user):
            sent["user"] = user
            return "Ответ [1]"
        Assistant(load_wiki(), env={"YANDEX_API_KEY": "k", "YANDEX_FOLDER_ID": "f"}, llm=llm,
                  log_path=f"{tmp.name}/log.jsonl").ask("Звоните на 89161234567, что с ЭКГ?", plan)
        self.assertNotIn("89161234567", sent["user"])
        self.assertIn("[телефон]", sent["user"])


class PolicyTest(unittest.TestCase):
    def test_policy_page(self):
        html = policy_html()
        for part in ("Политика обработки персональных данных", "ст. 10 152-ФЗ", "Отозвать согласие", "18 лет"):
            self.assertIn(part, html)


if __name__ == "__main__":
    unittest.main()
