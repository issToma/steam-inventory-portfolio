"""The Steam account card on the portfolio page: connect, status, change, disconnect."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from nicegui import ui

from invinv.core.http import SteamBusyError, SteamError
from invinv.core.parsers import SteamFormatError, parse_profile_input
from invinv.db import repo
from invinv.i18n import tr, tr_n
from invinv.runtime import runtime
from invinv.search import STEAM_MAX_WAIT
from invinv.ui.common import time_ago, time_until

PRIVACY_SETTINGS_URL = "https://steamcommunity.com/my/edit/settings"


class AccountCard:
    def __init__(self, on_change: Callable[[], Awaitable[None]]) -> None:
        self._on_change = on_change
        self._editing = False
        self._box = ui.column().classes("w-full")

    async def render(self) -> None:
        async with runtime.sessions() as session:
            account = await repo.get_account(session)
        self._box.clear()
        with self._box, ui.card().classes("w-full"):
            if account is None or self._editing:
                self._connect_form(cancellable=account is not None)
            else:
                self._status(account)

    def _connect_form(self, *, cancellable: bool) -> None:
        ui.label(tr("Connect your Steam inventory")).classes("text-subtitle1")
        ui.label(
            tr(
                "Paste a link to your Steam profile. Your items are imported automatically and "
                "kept up to date; nothing else is needed from your account."
            )
        ).classes("text-muted")
        with ui.row().classes("w-full items-center"):
            link = ui.input(
                tr("Profile link or SteamID64"),
                placeholder="https://steamcommunity.com/id/yourname",
            ).classes("grow")
            connect = ui.button(tr("Connect"))
            if cancellable:
                ui.button(tr("Cancel"), on_click=self._stop_editing).props("flat")
        self._privacy_help(expanded=False)

        async def submit() -> None:
            connect.disable()
            try:
                await self._connect(link.value or "")
            finally:
                connect.enable()

        connect.on_click(submit)
        link.on("keydown.enter", submit)

    async def _connect(self, text: str) -> None:
        try:
            ref = parse_profile_input(text)
        except ValueError:
            ui.notify(tr("That does not look like a Steam profile link."), type="warning")
            return
        try:
            profile = await runtime.market.profile(ref, priority=True, max_wait=STEAM_MAX_WAIT)
        except SteamBusyError:
            ui.notify(tr("Steam is busy right now; try again in a few minutes."), type="warning")
            return
        except SteamFormatError:
            ui.notify(tr("Steam does not know this profile. Check the link."), type="warning")
            return
        except SteamError:
            ui.notify(tr("Could not reach Steam; try again in a minute."), type="warning")
            return
        async with runtime.sessions() as session:
            await repo.set_inventory_owner(session, profile.steam_id64)
            await repo.save_profile(session, profile, datetime.now(UTC))
            await session.commit()
        self._editing = False
        if profile.privacy != "public":
            ui.notify(tr("Connected, but the profile is not public yet."), type="warning")
        else:
            ui.notify(
                tr("Connected as {name}. Importing your items…", name=profile.name),
                type="positive",
            )
        await self._on_change()

    def _status(self, account: repo.Account) -> None:
        profile = account.profile
        job = account.job
        private = (profile is not None and profile.privacy != "public") or (
            job is not None and job.status == "error" and "not public" in (job.last_error or "")
        )
        with ui.row().classes("w-full items-center no-wrap gap-4"):
            if profile and profile.avatar_url:
                ui.image(profile.avatar_url).classes("w-14 h-14 rounded")
            with ui.column().classes("grow gap-0"):
                if profile:
                    ui.link(profile.name, profile.profile_url, new_tab=True).classes(
                        "text-subtitle1 plain-link"
                    )
                else:
                    ui.label(tr("Steam account")).classes("text-subtitle1")
                self._status_lines(account, private)
            ui.button(tr("Sync now"), on_click=self._sync_now).props("flat")
            with ui.button(icon="more_vert").props("flat round"), ui.menu():
                ui.menu_item(tr("Change account"), on_click=self._start_editing)
                ui.menu_item(tr("Disconnect"), on_click=self._disconnect)
        if private:
            self._privacy_help(expanded=True)

    @staticmethod
    def _status_lines(account: repo.Account, private: bool) -> None:
        job = account.job
        caption = "text-caption text-muted"
        if private:
            ui.label(tr("⚠ Your inventory is hidden by Steam's privacy settings.")).classes(
                "text-warning"
            )
        elif job is not None and job.status == "error":
            ui.label(tr("⚠ The last import failed: {error}", error=job.last_error)).classes(
                "text-warning"
            )
            ui.label(tr("Next attempt {when}.", when=time_until(job.run_after))).classes(caption)
        elif account.positions is None:
            ui.label(tr("⏳ Importing your inventory… this usually takes a minute."))
        else:
            assert job is not None
            ui.label(
                tr(
                    "✓ Synced {when} · {items}",
                    when=time_ago(job.updated_at),
                    items=tr_n(account.positions, "{n} item", "{n} items"),
                )
            )
            ui.label(tr("Next update {when}.", when=time_until(job.run_after))).classes(caption)

    @staticmethod
    def _privacy_help(*, expanded: bool) -> None:
        with ui.expansion(tr("How to make your inventory visible"), value=expanded).classes(
            "w-full text-muted"
        ):
            ui.markdown(
                tr(
                    "1. Open your [Steam privacy settings]({url}) (log in to Steam in this "
                    "browser if asked).\n"
                    "2. Set **My profile** and **Inventory** to **Public**. "
                    '"Friends only" is not enough: this app does not log in to Steam.\n'
                    "3. Come back and press **Sync now**. Steam may take a few minutes to "
                    "apply it.",
                    url=PRIVACY_SETTINGS_URL,
                )
            )

    async def _sync_now(self) -> None:
        async with runtime.sessions() as session:
            account = await repo.get_account(session)
            if account is not None:
                await repo.run_job_now(session, repo.JOB_INVENTORY, account.steam_id64)
                await session.commit()
        ui.notify(tr("Checking your inventory…"))
        await self.render()

    async def _disconnect(self) -> None:
        async with runtime.sessions() as session:
            await repo.set_inventory_owner(session, None)
            await session.commit()
        ui.notify(tr("Disconnected. Its positions are kept and come back if you connect it again."))
        await self._on_change()

    async def _start_editing(self) -> None:
        self._editing = True
        await self.render()

    async def _stop_editing(self) -> None:
        self._editing = False
        await self.render()
