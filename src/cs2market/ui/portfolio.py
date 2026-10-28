from datetime import timedelta
from typing import Any

import pandas as pd
from nicegui import ui

from cs2market.core.parsers import SearchResult
from cs2market.db import repo
from cs2market.i18n import tr
from cs2market.portfolio import (
    PERIODS,
    PeriodStats,
    Portfolio,
    Position,
    load_portfolio,
    period_stats,
)
from cs2market.runtime import runtime
from cs2market.ui.common import (
    NAME_CELL_SLOT,
    SPARK_CELL_SLOT,
    fmt_pct,
    fmt_usd,
    icon_url,
    pct_color,
    rarity_color,
    span,
    sparkline_points,
    time_ago,
    time_until,
)
from cs2market.ui.games import GameSwitch, game_names, searchable_games
from cs2market.ui.layout import SEGMENTED_PROPS, page_frame, themed_chart
from cs2market.ui.positions import DELETE, PositionForm, add_position, position_dialog
from cs2market.ui.search_box import search_box

# Faint grid lines that work on both dark and light backgrounds.
GRID_COLOR = "rgba(128, 128, 128, 0.18)"


def period_options() -> dict[str, str]:
    return {key: tr(key) if key == "All" else key for key in PERIODS}


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
        {"name": "pin", "label": "", "field": "pinned", "align": "center"},
        {"name": "actions", "label": "", "field": "id", "align": "right"},
    ]


def _row(p: Position) -> dict[str, Any]:
    return {
        "id": p.holding_id,
        "item_id": p.item_id,
        "name": p.name,
        "icon": icon_url(p.icon_url),
        "color": rarity_color(p.rarity, p.quality, p.name_color, p.appid),
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
        "pinnable": p.source == repo.SOURCE_INVENTORY,
        "pinned": p.pinned,
    }


def _value_chart(history: pd.Series, period_key: str) -> dict[str, Any]:
    period = PERIODS[period_key]
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
        "yAxis": {
            "type": "value",
            "scale": True,
            "axisLabel": {":formatter": "v => '$' + v"},
            "splitLine": {"lineStyle": {"color": GRID_COLOR}},
        },
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
            games = GameSwitch(on_change=lambda: refresh())

            # --- Totals now and for the period chosen in the card's header -------------
            # The period cards carry the period in their caption and the toggle's colour, so
            # it is clear that they, the chart and the movers follow the switch.
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center"):
                    ui.label(tr("Portfolio value")).classes("text-subtitle1")
                    ui.space()
                    period_toggle = (
                        ui.toggle(period_options(), value="30D")
                        .props(SEGMENTED_PROPS)
                        .classes("segmented")
                    )
                # Side by side on wide screens; the period group wraps below on narrow ones.
                # Each group's share of the row follows its number of cards (_fit_cards).
                with ui.row().classes("w-full gap-x-10 gap-y-4 items-stretch"):
                    with ui.column().classes("gap-1") as now_group:
                        ui.label(tr("Now")).classes("text-overline text-muted")
                        totals = ui.row().classes("w-full gap-3 items-stretch no-wrap")
                        with totals:
                            for _ in range(3):
                                ui.skeleton(height="6.75rem").classes("flex-1")
                        _fit_cards(now_group, 3)
                    with ui.column().classes("gap-1") as period_group:
                        _fit_cards(period_group, 3)
                        with ui.row().classes("items-baseline gap-2"):
                            period_caption = ui.label().classes("text-overline text-primary")
                            period_since = ui.label().classes("text-caption text-muted")
                        stats = ui.row().classes("w-full gap-3 items-stretch no-wrap")
                chart_skeleton = ui.skeleton(height="18rem").classes("w-full")
                chart = ui.echart({}).classes("w-full h-72")
                chart.set_visibility(False)
            movers = ui.row().classes("w-full gap-3")

            # --- Positions ---------------------------------------------------------------
            with ui.row().classes("w-full items-center"):
                ui.label(tr("Positions")).classes("text-h6")
                ui.space()
                with ui.button(icon="lock").props("flat dense no-caps"):
                    ui.tooltip(
                        tr(
                            "Pinned positions stay when items leave the inventory, "
                            "for example into a storage unit."
                        )
                    )
                    with ui.menu():
                        ui.menu_item(tr("Pin all inventory items"), on_click=lambda: pin_all(True))
                        ui.menu_item(tr("Unpin all"), on_click=lambda: pin_all(False))
            table = (
                ui.table(columns=columns(with_pnl=False), rows=[], row_key="id")
                .classes("w-full")
                .props("loading")
            )
            table.add_slot("body-cell-name", NAME_CELL_SLOT)
            table.add_slot(
                "body-cell-pin",
                '<q-td :props="props">'
                '<q-btn v-if="props.row.pinnable" flat dense round size="sm" '
                ":icon=\"props.row.pinned ? 'lock' : 'lock_open'\" "
                ":color=\"props.row.pinned ? 'amber' : 'grey'\" "
                "@click.stop=\"$parent.$emit('pin', props.row)\" />"
                "</q-td>",
            )
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
                everything = await load_portfolio(runtime.sessions)
                games.set_games((p.appid for p in everything.positions), await game_names())
                portfolio = (
                    everything
                    if games.appid is None
                    else await load_portfolio(runtime.sessions, games.appid)
                )
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
                        status = await runtime.collector_status()
                        _stat(
                            tr("Waiting for prices"),
                            str(portfolio.unpriced),
                            "",
                            tr(
                                "all prices in about {span}",
                                span=span(timedelta(seconds=status.eta_seconds)),
                            )
                            if status.enabled and status.eta_seconds
                            else tr("fetched shortly"),
                        )
                _fit_cards(now_group, len(totals.default_slot.children))
                table.columns = columns(
                    with_pnl=any(p.pnl is not None for p in portfolio.positions)
                )
                table.rows = [_row(p) for p in portfolio.positions]
                table.props(remove="loading")
                table.update()
                chart_skeleton.set_visibility(False)
                chart.set_visibility(True)
                redraw()
                await _banner(banner)

            def redraw() -> None:
                if portfolio is None:
                    return
                period = period_toggle.value
                chart.options.clear()
                chart.options.update(themed_chart(_value_chart(portfolio.value_history, period)))
                chart.update()
                period_caption.text = (
                    tr("For all time")
                    if period == "All"
                    else tr("For the period · {period}", period=period)
                )
                history = portfolio.value_history
                period_since.text = (
                    tr(
                        "since {date}, when the last of your items came to the market",
                        date=f"{history.index[0]:%Y-%m-%d}",
                    )
                    if period == "All" and not history.empty
                    else ""
                )
                _period_stats(stats, period_stats(portfolio.value_history, PERIODS[period]))
                _movers(movers, portfolio, period)

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

                    search_box(
                        action_label=tr("Add to portfolio"),
                        on_pick=pick,
                        games=await searchable_games(),
                    )
                dialog.open()

            async def toggle_pin(event: Any) -> None:
                row = event.args
                async with runtime.sessions() as session:
                    await repo.set_pinned(session, row["id"], not row["pinned"])
                    await session.commit()
                await refresh()

            async def pin_all(pinned: bool) -> None:
                async with runtime.sessions() as session:
                    await repo.pin_all(session, pinned)
                    await session.commit()
                ui.notify(tr("All inventory items pinned") if pinned else tr("All items unpinned"))
                await refresh()

            table.on("edit", edit)
            table.on("pin", toggle_pin)
            period_toggle.on_value_change(redraw)
            # Show the page at once with placeholders; the data follows a moment later.
            ui.timer(0.05, refresh, once=True)
            ui.timer(60, refresh)


