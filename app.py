"""Веб-интерфейс дневника здоровья.

Запуск:  python app.py        затем открыть http://127.0.0.1:8000
         python app.py --port 8080 --today 2026-10-24   (фиксированная дата для демо)
"""

import argparse
import json
import mimetypes
import sys
import threading
import traceback
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from diary.assistant import Assistant
from diary.preparation import prep_for
from diary.privacy import CONSENT_TEXT, CONSENT_VERSION, CONTENT_TYPES, export_archive, policy_html
from diary.engine import build_plan, describe_rule, load_rules, parse_date
from diary.letters import dispute_letter, refusal_letter
from diary.metrics import summarize
from diary.motivation import sun_rays
from diary.reminders import reminders, to_ics
from diary.report import render_report
from diary.storage import Storage, list_demos
from diary.wiki import load_wiki, search

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
MAX_BODY = 15 * 1024 * 1024
OLDEST_RECORD = date(1900, 1, 1)
# Дневник работает только на этом компьютере: другие адреса в заголовке Host означают,
# что страницу открыли через чужой сайт (подмена адреса, DNS rebinding).
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
# Страницы могут грузить только свои файлы и отправлять данные только на свой адрес.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")


def fmt_date(iso):
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def check_today(value):
    """Дата расчёта из адреса (?today=): только настоящая дата ГГГГ-ММ-ДД."""
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError("Дата расчёта должна быть в формате ГГГГ-ММ-ДД.")


def second_stage(history, today):
    """Направление на второй этап по последней диспансеризации: пока он не пройден, это главный шаг."""
    disp = [r for r in history if r["test"] == "dispanserization" and r["date"] <= today]
    last = max(disp, key=lambda r: r["date"], default=None)
    if last and last.get("second_stage") == "referred":
        return {"id": last["id"], "date": last["date"]}
    return None


