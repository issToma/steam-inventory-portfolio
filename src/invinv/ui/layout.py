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
from invinv.ui.common import span, time_ago

type Theme = Literal["dark", "dim", "light"]

THEMES: dict[Theme, str] = {"dark": "Dark", "dim": "Dark dimmed", "light": "Light"}
DEFAULT_THEME: Theme = "dark"

# Palettes follow GitHub's Dark, Dark dimmed and Light themes.
_CSS = """
@font-face { font-family: "Inter"; font-weight: 100 900; font-display: swap;
             src: url("/static/fonts/InterVariable.woff2") format("woff2"); }
body, .q-btn, .q-field, .q-table, .q-item, .q-tooltip, .q-notification {
  font-family: "Inter", system-ui, sans-serif; }
/* Figures of equal width where numbers line up; not for names, where Inter's tabular forms
   would also widen hyphens and bars. */
.hero-value, .delta-pill, .kpi-value, .q-table td.text-right, .text-h6 {
  font-variant-numeric: tabular-nums; }
.text-h5 { font-weight: 600; letter-spacing: -0.015em; }
.text-h6, .text-subtitle1 { font-weight: 600; letter-spacing: -0.01em; }
/* Buttons in sentence case, flat and a little rounder, as current interfaces have them. */
.q-btn { text-transform: none; letter-spacing: normal; font-weight: 500; }
.q-btn--standard, .q-btn--rectangle { border-radius: 8px; }
.q-btn--standard { box-shadow: none !important; }
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
.nav-item { color: var(--text); border-radius: 8px; position: relative; }
.nav-item:hover { background: var(--hover); }
.nav-item .q-icon { color: var(--muted); }
.nav-item.active { background: var(--hover); font-weight: 600; }
.nav-item.active .q-icon { color: var(--q-primary); }
/* A short accent bar marks the page shown. */
.nav-item.active::before { content: ""; position: absolute; left: 0; top: 8px; bottom: 8px;
                           width: 3px; border-radius: 3px; background: var(--q-primary); }
a.plain-link:hover .nav-item { text-decoration: none; }
a.plain-link:has(.nav-item):hover { text-decoration: none; }
/* Settings sit apart at the bottom, under a hairline. */
.nav-bottom { border-top: 1px solid var(--border); padding-top: 8px; }
.nav-item.disabled { color: var(--muted); opacity: .6; }
.text-muted { color: var(--muted) !important; }
.period-card { border-color: var(--q-primary) !important; }
/* The headline figure, its change and the strip of smaller figures under it. */
.hero-value { font-size: 2.6rem; font-weight: 650; letter-spacing: -0.025em; line-height: 1.1; }
.delta-pill { border-radius: 999px; padding: 4px 12px; font-weight: 600; font-size: 0.95rem; }
.delta-up { color: #2ea043; background: rgba(46, 160, 67, 0.14); }
.delta-down { color: #f85149; background: rgba(248, 81, 73, 0.14); }
.delta-flat { color: var(--muted); background: var(--hover); }
.kpi { gap: 2px; padding: 2px 20px; border-left: 1px solid var(--border); justify-content: center; }
.kpi-strip > .q-row > .kpi:first-child, .kpi-strip .kpi:first-child { border-left: none;
                                                                   padding-left: 0; }
.kpi-label { font-size: 0.75rem; color: var(--muted); }
.kpi-value { font-size: 1.1rem; font-weight: 600; }
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
/* Motion: short and calm, and none at all when the system asks for reduced motion. */
@media (prefers-reduced-motion: no-preference) {
  @keyframes invinv-rise { from { opacity: 0; transform: translateY(6px); }
                           to { opacity: 1; transform: none; } }
  @keyframes invinv-fade { from { opacity: 0; } to { opacity: 1; } }
  .page-enter > * { animation: invinv-rise 0.4s cubic-bezier(0.2, 0.8, 0.2, 1) both; }
  .page-enter > *:nth-child(2) { animation-delay: 40ms; }
  .page-enter > *:nth-child(3) { animation-delay: 80ms; }
  .page-enter > *:nth-child(n + 4) { animation-delay: 120ms; }
  .hero-value, .delta-pill { animation: invinv-fade 0.35s ease-out both; }
  .q-table tbody tr, .nav-item, .kpi, .q-btn, .q-card {
    transition: background-color 0.18s ease, color 0.18s ease, border-color 0.18s ease; }
  .q-table tbody tr:hover { background: var(--hover); }
  .nav-item .q-icon { transition: transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1), color 0.18s; }
  .nav-item:hover .q-icon { transform: translateX(1px); }
  .nav-settings:hover .q-icon { transform: rotate(60deg); }
  .seg-indicator { transition: transform 0.32s cubic-bezier(0.2, 0.8, 0.2, 1),
                               width 0.32s cubic-bezier(0.2, 0.8, 0.2, 1); }
}
/* The sliding pill of a period switch sits behind its segments. */
.segmented { position: relative; }
.segmented .q-btn { position: relative; z-index: 1; }
.seg-indicator { position: absolute; left: 0; top: 0; z-index: 0; border-radius: 999px;
                 background: var(--q-primary); pointer-events: none; }
.segmented.has-indicator .q-btn.bg-primary { background: transparent !important; }
/* No spinner while a picture loads: frozen mid-turn it looked like a broken glyph. */
.q-img__loading { display: none !important; }
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


# The line colour of a chart's first series (ECharts' default palette), and a fill fading
# from it under the line.
CHART_LINE = "#5470c6"
CHART_FILL = {
    "type": "linear",
    "x": 0,
    "y": 0,
    "x2": 0,
    "y2": 1,
    "colorStops": [
        {"offset": 0, "color": "rgba(84, 112, 198, 0.35)"},
        {"offset": 1, "color": "rgba(84, 112, 198, 0)"},
    ],
}


def themed_chart(options: dict[str, Any]) -> dict[str, Any]:
    """Colour an ECharts option's text, axes, legend and tooltip for the current theme, and
    give it the shared motion: lines draw in on first show and glide to new data."""
    text, muted, line, surface = _CHART_COLORS[current_theme()]
    options = {
        "animationDuration": 700,
        "animationEasing": "cubicOut",
        "animationDurationUpdate": 400,
        "animationEasingUpdate": "cubicInOut",
        **options,
        "textStyle": {"color": text},
    }
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
        tooltip = options["tooltip"]
        if tooltip.get("trigger") == "axis" and "axisPointer" not in tooltip:
            # A dashed guide line follows the pointer across the chart.
            tooltip = {
                **tooltip,
                "axisPointer": {"type": "line", "lineStyle": {"color": muted, "type": "dashed"}},
            }
        options["tooltip"] = {
            **tooltip,
            "backgroundColor": surface,
            "borderColor": line,
            "textStyle": {"color": text},
        }
    return options


@contextmanager
def page_frame(active: str, *, animate: bool = True) -> Iterator[None]:
    """Wrap a page's content in the shared navigation, header and theme. In the app shell
    (ui.shell) the pages inside animate themselves, so the frame does not (`animate`)."""
    theme = current_theme()
    ui.add_css(_CSS)
    ui.add_head_html('<script src="/static/js/motion.js" defer></script>')
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
        with ui.element("div").classes("w-full nav-bottom"):
            _nav(NAV_BOTTOM, active)

    with ui.header().classes("app-header items-center gap-2 px-3 py-2"):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense color=grey")
        _global_search()
        ui.space()
        _collector_indicator()
        _language_menu()
        _theme_menu(theme)
        _account_chip()

    enter = " page-enter" if animate else ""
    with ui.column().classes(f"w-full max-w-[1600px] mx-auto p-4 gap-4{enter}"):
        yield


@contextmanager
def page_content() -> Iterator[None]:
    """A page's content in the app shell: it rises in each time the page is opened."""
    with ui.column().classes("w-full gap-4 page-enter"):
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
                ui.row().classes(
                    f"{classes} nav-{item.key} {'active' if item.key == active else ''}"
                ) as row,
            ):
                ui.icon(item.icon)
                ui.label(tr(item.label))
                if item.key == "alerts":
                    _unread_badge()
            app.storage.client.setdefault("nav_rows", {})[item.key] = row


