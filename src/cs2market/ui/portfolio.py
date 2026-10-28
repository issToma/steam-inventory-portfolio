from datetime import timedelta
from typing import Any

import pandas as pd
from nicegui import ui

from cs2market.core.parsers import SearchResult
from cs2market.db import repo
from cs2market.i18n import tr
from cs2market.portfolio import Portfolio, Position, load_portfolio
from cs2market.runtime import runtime
from cs2market.ui.common import (
    NAME_CELL_SLOT,
    SPARK_CELL_SLOT,
    fmt_pct,
    fmt_usd,
    icon_url,
    pct_color,
    rarity_color,
    sparkline_points,
    time_ago,
    time_until,
)
from cs2market.ui.layout import echart_theme, page_frame
from cs2market.ui.positions import DELETE, PositionForm, add_position, position_dialog
from cs2market.ui.search_box import search_box

RANGES = {"30D": timedelta(days=30), "1Y": timedelta(days=365), "All": None}


def range_options() -> dict[str, str]:
    return {key: tr(key) if key == "All" else key for key in RANGES}


def columns(*, with_pnl: bool) -> list[dict[str, Any]]:
    """P&L only appears once some position has a purchase price."""
    return [column for column in _all_columns() if with_pnl or column["name"] != "pnl"]


def _all_columns() -> list[dict[str, Any]]:
    return [
        {"name": "name", "label": tr("Item"), "field": "name", "align": "left", "sortable": True},
        {
            "name": "where",
            "label": tr("Where"),
            "field": "where",
            "align": "left",
            "sortable": True,
        },
        {"name": "quantity", "label": tr("Qty"), "field": "quantity", "align": "right"},
        {"name": "price", "label": tr("Price"), "field": "price", "align": "right"},
        {"name": "value", "label": tr("Value"), "field": "value", "align": "right"},
        {"name": "share", "label": tr("Share"), "field": "share", "align": "right"},
        {"name": "change_24h", "label": "24h", "field": "change_24h", "align": "right"},
        {"name": "change_7d", "label": "7d", "field": "change_7d", "align": "right"},
        {"name": "trend", "label": "", "field": "spark", "align": "center"},
        {"name": "pnl", "label": tr("P&L"), "field": "pnl", "align": "right"},
        {"name": "actions", "label": "", "field": "id", "align": "right"},
    ]


def _row(p: Position) -> dict[str, Any]:
    return {
        "id": p.holding_id,
        "item_id": p.item_id,
        "name": p.name,
        "icon": icon_url(p.icon_url),
        "color": rarity_color(p.rarity, p.quality),
        "spark": sparkline_points(list(p.trend)),
        "spark_up": len(p.trend) < 2 or p.trend[-1] >= p.trend[0],
        "where": tr("Inventory")
        if p.source == repo.SOURCE_INVENTORY
        else (p.location or tr("Manual")),
        "quantity": p.quantity,
        "price": fmt_usd(p.price),
        "value": fmt_usd(p.value),
        "share": "—" if p.share is None else f"{p.share:.1%}",
        "change_24h": fmt_pct(p.change_24h),
        "change_7d": fmt_pct(p.change_7d),
        "pnl": "—" if p.pnl is None else f"{'+' if p.pnl >= 0 else '-'}${abs(p.pnl):,.2f}",
        "source": p.source,
    }


def _value_chart(history: pd.Series, range_key: str) -> dict[str, Any]:
    period = RANGES[range_key]
    if period is not None and not history.empty:
        history = history[history.index >= history.index[-1] - period]
    index = pd.DatetimeIndex(history.index)
    data = [
        [int(ts.timestamp() * 1000), round(float(v), 2)]
        for ts, v in zip(index, history.to_numpy(), strict=True)
    ]
    return {
        "animation": False,
        "backgroundColor": "transparent",
        "tooltip": {"trigger": "axis", ":valueFormatter": "v => '$' + v.toFixed(2)"},
        "grid": {"left": 80, "right": 20, "top": 20, "bottom": 30},
        "xAxis": {"type": "time"},
        "yAxis": {"type": "value", "scale": True, "axisLabel": {":formatter": "v => '$' + v"}},
        "dataZoom": [{"type": "inside"}],
        "series": [
            {
                "name": tr("Value"),
                "type": "line",
                "data": data,
                "showSymbol": False,
                "areaStyle": {"opacity": 0.15},
            }
        ],
    }


