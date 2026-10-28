"""A minimal Telegram Bot API client: enough to send alerts to one chat.

The bot token is part of every request URL, so errors never include the URL or the
underlying exception text: they could end up in logs.
"""

from dataclasses import dataclass
from typing import Any

import aiohttp

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = aiohttp.ClientTimeout(total=20)


class TelegramError(Exception):
    """Telegram refused the request or could not be reached."""


@dataclass(frozen=True)
class TelegramChat:
    id: int
    title: str  # a group's title, or a person's name or username


class TelegramBot:
    def __init__(self, http: aiohttp.ClientSession, base_url: str = TELEGRAM_API) -> None:
        self._http = http
        self._base_url = base_url

    async def username(self, token: str) -> str:
        """The bot's @username; also a check that the token is valid."""
        me = await self._call(token, "getMe", {})
        return str(me.get("username", ""))

    async def latest_chat(self, token: str) -> TelegramChat | None:
        """The chat of the latest message sent to the bot, e.g. after the user sends /start."""
        updates = await self._call(token, "getUpdates", {"allowed_updates": ["message"]})
        for update in reversed(updates if isinstance(updates, list) else []):
            chat = (update.get("message") or {}).get("chat") if isinstance(update, dict) else None
            if isinstance(chat, dict) and isinstance(chat.get("id"), int):
                name = chat.get("title") or " ".join(
                    part for part in (chat.get("first_name"), chat.get("last_name")) if part
                )
                return TelegramChat(id=chat["id"], title=name or chat.get("username") or "")
        return None

    async def send(self, token: str, chat_id: str, html: str) -> None:
        await self._call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": html,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )

    async def _call(self, token: str, method: str, payload: dict[str, Any]) -> Any:
        url = f"{self._base_url}/bot{token}/{method}"
        try:
            async with self._http.post(url, json=payload, timeout=TIMEOUT) as response:
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise TelegramError(f"Telegram is unreachable ({type(exc).__name__})") from None
        if not isinstance(body, dict) or not body.get("ok"):
            description = body.get("description") if isinstance(body, dict) else None
            raise TelegramError(str(description or f"Telegram refused {method}"))
        return body.get("result")
