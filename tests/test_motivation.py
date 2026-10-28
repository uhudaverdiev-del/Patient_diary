"""«Солнце здоровья»: какие лучи горят и что в них не попадает."""

import tempfile
import unittest

from app import App
from diary.motivation import sun_rays
from diary.storage import Storage

TODAY = {"today": ["2026-10-24"]}


def demo_sun(name):
    app = App(storage=Storage(tempfile.mkdtemp()))
    return app.handle("POST", "/api/demo", TODAY, {"id": name})[1]["sun"]


class SunTest(unittest.TestCase):
    def test_mariya_all_done(self):
        sun = demo_sun("mariya")
        self.assertTrue(sun["all_done"])
        self.assertEqual(sun["lit"], sun["total"])
        # условные прививки (гепатит B, корь, краснуха) лучами не считаются
        self.assertEqual([r["mark"] for r in sun["rays"] if r["kind"] == "vaccine"], ["vac_diphtheria"])

    def test_anna_unlit_rays_lead_somewhere(self):
        sun = demo_sun("anna")
        self.assertFalse(sun["all_done"])
        unlit = [r for r in sun["rays"] if not r["lit"]]
        self.assertTrue(unlit)
        for ray in unlit:
            self.assertTrue(ray.get("mark") or ray.get("goto") in ("profile", "metrics"), ray)
        titles = {r["title"]: r["lit"] for r in sun["rays"]}
        self.assertFalse(titles["Прививка от дифтерии и столбняка"])
        self.assertTrue(titles["Указано, курите ли вы"])

    def test_petr_old_pressure_not_counted(self):
        rays = {r["id"]: r for r in demo_sun("petr")["rays"]}
        self.assertFalse(rays["pressure"]["lit"])
        self.assertFalse(rays["profile_smoking"]["lit"])

    def test_not_ready_plan(self):
        self.assertEqual(sun_rays({"ready": False})["total"], 0)
