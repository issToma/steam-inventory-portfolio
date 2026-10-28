from nicegui import ui

from cs2market.db import repo
from cs2market.i18n import LANGUAGES, current_language, tr, tr_n
from cs2market.runtime import runtime
from cs2market.settings import get_settings
from cs2market.ui.account import AccountCard
from cs2market.ui.layout import THEMES, current_theme, page_frame, set_theme, switch_language


def _hours(minutes: float) -> str:
    if minutes % 60:
        return tr_n(round(minutes), "{n} minute", "{n} minutes")
    return tr_n(round(minutes / 60), "{n} hour", "{n} hours")


def register() -> None:
    @ui.page("/settings")
    async def settings_page() -> None:
        with page_frame("settings"):
            ui.label(tr("Settings")).classes("text-h5")

            ui.label(tr("Steam account")).classes("text-h6")
            account = AccountCard(on_change=lambda: account.render())
            await account.render()

            ui.label(tr("Games")).classes("text-h6")
            await _games_card()

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
                        tr("{seconds} s", seconds=f"{settings.request_interval_seconds:g}"),
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
                        "These values come from the CS2M_* environment variables; "
                        "see .env.example. The pause cannot be set below 15 seconds."
                    )
                ).classes("text-caption text-muted")


async def _games_card() -> None:
    async with runtime.sessions() as session:
        games = await repo.inventory_games(session)
        disabled = await repo.disabled_games(session)
    with ui.card().classes("w-full"):
        if not games:
            ui.label(tr("The games of your inventory appear here after the first import.")).classes(
                "text-muted"
            )
            return
        ui.label(
            tr("Choose which games' inventories are imported. Changes apply at the next sync.")
        ).classes("text-caption text-muted")
        for game in games:
            with ui.row().classes("w-full items-center no-wrap gap-3"):
                if game.icon_url:
                    ui.image(game.icon_url).classes("w-8 h-8 rounded")
                ui.label(game.name).classes("grow")
                ui.label(tr_n(game.items, "{n} item", "{n} items")).classes("text-muted")

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
