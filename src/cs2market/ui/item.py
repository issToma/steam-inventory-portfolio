from datetime import timedelta
from typing import Any

import pandas as pd
from fastapi.responses import RedirectResponse
from nicegui import ui

from cs2market.analysis import (
    MissingRateError,
    compute_metrics,
    daily_series,
    ema,
    hourly_series,
    remove_outliers,
    sma,
    to_frame,
)
from cs2market.core.currency import USD, currency_name
from cs2market.core.fees import seller_proceeds
from cs2market.core.games import CS2_APPID
from cs2market.db import repo
from cs2market.i18n import tr
from cs2market.portfolio import fee_rate
from cs2market.runtime import runtime
from cs2market.ui.alerts_page import open_item_alerts
from cs2market.ui.common import fmt_pct, fmt_usd, icon_url, pct_color, rarity_color
from cs2market.ui.games import game_names, name_of
from cs2market.ui.layout import SEGMENTED_PROPS, page_frame, themed_chart
from cs2market.ui.portfolio import GRID_COLOR
from cs2market.ui.positions import add_position

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
            "lineStyle": {"width": 1.5},
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
        "animation": False,
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


def _stat(label: str, value: str, color: str = "") -> None:
    with ui.card().classes("min-w-36"):
        ui.label(label).classes("text-caption text-muted")
        ui.label(value).classes(f"text-h6 {color}")


def register() -> None:
    # Registered before /item/{item_id}, which would otherwise reject "by-name" as an id.
    @ui.page("/item/by-name")
    async def item_by_name(name: str, appid: int = CS2_APPID) -> RedirectResponse:
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

            if held:
                holding_line.text = tr(
                    "You hold {quantity} · worth {value}",
                    quantity=held,
                    value=fmt_usd(held * metrics.last_price),
                )

            with ui.row().classes("gap-3"):
                _stat(tr("Price"), fmt_usd(metrics.last_price))
                for label, value in (
                    ("24h", metrics.change_24h),
                    ("7d", metrics.change_7d),
                    ("30d", metrics.change_30d),
                ):
                    _stat(label, fmt_pct(value), pct_color(value))
                _stat(
                    tr("Volatility (30d, daily)"),
                    "—" if metrics.volatility_30d is None else f"{metrics.volatility_30d:.1%}",
                )
                _stat(
                    tr("Sales per day (30d)"),
                    "—"
                    if metrics.avg_daily_volume_30d is None
                    else f"{metrics.avg_daily_volume_30d:,.0f}",
                )
                _stat(tr("You receive after fee"), fmt_usd(metrics.proceeds_after_fee))

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
                            "Hides single odd sales, such as one sticker craft sold far above "
                            "the usual price. Metrics always ignore them."
                        )
                    )
                ema_switch = ui.switch("EMA")
                ema_window = (
                    ui.number(value=20, min=2, max=500, step=1).props("dense").classes("w-20")
                )

            chart = ui.echart({}).classes("w-full h-[520px]")

            def redraw() -> None:
                series = _select(frame if outliers_switch.value else raw_frame, range_toggle.value)
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

            with ui.card().classes("w-full max-w-md"):
                ui.label(tr("Fee calculator")).classes("text-subtitle1")
                price_input = ui.number(
                    tr("Listing price (buyer pays), $"),
                    value=round(metrics.last_price, 2),
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
