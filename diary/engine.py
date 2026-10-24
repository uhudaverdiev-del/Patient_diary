"""Движок персонального плана профилактики.

План строится только по правилам из data/rules.json (приказ Минздрава № 404н).
Нейросеть в расчёте плана, сроков и статусов не участвует.

Статусы:
  actual  — «актуально»: обследование пройдено, следующий срок ещё не скоро;
  soon    — «скоро необходимо пройти»: срок наступил или наступит в ближайшие soon_days дней;
  overdue — «просрочено»: срок прошёл.
"""

import json
from datetime import date, timedelta
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "rules.json"

STATUS_LABELS = {
    "actual": "актуально",
    "soon": "скоро необходимо пройти",
    "overdue": "просрочено",
}

FIRST_VISIT_TESTS = ["profosmotr", "dispanserization"]

SEX_LABELS = {"female": "женщинам", "male": "мужчинам", "any": "всем"}


def load_rules(path=RULES_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year, month):
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def birthday_at(birth, age):
    """Дата, когда человеку исполняется age лет (29 февраля → 28 февраля)."""
    year = birth.year + age
    day = min(birth.day, _days_in_month(year, birth.month))
    return date(year, birth.month, day)


def age_on(birth, on):
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def _interval_text(months):
    if months % 12 == 0:
        years = months // 12
        if years == 1:
            return "1 раз в год"
        return f"1 раз в {years} {'года' if years in (2, 3, 4) else 'лет'}"
    return f"1 раз в {months} мес."


def _age_text(rule):
    if "at_ages" in rule:
        ages = rule["at_ages"]
        if len(ages) == 1:
            return f"в {ages[0]} лет"
        return "в " + ", ".join(str(a) for a in ages[:-1]) + f" и {ages[-1]} лет"
    lo, hi = rule.get("age_min"), rule.get("age_max")
    if hi is None:
        return f"с {lo} лет"
    return f"{lo}–{hi} лет"


def describe_rule(rule):
    """Короткое объяснение, кому и как часто положено обследование."""
    who = SEX_LABELS[rule["sex"]]
    text = f"{who} {_age_text(rule)}"
    if "interval_months" in rule:
        text += ", " + _interval_text(rule["interval_months"])
    if rule.get("first_visit"):
        text += "; также при первом профилактическом осмотре"
    return text


def check_profile(profile, today):
    """Проверка профиля. Возвращает (возраст или None, список проблем).

    Каждая проблема: {"field", "level": "blocking" | "warning", "message"}.
    """
    problems = []
    birth = None
    try:
        birth = parse_date(profile.get("birth_date"))
    except ValueError:
        problems.append({
            "field": "birth_date",
            "level": "blocking",
            "message": "Дата рождения записана в непонятном формате. Укажите её как ГГГГ-ММ-ДД.",
        })
    if birth is None and not problems:
        problems.append({
            "field": "birth_date",
            "level": "blocking",
            "message": "Укажите дату рождения: от возраста зависит, какие обследования и как часто вам положены.",
        })
    age = None
    if birth is not None:
        if birth > today:
            problems.append({
                "field": "birth_date",
                "level": "blocking",
                "message": "Дата рождения в будущем. Проверьте, пожалуйста, год.",
            })
        else:
            age = age_on(birth, today)
            if age < 18:
                problems.append({
                    "field": "birth_date",
                    "level": "blocking",
                    "message": "Сервис составляет план для взрослых с 18 лет. Профилактические осмотры детей проводятся по другому порядку.",
                })
            elif age > 120:
                problems.append({
                    "field": "birth_date",
                    "level": "blocking",
                    "message": "Возраст больше 120 лет. Проверьте, пожалуйста, год рождения.",
                })
    if profile.get("sex") not in ("female", "male"):
        problems.append({
            "field": "sex",
            "level": "warning",
            "message": "Укажите пол. Без него в план попадут только общие обследования, а скрининги для женщин и мужчин (маммография, мазок, ПСА) не будут показаны.",
        })
    return age, problems


