"""«Солнце здоровья»: каждый луч — обследование, прививка или шаг профиля.

Луч загорается, когда шаг сделан. Считается только то, что положено сейчас:
будущие обследования (по возрасту ещё рано) и прививки «если не прививались» не входят.
"""

from datetime import date, timedelta


def _pressure_fresh(plan, today):
    for m in (plan.get("metrics") or {}).get("items", []):
        if m["kind"] == "pressure" and m["last"]:
            return date.fromisoformat(m["last"]["date"]) >= today - timedelta(days=365)
    return False


def sun_rays(plan):
    if not plan.get("ready"):
        return {"rays": [], "lit": 0, "total": 0, "all_done": False}
    today = date.fromisoformat(plan["today"])
    profile = plan.get("profile") or {}
    rays = []
    for item in plan["items"]:
        if item["upcoming"]:
            continue
        rays.append({"id": item["id"], "title": item["title"], "kind": "screening",
                     "lit": item["status"] == "actual" and not item["todo_this_year"], "mark": item["test"]})
    for item in plan.get("vaccines") or []:
        if item.get("conditional"):
            continue
        rays.append({"id": item["id"], "title": item["title"], "kind": "vaccine",
                     "lit": item["status"] == "actual", "mark": item["test"]})
    rays += [
        {"id": "profile_basic", "title": "Указаны дата рождения и пол", "kind": "profile",
         "lit": bool(profile.get("birth_date") and profile.get("sex")), "goto": "profile"},
        {"id": "profile_smoking", "title": "Указано, курите ли вы", "kind": "profile",
         "lit": bool(profile.get("smoking")), "goto": "profile"},
        {"id": "pressure", "title": "Давление измерено за последний год", "kind": "profile",
         "lit": _pressure_fresh(plan, today), "goto": "metrics"},
    ]
    lit = sum(r["lit"] for r in rays)
    return {"rays": rays, "lit": lit, "total": len(rays), "all_done": lit == len(rays)}
