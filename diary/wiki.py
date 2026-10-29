"""База знаний «Здоровье / Wiki»: статьи в data/wiki/*.md с источниками."""

import json
import re
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent.parent / "data" / "wiki"
CHECKED = "2026-10-24"  # если в статье нет своей даты проверки (поле checked)


def _parse(path, sources):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        raise ValueError(f"В статье {path.name} нет заголовка между ---")
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    refs = [s.strip() for s in meta.get("sources", "").split(",") if s.strip()]
    return {
        "slug": path.stem,
        "title": meta["title"],
        "category": meta.get("category", ""),
        "summary": meta.get("summary", ""),
        "body": match.group(2).strip(),
        "sources": [dict(sources[r], id=r) for r in refs],
        "checked": meta.get("checked", CHECKED),
    }


def load_wiki(wiki_dir=WIKI_DIR):
    sources = json.loads((wiki_dir / "_sources.json").read_text(encoding="utf-8"))
    articles = {}
    for path in sorted(wiki_dir.glob("*.md")):
        article = _parse(path, sources)
        articles[article["slug"]] = article
    return articles


TOKEN = re.compile(r"[^\W\d_]+|\d+")


# Как люди пишут в поиске → как написано в статьях.
SYNONYMS = {"ковид": "covid", "коронавирус": "covid"}


def _tokens(text):
    """Слова и числа отдельно: «COVID-19» и «covid19» дают «covid» и «19»."""
    return TOKEN.findall(text.lower().replace("ё", "е"))


def _term_hit(term, text):
    """Есть ли слово запроса в тексте (text — токены через пробел, с пробелом в начале и в конце).
    Число ищем целиком: «19» не находится в «1985». Слово — по основе с начала слова, чтобы «Казани»
    не находилось внутри «показаниям»; у коротких слов основа 4 буквы, чтобы «кровь» нашла «крови»."""
    if term.isdigit():
        return f" {term} " in text
    return f" {term[:5] if len(term) > 5 else term[:4]}" in text


def search(articles, query, min_score=1):
    """Поиск по словам и числам: совпадение в заголовке или кратком описании весит 3, в тексте — 1.
    Если запрос целиком есть в заголовке или описании, статья поднимается наверх."""
    terms = [SYNONYMS.get(t, t) for t in _tokens(query) if len(t) > 2 or t.isdigit()]
    phrase = " ".join(_tokens(query))
    scored = []
    for a in articles.values():
        title = " " + " ".join(_tokens(a["title"] + " " + a["summary"])) + " "
        body = " " + " ".join(_tokens(a["body"])) + " "
        score = sum(3 * _term_hit(t, title) + _term_hit(t, body) for t in terms)
        if score and len(terms) > 1 and all(_term_hit(t, title) or _term_hit(t, body) for t in terms):
            score += 2  # нашлись все слова запроса
        if phrase and len(phrase) > 2 and f" {phrase}" in title:
            score += 10
        if score >= min_score:
            scored.append((score, a["slug"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [articles[slug] for _, slug in scored]
