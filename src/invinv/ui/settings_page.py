from nicegui import ui

from invinv import alerts
from invinv.core.telegram import TelegramError
from invinv.db import repo
from invinv.i18n import LANGUAGES, current_language, tr, tr_in, tr_n
from invinv.runtime import runtime
from invinv.settings import get_settings
from invinv.ui.account import AccountCard
from invinv.ui.layout import THEMES, current_theme, page_content, set_theme, switch_language


def _hours(minutes: float) -> str:
    if minutes % 60:
        return tr_n(round(minutes), "{n} minute", "{n} minutes")
    return tr_n(round(minutes / 60), "{n} hour", "{n} hours")


async def content() -> None:
    """The settings page's content (in the app shell, see ui.shell)."""
    with page_content():
        ui.label(tr("Settings")).classes("text-h5")

        ui.label(tr("Steam account")).classes("text-h6")
        account = AccountCard(on_change=lambda: account.render())
        await account.render()

        ui.label(tr("Games")).classes("text-h6")
        await _games_card()

        ui.label(tr("Telegram alerts")).classes("text-h6").props("id=telegram")
        await _telegram_card()

        ui.label(tr("Appearance")).classes("text-h6")
        with ui.card().classes("w-full"), ui.row().classes("items-center gap-6"):
            ui.select(
                {key: tr(label) for key, label in THEMES.items()},
                label=tr("Theme"),
                value=current_theme(),
                on_change=lambda e: set_theme(e.value),
            ).classes("w-48")
            ui.select(
                dict(LANGUAGES),
                label=tr("Language"),
                value=current_language(),
                on_change=lambda e: switch_language(e.value),
            ).classes("w-48")

        ui.label(tr("Data collection")).classes("text-h6")
        settings = get_settings()
        status = await runtime.collector_status()
        with ui.card().classes("w-full"):
            rows = [
                (
                    tr("Collector"),
                    tr("running") if status.enabled else tr("turned off"),
                ),
                (tr("Tasks waiting now"), str(status.queued)),
                (
                    tr("Pause between requests to Steam"),
                    tr(
                        "{seconds} s now · adaptive, {fastest} to {slowest} s",
                        seconds=f"{status.pace_seconds:g}",
                        fastest=f"{settings.adaptive_min_interval_seconds:g}",
                        slowest=f"{settings.request_interval_seconds:g}",
                    )
                    if status.adaptive
                    else tr("{seconds} s", seconds=f"{settings.request_interval_seconds:g}"),
                ),
                (
                    tr("Important items refresh every"),
                    _hours(settings.history_refresh_minutes),
                ),
                (tr("Cheaper items refresh every"), tr("6 to 24 hours")),
                (tr("Inventory import every"), _hours(settings.inventory_refresh_hours * 60)),
            ]
            with ui.grid(columns=2).classes("gap-x-8 gap-y-1"):
                for label, value in rows:
                    ui.label(label).classes("text-muted")
                    ui.label(value)
            ui.label(
                tr(
                    "These values come from the INVINV_* environment variables; "
                    "see .env.example. The pause cannot be set below 8 seconds."
                )
            ).classes("text-caption text-muted")


async def _games_card() -> None:
    async with runtime.sessions() as session:
        games = await repo.inventory_games(session)
        disabled = await repo.disabled_games(session)
        sellable = await repo.sellable_counts(session)
    with ui.card().classes("w-full"):
        if not games:
            ui.label(tr("The games of your inventory appear here after the first import.")).classes(
                "text-muted"
            )
            return
        ui.label(
            tr(
                "Choose which games' inventories are imported. Changes apply at the next sync. "
                "Only items that can be sold on the market count in the portfolio."
            )
        ).classes("text-caption text-muted")
        for game in games:
            with ui.row().classes("w-full items-center no-wrap gap-3"):
                if game.icon_url:
                    ui.image(game.icon_url).classes("w-8 h-8 rounded")
                ui.label(game.name).classes("grow")
                ui.label(
                    tr(
                        "{sellable} of {total} sellable",
                        sellable=f"{sellable.get(game.appid, 0):,}",
                        total=tr_n(game.items, "{n} item", "{n} items"),
                    )
                ).classes("text-muted")

                ui.switch(
                    value=game.appid not in disabled,
                    on_change=lambda e, appid=game.appid: _set_game(appid, bool(e.value)),
                )


async def _set_game(appid: int, enabled: bool) -> None:
    async with runtime.sessions() as session:
        await repo.set_game_enabled(session, appid, enabled)
        account = await repo.get_account(session)
        if account is not None:
            await repo.run_job_now(session, repo.JOB_INVENTORY, account.steam_id64)
        await session.commit()
    ui.notify(tr("Saved; the inventory is synced again now."))