def _last_done(history, tests, today):
    dates = []
    for record in history:
        if record.get("test") in tests:
            d = parse_date(record.get("date"))
            if d is not None and d <= today:
                dates.append(d)
    return max(dates) if dates else None


def _status_for(due, today, soon_days):
    if due < today:
        return "overdue"
    if (due - today).days <= soon_days:
        return "soon"
    return "actual"


def _applies_to_sex(rule, sex):
    return rule["sex"] == "any" or rule["sex"] == sex


def _plan_interval_rule(rule, birth, age, today, history, soon_days, horizon_days,
                        test_active_now=False, continues_after=False):
    tests = [rule["test"]] + rule.get("satisfied_by", [])
    last = _last_done(history, tests, today)
    lo, hi = rule["age_min"], rule.get("age_max")
    start = birthday_at(birth, lo)
    end = birthday_at(birth, hi + 1) if hi is not None else None  # первый день, когда правило уже не действует
    notes = []

    in_band = age >= lo and (hi is None or age <= hi)
    first_visit_case = rule.get("first_visit") and age < lo

    if not in_band and not first_visit_case:
        if age < lo and not test_active_now and (start - today).days <= horizon_days:
            # Обследование начнётся в ближайший год: показываем заранее.
            return {
                "last_date": last,
                "next_due": start,
                "status": _status_for(start, today, soon_days),
                "notes": [f"Станет положено с {lo} лет."],
            }
        return None

    if first_visit_case:
        # Это обследование входит в первый профилактический осмотр, поэтому
        # пройденный осмотр или диспансеризация тоже засчитываются.
        if last is None:
            last = _last_done(history, FIRST_VISIT_TESTS, today)
        if last is not None:
            # При первом осмотре уже сделано; дальше — регулярно с возраста lo.
            if (start - today).days > horizon_days:
                return None
            return {
                "last_date": last,
                "next_due": start,
                "status": _status_for(start, today, soon_days),
                "notes": [f"Регулярно с {lo} лет."],
            }
        notes.append("Проводится при первом профилактическом осмотре. Если уже делали, укажите дату.")
        return {"last_date": None, "next_due": today, "status": "soon", "notes": notes, "needs_date": True}

    if last is not None:
        due = add_months(last, rule["interval_months"])
        if end is not None and due >= end and continues_after:
            # Со следующего возраста действует другое правило с тем же обследованием
            # (например, диспансеризация ежегодно с 40 лет).
            due = end
        elif end is not None and due >= end:
            return {
                "last_date": last,
                "next_due": None,
                "status": "actual",
                "notes": [f"По возрасту это обследование больше не требуется после {hi} лет."],
            }
        return {"last_date": last, "next_due": due, "status": _status_for(due, today, soon_days), "notes": notes}

    # Нет данных о прохождении. Если с момента, когда обследование стало
    # положено, прошёл целый интервал, — по нормативам оно уже просрочено.
    deadline = add_months(start, rule["interval_months"])
    notes.append("Нет данных о прохождении. Если проходили, укажите дату, и срок пересчитается.")
    if deadline < today:
        notes.insert(0, f"По нормативам его нужно было пройти до {deadline.strftime('%d.%m.%Y')}. Пройдите при ближайшем визите.")
        return {"last_date": None, "next_due": today, "status": "overdue", "notes": notes, "needs_date": True}
    return {"last_date": None, "next_due": today, "status": "soon", "notes": notes, "needs_date": True}


