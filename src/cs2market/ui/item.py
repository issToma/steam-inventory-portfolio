from datetime import timedelta
from typing import Any

import pandas as pd
from nicegui import ui

from cs2market.analysis import (
    MissingRateError,
    compute_metrics,
    daily_series,
    ema,
    hourly_series,
    sma,
    to_frame,
)
from cs2market.core.currency import USD, currency_name
from cs2market.core.fees import seller_proceeds
from cs2market.db import repo
from cs2market.i18n import tr
from cs2market.runtime import runtime
from cs2market.ui.common import fmt_pct, fmt_usd, pct_color
from cs2market.ui.layout import echart_theme, page_frame

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
            },
            {"type": "value", "gridIndex": 1, "splitNumber": 2},
        ],
        "dataZoom": [{"type": "inside", "xAxisIndex": [0, 1]}],
        "series": [*lines, volume],
    }


def _stat(label: str, value: str, color: str = "") -> None:
    with ui.card().classes("min-w-36"):
        ui.label(label).classes("text-caption text-muted")
        ui.label(value).classes(f"text-h6 {color}")


def register() -> None:
    @ui.page("/item/{item_id}")
    async def item_page(item_id: int) -> None:
        async with runtime.sessions() as session:
            item = await repo.get_item(session, item_id)
            points = await repo.load_history(session, item_id) if item else []
            fx = await repo.latest_fx(session)

        with page_frame("item"):
            if item is None:
                ui.label(tr("Item not found."))
                return
            ui.label(item.market_hash_name).classes("text-h5")
            traits = " · ".join(
                t for t in (item.quality, item.rarity, item.item_type, item.exterior) if t
            )
            if traits:
                ui.label(traits).classes("text-muted")

            try:
                frame = to_frame(points, fx)
            except MissingRateError as exc:
                ui.label(
                    tr(
                        "Waiting for Steam's {currency} → USD rate; the collector fetches it "
                        "right after the first history download.",
                        currency=currency_name(exc.currency),
                    )
                )
                return
            metrics = compute_metrics(frame)
            if metrics is None:
                ui.label(
                    tr(
                        "No price history yet. Either the collector has not fetched it, or the "
                        "item has never been sold on the market."
                    )
                )
                return

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
                range_toggle = ui.toggle(range_options(), value="30D")
                sma_switch = ui.switch("SMA")
                sma_window = (
                    ui.number(value=20, min=2, max=500, step=1).props("dense").classes("w-20")
                )
                ema_switch = ui.switch("EMA")
                ema_window = (
                    ui.number(value=20, min=2, max=500, step=1).props("dense").classes("w-20")
                )

            chart = ui.echart({}, theme=echart_theme()).classes("w-full h-[520px]")

            def redraw() -> None:
                series = _select(frame, range_toggle.value)
                chart.options.clear()
                chart.options.update(
                    _chart_options(
                        series,
                        int(sma_window.value) if sma_switch.value and sma_window.value else None,
                        int(ema_window.value) if ema_switch.value and ema_window.value else None,
                    )
                )
                chart.update()

            for control in (range_toggle, sma_switch, sma_window, ema_switch, ema_window):
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
                    proceeds = seller_proceeds(cents)
                    result.text = tr(
                        "You receive {proceeds}, fees {fees}",
                        proceeds=fmt_usd(proceeds / 100),
                        fees=fmt_usd((cents - proceeds) / 100),
                    )

                price_input.on_value_change(update_fee)
                update_fee()

            async with runtime.sessions() as session:
                watched = await repo.in_watchlist(session, item_id)

            async def toggle_watch() -> None:
                async with runtime.sessions() as session:
                    if watched:
                        await repo.remove_from_watchlist(session, item_id)
                    else:
                        await repo.add_to_watchlist(session, item.market_hash_name)
                    await session.commit()
                ui.navigate.reload()

            ui.button(
                tr("Remove from watchlist") if watched else tr("Add to watchlist"),
                on_click=toggle_watch,
            ).props(f"flat color={'negative' if watched else 'primary'}")
