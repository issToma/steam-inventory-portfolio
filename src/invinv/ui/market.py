import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any

import pandas as pd
from nicegui import app, ui

from invinv.analysis import MissingRateError, compare_line, price_views, to_frame
from invinv.db import repo
from invinv.i18n import tr, tr_n
from invinv.market import MIN_INDEX_ITEMS, IndexLine, MarketRow, indices, load_rows
from invinv.portfolio import PERIODS, load_portfolio
from invinv.runtime import runtime
from invinv.ui.common import NAME_CELL_SLOT, fmt_pct, icon_url, pct_color, rarity_color
from invinv.ui.games import GameSwitch, game_names, name_of
from invinv.ui.layout import SEGMENTED_PROPS, page_content, themed_chart
from invinv.ui.portfolio import GRID_COLOR, period_options

# More lines than this turn the chart into a tangle.
MAX_COMPARED = 8
DEFAULT_COMPARED = 3
_STORAGE_KEY = "compare_items"


async def _candidates() -> tuple[dict[int, str], dict[int, str | None]]:
    """Items with collected prices, the portfolio's and the watchlist's: their labels by
    name, and their rarity colours."""
    async with runtime.sessions() as session:
        items = {item.id: item for _, item in await repo.active_holdings(session)}
        items |= {item.id: item for item in await repo.watchlist_items(session)}
    names = await game_names()
    ordered = sorted(items.values(), key=lambda i: i.market_hash_name)
    labels = {
        item.id: f"{item.market_hash_name} · {name_of(item.appid, names)}" for item in ordered
    }
    colors = {
        item.id: rarity_color(item.rarity, item.quality, item.name_color, item.appid)
        for item in ordered
    }
    return labels, colors


class _ItemSelect(ui.select):
    """A select whose options carry their item's rarity colour, for the option slot."""

    def __init__(self, colors: Mapping[int, str | None], **kwargs: Any) -> None:
        self._colors = colors
        super().__init__(**kwargs)

    def _update_options(self) -> None:
        super()._update_options()
        for option, value in zip(self._props["options"], self._values, strict=True):
            option["color"] = self._colors.get(int(value))


# An option: a check when chosen, the name in its rarity colour, the game dimmed.
_OPTION_SLOT = """
<q-item v-bind="props.itemProps">
  <q-item-section side style="min-width: 28px">
    <q-icon v-if="props.selected" name="check" color="primary" size="xs" />
  </q-item-section>
  <q-item-section>
    <q-item-label>
      <span :style="props.opt.color ? {color: props.opt.color} : {}">
        {{ props.opt.label.split(' · ')[0] }}</span>
      <span class="text-muted" style="font-size: 0.8em">
        &nbsp;{{ props.opt.label.split(' · ')[1] || '' }}</span>
    </q-item-label>
  </q-item-section>
</q-item>
"""


