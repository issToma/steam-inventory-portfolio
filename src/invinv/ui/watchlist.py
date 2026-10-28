from datetime import UTC, datetime, timedelta
from typing import Any

from nicegui import ui

from invinv.analysis import (
    CHANGE_WINDOWS,
    MissingRateError,
    change_since,
    compute_metrics,
    price_views,
    recent_trend,
    to_frame,
)
from invinv.core.parsers import SearchResult
from invinv.db import repo
from invinv.i18n import tr
from invinv.pricing import listing_of, quote, recent_sales_price
from invinv.runtime import runtime
from invinv.ui.common import (
    NAME_CELL_SLOT,
    SPARK_CELL_SLOT,
    fmt_pct,
    fmt_usd,
    icon_url,
    rarity_color,
    sparkline_points,
)
from invinv.ui.games import GameSwitch, game_names, searchable_games
from invinv.ui.layout import page_frame
from invinv.ui.search_box import search_box

# Enough history for the 30-day metrics on the overview.
OVERVIEW_WINDOW = timedelta(days=35)


def columns() -> list[dict[str, Any]]:
    return [
        {"name": "name", "label": tr("Item"), "field": "name", "align": "left", "sortable": True},
        {"name": "price", "label": tr("Price"), "field": "price", "align": "right"},
        {"name": "change_24h", "label": "24h", "field": "change_24h", "align": "right"},
        {"name": "change_7d", "label": "7d", "field": "change_7d", "align": "right"},
        {"name": "change_30d", "label": "30d", "field": "change_30d", "align": "right"},
        {"name": "trend", "label": "", "field": "spark", "align": "center"},
        {"name": "volume", "label": tr("Sales/day"), "field": "volume", "align": "right"},
        {"name": "status", "label": tr("Status"), "field": "status", "align": "left"},
        {"name": "remove", "label": "", "field": "id", "align": "right"},
    ]


async def _rows() -> list[dict[str, Any]]:
    since = datetime.now(UTC) - OVERVIEW_WINDOW
    async with runtime.sessions() as session:
        items = await repo.watchlist_items(session)
        fx = await repo.latest_fx(session)
        jobs = await repo.jobs_for(session, items)
        rows = []
        for item in items:
            row: dict[str, Any] = {
                "id": item.id,
                "appid": item.appid,
                "name": item.market_hash_name,
                "icon": icon_url(item.icon_url),
                "color": rarity_color(item.rarity, item.quality, item.name_color, item.appid),
            }
            job = jobs.get(item.id)
            points = await repo.load_history(session, item.id, since)
            try:
                frame = to_frame(points, fx)
                views = price_views(frame) if not frame.empty else None
                metrics = compute_metrics(frame, views)
                current = quote(
                    listing_of(item),
                    recent_sales_price(views) if views else None,
                    datetime.now(UTC),
                )
                trend = recent_trend(frame)
                row["spark"] = sparkline_points(trend)
                row["spark_up"] = len(trend) < 2 or trend[-1] >= trend[0]
            except MissingRateError:
                metrics = None
                views = None
                current = None
                row["status"] = tr("waiting for exchange rate")
            if metrics is not None and views is not None:
                price = current.price if current else metrics.last_price
                now = datetime.now(UTC)
                changes = {
                    key: change_since(views.robust, price, span, now)
                    for key, span in CHANGE_WINDOWS.items()
                }
                row |= {
                    "price": fmt_usd(price),
                    "change_24h": fmt_pct(changes["24h"]),
                    "change_7d": fmt_pct(changes["7d"]),
                    "change_30d": fmt_pct(changes["30d"]),
                    "volume": f"{metrics.avg_daily_volume_30d:,.0f}"
                    if metrics.avg_daily_volume_30d is not None
                    else "—",
                }
            if job is not None and job.status == "error":
                row["status"] = tr("error: {error}", error=job.last_error)
            if metrics is not None:
                row.setdefault("status", tr("ok"))
            elif job is not None and job.status == "ok":
                row.setdefault("status", tr("no sales on record"))
            else:
                row.setdefault("status", tr("waiting for first fetch"))
            rows.append(row)
    return rows


def register() -> None:
    @ui.page("/watchlist")
    async def watchlist_page() -> None:
        with page_frame("watchlist"):
            with ui.row().classes("w-full items-center"):
                with ui.column().classes("gap-0"):
                    ui.label(tr("Watchlist")).classes("text-h5")
                    ui.label(tr("Items you follow without owning them.")).classes("text-muted")
                ui.space()
                ui.button(tr("Add item"), icon="add", on_click=lambda: open_add_dialog())
            games = GameSwitch(on_change=lambda: refresh())

            async def watched() -> set[str]:
                async with runtime.sessions() as session:
                    return {i.market_hash_name for i in await repo.watchlist_items(session)}

            async def add(result: SearchResult) -> bool:
                async with runtime.sessions() as session:
                    await repo.add_to_watchlist(
                        session, result.market_hash_name, result, appid=result.appid
                    )
                    await session.commit()
                ui.notify(
                    tr(
                        "Added {name}; history arrives in about a minute",
                        name=result.market_hash_name,
                    )
                )
                await refresh()
                return True

            async def open_add_dialog() -> None:
                with ui.dialog() as dialog, ui.card().classes("w-[48rem] max-w-full"):
                    ui.label(tr("Add to watchlist")).classes("text-h6")
                    search_box(
                        action_label=tr("Add"),
                        on_pick=add,
                        already=watched,
                        already_label=tr("In watchlist"),
                        games=await searchable_games(),
                    )
                dialog.open()

            table = (
                ui.table(columns=columns(), rows=[], row_key="id")
                .classes("w-full")
                .props("loading")
            )
            table.add_slot("body-cell-name", NAME_CELL_SLOT)
            table.add_slot("body-cell-trend", SPARK_CELL_SLOT)
            table.add_slot(
                "body-cell-remove",
                '<q-td :props="props">'
                '<q-btn flat dense round size="sm" icon="close" color="grey" '
                "@click.stop=\"$parent.$emit('remove', props.row)\" />"
                "</q-td>",
            )
            table.on("rowClick", lambda e: ui.navigate.to(f"/item/{e.args[1]['id']}"))

            async def remove(event: Any) -> None:
                row = event.args
                async with runtime.sessions() as session:
                    await repo.remove_from_watchlist(session, row["id"])
                    await session.commit()
                ui.notify(tr("Removed {name} from the watchlist", name=row["name"]))
                await refresh()

            table.on("remove", remove)

            async def refresh() -> None:
                rows = await _rows()
                games.set_games((row["appid"] for row in rows), await game_names())
                table.rows = [r for r in rows if games.appid in (None, r["appid"])]
                table.props(remove="loading")
                table.update()

            ui.timer(0.05, refresh, once=True)
            ui.timer(60, refresh)
