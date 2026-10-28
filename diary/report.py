"""Памятка «Что сказать терапевту»: одна страница для печати или сохранения в PDF.

Собирается из того же плана, что и интерфейс: что положено бесплатно по ОМС,
что просрочено, что уже пройдено и с каким результатом, о чём спросить врача.
"""

from datetime import date
from html import escape

CONDITIONS = {
    "mastectomy": "перенесена мастэктомия",
    "hysterectomy": "перенесена экстирпация (удаление) матки",
}
SEX = {"female": "женский", "male": "мужской"}


def _fmt(iso):
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else "—"


def _e(text):
    return escape(str(text or ""))


def _lower(title):
    """Название в середине фразы: с маленькой буквы, но аббревиатуры (ЭКГ, ЭГДС) не трогаем."""
    return title if len(title) > 1 and title[1].isupper() else title[:1].lower() + title[1:]


def doctor_questions(plan):
    """Что обсудить с терапевтом: формулировки, которые пациенту удобно прочитать вслух."""
    items = plan["items"]
    due = [i for i in items if i["status"] != "actual"]
    questions = []
    if due:
        disp = any(i["test"] == "dispanserization" for i in due)
        what = "диспансеризацию" if disp else "профилактический медицинский осмотр"
        questions.append(f"Прошу направить меня на {what} по приказу Минздрава № 404н. "
                         f"По возрасту мне положено: {'; '.join(_lower(i['title']) for i in due)}.")
    unknown = [i for i in items if i.get("needs_date")]
    if unknown:
        questions.append("Не помню, когда проходил(а): " + "; ".join(_lower(i["title"]) for i in unknown)
                         + ". Прошу проверить по медкарте.")
    profile = plan.get("profile", {})
    if profile.get("smoking") == "Курит":
        questions.append("Я курю: хочу обсудить, как бросить, и узнать свой сердечно-сосудистый риск.")
    for cond in profile.get("conditions") or []:
        if cond in CONDITIONS:
            questions.append(f"Прошу учесть: {CONDITIONS[cond]}.")
    for m in (plan.get("metrics") or {}).get("items", []):
        if m["last"] and m["last"]["high"]:
            questions.append(f"{m['title']} у меня {m['last']['text']} {m['unit']} (измерено {_fmt(m['last']['date'])}): "
                             "что с этим делать?")
    questions.append("Если что-то из анализов окажется не в норме, положен ли мне второй этап диспансеризации?")
    return questions


def work_day_note(age):
    """Ст. 185.1 ТК РФ: оплачиваемый день для диспансеризации."""
    if age >= 40:
        return "Работодатель обязан ежегодно дать 1 оплачиваемый рабочий день для диспансеризации (ст. 185.1 ТК РФ)."
    return "Работодатель обязан раз в 3 года дать 1 оплачиваемый рабочий день для диспансеризации (ст. 185.1 ТК РФ)."


