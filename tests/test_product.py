"""Доработки после продуктового разбора: запись, поликлиника, второй этап, частые вопросы AIbert."""

import tempfile
import unittest

from app import App
from diary.assistant import FAQ, faq_answer
from diary.storage import Storage
from diary.wiki import load_wiki

TODAY = {"today": ["2026-10-24"]}
PROFILE = {"birth_date": "1985-03-12", "sex": "female"}


class ProductTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(storage=Storage(self.tmp.name))
        self.call("POST", "/api/profile", {"name": "Анна", "consent": True, "profile": PROFILE})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        return self.app.handle(method, path, TODAY, body or {})

    def test_my_clinic_is_saved_and_trimmed(self):
        code, plan = self.call("POST", "/api/profile", {"profile": dict(PROFILE, clinic_name=" Поликлиника № 5 ",
                                                                        clinic_phone="+7 (495) 123-45-67",
                                                                        clinic_address="ул. Ленина, 1" + "x" * 200)})
        self.assertEqual(code, 200)
        self.assertEqual(plan["profile"]["clinic_name"], "Поликлиника № 5")
        self.assertEqual(plan["profile"]["clinic_phone"], "+7 (495) 123-45-67")
        self.assertEqual(len(plan["profile"]["clinic_address"]), 100)

    def test_approximate_last_dispanserization_from_onboarding(self):
        code, plan = self.call("POST", "/api/history", {"test": "dispanserization", "date": "2025-06-01",
                                                        "result": "Примерная дата, со слов"})
        self.assertEqual(code, 200)
        disp = next(i for i in plan["items"] if i["test"] == "dispanserization")
        self.assertEqual(disp["last_date"], "2025-06-01")
        self.assertNotEqual(disp["status"], "no_data")

    def test_second_stage_becomes_main_step_until_done(self):
        code, plan = self.call("POST", "/api/history", {"test": "dispanserization", "date": "2026-10-01", "second_stage": "referred"})
        self.assertEqual(code, 200)
        self.assertEqual(plan["second_stage"]["date"], "2026-10-01")
        record_id = plan["second_stage"]["id"]
        code, plan = self.call("PUT", "/api/history/" + record_id, {"second_stage": "done"})
        self.assertEqual(code, 200)
        self.assertIsNone(plan["second_stage"])
        code, plan = self.call("PUT", "/api/history/" + record_id, {"second_stage": None})
        self.assertNotIn("second_stage", plan["history"][0])

    def test_newer_dispanserization_replaces_old_referral(self):
        self.call("POST", "/api/history", {"test": "dispanserization", "date": "2025-09-01", "second_stage": True})
        code, plan = self.call("POST", "/api/history", {"test": "dispanserization", "date": "2026-10-01"})
        self.assertIsNone(plan["second_stage"])

    def test_bad_second_stage_value(self):
        code, _ = self.call("POST", "/api/history", {"test": "dispanserization", "date": "2026-10-01", "second_stage": "maybe"})
        self.assertEqual(code, 400)

    def test_review_line_is_honest_by_default(self):
        _, plan = self.call("GET", "/api/plan")
        self.assertEqual(set(plan["review"]), {"reviewer", "role", "date"})


class FaqTest(unittest.TestCase):
    def setUp(self):
        self.wiki = load_wiki()
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(storage=Storage(self.tmp.name))
        self.app.storage.load_demo("anna")

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, question):
        return self.app.handle("POST", "/api/ask", TODAY, {"question": question})[1]

    def test_every_faq_question_gets_its_own_answer_with_sources(self):
        for entry in FAQ:
            with self.subTest(question=entry["question"]):
                answer = faq_answer(entry["question"], self.wiki)
                self.assertEqual(answer["answer"], entry["answer"])
                for source in answer["sources"]:
                    self.assertTrue(source.get("url") or source.get("wiki") in self.wiki)

    def test_rephrased_questions(self):
        cases = {
            "Сколько стоит пройти диспансеризацию?": "бесплатно",
            "можно отпроситься с работы на профосмотр?": "185.1",
            "мне отказали в поликлинике, что делать": "заведующему",
            "я не по прописке живу, где проходить": "прикрепиться",
            "это обязательно или можно отказаться": "добровольно",
            "забыл полис дома": "паспорт",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertIn(expected, self.ask(question)["answer"])

    def test_plan_questions_still_answered_from_plan(self):
        self.assertIn("2026", self.ask("Что мне нужно пройти в этом году?")["answer"])
        self.assertIn("Просрочен", self.ask("Какие обследования просрочены?")["answer"])

    def test_unknown_question_suggests_known_ones(self):
        answer = self.ask("Сколько у меня будет внуков?")
        self.assertEqual(len(answer["suggest"]), 3)
        for question in answer["suggest"]:
            self.assertIsNotNone(faq_answer(question, self.wiki))


if __name__ == "__main__":
    unittest.main()
