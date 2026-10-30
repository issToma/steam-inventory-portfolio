"""The app shell: header and navigation are built once per visit, and moving between pages
swaps only the content beside them (ui.sub_pages), without reloading the page.

Links within the app (the navigation, table rows, search results) are caught by the sub
pages router and swap the content; other addresses load as usual.
"""

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request
from nicegui import ui

from invinv.ui import alerts_page, item, market, portfolio, settings_page, watchlist
from invinv.ui.layout import highlight_nav, page_frame

ROUTES: dict[str, Callable[..., Awaitable[None]]] = {
    "/": portfolio.content,
    "/watchlist": watchlist.content,
    "/alerts": alerts_page.content,
    "/market": market.content,
    "/settings": settings_page.content,
    # Before /item/{item_id}, which would otherwise take "by-name" for an id.
    "/item/by-name": item.by_name,
    "/item/{item_id}": item.content,
}
# The navigation entry each route belongs to.
_NAV_KEYS = {
    "/": "portfolio",
    "/watchlist": "watchlist",
    "/alerts": "alerts",
    "/market": "market",
    "/settings": "settings",
}


def register() -> None:
    for path in ROUTES:
        ui.page(path)(_shell)


def nav_key(path: str) -> str:
    """The navigation entry of a path; item pages belong to none."""
    return _NAV_KEYS.get(urlsplit(path).path, "")


async def _shell(request: Request) -> None:
    with page_frame(nav_key(request.url.path), animate=False):
        ui.sub_pages(ROUTES).classes("w-full")
    ui.context.client.sub_pages_router.on_path_changed(lambda path: highlight_nav(nav_key(path)))
