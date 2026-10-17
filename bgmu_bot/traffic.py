"""Bounded request cooldown; ordinary group conversation is not processed."""

from collections import OrderedDict
import time

from telegram.ext import ApplicationHandlerStop

from .commands import schedule_request
from .presentation import today


class Cooldown:
    def __init__(self, seconds=2, max_users=10000, clock=time.monotonic):
        self.seconds, self.max_users, self.clock = seconds, max_users, clock
        self.users = OrderedDict()

    def allow(self, user_id):
        now = self.clock()
        previous = self.users.get(user_id)
        if previous is not None and now-previous < self.seconds:
            return False
        self.users[user_id] = now
        self.users.move_to_end(user_id)
        while len(self.users) > self.max_users:
            self.users.popitem(last=False)
        return True


def relevant_text(text):
    if not text:
        return False
    if text.startswith('/'):
        return True
    try:
        return schedule_request(text, today()) is not None
    except ValueError:
        return True


class TrafficGuard:
    def __init__(self, is_owner, limiter=None):
        self.is_owner = is_owner
        self.limiter = limiter or Cooldown()

    async def handle(self, update, context):
        user, chat = update.effective_user, update.effective_chat
        if not user or getattr(user, 'is_bot', False) or self.is_owner(update):
            return
        message = update.effective_message
        if not update.callback_query and (not message or getattr(message, 'new_chat_members', None) or getattr(message, 'migrate_to_chat_id', None) or getattr(message, 'migrate_from_chat_id', None)):
            return
        if not update.callback_query and not (chat and chat.type == 'private') and not relevant_text(getattr(message, 'text', None)):
            return
        if self.limiter.allow(user.id):
            return
        if update.callback_query:
            await update.callback_query.answer('Подожди 2 секунды между запросами.')
        raise ApplicationHandlerStop
