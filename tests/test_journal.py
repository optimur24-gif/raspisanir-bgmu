from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden

from bgmu_bot.journal import JournalObserver, JournalStore, JOURNAL_GUIDE
from bgmu_bot.profiles import ProfileStore
from bgmu_bot.ui import create_application
from bgmu_bot.bgmu import BGMUSource
from bgmu_bot.website import WebsiteClient
from test_admin_tools import panel, update as panel_update


@pytest.fixture
def setup(tmp_path):
    profiles = ProfileStore(tmp_path/'preferences.sqlite3')
    observer = JournalObserver(profiles,profiles.journals,'Owner')
    bot = NS(id=900,send_message=AsyncMock(return_value=NS(message_id=100)),
             pin_chat_message=AsyncMock(),send_document=AsyncMock())
    return profiles,observer,NS(bot=bot,user_data={})


def update(message_id=101, *, text='Привет', chat_id=-10, kind='supergroup', edited=False, **fields):
    user = NS(id=2,username='student',full_name='Student',is_bot=False)
    message = NS(message_id=message_id,text=text,date=datetime(2026,10,18,2,tzinfo=timezone.utc),
                 from_user=user,**fields)
    return NS(effective_chat=NS(id=chat_id,type=kind),effective_user=user,
              effective_message=message,message=None if edited else message,
              edited_message=message if edited else None,my_chat_member=None)


@pytest.mark.asyncio
async def test_notice_precedes_recording_old_edits_and_callbacks_are_excluded(setup):
    profiles,observer,ctx=setup
    await observer.handle(update(1,text='Before warning'),ctx)
    ctx.bot.send_message.assert_awaited_once()
    assert '@Owner' in ctx.bot.send_message.call_args.args[1]
    assert not profiles.journals.log_path(-10).exists()
    ctx.bot.pin_chat_message.assert_awaited_once()
    await observer.handle(update(101,text='После предупреждения\nПоддельная строка'),ctx)
    await observer.handle(update(101,text='После предупреждения\nПоддельная строка'),ctx)
    await observer.handle(update(50,text='Old edit',edited=True),ctx)
    callback=update(102,text='Callback original');callback.message=None
    await observer.handle(callback,ctx)
    await observer.handle(update(103,text='Private',kind='private',chat_id=2),ctx)
    text=profiles.journals.log_path(-10).read_text()
    assert text.count('\n')==1
    assert '07:00:00, Уфа' in text and '@student (ID 2)' in text
    assert 'После предупреждения\\nПоддельная строка' in text
    assert not any(value in text for value in ('Before warning','Old edit','Callback original','Private'))
    assert profiles.journals.info(-10)[0]==1
    ctx.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_notice_never_records_and_retry_is_throttled(setup):
    profiles,observer,ctx=setup
    ctx.bot.send_message.side_effect=Forbidden('denied')
    for mid in range(101,105):await observer.handle(update(mid,text='Must not record'),ctx)
    assert not profiles.journals.log_path(-10).exists()
    ctx.bot.send_message.assert_awaited_once()
    assert profiles.journals.state(-10) is None
    ctx.bot.send_message.side_effect=None
    observer.retry_after.clear()
    await observer.handle(update(105,text='After actual warning'),ctx)
    assert profiles.journals.info(-10)[0]==1


@pytest.mark.asyncio
async def test_attachments_and_edits_no_download_and_anonymous_sender(setup):
    profiles,observer,ctx=setup
    ctx.bot.pin_chat_message.side_effect=Forbidden('not admin')
    await observer.handle(update(101,text=None,photo=[NS(file_id='secret-file')],caption='Подпись'),ctx)
    await observer.handle(update(102,text=None,voice=NS(file_id='voice-file')),ctx)
    await observer.handle(update(103,text='Правка',edited=True,edit_date=datetime(2026,10,18,3,tzinfo=timezone.utc)),ctx)
    await observer.handle(update(104,text='Аноним',sender_chat=NS(id=-10,title='Study',username=None)),ctx)
    text=profiles.journals.log_path(-10).read_text()
    assert '[Фото] Подпись' in text and '[Голосовое]' in text
    assert '08:00:00' in text and 'правка' in text
    assert 'Study (ID -10): Аноним' in text
    assert 'secret-file' not in text and 'voice-file' not in text
    assert len(text.splitlines())==4