def register() -> None:
    @ui.page("/")
    async def portfolio_page() -> None:
        with page_frame("portfolio"):
            with ui.row().classes("w-full items-center"):
                ui.label(tr("Portfolio")).classes("text-h5")
                ui.space()
                ui.button(tr("Add item"), icon="add", on_click=lambda: open_add_dialog())

            banner = ui.column().classes("w-full")

            # --- Totals, movers and chart -------------------------------------------------
            totals = ui.row().classes("gap-3")
            movers = ui.row().classes("w-full gap-3")
            with ui.row().classes("items-center"):
                range_toggle = ui.toggle(range_options(), value="1Y")
            chart = ui.echart({}, theme=echart_theme()).classes("w-full h-72")

            # --- Positions ---------------------------------------------------------------
            ui.label(tr("Positions")).classes("text-h6")
            table = ui.table(columns=columns(with_pnl=False), rows=[], row_key="id").classes(
                "w-full"
            )
            table.add_slot("body-cell-name", NAME_CELL_SLOT)
            table.add_slot("body-cell-trend", SPARK_CELL_SLOT)
            table.add_slot(
                "body-cell-actions",
                '<q-td :props="props">'
                '<q-btn flat dense round icon="edit" '
                "@click.stop=\"$parent.$emit('edit', props.row)\" />"
                "</q-td>",
            )
            table.on("rowClick", lambda e: ui.navigate.to(f"/item/{e.args[1]['item_id']}"))

            portfolio: Portfolio | None = None

            async def refresh() -> None:
                nonlocal portfolio
                portfolio = await load_portfolio(runtime.sessions)
                totals.clear()
                with totals:
                    _stat(tr("Value"), fmt_usd(portfolio.value))
                    _stat(tr("You receive after fees"), fmt_usd(portfolio.proceeds))
                    if portfolio.cost:
                        _stat(
                            tr("Profit / loss"),
                            f"{fmt_usd(portfolio.pnl)} ({portfolio.pnl / portfolio.cost:+.1%})",
                            pct_color(portfolio.pnl),
                            tr("after fees, positions with a purchase price"),
                        )
                    _stat(tr("Positions"), str(len(portfolio.positions)))
                    if portfolio.unpriced:
                        _stat(
                            tr("Waiting for prices"),
                            str(portfolio.unpriced),
                            "",
                            tr("fetched shortly"),
                        )
                table.columns = columns(
                    with_pnl=any(p.pnl is not None for p in portfolio.positions)
                )
                table.rows = [_row(p) for p in portfolio.positions]
                table.update()
                redraw()
                _movers(movers, portfolio)
                await _banner(banner)

            def redraw() -> None:
                if portfolio is None:
                    return
                chart.options.clear()
                chart.options.update(_value_chart(portfolio.value_history, range_toggle.value))
                chart.update()

            async def add_manual(result: SearchResult) -> bool:
                if not await add_position(result.market_hash_name, result):
                    return False
                await refresh()
                return True

            async def edit(event: Any) -> None:
                row = event.args
                async with runtime.sessions() as session:
                    holdings = dict((h.id, h) for h, _ in await repo.active_holdings(session))
                holding = holdings.get(row["id"])
                if holding is None:
                    return
                manual = holding.source == repo.SOURCE_MANUAL
                form = await position_dialog(
                    row["name"],
                    manual=manual,
                    initial=PositionForm(
                        holding.quantity, holding.location, holding.buy_price_usd, holding.bought_at
                    ),
                    deletable=manual,
                )
                if form == DELETE:
                    async with runtime.sessions() as session:
                        await repo.delete_manual_holding(session, holding.id)
                        await session.commit()
                    await refresh()
                    return
                if not isinstance(form, PositionForm):
                    return
                async with runtime.sessions() as session:
                    await repo.update_holding(
                        session,
                        holding.id,
                        quantity=form.quantity,
                        location=form.location,
                        buy_price_usd=form.buy_price_usd,
                        bought_at=form.bought_at,
                    )
                    await session.commit()
                await refresh()

            async def open_add_dialog() -> None:
                with ui.dialog() as dialog, ui.card().classes("w-[48rem] max-w-full"):
                    ui.label(tr("Add items that are not in your inventory")).classes("text-h6")
                    ui.label(
                        tr(
                            "For example, storage unit contents: Steam does not show them in "
                            "the inventory."
                        )
                    ).classes("text-muted")

                    async def pick(result: SearchResult) -> bool:
                        dialog.close()
                        return await add_manual(result)

                    search_box(action_label=tr("Add to portfolio"), on_pick=pick)
                dialog.open()

            table.on("edit", edit)
            range_toggle.on_value_change(redraw)
            await refresh()
            ui.timer(60, refresh)


