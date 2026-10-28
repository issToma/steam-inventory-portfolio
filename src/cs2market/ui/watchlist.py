from datetime import UTC, datetime, timedelta
from typing import Any, cast

from nicegui import ui

from cs2market.analysis import MissingRateError, compute_metrics, to_frame
from cs2market.core.http import SteamBusyError, SteamError
from cs2market.core.parsers import SearchResult, SteamFormatError, market_hash_name_from_input
from cs2market.core.steam import SKIN_KINDS, Exterior, ItemKind, Quality
from cs2market.db import repo
from cs2market.runtime import runtime
from cs2market.search import SearchQuery, search_local, search_steam
from cs2market.ui.common import fmt_pct, fmt_usd, header

# Enough history for the 30-day metrics on the overview.
OVERVIEW_WINDOW = timedelta(days=35)

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


COLUMNS: list[dict[str, Any]] = [
    {"name": "name", "label": "Item", "field": "name", "align": "left", "sortable": True},
    {"name": "price", "label": "Price", "field": "price", "align": "right"},
    {"name": "change_24h", "label": "24h", "field": "change_24h", "align": "right"},
    {"name": "change_7d", "label": "7d", "field": "change_7d", "align": "right"},
    {"name": "change_30d", "label": "30d", "field": "change_30d", "align": "right"},
    {"name": "volume", "label": "Sales/day", "field": "volume", "align": "right"},
    {"name": "status", "label": "Status", "field": "status", "align": "left"},
]


async def _rows() -> list[dict[str, Any]]:
    since = datetime.now(UTC) - OVERVIEW_WINDOW
    async with runtime.sessions() as session:
        items = await repo.watchlist_items(session)
        fx = await repo.latest_fx(session)
        jobs = await repo.jobs_for(session, items)
        rows = []
        for item in items:
            row: dict[str, Any] = {"id": item.id, "name": item.market_hash_name}
            job = jobs.get(item.id)
            points = await repo.load_history(session, item.id, since)
            try:
                metrics = compute_metrics(to_frame(points, fx))
            except MissingRateError:
                metrics = None
                row["status"] = "waiting for exchange rate"
            if metrics is not None:
                row |= {
                    "price": fmt_usd(metrics.last_price),
                    "change_24h": fmt_pct(metrics.change_24h),
                    "change_7d": fmt_pct(metrics.change_7d),
                    "change_30d": fmt_pct(metrics.change_30d),
                    "volume": f"{metrics.avg_daily_volume_30d:,.0f}"
                    if metrics.avg_daily_volume_30d is not None
                    else "—",
                }
            if job is not None and job.status == "error":
                row["status"] = f"error: {job.last_error}"
            if metrics is not None:
                row.setdefault("status", "ok")
            elif job is not None and job.status == "ok":
                row.setdefault("status", "no sales on record")
            else:
                row.setdefault("status", "waiting for first fetch")
            rows.append(row)
    return rows


def register() -> None:
    @ui.page("/")
    async def watchlist_page() -> None:
        header()
        with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):
            ui.label("Watchlist").classes("text-h5")
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

            table = ui.table(columns=COLUMNS, rows=await _rows(), row_key="id").classes("w-full")
            table.on("rowClick", lambda e: ui.navigate.to(f"/item/{e.args[1]['id']}"))

            async def refresh() -> None:
                table.rows = await _rows()
                table.update()

            async def add(result: SearchResult, button: ui.button) -> None:
                async with runtime.sessions() as session:
                    await repo.add_to_watchlist(session, result.market_hash_name, result)
                    await session.commit()
                button.disable()
                button.text = "Added"
                ui.notify(f"Added {result.market_hash_name}; history arrives in about a minute")
                await refresh()

            async def show_results(results: list[SearchResult]) -> None:
                async with runtime.sessions() as session:
                    watched = {i.market_hash_name for i in await repo.watchlist_items(session)}
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
                            button = ui.button("Add").props("flat dense")
                            if result.market_hash_name in watched:
                                button.text = "In watchlist"
                                button.disable()
                            else:
                                button.on_click(lambda r=result, b=button: add(r, b))

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
                    quality=None
                    if quality_toggle.value == ANY
                    else cast(Quality, quality_toggle.value),
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
                status.text = (
                    "Asking Steam… (requests are spaced out, this can take up to a minute)"
                )
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
            ui.timer(60, refresh)
