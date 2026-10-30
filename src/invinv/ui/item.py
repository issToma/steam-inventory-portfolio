from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
from fastapi.responses import RedirectResponse
from nicegui import ui

from invinv.analysis import (
    CHANGE_WINDOWS,
    MissingRateError,
    change_since,
    compute_metrics,
    daily_series,
    ema,
    hourly_series,
    price_views,
    remove_outliers,
    robust_prices,
    sma,
    to_frame,
)
from invinv.core.currency import USD, currency_name
from invinv.core.fees import seller_proceeds
from invinv.core.http import SteamBusyError, SteamError
from invinv.core.parsers import OrderBook, SteamFormatError
from invinv.db import repo
from invinv.i18n import tr, tr_n
from invinv.portfolio import fee_rate
from invinv.pricing import Listing, Quote, instant_sale, listing_of, quote, recent_sales_price
from invinv.runtime import runtime
from invinv.search import STEAM_MAX_WAIT
from invinv.ui.alerts_page import open_item_alerts
from invinv.ui.common import fmt_pct, fmt_usd, icon_url, rarity_color, time_ago
from invinv.ui.games import game_names, name_of
from invinv.ui.layout import (
    CHART_FILL,
    CHART_LINE,
    SEGMENTED_PROPS,
    delta_class,
    kpi,
    page_frame,
    themed_chart,
)
from invinv.ui.portfolio import GRID_COLOR
from invinv.ui.positions import add_position

RANGES = {
    "7D": timedelta(days=7),
    "30D": timedelta(days=30),
    "1Y": timedelta(days=365),
    "All": None,
}


def range_options() -> dict[str, str]:
    return {key: tr(key) if key == "All" else key for key in RANGES}


def _select(frame: pd.DataFrame, range_key: str) -> pd.DataFrame:
    """Hourly points for short ranges (when we have them), daily points otherwise."""
    period = RANGES[range_key]
    hourly = hourly_series(frame)
    series = (
        hourly
        if period is not None and period <= timedelta(days=30) and not hourly.empty
        else daily_series(frame)
    )
    if period is not None and not series.empty:
        series = series[series.index >= series.index[-1] - period]
    return series


def _points(series: pd.Series) -> list[list[Any]]:
    index = pd.DatetimeIndex(series.index)
    return [
        [int(ts.timestamp() * 1000), None if pd.isna(v) else round(float(v), 4)]
        for ts, v in zip(index, series.to_numpy(), strict=True)
    ]


def _chart_options(
    series: pd.DataFrame, sma_window: int | None, ema_window: int | None
) -> dict[str, Any]:
    price_tooltip = {":valueFormatter": "v => v == null ? '' : '$' + v.toFixed(2)"}
    lines: list[dict[str, Any]] = [
        {
            "name": tr("Price"),
            "type": "line",
            "data": _points(series["price"]),
            "showSymbol": False,
            "lineStyle": {"width": 1.5, "color": CHART_LINE},
            "itemStyle": {"color": CHART_LINE},
            "areaStyle": {"color": CHART_FILL},
            "tooltip": price_tooltip,
        }
    ]
    if sma_window:
        lines.append(
            {
                "name": f"SMA {sma_window}",
                "type": "line",
                "data": _points(sma(series["price"], sma_window)),
                "showSymbol": False,
                "lineStyle": {"width": 1, "type": "dashed"},
                "tooltip": price_tooltip,
            }
        )
    if ema_window:
        lines.append(
            {
                "name": f"EMA {ema_window}",
                "type": "line",
                "data": _points(ema(series["price"], ema_window)),
                "showSymbol": False,
                "lineStyle": {"width": 1, "type": "dotted"},
                "tooltip": price_tooltip,
            }
        )
    volume = {
        "name": tr("Sales"),
        "type": "bar",
        "xAxisIndex": 1,
        "yAxisIndex": 1,
        "data": _points(series["volume"]),
        "itemStyle": {"color": "#94a3b8"},
    }
    return {
        "backgroundColor": "transparent",
        "legend": {"top": 0},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "grid": [
            {"left": 70, "right": 20, "top": 40, "height": "58%"},
            {"left": 70, "right": 20, "top": "76%", "height": "14%"},
        ],
        "xAxis": [
            {"type": "time", "gridIndex": 0, "axisLabel": {"show": False}},
            {"type": "time", "gridIndex": 1},
        ],
        "yAxis": [
            {
                "type": "value",
                "scale": True,
                "gridIndex": 0,
                "axisLabel": {":formatter": "v => '$' + v"},
                "splitLine": {"lineStyle": {"color": GRID_COLOR}},
            },
            {
                "type": "value",
                "gridIndex": 1,
                "splitNumber": 2,
                "splitLine": {"lineStyle": {"color": GRID_COLOR}},
            },
        ],
        "dataZoom": [{"type": "inside", "xAxisIndex": [0, 1]}],
        "series": [*lines, volume],
    }


