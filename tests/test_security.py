"""Проверки защиты: чужие сайты, подмена адреса, неверные данные, заголовки страниц.

Каждый тест — найденная при проверке дыра. Сервер запускается по-настоящему на свободном порту.
"""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from app import App, make_handler
from diary.storage import Storage

TODAY = {"today": ["2026-10-24"]}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(storage=Storage(self.tmp.name), today="2026-10-24")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.app.storage.load_demo("anna")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        sent = {"Content-Type": "application/json"}
        sent.update(headers or {})
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        conn.request(method, path, body=data, headers=sent)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, payload, resp

    def has_history(self):
        return bool(self.app.storage.load()["history"])

    def test_other_site_cannot_delete_data(self):
        # простая форма с чужого сайта: тип text/plain, без предварительного запроса браузера
        code, _, _ = self.request("POST", "/api/reset", raw="{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(code, 403)
        code, _, _ = self.request("POST", "/api/reset", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        code, _, _ = self.request("POST", "/api/reset", {}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(code, 403)
        self.assertTrue(self.has_history())

    def test_own_page_can_change_data(self):
        origin = f"http://127.0.0.1:{self.port}"
        code, _, _ = self.request("POST", "/api/reset", {}, headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(code, 200)
        self.assertFalse(self.has_history())

    def test_foreign_host_cannot_read_plan(self):
        code, _, _ = self.request("GET", "/api/plan", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(code, 403)
        code, _, _ = self.request("GET", "/api/plan", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(code, 200)

    def test_bad_requests_get_an_answer_not_a_dropped_connection(self):
        cases = [
            ("POST", "/api/profile", "[]"),
            ("POST", "/api/profile", json.dumps({"profile": "x"})),
            ("POST", "/api/history", json.dumps({"test": ["ecg"], "date": "2026-01-01"})),
            ("POST", "/api/history", json.dumps({"test": "ecg", "date": 5})),
            ("POST", "/api/history", json.dumps({"test": "ecg", "date": "2026-01-01", "file": "x"})),
            ("POST", "/api/ask", json.dumps({"question": 5})),
            ("POST", "/api/demo", "[]"),
            ("GET", "/report?today=garbage", None),
            ("GET", "/calendar.ics?today=2026-02-30", None),
        ]
        for method, path, raw in cases:
            with self.subTest(path=path, raw=raw):
                code, payload, _ = self.request(method, path, raw=raw)
                self.assertEqual(code, 400)
                self.assertIn("error", json.loads(payload))

    def test_broken_content_length(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/ask")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_pages_are_not_cached_and_limit_scripts(self):
        _, _, resp = self.request("GET", "/")
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        self.assertIn("connect-src 'self'", resp.getheader("Content-Security-Policy"))
        for path in ("/api/plan", "/report", "/calendar.ics"):
            _, _, resp = self.request("GET", path)
            self.assertEqual(resp.getheader("Cache-Control"), "no-store", path)


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(storage=Storage(self.tmp.name))
        self.app.handle("POST", "/api/profile", TODAY, {"name": "Анна", "consent": True,
                                                         "profile": {"birth_date": "1985-03-12", "sex": "female"}})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, body):
        return self.app.handle(method, path, TODAY, body)

    def test_future_or_ancient_date_is_not_counted(self):
        for when in ("2026-10-25", "2099-01-01", "0001-01-01"):
            with self.subTest(when=when):
                code, _ = self.call("POST", "/api/history", {"test": "ecg", "date": when})
                self.assertEqual(code, 400)
        code, plan = self.call("POST", "/api/history", {"test": "ecg", "date": "2026-10-24"})
        self.assertEqual(code, 200)
        record = plan["history"][0]
        code, _ = self.call("PUT", "/api/history/" + record["id"], {"date": "2027-01-01"})
        self.assertEqual(code, 400)

    def test_long_texts_are_rejected(self):
        code, _ = self.call("POST", "/api/profile", {"name": "А" * 101, "profile": {"birth_date": "1985-03-12", "sex": "female"}})
        self.assertEqual(code, 400)
        code, _ = self.call("POST", "/api/history", {"test": "ecg", "date": "2026-10-01", "result": "x" * 2001})
        self.assertEqual(code, 400)

    def test_conditions_must_be_a_list(self):
        code, plan = self.call("POST", "/api/profile", {"profile": {"birth_date": "1985-03-12", "sex": "female",
                                                                    "conditions": "mastectomy"}})
        self.assertEqual(code, 200)
        self.assertEqual(plan["profile"]["conditions"], [])

    def test_bad_today_has_russian_error(self):
        code, data = self.app.handle("GET", "/api/plan", {"today": ["garbage"]}, {})
        self.assertEqual(code, 400)
        self.assertIn("ГГГГ-ММ-ДД", data["error"])


if __name__ == "__main__":
    unittest.main()
