"""Веб-интерфейс дневника здоровья.

Запуск:  python app.py        затем открыть http://127.0.0.1:8000
         python app.py --port 8080 --today 2026-10-24   (фиксированная дата для демо)
"""

import argparse
import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from diary.assistant import Assistant
from diary.privacy import CONSENT_TEXT, CONSENT_VERSION, CONTENT_TYPES, export_archive, policy_html
from diary.engine import build_plan, describe_rule, load_rules
from diary.reminders import reminders, to_ics
from diary.report import render_report
from diary.storage import Storage, list_demos
from diary.wiki import load_wiki, search

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 15 * 1024 * 1024


def test_catalog(rules_data):
    """Список обследований для формы «Отметить прохождение»."""
    seen = {}
    for rule in rules_data["rules"]:
        seen.setdefault(rule["test"], {"test": rule["test"], "title": rule["title"], "who": []})
        seen[rule["test"]]["who"].append(describe_rule(rule))
    for extra in rules_data.get("extra_tests", []):
        seen[extra["test"]] = {"test": extra["test"], "title": extra["title"], "who": [extra["who"]]}
    return list(seen.values())


class App:
    def __init__(self, storage=None, today=None, assistant=None):
        self.storage = storage or Storage()
        self.today = today
        self.rules = load_rules()
        self.wiki = load_wiki()
        self.assistant = assistant or Assistant(self.wiki, log_path=self.storage.user_dir / "llm_log.jsonl")

    def plan(self, today=None):
        state = self.storage.load()
        plan = build_plan(state["profile"], state["history"], today=today or self.today, rules_data=self.rules)
        plan["name"] = state.get("name", "")
        plan["profile"] = state["profile"]
        plan["history"] = sorted(state["history"], key=lambda r: r["date"], reverse=True)
        plan["reminders"] = reminders(plan)
        plan["consent"] = state.get("consent")
        return plan

    def known_tests(self):
        return {t["test"] for t in test_catalog(self.rules)}

    def add_records(self, body):
        """Отметка о прохождении. Для диспансеризации и профосмотра в also приходит список
        входящих в них обследований: каждое сохраняется отдельной записью с той же датой."""
        known = self.known_tests()
        if body.get("test") and body["test"] not in known:
            raise ValueError("Неизвестное обследование.")
        also = [t for t in body.get("also") or [] if t in known and t != body["test"]]
        self.storage.add_record(body)
        for test in dict.fromkeys(also):
            self.storage.add_record({"test": test, "date": body["date"], "result": f"в составе: {self.title(body['test'])}"})

    def title(self, test):
        return next((t["title"] for t in test_catalog(self.rules) if t["test"] == test), test)

    def report_page(self, today=None):
        titles = {t["test"]: t["title"] for t in test_catalog(self.rules)}
        return render_report(self.plan(today), titles).encode("utf-8")

    def calendar_file(self, today=None):
        return to_ics(self.plan(today)).encode("utf-8")

    def handle(self, method, path, query, body):
        """Возвращает (код, данные). Отделено от HTTP, чтобы было удобно тестировать."""
        today = (query.get("today") or [None])[0] or None
        try:
            if method == "GET" and path == "/api/plan":
                return 200, self.plan(today)
            if method == "GET" and path == "/api/demos":
                return 200, list_demos()
            if method == "GET" and path == "/api/wiki":
                q = (query.get("q") or [""])[0]
                found = search(self.wiki, q) if q.strip() else list(self.wiki.values())
                return 200, [{k: a[k] for k in ("slug", "title", "category", "summary")} for a in found]
            if method == "GET" and path.startswith("/api/wiki/"):
                return 200, self.wiki[path.rsplit("/", 1)[-1]]
            if method == "POST" and path == "/api/ask":
                return 200, self.assistant.ask(body.get("question", ""), self.plan(today))
            if method == "GET" and path == "/api/assistant":
                return 200, {"llm_enabled": self.assistant.llm_enabled, "status": self.assistant.status()}
            if method == "GET" and path == "/api/consent":
                return 200, {"version": CONSENT_VERSION, "text": CONSENT_TEXT}
            if method == "GET" and path == "/api/tests":
                return 200, test_catalog(self.rules)
            if method == "POST" and path == "/api/profile":
                self.storage.save_profile(body.get("profile", {}), body.get("name"), consent=body.get("consent") is True)
                return 200, self.plan(today)
            if method == "POST" and path == "/api/history":
                self.add_records(body)
                return 200, self.plan(today)
            if method == "PUT" and path.startswith("/api/history/"):
                self.storage.update_record(path.rsplit("/", 1)[-1], body, self.known_tests())
                return 200, self.plan(today)
            if method == "DELETE" and path.startswith("/api/history/"):
                self.storage.delete_record(path.rsplit("/", 1)[-1])
                return 200, self.plan(today)
            if method == "POST" and path == "/api/reset":
                self.storage.reset()
                return 200, self.plan(today)
            if method == "POST" and path == "/api/demo":
                self.storage.load_demo(body.get("id", ""))
                return 200, self.plan(today)
        except KeyError:
            return 404, {"error": "Не найдено."}
        except ValueError as e:
            return 400, {"error": str(e) or "Неверные данные."}
        return 404, {"error": "Неизвестный адрес."}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, payload, content_type="application/json; charset=utf-8", headers=None):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            # базовая защита браузера: не угадывать тип файла, не встраивать страницы в чужие сайты
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method):
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                body = {}
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send(413, {"error": "Слишком большой запрос."})
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        return self._send(400, {"error": "Неверный формат запроса."})
                code, payload = app.handle(method, url.path, parse_qs(url.query), body)
                return self._send(code, payload)
            if method == "GET" and url.path == "/privacy":
                return self._send(200, policy_html().encode("utf-8"), "text/html; charset=utf-8")
            if method == "GET" and url.path == "/export.zip":
                return self._send(200, export_archive(app.storage), "application/zip",
                                  {"Content-Disposition": 'attachment; filename="health-diary-data.zip"',
                                   "Cache-Control": "no-store"})
            if method == "GET" and url.path == "/report":
                today = (parse_qs(url.query).get("today") or [None])[0]
                return self._send(200, app.report_page(today), "text/html; charset=utf-8")
            if method == "GET" and url.path == "/calendar.ics":
                today = (parse_qs(url.query).get("today") or [None])[0]
                return self._send(200, app.calendar_file(today), "text/calendar; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="health-diary.ics"'})
            if method == "GET" and url.path.startswith("/files/"):
                path = app.storage.file_path(url.path[len("/files/"):])
                if path is None:
                    return self._send(404, {"error": "Файл не найден."})
                # тип по расширению, которое при загрузке выставлено по содержимому; иное — только скачивание
                ctype = CONTENT_TYPES.get(path.suffix.lstrip(".").lower())
                if ctype is None:
                    return self._send(200, path.read_bytes(), "application/octet-stream",
                                      {"Content-Disposition": "attachment"})
                return self._send(200, path.read_bytes(), ctype,
                                  {"Content-Security-Policy": "sandbox", "Cache-Control": "no-store"})
            if method == "GET":
                name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
                path = (WEB_DIR / name).resolve()
                if WEB_DIR in path.parents and path.is_file():
                    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or ctype.endswith("javascript"):
                        ctype += "; charset=utf-8"
                    return self._send(200, path.read_bytes(), ctype)
            return self._send(404, {"error": "Не найдено."})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

        def do_PUT(self):
            self._route("PUT")

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Дневник здоровья пациента")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--today", help="фиксированная дата расчёта ГГГГ-ММ-ДД (для демонстрации)")
    parser.add_argument("--open", action="store_true", help="сразу открыть сервис в браузере")
    args = parser.parse_args()
    app = App(today=args.today)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    except OSError:
        print(f"Порт {args.port} занят: возможно, дневник уже запущен в другом окне. "
              f"Откройте http://127.0.0.1:{args.port} или запустите с --port 8001.", flush=True)
        return
    print(f"Дневник здоровья запущен: http://127.0.0.1:{args.port}  (остановить: Ctrl+C)", flush=True)
    for line in app.assistant.status():
        print(line, flush=True)
    if args.open:
        threading.Timer(0.5, webbrowser.open, [f"http://127.0.0.1:{args.port}"]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
