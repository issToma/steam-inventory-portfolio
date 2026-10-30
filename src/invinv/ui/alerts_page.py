from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from nicegui import ui

from invinv import alerts
from invinv.core.parsers import SearchResult
from invinv.db import repo
from invinv.db.tables import AlertRule
from invinv.i18n import current_language, tr
from invinv.pricing import listing_of
from invinv.runtime import runtime
from invinv.ui.common import icon_url, rarity_color, time_ago
from invinv.ui.games import searchable_games
from invinv.ui.layout import item_url, page_content
from invinv.ui.search_box import search_box


def kind_options() -> dict[str, str]:
    return {
        alerts.KIND_ABOVE: tr("Price rises to"),
        alerts.KIND_BELOW: tr("Price falls to"),
        alerts.KIND_RISE: tr("Price rises by"),
        alerts.KIND_FALL: tr("Price falls by"),
    }


def window_options() -> dict[int, str]:
    return {24: tr("in 24 hours"), 168: tr("in 7 days")}


def describe(rule: AlertRule) -> str:
    return alerts.describe(current_language(), rule.kind, rule.threshold, rule.window_hours)


async def _current_reading(market_hash_name: str, appid: int) -> alerts.Reading | None:
    async with runtime.sessions() as session:
        item = await repo.get_item_by_name(session, market_hash_name, appid)
        if item is None:
            return None
        points = await repo.load_history(session, item.id)
        fx = await repo.latest_fx(session)
    return alerts.reading_from(points, fx, listing_of(item), datetime.now(UTC))