def test_catalog(rules_data):
    """Список обследований для формы «Отметить прохождение»."""
    seen = {}
    for rule in rules_data["rules"] + rules_data.get("vaccines", []):
        seen.setdefault(rule["test"], {"test": rule["test"], "title": rule["title"], "who": []})
        seen[rule["test"]]["who"].append(rule.get("who") or describe_rule(rule))
    for extra in rules_data.get("extra_tests", []):
        seen[extra["test"]] = {"test": extra["test"], "title": extra["title"], "who": [extra["who"]]}
    for entry in seen.values():
        entry["prep"] = prep_for(entry["test"])
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
        disputes = sorted(state.get("disputes", []), key=lambda d: d["date"], reverse=True)
        # Запись, которую пациент отметил как приписку, в расчёте сроков не участвует.
        disputed = {(d["test"], d["date"]) for d in disputes}
        counted = [r for r in state["history"] if (r["test"], r["date"]) not in disputed]
        plan = build_plan(state["profile"], counted, today=today or self.today, rules_data=self.rules)
        for item in plan["items"] + plan["vaccines"]:
            for d in disputes:
                if d["test"] == item["test"]:
                    item["notes"].append(f"Вы отметили ошибочную запись в выписке ОМС от {fmt_date(d['date'])}: "
                                         "в плане она не учитывается.")
        plan["disputes"] = disputes
        plan["name"] = state.get("name", "")
        plan["profile"] = state["profile"]
        plan["history"] = sorted(state["history"], key=lambda r: r["date"], reverse=True)
        plan["second_stage"] = second_stage(counted, plan["today"])
        plan["reminders"] = reminders(plan)
        plan["consent"] = state.get("consent")
        plan["metrics"] = summarize(state.get("metrics", []))
        plan["sun"] = sun_rays(plan)
        return plan

    def known_tests(self):
        return {t["test"] for t in test_catalog(self.rules)}

    def check_record_date(self, value, today=None):
        """Дата прохождения не может быть позже даты расчёта: иначе план считал бы будущее пройденным."""
        if value is None:
            return
        try:
            when = date.fromisoformat(str(value))
        except ValueError:
            raise ValueError("Укажите дату прохождения в формате ГГГГ-ММ-ДД.")
        limit = parse_date(today or self.today) or date.today()
        if when > limit:
            raise ValueError("Дата прохождения не может быть позже сегодняшней.")
        if when < OLDEST_RECORD:
            raise ValueError("Проверьте год в дате прохождения.")

    def add_records(self, body, today=None):
        """Отметка о прохождении. Для диспансеризации и профосмотра в also приходит список
        входящих в них обследований: каждое сохраняется отдельной записью с той же датой."""
        known = self.known_tests()
        if body.get("test") is not None and (not isinstance(body["test"], str) or body["test"] not in known):
            raise ValueError("Неизвестное обследование.")
        self.check_record_date(body.get("date"), today)
        also = body.get("also") if isinstance(body.get("also"), list) else []
        also = [t for t in also if t in known and t != body["test"]]
        self.storage.add_record(body)
        for test in dict.fromkeys(also):
            self.storage.add_record({"test": test, "date": body["date"], "result": f"в составе: {self.title(body['test'])}"})

    def title(self, test):
        return next((t["title"] for t in test_catalog(self.rules) if t["test"] == test), test)

    def letter(self, body, today=None):
        plan = self.plan(today)
        when_today = date.fromisoformat(plan["today"])
        if body.get("kind") == "dispute":
            found = next((d for d in plan["disputes"] if d["id"] == body.get("id")), None)
            if found is None:
                raise KeyError(body.get("id"))
            return dispute_letter(self.title(found["test"]), found["date"], when_today)
        item = next((i for i in plan["items"] + plan["vaccines"] if i["test"] == body.get("test")), None)
        if item is None:
            raise ValueError("Этого обследования нет в вашем плане.")
        return refusal_letter(item, body.get("reason"), body.get("date"), body.get("addressee"), when_today)

    def report_page(self, today=None):
        titles = {t["test"]: t["title"] for t in test_catalog(self.rules)}
        return render_report(self.plan(today), titles).encode("utf-8")

    def calendar_file(self, today=None):
        return to_ics(self.plan(today)).encode("utf-8")

    def handle(self, method, path, query, body):
        """Возвращает (код, данные). Отделено от HTTP, чтобы было удобно тестировать."""
        try:
            today = check_today((query.get("today") or [None])[0])
            if not isinstance(body, dict):
                raise ValueError("Неверный формат запроса.")
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
                question = body.get("question", "")
                if not isinstance(question, str):
                    raise ValueError("Задайте вопрос текстом.")
                return 200, self.assistant.ask(question, self.plan(today))
            if method == "GET" and path == "/api/assistant":
                return 200, {"llm_enabled": self.assistant.llm_enabled, "status": self.assistant.status()}
            if method == "GET" and path == "/api/consent":
                return 200, {"version": CONSENT_VERSION, "text": CONSENT_TEXT}
            if method == "GET" and path == "/api/tests":
                return 200, test_catalog(self.rules)
            if method == "POST" and path == "/api/profile":
                if not isinstance(body.get("profile", {}), dict):
                    raise ValueError("Неверный формат профиля.")
                self.storage.save_profile(body.get("profile", {}), body.get("name"), consent=body.get("consent") is True)
                return 200, self.plan(today)
            if method == "POST" and path == "/api/history":
                self.add_records(body, today)
                return 200, self.plan(today)
            if method == "PUT" and path.startswith("/api/history/"):
                self.check_record_date(body.get("date"), today)
                self.storage.update_record(path.rsplit("/", 1)[-1], body, self.known_tests())
                return 200, self.plan(today)
            if method == "DELETE" and path.startswith("/api/history/"):
                self.storage.delete_record(path.rsplit("/", 1)[-1])
                return 200, self.plan(today)
            if method == "POST" and path == "/api/metrics":
                self.storage.add_metric(body, parse_date(today or self.today))
                return 200, self.plan(today)
            if method == "DELETE" and path.startswith("/api/metrics/"):
                self.storage.delete_metric(path.rsplit("/", 1)[-1])
                return 200, self.plan(today)
            if method == "POST" and path == "/api/reset":
                self.storage.reset()
                return 200, self.plan(today)
            if method == "POST" and path == "/api/letter":
                # Текст письма только возвращается: не сохраняется, не пишется в журнал, не уходит в нейросеть.
                return 200, {"text": self.letter(body, today)}
            if method == "POST" and path == "/api/disputes":
                self.storage.add_dispute(body, self.known_tests(), date.fromisoformat(self.plan(today)["today"]))
                return 200, self.plan(today)
            if method == "DELETE" and path.startswith("/api/disputes/"):
                self.storage.delete_dispute(path.rsplit("/", 1)[-1])
                return 200, self.plan(today)
            if method == "POST" and path == "/api/demo":
                self.storage.load_demo(str(body.get("id", "")))
                return 200, self.plan(today)
        except KeyError:
            return 404, {"error": "Не найдено."}
        except ValueError as e:
            return 400, {"error": str(e) or "Неверные данные."}
        return 404, {"error": "Неизвестный адрес."}


