from datetime import UTC, datetime, timedelta
from typing import Any

from nicegui import ui

from cs2market.analysis import MissingRateError, compute_metrics, to_frame
from cs2market.core.parsers import market_hash_name_from_input
from cs2market.db import repo
from cs2market.runtime import runtime
from cs2market.ui.common import fmt_pct, fmt_usd, header

# Enough history for the 30-day metrics on the overview.
OVERVIEW_WINDOW = timedelta(days=35)

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
                name_input = ui.input(
                    "Item name or market URL",
                    placeholder="AK-47 | Redline (Field-Tested)",
                ).classes("grow")
                add_button = ui.button("Add")
            ui.label(
                "New items are fetched by the background collector, usually within a few minutes."
            ).classes("text-caption text-grey-7")

            table = ui.table(columns=COLUMNS, rows=await _rows(), row_key="id").classes("w-full")
            table.on("rowClick", lambda e: ui.navigate.to(f"/item/{e.args[1]['id']}"))

            async def refresh() -> None:
                table.rows = await _rows()
                table.update()

            async def add() -> None:
                try:
                    name = market_hash_name_from_input(name_input.value or "")
                except ValueError as exc:
                    ui.notify(str(exc), type="warning")
                    return
                if not name:
                    return
                async with runtime.sessions() as session:
                    await repo.add_to_watchlist(session, name)
                    await session.commit()
                name_input.value = ""
                ui.notify(f"Added {name}")
                await refresh()

            add_button.on_click(add)
            name_input.on("keydown.enter", add)
            ui.timer(60, refresh)
