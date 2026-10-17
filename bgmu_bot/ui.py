"""Personal dashboard, persistent group preferences and source navigation."""

import secrets
import logging
from datetime import date, datetime, time, timedelta
from html import escape
import os
import re

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler, MessageHandler, filters,
)

from .presentation import TIMEZONE, calendar_keyboard, parse_date, render_schedule, render_week, today
from .profiles import ProfileStore
from .refresh import morning_refresh, refresh_saved_groups
from .commands import schedule_request
from .group_chats import GroupUI, time_answer
from .notifications import scheduled_notifications
from .source import Choice, GroupUnavailable, Menu, ScheduleSource, SourceError


TODAY = "📅 Сегодня"
TOMORROW = "🌤 Завтра"
WEEK = "🗓 Неделя"
MY_GROUP = "👥 Моя группа"
BIND = "🎓 Выбрать группу"
OTHER = "🔎 Другие расписания"
ADMIN = "🔄 Обновить данные"
HOME = "🏠 Главная"
DATE = "🗓 Выбрать дату"


class BotUI:
    PAGE_SIZE = 12

    def __init__(self, source: ScheduleSource, profiles=None, admin_username="Timur0_15"):
        self.source = source
        self.profiles = profiles
        self.admin_username = admin_username.lstrip("@").casefold()

    def personal(self, update):
        return self.profiles is not None and hasattr(self.source, "current_group_start") and getattr(update, "effective_user", None) is not None

    def is_admin(self, update):
        user = getattr(update, "effective_user", None)
        return bool(self.admin_username and user and (user.username or "").casefold() == self.admin_username)

    def home_keyboard(self, update, *, selected=True):
        rows = [[TODAY, TOMORROW], [WEEK, DATE], [MY_GROUP, OTHER]] if selected else [[BIND], [OTHER]]
        if self.is_admin(update):
            rows.append([ADMIN])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True, input_field_placeholder="Номер группы или дата — например Л-101")

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
        if update.callback_query and not isinstance(keyboard, ReplyKeyboardMarkup):
            await update.callback_query.edit_message_text(text, **options)
        else:
            await update.effective_message.reply_text(text, **options)

    async def start(self, update, context):
        if self.personal(update):
            await self.home(update, context)
            return
        context.user_data.pop("navigation", None)
        await self.show_menu(update, context)

    async def home(self, update, context, notice=""):
        context.user_data.pop("navigation", None)
        self.state(context).update(screen="home", binding=False)
        saved = self.profiles.get(update.effective_user.id)
        text = "🎓 <b>Твоё расписание БГМУ</b>\n\n"
        if notice:
            text += escape(notice) + "\n\n"
        if saved:
            text += f"👥 <b>{escape(saved.labels[5])}</b>\n🏛 {escape(saved.labels[3])}\n📚 {escape(saved.labels[4])}"
            if len(saved.labels) == 7:
                text += "\n" + escape(saved.labels[6])
            text += "\n\nВыбери день — я уже помню твою группу ✨"
        else:
            text += "Давай настроим твоё расписание один раз ✨\n\n<b>Напиши номер группы</b>, например <code>Л-101</code>, или нажми «Выбрать группу».\nПотом расписание на сегодня, завтра и неделю будет в одном нажатии."
        await self.send(update, text, self.home_keyboard(update, selected=saved is not None), html=True)

    async def begin_binding(self, update, context):
        try:
            start = await self.source.current_group_start()
        except SourceError:
            await self.failure(update, context, "bind")
            return
        context.user_data.pop("navigation", None)
        state = self.state(context)
        state.update(path=start.path, labels=start.labels, binding=True, screen="binding", matches=None)
        await self.show_menu(update, context)

    async def show_group(self, update, context):
        saved = self.profiles.get(update.effective_user.id)
        if saved is None:
            await self.begin_binding(update, context)
            return
        state = self.state(context)
        state.update(screen="group", nonce=secrets.token_hex(4))
        rows = [[Button("✏️ Изменить группу", callback_data=f"{state['nonce']}:bind:")],
                [Button("🗑 Отвязать мою группу", callback_data=f"{state['nonce']}:forget:")],
                [Button(HOME, callback_data=f"{state['nonce']}:home:")]]
        text = "👥 <b>Моя группа</b>\n\n" + "\n".join(escape(value) for value in saved.labels[3:])
        text += "\n\nЭта привязка сохранится после перезапуска бота."
        await self.send(update, text, InlineKeyboardMarkup(rows), html=True)

    async def group_search(self, update, context, query):
        try:
            matches = await self.source.search_groups(query.strip()[:100])
        except SourceError:
            await self.failure(update, context, "bind")
            return
        state = self.state(context)
        state.update(matches=matches, binding=True, ready=False, screen="search")
        await self.render_matches(update, context, query=query)

    async def render_matches(self, update, context, *, page=0, query=""):
        state = self.state(context)
        choices = tuple(Choice(str(index), " · ".join((match.labels[5], match.labels[3], match.labels[4])))
                        for index, match in enumerate(state["matches"]))
        await self.render_menu(update, context, Menu("🔎 Выбери свою группу", choices), page=page, query=query)

    async def personal_selection(self, update, context):
        saved = self.profiles.get(update.effective_user.id)
        if saved is None:
            await self.begin_binding(update, context)
            return False
        try:
            match = await self.source.resolve_group(saved.path)
        except GroupUnavailable:
            await self.home(update, context, "Группа не найдена в новом учебном периоде. Выбери её заново через «Моя группа».")
            return False
        except SourceError:
            await self.failure(update, context, "home")
            return False
        if saved.path != match.path or saved.labels != match.labels:
            self.profiles.save(update.effective_user.id, match.path, match.labels)
        self.state(context).update(path=match.path, labels=match.labels, ready=True, binding=False, screen="schedule", matches=None)
        return True

    async def admin_refresh(self, update, context):
        if not self.is_admin(update):
            await self.send(update, "Эта кнопка доступна только администратору.")
            return
        await self.send(update, "🔄 Получаю свежие данные с сайта…")
        try:
            result = await refresh_saved_groups(self.source, self.profiles)
        except Exception:
            await self.send(update, "⚠️ Обновление не удалось. Предыдущие данные и привязки групп сохранены.", self.home_keyboard(update))
            return
        when = datetime.fromisoformat(result["refreshed_at"]).astimezone(TIMEZONE).strftime("%d.%m %H:%M")
        linked = ''
        if 'linked_group_refreshed' in result:
            linked = '\nП-114 передано исходному боту.' if result['linked_group_refreshed'] else '\n⚠️ П-114 для исходного бота обновить не удалось; предыдущая копия сохранена.'
        await self.send(update, f"✅ Данные обновлены · {when}, Уфа\nКаталогов: {result['catalogs']} · групп: {result['groups']}\nСтарый кэш заменён. Личные группы сохранены.{linked}\n\nУтреннее обновление: 07:00 по Уфе.", self.home_keyboard(update, selected=self.profiles.get(update.effective_user.id) is not None))

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
            if self.personal(update) and state.get("binding") and state["path"][0] == "group":
                self.profiles.save(update.effective_user.id, state["path"], state["labels"])
                await self.home(update, context, "✅ Группа сохранена! Теперь всё в одном нажатии.")
                return
            await self.show_calendar(update, context, today())
            return
        await self.render_menu(update, context, menu, page=page, query=query)

    async def render_menu(self, update, context, menu, *, page=0, query=""):
        state = self.state(context)
        state["nonce"] = secrets.token_hex(4)
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
        if self.personal(update):
            rows.append([Button(HOME, callback_data=f"{nonce}:home:")])
        text = ("🏛 " if state.get("binding") else "") + menu.title
        if query:
            text += f"\nПоиск: {query}\nНайдено: {count}"
        if count > self.PAGE_SIZE:
            text += f"\nПоказано {first + 1}–{min(first + self.PAGE_SIZE, count)} из {count}"
        text += "\n\n💬 Можно написать номер группы." if state.get("binding") else "\n\n💬 Отправь часть названия для поиска."
        await self.send(update, text, InlineKeyboardMarkup(rows))

    async def show_calendar(self, update, context, month: date):
        state = self.state(context)
        state["nonce"] = secrets.token_hex(4)
        title = " → ".join(state["labels"])
        keyboard = calendar_keyboard(month, state["nonce"])
        if self.personal(update):
            keyboard = InlineKeyboardMarkup([*keyboard.inline_keyboard[:-1], [Button(HOME, callback_data=f"{state['nonce']}:home:")]])
        await self.send(
            update,
            f"{title}\n\nВыберите дату или отправьте её в формате ДД.ММ.ГГГГ.",
            keyboard,
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
        keyboard = self.schedule_keyboard(state, day, schedule.source_url, personal=self.personal(update))
        await self.deliver_messages(update, messages, keyboard)

    def schedule_keyboard(self, state, day, source_url, *, personal=False, week=False):
        key = lambda action, value="": f"{state['nonce']}:{action}:{value}"
        current = today()
        rows = [[Button(TODAY, callback_data=key("day", current.isoformat())),
                 Button(TOMORROW, callback_data=key("day", (current + timedelta(days=1)).isoformat()))],
                [Button(WEEK, callback_data=key("week", day.isoformat())),
                 Button("📆 Другая дата", callback_data=key("month", day.isoformat()))]]
        if week and day > date.min + timedelta(days=7) and day < date.max - timedelta(days=7):
            rows.append([Button("‹ Неделя", callback_data=key("week", (day - timedelta(days=7)).isoformat())),
                         Button("Неделя ›", callback_data=key("week", (day + timedelta(days=7)).isoformat()))])
        if personal and state["path"][0] == "group":
            rows.append([Button("📌 Сохранить эту группу", callback_data=key("pin"))])
        rows.append([Button("↗ На сайте", url=source_url), Button(HOME, callback_data=key("home" if personal else "reset"))])
        if not personal:
            rows.append([Button("← Назад к выбору", callback_data=key("back"))])
        return InlineKeyboardMarkup(rows)

    async def show_week(self, update, context, day):
        state = self.state(context)
        try:
            schedules = await self.source.week(state["path"], day)
        except SourceError:
            await self.failure(update, context, "week", day.isoformat())
            return
        state["nonce"] = secrets.token_hex(4)
        keyboard = self.schedule_keyboard(state, schedules[0].day, schedules[0].source_url.replace("view=day", "view=week"), personal=self.personal(update), week=True)
        await self.deliver_messages(update, render_week(schedules), keyboard)

    async def deliver_messages(self, update, messages, keyboard):
        await self.send(update, messages[0], keyboard if len(messages) == 1 else None, html=True)
        for index, message in enumerate(messages[1:], start=1):
            await update.effective_message.reply_text(
                message, parse_mode=ParseMode.HTML,
                reply_markup=keyboard if index == len(messages) - 1 else None,
            )
        # Rendered lessons never enter Telegram user_data or the preferences DB.

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
        elif action == "home" and self.personal(update):
            await self.home(update, context)
        elif action == "bind" and self.personal(update):
            await self.begin_binding(update, context)
        elif action == "forget" and self.personal(update):
            self.profiles.delete(update.effective_user.id)
            await self.home(update, context, "Группа отвязана. Можно выбрать другую.")
        elif action == "pin" and self.personal(update) and len(state["path"]) in (6, 7) and state["path"][0] == "group":
            self.profiles.save(update.effective_user.id, state["path"], state["labels"])
            await self.home(update, context, "✅ Группа сохранена.")
        elif action == "menu":
            if state.get("matches") is not None:
                await self.begin_binding(update, context)
            else:
                await self.show_menu(update, context)
        elif action == "page" and value.isdigit():
            if state.get("matches") is not None:
                await self.render_matches(update, context, page=int(value), query=state["query"])
            else:
                await self.show_menu(update, context, page=int(value), query=state["query"])
        elif action == "back":
            if state.get("binding") and len(state["path"]) <= 3:
                await self.home(update, context)
                return
            state["path"] = state["path"][:-1]
            state["labels"] = state["labels"][:-1]
            state["ready"] = False
            await self.show_menu(update, context)
        elif action == "pick":
            if not value.isdigit() or int(value) >= len(state["choices"]) or state["ready"]:
                return
            choice = state["choices"][int(value)]
            if state.get("matches") is not None:
                match = state["matches"][int(choice.key)]
                state.update(path=match.path, labels=match.labels, matches=None)
            else:
                state["path"] += (choice.key,)
                state["labels"] += (choice.label,)
            state["choices"] = ()
            await self.show_menu(update, context)
        elif action in ("day", "month", "week") and state["ready"]:
            try:
                selected = date.fromisoformat(value)
            except ValueError:
                return
            if action == "day":
                await self.show_schedule(update, context, selected)
            elif action == "week":
                await self.show_week(update, context, selected)
            else:
                await self.show_calendar(update, context, selected)

    async def text(self, update, context):
        text = update.effective_message.text.strip()
        if self.personal(update):
            if text == ADMIN:
                await self.admin_refresh(update, context)
                return
            if text == HOME:
                await self.home(update, context)
                return
            if text == BIND:
                await self.begin_binding(update, context)
                return
            if text == MY_GROUP:
                await self.show_group(update, context)
                return
            if text == OTHER:
                context.user_data.pop("navigation", None)
                self.state(context).update(screen="advanced", binding=False, matches=None)
                await self.show_menu(update, context)
                return
            try:
                request = schedule_request(text, today())
            except ValueError as error:
                await self.send(update, str(error))
                return
            if request is not None:
                if await self.personal_selection(update, context):
                    if request.kind == 'week':
                        await self.show_week(update, context, request.day)
                    elif request.kind == 'day':
                        await self.show_schedule(update, context, request.day)
                    else:
                        try:
                            text = await time_answer(self.source, self.state(context)['path'], request.kind, datetime.now(TIMEZONE))
                        except SourceError:
                            await self.failure(update, context, 'home')
                            return
                        await self.send(update, text, html=True)
                return
            if text in (TODAY, TOMORROW, WEEK, DATE, "Сегодня", "Завтра", "Неделя"):
                if await self.personal_selection(update, context):
                    day = today() + timedelta(days=1 if text in (TOMORROW, "Завтра") else 0)
                    if text in (WEEK, "Неделя"):
                        await self.show_week(update, context, day)
                    elif text == DATE:
                        await self.show_calendar(update, context, day)
                    else:
                        await self.show_schedule(update, context, day)
                return
            state = self.state(context)
            if state.get("screen") in ("home", "group", "search") or state.get("binding"):
                try:
                    requested_day = parse_date(text)
                except ValueError as error:
                    if re.fullmatch(r"(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{1,2}-\d{1,2})", text):
                        await self.send(update, str(error))
                    else:
                        await self.group_search(update, context, text)
                else:
                    if await self.personal_selection(update, context):
                        await self.show_schedule(update, context, requested_day)
                return
        if not self.state(context)["ready"]:
            await self.show_menu(update, context, query=update.effective_message.text.strip()[:100])
            return
        try:
            day = parse_date(update.effective_message.text)
        except ValueError as error:
            await self.send(update, str(error))
            return
        await self.show_schedule(update, context, day)


def create_application(token: str, source: ScheduleSource, *, profiles=None, admin_username=None) -> Application:
    # Telegram API URLs contain the bot token; never enable request URL logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    personal_source = hasattr(source, "current_group_start")
    if personal_source:
        profiles = profiles if profiles is not None else ProfileStore.from_environment()
        if source.cache.ttl <= 0:
            source.cache.ttl = 300
    ui = BotUI(source, profiles, admin_username if admin_username is not None else os.environ.get("ADMIN_USERNAME", "Timur0_15"))
    builder = Application.builder().token(token)
    if hasattr(source, "aclose"):
        async def close_source(application):
            await source.aclose()
        builder.post_shutdown(close_source)
    application = builder.build()
    if personal_source:
        application.job_queue.run_daily(
            morning_refresh, time(hour=7, tzinfo=TIMEZONE),
            data=(source, profiles), name="bgmu-morning-refresh",
            job_kwargs={"misfire_grace_time": 3600, "coalesce": True},
        )
        application.job_queue.run_repeating(
            scheduled_notifications, interval=60, first=10, data=(source, profiles),
            name='bgmu-group-notifications', job_kwargs={'max_instances': 1, 'coalesce': True},
        )
    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler(["start", "menu"], ui.start, filters=private))
    if personal_source:
        application.add_handler(CommandHandler("group", ui.begin_binding, filters=private))
        application.add_handler(CommandHandler("refresh", ui.admin_refresh, filters=private))
    async def help_command(update, context):
        await update.effective_message.reply_text(
            "🎓 Напиши номер группы или нажми «Выбрать группу» — я сохраню твой выбор.\n"
            "📅 Сегодня · 🌤 Завтра · 🗓 Неделя — расписание в одном нажатии.\n"
            "«Моя группа» — изменить или отвязать группу; /group — новый выбор.\n"
            "«Другие расписания» — преподаватели, аудитории и другие периоды.\n"
            "Можно отправить дату ДД.ММ.ГГГГ или открыть календарь.\n"
            "/start и /menu — главная; /refresh — обновление для администратора.\n"
            "Источник: bgmu-time.ru. Кэш в памяти — 5 минут; обновление в 07:00 по Уфе (UTC+5)."
        )
    application.add_handler(CommandHandler("help", help_command, filters=private))
    if personal_source:
        groups = GroupUI(source, profiles)
        group_filter = filters.ChatType.GROUPS
        application.add_handler(ChatMemberHandler(groups.joined, ChatMemberHandler.MY_CHAT_MEMBER))
        application.add_handler(MessageHandler(group_filter & filters.StatusUpdate.NEW_CHAT_MEMBERS, groups.joined))
        application.add_handler(MessageHandler(group_filter & filters.StatusUpdate.MIGRATE, groups.migrate))
        application.add_handler(CommandHandler(['group', 'setgroup'], groups.configure, filters=group_filter))
        application.add_handler(CommandHandler(['start', 'help'], groups.help, filters=group_filter))
        application.add_handler(CommandHandler(['notifications_on', 'notifications_off'], groups.notifications, filters=group_filter))
        application.add_handler(MessageHandler(group_filter & filters.TEXT, groups.text))

        async def callback(update, context):
            chat = update.effective_chat
            if chat and chat.type in ('group', 'supergroup'):
                if (update.callback_query.data or '').startswith('g:'):
                    await groups.callback(update, context)
                else:
                    await update.callback_query.answer('Личные кнопки работают в личке. Для чата используй /group.', show_alert=True)
            else:
                await ui.callback(update, context)

        application.add_handler(CallbackQueryHandler(callback))
    else:
        application.add_handler(CallbackQueryHandler(ui.callback))
    application.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, ui.text))

    async def on_error(update, context):
        logging.getLogger(__name__).error(
            "Ошибка обработчика Telegram (%s)", type(context.error).__name__,
        )

    application.add_error_handler(on_error)
    return application
