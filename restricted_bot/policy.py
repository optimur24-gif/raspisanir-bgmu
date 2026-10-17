"""Fixed boundaries taken from the user's original bot."""

from aiogram import BaseMiddleware, Bot

EXPECTED_BOT_ID = 8975598877
GROUP_CHAT_ID = -1004345960675


class ScopedBot(Bot):
    async def __call__(self, method, request_timeout=None):
        if self.id != EXPECTED_BOT_ID:
            raise ValueError("Chat management is restricted to the original bot")
        chat_id = getattr(method, "chat_id", None)
        if chat_id is not None:
            # Positive IDs are private admin conversations; group targets are fixed.
            try:
                numeric = int(chat_id)
            except (ValueError, TypeError):
                raise ValueError("Named chat targets are not allowed") from None
            if numeric < 0 and numeric != GROUP_CHAT_ID:
                raise ValueError("Chat management is restricted to the original chat")
        return await super().__call__(method, request_timeout=request_timeout)


class ScopeMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
        bot = data.get("bot")
        if not chat or not bot or bot.id != EXPECTED_BOT_ID:
            return
        if chat.type != "private" and chat.id != GROUP_CHAT_ID:
            return
        return await handler(event, data)
