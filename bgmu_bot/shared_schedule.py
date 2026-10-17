"""Pass one website response to both bots without persisting timetable rows."""

import asyncio
from datetime import date
import hashlib
import json
import logging
import re
import time

from aiogram import BaseMiddleware

from .bgmu import readable_details
from .commands import schedule_request
from .presentation import today
from .source import SourceError


def normalize_group(value):
    return re.sub(r'[^\w]', '', value.casefold())


class SharedSchedule:
    group = 'П-114'

    def __init__(self, source):
        self.source = source
        self.rows = ()
        self.path = None
        self.ready = False
        self.warning = ''
        self.updated = 0
        self.generation = -1
        self.lock = asyncio.Lock()

    async def refresh(self, *, force=False):
        async with self.lock:
            if not force and self.ready and time.monotonic()-self.updated < 60 and self.generation == self.source.cache.generation:
                return
            matches = await self.source.search_groups(self.group)
            exact = [match for match in matches if normalize_group(match.labels[5]) == normalize_group(self.group)]
            if len(exact) != 1:
                raise SourceError('Группа П-114 не найдена однозначно в текущем периоде сайта.')
            path = exact[0].path + ('*',)
            payload = await self.source._schedule_data(path)
            rows = []
            seen = set()
            for event in payload['events']:
                if not event['date']:
                    continue
                try:
                    date.fromisoformat(event['date'])
                except ValueError:
                    raise SourceError('Сайт вернул неверную дату занятия П-114.') from None
                identity = str(event.get('id') or json.dumps(event, ensure_ascii=False, sort_keys=True))
                row_id = int(hashlib.sha256(identity.encode()).hexdigest()[:15], 16)
                if row_id in seen:
                    raise SourceError('Сайт вернул неоднозначные идентификаторы занятий П-114.')
                seen.add(row_id)
                subject = ' · '.join(str(event.get(key) or '') for key in ('subject', 'kind') if event.get(key)) or 'Предмет уточняется'
                extra = '\n'.join(str(event[key]) for key in ('comment', 'department') if event.get(key))
                if extra:
                    subject += '\n' + extra
                location = '\n'.join(readable_details(event['details'])) or 'Место уточняется'
                rows.append((row_id, event['date'], '–'.join(str(event.get(key) or '') for key in ('start', 'end')), subject, location))
            # Publish a complete semester at once; a failure leaves the old snapshot.
            self.rows = tuple(rows)
            self.path = path
            self.ready = True
            meta = payload.get('meta') or {}
            self.warning = 'Источник вернул сохранённую копию: возможны изменения расписания.' if meta.get('stale') else ''
            if meta.get('warning'):
                self.warning = ' '.join(filter(None, (self.warning, str(meta['warning']))))
            if payload.get('undatedRecords'):
                self.warning += ' На сайте есть записи без точной даты.'
            self.updated = time.monotonic()
            self.generation = self.source.cache.generation

    async def refresh_safely(self, *, force=False):
        try:
            await self.refresh(force=force)
            return True
        except SourceError as error:
            self.warning = 'Не удалось обновить П-114 с сайта. ' + ('Показана предыдущая копия.' if self.ready else 'Расписание пока недоступно.')
            logging.getLogger(__name__).warning('Обновление П-114 не выполнено (%s)', type(error).__name__)
            return False

    def attach(self, connection):
        # TEMP shadows the legacy on-disk table for every existing read path:
        # commands, digests, current lesson, search, statistics and CSV/ICS.
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('CREATE TEMP TABLE schedule(id INTEGER PRIMARY KEY,date TEXT,time TEXT,subject TEXT,room TEXT)')
        connection.executemany('INSERT INTO temp.schedule VALUES (?,?,?,?,?)', self.rows)
        connection.commit()
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            connection.execute(f"""CREATE TEMP TRIGGER website_schedule_{action} BEFORE {action} ON temp.schedule
                BEGIN SELECT RAISE(ABORT, 'Website schedule is read-only'); END""")


WRITE_CALLBACKS = ('add_class_', 'parse_week_', 'cls_del_', 'clear_week_', 'do_clear_week_', 'sch_edit', 'sch_copy')
SCHEDULE_CALLBACKS = ('week_', 'date_', 'menu_schedule', 'sch_', 'main_menu', 'study_plan')


class SharedScheduleMiddleware(BaseMiddleware):
    def __init__(self, feed_getter):
        self.feed_getter = feed_getter

    async def __call__(self, handler, event, data):
        feed = self.feed_getter()
        if feed is None:
            return await handler(event, data)
        callback = getattr(event, 'data', '') or ''
        if callback.startswith(WRITE_CALLBACKS):
            await event.answer('Расписание П-114 поступает с сайта через общего бота. Ручное изменение отключено.', show_alert=True)
            return
        # Cached shared responses do not add a website request per Telegram update.
        await feed.refresh_safely()
        text = getattr(event, 'text', '') or ''
        try:
            request = schedule_request(text, today())
        except ValueError:
            request = True
        related = re.match(r'(?i)^\s*(?:расписание|найти пары|аудитория|нагрузка|план|/start)', text)
        if not feed.ready and (request or callback.startswith(SCHEDULE_CALLBACKS) or related):
            if callback:
                await event.answer('Расписание П-114 пока недоступно. Попробуйте позже.', show_alert=True)
            else:
                await event.answer('⚠️ Расписание П-114 пока недоступно. Попробуйте позже.')
            return
        return await handler(event, data)
