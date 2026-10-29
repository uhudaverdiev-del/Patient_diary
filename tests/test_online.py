"""Онлайн-версия для жюри: у каждого посетителя свой дневник, чужие данные не видны, старые удаляются."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import ONLINE_ASK_LIMIT, ONLINE_DAILY_LLM_LIMIT, SESSION_COOKIE, App, Sessions, make_handler
from diary.assistant import Assistant
from diary.storage import Storage
from diary.wiki import load_wiki


class OnlineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.app = App(storage=Storage(base / "base"), today="2026-10-24", online=True,
                       assistant=Assistant(load_wiki(), env={}, log_path=base / "log.jsonl"))
        self.now = [1000.0]
        self.sessions = Sessions(self.app, base / "sessions", ttl=60, limit=3, clock=lambda: self.now[0])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, self.sessions))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, host="checkup.example", origin="https://checkup.example"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json", "Host": host, "X-Forwarded-Proto": "https"}
        if method != "GET":
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = f"{SESSION_COOKIE}={cookie}"
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        set_cookie = resp.getheader("Set-Cookie") or ""
        new = set_cookie.split(";")[0].split("=", 1)[1] if set_cookie else None
        return resp.status, json.loads(payload) if payload.startswith(b"{") else payload, new, set_cookie

    def visitor(self):
        _, _, cookie, _ = self.request("GET", "/api/plan")
        return cookie

    def test_any_host_opens_and_mode_is_online(self):
        code, data, cookie, raw = self.request("GET", "/api/mode")
        self.assertEqual(code, 200)
        self.assertTrue(data["online"])
        self.assertIn("HttpOnly", raw)
        self.assertIn("Secure", raw)
        self.assertTrue(cookie)

    def test_visitors_do_not_see_each_other(self):
        a, b = self.visitor(), self.visitor()
        self.assertNotEqual(a, b)
        self.request("POST", "/api/demo", {"id": "anna"}, cookie=a)
        _, plan_a, _, _ = self.request("GET", "/api/plan", cookie=a)
        _, plan_b, _, _ = self.request("GET", "/api/plan", cookie=b)
        self.assertTrue(plan_a["history"])
        self.assertFalse(plan_b["history"])

    def test_other_site_cannot_change_data(self):
        a = self.visitor()
        code, _, _, _ = self.request("POST", "/api/demo", {"id": "anna"}, cookie=a, origin="https://evil.example")
        self.assertEqual(code, 403)

    def test_old_diaries_are_deleted(self):
        a = self.visitor()
        self.request("POST", "/api/demo", {"id": "anna"}, cookie=a)
        folder = Path(self.tmp.name) / "sessions" / a
        self.assertTrue(folder.exists())
        self.now[0] += 61
        _, plan, new, _ = self.request("GET", "/api/plan", cookie=a)
        self.assertNotEqual(new, a)
        self.assertFalse(plan["history"])
        self.assertFalse(folder.exists())

    def test_number_of_diaries_is_limited(self):
        cookies = [self.visitor() for _ in range(5)]
        self.assertEqual(len(self.sessions.items), 3)
        self.assertNotIn(cookies[0], self.sessions.items)

    def test_files_are_not_uploaded_online(self):
        a = self.visitor()
        self.request("POST", "/api/demo", {"id": "anna"}, cookie=a)
        upload = {"name": "a.pdf", "data": "JVBERi0xLjQK"}
        code, plan, _, _ = self.request("POST", "/api/history", {"test": "ecg", "date": "2026-10-20", "file": upload}, cookie=a)
        self.assertEqual(code, 200)
        self.assertFalse(any(r.get("file") for r in plan["history"]))

    def test_ai_questions_are_limited(self):
        a = self.visitor()
        self.request("POST", "/api/demo", {"id": "anna"}, cookie=a)
        for _ in range(ONLINE_ASK_LIMIT):
            code, _, _, _ = self.request("POST", "/api/ask", {"question": "Что просрочено?"}, cookie=a)
            self.assertEqual(code, 200)
        code, data, _, _ = self.request("POST", "/api/ask", {"question": "Что просрочено?"}, cookie=a)
        self.assertEqual(code, 400)
        self.assertIn("30 вопросов", data["error"])

    def test_daily_llm_budget_falls_back_to_local(self):
        a = self.visitor()
        self.request("POST", "/api/demo", {"id": "anna"}, cookie=a)
        self.app.llm_budget.update(day=__import__("datetime").date.today().isoformat(), used=ONLINE_DAILY_LLM_LIMIT)
        code, data, _, _ = self.request("POST", "/api/ask", {"question": "Что просрочено?"}, cookie=a)
        self.assertEqual(code, 200)
        self.assertEqual(data["mode"], "local")
        self.assertIn("Лимит", data["note"])


if __name__ == "__main__":
    unittest.main()
