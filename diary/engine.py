"""Движок персонального плана профилактики.

План строится только по правилам из data/rules.json (приказ Минздрава № 404н).
Нейросеть в расчёте плана, сроков и статусов не участвует.

Статусы:
  actual  — «актуально»: обследование пройдено, следующий срок ещё не скоро;
  soon    — «скоро необходимо пройти»: срок наступил или наступит в ближайшие soon_days дней;
  overdue — «просрочено»: обследование проходили, и срок следующего уже прошёл;
  no_data — «нет данных»: отметок о прохождении нет, пройти при ближайшем визите.

Возраст для правил — тот, который исполняется в текущем календарном году
(так считает приказ № 404н), поэтому ПСА в 45 лет можно сдать в любой день этого года.
"""

import json
from datetime import date, timedelta
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "rules.json"

STATUS_LABELS = {
    "actual": "актуально",
    "soon": "скоро необходимо пройти",
    "overdue": "просрочено",
    "no_data": "нет данных",
}

STATUS_ORDER = {"overdue": 0, "soon": 1, "no_data": 2, "actual": 3}

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


def _sources_for(rule):
    """Какие обследования засчитываются вместо этого и через сколько месяцев после них снова пора.

    satisfied_by: ["ct_chest"] или [{"test": "ct_breast", "interval_months": 12}].
    """
    months = rule.get("interval_months")
    result = {rule["test"]: months}
    for extra in rule.get("satisfied_by", []):
        if isinstance(extra, dict):
            result[extra["test"]] = extra.get("interval_months", months)
        else:
            result[extra] = months
    return result


def _done_dates(history, tests, today):
    """[(дата, обследование)] для записей с датой не позже сегодняшней."""
    found = []
    for record in history:
        if record.get("test") in tests:
            d = parse_date(record.get("date"))
            if d is not None and d <= today:
                found.append((d, record["test"]))
    return found


def _last_done(history, tests, today):
    dates = [d for d, _ in _done_dates(history, tests, today)]
    return max(dates) if dates else None


def _status_for(due, today, soon_days):
    if due < today:
        return "overdue"
    if (due - today).days <= soon_days:
        return "soon"
    return "actual"


def year_start(birth, age):
    """1 января года, в котором человеку исполняется age лет.

    По приказу № 404н «годом прохождения … считается календарный год,
    в котором гражданин достигает соответствующего возраста».
    """
    return date(birth.year + age, 1, 1)


def _applies_to_sex(rule, sex):
    return rule["sex"] == "any" or rule["sex"] == sex


NO_DATA_NOTE = ("Нет отметки о прохождении. Пройдите при ближайшем визите в поликлинику "
                "или укажите дату, если уже проходили, и срок пересчитается.")


