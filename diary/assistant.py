"""AI-ассистент по дневнику и базе знаний.

Два режима:
  * локальный — ответ собирается по правилам из плана и статей Wiki, работает без интернета;
  * YandexGPT — если в .env есть YANDEX_API_KEY и YANDEX_FOLDER_ID. Модель получает
    только пронумерованные фрагменты плана и статей и обязана ссылаться на них.
    Ответ без ссылок или со ссылками на несуществующие источники отбрасывается,
    вместо него показывается локальный ответ.

В облако не уходят имя и дата рождения: только возраст, пол и пункты плана.
"""

import json
import os
import re
import urllib.request
from datetime import date, datetime
from pathlib import Path

from diary.wiki import search

ROOT = Path(__file__).resolve().parent.parent
YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

SYSTEM_PROMPT = (
    "Ты помощник в сервисе «Дневник здоровья». Отвечай по-русски, коротко и понятно для пациента. "
    "Используй ТОЛЬКО факты из пронумерованных источников ниже. После каждого факта ставь номер "
    "источника в квадратных скобках, например [2]. Если ответа в источниках нет, честно скажи, что "
    "не знаешь, и посоветуй обратиться к терапевту. Не ставь диагнозы и не назначай лечение."
)


ENV_NAMES = (".env", ".env.txt", "env.txt", "env")  # Блокнот любит дописывать .txt


def find_env_file(root=ROOT):
    for name in ENV_NAMES:
        path = Path(root) / name
        if path.is_file():
            return path
    return None


