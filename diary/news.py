"""Новости здоровья из открытого источника: лента Минздрава России (Atom).

Из ленты берутся только новости на темы пациента: профилактика, диспансеризация,
прививки, здоровый образ жизни, бесплатная помощь. Если сайт недоступен (нет интернета
на защите), показывается последняя сохранённая подборка, а при её отсутствии — снимок
из data/news_snapshot.json. Данные пациента при этом никуда не отправляются.
"""

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "news_snapshot.json"

SOURCE = {"name": "Минздрав России", "url": "https://minzdrav.gov.ru/news",
          "feed": "https://minzdrav.gov.ru/news.atom"}
ALLOWED_HOSTS = {"minzdrav.gov.ru", "www.minzdrav.gov.ru"}

# Начала слов, по которым новость считается полезной пациенту.
# Смотрим только заголовок: в кратком тексте слово «здоровье» встречается почти в любой новости.
TOPICS = ("диспансеризац", "профилакт", "профосмотр", "скрининг", "вакцин", "привив", "грипп",
          "здоров", "питани", "курени", "табак", "давлени", "диабет", "репродуктив")

MAX_ITEMS = 8
REFRESH_SECONDS = 3600
TIMEOUT_SECONDS = 4
MAX_FEED_BYTES = 2 * 1024 * 1024
ATOM = "{http://www.w3.org/2005/Atom}"


def _text(el):
    return re.sub(r"\s+", " ", (el.text or "") if el is not None else "").strip()


def _safe_link(url):
    """Только https-ссылки на сайт Минздрава: в ленте не может оказаться чужого адреса."""
    parsed = urlparse(url or "")
    return url if parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS else None


def is_relevant(title):
    words = re.findall(r"[^\W\d_]+", title.lower().replace("ё", "е"))
    return any(w.startswith(t) for w in words for t in TOPICS)


def parse_atom(data):
    """Atom-лента → [{title, url, date, summary}], только новости на темы пациента, новые сверху."""
    root = ET.fromstring(data)
    items = []
    for entry in root.findall(f"{ATOM}entry"):
        title = _text(entry.find(f"{ATOM}title"))
        summary = _text(entry.find(f"{ATOM}content")) or _text(entry.find(f"{ATOM}summary"))
        link = entry.find(f"{ATOM}link")
        url = _safe_link(link.get("href") if link is not None else "")
        published = _text(entry.find(f"{ATOM}published")) or _text(entry.find(f"{ATOM}updated"))
        try:
            day = datetime.fromisoformat(published).date().isoformat()
        except ValueError:
            continue
        if title and url and is_relevant(title):
            items.append({"title": title, "url": url, "date": day, "summary": summary})
    items.sort(key=lambda i: i["date"], reverse=True)
    return items[:MAX_ITEMS]


def _download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "CheckUp-Track/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(MAX_FEED_BYTES + 1)[:MAX_FEED_BYTES]


class News:
    def __init__(self, cache_path, download=_download, snapshot_path=SNAPSHOT, clock=time.time):
        self.cache_path = Path(cache_path)
        self.download = download
        self.snapshot_path = Path(snapshot_path)
        self.clock = clock
        self._last_try = None
        self._live = None

    def _saved(self):
        for path in (self.cache_path, self.snapshot_path):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("items"):
                    return saved
            except (OSError, ValueError):
                continue
        return {"items": [], "updated": None}

    def get(self):
        """{items, updated, live, source}. Сайт спрашиваем не чаще раза в час."""
        now = self.clock()
        if self._last_try is None or now - self._last_try >= REFRESH_SECONDS:
            self._last_try = now
            try:
                items = parse_atom(self.download(SOURCE["feed"]))
                if items:
                    self._live = {"items": items, "updated": datetime.fromtimestamp(now).date().isoformat()}
                    self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                    self.cache_path.write_text(json.dumps(self._live, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:  # нет интернета, сайт недоступен или прислал не то
                self._live = None
        result = dict(self._live) if self._live else self._saved()
        result["live"] = bool(self._live)
        result["source"] = {"name": SOURCE["name"], "url": SOURCE["url"]}
        return result
