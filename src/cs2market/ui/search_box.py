"""Item search used to add items to the watchlist and to the portfolio.

Typing searches the local catalog instantly; Enter or "Search Steam" asks Steam.
"""

from collections.abc import Awaitable, Callable
from typing import cast

from nicegui import ui

from cs2market.core.http import SteamBusyError, SteamError
from cs2market.core.parsers import (
    SearchResult,
    SteamFormatError,
    market_hash_name_from_input,
    parse_item_traits,
)
from cs2market.core.steam import SKIN_KINDS, Exterior, ItemKind, Quality
from cs2market.i18n import tr, tr_n
from cs2market.search import SearchQuery, search_local, search_steam
from cs2market.ui.common import fmt_usd, rarity_color

ICON_URL = "https://community.akamai.steamstatic.com/economy/image/{}/62fx62f"

ANY = "any"


def kind_options() -> dict[str, str]:
    return {
        ANY: tr("Any type"),
        "rifle": tr("Rifle"),
        "sniper_rifle": tr("Sniper rifle"),
        "pistol": tr("Pistol"),
        "smg": tr("SMG"),
        "shotgun": tr("Shotgun"),
        "machinegun": tr("Machine gun"),
        "equipment": tr("Equipment (Zeus)"),
        "knife": tr("Knife"),
        "gloves": tr("Gloves"),
        "sticker": tr("Sticker"),
        "case": tr("Case"),
        "agent": tr("Agent"),
        "graffiti": tr("Graffiti"),
        "patch": tr("Patch"),
        "charm": tr("Charm"),
        "music_kit": tr("Music kit"),
        "key": tr("Key"),
        "collectible": tr("Collectible (pins)"),
    }


def quality_options() -> dict[str, str]:
    return {
        ANY: tr("All"),
        "normal": tr("Classic"),
        "stattrak": "StatTrak™",
        "souvenir": "Souvenir",
    }


def exterior_options() -> dict[str, str]:
    return {ANY: tr("Any wear"), "fn": "FN", "mw": "MW", "ft": "FT", "ww": "WW", "bs": "BS"}


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
            button.text = tr("Added")

    with ui.row().classes("w-full items-center"):
        query_input = ui.input(
            tr("Find an item (name, part of it, or a market URL)"),
            placeholder=tr("redline, breakout case, karambit doppler…"),
        ).classes("grow")
        steam_button = ui.button(tr("Search Steam"), icon="travel_explore")
    with ui.row().classes("items-center gap-4"):
        kind_select = ui.select(kind_options(), value=ANY).props("dense outlined")
        quality_toggle = ui.toggle(quality_options(), value=ANY).props("dense no-caps")
        exterior_toggle = ui.toggle(exterior_options(), value=ANY).props("dense no-caps")
        exterior_toggle.set_visibility(False)
    status = ui.label().classes("text-caption text-muted")
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
                    traits = parse_item_traits(result.market_hash_name, result.item_type)
                    color = rarity_color(traits.rarity, traits.quality)
                    with ui.column().classes("grow gap-0"):
                        ui.label(result.market_hash_name).style(f"color: {color}" if color else "")
                        ui.label(result.item_type).classes("text-caption text-muted")
                    if result.sell_price_usd_cents is not None:
                        ui.label(
                            tr(
                                "{price} · {listings} listed",
                                price=fmt_usd(result.sell_price_usd_cents / 100),
                                listings=f"{result.sell_listings or 0:,}",
                            )
                        ).classes("text-muted")
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
        except ValueError:
            status.text = tr("That does not look like a market link to a single item.")
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
            tr_n(
                len(results),
                "{n} match among items seen before.",
                "{n} matches among items seen before.",
            )
            + " "
            + tr("Not what you need? Press Search Steam.")
            if results
            else tr("Nothing among items seen before. Press Search Steam to ask Steam.")
        )

    async def find_on_steam() -> None:
        query = current_query()
        if query is None:
            status.text = status.text or tr("Type at least two characters or pick a type.")
            return
        steam_button.disable()
        status.text = tr("Asking Steam… (requests are spaced out, this can take up to a minute)")
        try:
            results = await search_steam(query)
        except SteamBusyError as exc:
            status.text = tr(
                "Steam is rate limiting us; try again in about {minutes} min. "
                "Items seen before are still searchable.",
                minutes=f"{exc.wait / 60:.0f}",
            )
            return
        except (SteamError, SteamFormatError, TimeoutError) as exc:
            status.text = tr("Steam search failed: {error}", error=exc)
            return
        finally:
            steam_button.enable()
        await show_results(results)
        status.text = (
            tr("Steam found {count} (at most 10 per search; refine if needed).", count=len(results))
            if results
            else tr("Steam found nothing. Check the spelling or loosen the filters.")
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
