"""База знаний «Здоровье / Wiki»: статьи в data/wiki/*.md с источниками."""

import json
import re
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent.parent / "data" / "wiki"
CHECKED = "2026-10-24"


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
        "checked": CHECKED,
    }


def load_wiki(wiki_dir=WIKI_DIR):
    sources = json.loads((wiki_dir / "_sources.json").read_text(encoding="utf-8"))
    articles = {}
    for path in sorted(wiki_dir.glob("*.md")):
        article = _parse(path, sources)
        articles[article["slug"]] = article
    return articles


def search(articles, query):
    """Простой поиск по словам: сначала совпадения в заголовке, затем в тексте."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    scored = []
    for a in articles.values():
        title = (a["title"] + " " + a["summary"]).lower()
        body = a["body"].lower()
        score = sum(3 * (w[:5] in title) + (w[:5] in body) for w in words)
        if score:
            scored.append((score, a["slug"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [articles[slug] for _, slug in scored]