def make_handler(app):
    # Изменения данных идут по одному, чтобы два одновременных сохранения не затёрли друг друга.
    write_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        timeout = 30  # запрос, который не дослал данные, не держит сервер вечно

        def log_message(self, fmt, *args):
            pass

        def _host_ok(self):
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").strip().lower()
            return host in {f"{h}:{port}" for h in LOCAL_HOSTS} or (port == 80 and host in LOCAL_HOSTS)

        def _same_site(self):
            """Изменять данные можно только со страниц самого дневника, а не с чужого сайта.
            Браузер не даст чужому сайту отправить запрос с типом application/json без нашего разрешения,
            а заголовки Origin и Sec-Fetch-Site прямо говорят, откуда пришёл запрос."""
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return False
            if (self.headers.get("Sec-Fetch-Site") or "same-origin") not in ("same-origin", "none"):
                return False
            origin = self.headers.get("Origin")
            if origin is not None:
                port = self.server.server_address[1]
                return origin.lower() in {f"http://{h}:{port}" for h in LOCAL_HOSTS}
            return True

        def _send(self, code, payload, content_type="application/json; charset=utf-8", headers=None):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            # базовая защита браузера: не угадывать тип файла, не встраивать страницы в чужие сайты
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            headers = dict(headers or {})
            # данные о здоровье не оставляем в кэше браузера на диске
            headers.setdefault("Cache-Control", "no-store")
            if content_type.startswith("text/html"):
                headers.setdefault("Content-Security-Policy", CSP)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method):
            try:
                self._route_safe(method)
            except Exception:
                # Сбой в одном запросе не роняет сервер и не обрывает соединение молча.
                traceback.print_exc(file=sys.stderr)
                try:
                    self._send(500, {"error": "Что-то пошло не так. Обновите страницу и попробуйте ещё раз."})
                except OSError:
                    pass

        def _route_safe(self, method):
            url = urlparse(self.path)
            if not self._host_ok():
                return self._send(403, {"error": "Дневник открывается только по адресу http://127.0.0.1."})
            if method != "GET" and not self._same_site():
                return self._send(403, {"error": "Запрос отклонён: изменять данные можно только со страницы дневника."})
            if url.path.startswith("/api/"):
                body = {}
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if length < 0:
                    return self._send(400, {"error": "Неверный формат запроса."})
                if length > MAX_BODY:
                    return self._send(413, {"error": "Слишком большой запрос."})
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        return self._send(400, {"error": "Неверный формат запроса."})
                if method != "GET" and url.path != "/api/ask":
                    with write_lock:
                        code, payload = app.handle(method, url.path, parse_qs(url.query), body)
                else:
                    code, payload = app.handle(method, url.path, parse_qs(url.query), body)
                return self._send(code, payload)
            if method == "GET" and url.path == "/privacy":
                return self._send(200, policy_html().encode("utf-8"), "text/html; charset=utf-8")
            if method == "GET" and url.path == "/export.zip":
                return self._send(200, export_archive(app.storage), "application/zip",
                                  {"Content-Disposition": 'attachment; filename="health-diary-data.zip"',
                                   "Cache-Control": "no-store"})
            if method == "GET" and url.path in ("/report", "/calendar.ics"):
                try:
                    today = check_today((parse_qs(url.query).get("today") or [None])[0])
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
            if method == "GET" and url.path == "/report":
                return self._send(200, app.report_page(today), "text/html; charset=utf-8")
            if method == "GET" and url.path == "/calendar.ics":
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
