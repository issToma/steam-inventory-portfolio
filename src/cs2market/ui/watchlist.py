import asyncio
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from nicegui import ui

from cs2market.analysis import MissingRateError, compute_metrics, to_frame
from cs2market.api import SEARCH_MAX_WAIT, SEARCH_TIMEOUT
from cs2market.core.http import SteamBusyError, SteamError
from cs2market.core.parsers import SearchResult, SteamFormatError, market_hash_name_from_input
from cs2market.core.steam import SKIN_KINDS, Exterior, ItemKind, Quality
from cs2market.db import repo
from cs2market.runtime import runtime
from cs2market.ui.common import fmt_pct, fmt_usd, header

# Enough history for the 30-day metrics on the overview.
OVERVIEW_WINDOW = timedelta(days=35)

SEARCH_CACHE_TTL = 600.0
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


@dataclass(frozen=True)
class SearchQuery:
    text: str
    kind: ItemKind | None = None
    quality: Quality | None = None
    exterior: Exterior | None = None


_search_cache: dict[SearchQuery, tuple[float, list[SearchResult]]] = {}


async def _search(query: SearchQuery) -> list[SearchResult]:
    key = replace(query, text=query.text.casefold())
    cached = _search_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < SEARCH_CACHE_TTL:
        return cached[1]
    results = await asyncio.wait_for(
        runtime.market.search(
            query.text,
            kind=query.kind,
            quality=query.quality,
            exterior=query.exterior,
            priority=True,
            max_wait=SEARCH_MAX_WAIT,
        ),
        timeout=SEARCH_TIMEOUT,
    )
    _search_cache[key] = (time.monotonic(), results)
    return results


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
        jobs = await repo.jobs_for(session, [i.market_hash_name for i in items])
        rows = []
        for item in items:
            row: dict[str, Any] = {"id": item.id, "name": item.market_hash_name}
            job = jobs.get(item.market_hash_name)
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
            row.setdefault("status", "ok" if metrics else "waiting for first fetch")
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
                    "Search the market or paste an item URL",
                    placeholder="redline, breakout case, karambit doppler…",
                ).classes("grow")
                search_button = ui.button("Search", icon="search")
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

            def show_results(results: list[SearchResult], watched: set[str]) -> None:
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
                            ui.label(
                                f"{fmt_usd(result.sell_price_usd_cents / 100)} · "
                                f"{result.sell_listings:,} listed"
                            ).classes("text-grey-8")
                            button = ui.button("Add").props("flat dense")
                            if result.market_hash_name in watched:
                                button.text = "In watchlist"
                                button.disable()
                            else:
                                button.on_click(lambda r=result, b=button: add(r, b))

            async def search() -> None:
                try:
                    query = market_hash_name_from_input(query_input.value or "")
                except ValueError as exc:
                    status.text = str(exc)
                    return
                kind = None if kind_select.value == ANY else cast(ItemKind, kind_select.value)
                if len(query) < 2 and kind is None:
                    status.text = "Type at least two characters or pick a type."
                    return
                search_button.disable()
                status.text = (
                    "Searching… (requests to Steam are spaced out, this can take up to a minute)"
                )
                results_box.clear()
                try:
                    # A pasted URL names one exact item; filters could only hide it.
                    if (query_input.value or "").strip().startswith("http"):
                        request = SearchQuery(query)
                    else:
                        request = SearchQuery(
                            query,
                            kind=kind,
                            quality=None
                            if quality_toggle.value == ANY
                            else cast(Quality, quality_toggle.value),
                            exterior=None
                            if exterior_toggle.value == ANY
                            else cast(Exterior, exterior_toggle.value),
                        )
                    results = await _search(request)
                except SteamBusyError as exc:
                    status.text = (
                        f"Steam is rate limiting us; try again in about {exc.wait / 60:.0f} min."
                    )
                    return
                except (SteamError, SteamFormatError, TimeoutError) as exc:
                    status.text = f"Search failed: {exc}"
                    return
                finally:
                    search_button.enable()
                if not results:
                    status.text = "Nothing found. Check the spelling or loosen the filters."
                    return
                async with runtime.sessions() as session:
                    watched = {i.market_hash_name for i in await repo.watchlist_items(session)}
                status.text = (
                    f"Found {len(results)} (Steam shows at most 10; refine the query if needed)."
                )
                show_results(results, watched)

            async def requery() -> None:
                if (query_input.value or "").strip() or kind_select.value != ANY:
                    await search()

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
                await requery()

            search_button.on_click(search)
            kind_select.on_value_change(kind_changed)
            quality_toggle.on_value_change(requery)
            exterior_toggle.on_value_change(requery)
            query_input.on("keydown.enter", search)
            ui.timer(60, refresh)
