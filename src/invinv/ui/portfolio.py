from typing import Any

import pandas as pd
from nicegui import ui

from invinv.core.parsers import SearchResult
from invinv.db import repo
from invinv.i18n import tr, tr_n
from invinv.portfolio import (
    PERIODS,
    PeriodStats,
    Portfolio,
    Position,
    load_portfolio,
    period_stats,
)
from invinv.runtime import runtime
from invinv.ui.common import (
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
from invinv.ui.games import GameSwitch, game_names, searchable_games
from invinv.ui.layout import SEGMENTED_PROPS, kpi, page_frame, themed_chart
from invinv.ui.positions import DELETE, PositionForm, add_position, position_dialog
from invinv.ui.search_box import search_box

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
        _number("quantity", tr("Qty"), _COUNT),
        _number("price", tr("Price"), _USD),
        _number("value", tr("Value"), _USD),
        _number("share", tr("Share"), _PERCENT),
        _number("change_24h", "24h", _SIGNED_PERCENT, colored=True),
        _number("change_7d", "7d", _SIGNED_PERCENT, colored=True),
        {"name": "trend", "label": "", "field": "spark", "align": "center"},
        _number("pnl", tr("P&L"), _SIGNED_USD, colored=True),
        {"name": "pin", "label": "", "field": "pinned", "align": "center"},
        {"name": "chart", "label": "", "field": "chart_excluded", "align": "center"},
        {"name": "actions", "label": "", "field": "id", "align": "right"},
    ]


# Cells hold numbers, so that columns sort by value; these JavaScript formatters show them.
_COUNT = "v => v == null ? '—' : v.toLocaleString('en-US')"
_USD = (
    "v => v == null ? '—' : '$' + v.toLocaleString('en-US', "
    "{minimumFractionDigits: 2, maximumFractionDigits: 2})"
)
_SIGNED_USD = (
    "v => v == null ? '—' : (v < 0 ? '-$' : '+$') + Math.abs(v).toLocaleString('en-US', "
    "{minimumFractionDigits: 2, maximumFractionDigits: 2})"
)
_PERCENT = "v => v == null ? '—' : (v * 100).toFixed(1) + '%'"
_SIGNED_PERCENT = "v => v == null ? '—' : (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%'"


