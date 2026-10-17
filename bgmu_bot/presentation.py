import calendar
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup

from .source import Schedule, Section


TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def today() -> date:
    return datetime.now(TIMEZONE).date()


def parse_date(value: str) -> date:
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError("Введите дату в формате ДД.ММ.ГГГГ, например 21.10.2026.")


def calendar_keyboard(month: date, nonce: str) -> InlineKeyboardMarkup:
    def callback(action: str, value: str = "") -> str:
        return f"{nonce}:{action}:{value}"

    rows = [[Button(MONTHS[month.month - 1] + f" {month.year}", callback_data=callback("noop"))]]
    rows.append([Button(day, callback_data=callback("noop")) for day in WEEKDAYS])
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(month.year, month.month):
        rows.append([
            Button(
                str(day) if day else "·",
                callback_data=callback("day", date(month.year, month.month, day).isoformat())
                if day else callback("noop"),
            )
            for day in week
        ])
    first = month.replace(day=1)
    previous = (first - timedelta(days=1)).replace(day=1) if first > date.min else None
    following = (first + timedelta(days=32)).replace(day=1) if first < date(9999, 12, 1) else None
    rows.append([
        Button("‹ Месяц", callback_data=callback("month", previous.isoformat()) if previous else callback("noop")),
        Button("Месяц ›", callback_data=callback("month", following.isoformat()) if following else callback("noop")),
    ])
    current = today()
    rows.append([
        Button("Сегодня", callback_data=callback("day", current.isoformat())),
        Button("Завтра", callback_data=callback("day", (current + timedelta(days=1)).isoformat())),
    ])
    rows.append([Button("← Назад", callback_data=callback("back"))])
    return InlineKeyboardMarkup(rows)


def render_schedule(schedule: Schedule) -> list[str]:
    """Keep section headings and split before Telegram's message size limit."""
    heading = f"<b>{escape(schedule.title)}</b>\n{schedule.day:%d.%m.%Y}"
    sections = list(schedule.sections) or [Section("Расписание", ("На сайте нет занятий на эту дату.",))]
    if schedule.notices:
        sections.append(Section("Информация источника", schedule.notices))

    messages: list[str] = []
    current = heading
    # The conservative limit applies before HTML escaping too: it leaves room
    # for headings and keeps each message below Telegram's UTF-16 limit.
    for section in sections:
        section_heading = f"<b>{escape(section.title)}</b>"
        for index, entry in enumerate(section.entries or ("Нет занятий.",)):
            # Escape after splitting so HTML entities are never cut in half.
            for offset in range(0, max(1, len(entry)), 450):
                piece = escape(entry[offset:offset + 450])
                prefix = "\n\n" + section_heading + "\n" if index == 0 and offset == 0 else "\n\n"
                addition = prefix + piece
                if len((current + addition).encode("utf-16-le")) // 2 > 3500:
                    messages.append(current)
                    current = heading + "\n\n" + section_heading + "\n" + piece
                else:
                    current += addition
    messages.append(current)
    return messages
