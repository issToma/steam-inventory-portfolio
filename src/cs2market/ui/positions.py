"""The dialog for adding or editing a portfolio position."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from nicegui import ui

from cs2market.core.games import CS2_APPID
from cs2market.core.parsers import SearchResult
from cs2market.db import repo
from cs2market.i18n import tr
from cs2market.runtime import runtime


@dataclass
class PositionForm:
    quantity: int
    location: str | None
    buy_price_usd: Decimal | None
    bought_at: date | None


DELETE = "delete"


async def position_dialog(
    title: str, *, manual: bool, initial: PositionForm | None = None, deletable: bool = False
) -> PositionForm | str | None:
    """Ask for position details; quantity and place only for manual positions.

    Returns the form, DELETE when the user deletes the position, or None on cancel.
    """
    initial = initial or PositionForm(1, None, None, None)
    with ui.dialog() as dialog, ui.card().classes("min-w-80"):
        ui.label(title).classes("text-subtitle1")
        if not manual:
            ui.label(tr("Quantity comes from your Steam inventory.")).classes(
                "text-caption text-muted"
            )
        quantity = ui.number(tr("Quantity"), value=initial.quantity, min=1, step=1, format="%d")
        location = ui.input(
            tr("Where it is kept"), value=initial.location or "", placeholder=tr("Storage unit #1")
        )
        quantity.set_visibility(manual)
        location.set_visibility(manual)
        price = ui.number(
            tr("Purchase price per item, $ (optional)"),
            value=float(initial.buy_price_usd) if initial.buy_price_usd is not None else None,
            min=0,
            format="%.2f",
        )
        bought = ui.input(
            tr("Purchase date (optional, YYYY-MM-DD)"),
            value=initial.bought_at.isoformat() if initial.bought_at else "",
        )
        error = ui.label().classes("text-negative text-caption")

        def submit() -> None:
            try:
                buy = Decimal(str(price.value)) if price.value not in (None, "") else None
                day = date.fromisoformat(bought.value) if bought.value else None
            except InvalidOperation, ValueError:
                error.text = tr("Check the price and the date.")
                return
            count = int(quantity.value or 0)
            if count < 1:
                error.text = tr("Quantity must be at least 1.")
                return
            dialog.submit(PositionForm(count, location.value.strip() or None, buy, day))

        with ui.row().classes("w-full items-center"):
            if deletable:
                ui.button(tr("Delete"), on_click=lambda: dialog.submit(DELETE)).props(
                    "flat color=negative"
                )
            ui.space()
            ui.button(tr("Cancel"), on_click=lambda: dialog.submit(None)).props("flat")
            ui.button(tr("Save"), on_click=submit)
    result: PositionForm | str | None = await dialog
    dialog.clear()
    return result


async def add_position(
    market_hash_name: str, found: SearchResult | None = None, appid: int = CS2_APPID
) -> bool:
    """Ask for the details of a new manual position and save it. True when saved."""
    form = await position_dialog(market_hash_name, manual=True)
    if not isinstance(form, PositionForm):
        return False
    async with runtime.sessions() as session:
        await repo.add_manual_holding(
            session,
            market_hash_name,
            quantity=form.quantity,
            location=form.location,
            buy_price_usd=form.buy_price_usd,
            bought_at=form.bought_at,
            found=found,
            appid=appid,
        )
        await session.commit()
    ui.notify(tr("Added {name}", name=market_hash_name))
    return True
