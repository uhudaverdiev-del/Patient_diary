"""Показатели здоровья: давление, вес, холестерин, сахар крови.

Пациент вносит их сам (дома или по результатам анализов). Дневник показывает
последнее значение, изменение и отмечает значения выше критериев факторов риска,
по которым врач оценивает их на профосмотре и диспансеризации. Это подсказка,
а не диагноз.
"""

from datetime import date

CRITERIA_SOURCE = {
    "title": "Минздрав России. Методические рекомендации по профосмотру и диспансеризации: "
             "диагностические критерии факторов риска",
    "url": "https://www.consultant.ru/document/cons_doc_LAW_388771/d6b88a61a99168d27af1194ed85d90abb35b1a55/",
}

METRICS = {
    "pressure": {
        "title": "Артериальное давление", "unit": "мм рт. ст.", "two_values": True,
        "range": (40, 300), "limit": (140, 90),
        "high": "Давление 140/90 и выше считается повышенным. Если так при повторных измерениях — обсудите с терапевтом.",
        "ok": "Ниже 140/90: не повышено.",
    },
    "weight": {
        "title": "Вес", "unit": "кг", "two_values": False, "range": (20, 400), "limit": None,
    },
    "cholesterol": {
        "title": "Общий холестерин", "unit": "ммоль/л", "two_values": False,
        "range": (1, 20), "limit": 5.0,
        "high": "Холестерин 5,0 ммоль/л и выше — фактор риска болезней сердца и сосудов. Обсудите с терапевтом.",
        "ok": "Ниже 5,0 ммоль/л: не повышен.",
    },
    "glucose": {
        "title": "Сахар крови натощак", "unit": "ммоль/л", "two_values": False,
        "range": (1, 40), "limit": 6.1,
        "high": "Сахар натощак 6,1 ммоль/л и выше (из вены) — повод сказать терапевту: нужна проверка на диабет.",
        "ok": "Ниже 6,1 ммоль/л: не повышен.",
    },
}


def _number(value, label):
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{label}: укажите число.")
    return number


def clean_metric(record, today=None):
    """Проверяет запись {kind, date, value, value2} и возвращает её в аккуратном виде."""
    kind = record.get("kind")
    if kind not in METRICS:
        raise ValueError("Неизвестный показатель.")
    spec = METRICS[kind]
    when = record.get("date")
    if not when:
        raise ValueError("Укажите дату измерения.")
    day = date.fromisoformat(when)
    if day > (today or date.today()):
        raise ValueError("Дата измерения в будущем.")
    lo, hi = spec["range"]
    value = _number(record.get("value"), "Верхнее давление" if spec["two_values"] else spec["title"])
    clean = {"kind": kind, "date": day.isoformat(), "value": value}
    if spec["two_values"]:
        clean["value2"] = _number(record.get("value2"), "Нижнее давление")
        if clean["value2"] >= value:
            raise ValueError("Нижнее давление должно быть меньше верхнего.")
    for v in (clean["value"], clean.get("value2", lo)):
        if not lo <= v <= hi:
            raise ValueError(f"{spec['title']}: значение {v:g} похоже на опечатку.")
    return clean


def _is_high(spec, m):
    limit = spec["limit"]
    if limit is None:
        return None
    if spec["two_values"]:
        return m["value"] >= limit[0] or m["value2"] >= limit[1]
    return m["value"] >= limit


def _fmt(spec, m):
    if spec["two_values"]:
        return f"{m['value']:g}/{m['value2']:g}"
    return f"{m['value']:g}".replace(".", ",")


def summarize(metrics):
    """Для каждого показателя: все значения по датам, последнее, изменение и оценка."""
    result = []
    for kind, spec in METRICS.items():
        points = sorted((m for m in metrics if m.get("kind") == kind), key=lambda m: (m["date"], m.get("id", "")))
        item = {"kind": kind, "title": spec["title"], "unit": spec["unit"], "two_values": spec["two_values"],
                "limit": spec["limit"], "points": points, "last": None}
        if points:
            last = points[-1]
            high = _is_high(spec, last)
            item["last"] = {"date": last["date"], "text": _fmt(spec, last), "high": high,
                            "note": None if high is None else spec["high"] if high else spec["ok"]}
            if len(points) > 1:
                delta = last["value"] - points[-2]["value"]
                item["change"] = f"{delta:+g}".replace(".", ",") + " к прошлому измерению"
        result.append(item)
    return {"items": result, "source": CRITERIA_SOURCE}