def render_report(plan, test_titles):
    """HTML-страница памятки. test_titles: {код обследования: название} для истории."""
    if not plan.get("ready"):
        msg = " ".join(p["message"] for p in plan.get("problems", []))
        body = f"<h1>Памятка для терапевта</h1><p>Пока не могу составить памятку: {_e(msg)}</p>"
        return _page(body)

    today = date.fromisoformat(plan["today"])
    profile = plan.get("profile", {})
    items = plan["items"]
    overdue = [i for i in items if i["status"] == "overdue"]
    soon = [i for i in items if i["status"] == "soon"]
    no_data = [i for i in items if i["status"] == "no_data"]
    this_year = [i for i in items if i["status"] == "actual" and i["next_due"]
                 and date.fromisoformat(i["next_due"]).year <= today.year]

    def rows(chosen):
        return "".join(
            f"<tr><td><b>{_e(i['title'])}</b><br><small>{_e(i['who'])}</small></td>"
            f"<td>{_fmt(i['last_date'])}</td><td>{_fmt(i['next_due'])}</td>"
            f"<td><small>{_e(i['source']['clause'])} приказа № 404н</small></td></tr>"
            for i in chosen)

    def table(title, chosen, cls):
        if not chosen:
            return ""
        return (f"<h2 class='{cls}'>{title} ({len(chosen)})</h2><table><thead><tr><th>Обследование</th>"
                f"<th>Последний раз</th><th>Срок</th><th>Основание</th></tr></thead><tbody>{rows(chosen)}</tbody></table>")

    history = sorted(plan.get("history", []), key=lambda r: r["date"], reverse=True)
    history_html = "".join(
        f"<tr><td>{_fmt(r['date'])}</td><td>{_e(test_titles.get(r['test'], r['test']))}</td>"
        f"<td>{_e(r.get('result')) or '—'}</td></tr>" for r in history)

    who = [f"Возраст: {plan['age']}", f"пол: {SEX.get(profile.get('sex'), 'не указан')}"]
    if profile.get("smoking"):
        who.append(f"курение: {profile['smoking'].lower()}")
    body = f"""
<div class="no-print bar"><button onclick="window.print()">Распечатать или сохранить в PDF</button>
  <span>В окне печати выберите «Сохранить как PDF», чтобы отправить памятку на телефон.</span></div>
<h1>Памятка для терапевта</h1>
<p class="meta"><b>{_e(plan.get('name'))}</b> · {_e(', '.join(who))} · составлено {_fmt(plan['today'])}</p>
<div class="box"><b>Всё перечисленное входит в профилактический осмотр и диспансеризацию и проводится бесплатно
по полису ОМС</b> в поликлинике по месту прикрепления (приказ Минздрава России от 27.04.2021 № 404н).
{_e(work_day_note(plan['age']))}</div>
<h2>О чём сказать врачу</h2>
<ol>{''.join(f'<li>{_e(q)}</li>' for q in doctor_questions(plan))}</ol>
{metrics_html(plan)}
{table('Просрочено', overdue, 'overdue')}
{table('Скоро необходимо пройти', soon, 'soon')}
{table('Нет отметки о прохождении', no_data, 'no_data')}
{table(f'Ещё в {today.year} году', this_year, 'actual')}
<h2>Уже пройдено</h2>
{f'<table><thead><tr><th>Дата</th><th>Обследование</th><th>Результат</th></tr></thead><tbody>{history_html}</tbody></table>'
 if history else '<p>Отметок о пройденных обследованиях пока нет.</p>'}
<p class="foot">Памятка составлена сервисом «Дневник здоровья» по возрасту, полу и отметкам пациента.
Это не медицинское заключение: окончательный объём обследований определяет врач.</p>"""
    return _page(body)


def metrics_html(plan):
    measured = [m for m in (plan.get("metrics") or {}).get("items", []) if m["last"]]
    if not measured:
        return ""
    high = ' <span class="high">выше нормы</span>'
    rows = "".join(
        f"<tr><td>{_e(m['title'])}</td><td><b>{_e(m['last']['text'])}</b> {_e(m['unit'])}"
        f"{high if m['last']['high'] else ''}</td>"
        f"<td>{_fmt(m['last']['date'])}</td><td><small>{_e(m.get('change') or '')}</small></td></tr>"
        for m in measured)
    return (f"<h2>Мои показатели</h2><table><thead><tr><th>Показатель</th><th>Последнее значение</th>"
            f"<th>Дата</th><th>Изменение</th></tr></thead><tbody>{rows}</tbody></table>")


def _page(body):
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Памятка для терапевта</title>
<style>
  body {{ font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #1d2730; max-width: 820px; margin: 24px auto; padding: 0 16px; }}
  h1 {{ margin: 0 0 4px; font-size: 24px; }}
  h2 {{ font-size: 17px; margin: 22px 0 8px; border-bottom: 2px solid #e1e6eb; padding-bottom: 4px; }}
  h2.overdue {{ color: #c62828; }} h2.soon {{ color: #a15c00; }} h2.actual {{ color: #2e7d32; }} h2.no_data {{ color: #5d6b78; }}
  .meta {{ color: #5d6b78; margin: 0 0 12px; }}
  .box {{ background: #e8f0fd; border-radius: 8px; padding: 10px 12px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid #e1e6eb; }}
  th {{ font-size: 12px; color: #5d6b78; font-weight: 600; }}
  small {{ color: #5d6b78; }}
  .high {{ color: #c62828; font-weight: 600; font-size: 12px; }}
  ol li {{ margin: 4px 0; }}
  .foot {{ margin-top: 24px; font-size: 12px; color: #5d6b78; }}
  .bar {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; color: #5d6b78; font-size: 13px; }}
  .bar button {{ background: #1f6feb; color: #fff; border: 0; border-radius: 8px; padding: 8px 14px; font: inherit; font-weight: 600; cursor: pointer; }}
  @media print {{ .no-print {{ display: none; }} body {{ margin: 0; }} h2 {{ break-after: avoid; }} tr {{ break-inside: avoid; }} }}
</style></head><body>{body}</body></html>"""
