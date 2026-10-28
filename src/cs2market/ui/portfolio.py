from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from nicegui import ui

from cs2market.core.parsers import SearchResult
from cs2market.db import repo
from cs2market.portfolio import Portfolio, Position, load_portfolio
from cs2market.runtime import runtime
from cs2market.ui.account import AccountCard
from cs2market.ui.common import fmt_pct, fmt_usd, header, pct_color
from cs2market.ui.search_box import search_box

RANGES = {"30D": timedelta(days=30), "1Y": timedelta(days=365), "All": None}

COLUMNS: list[dict[str, Any]] = [
    {"name": "name", "label": "Item", "field": "name", "align": "left", "sortable": True},
    {"name": "where", "label": "Where", "field": "where", "align": "left", "sortable": True},
    {"name": "quantity", "label": "Qty", "field": "quantity", "align": "right"},
    {"name": "price", "label": "Price", "field": "price", "align": "right"},
    {"name": "value", "label": "Value", "field": "value", "align": "right"},
    {"name": "share", "label": "Share", "field": "share", "align": "right"},
    {"name": "change_24h", "label": "24h", "field": "change_24h", "align": "right"},
    {"name": "change_7d", "label": "7d", "field": "change_7d", "align": "right"},
    {"name": "pnl", "label": "P&L", "field": "pnl", "align": "right"},
    {"name": "actions", "label": "", "field": "id", "align": "right"},
]


@dataclass
class PositionForm:
    quantity: int
    location: str | None
    buy_price_usd: Decimal | None
    bought_at: date | None


def _row(p: Position) -> dict[str, Any]:
    return {
        "id": p.holding_id,
        "item_id": p.item_id,
        "name": p.name,
        "where": "Inventory" if p.source == repo.SOURCE_INVENTORY else (p.location or "Manual"),
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
        "tooltip": {"trigger": "axis", ":valueFormatter": "v => '$' + v.toFixed(2)"},
        "grid": {"left": 80, "right": 20, "top": 20, "bottom": 30},
        "xAxis": {"type": "time"},
        "yAxis": {"type": "value", "scale": True, "axisLabel": {":formatter": "v => '$' + v"}},
        "dataZoom": [{"type": "inside"}],
        "series": [
            {
                "name": "Value",
                "type": "line",
                "data": data,
                "showSymbol": False,
                "areaStyle": {"opacity": 0.15},
            }
        ],
    }


DELETE = "delete"


async def _position_dialog(
    title: str, *, manual: bool, initial: PositionForm | None = None, deletable: bool = False
) -> PositionForm | str | None:
    """Ask for position details; quantity and place only for manual positions.

    Returns the form, DELETE when the user deletes the position, or None on cancel.
    """
    initial = initial or PositionForm(1, None, None, None)
    with ui.dialog() as dialog, ui.card().classes("min-w-80"):
        ui.label(title).classes("text-subtitle1")
        if not manual:
            ui.label("Quantity comes from your Steam inventory.").classes(
                "text-caption text-grey-7"
            )
        quantity = ui.number("Quantity", value=initial.quantity, min=1, step=1, format="%d")
        location = ui.input(
            "Where it is kept", value=initial.location or "", placeholder="Storage unit #1"
        )
        quantity.set_visibility(manual)
        location.set_visibility(manual)
        price = ui.number(
            "Purchase price per item, $ (optional)",
            value=float(initial.buy_price_usd) if initial.buy_price_usd is not None else None,
            min=0,
            format="%.2f",
        )
        bought = ui.input(
            "Purchase date (optional, YYYY-MM-DD)",
            value=initial.bought_at.isoformat() if initial.bought_at else "",
        )
        error = ui.label().classes("text-negative text-caption")

        def submit() -> None:
            try:
                buy = Decimal(str(price.value)) if price.value not in (None, "") else None
                day = date.fromisoformat(bought.value) if bought.value else None
            except InvalidOperation, ValueError:
                error.text = "Check the price and the date."
                return
            count = int(quantity.value or 0)
            if count < 1:
                error.text = "Quantity must be at least 1."
                return
            dialog.submit(PositionForm(count, location.value.strip() or None, buy, day))

        with ui.row().classes("w-full items-center"):
            if deletable:
                ui.button("Delete", on_click=lambda: dialog.submit(DELETE)).props(
                    "flat color=negative"
                )
            ui.space()
            ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
            ui.button("Save", on_click=submit)
    result: PositionForm | str | None = await dialog
    dialog.clear()
    return result


