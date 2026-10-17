"""Connection metadata only. Message contents are never archived."""

import re


class RegistryObserver:
    def __init__(self, profiles):
        self.profiles = profiles

    async def handle(self, update, context):
        event = getattr(update, 'my_chat_member', None)
        if event:
            chat, new, old = event.chat, event.new_chat_member, event.old_chat_member
            present = new.status in ('member','administrator') or (new.status == 'restricted' and getattr(new,'is_member',False))
            if chat.type == 'private':
                self.profiles.contact_active(chat.id, present)
            elif chat.type in ('group','supergroup'):
                actor = event.from_user if old.status in ('left','kicked') and present else None
                self.profiles.observe_chat(chat.id, title=chat.title, username=getattr(chat,'username',None), status=new.status,
                                           added_by=actor.id if actor else None, added_by_username=getattr(actor,'username',None))
                if not present:
                    self.profiles.deactivate_chat(chat.id)
            return
        user, chat = update.effective_user, update.effective_chat
        if not user or getattr(user,'is_bot',False) or not chat:
            return
        if chat.type == 'private':
            referral = None
            text = getattr(update.effective_message, 'text', '') or ''
            match = re.fullmatch(r'/start(?:@\w+)?\s+([A-Za-z0-9_-]{1,64})', text)
            if match:
                referral = match[1]
            self.profiles.touch_contact(user.id, getattr(user,'username',None), getattr(user,'full_name',None), referral)

        elif chat.type in ('group','supergroup'):
            self.profiles.observe_chat(chat.id,title=chat.title,username=getattr(chat,'username',None))
