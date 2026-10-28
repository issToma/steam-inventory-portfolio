from datetime import UTC, datetime, timedelta
from typing import Any

from nicegui import ui

from cs2market.analysis import MissingRateError, compute_metrics, to_frame
from cs2market.core.parsers import SearchResult
from cs2market.db import repo
from cs2market.i18n import tr
from cs2market.runtime import runtime
from cs2market.ui.common import fmt_pct, fmt_usd
from cs2market.ui.layout import page_frame
from cs2market.ui.search_box import search_box

# Enough history for the 30-day metrics on the overview.
OVERVIEW_WINDOW = timedelta(days=35)


def columns() -> list[dict[str, Any]]:
    return [
        {"name": "name", "label": tr("Item"), "field": "name", "align": "left", "sortable": True},
        {"name": "price", "label": tr("Price"), "field": "price", "align": "right"},
        {"name": "change_24h", "label": "24h", "field": "change_24h", "align": "right"},
        {"name": "change_7d", "label": "7d", "field": "change_7d", "align": "right"},
        {"name": "change_30d", "label": "30d", "field": "change_30d", "align": "right"},
        {"name": "volume", "label": tr("Sales/day"), "field": "volume", "align": "right"},
        {"name": "status", "label": tr("Status"), "field": "status", "align": "left"},
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
                row["status"] = tr("waiting for exchange rate")
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

            async def watched() -> set[str]:
                async with runtime.sessions() as session:
                    return {i.market_hash_name for i in await repo.watchlist_items(session)}

            async def add(result: SearchResult) -> bool:
                async with runtime.sessions() as session:
                    await repo.add_to_watchlist(session, result.market_hash_name, result)
                    await session.commit()
                ui.notify(
                    tr(
                        "Added {name}; history arrives in about a minute",
                        name=result.market_hash_name,
                    )
                )
                await refresh()
                return True

            def open_add_dialog() -> None:
                with ui.dialog() as dialog, ui.card().classes("w-[48rem] max-w-full"):
                    ui.label(tr("Add to watchlist")).classes("text-h6")
                    search_box(
                        action_label=tr("Add"),
                        on_pick=add,
                        already=watched,
                        already_label=tr("In watchlist"),
                    )
                dialog.open()

            table = ui.table(columns=columns(), rows=await _rows(), row_key="id").classes("w-full")
            table.on("rowClick", lambda e: ui.navigate.to(f"/item/{e.args[1]['id']}"))

            async def refresh() -> None:
                table.rows = await _rows()
                table.update()

            ui.timer(60, refresh)
