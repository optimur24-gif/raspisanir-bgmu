"""Visible group recording notices, kept separate from journal implementation."""

import asyncio
import hashlib
import logging
import sqlite3
import time

from telegram.error import TelegramError

JOURNAL_GUIDE = (
    '📝 Журнал чата: перед началом записи бот отправляет в чат предупреждение. '
    'Новые доступные боту сообщения сохраняются на хостинге: автор, время и текст; '
    'для вложений — только обозначение и подпись, без скачивания файлов. '
    'Владелец бота может скачать журнал. Личные сообщения пользователей не записываются. '
    'Запись работает, пока бот находится в чате; чтобы прекратить её, удалите бота. '
    'Старую переписку Telegram боту не передаёт. '
    'При включённом Privacy Mode бот видит не все сообщения: для обычных сообщений '
    'нужны права администратора или отключение Privacy Mode в BotFather. '
    'Если предупреждение не удалось отправить, запись не начинается.'
)


def notice_text(owner):
    return (
        '⚠️ В этом чате ведётся журнал сообщений\n\n'
        'После этого предупреждения бот сохраняет новые доступные ему сообщения: '
        'автора, время, текст и подписи к вложениям. Фото, видео и другие файлы '
        'не скачиваются — в журнале остаются пометки. Правки записываются отдельной строкой.\n'
        f'Доступ к журналу на хостинге есть у владельца бота @{owner}. '
        'Владелец может скачать и вручную очистить журнал. '
        'Старые сообщения до предупреждения и личные сообщения боту не записываются.\n\n'
        'Журнал работает, пока бот находится в чате. '
        'Чтобы остановить запись, удалите бота из чата. '
        'Отключение рассылок и команд запись не останавливает. '
        'Telegram может передавать боту только часть сообщений в зависимости от его прав.'
    )


class NoticeObserver:
    def __init__(self, profiles, store, owner_username):
        self.profiles,self.store = profiles,store
        self.notice = notice_text(owner_username)
        if not isinstance(self.notice,str) or not self.notice.strip():
            raise ValueError('Empty journal disclosure')
        self.signature = hashlib.sha256(self.notice.encode()).hexdigest()
        self.locks = [asyncio.Lock() for _ in range(32)]
        self.retry_after = {}

    def lock(self, chat_id):
        return self.locks[abs(chat_id)%len(self.locks)]

    async def ensure_notice(self, bot, chat_id, *, rejoined=False):
        state = self.store.state(chat_id)
        if not rejoined and state:
            if not state['present']:return False
            if state['notice_id'] and state['signature']==self.signature:return True
        if time.monotonic() < self.retry_after.get(chat_id,0):return False
        try:
            sent = await bot.send_message(chat_id,self.notice,parse_mode=None)
            self.store.notified(chat_id,sent.message_id,self.signature)
        except (TelegramError,OSError,sqlite3.Error) as error:
            if len(self.retry_after)>=10000:self.retry_after.pop(next(iter(self.retry_after)))
            self.retry_after[chat_id] = time.monotonic()+60
            logging.getLogger(__name__).warning('Журнал чата %s не запущен: предупреждение не доставлено (%s)',chat_id,type(error).__name__)
            return False
        self.retry_after.pop(chat_id,None)
        try:
            await bot.pin_chat_message(chat_id,sent.message_id,disable_notification=False)
        except TelegramError:
            pass  # Pinning is best effort; the visible notice was delivered.
        return True

    async def handle(self, update, context):
        chat = update.effective_chat
        if not chat or chat.type not in ('group','supergroup'):return False
        async with self.lock(chat.id):
            return await self._handle(update,context,chat)

    async def _handle(self, update, context, chat):
        event = getattr(update,'my_chat_member',None)
        if event:
            new,old = event.new_chat_member,event.old_chat_member
            present = new.status in ('member','administrator') or (new.status=='restricted' and getattr(new,'is_member',False))
            if not present:
                self.store.removed(chat.id)
                self.retry_after.pop(chat.id,None)
                return False
            state = self.store.state(chat.id)
            rejoined = old.status in ('left','kicked') or bool(state and not state['present'])
            return await self.ensure_notice(context.bot,chat.id,rejoined=rejoined)
        message = getattr(update,'message',None) or getattr(update,'edited_message',None)
        if not message:return False
        if getattr(message,'migrate_to_chat_id',None):
            self.store.removed(chat.id)
            return False
        state = self.store.state(chat.id)
        already_disclosed = bool(state and state['present'] and state['notice_id'] and state['signature']==self.signature)
        if not await self.ensure_notice(context.bot,chat.id):return False
        joined = getattr(message,'new_chat_members',None) or ()
        if already_disclosed and any(not member.is_bot for member in joined):
            try:await context.bot.send_message(chat.id,self.notice,parse_mode=None)
            except TelegramError:pass
        return True

    async def announce_existing(self, context):
        # Existing installations must also disclose before their first recorded message.
        for chat in self.profiles.registered_chats():
            if not chat['active'] or chat['bot_status'] in ('left','kicked'):continue
            async with self.lock(chat['chat_id']):
                try:await self.ensure_notice(context.bot,chat['chat_id'])
                except (OSError,sqlite3.Error):
                    logging.getLogger(__name__).warning('Не удалось подготовить журнал чата %s',chat['chat_id'])
