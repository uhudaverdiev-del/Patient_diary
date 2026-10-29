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
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from diary.privacy import redact
from diary.wiki import WIKI_DIR, search

ROOT = Path(__file__).resolve().parent.parent
YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

SYSTEM_PROMPT = (
    "Ты помощник в сервисе «CheckUp Track». Отвечай по-русски, коротко и понятно для пациента. "
    "Используй ТОЛЬКО факты из пронумерованных источников ниже. После каждого факта ставь номер "
    "источника в квадратных скобках, например: «Маммографию проходят раз в 2 года [2].» Ответ без таких "
    "ссылок не будет показан пациенту. Источник [1] — личный план пациента: на вопросы «что мне пройти», "
    "«что просрочено», «когда» отвечай по нему. Если в источниках есть хотя бы часть ответа, дай её со ссылками. "
    "Только если в источниках совсем ничего нет по вопросу, напиши одно слово: НЕТ_В_ИСТОЧНИКАХ. "
    "Не ставь диагнозы и не назначай лечение."
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


def load_faq(path=ROOT / "data" / "faq.json", sources_path=WIKI_DIR / "_sources.json"):
    """Частые вопросы с готовыми ответами: AIbert отвечает на них и без нейросети."""
    refs = json.loads(sources_path.read_text(encoding="utf-8"))
    faq = json.loads(path.read_text(encoding="utf-8"))
    for entry in faq:
        entry["refs"] = [{"wiki": r[5:]} if r.startswith("wiki:") else {"title": refs[r]["title"], "url": refs[r]["url"]}
                         for r in entry["sources"]]
    return faq


FAQ = load_faq()


def _norm(text):
    return " ".join(re.findall(r"\w+", text.lower().replace("ё", "е")))


def faq_answer(question, wiki, faq=FAQ):
    """Первый частый вопрос, у которого в вопросе пациента нашлось по слову из каждой группы.
    Короткие слова («нет», «без», «где») ищем только с начала слова, остальные где угодно:
    так «осмотр» находится и в «профосмотре»."""
    q = " " + _norm(question)

    def has(stem):
        stem = _norm(stem)
        return (" " + stem) in q if len(stem) <= 3 else stem in q

    for entry in faq:
        if all(any(has(stem) for stem in group) for group in entry["match"]):
            sources = [{"title": "Справка: " + wiki[r["wiki"]]["title"], "wiki": r["wiki"]} if "wiki" in r else r
                       for r in entry["refs"] if "wiki" not in r or r["wiki"] in wiki]
            return {"answer": entry["answer"], "sources": sources}
    return None


def similar_questions(question, faq=FAQ, n=3):
    """Похожие частые вопросы: подсказка вместо пустого «не знаю»."""
    words = {w[:5] for w in _norm(question).split() if len(w) > 3}
    scored = sorted(faq, key=lambda e: -len(words & {w[:5] for w in _norm(e["question"]).split() if len(w) > 3}))
    return [e["question"] for e in scored[:n]]


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
    """Ответ без нейросети: частые вопросы, правила, поиск по плану и Wiki."""
    q = question.lower()
    found = faq_answer(question, wiki)
    if found:
        return found
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
        no_data = [i for i in items if i["status"] == "no_data"]
        empty = "Просроченных обследований нет."
        if no_data:
            empty += (" Но нет отметок о прохождении: " + "; ".join(i["title"] for i in no_data)
                      + ". Пройдите их при ближайшем визите или укажите даты.")
        return listing("Просрочены:", [i for i in items if i["status"] == "overdue"], empty)

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
    return {"answer": "Точного ответа на этот вопрос у меня нет. Если вопрос о здоровье, лучше спросить терапевта. "
                      "Возможно, вам подойдёт один из этих вопросов:",
            "sources": [], "suggest": similar_questions(question)}


MAX_PARTS = 5


def split_questions(text):
    """Делит сообщение на отдельные вопросы: по знаку «?» и по переносу строки.
    Совсем короткие куски вроде «А мне?» приклеиваем к соседнему, чтобы не терять смысл."""
    pieces = [p.strip() for p in re.split(r"(?<=\?)(?!\?)|\n+", text) if p and p.strip()]
    parts = []
    for piece in pieces:
        if parts and (len(piece) < 9 or len(parts[-1]) < 9):
            parts[-1] = f"{parts[-1]} {piece}"
        else:
            parts.append(piece)
    if len(parts) > MAX_PARTS:
        parts = parts[:MAX_PARTS - 1] + [" ".join(parts[MAX_PARTS - 1:])]
    return parts or [text]


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
                 "x-folder-id": env["YANDEX_FOLDER_ID"],
                 # Яндекс по умолчанию сохраняет запросы для улучшения моделей; данные о здоровье не оставляем.
                 "x-data-logging-enabled": "false"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["result"]["alternatives"][0]["message"]["text"]


def explain_error(e):
    """Почему YandexGPT не ответил, простыми словами. Ключ в текст не попадает: его нет ни в ответе, ни в ошибке."""
    if isinstance(e, urllib.error.HTTPError):
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
            err = body.get("error", body) if isinstance(body, dict) else {}
            detail = str(err.get("message", "") if isinstance(err, dict) else err)[:200]
        except Exception:
            pass
        hints = {
            400: "Яндекс не принял запрос: проверьте YANDEX_FOLDER_ID (идентификатор каталога вида b1g…, а не его название)",
            401: "Яндекс не принял ключ: проверьте YANDEX_API_KEY (нужен API-ключ сервисного аккаунта, без пробелов и кавычек)",
            403: "нет доступа: дайте сервисному аккаунту роль ai.languageModels.user на этот каталог и проверьте, что в облаке подключён платёжный аккаунт",
            404: "модель не найдена: проверьте YANDEX_FOLDER_ID и YANDEX_MODEL",
            429: "превышен лимит запросов к Яндексу, попробуйте через минуту",
        }
        hint = hints.get(e.code, "ошибка на стороне Яндекса, попробуйте позже" if e.code >= 500 else "Яндекс вернул ошибку")
        return f"{hint} (код {e.code}{': ' + detail if detail else ''})"
    text = str(getattr(e, "reason", "") or e)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return ("Python не может проверить сертификат сайта Яндекса. На Mac откройте папку «Программы → Python 3» "
                "и дважды щёлкните «Install Certificates.command», затем перезапустите дневник")
    if isinstance(e, (TimeoutError, OSError)) or "timed out" in text:
        return f"нет связи с Яндексом: проверьте интернет, VPN или антивирус ({text[:120]})"
    return f"ответ Яндекса не удалось разобрать ({type(e).__name__})"


SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def normalize_citations(text):
    """Приводит ссылки модели к виду [1][2]: YandexGPT пишет и [1, 2], и [1–3], и [источник 1], и ¹."""
    def numbers(inner):
        found = []
        for a, b in re.findall(r"(\d+)\s*(?:[-–—]\s*(\d+))?", inner):
            lo, hi = int(a), int(b or a)
            found.extend(range(lo, hi + 1) if 0 < hi - lo < 10 else [lo])
        return "".join(f"[{n}]" for n in found)

    text = re.sub(r"\[\s*(?:источник[и]?\s*)?№?\s*(\d+(?:\s*[,;–—-]\s*№?\s*\d+)*)\s*\]",
                  lambda m: numbers(m.group(1)), text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*источник[и]?\s*№?\s*(\d+(?:\s*[,;–—-]\s*\d+)*)\s*\)",
                  lambda m: numbers(m.group(1)), text, flags=re.IGNORECASE)
    return re.sub(r"(?<=[^\s\d])([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", lambda m: f"[{m.group(1).translate(SUPERSCRIPT)}]", text)


