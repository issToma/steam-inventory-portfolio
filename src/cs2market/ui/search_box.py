"""Item search used to add items to the watchlist and to the portfolio.

Typing searches the local catalog instantly; Enter or "Search Steam" asks Steam.
"""

from collections.abc import Awaitable, Callable
from typing import cast

from nicegui import ui

from cs2market.core.http import SteamBusyError, SteamError
from cs2market.core.parsers import SearchResult, SteamFormatError, market_hash_name_from_input
from cs2market.core.steam import SKIN_KINDS, Exterior, ItemKind, Quality
from cs2market.search import SearchQuery, search_local, search_steam
from cs2market.ui.common import fmt_usd

ICON_URL = "https://community.akamai.steamstatic.com/economy/image/{}/62fx62f"

ANY = "any"
KIND_OPTIONS: dict[str, str] = {
    ANY: "Any type",
    "rifle": "Rifle",
    "sniper_rifle": "Sniper rifle",
    "pistol": "Pistol",
    "smg": "SMG",
    "shotgun": "Shotgun",
    "machinegun": "Machine gun",
    "equipment": "Equipment (Zeus)",
    "knife": "Knife",
    "gloves": "Gloves",
    "sticker": "Sticker",
    "case": "Case",
    "agent": "Agent",
    "graffiti": "Graffiti",
    "patch": "Patch",
    "charm": "Charm",
    "music_kit": "Music kit",
    "key": "Key",
    "collectible": "Collectible (pins)",
}
QUALITY_OPTIONS: dict[str, str] = {
    ANY: "All",
    "normal": "Classic",
    "stattrak": "StatTrak™",
    "souvenir": "Souvenir",
}
EXTERIOR_OPTIONS: dict[str, str] = {
    ANY: "Any wear",
    "fn": "FN",
    "mw": "MW",
    "ft": "FT",
    "ww": "WW",
    "bs": "BS",
}


def search_box(
    *,
    action_label: str,
    on_pick: Callable[[SearchResult], Awaitable[bool]],
    already: Callable[[], Awaitable[set[str]]] | None = None,
    already_label: str = "",
) -> None:
    """Render the search UI. `on_pick` returns True when the item was added; items whose
    names `already` returns get a disabled `already_label` button instead."""

    async def pick(result: SearchResult, button: ui.button) -> None:
        if await on_pick(result):
            button.disable()
            button.text = "Added"

    with ui.row().classes("w-full items-center"):
        query_input = ui.input(
            "Find an item (name, part of it, or a market URL)",
            placeholder="redline, breakout case, karambit doppler…",
        ).classes("grow")
        steam_button = ui.button("Search Steam", icon="travel_explore")
    with ui.row().classes("items-center gap-4"):
        kind_select = ui.select(KIND_OPTIONS, value=ANY).props("dense outlined")
        quality_toggle = ui.toggle(QUALITY_OPTIONS, value=ANY).props("dense no-caps")
        exterior_toggle = ui.toggle(EXTERIOR_OPTIONS, value=ANY).props("dense no-caps")
        exterior_toggle.set_visibility(False)
    status = ui.label().classes("text-caption text-grey-7")
    results_box = ui.column().classes("w-full gap-1")

    async def show_results(results: list[SearchResult]) -> None:
        done = await already() if already else set()
        results_box.clear()
        with results_box:
            for result in results:
                with (
                    ui.card().classes("w-full py-1 px-2"),
                    ui.row().classes("w-full items-center no-wrap gap-3"),
                ):
                    if result.icon_url:
                        ui.image(ICON_URL.format(result.icon_url)).classes("w-12 h-9")
                    with ui.column().classes("grow gap-0"):
                        ui.label(result.market_hash_name)
                        ui.label(result.item_type).classes("text-caption text-grey-7")
                    if result.sell_price_usd_cents is not None:
                        ui.label(
                            f"{fmt_usd(result.sell_price_usd_cents / 100)} · "
                            f"{result.sell_listings or 0:,} listed"
                        ).classes("text-grey-8")
                    button = ui.button(action_label).props("flat dense")
                    if result.market_hash_name in done:
                        button.text = already_label
                        button.disable()
                    else:
                        button.on_click(lambda r=result, b=button: pick(r, b))

    def current_query() -> SearchQuery | None:
        """The query from the input and filters, or None (with a hint) if unusable."""
        raw = (query_input.value or "").strip()
        try:
            text = market_hash_name_from_input(raw)
        except ValueError as exc:
            status.text = str(exc)
            return None
        if raw.startswith("http"):
            # A pasted URL names one exact item; filters could only hide it.
            return SearchQuery(text)
        kind = None if kind_select.value == ANY else cast(ItemKind, kind_select.value)
        if len(text) < 2 and kind is None:
            status.text = ""
            results_box.clear()
            return None
        return SearchQuery(
            text,
            kind=kind,
            quality=None if quality_toggle.value == ANY else cast(Quality, quality_toggle.value),
            exterior=None
            if exterior_toggle.value == ANY
            else cast(Exterior, exterior_toggle.value),
        )

    async def find_local() -> None:
        query = current_query()
        if query is None:
            return
        results = await search_local(query)
        await show_results(results)
        status.text = (
            f"{len(results)} match(es) among items seen before. "
            "Not what you need? Press Search Steam."
            if results
            else "Nothing among items seen before. Press Search Steam to ask Steam."
        )

    async def find_on_steam() -> None:
        query = current_query()
        if query is None:
            status.text = status.text or "Type at least two characters or pick a type."
            return
        steam_button.disable()
        status.text = "Asking Steam… (requests are spaced out, this can take up to a minute)"
        try:
            results = await search_steam(query)
        except SteamBusyError as exc:
            status.text = (
                f"Steam is rate limiting us; try again in about {exc.wait / 60:.0f} min. "
                "Items seen before are still searchable."
            )
            return
        except (SteamError, SteamFormatError, TimeoutError) as exc:
            status.text = f"Steam search failed: {exc}"
            return
        finally:
            steam_button.enable()
        await show_results(results)
        status.text = (
            f"Steam found {len(results)} (at most 10 per search; refine if needed)."
            if results
            else "Steam found nothing. Check the spelling or loosen the filters."
        )

    async def kind_changed() -> None:
        # Wear and StatTrak/Souvenir exist only for skins; reset hidden filters so
        # they cannot silently exclude stickers, cases and the like.
        kind = kind_select.value
        skin = kind in SKIN_KINDS
        quality_toggle.set_visibility(kind == ANY or skin)
        exterior_toggle.set_visibility(skin)
        if not skin:
            exterior_toggle.set_value(ANY)
            if kind != ANY:
                quality_toggle.set_value(ANY)
        await find_local()

    query_input.on_value_change(find_local)
    query_input.on("keydown.enter", find_on_steam)
    steam_button.on_click(find_on_steam)
    kind_select.on_value_change(kind_changed)
    quality_toggle.on_value_change(find_local)
    exterior_toggle.on_value_change(find_local)
