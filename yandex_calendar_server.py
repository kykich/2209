"""MCP-сервер Яндекс.Календаря (CalDAV).

Отдельный MCP-сервер проекта: предоставляет инструменты для работы с
Яндекс.Календарём по протоколу CalDAV (https://caldav.yandex.ru/):
  * list_calendars — список календарей пользователя;
  * list_events    — события за период (по датам);
  * create_event   — создать событие;
  * update_event   — изменить событие (по UID);
  * delete_event   — удалить событие (по UID).

Учётные данные берутся из файла ya.txt (первая строка — логин/email,
вторая — пароль приложения с доступом к календарю). Файл НЕ коммитится.

Запуск (как stdio-подпроцесс MCP):
    python yandex_calendar_server.py

Переключение проекта на этот сервер — в rtk_app/config.py:
    MCP_SERVER_ARGS = ["yandex_calendar_server.py"]
"""

import datetime as _dt
import os

from mcp.server.mcpserver import MCPServer

# BASE_DIR — папка проекта (рядом с config.py); учитываем и запуск из др. CWD.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

mcp = MCPServer("yandex-calendar")


# --------------------------------------------------------------------------
# Учётные данные и подключение
# --------------------------------------------------------------------------
def _creds():
    """Читает логин/пароль из ya.txt. Формат: строка 1 — логин, строка 2 — пароль."""
    path = os.environ.get("YA_CRED_FILE") or os.path.join(BASE_DIR, "ya.txt")
    if not os.path.isfile(path):
        raise RuntimeError("не найден файл учётных данных Яндекс.Календаря: %s" % path)
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("ya.txt должен содержать две строки: логин и пароль приложения")
    return lines[0], lines[1]


def _client():
    """Создаёт CalDAV-клиент Яндекс.Календаря."""
    import caldav
    login, password = _creds()
    url = os.environ.get("YA_CALDAV_URL") or "https://caldav.yandex.ru/"
    return caldav.DAVClient(url=url, username=login, password=password)


def _main_calendar(client):
    """Возвращает основной (первый) календарь пользователя."""
    cals = list(client.principal().calendars())
    if not cals:
        raise RuntimeError("у пользователя нет доступных календарей")
    return cals[0]


def _parse_dt(value, default_date=None):
    """Разбирает дату/время из строки.

    Поддерживаются форматы:
      * "YYYY-MM-DD"                    — дата (без времени);
      * "YYYY-MM-DD HH:MM"             — дата и время;
      * "YYYY-MM-DDTHH:MM"             — ISO;
      * "YYYY-MM-DD HH:MM:SS"          — с секундами.
    Возвращает datetime. Если время не задано, берётся 00:00.
    """
    if value is None or value == "":
        return default_date
    s = str(value).strip().replace("T", " ")
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError("не удалось разобрать дату/время: %r" % value)


def _tzinfo():
    """Часовой пояс для отображения/создания событий (по умолчанию Europe/Moscow)."""
    name = os.environ.get("CALENDAR_TZ") or _default_tz_name()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return _dt.timezone(_dt.timedelta(hours=3))   # запасной вариант — MSK


def _default_tz_name():
    """Читает CALENDAR_TZ из config (без жёсткой зависимости от rtk_app)."""
    try:
        from rtk_app import config
        return getattr(config, "CALENDAR_TZ", "Europe/Moscow")
    except Exception:
        return "Europe/Moscow"


