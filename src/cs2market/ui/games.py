"""Game names and the game switch shown above lists when more than one game is involved."""

from collections.abc import Awaitable, Callable, Iterable

from nicegui import ui

from cs2market.core.games import KNOWN_GAMES, game_name
from cs2market.db import repo
from cs2market.i18n import tr
from cs2market.runtime import runtime

ALL_GAMES = 0


async def game_names() -> dict[int, str]:
    """Names of the games of the connected inventory, for the ones Steam told us about."""
    async with runtime.sessions() as session:
        return {g.appid: g.name for g in await repo.inventory_games(session)}


def name_of(appid: int, names: dict[int, str]) -> str:
    return names.get(appid) or game_name(appid)


class GameSwitch:
    """A row of game buttons ("All games", "Counter-Strike 2", ...); hidden for one game."""

    def __init__(self, on_change: Callable[[], Awaitable[None]]) -> None:
        self._on_change = on_change
        self._toggle = ui.toggle({ALL_GAMES: tr("All games")}, value=ALL_GAMES).props(
            "dense no-caps"
        )
        self._toggle.on_value_change(self._changed)
        self._toggle.set_visibility(False)

    @property
    def appid(self) -> int | None:
        value = self._toggle.value
        return None if value in (None, ALL_GAMES) else int(value)

    def set_games(self, appids: Iterable[int], names: dict[int, str]) -> None:
        games = sorted(set(appids), key=lambda a: name_of(a, names))
        options = {ALL_GAMES: tr("All games")} | {a: name_of(a, names) for a in games}
        if self._toggle.options != options:
            self._toggle.set_options(
                options, value=self._toggle.value if self._toggle.value in options else ALL_GAMES
            )
        self._toggle.set_visibility(len(games) > 1)

    async def _changed(self) -> None:
        await self._on_change()


async def searchable_games() -> dict[int, str]:
    """Games to offer in search: the inventory's games first, then the other known ones."""
    names = await game_names()
    ordered = [*names, *(appid for appid in KNOWN_GAMES if appid not in names)]
    return {appid: name_of(appid, names) for appid in ordered}