def _price_hint(current: Quote, listing: Listing | None) -> str:
    if current.source == "listing" and listing is not None:
        return tr("cheapest listing · {ago}", ago=time_ago(listing.checked_at))
    if listing is not None and listing.lowest_usd is None:
        return tr("nothing listed · median of recent sales")
    return tr("median of recent sales")


# Price levels of each side shown in the order book card.
BOOK_LEVELS = 5


def _order_book_card(
    name: str, appid: int, held: int, fee: float, fx: Mapping[int, Decimal]
) -> None:
    """Buy orders and listings right now, loaded on demand: one request to Steam."""
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center"):
            with ui.column().classes("gap-0"):
                ui.label(tr("Order book")).classes("text-subtitle1")
                ui.label(
                    tr(
                        "What buyers offer right now: selling into the best buy order happens "
                        "at once, a listing waits for a buyer."
                    )
                ).classes("text-caption text-muted")
            ui.space()
            button = ui.button(tr("Load from Steam"), icon="refresh").props("outline")
        content = ui.column().classes("w-full gap-3")

    async def load() -> None:
        button.props("loading")
        try:
            book = await runtime.market.order_book(
                name, appid, priority=True, max_wait=STEAM_MAX_WAIT
            )
        except SteamBusyError:
            ui.notify(tr("Steam is busy right now; try again in a few minutes."), type="warning")
            return
        except SteamError, SteamFormatError:
            ui.notify(tr("Could not reach Steam; try again in a minute."), type="warning")
            return
        finally:
            button.props(remove="loading")
        rate = Decimal(1) if book.currency == USD else fx.get(book.currency)
        content.clear()
        with content:
            if rate is None:
                ui.label(
                    tr(
                        "Steam answered in {currency}, whose rate is not known yet.",
                        currency=currency_name(book.currency),
                    )
                ).classes("text-muted")
                return
            _render_book(book, rate, held, fee)
        button.text = tr("Refresh")

    button.on_click(load)


def _render_book(book: OrderBook, rate: Decimal, held: int, fee: float) -> None:
    def usd(amount: Decimal | None) -> str:
        return fmt_usd(float(amount * rate)) if amount is not None else "—"

    bid = book.best_bid
    with ui.row().classes("w-full items-stretch gap-y-3 kpi-strip"):
        kpi(
            tr("Sell now"),
            usd(bid),
            hint=tr(
                "you receive {amount}",
                amount=fmt_usd(seller_proceeds(round(bid * rate * 100), fee) / 100),
            )
            if bid is not None
            else tr("no buy orders"),
        )
        kpi(tr("Cheapest listing"), usd(book.best_ask))
        if bid is not None and book.best_ask:
            kpi(tr("Spread"), f"{(book.best_ask - bid) / book.best_ask:.1%}")
        kpi(tr("Buy orders"), f"{book.buy_orders:,}")
        kpi(tr("Listings"), f"{book.sell_listings:,}")
    if held:
        sale = instant_sale([(price * rate, quantity) for price, quantity in book.bids], held, fee)
        text = tr(
            "Selling your {n} at once: buyers pay {paid}, you receive {received}.",
            n=held,
            paid=fmt_usd(float(sale.buyers_pay)),
            received=fmt_usd(float(sale.you_receive)),
        )
        if sale.sold < held:
            text += " " + tr("Only {sold} would sell: the buy orders run out.", sold=sale.sold)
        ui.label(text)
    with ui.row().classes("w-full gap-6"):
        for title, levels in ((tr("Buy orders"), book.bids), (tr("Listings"), book.asks)):
            with ui.column().classes("gap-0 min-w-48"):
                ui.label(title).classes("text-caption text-muted")
                for price, quantity in levels[:BOOK_LEVELS]:
                    with ui.row().classes("w-full justify-between gap-6"):
                        ui.label(usd(price))
                        ui.label(tr("{n} pcs", n=f"{quantity:,}")).classes("text-muted")


