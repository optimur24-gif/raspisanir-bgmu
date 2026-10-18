"""Isolate warning-module failures from schedules and fail closed for recording."""
import importlib
import logging

from .journal import JournalObserver


class JournalRuntime:
    def __init__(self, profiles, store, owner_username):
        self.profiles = profiles
        self.writer = JournalObserver(profiles,store,owner_username)
        self.notices = None
        self.last_error = object()
        try:
            module = importlib.import_module('.journal_notice',__package__)
            self.notices = module.NoticeObserver(profiles,store,owner_username)
        except Exception as error:
            self.report_error(error)
        else:
            self.report_error(None)

    def report_error(self, error):
        kind = type(error).__name__ if error is not None else None
        if kind == self.last_error:return
        self.last_error = kind
        self.profiles.set_metadata('journal_notice_error',kind)
        if kind:
            logging.getLogger(__name__).warning('Модуль предупреждения недоступен (%s). Запись чатов заблокирована; расписание продолжает работать.',kind)

    async def handle(self, update, context):
        if self.notices is None:return
        try:
            disclosed = await self.notices.handle(update,context)
        except Exception as error:
            self.report_error(error)
            return
        if disclosed is True:
            self.report_error(None)
            await self.writer.handle(update,context,disclosed=True)

    async def announce_existing(self, context):
        if self.notices is None:return
        try:
            await self.notices.announce_existing(context)
        except Exception as error:
            self.report_error(error)