NOT_FOUND = "НЕТ_В_ИСТОЧНИКАХ"


def is_refusal(text):
    """Модель честно не нашла ответа: «не знаю», «нет в источниках», «не могу ответить» без единой ссылки."""
    if re.search(r"\[\d+\]", text):
        return False
    low = text.lower().replace("ё", "е")
    return NOT_FOUND.lower() in low or bool(re.search(r"\bне знаю\b|не могу (ответить|помочь)|нет (информации|данных|ответа)", low))


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
        parts = split_questions(question)
        if len(parts) > 1:
            # Несколько вопросов в одном сообщении: отвечаем на каждый отдельно, со своими источниками.
            return {"parts": [dict(self._ask_one(q, plan), question=q) for q in parts]}
        return self._ask_one(question, plan)

    def _ask_one(self, question, plan):
        fallback = local_answer(question, plan, self.wiki)
        # На частые вопросы отвечаем проверенным текстом и без нейросети: так ответ не зависит от сети и не «плывёт».
        if not self.llm_enabled or not plan.get("ready") or faq_answer(question, self.wiki):
            return dict(fallback, mode="local")
        # телефоны, e-mail и номера документов не уходят в облако и не пишутся в журнал
        safe_question = redact(question)
        context, sources = build_context(safe_question, plan, self.wiki)
        user = f"Источники:\n{context}\n\nВопрос пациента: {safe_question}"
        try:
            text = normalize_citations(self.llm(SYSTEM_PROMPT, user).strip())
        except Exception as e:  # сеть, ключ, квота — показываем локальный ответ
            reason = explain_error(e)
            self._log(user, None, f"error: {e}; {reason}")
            return dict(fallback, mode="local", note=f"YandexGPT недоступен: {reason}. Показан локальный ответ.")
        if is_refusal(text):
            self._log(user, text, "refused: no answer in sources")
            return dict(fallback, mode="local", note="YandexGPT не нашёл ответа в источниках, показан ответ дневника.")
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