def _plan_age_rule(rule, birth, age, today, history, soon_days, horizon_days):
    ages = sorted(rule["at_ages"])
    tests = [rule["test"]] + rule.get("satisfied_by", [])
    if age in ages:
        window_start = birthday_at(birth, age)
        window_end = birthday_at(birth, age + 1) - timedelta(days=1)
        done = [r for r in history if r.get("test") in tests
                and parse_date(r.get("date")) and window_start <= parse_date(r["date"]) <= today]
        if not done:
            return {
                "last_date": _last_done(history, tests, today),
                "next_due": window_end,
                "status": "soon",
                "notes": [f"Положено в {age} лет: пройдите до {window_end.strftime('%d.%m.%Y')}."],
            }
    future = [a for a in ages if a > age]
    if not future:
        return None
    nxt = birthday_at(birth, future[0])
    if (nxt - today).days > horizon_days:
        return None
    return {
        "last_date": _last_done(history, tests, today),
        "next_due": nxt,
        "status": _status_for(nxt, today, soon_days),
        "notes": [f"Положено в {future[0]} лет."],
    }


def build_plan(profile, history=None, today=None, rules_data=None, horizon_days=365):
    """Строит персональный план.

    profile: {"birth_date": "ГГГГ-ММ-ДД", "sex": "female"|"male", "conditions": [...]}
    history: [{"test": "mammography", "date": "ГГГГ-ММ-ДД", ...}, ...]
    """
    today = parse_date(today) or date.today()
    history = history or []
    rules_data = rules_data or load_rules()
    soon_days = rules_data.get("soon_days", 60)

    age, problems = check_profile(profile, today)
    result = {
        "today": today.isoformat(),
        "age": age,
        "ready": not any(p["level"] == "blocking" for p in problems),
        "problems": problems,
        "items": [],
    }
    if not result["ready"]:
        return result

    for record in history:
        d = parse_date(record.get("date"))
        if d is not None and d > today:
            problems.append({
                "field": "history",
                "level": "warning",
                "message": f"Дата {d.strftime('%d.%m.%Y')} у записи «{record.get('test')}» в будущем, она не учитывается.",
            })

    birth = parse_date(profile["birth_date"])
    sex = profile.get("sex")
    conditions = set(profile.get("conditions") or [])

    def in_band(r):
        return "age_min" in r and r["age_min"] <= age and (r.get("age_max") is None or age <= r["age_max"])

    active_tests = {r["test"] for r in rules_data["rules"] if in_band(r)}

    for rule in rules_data["rules"]:
        if rule["sex"] != "any" and sex not in ("female", "male"):
            continue
        if not _applies_to_sex(rule, sex):
            continue
        if conditions.intersection(rule.get("exclude_if", [])):
            continue
        if "at_ages" in rule:
            planned = _plan_age_rule(rule, birth, age, today, history, soon_days, horizon_days)
        else:
            continues_after = rule.get("age_max") is not None and any(
                r["test"] == rule["test"] and r.get("age_min") == rule["age_max"] + 1
                for r in rules_data["rules"])
            planned = _plan_interval_rule(
                rule, birth, age, today, history, soon_days, horizon_days,
                test_active_now=rule["test"] in active_tests and not in_band(rule),
                continues_after=continues_after)
        if planned is None:
            continue
        source = rules_data["sources"][rule["source"]]
        result["items"].append({
            "id": rule["id"],
            "test": rule["test"],
            "title": rule["title"],
            "who": describe_rule(rule),
            "includes": rule.get("includes"),
            "last_date": planned["last_date"].isoformat() if planned["last_date"] else None,
            "next_due": planned["next_due"].isoformat() if planned["next_due"] else None,
            "status": planned["status"],
            "status_label": STATUS_LABELS[planned["status"]],
            "needs_date": planned.get("needs_date", False),
            "notes": planned["notes"],
            "source": {"title": source["title"], "url": source["url"], "clause": rule["clause"]},
            "wiki": rule.get("wiki"),
            "free_oms": True,
        })

    order = {"overdue": 0, "soon": 1, "actual": 2}
    result["items"].sort(key=lambda i: (order[i["status"]], i["next_due"] or "9999"))
    counts = {s: sum(1 for i in result["items"] if i["status"] == s) for s in order}
    total = len(result["items"])
    result["summary"] = {
        "total": total,
        "counts": counts,
        "progress_percent": round(100 * counts["actual"] / total) if total else 0,
    }
    return result