def _chip(label: str, color: str | None, on_remove: Callable[[], Awaitable[None]]) -> None:
    """A chosen item: its name in its rarity colour on a faint wash of the same colour."""
    name, _, game = label.partition(" · ")
    tint = color or "var(--muted)"
    with ui.row().classes("rarity-chip items-center no-wrap gap-1").style(f"--chip: {tint}"):
        ui.label(name).classes("rarity-chip-name")
        if game:
            ui.label(game).classes("rarity-chip-game")
        ui.button(icon="close", on_click=on_remove).props("flat round dense size=xs")


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
                "name": names[item_id],
                "type": "line",
                "showSymbol": False,
                "data": [
                    [int(ts.timestamp() * 1000), round(float(v), 2)]
                    for ts, v in zip(index, line.to_numpy(), strict=True)
                ],
            }
        )
    return {
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


def _rating_columns() -> list[dict[str, Any]]:
    return [
        {"name": "name", "label": tr("Item"), "field": "name", "align": "left", "sortable": True},
        {
            "name": "segment",
            "label": tr("Segment"),
            "field": "segment",
            "align": "left",
            "sortable": True,
        },
        {
            "name": "price",
            "label": tr("Price"),
            "field": "price",
            "align": "right",
            "sortable": True,
            ":format": "v => v == null ? '—' : '$' + v.toFixed(2)",
        },
        {
            "name": "change",
            "label": tr("Change"),
            "field": "change",
            "align": "right",
            "sortable": True,
            ":format": "v => v == null ? '—' : (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%'",
        },
        {
            "name": "sales",
            "label": tr("Sales/day"),
            "field": "sales",
            "align": "right",
            "sortable": True,
            ":format": "v => v == null ? '—' : v.toFixed(0)",
        },
    ]


def _rating_rows(rows: Sequence[MarketRow], period_key: str) -> list[dict[str, Any]]:
    return [
        {
            "id": r.item.id,
            "appid": r.item.appid,
            "name": r.item.market_hash_name,
            "icon": icon_url(r.item.icon_url),
            "color": rarity_color(r.item.rarity, r.item.quality, r.item.name_color, r.item.appid),
            "segment": r.segment or "—",
            "price": r.price,
            "change": r.changes.get(period_key),
            "sales": r.sales_per_day,
        }
        for r in rows
    ]


def _index_chart(lines: Sequence[IndexLine], names: Mapping[int, str]) -> dict[str, Any]:
    labelled = {i: pd.Series(line.line) for i, line in enumerate(lines[:MAX_COMPARED])}
    titles = {
        i: f"{name_of(line.appid, dict(names))} · {line.segment} ({line.items})"
        for i, line in enumerate(lines[:MAX_COMPARED])
    }
    return _chart(labelled, titles)


async def content() -> None:
    """The Market page's content (in the app shell, see ui.shell)."""
    with page_content():
        with ui.row().classes("w-full items-center"):
            with ui.column().classes("gap-0"):
                ui.label(tr("Market")).classes("text-h5")
                ui.label(tr("How the prices of your items compare and move.")).classes("text-muted")
            ui.space()
            period_toggle = (
                ui.toggle(period_options(), value="30D").props(SEGMENTED_PROPS).classes("segmented")
            )

        # --- Compare (F10) -----------------------------------------------------------
        with ui.card().classes("w-full"):
            ui.label(tr("Compare")).classes("text-h6")
            ui.label(
                tr(
                    "Each line is the item's price as a percentage of where it stood at the "
                    "start of the period (the median of its recent sales, so single odd "
                    "trades do not count)."
                )
            ).classes("text-caption text-muted")
            candidates, colors = await _candidates()
            picker = (
                _ItemSelect(
                    colors,
                    options=candidates,
                    multiple=True,
                    with_input=True,
                    label=tr(
                        "Add items from your portfolio and watchlist (up to {n})", n=MAX_COMPARED
                    ),
                    value=await _default_selection(candidates),
                )
                # The chosen items show as the chips below rather than inside the field.
                .props(":display-value=\"''\" popup-content-class=item-menu")
                .classes("w-full")
            )
            picker.add_slot("option", _OPTION_SLOT)
            chips = ui.row().classes("w-full gap-2")
            chart = ui.echart({}).classes("w-full h-[460px]")

        # --- Rating (F8) -------------------------------------------------------------
        with ui.card().classes("w-full"):
            ui.label(tr("Rating")).classes("text-h6")
            ui.label(
                tr(
                    "Your portfolio and watchlist items. Sort by change for the top gainers "
                    "and losers of the period, by sales for the most traded."
                )
            ).classes("text-caption text-muted")
            games = GameSwitch(on_change=lambda: show_rating())
            table = ui.table(
                columns=_rating_columns(),
                rows=[],
                row_key="id",
                pagination={"rowsPerPage": 25, "sortBy": "change", "descending": True},
            ).classes("w-full")
            table.add_slot("body-cell-name", NAME_CELL_SLOT)
            table.on("rowClick", lambda e: ui.navigate.to(f"/item/{e.args[1]['id']}"))

        # --- Indices (F9) ------------------------------------------------------------
        with ui.card().classes("w-full"):
            ui.label(tr("Indices")).classes("text-h6")
            ui.label(
                tr(
                    "Price movement of the typical (median) item of each segment (game and "
                    "rarity) with at least {n} of your items, at 100% at the start of the "
                    "period.",
                    n=MIN_INDEX_ITEMS,
                )
            ).classes("text-caption text-muted")
            index_chart = ui.echart({}).classes("w-full h-[400px]")
            index_list = ui.column().classes("w-full gap-1")

        names = await game_names()
        rows = await load_rows(runtime.sessions, datetime.now(UTC))
        games.set_games((r.item.appid for r in rows), names)

        async def redraw() -> None:
            chosen = [int(i) for i in (picker.value or [])]
            if len(chosen) > MAX_COMPARED:
                chosen = chosen[:MAX_COMPARED]
                picker.value = chosen
                ui.notify(tr("Up to {n} items at once.", n=MAX_COMPARED), type="warning")
                return  # the value change above redraws
            app.storage.user[_STORAGE_KEY] = chosen
            chips.clear()
            with chips:
                for item_id in chosen:
                    _chip(candidates[item_id], colors.get(item_id), partial(remove, item_id))
            async with runtime.sessions() as session:
                points = await repo.load_histories(session, chosen)
                fx = await repo.latest_fx(session)
            lines = await asyncio.to_thread(
                _lines, points, fx, period_toggle.value, datetime.now(UTC)
            )
            chart.options.clear()
            titles = {i: candidates[i].split(" · ")[0] for i in lines}
            chart.options.update(themed_chart(_chart(lines, titles)))
            chart.update()

        async def remove(item_id: int) -> None:
            picker.value = [i for i in picker.value or [] if int(i) != item_id]

        async def show_rating() -> None:
            shown = [r for r in rows if games.appid in (None, r.item.appid)]
            table.rows = _rating_rows(shown, period_toggle.value)
            table.update()

        async def show_indices() -> None:
            lines = await asyncio.to_thread(
                indices, rows, PERIODS[period_toggle.value], datetime.now(UTC)
            )
            index_chart.options.clear()
            index_chart.options.update(themed_chart(_index_chart(lines, names)))
            index_chart.update()
            index_list.clear()
            with index_list:
                if not lines:
                    ui.label(tr("Not enough items in any segment yet.")).classes("text-muted")
                for line in lines:
                    with ui.row().classes("w-full items-center no-wrap gap-3"):
                        ui.label(f"{name_of(line.appid, names)} · {line.segment}").classes("grow")
                        ui.label(tr_n(line.items, "{n} item", "{n} items")).classes("text-muted")
                        ui.label(fmt_pct(line.change)).classes(
                            f"w-20 text-right {pct_color(line.change)}"
                        )

        async def period_changed() -> None:
            await show_rating()
            await redraw()
            await show_indices()

        picker.on_value_change(redraw)
        period_toggle.on_value_change(period_changed)
        await show_rating()
        await redraw()
        await show_indices()