def _fit_cards(group: ui.column, cards: int) -> None:
    """Size a group of stat cards by its card count, so no card is squeezed under another."""
    min_width = cards * 9 + (cards - 1) * 0.75  # rem: min-w-36 cards with gap-3 between
    group.style(f"flex: {cards} 1 0; min-width: min({min_width}rem, 100%)")


def _stat(label: str, value: str, color: str = "", hint: str = "", *, period: bool = False) -> None:
    # One height for every card, with or without a hint line, so rows line up.
    with ui.card().classes(
        "flex-1 basis-0 min-w-36 min-h-[6.75rem]" + (" period-card" if period else "")
    ):
        ui.label(label).classes("text-caption text-muted")
        ui.label(value).classes(f"text-h6 {color}")
        if hint:
            ui.label(hint).classes("text-caption text-muted")


def _period_stats(container: ui.row, stats: PeriodStats | None) -> None:
    container.clear()
    if stats is None:
        return
    with container:
        _stat(
            tr("Change"),
            f"{'+' if stats.change >= 0 else '-'}${abs(stats.change):,.2f}"
            + (f" ({stats.change_pct:+.1%})" if stats.change_pct is not None else ""),
            pct_color(stats.change),
            tr("from {value}", value=fmt_usd(stats.start)),
            period=True,
        )
        _stat(tr("Highest"), fmt_usd(stats.high), "", f"{stats.high_on:%Y-%m-%d}", period=True)
        _stat(tr("Lowest"), fmt_usd(stats.low), "", f"{stats.low_on:%Y-%m-%d}", period=True)


def _movers(container: ui.row, portfolio: Portfolio, period: str) -> None:
    """Biggest gainers and losers among the positions over the selected period."""
    container.clear()
    changed = [(p, p.change(period)) for p in portfolio.positions]
    gainers = sorted(((p, c) for p, c in changed if c and c > 0), key=lambda x: -x[1])
    losers = sorted(((p, c) for p, c in changed if c and c < 0), key=lambda x: x[1])
    label = tr("all time") if period == "All" else period
    with container:
        for title, entries in (
            (tr("Top gainers, {period}", period=label), gainers),
            (tr("Top losers, {period}", period=label), losers),
        ):
            # Outlined like the period cards: these follow the period switch too.
            with ui.card().classes("grow min-w-72 period-card"):
                ui.label(title).classes("text-caption text-primary")
                if not entries:
                    ui.label("—").classes("text-muted")
                for p, change in entries[:3]:
                    with ui.row().classes("w-full no-wrap items-center gap-2"):
                        color = rarity_color(p.rarity, p.quality, p.name_color, p.appid)
                        ui.link(p.name, f"/item/{p.item_id}").classes(
                            "plain-link grow truncate"
                        ).style(f"color: {color}" if color else "")
                        ui.label(fmt_pct(change)).classes(pct_color(change))


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