def register() -> None:
    # Registered before /item/{item_id}, which would otherwise reject "by-name" as an id.
    @ui.page("/item/by-name")
    async def item_by_name(name: str, appid: int) -> RedirectResponse:
        async with runtime.sessions() as session:
            item = await repo.get_or_create_item(session, name, appid)
            await session.commit()
        return RedirectResponse(f"/item/{item.id}")

    @ui.page("/item/{item_id}")
    async def item_page(item_id: int) -> None:
        async with runtime.sessions() as session:
            item = await repo.get_item(session, item_id)
            points = await repo.load_history(session, item_id) if item else []
            fx = await repo.latest_fx(session)
            tracked = await repo.is_tracked(session, item_id) if item else False
            watched = await repo.in_watchlist(session, item_id) if item else False
            held = (await repo.held_quantities(session, [item_id])).get(item_id, 0) if item else 0

        with page_frame("item"):
            if item is None:
                ui.label(tr("Item not found."))
                return
            with ui.row().classes("items-center no-wrap gap-4"):
                image = icon_url(item.icon_url, "256fx192f")
                if image:
                    ui.image(image).classes("w-40 h-28").props("fit=contain")
                with ui.column().classes("gap-1"):
                    color = rarity_color(item.rarity, item.quality, item.name_color, item.appid)
                    ui.label(item.market_hash_name).classes("text-h5").style(
                        f"color: {color}" if color else ""
                    )
                    game = name_of(item.appid, await game_names())
                    traits = " · ".join(
                        t for t in (game, item.item_type, item.exterior, item.collection) if t
                    )
                    if traits:
                        ui.label(traits).classes("text-muted")
                    holding_line = ui.label().classes("text-subtitle2")

            async def toggle_watch() -> None:
                async with runtime.sessions() as session:
                    if watched:
                        await repo.remove_from_watchlist(session, item_id)
                    else:
                        await repo.add_to_watchlist(
                            session, item.market_hash_name, appid=item.appid
                        )
                    await session.commit()
                ui.navigate.reload()

            async def add_to_portfolio() -> None:
                if await add_position(item.market_hash_name, appid=item.appid):
                    ui.navigate.reload()

            with ui.row().classes("gap-2"):
                ui.button(
                    tr("Remove from watchlist") if watched else tr("Add to watchlist"),
                    icon="visibility_off" if watched else "visibility",
                    on_click=toggle_watch,
                ).props(f"outline color={'negative' if watched else 'primary'}")
                ui.button(tr("Add to portfolio"), icon="add", on_click=add_to_portfolio).props(
                    "outline"
                )
                ui.button(
                    tr("Alerts"),
                    icon="add_alert",
                    on_click=lambda: open_item_alerts(item.market_hash_name, item.appid),
                ).props("outline")

            try:
                raw_frame = to_frame(points, fx, drop_outliers=False)
            except MissingRateError as exc:
                ui.label(
                    tr(
                        "Waiting for Steam's {currency} → USD rate; the collector fetches it "
                        "right after the first history download.",
                        currency=currency_name(exc.currency),
                    )
                )
                return
            frame = remove_outliers(raw_frame)
            fee = fee_rate(item)
            metrics = compute_metrics(frame, publisher_fee=fee)
            if metrics is None:
                ui.label(
                    tr(
                        "No price history yet. Either the collector has not fetched it, or the "
                        "item has never been sold on the market."
                    )
                    if tracked
                    else tr(
                        "Prices of this item are not collected yet. Add it to the watchlist or "
                        "the portfolio and its full history arrives within a minute."
                    )
                ).classes("text-muted")
                return

            listing = listing_of(item)
            views = price_views(frame)
            current = quote(listing, recent_sales_price(views), datetime.now(UTC)) or Quote(
                metrics.last_price, "sales"
            )
            if held:
                holding_line.text = tr(
                    "You hold {quantity} · worth {value}",
                    quantity=held,
                    value=fmt_usd(held * current.price),
                )

            now = datetime.now(UTC)
            changes = {
                label: change_since(views.robust, current.price, span, now)
                for label, span in CHANGE_WINDOWS.items()
            }
            median = listing.median_24h_usd if listing else None
            with ui.card().classes("w-full gap-3"):
                # The price and how it moved, then the details in one strip.
                with ui.row().classes("items-center gap-3"):
                    ui.label(fmt_usd(current.price)).classes("hero-value")
                    for label, change in changes.items():
                        ui.label(f"{label} {fmt_pct(change)}").classes(
                            f"delta-pill {delta_class(change)}"
                        )
                ui.label(_price_hint(current, listing)).classes("kpi-label")
                with ui.row().classes("w-full items-stretch gap-y-3 kpi-strip"):
                    kpi(
                        tr("Median sale, 24h"),
                        fmt_usd(float(median)) if median is not None else "—",
                        tooltip=tr_n(listing.sales_24h or 0, "{n} sale", "{n} sales")
                        if listing is not None
                        else "",
                    )
                    kpi(
                        tr("Last sale"),
                        fmt_usd(metrics.last_price),
                        tooltip=time_ago(metrics.last_ts.to_pydatetime()),
                    )
                    kpi(
                        tr("Sales per day (30d)"),
                        "—"
                        if metrics.avg_daily_volume_30d is None
                        else f"{metrics.avg_daily_volume_30d:,.0f}",
                    )
                    kpi(
                        tr("Volatility (30d, daily)"),
                        "—" if metrics.volatility_30d is None else f"{metrics.volatility_30d:.1%}",
                    )
                    kpi(
                        tr("You receive after fee"),
                        fmt_usd(seller_proceeds(round(current.price * 100), fee) / 100),
                    )

            with ui.row().classes("items-center gap-4"):
                range_toggle = (
                    ui.toggle(range_options(), value="30D")
                    .props(SEGMENTED_PROPS)
                    .classes("segmented")
                )
                sma_switch = ui.switch("SMA")
                sma_window = (
                    ui.number(value=20, min=2, max=500, step=1).props("dense").classes("w-20")
                )
                outliers_switch = ui.switch(tr("Hide outliers"), value=True)
                with outliers_switch:
                    ui.tooltip(
                        tr(
                            "Draws the median of about the last 20 sales, so that single odd "
                            "sales (say, a cheap item bought for far more) do not spike the "
                            "chart. Prices and changes always use it."
                        )
                    )
                ema_switch = ui.switch("EMA")
                ema_window = (
                    ui.number(value=20, min=2, max=500, step=1).props("dense").classes("w-20")
                )

            chart = ui.echart({}).classes("w-full h-[520px]")

            def redraw() -> None:
                if outliers_switch.value:
                    series = _select(frame, range_toggle.value)
                    series = series.assign(price=robust_prices(series))
                else:
                    series = _select(raw_frame, range_toggle.value)
                chart.options.clear()
                chart.options.update(
                    themed_chart(
                        _chart_options(
                            series,
                            int(sma_window.value)
                            if sma_switch.value and sma_window.value
                            else None,
                            int(ema_window.value)
                            if ema_switch.value and ema_window.value
                            else None,
                        )
                    )
                )
                chart.update()

            for control in (
                range_toggle,
                sma_switch,
                sma_window,
                ema_switch,
                ema_window,
                outliers_switch,
            ):
                control.on_value_change(redraw)
            redraw()

            last_currency = points[-1].currency
            if last_currency != USD:
                ui.label(
                    tr(
                        "Steam returned prices in {currency}; converted to USD at Steam's own "
                        "rate ({rate} USD per unit).",
                        currency=currency_name(last_currency),
                        rate=f"{fx[last_currency]:.4f}",
                    )
                ).classes("text-caption text-muted")

            _order_book_card(item.market_hash_name, item.appid, held, fee, fx)

            with ui.card().classes("w-full max-w-md"):
                ui.label(tr("Fee calculator")).classes("text-subtitle1")
                price_input = ui.number(
                    tr("Listing price (buyer pays), $"),
                    value=round(current.price, 2),
                    format="%.2f",
                )
                result = ui.label()

                def update_fee() -> None:
                    cents = round((price_input.value or 0) * 100)
                    proceeds = seller_proceeds(cents, fee)
                    result.text = tr(
                        "You receive {proceeds}, fees {fees}",
                        proceeds=fmt_usd(proceeds / 100),
                        fees=fmt_usd((cents - proceeds) / 100),
                    )

                price_input.on_value_change(update_fee)
                update_fee()