@pytest.mark.asyncio
async def test_removal_stops_recording_rejoin_warns_again_and_restart_persists(setup):
    profiles,observer,ctx=setup
    await observer.handle(update(101),ctx)
    event=update();event.my_chat_member=NS(new_chat_member=NS(status='left'),old_chat_member=NS(status='member'))
    await observer.handle(event,ctx)
    await observer.handle(update(102,text='After removal'),ctx)
    assert profiles.journals.info(-10)[0]==1
    ctx.bot.send_message.return_value=NS(message_id=200)
    event.my_chat_member=NS(new_chat_member=NS(status='member'),old_chat_member=NS(status='left'))
    await observer.handle(event,ctx)
    await observer.handle(update(201,text='Rejoined'),ctx)
    assert profiles.journals.info(-10)[0]==2
    reopened=ProfileStore(profiles.path)
    restarted=JournalObserver(reopened,reopened.journals,'Owner')
    ctx.bot.send_message.reset_mock()
    await restarted.handle(update(202,text='After restart'),ctx)
    ctx.bot.send_message.assert_not_awaited()
    assert reopened.journals.info(-10)[0]==3
    assert 'After removal' not in reopened.journals.log_path(-10).read_text()


@pytest.mark.asyncio
async def test_existing_chats_receive_notice_once_and_new_members_are_informed(setup):
    profiles,observer,ctx=setup
    profiles.observe_chat(-10,title='Study',status='member')
    profiles.observe_chat(-20,title='Removed',status='left')
    await observer.announce_existing(ctx);await observer.announce_existing(ctx)
    assert ctx.bot.send_message.await_count==1
    await observer.handle(update(101,text=None,new_chat_members=[NS(is_bot=False)]),ctx)
    assert ctx.bot.send_message.await_count==2
    assert not profiles.journals.log_path(-10).exists()
    assert profiles.journals.state(-20) is None


@pytest.mark.asyncio
async def test_private_owner_download_and_clear_only_cannot_disable_recording(setup):
    profiles,observer,ctx=setup
    profiles.observe_chat(-10,title='Study',status='member')
    await observer.handle(update(101,text='Delete this'),ctx)
    p=panel(profiles);await p.root(panel_update(),ctx)
    async def click(action,value='-10',username='Owner',kind='private'):
        nonce=ctx.user_data['admin_tools']['nonce']
        u=panel_update(username=username,kind=kind,data=f'a:{nonce}:{action}:{value}')
        await p.callback(u,ctx)
        return u
    await click('journal_get',username='Student')
    await click('journal_get',kind='group')
    ctx.bot.send_document.assert_not_awaited()
    viewed=await click('journal')
    assert 'Delete this' in viewed.callback_query.edit_message_text.call_args.args[0]
    await click('journal_get')
    assert ctx.bot.send_document.call_args.kwargs['chat_id']==1
    await click('journal_clear',username='Student')
    assert profiles.journals.info(-10)[0]==1
    await click('journal_clear_ask')
    assert profiles.journals.info(-10)[0]==1
    await click('journal_clear')
    assert profiles.journals.info(-10)==(0,0)
    await observer.handle(update(101,text='Delete this'),ctx)
    assert profiles.journals.info(-10)==(0,0)
    await observer.handle(update(102,text='Recording continues'),ctx)
    assert profiles.journals.info(-10)[0]==1
    await click('journals_clear_ask','')
    assert profiles.journals.info(-10)[0]==1
    await click('journals_clear','')
    assert profiles.journals.info(-10)==(0,0)
    assert profiles.journals.state(-10)['present']
    assert profiles.journals.path.stat().st_mode & 0o777 == 0o600
    assert profiles.journals.log_path(-10).stat().st_mode & 0o777 == 0o600


def test_journal_handler_runs_before_cooldown_and_disclosure_has_startup_job(setup):
    profiles,_,_=setup
    application=create_application('123456:FAKE',BGMUSource(WebsiteClient()),profiles=profiles,admin_username='Owner')
    assert isinstance(application.handlers[-12][0].callback.__self__,JournalObserver)
    assert application.job_queue.get_jobs_by_name('bgmu-journal-disclosure')
    assert 'удалите бота' in JOURNAL_GUIDE and 'Личные сообщения' in JOURNAL_GUIDE
