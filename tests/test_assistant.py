import json
import tempfile
import unittest
from pathlib import Path

from diary.assistant import Assistant, build_context, citations_ok, env_report, find_env_file, load_env, local_answer
from diary.engine import build_plan
from diary.wiki import load_wiki

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo"
TODAY = "2026-10-24"
KEYS = {"YANDEX_API_KEY": "test", "YANDEX_FOLDER_ID": "folder"}


def demo_plan(name):
    demo = json.loads((DEMO / f"{name}.json").read_text(encoding="utf-8"))
    plan = build_plan(demo["profile"], demo["history"], today=TODAY)
    plan["profile"] = demo["profile"]
    plan["name"] = demo["name"]
    return plan


class LocalAnswerTest(unittest.TestCase):
    def setUp(self):
        self.wiki = load_wiki()
        self.plan = demo_plan("anna")

    def test_what_this_year(self):
        r = local_answer("Что мне нужно пройти в этом году?", self.plan, self.wiki)
        self.assertIn("2026", r["answer"])
        self.assertIn("Маммография", r["answer"])
        self.assertTrue(r["sources"])

    def test_overdue(self):
        r = local_answer("Какие обследования просрочены?", self.plan, self.wiki)
        self.assertIn("Просрочены", r["answer"])
        self.assertNotIn("Маммография", r["answer"])

    def test_why(self):
        r = local_answer("Зачем нужна маммография?", self.plan, self.wiki)
        self.assertIn("молочн", r["answer"])
        self.assertIn("mammography", [s.get("wiki") for s in r["sources"]])

    def test_unknown_question_is_honest(self):
        r = local_answer("Какая погода завтра в Казани?", self.plan, self.wiki)
        self.assertIn("терапевт", r["answer"])

    def test_no_plan(self):
        r = local_answer("Что пройти?", demo_plan("no_birth_date"), self.wiki)
        self.assertIn("дату рождения", r["answer"])


class LlmModeTest(unittest.TestCase):
    def setUp(self):
        self.wiki = load_wiki()
        self.plan = demo_plan("anna")
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "log.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, reply):
        def llm(system, user):
            self.sent = user
            if isinstance(reply, Exception):
                raise reply
            return reply
        return Assistant(self.wiki, env=KEYS, llm=llm, log_path=self.log)

    def test_accepted_with_citations(self):
        r = self.make("Маммография просрочена не была, её нужно пройти сейчас [1][2].").ask("Зачем маммография?", self.plan)
        self.assertEqual(r["mode"], "yandexgpt")

    def test_rejected_without_citations(self):
        r = self.make("Пройдите всё срочно.").ask("Что пройти в этом году?", self.plan)
        self.assertEqual(r["mode"], "local")
        self.assertIn("отклонён", r["note"])

    def test_rejected_with_fake_citation(self):
        r = self.make("Ответ [9].").ask("Что пройти в этом году?", self.plan)
        self.assertEqual(r["mode"], "local")

    def test_network_error_falls_back(self):
        r = self.make(OSError("нет сети")).ask("Что пройти в этом году?", self.plan)
        self.assertEqual(r["mode"], "local")
        self.assertTrue(self.log.exists())

    def test_no_personal_data_sent(self):
        self.make("Ок [1].").ask("Что пройти в этом году?", self.plan)
        self.assertNotIn("Анна", self.sent)
        self.assertNotIn("1985", self.sent)
        self.assertIn("Возраст 41", self.sent)

    def test_without_keys_local(self):
        r = Assistant(self.wiki, env={}, log_path=self.log).ask("Что пройти?", self.plan)
        self.assertEqual(r["mode"], "local")

    def test_citations_check(self):
        self.assertTrue(citations_ok("а [1] б [2]", 2))
        self.assertFalse(citations_ok("а [3]", 2))
        self.assertFalse(citations_ok("без ссылок", 2))


class EnvFileTest(unittest.TestCase):
    """Типичные ошибки при создании .env в Блокноте не должны ломать ключ."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name, data):
        (self.dir / name).write_bytes(data)
        return find_env_file(self.dir)

    def assert_keys(self, path):
        env = load_env(path)
        self.assertEqual(env.get("YANDEX_API_KEY"), "AQV-1")
        self.assertEqual(env.get("YANDEX_FOLDER_ID"), "b1g2")

    def test_plain(self):
        self.assert_keys(self.write(".env", b"YANDEX_API_KEY=AQV-1\nYANDEX_FOLDER_ID=b1g2\n"))

    def test_txt_extension_bom_and_spaces(self):
        text = "\ufeffYANDEX_API_KEY = AQV-1 \r\nyandex_folder_id=\"b1g2\"\r\n"
        self.assert_keys(self.write(".env.txt", text.encode("utf-8")))

    def test_utf16(self):
        self.assert_keys(self.write(".env", "YANDEX_API_KEY=AQV-1\nYANDEX_FOLDER_ID=b1g2".encode("utf-16")))

    def test_report_missing_file(self):
        lines = env_report({}, None, self.dir)
        self.assertIn("Не найден файл .env", lines[1])

    def test_report_missing_line(self):
        path = self.write(".env", b"YANDEX_API_KEY=AQV-1\n")
        lines = env_report(load_env(path), path, self.dir)
        self.assertIn("YANDEX_FOLDER_ID", lines[1])


if __name__ == "__main__":
    unittest.main()
