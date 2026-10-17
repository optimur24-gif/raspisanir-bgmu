"""Telegram navigation. User choices live only in process memory."""

import secrets
import logging
from datetime import date
import re

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters,
)

from .presentation import calendar_keyboard, parse_date, render_schedule, today
from .source import ScheduleSource, SourceError


class BotUI:
    PAGE_SIZE = 12

    def __init__(self, source: ScheduleSource):
        self.source = source

    @staticmethod
    def state(context) -> dict:
        return context.user_data.setdefault("navigation", {
            "path": (), "labels": (), "choices": (), "ready": False,
            "nonce": secrets.token_hex(4), "query": "",
        })

    @staticmethod
    async def send(update, text: str, keyboard=None, *, html=False):
        options = {"reply_markup": keyboard}
        if html:
            options["parse_mode"] = ParseMode.HTML
        if update.callback_query:
            await update.callback_query.edit_message_text(text, **options)
        else:
            await update.effective_message.reply_text(text, **options)

    async def start(self, update, context):
        context.user_data.pop("navigation", None)
        await self.show_menu(update, context)

    async def show_menu(self, update, context, *, page=0, query=""):
        state = self.state(context)
        try:
            menu = await self.source.menu(state["path"])
        except SourceError:
            await self.failure(update, context, "menu")
            return
        state["nonce"] = secrets.token_hex(4)
        state["ready"] = menu is None
        if menu is None:
            await self.show_calendar(update, context, today())
            return
        normalize = lambda text: re.sub(r"[^\w]", "", text.casefold().replace("ё", "е"))
        state["query"] = query
        state["choices"] = tuple(choice for choice in menu.choices if normalize(query) in normalize(choice.label))
        count = len(state["choices"])
        page = min(max(0, page), max(0, (count - 1) // self.PAGE_SIZE))
        first = page * self.PAGE_SIZE
        nonce = state["nonce"]
        rows = [[Button(choice.label, callback_data=f"{nonce}:pick:{index}")]
                for index, choice in enumerate(state["choices"][first:first + self.PAGE_SIZE], start=first)]
        pages = []
        if page:
            pages.append(Button("‹ Предыдущие", callback_data=f"{nonce}:page:{page - 1}"))
        if first + self.PAGE_SIZE < count:
            pages.append(Button("Следующие ›", callback_data=f"{nonce}:page:{page + 1}"))
        if pages:
            rows.append(pages)
        if query:
            rows.append([Button("Сбросить поиск", callback_data=f"{nonce}:menu:")])
        if state["path"]:
            rows.append([Button("← Назад", callback_data=f"{nonce}:back:")])
        if not state["choices"]:
            rows.append([Button("Обновить", callback_data=f"{nonce}:menu:")])
        text = menu.title
        if query:
            text += f"\nПоиск: {query}\nНайдено: {count}"
        if count > self.PAGE_SIZE:
            text += f"\nПоказано {first + 1}–{min(first + self.PAGE_SIZE, count)} из {count}"
        text += "\n\nМожно отправить часть названия для поиска."
        await self.send(update, text, InlineKeyboardMarkup(rows))

    async def show_calendar(self, update, context, month: date):
        state = self.state(context)
        state["nonce"] = secrets.token_hex(4)
        title = " → ".join(state["labels"])
        await self.send(
            update,
            f"{title}\n\nВыберите дату или отправьте её в формате ДД.ММ.ГГГГ.",
            calendar_keyboard(month, state["nonce"]),
        )

    async def show_schedule(self, update, context, day: date):
        state = self.state(context)
        try:
            schedule = await self.source.schedule(state["path"], day)
        except SourceError:
            await self.failure(update, context, "day", day.isoformat())
            return
        messages = render_schedule(schedule)
        state["nonce"] = secrets.token_hex(4)
        keyboard = InlineKeyboardMarkup([
            [Button("Обновить с сайта", callback_data=f"{state['nonce']}:day:{day.isoformat()}")],
            [Button("Выбрать дату", callback_data=f"{state['nonce']}:month:{day.isoformat()}")],
            [Button("Открыть на сайте", url=schedule.source_url)],
            [Button("← Назад к выбору", callback_data=f"{state['nonce']}:back:")],
            [Button("Изменить выбор", callback_data=f"{state['nonce']}:reset:")],
        ])
        await self.send(update, messages[0], keyboard if len(messages) == 1 else None, html=True)
        for index, message in enumerate(messages[1:], start=1):
            await update.effective_message.reply_text(
                message, parse_mode=ParseMode.HTML,
                reply_markup=keyboard if index == len(messages) - 1 else None,
            )
        # Neither the fetched schedule nor its rendered messages are put in
        # context, a database, a file, or a persistent cache.

    async def failure(self, update, context, action: str, value: str = ""):
        state = self.state(context)
        state["nonce"] = secrets.token_hex(4)
        await self.send(
            update,
            "Не удалось получить данные с сайта расписания. Попробуйте ещё раз позже.",
            InlineKeyboardMarkup([
                [Button("Повторить", callback_data=f"{state['nonce']}:{action}:{value}")],
                [Button("Начать заново", callback_data=f"{state['nonce']}:reset:")],
                [Button("Открыть сайт", url="https://bgmu-time.ru/")],
            ]),
        )

    async def callback(self, update, context):
        query = update.callback_query
        state = self.state(context)
        parts = (query.data or "").split(":", 2)
        if len(parts) != 3 or parts[0] != state["nonce"]:
            await query.answer("Это меню устарело. Откройте /start.", show_alert=True)
            return
        await query.answer()
        _, action, value = parts
        if action == "noop":
            return
        if action == "reset":
            await self.start(update, context)
        elif action == "menu":
            await self.show_menu(update, context)
        elif action == "page" and value.isdigit():
            await self.show_menu(update, context, page=int(value), query=state["query"])
        elif action == "back":
            state["path"] = state["path"][:-1]
            state["labels"] = state["labels"][:-1]
            state["ready"] = False
            await self.show_menu(update, context)
        elif action == "pick":
            if not value.isdigit() or int(value) >= len(state["choices"]) or state["ready"]:
                return
            choice = state["choices"][int(value)]
            state["path"] += (choice.key,)
            state["labels"] += (choice.label,)
            state["choices"] = ()
            await self.show_menu(update, context)
        elif action in ("day", "month") and state["ready"]:
            try:
                selected = date.fromisoformat(value)
            except ValueError:
                return
            if action == "day":
                await self.show_schedule(update, context, selected)
            else:
                await self.show_calendar(update, context, selected)

    async def text(self, update, context):
        if not self.state(context)["ready"]:
            await self.show_menu(update, context, query=update.effective_message.text.strip()[:100])
            return
        try:
            day = parse_date(update.effective_message.text)
        except ValueError as error:
            await self.send(update, str(error))
            return
        await self.show_schedule(update, context, day)


def create_application(token: str, source: ScheduleSource) -> Application:
    # Telegram API URLs contain the bot token; never enable request URL logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    ui = BotUI(source)
    builder = Application.builder().token(token)
    if hasattr(source, "aclose"):
        async def close_source(application):
            await source.aclose()
        builder.post_shutdown(close_source)
    application = builder.build()
    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler(["start", "menu"], ui.start, filters=private))
    async def help_command(update, context):
        await update.effective_message.reply_text(
            "/start — выбрать раздел, факультет, курс и группу\n"
            "/menu — начать выбор заново\n"
            "Для поиска отправьте часть названия группы, преподавателя или аудитории.\n"
            "После выбора отправьте дату ДД.ММ.ГГГГ или воспользуйтесь календарём.\n"
            "Расписание запрашивается с bgmu-time.ru при каждом обращении. Время занятий — Уфа, UTC+5."
        )
    application.add_handler(CommandHandler("help", help_command, filters=private))
    application.add_handler(CallbackQueryHandler(ui.callback))
    application.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, ui.text))

    async def on_error(update, context):
        logging.getLogger(__name__).error(
            "Ошибка обработчика Telegram (%s)", type(context.error).__name__,
        )

    application.add_error_handler(on_error)
    return application