def _plan_interval_rule(rule, birth, year_age, today, history, soon_days, horizon_days,
                        test_active_now=False, continues_after=False, titles=None):
    sources = _sources_for(rule)
    done = _done_dates(history, sources, today)
    last = max((d for d, _ in done), default=None)
    lo, hi = rule["age_min"], rule.get("age_max")
    start = year_start(birth, lo)
    effective = parse_date(rule.get("effective_from"))
    if effective and effective > start:
        start = effective
    end = year_start(birth, hi + 1) if hi is not None else None  # с этого дня правило уже не действует
    notes = [rule["effective_note"]] if rule.get("effective_note") and last is None else []

    in_band = year_age >= lo and (hi is None or year_age <= hi)
    first_visit_case = rule.get("first_visit") and year_age < lo

    if not in_band and not first_visit_case:
        if year_age < lo and not test_active_now and (start - today).days <= horizon_days:
            # Обследование начнётся в ближайший год: показываем заранее.
            return {
                "last_date": last,
                "next_due": start,
                "status": _status_for(start, today, soon_days),
                "notes": [f"Станет положено в {start.year} году, когда вам исполнится {lo}."],
                "upcoming": True,
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
        return {"last_date": None, "next_due": today, "status": "no_data", "needs_date": True,
                "notes": ["Проводится при первом профилактическом осмотре.", NO_DATA_NOTE]}

    if last is not None:
        # Срок считаем от каждой подходящей записи: после КТ молочных желёз
        # маммография нужна через 12 месяцев, а после самой маммографии — через 24.
        due = max(add_months(d, sources[t]) for d, t in done)
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
        last_day, last_test = max(done)
        if last_test != rule["test"]:
            title = (titles or {}).get(last_test, last_test)
            notes.append(f"Засчитано: {title} от {last_day.strftime('%d.%m.%Y')}.")
        return {"last_date": last, "next_due": due, "status": _status_for(due, today, soon_days), "notes": notes}

    # Отметок нет: не знаем, проходил ли человек обследование, поэтому не пишем
    # «просрочено», а предлагаем пройти при ближайшем визите.
    notes.append(NO_DATA_NOTE)
    return {"last_date": None, "next_due": today, "status": "no_data", "notes": notes, "needs_date": True}


def _plan_age_rule(rule, birth, year_age, today, history, soon_days, horizon_days):
    """Обследование в определённом возрасте (ПСА, ЭГДС): в любой день календарного года."""
    ages = sorted(rule["at_ages"])
    tests = [rule["test"]] + rule.get("satisfied_by", [])
    last = _last_done(history, tests, today)
    if year_age in ages:
        year_end = date(today.year, 12, 31)
        done = last is not None and last.year == today.year
        if not done:
            return {
                "last_date": last,
                "next_due": year_end,
                "status": _status_for(year_end, today, soon_days),
                "notes": [f"Положено в год, когда исполняется {year_age}: пройдите в любой день до {year_end.strftime('%d.%m.%Y')}."],
                "todo_this_year": True,
            }
    future = [a for a in ages if a > year_age]
    done_this_year = year_age in ages and last is not None and last.year == today.year
    if not future:
        if done_this_year:
            return {"last_date": last, "next_due": None, "status": "actual",
                    "notes": ["Пройдено. По возрасту больше не требуется."]}
        return None
    nxt = year_start(birth, future[0])
    if done_this_year:
        return {"last_date": last, "next_due": nxt, "status": "actual",
                "notes": [f"Пройдено в этом году. Следующий раз — в {nxt.year} году, когда вам исполнится {future[0]}."]}
    if (nxt - today).days > horizon_days:
        return None
    return {
        "last_date": last,
        "next_due": nxt,
        "status": _status_for(nxt, today, soon_days),
        "notes": [f"Положено в {nxt.year} году, когда вам исполнится {future[0]}."],
        "upcoming": True,
    }


def _plan_once_rule(rule, year_age, today, history):
    """Прививка, которую делают один раз (курсом), если человек не болел и не прививался.

    Без отметки статус «нет данных» с пояснением, когда она нужна: решает врач.
    conditional: прививка по показаниям, в напоминания, календарь и «солнце» не входит.
    """
    lo, hi = rule["age_min"], rule.get("age_max")
    if year_age < lo or (hi is not None and year_age > hi):
        return None
    last = _last_done(history, [rule["test"]], today)
    if last is not None:
        return {"last_date": last, "next_due": None, "status": "actual", "conditional": True,
                "notes": ["Отмечено, что прививка сделана: повторять не нужно."]}
    return {"last_date": None, "next_due": None, "status": "no_data", "conditional": True, "needs_date": True,
            "notes": [rule["condition"], "Сведения о прививках есть в медкарте: спросите терапевта."]}


def _make_item(rule, planned, sources, kind):
    source = sources[rule["source"]]
    return {
        "id": rule["id"],
        "kind": kind,
        "test": rule["test"],
        "title": rule["title"],
        "who": rule.get("who") or describe_rule(rule),
        "includes": rule.get("includes"),
        "last_date": planned["last_date"].isoformat() if planned["last_date"] else None,
        "next_due": planned["next_due"].isoformat() if planned["next_due"] else None,
        "status": planned["status"],
        "status_label": ("пройти в этом году" if planned.get("todo_this_year") and planned["status"] == "actual"
                         else "если не прививались" if planned.get("conditional") and planned["status"] != "actual"
                         else STATUS_LABELS[planned["status"]]),
        "needs_date": planned.get("needs_date", False),
        "upcoming": planned.get("upcoming", False),
        "todo_this_year": planned.get("todo_this_year", False),
        "conditional": planned.get("conditional", False),
        "notes": planned["notes"],
        "source": {"title": source["title"], "url": source["url"], "clause": rule["clause"],
                   "short": source.get("short", "")},
        "wiki": rule.get("wiki"),
        "free_oms": True,
    }


def _plan_vaccines(rules_data, profile, birth, year_age, today, history, soon_days, horizon_days, titles):
    """Прививки по национальному календарю (приказ № 1122н): отдельный блок плана."""
    sex = profile.get("sex")
    conditions = set(profile.get("conditions") or [])
    result = []
    for rule in rules_data.get("vaccines", []):
        if rule["sex"] != "any" and rule["sex"] != sex:
            continue
        if rule.get("only_if") and not (conditions.intersection(rule["only_if"])
                                        or year_age >= rule.get("or_year_age_min", 999)):
            continue
        if rule.get("once"):
            planned = _plan_once_rule(rule, year_age, today, history)
        else:
            planned = _plan_interval_rule(rule, birth, year_age, today, history, soon_days, horizon_days, titles=titles)
            if planned is not None and rule.get("season_note"):
                planned["notes"].append(rule["season_note"])
        if planned is not None:
            result.append(_make_item(rule, planned, rules_data["sources"], "vaccine"))
    result.sort(key=lambda i: (i["conditional"], STATUS_ORDER[i["status"]], i["next_due"] or "9999"))
    return result


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
        "risk_factors": [],
        "vaccines": [],
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

    year_age = today.year - birth.year  # сколько исполняется в этом году

    def in_band(r):
        return "age_min" in r and r["age_min"] <= year_age and (r.get("age_max") is None or year_age <= r["age_max"])

    titles = {r["test"]: r["title"] for r in rules_data["rules"]}
    titles.update({t["test"]: t["title"] for t in rules_data.get("extra_tests", [])})
    titles.update({v["test"]: v["title"] for v in rules_data.get("vaccines", [])})
    active_tests = {r["test"] for r in rules_data["rules"] if in_band(r)}

    for rule in rules_data["rules"]:
        if rule["sex"] != "any" and sex not in ("female", "male"):
            continue
        if not _applies_to_sex(rule, sex):
            continue
        if conditions.intersection(rule.get("exclude_if", [])):
            continue
        if "at_ages" in rule:
            planned = _plan_age_rule(rule, birth, year_age, today, history, soon_days, horizon_days)
        else:
            continues_after = rule.get("age_max") is not None and any(
                r["test"] == rule["test"] and r.get("age_min") == rule["age_max"] + 1
                for r in rules_data["rules"])
            planned = _plan_interval_rule(
                rule, birth, year_age, today, history, soon_days, horizon_days,
                test_active_now=rule["test"] in active_tests and not in_band(rule),
                continues_after=continues_after, titles=titles)
        if planned is None:
            continue
        result["items"].append(_make_item(rule, planned, rules_data["sources"], "screening"))

    _merge_profosmotr(result["items"])
    order = STATUS_ORDER
    result["items"].sort(key=lambda i: (order[i["status"]], i["next_due"] or "9999"))
    counts = {s: sum(1 for i in result["items"] if i["status"] == s) for s in order}
    total = len(result["items"])
    # Прогресс считаем только по тому, что положено уже сейчас: обследования, которые
    # начнутся позже, не учитываем, а ПСА «в этом году» засчитываем, только когда сдан.
    required = [i for i in result["items"] if not i["upcoming"]]
    done = [i for i in required if i["status"] == "actual" and not i["todo_this_year"]]
    result["summary"] = {
        "total": total,
        "counts": counts,
        "required": len(required),
        "done": len(done),
        "progress_percent": round(100 * len(done) / len(required)) if required else 100,
    }
    result["vaccines"] = _plan_vaccines(rules_data, profile, birth, year_age, today, history,
                                        soon_days, horizon_days, titles)
    result["risk_factors"] = [dict(rules_data["risk_factors"][key], id=key)
                              for key in _risk_factors(profile) if key in rules_data.get("risk_factors", {})]
    return result


def _risk_factors(profile):
    return ["smoking"] if profile.get("smoking") == "Курит" else []


def _merge_profosmotr(items):
    """Профосмотр входит в диспансеризацию: если диспансеризация нужна не позже профосмотра,
    отдельной задачей профосмотр не показываем."""
    prof = next((i for i in items if i["test"] == "profosmotr"), None)
    disp = next((i for i in items if i["test"] == "dispanserization"), None)
    if not prof or not disp or not disp["next_due"]:
        return
    if prof["next_due"] and disp["next_due"] > prof["next_due"]:
        return
    items.remove(prof)
    disp["notes"].insert(0, "Включает профилактический осмотр: отдельно проходить его не нужно.")
