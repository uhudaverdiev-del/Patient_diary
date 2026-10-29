"""Лента новостей: отбор по темам пациента, безопасные ссылки и работа без интернета."""

import json
import tempfile
import unittest
from pathlib import Path

from app import App
from diary.news import News, is_relevant, parse_atom
from diary.storage import Storage

FIXTURE = (Path(__file__).parent / "fixtures" / "minzdrav_news.atom").read_bytes()


def offline(url):
    raise OSError("нет интернета")


class ParseTest(unittest.TestCase):
    def test_only_patient_topics(self):
        titles = [i["title"] for i in parse_atom(FIXTURE)]
        self.assertTrue(any("диспансеризацию" in t for t in titles))
        self.assertTrue(any("гриппа" in t for t in titles))
        self.assertFalse(any("Сенеж" in t for t in titles))
        self.assertFalse(any("Приморском" in t for t in titles))

    def test_short_word_does_not_match_city(self):
        # «Омскую область» не должна попадать в ленту из-за слова, похожего на «ОМС».
        self.assertFalse(any("Омскую" in i["title"] for i in parse_atom(FIXTURE)))
        self.assertFalse(is_relevant("Михаил Мурашко посетил Омскую область"))

    def test_newest_first_with_dates_and_links(self):
        items = parse_atom(FIXTURE)
        self.assertEqual(items[0]["date"], "2026-10-20")
        self.assertEqual([i["date"] for i in items], sorted((i["date"] for i in items), reverse=True))
        self.assertTrue(all(i["url"].startswith("https://minzdrav.gov.ru/news/") for i in items))

    def test_foreign_link_dropped(self):
        feed = FIXTURE.replace(b'href="https://minzdrav.gov.ru/news/2026/07/31/', b'href="https://evil.example/2026/07/31/')
        self.assertFalse(any("Драпкина" in i["title"] for i in parse_atom(feed)))
        feed = FIXTURE.replace(b'href="https://minzdrav.gov.ru/news/2026/07/31/', b'href="javascript:alert(1)//')
        self.assertFalse(any("Драпкина" in i["title"] for i in parse_atom(feed)))


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "news_cache.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_feed_saved_for_offline(self):
        news = News(self.cache, download=lambda url: FIXTURE, clock=lambda: 1793000000)
        result = news.get()
        self.assertTrue(result["live"])
        self.assertEqual(result["source"]["name"], "Минздрав России")
        self.assertTrue(self.cache.exists())
        later = News(self.cache, download=offline).get()
        self.assertFalse(later["live"])
        self.assertEqual(later["items"], result["items"])

    def test_offline_without_cache_uses_snapshot(self):
        result = News(self.cache, download=offline).get()
        self.assertFalse(result["live"])
        self.assertTrue(result["items"])
        self.assertTrue(result["updated"])

    def test_broken_feed_falls_back(self):
        result = News(self.cache, download=lambda url: b"<html>not a feed").get()
        self.assertFalse(result["live"])
        self.assertTrue(result["items"])

    def test_site_asked_once_an_hour(self):
        calls = []
        now = [1793000000]

        def download(url):
            calls.append(url)
            return FIXTURE
        news = News(self.cache, download=download, clock=lambda: now[0])
        news.get()
        news.get()
        self.assertEqual(len(calls), 1)
        now[0] += 3600
        news.get()
        self.assertEqual(len(calls), 2)

    def test_api(self):
        app = App(storage=Storage(self.tmp.name), news=News(self.cache, download=offline))
        code, data = app.handle("GET", "/api/news", {"today": ["2026-10-24"]}, {})
        self.assertEqual(code, 200)
        self.assertTrue(all({"title", "url", "date"} <= set(i) for i in data["items"]))


if __name__ == "__main__":
    unittest.main()
