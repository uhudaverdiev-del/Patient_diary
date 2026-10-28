import json
import unittest

from diary.engine import load_rules
from diary.wiki import WIKI_DIR, load_wiki, search


class WikiTest(unittest.TestCase):
    def setUp(self):
        self.wiki = load_wiki()

    def test_every_article_has_sources(self):
        self.assertGreaterEqual(len(self.wiki), 15)
        for a in self.wiki.values():
            self.assertTrue(a["sources"], a["slug"])
            for s in a["sources"]:
                self.assertTrue(s["url"].startswith("https://"))

    def test_every_plan_item_has_article(self):
        for rule in load_rules()["rules"]:
            self.assertIn(rule["wiki"], self.wiki, rule["id"])

    def test_all_sources_used(self):
        sources = json.loads((WIKI_DIR / "_sources.json").read_text(encoding="utf-8"))
        used = {s["id"] for a in self.wiki.values() for s in a["sources"]}
        self.assertEqual(set(sources), used)

    def test_new_articles(self):
        for slug in ("reproductive_health", "deep_dispanserization", "vaccination",
                     "dispensary_observation", "clinic_refused"):
            self.assertIn(slug, self.wiki)
            self.assertEqual(self.wiki[slug]["checked"], "2026-10-28")
        self.assertEqual(self.wiki["ecg"]["checked"], "2026-10-24")
        self.assertEqual(search(self.wiki, "отказали в поликлинике")[0]["slug"], "clinic_refused")
        self.assertEqual(search(self.wiki, "прививка от столбняка")[0]["slug"], "vaccination")

    def test_choice_of_clinic_uses_current_order(self):
        ids = {s["id"] for s in self.wiki["oms_rights"]["sources"]}
        self.assertIn("216n", ids)
        self.assertIn("323fz_21", ids)

    def test_search(self):
        self.assertEqual(search(self.wiki, "маммография")[0]["slug"], "mammography")
        self.assertEqual(search(self.wiki, "выходной на работе для диспансеризации")[0]["slug"], "how_to_pass")
        self.assertEqual(search(self.wiki, "zzz"), [])


if __name__ == "__main__":
    unittest.main()
