"""The frame every page shares: left navigation panel, header and colour theme."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import quote

from nicegui import app, ui

from invinv import APP_FULL_NAME, APP_NAME
from invinv.core.parsers import SearchResult
from invinv.db import repo
from invinv.i18n import LANGUAGES, Language, current_language, set_language, tr, tr_n
from invinv.runtime import runtime
from invinv.search import SearchQuery, search_local
from invinv.ui.common import span

type Theme = Literal["dark", "dim", "light"]

THEMES: dict[Theme, str] = {"dark": "Dark", "dim": "Dark dimmed", "light": "Light"}
DEFAULT_THEME: Theme = "dark"

# Palettes follow GitHub's Dark, Dark dimmed and Light themes.
_CSS = """
body.theme-dark  { --bg: #0d1117; --surface: #161b22; --border: #30363d;
                   --text: #e6edf3; --muted: #8d96a0; --hover: #1f262e; }
body.theme-dim   { --bg: #22272e; --surface: #2d333b; --border: #444c56;
                   --text: #adbac7; --muted: #768390; --hover: #373e47; }
body.theme-light { --bg: #ffffff; --surface: #f6f8fa; --border: #d0d7de;
                   --text: #1f2328; --muted: #59636e; --hover: #eaeef2; }
body { background: var(--bg) !important; color: var(--text); }
body.body--dark { --q-dark-page: var(--bg); --q-dark: var(--surface); }
.q-card { background: var(--surface) !important; color: var(--text);
          border: 1px solid var(--border); box-shadow: none !important; }
.q-table__card, .q-table__container { background: var(--surface) !important; color: var(--text); }
.q-table th, .q-table td { border-color: var(--border) !important; }
.q-table tbody tr:hover { background: var(--hover); }
.app-header { background: var(--surface) !important; color: var(--text) !important;
              border-bottom: 1px solid var(--border); }
.app-drawer { background: var(--surface) !important; border-right: 1px solid var(--border); }
.nav-item { color: var(--text); border-radius: 6px; }
.nav-item:hover { background: var(--hover); }
.nav-item.active { background: var(--hover); font-weight: 600; }
.nav-item.disabled { color: var(--muted); opacity: .6; }
.text-muted { color: var(--muted) !important; }
.period-card { border-color: var(--q-primary) !important; }
/* Game switch: separate, roomy pill buttons; the active one is filled with the accent. */
.game-switch { gap: 8px; background: transparent !important; box-shadow: none !important; }
.game-switch .q-btn { border-radius: 999px !important; padding: 6px 18px; font-size: 0.95rem;
                      border: 1px solid var(--border); background: var(--surface);
                      color: var(--text); }
.game-switch .q-btn.bg-primary { border-color: var(--q-primary); }
.game-switch .q-btn:hover { background: var(--hover); }
.game-switch .q-btn.bg-primary:hover { background: var(--q-primary) !important; }
/* Period switches: one bordered capsule of compact segments, the chosen one filled. */
.segmented { border: 1px solid var(--border); border-radius: 999px; padding: 3px;
             background: var(--bg) !important; box-shadow: none !important; }
.segmented .q-btn { border-radius: 999px !important; padding: 2px 14px; min-height: 30px;
                    font-weight: 500; color: var(--muted); }
.segmented .q-btn:hover { color: var(--text); }
.segmented .q-btn.bg-primary { color: #fff; }
a.plain-link { color: inherit; text-decoration: none; }
a.plain-link:hover { text-decoration: underline; }
.unread-alert { border-left: 3px solid var(--q-primary); }
"""


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str
    path: str | None  # None while the section is not built yet


NAV: list[NavItem] = [
    NavItem("portfolio", "Portfolio", "account_balance_wallet", "/"),
    NavItem("watchlist", "Watchlist", "visibility", "/watchlist"),
    NavItem("alerts", "Alerts", "notifications", "/alerts"),
    NavItem("market", "Market", "query_stats", "/market"),
]
NAV_BOTTOM: list[NavItem] = [NavItem("settings", "Settings", "settings", "/settings")]


# Quasar props shared by the period switches (styled by the .segmented class).
SEGMENTED_PROPS = "no-caps unelevated toggle-color=primary toggle-text-color=white"


def current_theme() -> Theme:
    try:
        theme = app.storage.user.get("theme", DEFAULT_THEME)
    except RuntimeError:  # outside a page request, e.g. in tests
        return DEFAULT_THEME
    return theme if theme in THEMES else DEFAULT_THEME


def switch_language(language: Language) -> None:
    set_language(language)
    ui.navigate.reload()


def set_theme(theme: Theme) -> None:
    app.storage.user["theme"] = theme
    ui.navigate.reload()


def is_dark() -> bool:
    return current_theme() != "light"


# Chart colours per theme: text, muted text, lines, tooltip background (GitHub palettes).
_CHART_COLORS: dict[Theme, tuple[str, str, str, str]] = {
    "dark": ("#e6edf3", "#8d96a0", "#30363d", "#161b22"),
    "dim": ("#adbac7", "#768390", "#444c56", "#2d333b"),
    "light": ("#1f2328", "#59636e", "#d0d7de", "#ffffff"),
}


def themed_chart(options: dict[str, Any]) -> dict[str, Any]:
    """Colour an ECharts option's text, axes, legend and tooltip for the current theme."""
    text, muted, line, surface = _CHART_COLORS[current_theme()]
    options = {**options, "textStyle": {"color": text}}
    for key in ("xAxis", "yAxis"):
        axes = options.get(key)
        if axes is None:
            continue
        styled = [
            {
                **axis,
                "axisLabel": {**axis.get("axisLabel", {}), "color": muted},
                "axisLine": {"lineStyle": {"color": line}},
            }
            for axis in (axes if isinstance(axes, list) else [axes])
        ]
        options[key] = styled if isinstance(axes, list) else styled[0]
    if "legend" in options:
        options["legend"] = {**options["legend"], "textStyle": {"color": text}}
    if "tooltip" in options:
        options["tooltip"] = {
            **options["tooltip"],
            "backgroundColor": surface,
            "borderColor": line,
            "textStyle": {"color": text},
        }
    return options


@contextmanager
def page_frame(active: str) -> Iterator[None]:
    """Wrap a page's content in the shared navigation, header and theme."""
    theme = current_theme()
    ui.add_css(_CSS)
    ui.dark_mode(is_dark())
    ui.query("body").classes(f"theme-{theme}")

    with ui.left_drawer(value=True, bordered=False).classes("app-drawer p-2") as drawer:
        with ui.row().classes("items-center gap-2 px-3 py-3 no-wrap"):
            ui.icon("inventory_2", size="sm")
            with ui.column().classes("gap-0"):
                ui.label(APP_NAME).classes("text-h6 leading-tight")
                ui.label(APP_FULL_NAME).classes("text-caption text-muted leading-tight")
        _nav(NAV, active)
        ui.space()
        _nav(NAV_BOTTOM, active)

    with ui.header().classes("app-header items-center gap-2 py-1"):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense color=grey")
        _global_search()
        ui.space()
        _collector_indicator()
        _alerts_bell()
        _language_menu()
        _theme_menu(theme)
        _account_chip()

    with ui.column().classes("w-full max-w-[1600px] mx-auto p-4 gap-4"):
        yield


def _nav(items: list[NavItem], active: str) -> None:
    with ui.column().classes("w-full gap-1"):
        for item in items:
            classes = "nav-item w-full items-center gap-3 px-3 py-2 no-wrap"
            if item.path is None:
                with ui.row().classes(f"{classes} disabled"):
                    ui.icon(item.icon)
                    ui.label(tr(item.label)).classes("grow")
                    ui.badge(tr("soon")).props("outline color=grey")
                continue
            with (
                ui.link(target=item.path).classes("plain-link w-full"),
                ui.row().classes(f"{classes} {'active' if item.key == active else ''}"),
            ):
                ui.icon(item.icon)
                ui.label(tr(item.label))


def item_url(market_hash_name: str, appid: int) -> str:
    return f"/item/by-name?appid={appid}&name={quote(market_hash_name)}"


def _global_search() -> None:
    """Type to search every item seen so far; pick one to open its page."""
    box = (
        ui.input(placeholder=tr("Search items…"))
        .props("dense outlined clearable")
        .classes("w-full max-w-md ml-2")
    )
    with box.add_slot("prepend"):
        ui.icon("search")
    with box:
        menu = ui.menu().props("no-focus no-parent-event fit")
    results: list[SearchResult] = []

    async def update() -> None:
        nonlocal results
        text = (box.value or "").strip()
        results = (
            await search_local(SearchQuery(text, appid=None), limit=8) if len(text) >= 2 else []
        )
        menu.clear()
        if not results:
            menu.close()
            return
        with menu:
            for result in results:
                ui.menu_item(
                    result.market_hash_name,
                    on_click=lambda r=result: ui.navigate.to(item_url(r.market_hash_name, r.appid)),
                )
        menu.open()

    def open_first() -> None:
        if results:
            ui.navigate.to(item_url(results[0].market_hash_name, results[0].appid))

    box.on_value_change(update)
    box.on("keydown.enter", open_first)


def _collector_indicator() -> None:
    # Quiet when everything is fresh: just a dot with a tooltip. Text appears only while
    # something is happening, so the user can see why prices may lag.
    with ui.row().classes("items-center gap-1 no-wrap mr-2"):
        dot = ui.icon("circle", size="xs")
        with dot:
            hint = ui.tooltip()
        label = ui.label().classes("text-caption text-muted gt-sm")
        resume = ui.button(tr("Resume now"), on_click=lambda: resume_now()).props(
            "flat dense no-caps size=sm color=amber"
        )
        with resume:
            ui.tooltip(
                tr(
                    "Steam limits requests per IP address. If you switched to another IP "
                    "(say, another VPN server), continue right away instead of waiting."
                )
            )
        resume.set_visibility(False)

    async def resume_now() -> None:
        await runtime.resume_steam()
        ui.notify(tr("Resumed. If Steam still answers 429, the next pause is longer."))
        await update()

    async def update() -> None:
        status = await runtime.collector_status()
        if not status.enabled:
            color, text = "grey", tr("Collector is off")
        elif status.paused_seconds is not None:
            minutes = max(round(status.paused_seconds / 60), 1)
            color, text = (
                "amber",
                tr("Steam limited requests · waiting {minutes} min", minutes=minutes),
            )
        elif status.loading_prices:
            color, text = (
                "light-blue",
                tr(
                    "Loading prices · {priced} of {total} · ~{eta} left · {pace} s/request",
                    priced=status.prices.priced,
                    total=status.prices.total,
                    eta=span(timedelta(seconds=status.eta_seconds)),
                    pace=f"{status.pace_seconds:g}",
                ),
            )
        elif status.queued:
            color, text = (
                "light-blue",
                tr_n(status.queued, "Updating · {n} task queued", "Updating · {n} tasks queued"),
            )
        else:
            color, text = "green", ""
        dot.props(f"color={color}")
        resume.set_visibility(status.paused_seconds is not None)
        label.text = text
        pace = (
            tr("One request to Steam every {pace} s", pace=f"{status.pace_seconds:g}")
            if status.enabled
            else ""
        )
        hint.text = " · ".join(part for part in (text or tr("Prices are up to date"), pace) if part)

    ui.timer(15, update)
    ui.timer(0.1, update, once=True)


def _alerts_bell() -> None:
    with ui.button(icon="notifications", on_click=lambda: ui.navigate.to("/alerts")).props(
        "flat round dense color=grey"
    ):
        badge = ui.badge(color="red").props("floating rounded")
        ui.tooltip(tr("Alerts"))

    async def update() -> None:
        async with runtime.sessions() as session:
            unread = await repo.unread_alert_count(session)
        badge.text = str(unread) if unread < 100 else "99+"
        badge.set_visibility(unread > 0)

    badge.set_visibility(False)
    ui.timer(30, update)
    ui.timer(0.1, update, once=True)


def _account_chip() -> None:
    """The connected Steam account in the top right corner, on every page."""
    chip = ui.row().classes("items-center no-wrap ml-2")

    async def render() -> None:
        async with runtime.sessions() as session:
            account = await repo.get_account(session)
        chip.clear()
        with chip:
            if account is None:
                ui.button(
                    tr("Connect Steam"), icon="link", on_click=lambda: ui.navigate.to("/settings")
                ).props("flat dense no-caps")
                return
            profile = account.profile
            with ui.button().props("flat dense no-caps"):
                with ui.row().classes("items-center no-wrap gap-2"):
                    if profile and profile.avatar_url:
                        ui.image(profile.avatar_url).classes("w-7 h-7 rounded-full")
                    else:
                        ui.icon("account_circle")
                    ui.label(profile.name if profile else account.steam_id64).classes(
                        "gt-xs max-w-40 truncate"
                    )
                with ui.menu():
                    ui.menu_item(tr("Sync now"), on_click=sync_now)
                    ui.menu_item(tr("Settings"), on_click=lambda: ui.navigate.to("/settings"))

    async def sync_now() -> None:
        async with runtime.sessions() as session:
            account = await repo.get_account(session)
            if account is not None:
                await repo.run_job_now(session, repo.JOB_INVENTORY, account.steam_id64)
                await session.commit()
        ui.notify(tr("Checking your inventory…"))

    ui.timer(0.1, render, once=True)


def _theme_menu(theme: Theme) -> None:
    with ui.button(icon="palette").props("flat round dense color=grey"):
        with ui.menu():
            for key, label in THEMES.items():
                ui.menu_item(
                    f"{'✓ ' if key == theme else ''}{tr(label)}",
                    on_click=lambda k=key: set_theme(k),
                )
        ui.tooltip(tr("Theme"))


def _language_menu() -> None:
    current = current_language()
    with ui.button(current.upper()).props("flat dense color=grey"):
        with ui.menu():
            for key, label in LANGUAGES.items():
                ui.menu_item(
                    f"{'✓ ' if key == current else ''}{label}",
                    on_click=lambda k=key: switch_language(k),
                )
        ui.tooltip(tr("Language"))
