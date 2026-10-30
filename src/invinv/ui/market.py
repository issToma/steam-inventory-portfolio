import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from nicegui import app, ui

from invinv.analysis import MissingRateError, compare_line, price_views, to_frame
from invinv.db import repo
from invinv.i18n import tr
from invinv.portfolio import PERIODS, load_portfolio
from invinv.runtime import runtime
from invinv.ui.games import game_names, name_of
from invinv.ui.layout import SEGMENTED_PROPS, page_frame, themed_chart
from invinv.ui.portfolio import GRID_COLOR, period_options

# More lines than this turn the chart into a tangle.
MAX_COMPARED = 8
DEFAULT_COMPARED = 3
_STORAGE_KEY = "compare_items"


async def _candidates() -> dict[int, str]:
    """Items with collected prices: the portfolio's and the watchlist's, by name."""
    async with runtime.sessions() as session:
        items = {item.id: item for _, item in await repo.active_holdings(session)}
        items |= {item.id: item for item in await repo.watchlist_items(session)}
    names = await game_names()
    return {
        item.id: f"{item.market_hash_name} · {name_of(item.appid, names)}"
        for item in sorted(items.values(), key=lambda i: i.market_hash_name)
    }


async def _default_selection(candidates: Mapping[int, str]) -> list[int]:
    """The last comparison of this browser, or else the most valuable positions."""
    saved = app.storage.user.get(_STORAGE_KEY, [])
    chosen = [item_id for item_id in saved if item_id in candidates]
    if chosen:
        return chosen[:MAX_COMPARED]
    portfolio = await load_portfolio(runtime.sessions)
    top = [p.item_id for p in portfolio.positions if p.value]
    return list(dict.fromkeys(top))[:DEFAULT_COMPARED]


def _lines(
    points: Mapping[int, Sequence[repo.StoredPoint]],
    fx: Mapping[int, Decimal],
    period_key: str,
    now: datetime,
) -> dict[int, pd.Series]:
    lines = {}
    for item_id, rows in points.items():
        try:
            frame = to_frame(rows, fx)
        except MissingRateError:
            continue
        if not frame.empty:
            lines[item_id] = compare_line(price_views(frame), PERIODS[period_key], now)
    return lines


def _chart(lines: Mapping[int, pd.Series], names: Mapping[int, str]) -> dict[str, Any]:
    series = []
    for item_id, line in lines.items():
        index = pd.DatetimeIndex(line.index)
        series.append(
            {
                "name": names[item_id].split(" · ")[0],
                "type": "line",
                "showSymbol": False,
                "data": [
                    [int(ts.timestamp() * 1000), round(float(v), 2)]
                    for ts, v in zip(index, line.to_numpy(), strict=True)
                ],
            }
        )
    return {
        "animation": False,
        "backgroundColor": "transparent",
        "tooltip": {"trigger": "axis", ":valueFormatter": "v => v.toFixed(1) + '%'"},
        "legend": {"type": "scroll", "top": 0},
        "grid": {"left": 60, "right": 20, "top": 40, "bottom": 30},
        "xAxis": {"type": "time"},
        "yAxis": {
            "type": "value",
            "scale": True,
            "axisLabel": {":formatter": "v => v + '%'"},
            "splitLine": {"lineStyle": {"color": GRID_COLOR}},
        },
        "dataZoom": [{"type": "inside"}],
        "series": series,
    }


def register() -> None:
    @ui.page("/market")
    async def market_page() -> None:
        with page_frame("market"):
            with ui.column().classes("gap-0"):
                ui.label(tr("Market")).classes("text-h5")
                ui.label(tr("How the prices of your items compare and move.")).classes("text-muted")

            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center"):
                    ui.label(tr("Compare")).classes("text-h6")
                    ui.space()
                    period_toggle = (
                        ui.toggle(period_options(), value="30D")
                        .props(SEGMENTED_PROPS)
                        .classes("segmented")
                    )
                ui.label(
                    tr(
                        "Each line is the item's price as a percentage of where it stood at the "
                        "start of the period (the median of its recent sales, so single odd "
                        "trades do not count)."
                    )
                ).classes("text-caption text-muted")
                candidates = await _candidates()
                picker = (
                    ui.select(
                        candidates,
                        multiple=True,
                        with_input=True,
                        label=tr(
                            "Items from your portfolio and watchlist (up to {n})", n=MAX_COMPARED
                        ),
                        value=await _default_selection(candidates),
                    )
                    .props("use-chips")
                    .classes("w-full")
                )
                chart = ui.echart({}).classes("w-full h-[460px]")

            async def redraw() -> None:
                chosen = [int(i) for i in (picker.value or [])]
                if len(chosen) > MAX_COMPARED:
                    chosen = chosen[:MAX_COMPARED]
                    picker.value = chosen
                    ui.notify(tr("Up to {n} items at once.", n=MAX_COMPARED), type="warning")
                    return  # the value change above redraws
                app.storage.user[_STORAGE_KEY] = chosen
                async with runtime.sessions() as session:
                    points = await repo.load_histories(session, chosen)
                    fx = await repo.latest_fx(session)
                lines = await asyncio.to_thread(
                    _lines, points, fx, period_toggle.value, datetime.now(UTC)
                )
                chart.options.clear()
                chart.options.update(themed_chart(_chart(lines, candidates)))
                chart.update()

            picker.on_value_change(redraw)
            period_toggle.on_value_change(redraw)
            await redraw()