def highlight_nav(active: str) -> None:
    """Mark the navigation entry of the page now shown (the shell swaps pages in place)."""
    for key, row in app.storage.client.get("nav_rows", {}).items():
        if key == active:
            row.classes(add="active")
        else:
            row.classes(remove="active")


def kpi(label: str, value: str, color: str = "", tooltip: str = "", *, hint: str = "") -> None:
    """One figure of a strip of figures (.kpi-strip): a small label over its value, and
    optionally a line under it (`hint`) or a tooltip."""
    with ui.column().classes("kpi"):
        ui.label(label).classes("kpi-label")
        ui.label(value).classes(f"kpi-value {color}")
        if hint:
            ui.label(hint).classes("kpi-label")
        if tooltip:
            ui.tooltip(tooltip)


def delta_class(change: float | None) -> str:
    """The tinted pill class of a change: up, down, or neither."""
    if not change:
        return "delta-flat"
    return "delta-up" if change > 0 else "delta-down"


def item_url(market_hash_name: str, appid: int) -> str:
    return f"/item/by-name?appid={appid}&name={quote(market_hash_name)}"


def _global_search() -> None:
    """Type to search every item seen so far; pick one to open its page."""
    box = (
        ui.input(placeholder=tr("Search items…"))
        .props("dense outlined rounded clearable")
        .classes("w-full max-w-md ml-3")
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
        checked = (
            tr("last price check {ago}", ago=time_ago(status.last_price_check))
            if status.last_price_check
            else ""
        )
        hint.text = " · ".join(
            part for part in (text or tr("Prices are up to date"), checked, pace) if part
        )

    ui.timer(15, update)
    ui.timer(0.1, update, once=True)


def _unread_badge() -> None:
    """The number of unread alerts on the Alerts entry of the navigation."""
    badge = ui.badge(color="red").props("rounded").classes("ml-auto")
    badge.set_visibility(False)

    async def update() -> None:
        async with runtime.sessions() as session:
            unread = await repo.unread_alert_count(session)
        badge.text = str(unread) if unread < 100 else "99+"
        badge.set_visibility(unread > 0)

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
