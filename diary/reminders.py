"""Напоминания: список ближайших сроков и файл для календаря телефона (.ics).

Файл .ics открывается в Google, Яндекс и Apple Календаре: каждое обследование
становится событием в день срока с напоминаниями за неделю и утром в этот день.
Просроченные обследования и те, о которых нет отметок, ставятся одним событием
через неделю от сегодняшнего дня, чтобы пациент успел записаться.
"""

from datetime import date, datetime, timedelta, timezone

DUE_SOON_DAYS = 30
OVERDUE_SHIFT_DAYS = 7


def _days_left(item, today):
    return (date.fromisoformat(item["next_due"]) - today).days


def due_items(plan):
    """Обследования и обязательные прививки. Прививки «если не прививались» решает врач:
    в напоминания и календарь они не попадают."""
    return plan["items"] + [v for v in plan.get("vaccines", []) if not v.get("conditional")]


def reminders(plan, within_days=DUE_SOON_DAYS):
    """Что подходит по сроку: всё просроченное и то, что наступит в ближайшие within_days дней."""
    if not plan.get("ready"):
        return []
    today = date.fromisoformat(plan["today"])
    result = []
    for item in due_items(plan):
        if not item.get("next_due"):
            continue
        days = _days_left(item, today)
        if item["status"] in ("overdue", "no_data") or days <= within_days:
            result.append({
                "id": item["id"], "test": item["test"], "title": item["title"],
                "due": item["next_due"], "days_left": days,
                "overdue": item["status"] == "overdue",
                "no_data": item["status"] == "no_data",
                "text": _reminder_text(item, days),
            })
    result.sort(key=lambda r: (not r["overdue"], not r["no_data"], r["days_left"]))
    return result


def _reminder_text(item, days):
    due = date.fromisoformat(item["next_due"]).strftime("%d.%m.%Y")
    if item["status"] == "no_data":
        return "нет отметки о прохождении, пройдите при ближайшем визите."
    if item["status"] == "overdue" and days < 0:
        return f"просрочено с {due}, запишитесь в поликлинику."
    if days <= 0:
        return "пройти нужно уже сейчас."
    return f"через {_days_word(days)}, до {due}."


def _days_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} дня"
    return f"{n} дней"


def _tests_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} обследование"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} обследования"
    return f"{n} обследований"


def _escape(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line):
    """Строки .ics не длиннее 75 байт: длинные переносятся с пробелом в начале."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += b
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _event(uid, day, summary, description, stamp, url=None, alarm_title=None):
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}@patient-diary",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
    ]
    if url:
        lines.append(f"URL:{url}")
    lines.append("TRANSP:TRANSPARENT")
    for trigger, text in (("-P7D", "Через неделю"), ("PT9H", "Сегодня")):
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{trigger}",
                  f"DESCRIPTION:{_escape(text + ': ' + (alarm_title or summary))}", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def to_ics(plan, now=None):
    """План в формате iCalendar (RFC 5545).

    Просроченные обследования и те, о которых нет отметок, собираются в одно событие
    «Записаться в поликлинику» через неделю: их проходят за один визит на профосмотр
    или диспансеризацию.
    Остальные — отдельными событиями в день срока.
    """
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Patient Diary//RU",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Дневник здоровья"]
    if plan.get("ready"):
        today = date.fromisoformat(plan["today"])
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        items = [i for i in due_items(plan) if i.get("next_due")]
        overdue = [i for i in items if i["status"] == "overdue"]
        no_data = [i for i in items if i["status"] == "no_data"]
        if overdue or no_data:
            body = []
            if overdue:
                body.append("Срок уже наступил:")
                body += [f"• {i['title']} — срок {date.fromisoformat(i['next_due']).strftime('%d.%m.%Y')}" for i in overdue]
            if no_data:
                body.append("Нет отметки о прохождении:")
                body += [f"• {i['title']}" for i in no_data]
            body.append("Обычно обследования делаются за один визит на профосмотр или диспансеризацию. "
                        "Запишитесь к терапевту по месту прикрепления или через Госуслуги. "
                        "Бесплатно по полису ОМС.")
            lines += _event(f"overdue-{plan['today']}", today + timedelta(days=OVERDUE_SHIFT_DAYS),
                            f"Записаться в поликлинику: {_tests_word(len(overdue) + len(no_data))}",
                            "\n".join(body), stamp, alarm_title="записаться на диспансеризацию")
        for item in items:
            if item["status"] in ("overdue", "no_data"):
                continue
            description = [f"Кому положено: {item['who']}."]
            if item.get("includes"):
                description.append(f"Что входит: {item['includes']}.")
            if item.get("free_oms"):
                description.append("Бесплатно по полису ОМС в поликлинике по месту прикрепления.")
            src = item.get("source") or {}
            if src.get("clause"):
                description.append(f"Основание: {src['clause']} {src.get('short') or 'приказа № 404н'}.")
            day = max(date.fromisoformat(item["next_due"]), today)
            lines += _event(f"{item['id']}-{item['next_due']}", day, item["title"],
                            "\n".join(description), stamp, url=src.get("url"))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