def _masked(token: str) -> str:
    """Enough of a bot token to recognise it, without showing the secret part."""
    bot_id, _, secret = token.partition(":")
    return f"{bot_id}:{secret[:3]}…" if secret else "…"


async def _telegram_card() -> None:
    card = ui.card().classes("w-full")

    async def render() -> None:
        async with runtime.sessions() as session:
            token = await repo.get_setting(session, repo.SETTING_TELEGRAM_TOKEN)
            chat = await repo.get_setting(session, repo.SETTING_TELEGRAM_CHAT)
            language = await repo.get_setting(session, repo.SETTING_TELEGRAM_LANGUAGE)
        card.clear()
        with card:
            ui.label(tr("Alerts always show up in InvInv. To also get them in Telegram:")).classes(
                "text-muted"
            )
            with ui.element("ol").classes("list-decimal pl-6 text-muted"):
                for step in (
                    tr("In Telegram, message @BotFather, send /newbot and copy the token."),
                    tr("Paste the token below and save it."),
                    tr("Send any message to your new bot, then press Find my chat."),
                ):
                    with ui.element("li"):
                        ui.label(step)
            with ui.row().classes("w-full items-end gap-3"):
                token_input = (
                    ui.input(
                        tr("Bot token"),
                        placeholder=_masked(token) if token else "123456789:AA…",
                        password=True,
                    )
                    .props("autocomplete=off")
                    .classes("w-96 max-w-full")
                )
                ui.button(tr("Save token"), on_click=lambda: save_token(token_input.value))
            if not token:
                return
            with ui.row().classes("w-full items-center gap-3"):
                ui.button(tr("Find my chat"), icon="search", on_click=find_chat).props("outline")
                ui.label(tr("Chat: {chat}", chat=chat) if chat else tr("No chat yet")).classes(
                    "text-muted"
                )
            with ui.row().classes("w-full items-center gap-3"):
                ui.select(
                    dict(LANGUAGES),
                    label=tr("Message language"),
                    value=alerts.as_language(language or "en"),
                    on_change=lambda e: save_language(e.value),
                ).classes("w-48")
                ui.button(tr("Send a test message"), icon="send", on_click=send_test).props(
                    "outline"
                ).set_enabled(bool(chat))
                ui.button(tr("Disconnect"), on_click=disconnect).props("flat color=negative")

    async def save_token(value: str | None) -> None:
        token = (value or "").strip()
        if not token:
            ui.notify(tr("Paste the token from @BotFather first."), type="warning")
            return
        try:
            name = await runtime.telegram.username(token)
        except TelegramError as exc:
            ui.notify(tr("Telegram did not accept the token: {error}", error=exc), type="negative")
            return
        async with runtime.sessions() as session:
            await repo.set_setting(session, repo.SETTING_TELEGRAM_TOKEN, token)
            await repo.set_setting(session, repo.SETTING_TELEGRAM_CHAT, None)
            await session.commit()
        ui.notify(tr("Connected to @{bot}. Now send it any message.", bot=name), multi_line=True)
        await render()

    async def find_chat() -> None:
        async with runtime.sessions() as session:
            token = await repo.get_setting(session, repo.SETTING_TELEGRAM_TOKEN)
        if not token:
            return
        try:
            chat = await runtime.telegram.latest_chat(token)
        except TelegramError as exc:
            ui.notify(str(exc), type="negative")
            return
        if chat is None:
            ui.notify(
                tr("No messages to the bot yet. Send it any message and try again."),
                type="warning",
            )
            return
        async with runtime.sessions() as session:
            await repo.set_setting(session, repo.SETTING_TELEGRAM_CHAT, str(chat.id))
            await session.commit()
        ui.notify(tr("Alerts will go to {chat}", chat=chat.title or chat.id))
        await render()

    async def save_language(language: str) -> None:
        async with runtime.sessions() as session:
            await repo.set_setting(session, repo.SETTING_TELEGRAM_LANGUAGE, language)
            await session.commit()

    async def send_test() -> None:
        async with runtime.sessions() as session:
            telegram = await repo.telegram_settings(session)
        if telegram is None:
            return
        language = alerts.as_language(telegram.language)
        try:
            await runtime.telegram.send(
                telegram.token,
                telegram.chat_id,
                tr_in(language, "InvInv is connected: alerts will arrive here."),
            )
        except TelegramError as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify(tr("Sent; check Telegram."))

    async def disconnect() -> None:
        async with runtime.sessions() as session:
            await repo.set_setting(session, repo.SETTING_TELEGRAM_TOKEN, None)
            await repo.set_setting(session, repo.SETTING_TELEGRAM_CHAT, None)
            await session.commit()
        ui.notify(tr("Telegram disconnected"))
        await render()

    await render()
