from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from invinv.core.telegram import TelegramBot, TelegramError

TOKEN = "123:secret"


@pytest.fixture
async def bot() -> AsyncIterator[tuple[TelegramBot, list[tuple[str, Any]]]]:
    calls: list[tuple[str, Any]] = []

    async def handle(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        calls.append((method, await request.json()))
        if request.match_info["token"] != f"bot{TOKEN}":
            return web.json_response({"ok": False, "description": "Unauthorized"}, status=401)
        if method == "sendPhoto" and "broken" in (await request.json())["photo"]:
            return web.json_response(
                {"ok": False, "description": "Bad Request: wrong file identifier"}, status=400
            )
        results: dict[str, Any] = {
            "getMe": {"username": "invinv_bot"},
            "getUpdates": [
                {"update_id": 1, "message": {"chat": {"id": 5, "first_name": "Old"}}},
                {"update_id": 2, "message": {"chat": {"id": 7, "first_name": "Ann"}}},
            ],
            "sendMessage": {"message_id": 1},
            "sendPhoto": {"message_id": 2},
        }
        return web.json_response({"ok": True, "result": results[method]})

    app = web.Application()
    app.router.add_post("/{token}/{method}", handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        yield TelegramBot(http, str(server.make_url("")).rstrip("/")), calls


async def test_bot_calls(bot: tuple[TelegramBot, list[tuple[str, Any]]]) -> None:
    client, calls = bot

    assert await client.username(TOKEN) == "invinv_bot"
    chat = await client.latest_chat(TOKEN)
    await client.send(TOKEN, "7", "<b>hi</b>")

    assert chat is not None and (chat.id, chat.title) == (7, "Ann")
    assert calls[-1] == (
        "sendMessage",
        {
            "chat_id": "7",
            "text": "<b>hi</b>",
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
    )


async def test_refusal_is_reported_without_the_token(
    bot: tuple[TelegramBot, list[tuple[str, Any]]],
) -> None:
    client, _ = bot

    with pytest.raises(TelegramError) as error:
        await client.username("999:wrong")

    assert str(error.value) == "Unauthorized"


async def test_unreachable_telegram_does_not_leak_the_token() -> None:
    async with aiohttp.ClientSession() as http:
        client = TelegramBot(http, "http://127.0.0.1:9")
        with pytest.raises(TelegramError) as error:
            await client.send("123:very-secret", "1", "x")

    assert "very-secret" not in str(error.value)
    assert error.value.__cause__ is None


async def test_photo_with_caption_and_text_when_the_photo_fails(
    bot: tuple[TelegramBot, list[tuple[str, Any]]],
) -> None:
    client, calls = bot

    await client.send(TOKEN, "7", "<b>hi</b>", photo_url="https://cdn/ok.png")
    await client.send(TOKEN, "7", "<b>hi</b>", photo_url="https://cdn/broken.png")

    assert [method for method, _ in calls] == ["sendPhoto", "sendPhoto", "sendMessage"]
    assert calls[0][1]["caption"] == "<b>hi</b>"