def _stat(label: str, value: str, color: str = "", hint: str = "") -> None:
    with ui.card().classes("min-w-40"):
        ui.label(label).classes("text-caption text-muted")
        ui.label(value).classes(f"text-h6 {color}")
        if hint:
            ui.label(hint).classes("text-caption text-muted")


def _movers(container: ui.row, portfolio: Portfolio) -> None:
    """Biggest 24h gainers and losers among the positions."""
    container.clear()
    changed = [p for p in portfolio.positions if p.change_24h]
    gainers = sorted((p for p in changed if (p.change_24h or 0) > 0), key=_change, reverse=True)
    losers = sorted((p for p in changed if (p.change_24h or 0) < 0), key=_change)
    with container:
        for title, positions in (
            (tr("Top gainers, 24h"), gainers),
            (tr("Top losers, 24h"), losers),
        ):
            with ui.card().classes("grow min-w-72"):
                ui.label(title).classes("text-caption text-muted")
                if not positions:
                    ui.label("—").classes("text-muted")
                for p in positions[:3]:
                    with ui.row().classes("w-full no-wrap items-center gap-2"):
                        ui.link(p.name, f"/item/{p.item_id}").classes("plain-link grow truncate")
                        ui.label(fmt_pct(p.change_24h)).classes(pct_color(p.change_24h))


def _change(position: Position) -> float:
    return position.change_24h or 0.0


async def _banner(container: ui.column) -> None:
    """A short notice when the inventory is not connected or cannot be read."""
    async with runtime.sessions() as session:
        account = await repo.get_account(session)
    container.clear()
    problem = account is not None and (
        (account.profile is not None and account.profile.privacy != "public")
        or (account.job is not None and account.job.status == "error")
    )
    if account is not None and not problem:
        job = account.job
        if job is not None and account.positions is not None:
            with container:
                ui.label(
                    tr(
                        "Inventory synced {when} · next update {next}",
                        when=time_ago(job.updated_at),
                        next=time_until(job.run_after),
                    )
                ).classes("text-caption text-muted")
        return
    with container, ui.card().classes("w-full"), ui.row().classes("w-full items-center"):
        ui.icon("warning" if problem else "link", color="amber" if problem else None)
        ui.label(
            tr("Your Steam inventory could not be imported. See Settings for details.")
            if problem
            else tr("Connect your Steam inventory to import your items automatically.")
        ).classes("grow")
        ui.button(tr("Open settings"), on_click=lambda: ui.navigate.to("/settings")).props("flat")