def _localize_str(value):
    """Приводит дату/время к строке в локальном поясе (Europe/Moscow)."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_tzinfo())
        return value.astimezone(_tzinfo()).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _fmt_event(comp=None, ev=None):
    """Формирует краткое текстовое описание события (VEVENT)."""
    summary = str(comp.get("summary") or "") if comp is not None else ""
    uid = str(comp.get("uid") or "") if comp is not None else ""
    dtstart = comp.get("dtstart").dt if (comp is not None and comp.get("dtstart")) else None
    dtend = comp.get("dtend").dt if (comp is not None and comp.get("dtend")) else None
    desc = str(comp.get("description") or "") if comp is not None else ""
    loc = str(comp.get("location") or "") if comp is not None else ""
    dtstart = _localize_str(dtstart)
    dtend = _localize_str(dtend)
    bits = ["%s — %s" % (dtstart, summary)]
    if dtend:
        bits.append("до %s" % dtend)
    if loc:
        bits.append("место: %s" % loc)
    text = ", ".join(bits)
    if desc:
        text += "\n  " + desc
    return "  UID: %s\n  %s" % (uid, text)


def _events_in_range(cal, start, end):
    """Возвращает список (Event, VEVENT-компонент) за период."""
    import icalendar
    out = []
    raw = cal.search(start=start, end=end, event=True, expand=False)
    for ev in raw:
        try:
            cal_obj = icalendar.Calendar.from_ical(str(ev.data))
        except Exception:
            continue
        for comp in cal_obj.walk("VEVENT"):
            out.append((ev, comp))
    return out


# --------------------------------------------------------------------------
# Инструменты MCP
# --------------------------------------------------------------------------
@mcp.tool()
def list_calendars() -> str:
    """Список календарей пользователя Яндекс.Календаря."""
    try:
        with _client() as client:
            cals = list(client.principal().calendars())
    except Exception as exc:
        return "Ошибка доступа к календарю: %s" % exc
    if not cals:
        return "Календарей не найдено."
    lines = []
    for i, c in enumerate(cals):
        try:
            name = c.get_display_name()
        except Exception:
            name = "календарь %d" % i
        lines.append("- %s (%s)" % (name, c.url))
    return "Календари:\n" + "\n".join(lines)


@mcp.tool()
def list_events(start: str, end: str) -> str:
    """Список событий календаря за период.

    start, end — границы периода в формате "YYYY-MM-DD" или "YYYY-MM-DD HH:MM".
    Пример: start="2025-01-01", end="2025-01-31".
    """
    try:
        s = _parse_dt(start)
        e = _parse_dt(end)
        if s is None or e is None:
            return "Укажите start и end (например, 2025-01-01 и 2025-01-31)."
        with _client() as client:
            cal = _main_calendar(client)
            events = _events_in_range(cal, s, e)
    except Exception as exc:
        return "Ошибка доступа к календарю: %s" % exc
    if not events:
        return "Событий за период %s — %s нет." % (
            s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"))
    lines = ["События %s — %s:" % (
        s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"))]
    for _ev, comp in events:
        lines.append(_fmt_event(comp))
    return "\n".join(lines)


@mcp.tool()
def create_event(summary: str, start: str, end: str,
                 description: str = "", location: str = "") -> str:
    """Создать событие в календаре.

    summary     — название;
    start, end  — начало и конец ("YYYY-MM-DD" или "YYYY-MM-DD HH:MM");
    description — описание (необязательно);
    location    — место (необязательно).
    """
    try:
        s = _parse_dt(start)
        e = _parse_dt(end)
        if s is None or e is None:
            return "Укажите start и end события."
        if not summary:
            return "Укажите название события (summary)."
        # Привязываем события без пояса к локальному (Europe/Moscow),
        # чтобы время сохранялось корректно.
        tz = _tzinfo()
        if isinstance(s, _dt.datetime) and s.tzinfo is None:
            s = s.replace(tzinfo=tz)
        if isinstance(e, _dt.datetime) and e.tzinfo is None:
            e = e.replace(tzinfo=tz)
        with _client() as client:
            cal = _main_calendar(client)
            ev = cal.save_event(
                dtstart=s, dtend=e, summary=summary,
                description=description or None,
                location=location or None,
            )
    except Exception as exc:
        return "Не удалось создать событие: %s" % exc
    return "Событие создано: «%s» (%s). UID: %s" % (
        summary, start, ev.url)


@mcp.tool()
def update_event(uid: str, summary: str = "", start: str = "",
                 end: str = "", description: str = "", location: str = "") -> str:
    """Изменить существующее событие по его UID.

    Задавайте только те поля, которые нужно изменить. Пустые поля не меняются.
    """
    try:
        import icalendar
        with _client() as client:
            cal = _main_calendar(client)
            target = None
            for ev in cal.events():
                try:
                    cal_obj = icalendar.Calendar.from_ical(str(ev.data))
                except Exception:
                    continue
                for comp in cal_obj.walk("VEVENT"):
                    if str(comp.get("uid") or "") == str(uid):
                        target = (ev, comp)
                        break
                if target:
                    break
            if not target:
                return "Событие с UID %s не найдено." % uid
            ev, comp = target
            if summary:
                comp["summary"] = summary
            if description:
                comp["description"] = description
            if location:
                comp["location"] = location
            if start:
                comp["dtstart"].dt = _parse_dt(start)
            if end:
                comp["dtend"].dt = _parse_dt(end)
            ev.data = cal_obj.to_ical().decode("utf-8")
            ev.save()
    except Exception as exc:
        return "Не удалось изменить событие: %s" % exc
    return "Событие %s изменено." % uid


@mcp.tool()
def delete_event(uid: str) -> str:
    """Удалить событие по его UID."""
    try:
        import icalendar
        with _client() as client:
            cal = _main_calendar(client)
            for ev in cal.events():
                try:
                    cal_obj = icalendar.Calendar.from_ical(str(ev.data))
                except Exception:
                    continue
                for comp in cal_obj.walk("VEVENT"):
                    if str(comp.get("uid") or "") == str(uid):
                        ev.delete()
                        return "Событие %s удалено." % uid
    except Exception as exc:
        return "Не удалось удалить событие: %s" % exc
    return "Событие с UID %s не найдено." % uid


if __name__ == "__main__":
    # Транспорт по умолчанию — stdio (как подпроцесс MCP-клиента проекта).
    mcp.run()