def _number(name: str, label: str, formatter: str, *, colored: bool = False) -> dict[str, Any]:
    column = {
        "name": name,
        "label": label,
        "field": name,
        "align": "right",
        "sortable": True,
        ":format": formatter,
    }
    if colored:
        column[":classes"] = (
            f"row => row.{name} > 0 ? 'text-positive' : row.{name} < 0 ? 'text-negative' : ''"
        )
    return column


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
        "price": p.price,
        "value": p.value,
        "share": p.share,
        "change_24h": p.change_24h,
        "change_7d": p.change_7d,
        "pnl": p.pnl,
        "source": p.source,
        "pinnable": p.source == repo.SOURCE_INVENTORY,
        "pinned": p.pinned,
        "chart_excluded": p.chart_excluded,
        "chart_tip": tr("Count it in the value chart again")
        if p.chart_excluded
        else tr("Leave it out of the value chart"),
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

            # --- Value now, its change over the chosen period, and the chart ------------
            with ui.card().classes("w-full gap-4"):
                with ui.row().classes("w-full items-start no-wrap"):
                    with ui.column().classes("gap-1"):
                        ui.label(tr("Portfolio value")).classes("text-caption text-muted")
                        with ui.row().classes("items-center gap-3"):
                            hero_value = ui.label().classes("hero-value")
                            hero_value.set_visibility(False)
                            hero_skeleton = ui.skeleton(width="12rem", height="2.6rem")
                            hero_change = ui.label().classes("delta-pill")
                            hero_change.set_visibility(False)
                    ui.space()
                    period_toggle = (
                        ui.toggle(period_options(), value="30D")
                        .props(SEGMENTED_PROPS)
                        .classes("segmented")
                    )
                # One strip of figures: the totals now, then the period's range.
                with ui.row().classes("w-full items-stretch gap-y-3 kpi-strip"):
                    totals = ui.row().classes("items-stretch gap-0 no-wrap")
                    with totals:
                        for _ in range(3):
                            ui.skeleton(width="8rem", height="2.6rem").classes("mx-4")
                    ui.space()
                    with ui.row().classes("items-stretch gap-0 no-wrap"):
                        with ui.column().classes("kpi justify-center"):
                            period_caption = ui.label(
                                tr("For the period · {period}", period=period_toggle.value)
                            ).classes("kpi-label text-primary")
                            period_since = ui.label().classes("kpi-label")
                        stats = ui.row().classes("items-stretch gap-0 no-wrap")
                chart_skeleton = ui.skeleton(height="18rem").classes("w-full")
                chart = ui.echart({}).classes("w-full h-72")
                chart.set_visibility(False)
                with ui.row().classes("items-center gap-2 no-wrap") as start_note:
                    ui.icon("info", size="xs").classes("text-muted")
                    start_text = ui.label().classes("text-caption text-muted")
                    ui.button(
                        tr("Leave it out of the chart"), on_click=lambda: exclude_newest()
                    ).props("flat dense no-caps size=sm color=primary")
                start_note.set_visibility(False)
                with ui.row().classes("items-center gap-2 no-wrap") as excluded_note:
                    ui.icon("hide_source", size="xs").classes("text-orange")
                    excluded_text = ui.label().classes("text-caption text-muted")
                    ui.button(tr("Count them all again"), on_click=lambda: include_all()).props(
                        "flat dense no-caps size=sm color=primary"
                    )
                excluded_note.set_visibility(False)
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
                ui.table(
                    columns=columns(with_pnl=False),
                    rows=[],
                    row_key="id",
                    # Hundreds of rows with icons and trend lines are slow to draw at once.
                    pagination={"rowsPerPage": 50, "sortBy": None},
                )
                .classes("w-full")
                .props(":rows-per-page-options=[25,50,100,0] loading")
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
            table.add_slot(
                "body-cell-chart",
                '<q-td :props="props">'
                '<q-btn flat dense round size="sm" '
                ":icon=\"props.row.chart_excluded ? 'hide_source' : 'show_chart'\" "
                ":color=\"props.row.chart_excluded ? 'orange' : 'grey'\" "
                "@click.stop=\"$parent.$emit('chart', props.row)\">"
                "<q-tooltip>{{ props.row.chart_tip }}</q-tooltip></q-btn>"
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
                hero_value.text = fmt_usd(portfolio.value)
                hero_value.set_visibility(True)
                hero_skeleton.set_visibility(False)
                totals.clear()
                with totals:
                    kpi(tr("You receive after fees"), fmt_usd(portfolio.proceeds))
                    if portfolio.cost:
                        kpi(
                            tr("Profit / loss"),
                            f"{fmt_usd(portfolio.pnl)} ({portfolio.pnl / portfolio.cost:+.1%})",
                            pct_color(portfolio.pnl),
                            tr("after fees, positions with a purchase price"),
                        )
                    kpi(tr("Positions"), f"{len(portfolio.positions):,}")
                    if portfolio.unpriced:
                        kpi(tr("Waiting for prices"), f"{portfolio.unpriced:,}")
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
                span = PERIODS[period]
                # The period reaches back before the chart starts (always so for All).
                cut = not history.empty and (
                    span is None or history.index[0] > history.index[-1] - span
                )
                period_since.text = (
                    tr(
                        "since {date}, when the last of your items came to the market",
                        date=f"{history.index[0]:%Y-%m-%d}",
                    )
                    if cut
                    else ""
                )
                start_note.set_visibility(cut and portfolio.newest_item is not None)
                excluded_note.set_visibility(portfolio.chart_excluded > 0)
                excluded_text.text = tr_n(
                    portfolio.chart_excluded,
                    "{n} item is left out of the chart and the period stats.",
                    "{n} items are left out of the chart and the period stats.",
                )
                if portfolio.newest_item and not history.empty:
                    start_text.text = tr(
                        "The chart starts on {date}: that day your newest item, {item}, first "
                        "sold. Before it, the portfolio as it is today did not exist.",
                        date=f"{history.index[0]:%Y-%m-%d}",
                        item=portfolio.newest_item,
                    )
                change = period_stats(portfolio.value_history, PERIODS[period])
                _period_stats(stats, change)
                _delta_pill(hero_change, change)
                _movers(movers, portfolio, period)

            async def add_manual(result: SearchResult) -> bool:
                if not await add_position(result.market_hash_name, result, appid=result.appid):
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

            async def toggle_chart(event: Any) -> None:
                row = event.args
                async with runtime.sessions() as session:
                    await repo.set_chart_excluded(session, row["id"], not row["chart_excluded"])
                    await session.commit()
                await refresh()

            async def exclude_newest() -> None:
                if portfolio is None or portfolio.newest_holding_id is None:
                    return
                async with runtime.sessions() as session:
                    await repo.set_chart_excluded(session, portfolio.newest_holding_id, True)
                    await session.commit()
                ui.notify(tr("{item} is left out of the chart", item=portfolio.newest_item))
                await refresh()

            async def include_all() -> None:
                async with runtime.sessions() as session:
                    await repo.include_all_in_chart(session)
                    await session.commit()
                await refresh()

            table.on("edit", edit)
            table.on("pin", toggle_pin)
            table.on("chart", toggle_chart)
            period_toggle.on_value_change(redraw)
            # Show the page at once with placeholders; the data follows a moment later.
            ui.timer(0.05, refresh, once=True)
            ui.timer(60, refresh)


def _delta_pill(pill: ui.label, stats: PeriodStats | None) -> None:
    """The period's change beside the portfolio's value: a tinted pill with an arrow."""
    if stats is None:
        pill.set_visibility(False)
        return
    up = stats.change >= 0
    text = f"{'▲' if up else '▼'} {fmt_usd(abs(stats.change))}"
    if stats.change_pct is not None:
        text += f" · {stats.change_pct:+.1%}"
    pill.text = text
    pill.classes(remove="delta-up delta-down", add="delta-up" if up else "delta-down")
    pill.set_visibility(True)


def _period_stats(container: ui.row, stats: PeriodStats | None) -> None:
    container.clear()
    if stats is None:
        return
    with container:
        kpi(tr("From"), fmt_usd(stats.start))
        kpi(tr("Highest"), fmt_usd(stats.high), tooltip=f"{stats.high_on:%Y-%m-%d}")
        kpi(tr("Lowest"), fmt_usd(stats.low), tooltip=f"{stats.low_on:%Y-%m-%d}")


def _movers(container: ui.row, portfolio: Portfolio, period: str) -> None:
    """Positions that gained or lost the most money over the selected period.

    Ranked by dollars rather than percent: a cent item going from $0.01 to $0.03 is +200%
    but hardly moves the portfolio.
    """
    container.clear()
    changed = []
    for p in portfolio.positions:
        change = p.change(period)
        if p.value and change is not None and change > -1:
            changed.append((p, change, p.value - p.value / (1 + change)))
    gainers = sorted((x for x in changed if x[2] > 0), key=lambda x: -x[2])
    losers = sorted((x for x in changed if x[2] < 0), key=lambda x: x[2])
    label = tr("all time") if period == "All" else period
    with container:
        for title, entries in (
            (tr("Top gainers, {period}", period=label), gainers),
            (tr("Top losers, {period}", period=label), losers),
        ):
            with ui.card().classes("grow min-w-72"):
                ui.label(title).classes("text-caption text-primary")
                if not entries:
                    ui.label("—").classes("text-muted")
                for p, change, dollars in entries[:MOVERS_SHOWN]:
                    _mover(p, change, dollars)
                more = entries[MOVERS_SHOWN:MOVERS_MAX]
                if more:
                    with (
                        ui.expansion(tr("{n} more", n=len(more)))
                        .classes("w-full")
                        .props("dense header-class=text-muted")
                    ):
                        for p, change, dollars in more:
                            _mover(p, change, dollars)


# Top movers shown at once, and in all with the rest unfolded.
MOVERS_SHOWN = 3
MOVERS_MAX = 10


def _mover(p: Position, change: float, dollars: float) -> None:
    with ui.row().classes("w-full no-wrap items-center gap-2"):
        color = rarity_color(p.rarity, p.quality, p.name_color, p.appid)
        ui.link(p.name, f"/item/{p.item_id}").classes("plain-link grow truncate").style(
            f"color: {color}" if color else ""
        )
        sign = "+" if dollars > 0 else "-"
        ui.label(f"{sign}{fmt_usd(abs(dollars))} ({fmt_pct(change)})").classes(
            f"{pct_color(change)} whitespace-nowrap"
        )


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
    private = account is not None and (
        (account.profile is not None and account.profile.privacy != "public")
        or (
            account.job is not None
            and account.job.status == "error"
            and "not public" in (account.job.last_error or "")
        )
    )
    if private:
        # A deliberate choice as often as not (pinned positions stay): say it quietly.
        with container, ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("lock", size="xs").classes("text-muted")
            ui.label(
                tr(
                    "Your inventory is private: positions stay as they are (pinned ones keep "
                    "their quantity), new items are not imported."
                )
            ).classes("text-caption text-muted")
            ui.link(tr("Settings"), "/settings").classes("text-caption")
        return
    with container, ui.card().classes("w-full"), ui.row().classes("w-full items-center"):
        ui.icon("warning" if problem else "link", color="amber" if problem else None)
        ui.label(
            tr("Your Steam inventory could not be imported. See Settings for details.")
            if problem
            else tr("Connect your Steam inventory to import your items automatically.")
        ).classes("grow")
        ui.button(tr("Open settings"), on_click=lambda: ui.navigate.to("/settings")).props("flat")