def alert_form(
    market_hash_name: str,
    *,
    appid: int,
    found: SearchResult | None = None,
    on_saved: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """A form that adds an alert rule for one item."""
    with ui.row().classes("w-full items-end gap-3"):
        kind = ui.select(kind_options(), value=alerts.KIND_ABOVE, label=tr("Notify when")).classes(
            "w-48"
        )
        value = ui.number(label="$", min=0, format="%.2f").classes("w-32")
        window = ui.select(window_options(), value=24).classes("w-36")
        save = ui.button(tr("Add alert"), icon="add_alert")

    def update_inputs() -> None:
        by_change = kind.value in alerts.CHANGE_KINDS
        value.props(f"label={'%' if by_change else '$'}")
        value.format = "%.1f" if by_change else "%.2f"
        window.set_visibility(by_change)

    kind.on_value_change(update_inputs)
    update_inputs()

    async def submit() -> None:
        try:
            threshold = Decimal(str(value.value))
        except InvalidOperation:
            threshold = Decimal(0)
        if threshold <= 0:
            ui.notify(tr("Enter a value above zero."), type="warning")
            return
        by_change = kind.value in alerts.CHANGE_KINDS
        window_hours = int(window.value) if by_change else None
        reading = await _current_reading(market_hash_name, appid)
        met_now = reading is not None and alerts.is_met(
            kind.value, float(threshold), window_hours, reading
        )
        async with runtime.sessions() as session:
            await repo.add_alert(
                session,
                market_hash_name,
                kind.value,
                threshold,
                window_hours,
                appid=appid,
                found=found,
                armed=not met_now,
            )
            await session.commit()
        if met_now:
            ui.notify(
                tr("Added. This is already true now; you will be notified the next time it is."),
                multi_line=True,
            )
        else:
            ui.notify(tr("Alert added"))
        value.value = None
        if on_saved is not None:
            await on_saved()

    save.on_click(submit)


async def open_item_alerts(market_hash_name: str, appid: int) -> None:
    """A dialog with an item's alerts and a form for a new one."""
    with ui.dialog() as dialog, ui.card().classes("w-[44rem] max-w-full"):
        ui.label(tr("Alerts for {name}", name=market_hash_name)).classes("text-h6")
        rules = ui.column().classes("w-full gap-1")

        async def render() -> None:
            async with runtime.sessions() as session:
                item = await repo.get_item_by_name(session, market_hash_name, appid)
                found = await repo.alert_rules(session, item.id) if item else []
            rules.clear()
            with rules:
                if not found:
                    ui.label(tr("No alerts for this item yet.")).classes("text-muted")
                for rule, _ in found:
                    _rule_row(rule, render)

        await render()
        ui.separator()
        alert_form(market_hash_name, appid=appid, on_saved=render)
        await _telegram_hint()
    dialog.open()


def _rule_row(rule: AlertRule, on_change: Callable[[], Awaitable[None]]) -> None:
    async def toggle(enabled: bool) -> None:
        async with runtime.sessions() as session:
            await repo.set_alert_enabled(session, rule.id, enabled)
            await session.commit()
        await on_change()

    async def remove() -> None:
        async with runtime.sessions() as session:
            await repo.delete_alert(session, rule.id)
            await session.commit()
        await on_change()

    with ui.row().classes("w-full items-center no-wrap gap-3"):
        ui.label(describe(rule)).classes("grow")
        ui.label(_status(rule)).classes("text-caption text-muted")
        ui.switch(value=rule.enabled, on_change=lambda e: toggle(bool(e.value))).props("dense")
        ui.button(icon="close", on_click=remove).props("flat dense round size=sm color=grey")


def _status(rule: AlertRule) -> str:
    if not rule.enabled:
        return tr("off")
    if rule.last_fired_at is not None:
        return tr("fired {ago}", ago=time_ago(rule.last_fired_at))
    return tr("waiting")


async def _telegram_hint() -> None:
    async with runtime.sessions() as session:
        telegram = await repo.telegram_settings(session)
    if telegram is None:
        with ui.row().classes("items-center gap-2 text-caption text-muted"):
            ui.icon("info", size="xs")
            ui.label(tr("Alerts show up here and under the bell."))
            ui.link(tr("Set up Telegram to get them on your phone."), "/settings#telegram")


async def content() -> None:
    """The alerts page's content (in the app shell, see ui.shell)."""
    with page_content():
        with ui.row().classes("w-full items-center"):
            with ui.column().classes("gap-0"):
                ui.label(tr("Alerts")).classes("text-h5")
                ui.label(tr("Get notified when a price reaches a level or moves fast.")).classes(
                    "text-muted"
                )
            ui.space()
            ui.button(tr("New alert"), icon="add_alert", on_click=lambda: open_new())
        await _telegram_hint()

        with ui.row().classes("w-full gap-4 items-start"):
            with ui.card().classes("flex-1 min-w-[22rem]"):
                ui.label(tr("Notifications")).classes("text-h6")
                feed = ui.column().classes("w-full gap-2")
            with ui.card().classes("flex-1 min-w-[22rem]"):
                ui.label(tr("Rules")).classes("text-h6")
                rules = ui.column().classes("w-full gap-1")

        async def render_rules() -> None:
            async with runtime.sessions() as session:
                found = await repo.alert_rules(session)
            rules.clear()
            with rules:
                if not found:
                    ui.label(tr("No alerts yet. Add one here or from an item's page.")).classes(
                        "text-muted"
                    )
                for rule, item in found:
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        _item_link(item.market_hash_name, item.appid, item.icon_url)
                    with ui.element("div").classes("w-full pl-10"):
                        _rule_row(rule, render_rules)

        async def render_feed() -> None:
            async with runtime.sessions() as session:
                events = await repo.alert_events(session)
                # Highlighted on this visit, read from now on.
                unread = {event.id for event, _ in events if event.read_at is None}
                await repo.mark_alerts_read(session)
                await session.commit()
            language = current_language()
            feed.clear()
            with feed:
                if not events:
                    ui.label(tr("Nothing has fired yet.")).classes("text-muted")
                for event, item in events:
                    marker = "unread-alert" if event.id in unread else ""
                    with ui.column().classes(f"w-full gap-0 pl-3 {marker}"):
                        with ui.row().classes("w-full items-center no-wrap gap-2"):
                            color = rarity_color(
                                item.rarity, item.quality, item.name_color, item.appid
                            )
                            _item_link(item.market_hash_name, item.appid, item.icon_url, color)
                            ui.space()
                            ui.label(time_ago(event.created_at)).classes("text-caption text-muted")
                        rule = alerts.describe(
                            language, event.kind, event.threshold, event.window_hours
                        )
                        summary = alerts.event_summary(language, event)
                        ui.label(f"{rule}: {summary}").classes("pl-10")

        async def pick(result: SearchResult) -> bool:
            picked.clear()
            with picked:
                ui.label(result.market_hash_name).classes("text-subtitle1")
                alert_form(
                    result.market_hash_name,
                    appid=result.appid,
                    found=result,
                    on_saved=on_new_saved,
                )
            return False

        async def on_new_saved() -> None:
            new_dialog.close()
            await render_rules()

        with ui.dialog() as new_dialog, ui.card().classes("w-[48rem] max-w-full"):
            ui.label(tr("New alert")).classes("text-h6")
            picked = ui.column().classes("w-full")
            search_slot = ui.column().classes("w-full")

        async def open_new() -> None:
            picked.clear()
            search_slot.clear()
            with search_slot:
                search_box(
                    action_label=tr("Choose"),
                    on_pick=pick,
                    games=await searchable_games(),
                )
            new_dialog.open()

        await render_rules()
        await render_feed()


def _item_link(name: str, appid: int, icon: str | None, color: str | None = None) -> None:
    with (
        ui.link(target=item_url(name, appid)).classes("plain-link"),
        ui.row().classes("items-center no-wrap gap-2"),
    ):
        image = icon_url(icon)
        if image:
            ui.image(image).classes("w-8 h-6").props("fit=contain")
        else:
            ui.element("div").classes("w-8")
        ui.label(name).style(f"color: {color}" if color else "")
