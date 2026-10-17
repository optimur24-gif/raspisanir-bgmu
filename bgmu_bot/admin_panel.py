"""Owner-only broadcast drafts, recipient selection and connection registry."""

from datetime import datetime
import secrets

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup
from telegram.error import TelegramError

from .broadcasts import PREFIX
from .presentation import TIMEZONE

ADMIN_PANEL = '🛠 Админ-панель'


class AdminPanel:
    PAGE_SIZE = 8

    def __init__(self, profiles, ui):
        self.profiles, self.ui = profiles, ui

    def allowed(self, update):
        chat = getattr(update, 'effective_chat', None)
        return bool(chat and chat.type == 'private' and self.ui.is_admin(update))

    @staticmethod
    def state(context):
        return context.user_data.setdefault('admin_tools', {})

    async def deny(self, update):
        if update.callback_query:
            await update.callback_query.answer('Только для владельца бота в личке.', show_alert=True)
        else:
            await update.effective_message.reply_text('Этот раздел доступен только владельцу бота.')

    async def show(self, update, context, text, rows):
        state = self.state(context)
        state['nonce'] = secrets.token_hex(4)
        keyboard = InlineKeyboardMarkup([[Button(label, callback_data=f"a:{state['nonce']}:{action}:{value}") for label,action,value in row] for row in rows])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        else:
            await update.effective_message.reply_text(text, reply_markup=keyboard)

    async def root(self, update, context):
        if not self.allowed(update):
            await self.deny(update)
            return
        self.state(context).clear()
        stats = self.profiles.audience_counts()
        await self.show(update, context,
            f"🛠 Админ-панель\nПользователей: {stats['users']} · получают объявления: {stats['subscribed_users']}\n"
            f"Чатов: {stats['groups']} · получают объявления: {stats['subscribed_groups']}\n\n"
            'Здесь доступны только сведения о подключении бота. Журналов переписки нет.',
            [[('📣 Новая рассылка','audiences','')], [('👤 Пользователи','users','0')], [('👥 Подключённые чаты','chats','0')],
             [('🔔 Новые пользователи и чаты','alerts','')],
             [('💾 Резервные копии','backups','')],
             [('📬 История и статус рассылок','history','')], [('🧹 Очистить историю рассылок','clear_ask','')],
             [('🔄 Обновить данные','refresh',''),('🎯 Обновить курс','course','')]])

    async def alerts(self, update, context):
        rows = []
        lines = ['🔔 Уведомления владельцу в личку',
                 'О новых пользователях и впервые обнаруженных чатах. Повторный /start и смена прав не создают дубликаты.']
        for kind, label in (('users','Пользователи'),('groups','Чаты')):
            enabled = self.profiles.metadata('owner_alerts_'+kind) is not False
            lines.append(label+': '+('включены' if enabled else 'выключены'))
            rows.append([(('🔕 Выключить: ' if enabled else '🔔 Включить: ')+label,
                          'alert_set',kind+('_off' if enabled else '_on'))])
        rows.append([('← Админ-панель','root','')])
        await self.show(update,context,'\n'.join(lines),rows)

    async def backups(self, update, context):
        lines = ['💾 Последние резервные копии',
                 'Настройки всех пользователей и чатов — в одном SQLite-файле. '
                 'Расписание — сжатая копия загруженных данных кэша, до 128 ответов сайта. '
                 'Предыдущий файл заменяется после успешной записи нового.']
        rows = []
        for kind, filename, label in (('settings','settings.sqlite3','Настройки'),('schedule','schedule.json.gz','Расписание')):
            path = self.profiles.backups.directory/filename
            try:
                info = path.stat()
                when = datetime.fromtimestamp(info.st_mtime,TIMEZONE).strftime('%d.%m.%Y %H:%M')
                lines.append(f'{label}: {when}, Уфа · {info.st_size/1024:.1f} КБ')
                rows.append([('📥 Скачать: '+label,'backup_get',kind)])
            except FileNotFoundError:
                lines.append(label+': копия ещё не создана.')
        lines.append('Копия расписания появляется после загрузки расписания с сайта. '
                     'Файлы находятся на том же диске хостинга: скачай их для защиты от потери диска.')
        rows.append([('← Админ-панель','root','')])
        await self.show(update,context,'\n'.join(lines),rows)

    async def download_backup(self, update, context, kind):
        filename = {'settings':'settings.sqlite3','schedule':'schedule.json.gz'}.get(kind)
        if filename is None:
            return
        try:
            with (self.profiles.backups.directory/filename).open('rb') as saved:
                await context.bot.send_document(chat_id=update.effective_user.id,document=saved,
                    filename=filename,caption='Резервная копия: '+filename)
        except (OSError, TelegramError):
            await update.effective_message.reply_text('Не удалось отправить копию. Проверь наличие файла и повтори позже.')
        await self.backups(update,context)

    async def audiences(self, update, context):
        self.state(context).pop('draft', None)
        await self.show(update, context, '📣 Кому отправить объявление?', [
            [('Всем пользователям в личку','audience','users')],
            [('Всем подключённым группам','audience','groups')],
            [('Выбрать отдельные группы','audience','selected')], [('← Админ-панель','root','')]])

    async def select_groups(self, update, context, page=0):
        draft = self.state(context).get('draft', {})
        chosen = draft.setdefault('selected', [])
        eligible = set(self.profiles.announcement_targets('groups'))
        groups = [row for row in self.profiles.registered_chats() if row['chat_id'] in eligible]
        page = min(max(0,page), max(0,(len(groups)-1)//self.PAGE_SIZE))
        rows = []
        for group in groups[page*self.PAGE_SIZE:(page+1)*self.PAGE_SIZE]:
            label = ('✅ ' if group['chat_id'] in chosen else '▫️ ') + (group['title'] or str(group['chat_id']))[:60]
            rows.append([(label,'toggle',str(group['chat_id']))])
        pages = []
        if page:
            pages.append(('‹','select_page',str(page-1)))
        if (page+1)*self.PAGE_SIZE < len(groups):
            pages.append(('›','select_page',str(page+1)))
        if pages:
            rows.append(pages)
        rows += [[('Продолжить','compose','')], [('← Аудитории','audiences','')]]
        self.state(context)['select_page'] = page
        await self.show(update, context, f'Выбери одну или несколько групп. Выбрано: {len(chosen)}.\nОтключившие объявления чаты не показываются.', rows)

    async def compose(self, update, context):
        draft = self.state(context).get('draft')
        if not draft:
            return
        targets = self.profiles.announcement_targets(draft['audience'],draft.get('selected'))
        if not targets:
            await self.show(update, context, 'Нет доступных получателей. Пользователь должен написать боту, а группа — добавить его и разрешить объявления.', [[('← Аудитории','audiences','')]])
            return
        draft['stage'] = 'text'
        await self.show(update, context, f'Получателей: {len(targets)}.\nОтправь текст объявления одним сообщением, до 3500 символов.\nДо подтверждения ничего не отправляется.', [[('Отмена','root','')]])

    async def text(self, update, context):
        if not self.allowed(update):
            return False
        draft = self.state(context).get('draft')
        if not draft or draft.get('stage') != 'text':
            return False
        body = (update.effective_message.text or '').strip()
        if not body or len(body.encode('utf-16-le'))//2 > 3500:
            await update.effective_message.reply_text('Нужен непустой текст до 3500 символов Telegram.')
            return True
        draft.update(body=body, stage='preview')
        targets = self.profiles.announcement_targets(draft['audience'],draft.get('selected'))
        await update.effective_message.reply_text(PREFIX+body, parse_mode=None)
        labels = {'users':'все пользователи в личку','groups':'все подключённые группы','selected':'выбранные группы'}
        await self.show(update, context, f"Предпросмотр выше.\nАудитория: {labels[draft['audience']]}\nПолучателей сейчас: {len(targets)}. Отписки учитываются перед отправкой.",
            [[('✅ Подтвердить отправку','send','')], [('✏️ Изменить текст','compose','')], [('Отмена','root','')]])
        return True

    async def users(self, update, context, page=0):
        contacts = self.profiles.registered_contacts()
        page = min(max(0, page), max(0, (len(contacts)-1)//self.PAGE_SIZE))
        lines = [f'👤 Пользователи бота: {len(contacts)}',
                 'Только люди, обращавшиеся к боту в личке. Участники чатов не перечисляются.']
        access_rows = []
        for contact in contacts[page*self.PAGE_SIZE:(page+1)*self.PAGE_SIZE]:
            blocked = self.profiles.access_blocked(contact['user_id'])
            access_rows.append([(('🔓 ' if blocked else '🔒 ')+(contact['name'] or contact['username'] or str(contact['user_id']))[:45], 'access',str(contact['user_id']))])
            username = '@'+contact['username'] if contact['username'] else 'тег не установлен'
            lines += ['', (contact['name'] or 'Имя неизвестно')[:100],
                      f"{username} · ID {contact['user_id']}",
                      'Доступ к боту: '+('отключён' if blocked else 'разрешён'),
                      'Доступен: '+('да' if contact['active'] else 'нет'),
                      'Объявления: '+('включены' if contact['announcements'] else 'отключены')]
            if contact['username']:
                lines.append('https://t.me/'+contact['username'])
        if not contacts:
            lines.append('Пока нет зарегистрированных пользователей.')
        pages = []
        if page:
            pages.append(('‹','users',str(page-1)))
        if (page+1)*self.PAGE_SIZE < len(contacts):
            pages.append(('›','users',str(page+1)))
        rows = access_rows + ([pages] if pages else [])
        rows.append([('← Админ-панель','root','')])
        await self.show(update, context, '\n'.join(lines), rows)

    async def chats(self, update, context, page=0, refresh=False):
        groups = self.profiles.registered_chats()
        page = min(max(0,page),max(0,(len(groups)-1)//self.PAGE_SIZE))
        current = groups[page*self.PAGE_SIZE:(page+1)*self.PAGE_SIZE]
        if refresh:
            for group in current:
                try:
                    chat = await context.bot.get_chat(group['chat_id'])
                    member = await context.bot.get_chat_member(group['chat_id'],context.bot.id)
                    self.profiles.observe_chat(chat.id,title=chat.title,username=getattr(chat,'username',None),status=member.status)
                except TelegramError:
                    self.profiles.observe_chat(group['chat_id'],title=group['title'],username=group['username'],status='unavailable')
            current = self.profiles.registered_chats()[page*self.PAGE_SIZE:(page+1)*self.PAGE_SIZE]
        lines = ['👥 Подключения бота (без содержимого переписки)']
        status = {'administrator':'администратор','member':'участник','restricted':'ограничен','left':'вышел','kicked':'удалён','unavailable':'не удалось проверить'}
        access_rows = []
        for group in current:
            blocked = self.profiles.access_blocked(group['chat_id'])
            access_rows.append([(('🔓 ' if blocked else '🔒 ')+(group['title'] or str(group['chat_id']))[:45], 'access',str(group['chat_id']))])
            access_rows.append([('👥 Участники: '+(group['title'] or str(group['chat_id']))[:40],'members',f"{group['chat_id']},0")])
            saved = self.profiles.chat_group(group['chat_id'])
            actor = '@'+group['added_by_username'] if group['added_by_username'] else (str(group['added_by']) if group['added_by'] else 'Telegram не сообщил')
            when = datetime.fromisoformat(group['added_at']).astimezone(TIMEZONE).strftime('%d.%m %H:%M') if group['added_at'] else 'неизвестно'
            lines += ['', f"{group['title'] or 'Чат'} · {group['chat_id']}",
                      f"Статус: {status.get(group['bot_status'],'не проверено')} · {'активен' if group['active'] else 'неактивен'}",
                      f"Учебная группа: {saved.labels[5] if saved else 'не выбрана'}",
                      "Доступ к боту: "+("отключён" if blocked else "разрешён"),
                      f"Добавил: {actor} · {when}, Уфа",
                      'Объявления: '+('включены' if group['announcements'] else 'отключены')]
            if group['username']:
                lines.append('Публичная ссылка: https://t.me/'+group['username'])
            else:
                lines.append('Закрытый чат: публичной ссылки нет.')
        if not groups:
            lines.append('\nПока нет известных подключений.')
        rows = access_rows
        pages = []
        if page:
            pages.append(('‹','chats',str(page-1)))
        if (page+1)*self.PAGE_SIZE < len(groups):
            pages.append(('›','chats',str(page+1)))
        if pages:
            rows.append(pages)
        rows += [[('🔄 Проверить права этой страницы','check',str(page))], [('← Админ-панель','root','')]]
        await self.show(update, context, '\n'.join(lines), rows)

    async def members(self, update, context, chat_id, page=0, refresh=False):
        if chat_id >= 0 or not self.profiles.access_target(chat_id):
            await self.show(update,context,'Чат не найден.',[[('← Чаты','chats','0')]])
            return
        warnings = []
        if refresh:
            try:
                count = await context.bot.get_chat_member_count(chat_id)
                self.profiles.set_metadata(f'member_count:{chat_id}',count)
            except TelegramError:
                warnings.append('Telegram не разрешил получить общее число участников.')
            try:
                admins = await context.bot.get_chat_administrators(chat_id)
                for member in admins:
                    if not getattr(member,'is_anonymous',False):
                        self.profiles.observe_member(chat_id,member.user)
            except TelegramError:
                warnings.append('Список администраторов недоступен.')
        known = self.profiles.chat_member_count(chat_id)
        page = min(max(page,0),max(0,(known-1)//self.PAGE_SIZE))
        target = self.profiles.access_target(chat_id)
        total = self.profiles.metadata(f'member_count:{chat_id}')
        lines = [f'👥 Участники: {(target[0] or str(chat_id))[:128]}',
                 f'Известно боту: {known}. Всего по последней проверке Telegram: {total if total is not None else "не проверено"}.',
                 'Это неполный список: Telegram не выдаёт всех участников. '
                 'Учитываются доступные боту сообщения, события входа/выхода и проверенные администраторы.']
        for member in self.profiles.chat_members(chat_id,offset=page*self.PAGE_SIZE,limit=self.PAGE_SIZE):
            lines += ['', (member['name'] or 'Без имени')[:128],
                      ('@'+member['username'] if member['username'] else 'Тег не установлен')+f" · ID {member['user_id']}",
                      'Статус: '+('наблюдался в чате' if member['active'] else 'зафиксирован выход')]
        if not known:
            lines.append('Пока никто не зарегистрирован. Нажми «Обновить», чтобы запросить администраторов.')
        lines += warnings
        pages = []
        if page: pages.append(('‹','members',f'{chat_id},{page-1}'))
        if (page+1)*self.PAGE_SIZE < known: pages.append(('›','members',f'{chat_id},{page+1}'))
        rows = [pages] if pages else []
        rows += [[('🔄 Обновить число и администраторов','members_refresh',f'{chat_id},{page}')],
                 [('← Чаты','chats','0')]]
        await self.show(update,context,'\n'.join(lines),rows)

    async def access(self, update, context, target_id):
        target = self.profiles.access_target(target_id)
        if not target:
            await self.show(update,context,'Получатель не найден.',[[('← Админ-панель','root','')]])
            return
        if target_id == update.effective_user.id:
            await self.show(update,context,'Владелец не может отключить себе доступ.',[[('← Админ-панель','root','')]])
            return
        blocked = self.profiles.access_blocked(target_id)
        text = (f"{'Пользователь' if target_id > 0 else 'Чат'}: {target[0] or target[1] or target_id}\nID: {target_id}\n"
                f"Доступ: {'отключён' if blocked else 'разрешён'}.\n\n"
                'Отключение запрещает команды, кнопки и автоматические рассылки общего бота. '
                'Блокировка пользователя действует также в общих чатах. '
                'Настройки и привязки сохраняются; доступ можно восстановить.')
        action = 'access_allow' if blocked else 'access_deny'
        await self.show(update,context,text,
            [[('✅ Восстановить доступ' if blocked else '⛔ Подтвердить отключение',action,str(target_id))],
             [('← Пользователи' if target_id > 0 else '← Чаты','users' if target_id > 0 else 'chats','0')]])

    async def history(self, update, context):
        ids = self.profiles.recent_broadcasts(update.effective_user.id)
        self.state(context)['jobs'] = ids
        rows = []
        for index, job_id in enumerate(ids):
            report = self.profiles.broadcast_report(job_id,update.effective_user.id)
            rows.append([(f"{index+1}. {report['state']} · отправлено {report['counts'].get('sent',0)}",'report',job_id)])
        rows.append([('← Админ-панель','root','')])
        await self.show(update, context, 'Последние рассылки. Нажми для отчёта или отмены.' if ids else 'Рассылок пока нет.', rows)

    async def report(self, update, context, job_id):
        result = self.profiles.broadcast_report(job_id,update.effective_user.id)
        if not result:
            await self.history(update,context)
            return
        counts = result['counts']
        text = (f"Рассылка: {result['state']}\nОтправлено: {counts.get('sent',0)}\n"
                f"Ожидают: {counts.get('pending',0)} · отправляются: {counts.get('sending',0)}\n"
                f"Отписались/отключили: {counts.get('opted_out',0)}\nЗаблокировали: {counts.get('blocked',0)}\n"
                f"Ошибки: {counts.get('failed',0)}\nНеподтверждённая доставка: {counts.get('uncertain',0)}\n"
                'Неподтверждённые сообщения автоматически не повторяются.')
        rows = [[('🔄 Обновить отчёт','report',job_id)]]
        if result['state'] in ('queued','running'):
            rows.append([('Остановить рассылку','cancel_job',job_id)])
        rows.append([('← История','history','')])
        await self.show(update,context,text,rows)

    async def callback(self, update, context):
        if not self.allowed(update):
            await self.deny(update)
            return
        state = self.state(context)
        parts = (update.callback_query.data or '').split(':',3)
        if len(parts) != 4 or parts[1] != state.get('nonce'):
            await update.callback_query.answer('Меню устарело. Открой /admin.',show_alert=True)
            return
        _,_,action,value = parts
        await update.callback_query.answer()
        if action == 'root':
            await self.root(update,context)
        elif action == 'refresh':
            state.clear()
            await self.ui.admin_refresh(update,context)
        elif action == 'course':
            state.clear()
            await self.ui.admin_course(update,context,begin=True)
        elif action == 'backups':
            await self.backups(update,context)
        elif action == 'backup_get' and value in ('settings','schedule'):
            await self.download_backup(update,context,value)
        elif action == 'alerts':
            await self.alerts(update,context)
        elif action == 'alert_set' and value in ('users_on','users_off','groups_on','groups_off'):
            kind, setting = value.split('_')
            self.profiles.set_metadata('owner_alerts_'+kind, setting == 'on')
            await self.alerts(update,context)
        elif action == 'audiences':
            await self.audiences(update,context)
        elif action == 'audience' and value in ('users','groups','selected'):
            state['draft'] = {'audience':value,'selected':[]}
            if value == 'selected':
                await self.select_groups(update,context)
            else:
                await self.compose(update,context)
        elif action == 'toggle':
            draft = state.get('draft',{})
            if draft.get('audience') == 'selected' and value.lstrip('-').isdigit():
                chat_id = int(value)
                if chat_id in self.profiles.announcement_targets('groups'):
                    selected = draft['selected']
                    selected.remove(chat_id) if chat_id in selected else selected.append(chat_id)
                await self.select_groups(update,context,state.get('select_page',0))
        elif action == 'select_page' and value.isdigit() and state.get('draft',{}).get('audience') == 'selected':
            await self.select_groups(update,context,int(value))
        elif action == 'compose':
            await self.compose(update,context)
        elif action == 'send':
            draft = state.get('draft',{})
            if draft.get('stage') != 'preview':
                return
            targets = self.profiles.announcement_targets(draft['audience'],draft.get('selected'))
            state.pop('draft',None)
            if not targets:
                await self.show(update,context,'Нет доступных получателей.',[[('← Аудитории','audiences','')]])
                return
            job_id = self.profiles.queue_broadcast(update.effective_user.id,draft['body'],draft['audience'],targets)
            await self.report(update,context,job_id)
        elif action in ('members','members_refresh') and ',' in value:
            chat_id, page = value.split(',',1)
            if chat_id.startswith('-') and chat_id[1:].isdigit() and page.isdigit():
                await self.members(update,context,int(chat_id),int(page),refresh=action == 'members_refresh')
        elif action in ('access','access_allow','access_deny') and value.lstrip('-').isdigit():
            target_id = int(value)
            if action != 'access' and target_id != update.effective_user.id and self.profiles.access_target(target_id):
                self.profiles.set_access_blocked(target_id,action == 'access_deny',update.effective_user.id)
            await self.access(update,context,target_id)
        elif action == 'users' and value.isdigit():
            await self.users(update,context,int(value))
        elif action in ('chats','check') and value.isdigit():
            await self.chats(update,context,int(value),refresh=action=='check')
        elif action == 'history':
            await self.history(update,context)
        elif action == 'report':
            await self.report(update,context,value)
        elif action == 'cancel_job':
            self.profiles.cancel_broadcast(value,update.effective_user.id)
            await self.report(update,context,value)
        elif action == 'clear_ask':
            await self.show(update,context,'Удалить историю завершённых и отменённых рассылок, включая тексты объявлений?\nАктивные рассылки, группы, настройки и ДЗ сохранятся.',
                [[('🧹 Да, удалить историю','clear_yes','')],[('Отмена','root','')]])
        elif action == 'clear_yes':
            count = self.profiles.clear_broadcast_history(update.effective_user.id)
            await self.show(update,context,f'Удалено рассылок: {count}. Освободившиеся страницы базы будут использованы повторно.',[[('← Админ-панель','root','')]])
