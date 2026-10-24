"""Веб-интерфейс дневника здоровья.

Запуск:  python app.py        затем открыть http://127.0.0.1:8000
         python app.py --port 8080 --today 2026-10-24   (фиксированная дата для демо)
"""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from diary.engine import build_plan, describe_rule, load_rules
from diary.storage import Storage, list_demos

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 15 * 1024 * 1024


def test_catalog(rules_data):
    """Список обследований для формы «Отметить прохождение»."""
    seen = {}
    for rule in rules_data["rules"]:
        seen.setdefault(rule["test"], {"test": rule["test"], "title": rule["title"], "who": []})
        seen[rule["test"]]["who"].append(describe_rule(rule))
    seen.setdefault("profosmotr", {"test": "profosmotr", "title": "Профилактический медицинский осмотр", "who": []})
    return list(seen.values())


class App:
    def __init__(self, storage=None, today=None):
        self.storage = storage or Storage()
        self.today = today
        self.rules = load_rules()

    def plan(self, today=None):
        state = self.storage.load()
        plan = build_plan(state["profile"], state["history"], today=today or self.today, rules_data=self.rules)
        plan["name"] = state.get("name", "")
        plan["profile"] = state["profile"]
        plan["history"] = sorted(state["history"], key=lambda r: r["date"], reverse=True)
        return plan

    def handle(self, method, path, query, body):
        """Возвращает (код, данные). Отделено от HTTP, чтобы было удобно тестировать."""
        today = (query.get("today") or [None])[0] or None
        try:
            if method == "GET" and path == "/api/plan":
                return 200, self.plan(today)
            if method == "GET" and path == "/api/demos":
                return 200, list_demos()
            if method == "GET" and path == "/api/tests":
                return 200, test_catalog(self.rules)
            if method == "POST" and path == "/api/profile":
                self.storage.save_profile(body.get("profile", {}), body.get("name"))
                return 200, self.plan(today)
            if method == "POST" and path == "/api/history":
                self.storage.add_record(body)
                return 200, self.plan(today)
            if method == "DELETE" and path.startswith("/api/history/"):
                self.storage.delete_record(path.rsplit("/", 1)[-1])
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

        def _send(self, code, payload, content_type="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
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
            if method == "GET" and url.path.startswith("/files/"):
                path = app.storage.file_path(url.path[len("/files/"):])
                if path is None:
                    return self._send(404, {"error": "Файл не найден."})
                ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                return self._send(200, path.read_bytes(), ctype)
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

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Дневник здоровья пациента")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--today", help="фиксированная дата расчёта ГГГГ-ММ-ДД (для демонстрации)")
    args = parser.parse_args()
    app = App(today=args.today)
    if not app.storage.state_path.exists():
        app.storage.load_demo("anna")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    print(f"Дневник здоровья запущен: http://127.0.0.1:{args.port}  (остановить: Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
