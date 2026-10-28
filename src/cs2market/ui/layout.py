"""The frame every page shares: left navigation panel, header and colour theme."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from nicegui import app, ui

from cs2market.i18n import LANGUAGES, Language, current_language, set_language, tr

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
a.plain-link { color: inherit; text-decoration: none; }
a.plain-link:hover { text-decoration: underline; }
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
    NavItem("alerts", "Alerts", "notifications", None),
    NavItem("market", "Market", "query_stats", None),
]
NAV_BOTTOM: list[NavItem] = [NavItem("settings", "Settings", "settings", None)]


def current_theme() -> Theme:
    theme = app.storage.user.get("theme", DEFAULT_THEME)
    return theme if theme in THEMES else DEFAULT_THEME


def is_dark() -> bool:
    return current_theme() != "light"


def echart_theme() -> str | None:
    """ECharts' built-in theme matching the page; charts set a transparent background."""
    return "dark" if is_dark() else None


@contextmanager
def page_frame(active: str) -> Iterator[None]:
    """Wrap a page's content in the shared navigation, header and theme."""
    theme = current_theme()
    ui.add_css(_CSS)
    ui.dark_mode(is_dark())
    ui.query("body").classes(f"theme-{theme}")

    with ui.left_drawer(value=True, bordered=False).classes("app-drawer p-2") as drawer:
        with ui.row().classes("items-center gap-2 px-3 py-3"):
            ui.icon("insights", size="sm")
            ui.label("cs2market").classes("text-h6")
        _nav(NAV, active)
        ui.space()
        _nav(NAV_BOTTOM, active)

    with ui.header().classes("app-header items-center gap-2 py-1"):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense color=grey")
        ui.space()
        _language_menu()
        _theme_menu(theme)

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
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


def _theme_menu(theme: Theme) -> None:
    def choose(choice: Theme) -> None:
        app.storage.user["theme"] = choice
        ui.navigate.reload()

    with ui.button(icon="palette").props("flat round dense color=grey"):
        with ui.menu():
            for key, label in THEMES.items():
                ui.menu_item(
                    f"{'✓ ' if key == theme else ''}{tr(label)}",
                    on_click=lambda k=key: choose(k),
                )
        ui.tooltip(tr("Theme"))


def _language_menu() -> None:
    def choose(language: Language) -> None:
        set_language(language)
        ui.navigate.reload()

    current = current_language()
    with ui.button(current.upper()).props("flat dense color=grey"):
        with ui.menu():
            for key, label in LANGUAGES.items():
                ui.menu_item(
                    f"{'✓ ' if key == current else ''}{label}",
                    on_click=lambda k=key: choose(k),
                )
        ui.tooltip(tr("Language"))