def register() -> None:
    @ui.page("/")
    async def portfolio_page() -> None:
        header()
        with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
            ui.label("Portfolio").classes("text-h5")

            account = AccountCard(on_change=lambda: refresh())

            # --- Totals and chart ------------------------------------------------------
            totals = ui.row().classes("gap-3")
            with ui.row().classes("items-center"):
                range_toggle = ui.toggle(list(RANGES), value="1Y")
            chart = ui.echart({}).classes("w-full h-72")

            # --- Positions ---------------------------------------------------------------
            ui.label("Positions").classes("text-h6")
            table = ui.table(columns=COLUMNS, rows=[], row_key="id").classes("w-full")
            table.add_slot(
                "body-cell-actions",
                '<q-td :props="props">'
                '<q-btn flat dense round icon="edit" '
                "@click.stop=\"$parent.$emit('edit', props.row)\" />"
                "</q-td>",
            )
            table.on("rowClick", lambda e: ui.navigate.to(f"/item/{e.args[1]['item_id']}"))

            ui.label("Add items that are not in your inventory").classes("text-h6")
            ui.label(
                "For example, storage unit contents: Steam does not show them in the inventory."
            ).classes("text-grey-7")

            portfolio: Portfolio | None = None

            async def refresh() -> None:
                nonlocal portfolio
                portfolio = await load_portfolio(runtime.sessions)
                totals.clear()
                with totals:
                    _stat("Value", fmt_usd(portfolio.value))
                    _stat("You receive after fees", fmt_usd(portfolio.proceeds))
                    if portfolio.cost:
                        _stat(
                            "Profit / loss",
                            f"{fmt_usd(portfolio.pnl)} ({portfolio.pnl / portfolio.cost:+.1%})",
                            pct_color(portfolio.pnl),
                            "after fees, positions with a purchase price",
                        )
                    _stat("Positions", str(len(portfolio.positions)))
                    if portfolio.unpriced:
                        _stat("Waiting for prices", str(portfolio.unpriced), "", "fetched shortly")
                table.rows = [_row(p) for p in portfolio.positions]
                table.update()
                redraw()
                await account.render()

            def redraw() -> None:
                if portfolio is None:
                    return
                chart.options.clear()
                chart.options.update(_value_chart(portfolio.value_history, range_toggle.value))
                chart.update()

            async def add_manual(result: SearchResult) -> bool:
                form = await _position_dialog(result.market_hash_name, manual=True)
                if not isinstance(form, PositionForm):
                    return False
                async with runtime.sessions() as session:
                    await repo.add_manual_holding(
                        session,
                        result.market_hash_name,
                        quantity=form.quantity,
                        location=form.location,
                        buy_price_usd=form.buy_price_usd,
                        bought_at=form.bought_at,
                        found=result,
                    )
                    await session.commit()
                ui.notify(f"Added {result.market_hash_name}")
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
                form = await _position_dialog(
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

            search_box(action_label="Add to portfolio", on_pick=add_manual)
            table.on("edit", edit)
            range_toggle.on_value_change(redraw)
            await refresh()
            ui.timer(60, refresh)


def _stat(label: str, value: str, color: str = "", hint: str = "") -> None:
    with ui.card().classes("min-w-40"):
        ui.label(label).classes("text-caption text-grey-7")
        ui.label(value).classes(f"text-h6 {color}")
        if hint:
            ui.label(hint).classes("text-caption text-grey-6")