def _read_text(path):
    """Текст файла в любой кодировке, которую может выбрать Блокнот."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


def load_env(path=None):
    """Читает .env (KEY=VALUE) без сторонних библиотек. Переменные окружения важнее."""
    path = find_env_file() if path is None else Path(path)
    values = {}
    if path and path.is_file():
        for line in _read_text(path).splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip().upper()] = value.strip().strip('"').strip("'").strip()
    for key in ("YANDEX_API_KEY", "YANDEX_FOLDER_ID", "YANDEX_MODEL"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def env_report(env, path, root=ROOT):
    """Понятное объяснение, нашёл ли сервис ключ и где искал (для консоли и вкладки «Ассистент»)."""
    missing = [k for k in ("YANDEX_API_KEY", "YANDEX_FOLDER_ID") if not env.get(k)]
    if not missing:
        where = f"из файла {path.name}" if path else "из переменных окружения"
        return [f"YandexGPT: ключ найден ({where}), ассистент работает через нейросеть."]
    lines = ["YandexGPT: выключен, ассистент работает в локальном режиме."]
    if path is None:
        lines.append(f"  Не найден файл .env в папке: {Path(root).resolve()}")
        lines.append("  Создайте файл .env в этой папке, рядом с app.py.")
    else:
        lines.append(f"  Файл {path.name} найден, но в нём нет строк: {', '.join(missing)}")
        lines.append("  Формат: YANDEX_API_KEY=ваш_ключ и YANDEX_FOLDER_ID=id_каталога, каждое с новой строки.")
    return lines


def _fmt(iso):
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else "—"


def _item_line(item):
    line = f"{item['title']} — {item['status_label']}"
    if item["next_due"]:
        line += f", срок: {_fmt(item['next_due'])}"
    return line


def _plan_source(item):
    return {"title": f"{item['source']['clause']} приказа № 404н — {item['title']}", "url": item["source"]["url"]}


def _wiki_source(article):
    return {"title": f"Wiki: {article['title']}", "wiki": article["slug"]}


def _why_text(article):
    """Раздел «Зачем» статьи, если он есть, иначе краткое описание."""
    match = re.search(r"^## Зачем\n+(.*?)(?=\n## |\Z)", article["body"], re.S | re.M)
    if match:
        return " ".join(match.group(1).split())
    return article["summary"]


def _find_items(plan, question):
    """Пункты плана, о которых спрашивают (по словам из названия)."""
    q = question.lower()
    found = []
    for item in plan["items"]:
        words = [w for w in re.findall(r"\w+", item["title"].lower()) if len(w) >= 3]
        if any(w[:6] in q for w in words):
            found.append(item)
    return found


def local_answer(question, plan, wiki):
    """Ответ без нейросети: правила + поиск по плану и Wiki."""
    q = question.lower()
    if not plan.get("ready"):
        msg = " ".join(p["message"] for p in plan["problems"] if p["level"] == "blocking")
        return {"answer": f"Пока не могу составить план: {msg}", "sources": []}

    today = date.fromisoformat(plan["today"])
    items = plan["items"]

    def listing(title, chosen, empty):
        if not chosen:
            return {"answer": empty, "sources": []}
        lines = [f"{i + 1}. {_item_line(it)}" for i, it in enumerate(chosen)]
        sources = []
        for it in chosen:
            src_title = ("Приказ № 404н, приложение 2 (онкоскрининг)" if "прил." in it["source"]["clause"]
                         else "Приказ Минздрава № 404н (профосмотр и диспансеризация)")
            src = {"title": src_title, "url": it["source"]["url"]}
            if src not in sources:
                sources.append(src)
        return {"answer": title + "\n" + "\n".join(lines), "sources": sources}

    if "просроч" in q:
        return listing("Просрочены:", [i for i in items if i["status"] == "overdue"],
                       "Просроченных обследований нет.")

    asks_why = any(w in q for w in ("зачем", "почему", "для чего", "что такое", "что это"))
    mentioned = _find_items(plan, question)
    if asks_why or (mentioned and "когда" in q):
        articles = []
        for it in mentioned:
            if it.get("wiki") in wiki and wiki[it["wiki"]] not in articles:
                articles.append(wiki[it["wiki"]])
        if not articles:
            articles = search(wiki, question, min_score=3)[:1]
        if articles or mentioned:
            parts, sources = [], []
            for a in articles:
                parts.append(f"{a['title']}. {_why_text(a) if asks_why else a['summary']}")
                sources.append(_wiki_source(a))
            for it in mentioned:
                parts.append(f"В вашем плане: {_item_line(it)}.")
                sources.append(_plan_source(it))
            return {"answer": "\n".join(parts), "sources": sources}

    if "скоро" in q or "ближайш" in q:
        return listing("Скоро необходимо пройти:", [i for i in items if i["status"] == "soon"],
                       "В ближайшие два месяца ничего проходить не нужно.")

    if re.search(r"\bгод[ауе]?\b|\bплан|пройти|сдать", q):
        this_year = [i for i in items if i["status"] != "actual"
                     or (i["next_due"] and date.fromisoformat(i["next_due"]).year <= today.year)]
        return listing(f"В {today.year} году вам нужно пройти:", this_year,
                       f"В {today.year} году все обследования уже пройдены. Следующее: "
                       f"{_item_line(items[0]) if items else '—'}.")

    articles = search(wiki, question, min_score=3)[:2]
    if articles:
        return {"answer": "\n".join(f"{a['title']}: {a['summary']}" for a in articles),
                "sources": [_wiki_source(a) for a in articles]}
    return {"answer": "Не нашёл ответа в вашем дневнике и базе знаний. С этим вопросом лучше обратиться к терапевту.",
            "sources": []}


def build_context(question, plan, wiki):
    """Пронумерованные источники для модели. Без имени и даты рождения."""
    sources, blocks = [], []
    if plan.get("ready"):
        sex = {"female": "женский", "male": "мужской"}.get(plan.get("profile", {}).get("sex"), "не указан")
        lines = "\n".join("- " + _item_line(i) for i in plan["items"])
        sources.append({"title": "Ваш план профилактики (приказ № 404н)", "url": plan["items"][0]["source"]["url"] if plan["items"] else None})
        blocks.append(f"Персональный план на {_fmt(plan['today'])}. Возраст {plan['age']}, пол {sex}.\n{lines}")
    candidates = []
    for it in _find_items(plan, question) if plan.get("ready") else []:
        if it.get("wiki") in wiki and wiki[it["wiki"]] not in candidates:
            candidates.append(wiki[it["wiki"]])
    for a in search(wiki, question):
        if a not in candidates:
            candidates.append(a)
    for a in candidates[:3]:
        sources.append(_wiki_source(a))
        blocks.append(f"{a['title']}\n{a['body'][:1500]}")
    text = "\n\n".join(f"[{n}] {b}" for n, b in enumerate(blocks, 1))
    return text, sources


def ask_yandex(system, user, env, timeout=30):
    body = {
        "modelUri": f"gpt://{env['YANDEX_FOLDER_ID']}/{env.get('YANDEX_MODEL', 'yandexgpt/latest')}",
        "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": "800"},
        "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
    }
    req = urllib.request.Request(
        YANDEX_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Api-Key {env['YANDEX_API_KEY']}",
                 "x-folder-id": env["YANDEX_FOLDER_ID"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["result"]["alternatives"][0]["message"]["text"]


def citations_ok(text, n_sources):
    cited = [int(n) for n in re.findall(r"\[(\d+)\]", text)]
    return bool(cited) and all(1 <= n <= n_sources for n in cited)


class Assistant:
    def __init__(self, wiki, env=None, llm=None, log_path=None):
        self.wiki = wiki
        self.env_path = find_env_file() if env is None else None
        self.env = load_env(self.env_path or ROOT / ".env") if env is None else env
        # llm(system, user) -> str; по умолчанию YandexGPT, в тестах подменяется
        self.llm = llm or (lambda s, u: ask_yandex(s, u, self.env))
        self.log_path = Path(log_path or ROOT / "data" / "user" / "llm_log.jsonl")

    @property
    def llm_enabled(self):
        return bool(self.env.get("YANDEX_API_KEY") and self.env.get("YANDEX_FOLDER_ID"))

    def status(self):
        return env_report(self.env, self.env_path)

    def ask(self, question, plan):
        question = (question or "").strip()[:500]
        if not question:
            return {"answer": "Задайте вопрос, например: «Что мне нужно пройти в этом году?»", "sources": [], "mode": "local"}
        fallback = local_answer(question, plan, self.wiki)
        if not self.llm_enabled or not plan.get("ready"):
            return dict(fallback, mode="local")
        context, sources = build_context(question, plan, self.wiki)
        user = f"Источники:\n{context}\n\nВопрос пациента: {question}"
        try:
            text = self.llm(SYSTEM_PROMPT, user).strip()
        except Exception as e:  # сеть, ключ, квота — показываем локальный ответ
            self._log(user, None, f"error: {e}")
            return dict(fallback, mode="local", note="YandexGPT недоступен, показан локальный ответ.")
        if not citations_ok(text, len(sources)):
            self._log(user, text, "rejected: no valid citations")
            return dict(fallback, mode="local", note="Ответ модели без ссылок на источники отклонён, показан локальный ответ.")
        self._log(user, text, "accepted")
        return {"answer": text, "sources": sources, "mode": "yandexgpt", "numbered": True}

    def _log(self, prompt, answer, verdict):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "prompt": prompt,
                                "answer": answer, "verdict": verdict}, ensure_ascii=False) + "\n")
